"""Asset-Ordner-Analyse mit Frame-Extraktion und Gemini."""

from __future__ import annotations

import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from otio_app.analysis_models import (
    AssetFolderAnalysis,
    AssetMediaAnalysis,
    InventoryDocument,
)
from otio_app.models import Project, validate_asset_selection
from otio_app.project_layout import safe_folder_slug
from otio_app.services.analysis_cancel import AnalysisCancelledError
from otio_app.services.analysis_log import append_analysis_log
from otio_app.services.analysis_progress import AnalysisRunReport, ProgressCallback, noop_progress
from otio_app.services.asset_analysis_signature import (
    ANALYSIS_SCHEMA_VERSION,
    ANALYSIS_SCOPE_FRAMES,
    try_build_analysis_signature,
)
from otio_app.services.frame_extract import extract_frames, list_existing_frame_jpegs
from otio_app.services.asset_watermark_check import (
    WATERMARK_BLOCK_ERROR,
    WATERMARK_CHECK_VERSION,
    StockWatermarkCheckResult,
    check_frames_for_stock_watermark,
    prune_stale_watermark_review,
    remove_watermark_review_item,
    stock_watermark_from_v3_defects,
    upsert_watermark_review_item,
    watermark_check_is_current,
)
from otio_app.services.gemini_client import (
    ASSET_DESCRIPTION_PROMPT_VERSION,
    GeminiNotConfiguredError,
    analyze_media_from_frames,
    is_transient_api_error,
    resolve_gemini_model,
)
from otio_app.services.inventory_loader import (
    should_skip_folder_analysis,
    sync_folder_inventory_with_status,
)
from otio_app.services.media_inventory_cache import (
    CACHE_SCOPE_PRIMARY,
    CACHE_SCOPE_SUPPLEMENT,
    discover_folder_media_paths,
    has_successful_asset_cache,
    list_assets_missing_successful_cache,
    load_cached_media_for_asset,
    media_cache_path,
    media_file_is_accessible,
    media_stem_slug,
    resolve_media_for_analysis,
    scope_subdir,
    _save_cached_media_safe,
)
from otio_app.services.media_utils import (
    NO_ANALYZABLE_MEDIA_DESCRIPTION,
    is_image_media,
)


ShouldCancel = Callable[[], bool]
_ANALYSIS_RAW_RESPONSE_MAX_CHARS = 4_000


def _bounded_analysis_raw_response(raw: str) -> str:
    """Begrenzt diagnostische Rohantworten; vollständige Traces gehören nicht ins Cache-JSON."""
    text = (raw or "").strip()
    if len(text) <= _ANALYSIS_RAW_RESPONSE_MAX_CHARS:
        return text
    return text[:_ANALYSIS_RAW_RESPONSE_MAX_CHARS].rstrip() + "\n…[truncated]"


def _frames_dir(
    project: Project,
    folder_name: str,
    media_path: Path,
    *,
    scope: str = CACHE_SCOPE_PRIMARY,
) -> Path:
    root = project.work_dir_path / "frames" / safe_folder_slug(folder_name)
    subdir = scope_subdir(scope)
    if subdir:
        root = root / subdir
    return root / safe_folder_slug(media_path.stem)


def _folder_summary(assets: list[AssetMediaAnalysis]) -> str:
    parts: list[str] = []
    for asset in assets:
        if asset.description:
            parts.append(f"{Path(asset.path).name}: {asset.description}")
    return "\n\n".join(parts)


def _count_media_to_analyze(
    project: Project,
    folder_names: list[str],
    *,
    model: Optional[str] = None,
    include_supplements: bool = True,
) -> int:
    total = 0
    for folder_name in folder_names:
        total += len(
            _list_media_needing_analysis_run(project, folder_name, model=model)
        )
        if include_supplements:
            total += _count_open_supplements(project, folder_name, model=model)
    return total


def _log(project: Project, message: str) -> None:
    append_analysis_log(project, message)


def _is_cancelled(should_cancel: ShouldCancel | None) -> bool:
    return bool(should_cancel and should_cancel())


def _list_media_needing_analysis_run(
    project: Project,
    folder_name: str,
    *,
    model: Optional[str] = None,
) -> list[Path]:
    """v3-Lücken plus aktuelle Caches ohne Stock-Wasserzeichen-Prüfung."""
    missing = list_assets_missing_successful_cache(
        project, folder_name, model=model
    )
    pending: list[Path] = []
    missing_slugs = {media_stem_slug(path) for path in missing}
    for media_path in discover_folder_media_paths(project, folder_name):
        if media_stem_slug(media_path) in missing_slugs:
            continue
        if not has_successful_asset_cache(
            project, folder_name, media_path, model=model
        ):
            continue
        cached = load_cached_media_for_asset(project, folder_name, media_path)
        if cached is None or watermark_check_is_current(cached):
            continue
        pending.append(media_path)
    return missing + pending


def _apply_watermark_fields(
    entry: AssetMediaAnalysis,
    result: StockWatermarkCheckResult,
) -> None:
    entry.watermark_blocked = False
    entry.watermark_provider = result.provider
    entry.watermark_note = result.note
    if result.failed_open:
        return
    entry.watermark_check_version = WATERMARK_CHECK_VERSION


def _persist_watermark_block(
    *,
    cache_file: Path,
    entry: AssetMediaAnalysis,
    result: StockWatermarkCheckResult,
    project: Project,
    folder_name: str,
    media_path: Path,
) -> AssetMediaAnalysis:
    entry.watermark_blocked = True
    entry.watermark_check_version = WATERMARK_CHECK_VERSION
    entry.watermark_provider = result.provider
    entry.watermark_note = result.note
    entry.error = WATERMARK_BLOCK_ERROR
    entry.description = ""
    entry.caption = ""
    upsert_watermark_review_item(
        project,
        folder=folder_name,
        media_path=media_path,
        provider=result.provider,
        note=result.note,
    )
    saved = _save_cached_media_safe(cache_file, entry)
    _log(
        project,
        f"FAIL (Wasserzeichen) {folder_name}/{media_path.name} "
        f"provider={result.provider or '?'}",
    )
    return saved


def _analyze_single_media(
    project: Project,
    folder_name: str,
    media_path: Path,
    *,
    use_api: bool,
    model: Optional[str],
    should_cancel: ShouldCancel | None = None,
    scope: str = CACHE_SCOPE_PRIMARY,
) -> tuple[AssetMediaAnalysis, str]:
    resolved_path = resolve_media_for_analysis(
        project, folder_name, media_path, scope=scope
    )
    cache_file = media_cache_path(project, folder_name, resolved_path, scope=scope)
    per_file = max(1, project.frames_per_shot)

    _log(
        project,
        f"START {folder_name}/{resolved_path.name} "
        f"cache={cache_file.name} resolved={resolved_path}",
    )

    requested_model = (model or "").strip()
    resolved_model = resolve_gemini_model(model)
    cached = load_cached_media_for_asset(
        project, folder_name, resolved_path, scope=scope
    )

    if has_successful_asset_cache(
        project, folder_name, resolved_path, model=resolved_model, scope=scope
    ) and watermark_check_is_current(cached):
        assert cached is not None
        _log(project, f"SKIP (Cache ok) {folder_name}/{resolved_path.name}")
        return cached, "cache"

    accessible, access_error = media_file_is_accessible(resolved_path)
    if not accessible:
        entry = AssetMediaAnalysis(
            path=str(resolved_path),
            error=access_error or "Mediendatei nicht lesbar",
        )
        entry = _save_cached_media_safe(cache_file, entry)
        _log(project, f"FAIL (nicht lesbar) {folder_name}/{resolved_path.name}: {entry.error}")
        return entry, "fehler"

    if _is_cancelled(should_cancel):
        _log(project, f"ABBRUCH vor Start {folder_name}/{resolved_path.name}")
        raise AnalysisCancelledError()

    frames_dir = _frames_dir(project, folder_name, resolved_path, scope=scope)
    frame_count = 1 if is_image_media(resolved_path) else per_file

    frames = extract_frames(resolved_path, frames_dir, frame_count, should_cancel=should_cancel)
    if not frames:
        frames = list_existing_frame_jpegs(frames_dir)[:frame_count]
    if not frames:
        shutil.rmtree(frames_dir, ignore_errors=True)
        if _is_cancelled(should_cancel):
            _log(project, f"ABBRUCH bei Frames {folder_name}/{resolved_path.name}")
            raise AnalysisCancelledError()
        frames = extract_frames(
            resolved_path, frames_dir, frame_count, should_cancel=should_cancel
        )
    if not frames:
        frames = list_existing_frame_jpegs(frames_dir)[:frame_count]

    _log(
        project,
        f"FRAMES {folder_name}/{resolved_path.name}: {len(frames)}/{frame_count} "
        f"in {frames_dir}",
    )

    entry = AssetMediaAnalysis(
        path=str(resolved_path),
        frames_used=[str(frame) for frame in frames],
    )

    if not frames:
        entry.description = NO_ANALYZABLE_MEDIA_DESCRIPTION
        entry = _save_cached_media_safe(cache_file, entry)
        _log(
            project,
            f"FAIL (keine Frames) {folder_name}/{resolved_path.name} "
            f"-> {cache_file.name} written={cache_file.is_file()}",
        )
        return entry, "fehler"
    if _is_cancelled(should_cancel):
        _log(project, f"ABBRUCH vor Gemini {folder_name}/{resolved_path.name}")
        raise AnalysisCancelledError()
    if use_api:
        watermark_result = check_frames_for_stock_watermark(
            frames,
            media_name=resolved_path.name,
            folder_name=folder_name,
            model=resolved_model,
        )
        if watermark_result.failed_open:
            _log(
                project,
                f"WARN (Wasserzeichen fail-open) {folder_name}/{resolved_path.name}: "
                f"{watermark_result.note or 'Prüfung nicht auswertbar'}",
            )
        elif watermark_result.blocked:
            return (
                _persist_watermark_block(
                    cache_file=cache_file,
                    entry=entry,
                    result=watermark_result,
                    project=project,
                    folder_name=folder_name,
                    media_path=resolved_path,
                ),
                "fehler",
            )
        else:
            _apply_watermark_fields(entry, watermark_result)
            remove_watermark_review_item(
                project, resolved_path, folder=folder_name
            )

        if has_successful_asset_cache(
            project, folder_name, resolved_path, model=resolved_model, scope=scope
        ):
            cached_ok = load_cached_media_for_asset(
                project, folder_name, resolved_path, scope=scope
            )
            if cached_ok is not None:
                _apply_watermark_fields(cached_ok, watermark_result)
                if not cached_ok.frames_used:
                    cached_ok.frames_used = entry.frames_used
                cached_ok = _save_cached_media_safe(cache_file, cached_ok)
                _log(
                    project,
                    f"SKIP (Cache ok, Wasserzeichen geprüft) "
                    f"{folder_name}/{resolved_path.name}",
                )
                return cached_ok, "cache"

        try:
            analysis = _analyze_frames_with_retry(
                project,
                folder_name,
                resolved_path.name,
                frames,
                resolved_model=resolved_model,
                should_cancel=should_cancel,
            )
            entry.description_model_requested = requested_model
            entry.description_model_resolved = resolved_model
            entry.description_model = resolved_model
            entry.description_prompt_version = ASSET_DESCRIPTION_PROMPT_VERSION
            entry.analysis_schema_version = ANALYSIS_SCHEMA_VERSION
            entry.analysis_scope = ANALYSIS_SCOPE_FRAMES
            entry.description_generated_at = datetime.now(timezone.utc)

            if not analysis.parse_ok:
                entry.analysis_parse_ok = False
                entry.description = ""
                entry.caption = ""
                entry.analysis_raw_response = _bounded_analysis_raw_response(
                    analysis.raw_response
                )
                entry.error = "Asset-Analyse: Gemini-Antwort konnte nicht als v3-JSON geparst werden."
                entry = _save_cached_media_safe(cache_file, entry)
                _log(
                    project,
                    f"FAIL (Parse) {folder_name}/{resolved_path.name}: parse_ok=False",
                )
                return entry, "fehler"

            signature = try_build_analysis_signature(
                resolved_path,
                resolved_model_id=resolved_model,
            )
            if signature is None:
                entry.analysis_parse_ok = False
                entry.description = ""
                entry.analysis_raw_response = _bounded_analysis_raw_response(
                    analysis.raw_response
                )
                entry.error = "Asset-Analyse: Dateisignatur konnte nicht gelesen werden."
                entry = _save_cached_media_safe(cache_file, entry)
                _log(
                    project,
                    f"FAIL (Signatur) {folder_name}/{resolved_path.name}",
                )
                return entry, "fehler"

            v3_watermark = stock_watermark_from_v3_defects(
                analysis.defect_items, analysis.defects
            )
            if v3_watermark is not None and v3_watermark.blocked:
                entry.defect_items = list(analysis.defect_items)
                entry.defects = analysis.defects
                return (
                    _persist_watermark_block(
                        cache_file=cache_file,
                        entry=entry,
                        result=v3_watermark,
                        project=project,
                        folder_name=folder_name,
                        media_path=resolved_path,
                    ),
                    "fehler",
                )

            entry.description = analysis.description
            entry.caption = analysis.caption
            entry.content_tags = list(analysis.content_tags)
            entry.motion = analysis.motion
            entry.framing = analysis.framing
            entry.people = analysis.people
            entry.people_action = analysis.people_action
            entry.defects = analysis.defects
            entry.motion_profile = analysis.motion_profile
            entry.framing_profile = analysis.framing_profile
            entry.look_profile = analysis.look_profile
            entry.quality_profile = analysis.quality_profile
            entry.defect_items = list(analysis.defect_items)
            entry.analysis_confidence = analysis.confidence
            entry.analysis_parse_ok = True
            entry.analysis_signature = signature
            # Erfolgreiche Parses speichern keine Rohantwort (kein JSON-Duplikat im Cache).
            entry.analysis_raw_response = ""
            entry.error = None
            _apply_watermark_fields(entry, watermark_result)
            entry = _save_cached_media_safe(cache_file, entry)
            if entry.error and "Cache konnte nicht geschrieben werden" in entry.error:
                _log(project, f"FAIL (Cache-Schreibfehler) {folder_name}/{resolved_path.name}: {entry.error}")
                return entry, "fehler"
            _log(
                project,
                f"OK (Gemini) {folder_name}/{resolved_path.name} -> {cache_file.name} "
                f"written={cache_file.is_file()}",
            )
            return entry, "neu"
        except GeminiNotConfiguredError:
            raise
        except Exception as exc:  # noqa: BLE001
            entry.error = str(exc)
            entry.description = ""
            entry.analysis_parse_ok = False
            entry = _save_cached_media_safe(cache_file, entry)
            _log(project, f"FAIL (Gemini) {folder_name}/{resolved_path.name}: {exc}")
            return entry, "fehler"
    entry.error = "API-Aufruf nicht bestätigt."
    entry = _save_cached_media_safe(cache_file, entry)
    _log(project, f"FAIL (API nicht bestätigt) {folder_name}/{resolved_path.name}")
    return entry, "fehler"


def analyze_supplement_media(
    project: Project,
    folder_name: str,
    media_path: Path,
    *,
    use_api: bool = True,
    model: Optional[str] = None,
    should_cancel: ShouldCancel | None = None,
) -> tuple[AssetMediaAnalysis, str]:
    """Beschafftes Asset mit derselben Analyse wie ein Original beschreiben.

    Identischer Prompt, identisches v3-Schema, identische Signatur — nur Cache
    und Frames liegen im Supplement-Scope, damit die Ordner-Discovery aus dem
    Dateinamen kein fehlendes Original ableitet.

    Returns:
        ``(entry, outcome)`` mit outcome ``cache`` | ``neu`` | ``fehler``.
    """
    return _analyze_single_media(
        project,
        folder_name,
        Path(media_path),
        use_api=use_api,
        model=model,
        should_cancel=should_cancel,
        scope=CACHE_SCOPE_SUPPLEMENT,
    )


def _build_folder_assets(
    project: Project,
    folder_name: str,
    media_paths: list[Path],
    analyzed: dict[str, AssetMediaAnalysis],
) -> list[AssetMediaAnalysis]:
    assets: list[AssetMediaAnalysis] = []
    for media_path in media_paths:
        slug = media_stem_slug(media_path)
        if slug in analyzed:
            assets.append(analyzed[slug])
            continue
        cached = load_cached_media_for_asset(project, folder_name, media_path)
        if cached is not None:
            assets.append(cached)
        else:
            assets.append(AssetMediaAnalysis(path=str(media_path)))
    return assets


#: Ein vorübergehender Serverfehler darf keinen Ordner entwerten.
ASSET_ANALYSIS_MAX_ATTEMPTS = 3
ASSET_ANALYSIS_RETRY_SECONDS = (4.0, 12.0)


def _sleep_cancellable(seconds: float, should_cancel: ShouldCancel | None) -> None:
    """Warten in kleinen Scheiben, damit Stop nicht blockiert wird."""
    remaining = seconds
    while remaining > 0:
        if _is_cancelled(should_cancel):
            return
        step = min(0.5, remaining)
        time.sleep(step)
        remaining -= step


def _analyze_frames_with_retry(
    project: Project,
    folder_name: str,
    media_name: str,
    frames: list[Path],
    *,
    resolved_model: str,
    should_cancel: ShouldCancel | None,
):
    """Frame-Analyse mit begrenzten Wiederholungen bei transienten Fehlern.

    Ohne Wiederholung macht ein einzelner 503 den Ordner unvollständig und sein
    Inventar wird beim folgenden Sync entfernt — ein teurer Nebeneffekt für ein
    Problem, das beim zweiten Versuch meist weg ist.
    """
    last_error: Exception | None = None
    for attempt in range(1, ASSET_ANALYSIS_MAX_ATTEMPTS + 1):
        try:
            return analyze_media_from_frames(
                media_name,
                folder_name,
                frames,
                project.language,
                model=resolved_model,
            )
        except GeminiNotConfiguredError:
            raise
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if not is_transient_api_error(exc):
                raise
            if attempt >= ASSET_ANALYSIS_MAX_ATTEMPTS or _is_cancelled(should_cancel):
                raise
            wait = ASSET_ANALYSIS_RETRY_SECONDS[
                min(attempt - 1, len(ASSET_ANALYSIS_RETRY_SECONDS) - 1)
            ]
            _log(
                project,
                f"RETRY {attempt}/{ASSET_ANALYSIS_MAX_ATTEMPTS - 1} "
                f"{folder_name}/{media_name} nach {wait:.0f}s: {exc}",
            )
            _sleep_cancellable(wait, should_cancel)
    assert last_error is not None
    raise last_error


def _count_open_supplements(
    project: Project,
    folder_name: str,
    *,
    model: Optional[str],
) -> int:
    from otio_app.services.supplement_inventory import (
        count_supplements_needing_analysis,
    )

    try:
        return count_supplements_needing_analysis(project, [folder_name], model=model)
    except Exception:  # noqa: BLE001
        # Zählung ist nur Steuerung des Laufs — nie Grund für einen Abbruch.
        return 0


def _analyze_open_supplements(
    project: Project,
    folder_name: str,
    *,
    use_api: bool,
    model: Optional[str],
    should_cancel: ShouldCancel | None,
    on_progress: ProgressCallback,
    report: AnalysisRunReport | None,
) -> None:
    """Holt die reguläre Analyse für beschaffte Assets ohne v3-Signatur nach.

    Betrifft Zeilen aus Funnel und Inbox, die vor dieser Änderung importiert
    wurden oder ohne API-Schlüssel angekommen sind.
    """
    from otio_app.services.supplement_inventory import analyze_supplements_for_folder

    supplement_report = analyze_supplements_for_folder(
        project,
        folder_name,
        use_api=use_api,
        model=model,
        should_cancel=should_cancel,
        on_progress=lambda event, payload: on_progress(event, payload),
    )
    if supplement_report.analyzed or supplement_report.failed:
        _log(
            project,
            f"SUPPLEMENTS {folder_name}: {supplement_report.analyzed} analysiert, "
            f"{supplement_report.cached} aus Cache, {supplement_report.failed} fehlerhaft",
        )
    if report is None:
        return
    report.media_analyzed += supplement_report.analyzed
    report.media_cached += supplement_report.cached
    report.media_failed += supplement_report.failed
    report.failures.extend(supplement_report.failures)


def _analyze_folder(
    project: Project,
    folder_name: str,
    *,
    use_api: bool,
    model: Optional[str],
    on_progress: ProgressCallback = noop_progress,
    should_cancel: ShouldCancel | None = None,
    folder_index: int = 0,
    folder_count: int = 1,
    report: AnalysisRunReport | None = None,
    analyze_supplements: bool = True,
) -> AssetFolderAnalysis:
    media_paths = discover_folder_media_paths(project, folder_name)
    to_process = _list_media_needing_analysis_run(
        project, folder_name, model=model
    )
    open_supplements = (
        _count_open_supplements(project, folder_name, model=model)
        if analyze_supplements
        else 0
    )

    _log(
        project,
        f"ORDNER {folder_name}: {len(media_paths)} Medien, "
        f"{len(to_process)} zu prüfen: "
        + ", ".join(path.name for path in to_process)
        + (f" | {open_supplements} Supplements offen" if open_supplements else ""),
    )

    if not to_process and not open_supplements:
        existing = should_skip_folder_analysis(project, folder_name, media_paths)
        if existing is not None:
            _log(project, f"ORDNER-SKIP {folder_name}: alle JSONs vorhanden")
            on_progress(
                "folder_skip",
                {
                    "folder": folder_name,
                    "folder_index": folder_index,
                    "folder_count": folder_count,
                    "reason": "Alle Assets haben Analyse-JSON, Inventar vorhanden",
                },
            )
            if report is not None:
                report.folders_skipped.append(folder_name)
            return existing

    on_progress(
        "folder_start",
        {
            "folder": folder_name,
            "folder_index": folder_index,
            "folder_count": folder_count,
            "media_count": len(to_process),
            "missing_cache_count": len(to_process),
        },
    )

    analyzed: dict[str, AssetMediaAnalysis] = {}
    cancelled_mid_folder = False
    for media_index, media_path in enumerate(to_process, start=1):
        if _is_cancelled(should_cancel):
            cancelled_mid_folder = True
            _log(
                project,
                f"ORDNER-ABBRUCH {folder_name} nach {media_index - 1}/{len(to_process)} Assets",
            )
            break
        on_progress(
            "media_start",
            {
                "folder": folder_name,
                "media_name": media_path.name,
                "media_index": media_index,
                "media_count": len(to_process),
                "folder_index": folder_index,
                "folder_count": folder_count,
                "needs_analysis": True,
            },
        )
        try:
            entry, outcome = _analyze_single_media(
                project,
                folder_name,
                media_path,
                use_api=use_api,
                model=model,
                should_cancel=should_cancel,
            )
            analyzed[media_stem_slug(media_path)] = entry
            if report is not None:
                if outcome == "cache":
                    report.media_cached += 1
                elif outcome == "neu":
                    report.media_analyzed += 1
                else:
                    report.media_failed += 1
                    if entry.error:
                        report.failures.append(f"{folder_name}/{media_path.name}: {entry.error}")
                    else:
                        report.failures.append(
                            f"{folder_name}/{media_path.name}: Keine analysierbaren Medien"
                        )
            on_progress(
                "media_done",
                {
                    "folder": folder_name,
                    "media_name": media_path.name,
                    "media_index": media_index,
                    "media_count": len(to_process),
                    "folder_index": folder_index,
                    "folder_count": folder_count,
                    "outcome": outcome,
                    "error": entry.error,
                },
            )
        except AnalysisCancelledError:
            cancelled_mid_folder = True
            _log(
                project,
                f"ORDNER-ABBRUCH {folder_name} während {media_path.name} "
                f"({media_index - 1}/{len(to_process)} fertig)",
            )
            break
        except GeminiNotConfiguredError:
            raise
        except Exception as exc:  # noqa: BLE001
            entry = AssetMediaAnalysis(path=str(media_path), error=str(exc))
            _save_cached_media_safe(
                media_cache_path(
                    project,
                    folder_name,
                    resolve_media_for_analysis(project, folder_name, media_path),
                ),
                entry,
            )
            analyzed[media_stem_slug(media_path)] = entry
            _log(project, f"FAIL (Exception) {folder_name}/{media_path.name}: {exc}")
            if report is not None:
                report.media_failed += 1
                report.failures.append(f"{folder_name}/{media_path.name}: {exc}")
            on_progress(
                "media_done",
                {
                    "folder": folder_name,
                    "media_name": media_path.name,
                    "media_index": media_index,
                    "media_count": len(to_process),
                    "outcome": "fehler",
                    "error": str(exc),
                },
            )

    assets = _build_folder_assets(project, folder_name, media_paths, analyzed)
    item = AssetFolderAnalysis(
        folder=folder_name,
        media_files=[asset.path for asset in assets],
        assets=assets,
        frames_used=[frame for asset in assets for frame in asset.frames_used],
        description=_folder_summary(assets),
    )
    if not assets:
        item.description = NO_ANALYZABLE_MEDIA_DESCRIPTION

    if analyze_supplements and not cancelled_mid_folder:
        _analyze_open_supplements(
            project,
            folder_name,
            use_api=use_api,
            model=model,
            should_cancel=should_cancel,
            on_progress=on_progress,
            report=report,
        )

    inventory_saved = sync_folder_inventory_with_status(project, folder_name)
    _log(
        project,
        f"ORDNER-FERTIG {folder_name}: {len(to_process)} geplant, "
        f"{len(analyzed)} analysiert, inventory_saved={inventory_saved}"
        + (" (abgebrochen)" if cancelled_mid_folder else ""),
    )
    if report is not None:
        report.folders_processed.append(folder_name)
    on_progress(
        "folder_done",
        {
            "folder": folder_name,
            "folder_index": folder_index,
            "folder_count": folder_count,
            "asset_count": len(assets),
            "inventory_saved": inventory_saved,
        },
    )
    return item


def analyze_asset_folders(
    project: Project,
    folder_names: list[str],
    *,
    use_api: bool = True,
    model: Optional[str] = None,
    on_progress: ProgressCallback = noop_progress,
    should_cancel: ShouldCancel | None = None,
    analyze_supplements: bool = True,
) -> tuple[InventoryDocument, AnalysisRunReport]:
    """Analysiert fehlende Assets in Ordnern; Inventar-JSON nur bei vollständigem Ordner.

    ``analyze_supplements`` holt zusätzlich beschaffte Assets (Funnel, Inbox)
    nach, die noch keine aktuelle v3-Analyse haben — mit demselben Prompt wie
    Originale, damit das Inventar sprachübergreifend einheitlich bleibt.
    """
    selected = validate_asset_selection(project.asset_subdir_names, folder_names)
    report = AnalysisRunReport()
    dropped_review = prune_stale_watermark_review(project)
    if dropped_review:
        _log(
            project,
            f"REVIEW-PRUNE {dropped_review} Wasserzeichen-Einträge ohne Datei entfernt",
        )
    total_media = _count_media_to_analyze(
        project, selected, model=model, include_supplements=analyze_supplements
    )
    if total_media == 0:
        total_media = sum(
            len(discover_folder_media_paths(project, folder_name))
            for folder_name in selected
        )
    _log(project, f"RUN START folders={selected} missing_assets={total_media}")
    on_progress(
        "start",
        {
            "folder_count": len(selected),
            "total_media": total_media,
        },
    )

    items: list[AssetFolderAnalysis] = []
    cancelled = False

    for folder_index, folder_name in enumerate(selected, start=1):
        if _is_cancelled(should_cancel):
            cancelled = True
            _log(project, f"RUN CANCELLED before folder {folder_name}")
            break
        items.append(
            _analyze_folder(
                project,
                folder_name,
                use_api=use_api,
                model=model,
                on_progress=on_progress,
                should_cancel=should_cancel,
                folder_index=folder_index,
                folder_count=len(selected),
                report=report,
                analyze_supplements=analyze_supplements,
            )
        )
        if _is_cancelled(should_cancel):
            cancelled = True
            _log(project, f"RUN CANCELLED after folder {folder_name}")
            break

    report.cancelled = cancelled
    if cancelled:
        _log(
            project,
            f"RUN END (CANCELLED) analyzed={report.media_analyzed} "
            f"cached={report.media_cached} failed={report.media_failed}",
        )
        on_progress(
            "cancelled",
            {
                "total_media": max(total_media, 1),
                "done": report.media_analyzed + report.media_cached + report.media_failed,
            },
        )
    else:
        _log(
            project,
            f"RUN END analyzed={report.media_analyzed} cached={report.media_cached} "
            f"failed={report.media_failed} failures={report.failures}",
        )
        on_progress(
            "complete",
            {
                "total_media": max(total_media, 1),
                "done": True,
            },
        )
    document = InventoryDocument(project_id=project.id, items=items)
    return document, report
