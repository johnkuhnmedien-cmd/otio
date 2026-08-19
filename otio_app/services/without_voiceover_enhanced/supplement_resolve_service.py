"""Sequenzielle Supplement-Auflösung: Download → Frames → LLM → PASS/FAIL.

Pro Coverage Gap werden Top-N gerankte Stock-Kandidaten nacheinander geprüft.
Beim ersten PASS wird das Gap geschlossen; FAIL löscht die temporären Dateien.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import requests

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis
from otio_app.defaults import SUPPLEMENTAL_FOLDER_NAME
from otio_app.models import Project
from otio_app.project_layout import get_folder_inventory_path, safe_folder_slug
from otio_app.services.api_keys import get_api_key, is_api_key_set
from otio_app.services.frame_extract import extract_frames
from otio_app.services.gemini_client import (
    describe_and_validate_supplement_asset,
    is_gemini_configured,
)
from otio_app.services.inventory_loader import load_folder_inventory, save_folder_inventory
from otio_app.services.media_utils import is_image_media
from otio_app.services.supplement_inventory import (
    INTAKE_SOURCE_FUNNEL,
    SupplementProvenance,
    ingest_supplement_asset,
    upsert_supplement_into_inventory,
)
from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
    load_cut_plan_options,
)
from otio_app.services.without_voiceover_enhanced.io_utils import load_model, write_json
from otio_app.services.without_voiceover_enhanced.local_media_service import (
    STATUS_EXPORT_READY,
    refresh_supplement_validation,
    validate_local_media_path,
)
from otio_app.services.without_voiceover_enhanced.models import (
    AcceptedSupplementsDocument,
    CoverageGap,
    CoverageGapsDocument,
    EnhancedScriptDocument,
    RoughCutPlanDocument,
    StockCandidate,
    StockSearchResultsDocument,
    SupplementResolveAttempt,
    SupplementResolveGapResult,
    SupplementResolveReport,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    accepted_supplements_path,
    coverage_gaps_path,
    rough_cut_plan_path,
    stock_candidate_download_dir,
    stock_search_results_path,
    supplement_resolve_report_path,
)
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    require_locked_script,
)
from otio_app.services.without_voiceover_enhanced.stock.http_utils import (
    STOCK_USER_AGENT,
)

logger = logging.getLogger(__name__)

DEFAULT_VALIDATION_MODEL = "gemini-3.5-flash"


@dataclass(frozen=True)
class SupplementResolveProgressEvent:
    """Live-Fortschritt für UI (Progress-Bar + Statuszeile)."""

    phase: str
    # gap_start | download | frames | llm | result | gap_done | finished
    gap_id: str = ""
    gap_index: int = 0
    gap_total: int = 0
    candidate_id: str = ""
    candidate_index: int = 0
    candidate_total: int = 0
    provider: str = ""
    status: str = ""
    message: str = ""
    fraction: float = 0.0


ProgressCallback = Callable[[SupplementResolveProgressEvent], None]


class SupplementResolveError(RuntimeError):
    pass


def _progress_fraction(
    *,
    gap_index: int,
    gap_total: int,
    candidate_index: int = 0,
    candidate_total: int = 0,
    within: float = 0.0,
) -> float:
    """Gesamtfortschritt 0..1 über Gaps; optional Anteil innerhalb des aktuellen Gaps."""
    if gap_total <= 0:
        return 0.0
    base = max(0, gap_index - 1) / gap_total
    slice_size = 1.0 / gap_total
    if candidate_total > 0 and candidate_index > 0:
        cand_frac = (candidate_index - 1 + max(0.0, min(1.0, within))) / candidate_total
        return min(1.0, base + slice_size * cand_frac)
    return min(1.0, base + slice_size * max(0.0, min(1.0, within)))


def _emit(
    progress_callback: ProgressCallback | None,
    event: SupplementResolveProgressEvent,
) -> None:
    if progress_callback is not None:
        progress_callback(event)


def _extension_for_candidate(candidate: StockCandidate, url: str) -> str:
    path = urlparse(url).path.lower()
    for ext in (".mp4", ".mov", ".webm", ".jpg", ".jpeg", ".png", ".webp"):
        if path.endswith(ext):
            return ext
    if (candidate.media_type or "").lower() == "video":
        return ".mp4"
    return ".jpg"


def rank_candidates_for_gap(
    candidates: list[StockCandidate],
    gap: CoverageGap,
) -> list[StockCandidate]:
    """Sortiert Treffer ohne Download — bessere zuerst."""
    preferred = (gap.preferred_media_type or "video").lower()

    def score(item: StockCandidate) -> tuple:
        media = (item.media_type or "").lower()
        media_score = 2 if media == preferred else (1 if media in {"photo", "image", "video"} else 0)
        has_download = 1 if (item.download_url or item.preview_url).strip() else 0
        has_license = 1 if item.license else 0
        pixels = int(item.width or 0) * int(item.height or 0)
        duration = float(item.duration_seconds or 0.0)
        # Zu kurze Videos leicht abwerten.
        duration_bonus = 1 if duration <= 0 or duration >= 2.0 else 0
        return (media_score, has_download, has_license, duration_bonus, pixels)

    return sorted(candidates, key=score, reverse=True)


def dedupe_stock_candidates(candidates: list[StockCandidate]) -> list[StockCandidate]:
    """Ein Eintrag pro candidate_id; erste Gap-Zuordnung bleibt."""
    seen: set[str] = set()
    unique: list[StockCandidate] = []
    for candidate in candidates:
        key = candidate.candidate_id
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def _candidates_for_gap(
    results: StockSearchResultsDocument,
    gap_id: str,
) -> list[StockCandidate]:
    return [c for c in results.candidates if c.gap_id == gap_id]


def _folder_for_gap(
    project: Project,
    gap: CoverageGap,
    locked: EnhancedScriptDocument,
) -> str:
    rough = load_model(rough_cut_plan_path(project), RoughCutPlanDocument)
    segment_ids: set[str] = set()
    if rough is not None:
        related = set(gap.related_shot_ids or [])
        for shot in rough.shots:
            if shot.shot_id in related or (
                gap.gap_id and shot.coverage_gap_id == gap.gap_id
            ):
                for anchor in (shot.start_anchor, shot.end_anchor):
                    sid = (
                        anchor.after_segment_id
                        if anchor.type == "pause"
                        else anchor.segment_id
                    )
                    if sid:
                        segment_ids.add(sid)
                if shot.narration_start_anchor.segment_id:
                    segment_ids.add(shot.narration_start_anchor.segment_id)
    for segment in locked.segments:
        if segment.segment_id in segment_ids and segment.folder_name:
            return segment.folder_name
    if project.selected_asset_subdirs:
        return project.selected_asset_subdirs[0]
    return SUPPLEMENTAL_FOLDER_NAME


def _passage_for_gap(gap: CoverageGap, locked: EnhancedScriptDocument) -> str:
    parts = [
        gap.needed_visual or "",
        gap.editorial_purpose or "",
        gap.subject or "",
        gap.reason or "",
    ]
    text = " ".join(p for p in parts if p).strip()
    if text:
        return text
    # Fallback: erste Skriptsegmente als Kontext.
    sample = " ".join(s.text for s in locked.segments[:3] if s.text)
    return sample or gap.gap_id


def download_stock_candidate(
    project: Project,
    candidate: StockCandidate,
    *,
    gap_id: str,
) -> Path:
    url = (candidate.download_url or candidate.preview_url or "").strip()
    if not url.startswith("http"):
        raise SupplementResolveError(
            f"{candidate.candidate_id}: keine Download-URL vorhanden."
        )
    target_dir = stock_candidate_download_dir(
        project, gap_id=gap_id, candidate_id=candidate.candidate_id
    )
    if target_dir.exists():
        shutil.rmtree(target_dir, ignore_errors=True)
    target_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{safe_folder_slug(candidate.candidate_id)}{_extension_for_candidate(candidate, url)}"
    target = target_dir / filename
    headers = {"User-Agent": STOCK_USER_AGENT}
    if candidate.provider == "pexels":
        key = get_api_key("PEXELS_API_KEY")
        if key:
            headers["Authorization"] = key
    response = requests.get(url, headers=headers, timeout=120, stream=True)
    response.raise_for_status()
    with target.open("wb") as handle:
        for chunk in response.iter_content(chunk_size=1024 * 256):
            if chunk:
                handle.write(chunk)
    if not target.is_file() or target.stat().st_size <= 0:
        raise SupplementResolveError(f"Download leer: {candidate.candidate_id}")
    return target


def _cleanup_candidate_dir(path: Path | None) -> None:
    if path is None:
        return
    root = path if path.is_dir() else path.parent
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)


def _extract_validation_frames(
    project: Project,
    media_path: Path,
) -> list[Path]:
    frames_dir = media_path.parent / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    count = 1 if is_image_media(media_path) else max(1, int(project.frames_per_shot or 3))
    frames = extract_frames(media_path, frames_dir, count)
    return [Path(p) for p in frames if Path(p).is_file()]


def _import_into_inventory(
    project: Project,
    *,
    folder_name: str,
    candidate: StockCandidate,
    media_path: Path,
    frames: list[Path],
    description: str,
    validation_status: str,
    validation_score: float,
    intake_source: str = INTAKE_SOURCE_FUNNEL,
) -> str:
    """Übergibt ein angenommenes Asset an das Inventar-Eingangstor.

    Das Asset bekommt dort dieselbe Analyse wie ein Original — ``description``
    ist dann die Bildbeschreibung, nicht mehr die Ranking-Begründung. Die
    Begründung bleibt als ``supplement_intake_note`` erhalten.

    Gleiche Provider-Asset-ID ersetzt ältere Inventar-Zeilen (keine Doppel-IDs
    für dasselbe Stock-Asset — sonst greifen max_usage / Reuse-Abstand nicht).
    """
    from otio_app.services.without_voiceover_enhanced.enhanced_supplement_dedupe import (
        preferred_inventory_asset_id,
        provider_identity_for_candidate,
    )

    identity = provider_identity_for_candidate(candidate)
    asset_id = preferred_inventory_asset_id(
        project, candidate, folder_name=folder_name
    )
    license_meta = {
        "license": candidate.license or "",
        "attribution": candidate.attribution or candidate.creator or "",
    }
    if identity is not None:
        license_meta["provider"] = identity.provider
        license_meta["provider_asset_id"] = identity.provider_asset_id

    provenance = SupplementProvenance(
        asset_id=asset_id,
        asset_origin=candidate.provider or "supplement",
        provider=candidate.provider,
        provider_asset_id=identity.provider_asset_id if identity else "",
        source_url=candidate.source_page or candidate.download_url,
        media_type=candidate.media_type
        or ("image" if is_image_media(media_path) else "video"),
        license_metadata=license_meta,
        supplement_validation_status=validation_status,
        supplement_validation_score=float(validation_score),
        approved_for_cut_plan=True,
        intake_source=intake_source,
        intake_note=(description or "").strip(),
        fallback_description=description or candidate.title or asset_id,
    )
    result = ingest_supplement_asset(
        project,
        folder_name=folder_name,
        media_path=media_path,
        provenance=provenance,
        use_api=is_gemini_configured(),
    )
    if frames and not result.asset.frames_used:
        # Validierungsframes behalten, wenn die Analyse keine eigenen lieferte.
        upsert_supplement_into_inventory(
            project,
            folder_name=folder_name,
            asset=result.asset.model_copy(
                update={"frames_used": [str(p) for p in frames]}
            ),
        )
    return folder_name


def _persist_accepted(
    project: Project,
    candidate: StockCandidate,
    *,
    media_path: Path,
) -> StockCandidate:
    status, error = validate_local_media_path(media_path, candidate.media_type)
    candidate.local_media_path = str(media_path)
    candidate.selected = True
    candidate.media_validation_status = status
    candidate.media_validation_error = error
    if status != STATUS_EXPORT_READY:
        # Technisch ungültig trotz LLM-PASS → nicht behalten.
        raise SupplementResolveError(
            f"{candidate.candidate_id}: technische Validierung fehlgeschlagen ({error})"
        )
    candidate = refresh_supplement_validation(candidate)
    from otio_app.services.without_voiceover_enhanced.gap_status_service import (
        compute_cut_plan_run_id_from_path,
    )
    from otio_app.services.without_voiceover_enhanced.models import CoverageGapsDocument
    from otio_app.services.without_voiceover_enhanced.paths import (
        coverage_gaps_path,
        unified_cut_plan_path,
    )

    coverage = load_model(coverage_gaps_path(project), CoverageGapsDocument)
    run_id = str(getattr(coverage, "cut_plan_run_id", "") or "").strip()
    if not run_id:
        run_id = compute_cut_plan_run_id_from_path(unified_cut_plan_path(project))
    if run_id:
        candidate.cut_plan_run_id = run_id

    existing = load_model(accepted_supplements_path(project), AcceptedSupplementsDocument)
    locked = require_locked_script(project)
    supplements = list(existing.supplements) if existing is not None else []
    supplements = [s for s in supplements if s.candidate_id != candidate.candidate_id]
    supplements.append(candidate)
    write_json(
        accepted_supplements_path(project),
        AcceptedSupplementsDocument(
            script_version=locked.script_version,
            supplements=supplements,
        ),
    )
    from otio_app.services.without_voiceover_enhanced.coverage_gap_external_export import (
        refresh_coverage_gaps_external_export,
    )

    refresh_coverage_gaps_external_export(project)
    return candidate


def _validate_with_llm(
    *,
    project: Project,
    candidate: StockCandidate,
    folder_name: str,
    frames: list[Path],
    passage_text: str,
    visual_requirement: str,
    must_include: list[str],
    must_avoid: list[str],
    llm_callable: Callable[..., dict] | None,
) -> dict:
    if llm_callable is not None:
        return llm_callable(
            media_name=candidate.candidate_id,
            folder_name=folder_name,
            frame_paths=frames,
            passage_text=passage_text,
            visual_requirement=visual_requirement,
            must_show=must_include,
            avoid_showing=must_avoid,
            language=project.language,
        )
    if not is_api_key_set("GEMINI_API_KEY"):
        return {
            "description": "",
            "status": "FAIL",
            "score": 0.0,
            "reason": "GEMINI_API_KEY fehlt — Supplement-Validierung nicht möglich.",
        }
    return describe_and_validate_supplement_asset(
        media_name=candidate.candidate_id,
        folder_name=folder_name,
        frame_paths=frames,
        passage_text=passage_text,
        visual_requirement=visual_requirement,
        location_name=folder_name,
        must_show=must_include,
        avoid_showing=must_avoid,
        language=project.language or "de",
        model=DEFAULT_VALIDATION_MODEL,
    )


def resolve_supplements_for_gaps(
    project: Project,
    *,
    max_candidates_per_gap: int | None = None,
    progress_callback: ProgressCallback | None = None,
    llm_callable: Callable[..., dict] | None = None,
    download_callable: Callable[..., Path] | None = None,
    only_gap_ids: list[str] | None = None,
    should_stop: Callable[[], bool] | None = None,
    merge_report: bool = False,
) -> SupplementResolveReport:
    """Haupt-Orchestrierung: sequenziell pro Gap bis erster PASS.

    only_gap_ids: optional nur diese Gaps verarbeiten (für Gap-für-Gap-UI).
    should_stop: kooperativer Abbruch zwischen Gaps/Kandidaten.
    merge_report: bestehende Report-Ergebnisse anderer Gaps behalten.
    """
    locked = require_locked_script(project)
    coverage = load_model(coverage_gaps_path(project), CoverageGapsDocument)
    results = load_model(stock_search_results_path(project), StockSearchResultsDocument)
    if coverage is None or not coverage.gaps:
        raise SupplementResolveError("Keine Coverage Gaps vorhanden.")
    if results is None or not results.candidates:
        raise SupplementResolveError("Keine Stockergebnisse — zuerst Stock suchen.")

    options = load_cut_plan_options(project)
    top_n = int(max_candidates_per_gap or options.max_candidates_per_gap)

    all_gaps = list(coverage.gaps)
    if only_gap_ids is not None:
        wanted = {str(g).strip() for g in only_gap_ids if str(g).strip()}
        gaps_to_process = [g for g in all_gaps if g.gap_id in wanted]
        if not gaps_to_process:
            raise SupplementResolveError(
                "Keine passenden Coverage Gaps für only_gap_ids."
            )
    else:
        gaps_to_process = all_gaps

    gap_index_by_id = {g.gap_id: i for i, g in enumerate(all_gaps, start=1)}
    total_gaps = len(all_gaps)

    reprocess_ids = {g.gap_id for g in gaps_to_process}
    if merge_report:
        existing = load_model(
            supplement_resolve_report_path(project), SupplementResolveReport
        )
        if existing is not None:
            report = existing
            report.script_version = locked.script_version
            report.max_candidates_per_gap = top_n
            report.stopped = False
            report.gaps = [g for g in report.gaps if g.gap_id not in reprocess_ids]
            report.filled_gap_ids = [
                x for x in report.filled_gap_ids if x not in reprocess_ids
            ]
            report.unfilled_gap_ids = [
                x for x in report.unfilled_gap_ids if x not in reprocess_ids
            ]
        else:
            report = SupplementResolveReport(
                script_version=locked.script_version,
                max_candidates_per_gap=top_n,
            )
    else:
        report = SupplementResolveReport(
            script_version=locked.script_version,
            max_candidates_per_gap=top_n,
        )

    def _stop_requested() -> bool:
        return bool(should_stop and should_stop())

    for gap in gaps_to_process:
        if _stop_requested():
            report.stopped = True
            break

        gap_index = gap_index_by_id.get(gap.gap_id, 1)
        gap_result = SupplementResolveGapResult(gap_id=gap.gap_id)
        folder_name = _folder_for_gap(project, gap, locked)
        passage = _passage_for_gap(gap, locked)
        visual = gap.needed_visual or gap.subject or passage
        ranked = rank_candidates_for_gap(
            _candidates_for_gap(results, gap.gap_id), gap
        )[:top_n]
        candidate_total = len(ranked)

        _emit(
            progress_callback,
            SupplementResolveProgressEvent(
                phase="gap_start",
                gap_id=gap.gap_id,
                gap_index=gap_index,
                gap_total=total_gaps,
                candidate_total=candidate_total,
                message=f"Gap {gap_index}/{total_gaps}: {gap.gap_id}",
                fraction=_progress_fraction(
                    gap_index=gap_index,
                    gap_total=total_gaps,
                    within=0.0,
                ),
            ),
        )

        if not ranked:
            gap_result.attempts.append(
                SupplementResolveAttempt(
                    gap_id=gap.gap_id,
                    candidate_id="",
                    status="SKIPPED",
                    reason="Keine Kandidaten für dieses Gap.",
                )
            )
            report.gaps.append(gap_result)
            report.unfilled_gap_ids.append(gap.gap_id)
            _emit(
                progress_callback,
                SupplementResolveProgressEvent(
                    phase="gap_done",
                    gap_id=gap.gap_id,
                    gap_index=gap_index,
                    gap_total=total_gaps,
                    status="SKIPPED",
                    message=f"Gap {gap_index}/{total_gaps}: keine Kandidaten",
                    fraction=_progress_fraction(
                        gap_index=gap_index,
                        gap_total=total_gaps,
                        within=1.0,
                    ),
                ),
            )
            continue

        for cand_index, candidate in enumerate(ranked, start=1):
            if _stop_requested():
                report.stopped = True
                gap_result.attempts.append(
                    SupplementResolveAttempt(
                        gap_id=gap.gap_id,
                        candidate_id=candidate.candidate_id,
                        provider=candidate.provider,
                        status="SKIPPED",
                        reason="Abbruch durch Benutzer.",
                    )
                )
                break

            attempt = SupplementResolveAttempt(
                gap_id=gap.gap_id,
                candidate_id=candidate.candidate_id,
                provider=candidate.provider,
            )
            media_path: Path | None = None
            try:
                _emit(
                    progress_callback,
                    SupplementResolveProgressEvent(
                        phase="download",
                        gap_id=gap.gap_id,
                        gap_index=gap_index,
                        gap_total=total_gaps,
                        candidate_id=candidate.candidate_id,
                        candidate_index=cand_index,
                        candidate_total=candidate_total,
                        provider=candidate.provider,
                        message=(
                            f"Gap {gap_index}/{total_gaps} · "
                            f"Kandidat {cand_index}/{candidate_total}: "
                            f"Download {candidate.provider}/{candidate.candidate_id}"
                        ),
                        fraction=_progress_fraction(
                            gap_index=gap_index,
                            gap_total=total_gaps,
                            candidate_index=cand_index,
                            candidate_total=candidate_total,
                            within=0.1,
                        ),
                    ),
                )
                if download_callable is not None:
                    media_path = download_callable(
                        project, candidate, gap_id=gap.gap_id
                    )
                else:
                    media_path = download_stock_candidate(
                        project, candidate, gap_id=gap.gap_id
                    )

                _emit(
                    progress_callback,
                    SupplementResolveProgressEvent(
                        phase="frames",
                        gap_id=gap.gap_id,
                        gap_index=gap_index,
                        gap_total=total_gaps,
                        candidate_id=candidate.candidate_id,
                        candidate_index=cand_index,
                        candidate_total=candidate_total,
                        provider=candidate.provider,
                        message=(
                            f"Gap {gap_index}/{total_gaps} · "
                            f"Kandidat {cand_index}/{candidate_total}: Frames…"
                        ),
                        fraction=_progress_fraction(
                            gap_index=gap_index,
                            gap_total=total_gaps,
                            candidate_index=cand_index,
                            candidate_total=candidate_total,
                            within=0.4,
                        ),
                    ),
                )
                frames = _extract_validation_frames(project, media_path)
                if not frames:
                    raise SupplementResolveError("Keine Frames extrahiert.")

                _emit(
                    progress_callback,
                    SupplementResolveProgressEvent(
                        phase="llm",
                        gap_id=gap.gap_id,
                        gap_index=gap_index,
                        gap_total=total_gaps,
                        candidate_id=candidate.candidate_id,
                        candidate_index=cand_index,
                        candidate_total=candidate_total,
                        provider=candidate.provider,
                        message=(
                            f"Gap {gap_index}/{total_gaps} · "
                            f"Kandidat {cand_index}/{candidate_total}: LLM-Match…"
                        ),
                        fraction=_progress_fraction(
                            gap_index=gap_index,
                            gap_total=total_gaps,
                            candidate_index=cand_index,
                            candidate_total=candidate_total,
                            within=0.7,
                        ),
                    ),
                )
                validation = _validate_with_llm(
                    project=project,
                    candidate=candidate,
                    folder_name=folder_name,
                    frames=frames,
                    passage_text=passage,
                    visual_requirement=visual,
                    must_include=list(gap.must_include or []),
                    must_avoid=list(gap.must_avoid or []),
                    llm_callable=llm_callable,
                )
                status = str(validation.get("status") or "FAIL").upper()
                attempt.status = status
                attempt.score = float(validation.get("score") or 0.0)
                attempt.reason = str(validation.get("reason") or "")
                attempt.description = str(validation.get("description") or "")
                attempt.local_media_path = str(media_path)
                attempt.frames_used = [str(p) for p in frames]

                _emit(
                    progress_callback,
                    SupplementResolveProgressEvent(
                        phase="result",
                        gap_id=gap.gap_id,
                        gap_index=gap_index,
                        gap_total=total_gaps,
                        candidate_id=candidate.candidate_id,
                        candidate_index=cand_index,
                        candidate_total=candidate_total,
                        provider=candidate.provider,
                        status=status,
                        message=(
                            f"Gap {gap_index}/{total_gaps} · "
                            f"{candidate.candidate_id} → {status}"
                            + (f" — {attempt.reason}" if attempt.reason else "")
                        ),
                        fraction=_progress_fraction(
                            gap_index=gap_index,
                            gap_total=total_gaps,
                            candidate_index=cand_index,
                            candidate_total=candidate_total,
                            within=1.0,
                        ),
                    ),
                )

                if status == "PASS":
                    _persist_accepted(project, candidate, media_path=media_path)
                    _import_into_inventory(
                        project,
                        folder_name=folder_name,
                        candidate=candidate,
                        media_path=media_path,
                        frames=frames,
                        description=attempt.description,
                        validation_status=status,
                        validation_score=attempt.score,
                    )
                    attempt.inventory_folder = folder_name
                    gap_result.filled = True
                    gap_result.accepted_candidate_id = candidate.candidate_id
                    gap_result.attempts.append(attempt)
                    break

                # WEAK_PASS / FAIL / NEEDS_USER_REVIEW → verwerfen und weiter.
                _cleanup_candidate_dir(media_path)
                attempt.local_media_path = None
                attempt.frames_used = []
                gap_result.attempts.append(attempt)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Supplement resolve failed for %s/%s: %s",
                    gap.gap_id,
                    candidate.candidate_id,
                    exc,
                )
                _cleanup_candidate_dir(media_path)
                attempt.status = (
                    "DOWNLOAD_FAILED"
                    if "Download" in str(exc) or "download" in str(exc).lower()
                    else "ERROR"
                )
                attempt.reason = str(exc)
                gap_result.attempts.append(attempt)
                _emit(
                    progress_callback,
                    SupplementResolveProgressEvent(
                        phase="result",
                        gap_id=gap.gap_id,
                        gap_index=gap_index,
                        gap_total=total_gaps,
                        candidate_id=candidate.candidate_id,
                        candidate_index=cand_index,
                        candidate_total=candidate_total,
                        provider=candidate.provider,
                        status=attempt.status,
                        message=(
                            f"Gap {gap_index}/{total_gaps} · "
                            f"{candidate.candidate_id} → {attempt.status}: {exc}"
                        ),
                        fraction=_progress_fraction(
                            gap_index=gap_index,
                            gap_total=total_gaps,
                            candidate_index=cand_index,
                            candidate_total=candidate_total,
                            within=1.0,
                        ),
                    ),
                )

        report.gaps.append(gap_result)
        if gap_result.filled:
            report.filled_gap_ids.append(gap.gap_id)
        elif report.stopped and not gap_result.filled:
            # Abbruch mitten im Gap: weder filled noch unfilled zählen.
            pass
        else:
            report.unfilled_gap_ids.append(gap.gap_id)
        _emit(
            progress_callback,
            SupplementResolveProgressEvent(
                phase="gap_done",
                gap_id=gap.gap_id,
                gap_index=gap_index,
                gap_total=total_gaps,
                status=(
                    "PASS"
                    if gap_result.filled
                    else ("STOPPED" if report.stopped else "UNFILLED")
                ),
                message=(
                    f"Gap {gap_index}/{total_gaps} fertig: "
                    + (
                        f"PASS `{gap_result.accepted_candidate_id}`"
                        if gap_result.filled
                        else (
                            "abgebrochen"
                            if report.stopped
                            else "kein Treffer"
                        )
                    )
                ),
                fraction=_progress_fraction(
                    gap_index=gap_index,
                    gap_total=total_gaps,
                    within=1.0,
                ),
            ),
        )
        if report.stopped:
            break

    processed = len(report.gaps)
    if report.stopped:
        report.message = (
            f"Abgebrochen nach {processed}/{total_gaps} Gaps · "
            f"{len(report.filled_gap_ids)} gefüllt · "
            f"{len(report.unfilled_gap_ids)} ohne Treffer"
        )
    else:
        report.message = (
            f"{len(report.filled_gap_ids)}/{total_gaps} Gaps gefüllt · "
            f"{len(report.unfilled_gap_ids)} offen"
        )
    write_json(supplement_resolve_report_path(project), report)
    _emit(
        progress_callback,
        SupplementResolveProgressEvent(
            phase="finished",
            gap_total=total_gaps,
            status="STOPPED" if report.stopped else "DONE",
            message=report.message,
            fraction=1.0 if not report.stopped else _progress_fraction(
                gap_index=max(1, processed),
                gap_total=total_gaps,
                within=1.0,
            ),
        ),
    )
    return report
