"""Asset-Ordner-Analyse mit Frame-Extraktion und Gemini."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable, Optional

from otio_app.analysis_models import (
    AssetFolderAnalysis,
    AssetMediaAnalysis,
    InventoryDocument,
)
from otio_app.models import Project, validate_asset_selection
from otio_app.project_layout import safe_folder_slug
from otio_app.services.analysis_log import append_analysis_log
from otio_app.services.analysis_progress import AnalysisRunReport, ProgressCallback, noop_progress
from otio_app.services.frame_extract import extract_frames, list_existing_frame_jpegs
from otio_app.services.gemini_client import (
    GeminiNotConfiguredError,
    describe_media_from_frames,
)
from otio_app.services.inventory_loader import (
    should_skip_folder_analysis,
    sync_folder_inventory_with_status,
)
from otio_app.services.media_inventory_cache import (
    discover_folder_media_paths,
    has_successful_asset_cache,
    list_assets_missing_successful_cache,
    load_cached_media_for_asset,
    media_cache_path,
    media_file_is_accessible,
    media_stem_slug,
    resolve_media_for_analysis,
    _save_cached_media_safe,
)
from otio_app.services.media_utils import (
    NO_ANALYZABLE_MEDIA_DESCRIPTION,
    is_image_media,
)


ShouldCancel = Callable[[], bool]


class AnalysisCancelledError(Exception):
    """Asset-Analyse wurde vom Nutzer abgebrochen."""


def _frames_dir(project: Project, folder_name: str, media_path: Path) -> Path:
    return (
        project.work_dir_path
        / "frames"
        / safe_folder_slug(folder_name)
        / safe_folder_slug(media_path.stem)
    )


def _folder_summary(assets: list[AssetMediaAnalysis]) -> str:
    parts: list[str] = []
    for asset in assets:
        if asset.description:
            parts.append(f"{Path(asset.path).name}: {asset.description}")
    return "\n\n".join(parts)


def _count_media_to_analyze(project: Project, folder_names: list[str]) -> int:
    total = 0
    for folder_name in folder_names:
        missing = list_assets_missing_successful_cache(project, folder_name)
        total += len(missing)
    return total


def _log(project: Project, message: str) -> None:
    append_analysis_log(project, message)


def _is_cancelled(should_cancel: ShouldCancel | None) -> bool:
    return bool(should_cancel and should_cancel())


def _analyze_single_media(
    project: Project,
    folder_name: str,
    media_path: Path,
    *,
    use_api: bool,
    model: Optional[str],
) -> tuple[AssetMediaAnalysis, str]:
    resolved_path = resolve_media_for_analysis(project, folder_name, media_path)
    cache_file = media_cache_path(project, folder_name, resolved_path)
    per_file = max(1, project.frames_per_shot)

    _log(
        project,
        f"START {folder_name}/{resolved_path.name} "
        f"cache={cache_file.name} resolved={resolved_path}",
    )

    if has_successful_asset_cache(project, folder_name, resolved_path):
        cached = load_cached_media_for_asset(project, folder_name, resolved_path)
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

    frames_dir = _frames_dir(project, folder_name, resolved_path)
    frame_count = 1 if is_image_media(resolved_path) else per_file

    frames = extract_frames(resolved_path, frames_dir, frame_count)
    if not frames:
        frames = list_existing_frame_jpegs(frames_dir)[:frame_count]
    if not frames:
        shutil.rmtree(frames_dir, ignore_errors=True)
        frames = extract_frames(resolved_path, frames_dir, frame_count)
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
    if use_api:
        try:
            entry.description = describe_media_from_frames(
                resolved_path.name,
                folder_name,
                frames,
                project.language,
                model=model,
            )
            entry.error = None
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
            entry = _save_cached_media_safe(cache_file, entry)
            _log(project, f"FAIL (Gemini) {folder_name}/{resolved_path.name}: {exc}")
            return entry, "fehler"
    entry.error = "API-Aufruf nicht bestätigt."
    entry = _save_cached_media_safe(cache_file, entry)
    _log(project, f"FAIL (API nicht bestätigt) {folder_name}/{resolved_path.name}")
    return entry, "fehler"


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
) -> AssetFolderAnalysis:
    media_paths = discover_folder_media_paths(project, folder_name)
    missing_cache = list_assets_missing_successful_cache(project, folder_name)

    _log(
        project,
        f"ORDNER {folder_name}: {len(media_paths)} Medien, "
        f"{len(missing_cache)} ohne JSON: "
        + ", ".join(path.name for path in missing_cache),
    )

    if not missing_cache:
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
            "media_count": len(missing_cache),
            "missing_cache_count": len(missing_cache),
        },
    )

    analyzed: dict[str, AssetMediaAnalysis] = {}
    cancelled_mid_folder = False
    for media_index, media_path in enumerate(missing_cache, start=1):
        if _is_cancelled(should_cancel):
            cancelled_mid_folder = True
            _log(
                project,
                f"ORDNER-ABBRUCH {folder_name} nach {media_index - 1}/{len(missing_cache)} Assets",
            )
            break
        on_progress(
            "media_start",
            {
                "folder": folder_name,
                "media_name": media_path.name,
                "media_index": media_index,
                "media_count": len(missing_cache),
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
                    "media_count": len(missing_cache),
                    "folder_index": folder_index,
                    "folder_count": folder_count,
                    "outcome": outcome,
                    "error": entry.error,
                },
            )
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
                    "media_count": len(missing_cache),
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

    inventory_saved = sync_folder_inventory_with_status(project, folder_name)
    _log(
        project,
        f"ORDNER-FERTIG {folder_name}: {len(missing_cache)} geplant, "
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
) -> tuple[InventoryDocument, AnalysisRunReport]:
    """Analysiert fehlende Assets in Ordnern; Inventar-JSON nur bei vollständigem Ordner."""
    selected = validate_asset_selection(project.asset_subdir_names, folder_names)
    report = AnalysisRunReport()
    total_media = _count_media_to_analyze(project, selected)
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
