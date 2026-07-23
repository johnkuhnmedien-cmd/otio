"""Automatischer Supplement-Funnel: Text → Thumbnail → Download → Tech.

Semantische Auswahl endet mit dem Thumbnail-Ranking.
Nach dem Voll-Download: keine zweite LLM-Prüfung, keine Frame-Extraktion,
keine manuelle Freigabe. Technisch gültig → auto export_ready.
Lizenzmetadaten werden bestmöglich gespeichert, blockieren aber nicht.
"""

from __future__ import annotations

import logging
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from otio_app.models import Project
from otio_app.services.api_keys import get_api_key, is_api_key_set
from otio_app.services.gemini_client import describe_and_validate_supplement_asset
from otio_app.services.inventory_loader import load_folder_inventory
from otio_app.services.media_utils import file_sha256
from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
    load_cut_plan_options,
)
from otio_app.services.without_voiceover_enhanced.fit_bridge import (
    filter_candidates_by_duration,
    required_candidate_duration_seconds,
)
from otio_app.services.without_voiceover_enhanced.io_utils import load_model, write_json
from otio_app.services.without_voiceover_enhanced.local_media_service import (
    STATUS_EXPORT_READY,
    apply_license_export_gate,
    classify_license_metadata_status,
    list_export_ready_supplements,
    validate_local_media_path,
)
from otio_app.services.without_voiceover_enhanced.models import (
    AcceptedSupplementsDocument,
    CoverageGap,
    CoverageGapsDocument,
    EnhancedScriptDocument,
    FunnelCandidateRecord,
    RoughCutPlanDocument,
    StockCandidate,
    StockSearchResultsDocument,
    SupplementFunnelGapReport,
    SupplementFunnelReport,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    accepted_supplements_path,
    coverage_gaps_path,
    rough_cut_plan_path,
    stock_candidate_download_dir,
    stock_search_results_path,
    supplement_funnel_report_path,
)
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    require_locked_script,
)
from otio_app.services.without_voiceover_enhanced.stock.safe_fetch import (
    SafeFetchError,
    fetch_full_media_bytes,
)
from otio_app.services.without_voiceover_enhanced.stock_provider_config import (
    enabled_provider_names,
)
from otio_app.services.without_voiceover_enhanced.supplement_funnel_status import (
    transition,
)
from otio_app.services.without_voiceover_enhanced.supplement_resolve_service import (
    _cleanup_candidate_dir,
    _extension_for_candidate,
    _extract_validation_frames,
    _folder_for_gap,
    _import_into_inventory,
    _passage_for_gap,
    rank_candidates_for_gap,
)
from otio_app.services.without_voiceover_enhanced.supplement_thumbnail_rank_service import (
    FunnelRankError,
    compute_preliminary_score,
    default_funnel_text_llm,
    default_funnel_vision_llm,
    fetch_preview_bytes_for_candidate,
    format_provider_distribution,
    order_by_final_scores,
    pick_finalists_from_batches,
    run_final_comparison,
    run_text_ranking,
    run_thumbnail_batch,
    select_provider_balanced_candidates,
    split_thumbnail_batches,
)

logger = logging.getLogger(__name__)

DEFAULT_FUNNEL_MODEL = "gemini-3.5-flash"

# Re-export für Tests/Kompatibilität (nicht im Auto-Funnel-Pfad).
__all__ = [
    "FunnelProgressEvent",
    "SupplementFunnelError",
    "confirm_funnel_candidate",
    "download_full_candidate_safe",
    "list_open_funnel_gap_ids",
    "run_full_content_review",
    "run_supplement_funnel_for_gaps",
]


class SupplementFunnelError(RuntimeError):
    pass


@dataclass(frozen=True)
class FunnelProgressEvent:
    phase: str
    gap_id: str = ""
    gap_index: int = 0
    gap_total: int = 0
    message: str = ""
    fraction: float = 0.0


ProgressCallback = Callable[[FunnelProgressEvent], None]
TextLlm = Callable[[str], str]
VisionLlm = Callable[[str, list[tuple[str, bytes]]], str]
FullReviewLlm = Callable[..., dict]


def _emit(cb: ProgressCallback | None, event: FunnelProgressEvent) -> None:
    if cb is not None:
        cb(event)


def _utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _candidates_for_gap(
    results: StockSearchResultsDocument, gap_id: str
) -> list[StockCandidate]:
    return [c for c in results.candidates if c.gap_id == gap_id]


def _inventory_reuse_ids(project: Project, gap: CoverageGap) -> list[str]:
    """Lokale Originalassets + export_ready Supplements — nur Anzeige, keine Auto-Wahl."""
    ids: list[str] = []
    locked = require_locked_script(project)
    folder = _folder_for_gap(project, gap, locked)
    inventory = load_folder_inventory(project, folder)
    if inventory is not None:
        for asset in inventory.assets or []:
            if getattr(asset, "approved_for_cut_plan", False) or (
                getattr(asset, "analysis_status", "") == "complete"
            ):
                if asset.asset_id:
                    ids.append(str(asset.asset_id))
    for supplement in list_export_ready_supplements(project):
        if supplement.candidate_id and supplement.candidate_id not in ids:
            ids.append(supplement.candidate_id)
    return ids


def _gap_already_export_ready(
    previous: SupplementFunnelReport | None,
    *,
    gap_id: str,
    project: Project,
) -> bool:
    """Idempotenz: bereits export_ready Gaps nicht erneut laden."""
    if previous is not None:
        for gap_rep in previous.gaps:
            if gap_rep.gap_id != gap_id:
                continue
            if gap_rep.filled or gap_rep.export_ready_candidate_id:
                return True
            if any(c.funnel_status == "export_ready" for c in gap_rep.candidates):
                return True
    for supplement in list_export_ready_supplements(project):
        if (supplement.gap_id or "") == gap_id:
            return True
    return False


def _gap_context(
    project: Project,
    gap: CoverageGap,
    locked: EnhancedScriptDocument,
) -> dict[str, Any]:
    rough = load_model(rough_cut_plan_path(project), RoughCutPlanDocument)
    related = set(gap.related_shot_ids or [])
    shots = []
    if rough is not None:
        for shot in rough.shots:
            if shot.shot_id in related or shot.coverage_gap_id == gap.gap_id:
                shots.append(
                    {
                        "shot_id": shot.shot_id,
                        "narrative_function": shot.narrative_function
                        or shot.editorial_function,
                        "visual_intent": shot.visual_intent,
                    }
                )
    segments = [
        {"segment_id": s.segment_id, "text": s.text, "folder_name": s.folder_name}
        for s in locked.segments[:8]
    ]
    return {
        "shots": shots,
        "segments": segments,
        "style_profile": getattr(locked, "style_profile", None) or {},
        "passage": _passage_for_gap(gap, locked),
    }


def download_full_candidate_safe(
    project: Project,
    candidate: StockCandidate,
    *,
    gap_id: str,
) -> Path:
    """Voll-Download mit SSRF-Schutz; URL nie als local_media_path speichern."""
    url = (candidate.download_url or "").strip()
    if not url:
        raise SupplementFunnelError(
            f"{candidate.candidate_id}: keine Download-URL."
        )
    if url == (candidate.source_page or "").strip():
        raise SupplementFunnelError(
            f"{candidate.candidate_id}: Download-URL ist Source-Page."
        )
    headers: dict[str, str] = {}
    if candidate.provider == "pexels":
        key = get_api_key("PEXELS_API_KEY")
        if key:
            headers["Authorization"] = key
    try:
        fetched = fetch_full_media_bytes(
            url, provider=candidate.provider, headers=headers or None
        )
    except SafeFetchError as exc:
        raise SupplementFunnelError(str(exc)) from exc

    target_dir = stock_candidate_download_dir(
        project, gap_id=gap_id, candidate_id=candidate.candidate_id
    )
    if target_dir.exists():
        shutil.rmtree(target_dir, ignore_errors=True)
    target_dir.mkdir(parents=True, exist_ok=True)
    from otio_app.project_layout import safe_folder_slug

    target = target_dir / (
        f"{safe_folder_slug(candidate.candidate_id)}"
        f"{_extension_for_candidate(candidate, fetched.final_url)}"
    )
    target.write_bytes(fetched.content)
    if target.stat().st_size <= 0:
        raise SupplementFunnelError(f"Download leer: {candidate.candidate_id}")
    return target


def _map_full_review_decision(raw: dict[str, Any]) -> dict[str, Any]:
    """Mappt Gemini PASS/FAIL auf Funnel-Decisions (Legacy, nicht Auto-Pfad)."""
    status = str(raw.get("status") or raw.get("decision") or "").upper()
    decision = str(raw.get("decision") or "").strip()
    if decision in {"review_ready", "reject", "manual_review_required"}:
        mapped = decision
    elif status == "PASS":
        mapped = "review_ready"
    elif status in {"WEAK_PASS", "NEEDS_USER_REVIEW"}:
        mapped = "manual_review_required"
    else:
        mapped = "reject"
    return {
        "candidate_id": str(raw.get("candidate_id") or ""),
        "decision": mapped,
        "semantic_fit": int(raw.get("semantic_fit") or raw.get("score") or 0)
        if not isinstance(raw.get("score"), float)
        else int(round(float(raw.get("score", 0)) * 100)),
        "editorial_function_fit": int(raw.get("editorial_function_fit") or 0),
        "style_fit": int(raw.get("style_fit") or 0),
        "visual_quality": int(raw.get("visual_quality") or 0),
        "misrepresentation_risk": int(raw.get("misrepresentation_risk") or 0),
        "reason": str(raw.get("reason") or ""),
        "description": str(raw.get("description") or ""),
        "score": float(raw.get("score") or 0.0),
    }


def run_full_content_review(
    *,
    project: Project,
    candidate: StockCandidate,
    media_path: Path,
    gap: CoverageGap,
    locked: EnhancedScriptDocument,
    llm_callable: FullReviewLlm | None = None,
) -> dict[str, Any]:
    """Legacy Full-Review (Frames + LLM). Nicht Teil des automatischen Funnels."""
    folder = _folder_for_gap(project, gap, locked)
    passage = _passage_for_gap(gap, locked)
    visual = gap.needed_visual or gap.subject or passage

    status, error = validate_local_media_path(media_path, candidate.media_type)
    if status != STATUS_EXPORT_READY:
        return {
            "decision": "reject",
            "reason": error or "local_media_invalid",
            "technically_valid": False,
        }

    frames = _extract_validation_frames(project, media_path)
    if not frames:
        return {
            "decision": "reject",
            "reason": "Keine Frames extrahiert.",
            "technically_valid": True,
        }

    if llm_callable is not None:
        raw = llm_callable(
            media_name=candidate.candidate_id,
            folder_name=folder,
            frame_paths=frames,
            passage_text=passage,
            visual_requirement=visual,
            must_show=list(gap.must_include or []),
            avoid_showing=list(gap.must_avoid or []),
            language=project.language,
            candidate_id=candidate.candidate_id,
        )
    else:
        if not is_api_key_set("GEMINI_API_KEY"):
            return {
                "decision": "manual_review_required",
                "reason": "GEMINI_API_KEY fehlt — keine Auto-Annahme.",
                "technically_valid": True,
                "frames": frames,
            }
        raw = describe_and_validate_supplement_asset(
            media_name=candidate.candidate_id,
            folder_name=folder,
            frame_paths=frames,
            passage_text=passage,
            visual_requirement=visual,
            location_name=folder,
            must_show=list(gap.must_include or []),
            avoid_showing=list(gap.must_avoid or []),
            language=project.language or "de",
            model=DEFAULT_FUNNEL_MODEL,
        )
    mapped = _map_full_review_decision(raw)
    mapped["technically_valid"] = True
    mapped["frames"] = frames
    mapped["candidate_id"] = candidate.candidate_id
    return mapped


def confirm_funnel_candidate(
    project: Project,
    *,
    gap_id: str,
    candidate_id: str,
) -> StockCandidate:
    """Legacy-Freigabe für historische review_ready-Dokumente.

    Nicht Teil des automatischen Hauptwegs (R2).
    """
    report = load_model(supplement_funnel_report_path(project), SupplementFunnelReport)
    if report is None:
        raise SupplementFunnelError("Kein Funnel-Report vorhanden.")
    gap_report = next((g for g in report.gaps if g.gap_id == gap_id), None)
    if gap_report is None:
        raise SupplementFunnelError(f"Gap nicht im Report: {gap_id}")
    record = next(
        (c for c in gap_report.candidates if c.candidate_id == candidate_id), None
    )
    if record is None:
        raise SupplementFunnelError(f"Kandidat nicht im Report: {candidate_id}")
    if record.funnel_status not in {
        "review_ready",
        "manual_review_required",
        "license_review_required",
        "technically_valid",
        "selected",
    }:
        raise SupplementFunnelError(
            f"Kandidat nicht freigabefähig (Status={record.funnel_status})."
        )
    if not record.local_media_path:
        raise SupplementFunnelError("Kein lokaler Medienpfad.")
    media_path = Path(record.local_media_path)
    if not media_path.is_file():
        raise SupplementFunnelError("Lokale Datei fehlt.")

    results = load_model(stock_search_results_path(project), StockSearchResultsDocument)
    if results is None:
        raise SupplementFunnelError("Keine Stockergebnisse.")
    candidate = next(
        (c for c in results.candidates if c.candidate_id == candidate_id), None
    )
    if candidate is None:
        raise SupplementFunnelError("Kandidat nicht in Search Results.")

    status, error = validate_local_media_path(media_path, candidate.media_type)
    if status != STATUS_EXPORT_READY:
        raise SupplementFunnelError(f"Technisch ungültig: {error}")

    candidate.local_media_path = str(media_path)
    candidate.selected = True
    candidate.funnel_managed = True
    record.funnel_status = transition(record.funnel_status, "selected")
    record.review_status = "accepted"
    candidate = apply_license_export_gate(candidate)

    return _persist_export_ready(
        project,
        report=report,
        gap_report=gap_report,
        record=record,
        candidate=candidate,
        media_path=media_path,
        gap_id=gap_id,
    )


def _persist_export_ready(
    project: Project,
    *,
    report: SupplementFunnelReport,
    gap_report: SupplementFunnelGapReport,
    record: FunnelCandidateRecord,
    candidate: StockCandidate,
    media_path: Path,
    gap_id: str,
) -> StockCandidate:
    """technically_valid → selected → export_ready → Accepted + Inventar."""
    if record.funnel_status != "selected":
        record.funnel_status = transition(record.funnel_status, "selected")
    record.funnel_status = transition(record.funnel_status, "export_ready")
    record.review_status = "accepted"
    record.download_status = "accepted"
    record.local_media_path = str(media_path)
    record.sha256 = file_sha256(media_path)
    record.fetched_at = record.fetched_at or _utcnow()
    record.license_name = candidate.license
    record.license_url = getattr(candidate, "license_url", None)
    record.source_page = candidate.source_page
    record.creator = candidate.creator
    record.attribution = candidate.attribution
    license_status = classify_license_metadata_status(candidate)
    record.license_metadata_status = license_status
    gap_report.license_metadata_status = license_status
    if license_status != "complete":
        report.license_incomplete_count += 1

    candidate.local_media_path = str(media_path)
    candidate.selected = True
    candidate.funnel_managed = True
    candidate.media_validation_status = STATUS_EXPORT_READY
    candidate.media_validation_error = None
    candidate.license_metadata_status = license_status
    candidate.gap_id = gap_id

    locked = require_locked_script(project)
    existing = load_model(accepted_supplements_path(project), AcceptedSupplementsDocument)
    supplements = list(existing.supplements) if existing else []
    supplements = [s for s in supplements if s.candidate_id != candidate.candidate_id]
    supplements.append(candidate)
    write_json(
        accepted_supplements_path(project),
        AcceptedSupplementsDocument(
            script_version=locked.script_version, supplements=supplements
        ),
    )

    coverage = load_model(coverage_gaps_path(project), CoverageGapsDocument)
    gap = None
    if coverage is not None:
        gap = next((g for g in coverage.gaps if g.gap_id == gap_id), None)
    if gap is not None:
        folder = _folder_for_gap(project, gap, locked)
        from otio_app.services.without_voiceover_enhanced.supplement_clean_media import (
            ensure_new_supplement_clean_media,
        )

        cleaned = ensure_new_supplement_clean_media(
            project, folder_name=folder, media_path=media_path
        )
        media_path = cleaned
        record.local_media_path = str(media_path)
        candidate.local_media_path = str(media_path)
        # Keine Validierungsframes im Auto-Funnel — Inventar ohne Framepfade.
        _import_into_inventory(
            project,
            folder_name=folder,
            candidate=candidate,
            media_path=media_path,
            frames=[],
            description=record.reason or candidate.title or candidate.candidate_id,
            validation_status="PASS",
            validation_score=float(record.final_score or 0) / 100.0,
        )

    gap_report.export_ready_candidate_id = candidate.candidate_id
    gap_report.filled = True
    gap_report.review_ready_candidate_id = None
    _upsert_gap_report(report, gap_report)
    write_json(supplement_funnel_report_path(project), report)
    return candidate


def _upsert_gap_report(
    report: SupplementFunnelReport, gap_report: SupplementFunnelGapReport
) -> None:
    for index, existing_gap in enumerate(report.gaps):
        if existing_gap.gap_id == gap_report.gap_id:
            report.gaps[index] = gap_report
            return
    report.gaps.append(gap_report)


def _try_auto_accept_candidate(
    project: Project,
    *,
    report: SupplementFunnelReport,
    gap_report: SupplementFunnelGapReport,
    record: FunnelCandidateRecord,
    candidate: StockCandidate,
    media_path: Path,
    gap: CoverageGap,
    locked: EnhancedScriptDocument,
    progress_callback: ProgressCallback | None,
    gap_index: int,
    gap_total: int,
) -> str:
    """Technische Prüfung. Rückgabe: accepted | local_media_invalid.

    Lizenzmetadaten werden gespeichert, blockieren aber nicht (R3).
    """
    del locked  # Kontext nur für Signatur-Kompatibilität
    _emit(
        progress_callback,
        FunnelProgressEvent(
            phase="technical",
            gap_id=gap.gap_id,
            gap_index=gap_index,
            gap_total=gap_total,
            message=f"Gap {gap_index}/{gap_total} · Technische Prüfung",
            fraction=(gap_index - 0.15) / max(1, gap_total),
        ),
    )
    status, error = validate_local_media_path(media_path, candidate.media_type)
    if status != STATUS_EXPORT_READY:
        record.funnel_status = transition(record.funnel_status, "local_media_invalid")
        record.download_status = "invalid"
        record.reason = error or "local_media_invalid"
        _cleanup_candidate_dir(media_path)
        return "local_media_invalid"

    record.funnel_status = transition(record.funnel_status, "technically_valid")
    record.local_media_path = str(media_path)
    record.sha256 = file_sha256(media_path)
    record.fetched_at = _utcnow()
    # Vorhandene Herkunfts-/Lizenzdaten speichern — nichts erfinden.
    record.license_name = candidate.license
    record.license_url = getattr(candidate, "license_url", None)
    record.source_page = candidate.source_page
    record.creator = candidate.creator
    record.attribution = candidate.attribution
    record.license_metadata_status = classify_license_metadata_status(candidate)

    candidate.local_media_path = str(media_path)
    candidate.selected = True
    candidate.funnel_managed = True
    candidate = apply_license_export_gate(candidate)

    _persist_export_ready(
        project,
        report=report,
        gap_report=gap_report,
        record=record,
        candidate=candidate,
        media_path=media_path,
        gap_id=gap.gap_id,
    )
    _emit(
        progress_callback,
        FunnelProgressEvent(
            phase="accepted",
            gap_id=gap.gap_id,
            gap_index=gap_index,
            gap_total=gap_total,
            message=(
                f"Gap {gap_index}/{gap_total} · Übernommen: {candidate.candidate_id}"
            ),
            fraction=gap_index / max(1, gap_total),
        ),
    )
    return "accepted"


def list_open_funnel_gap_ids(project: Project) -> list[str]:
    """Offene Coverage-Gap-IDs in Coverage-Dokument-Reihenfolge."""
    coverage = load_model(coverage_gaps_path(project), CoverageGapsDocument)
    if coverage is None or not coverage.gaps:
        return []
    previous = load_model(supplement_funnel_report_path(project), SupplementFunnelReport)
    open_ids: list[str] = []
    for gap in coverage.gaps:
        if not _gap_already_export_ready(
            previous, gap_id=gap.gap_id, project=project
        ):
            open_ids.append(gap.gap_id)
    return open_ids


def _resolve_requested_gaps(
    coverage: CoverageGapsDocument,
    *,
    gap_ids: list[str] | None,
    only_gap_ids: list[str] | None,
) -> list[CoverageGap]:
    """Validiert und ordnet angeforderte Gap-IDs deterministisch."""
    requested = gap_ids if gap_ids is not None else only_gap_ids
    if requested is None:
        return list(coverage.gaps)
    by_id = {g.gap_id: g for g in coverage.gaps}
    ordered: list[CoverageGap] = []
    seen: set[str] = set()
    for raw in requested:
        gid = str(raw or "").strip()
        if not gid:
            raise SupplementFunnelError("Leere Gap-ID ist nicht erlaubt.")
        if gid in seen:
            raise SupplementFunnelError(f"Doppelte Gap-ID: {gid}")
        if gid not in by_id:
            raise SupplementFunnelError(f"Unbekannte Gap-ID: {gid}")
        seen.add(gid)
        ordered.append(by_id[gid])
    return ordered


def run_supplement_funnel_for_gaps(
    project: Project,
    *,
    max_candidates_per_gap: int | None = None,
    max_full_download_attempts: int | None = None,
    gap_ids: list[str] | None = None,
    only_gap_ids: list[str] | None = None,
    skip_filled: bool = True,
    force_restart: bool = False,
    progress_callback: ProgressCallback | None = None,
    text_llm: TextLlm | None = None,
    vision_llm: VisionLlm | None = None,
    full_review_llm: FullReviewLlm | None = None,
    preview_fetch: Callable[..., Any] | None = None,
    download_callable: Callable[..., Path] | None = None,
    should_stop: Callable[[], bool] | None = None,
    model: str | None = None,
) -> SupplementFunnelReport:
    """Haupt-Orchestrierung: Thumbnail-Ranking ist letzte semantische Stufe.

    ``gap_ids`` (bevorzugt) oder ``only_gap_ids``: explizite Gap-Liste.
    ``full_review_llm`` wird ignoriert — kein zweiter LLM-Call.
    ``model``: Gemini-Modell-ID für Text-/Thumbnail-Ranking (Default:
    ``DEFAULT_FUNNEL_MODEL``). Wird ignoriert, wenn ``text_llm`` /
    ``vision_llm`` explizit übergeben werden.
    """
    del full_review_llm  # bewusst nicht verwendet (R2/R3)

    locked = require_locked_script(project)
    coverage = load_model(coverage_gaps_path(project), CoverageGapsDocument)
    results = load_model(stock_search_results_path(project), StockSearchResultsDocument)
    if coverage is None or not coverage.gaps:
        raise SupplementFunnelError("Keine Coverage Gaps vorhanden.")
    if results is None or not results.candidates:
        raise SupplementFunnelError("Keine Stockergebnisse — zuerst Stock suchen.")

    options = load_cut_plan_options(project)
    top_n = int(max_candidates_per_gap or options.max_candidates_per_gap or 20)
    top_n = max(1, min(20, top_n))
    max_downloads = int(
        max_full_download_attempts
        or options.max_full_download_attempts_per_gap
        or 3
    )
    max_downloads = max(1, min(3, max_downloads))
    enabled = set(enabled_provider_names(project))
    funnel_model = (model or DEFAULT_FUNNEL_MODEL).strip() or DEFAULT_FUNNEL_MODEL
    if text_llm is None:
        text_llm = lambda prompt: default_funnel_text_llm(
            prompt, model=funnel_model
        )
    if vision_llm is None:
        vision_llm = (
            lambda prompt, images: default_funnel_vision_llm(
                prompt, images, model=funnel_model
            )
        )

    gaps = _resolve_requested_gaps(
        coverage, gap_ids=gap_ids, only_gap_ids=only_gap_ids
    )
    run_id = f"funnel_{uuid.uuid4().hex[:12]}"
    report = SupplementFunnelReport(
        run_id=run_id,
        script_version=locked.script_version,
        max_candidates_per_gap=top_n,
        max_full_download_attempts_per_gap=max_downloads,
        llm_model=funnel_model,
        requested_gap_ids=[g.gap_id for g in gaps],
    )

    previous = load_model(supplement_funnel_report_path(project), SupplementFunnelReport)

    total = len(gaps)
    for gap_index, gap in enumerate(gaps, start=1):
        if should_stop and should_stop():
            report.stopped = True
            break
        if skip_filled and not force_restart and _gap_already_export_ready(
            previous, gap_id=gap.gap_id, project=project
        ):
            report.skipped_gap_ids.append(gap.gap_id)
            _emit(
                progress_callback,
                FunnelProgressEvent(
                    phase="gap_skip",
                    gap_id=gap.gap_id,
                    gap_index=gap_index,
                    gap_total=total,
                    message=(
                        f"Gap {gap_index}/{total}: übersprungen "
                        "(bereits export_ready)"
                    ),
                    fraction=gap_index / max(1, total),
                ),
            )
            continue

        _emit(
            progress_callback,
            FunnelProgressEvent(
                phase="gap_start",
                gap_id=gap.gap_id,
                gap_index=gap_index,
                gap_total=total,
                message=f"Gap {gap_index}/{total}",
                fraction=(gap_index - 1) / max(1, total),
            ),
        )

        gap_report = SupplementFunnelGapReport(gap_id=gap.gap_id, run_id=run_id)
        gap_report.inventory_reuse_ids = _inventory_reuse_ids(project, gap)
        context = _gap_context(project, gap, locked)

        raw_candidates = _candidates_for_gap(results, gap.gap_id)
        ranked_meta = rank_candidates_for_gap(raw_candidates, gap)
        # Phase 4: harter Dauer-Vorfilter VOR Text-/Thumbnail-Scoring.
        min_duration = required_candidate_duration_seconds(
            gap.target_duration_seconds,
            head_trim=float(options.video_head_trim_sec or 0.0),
            short_tolerance=float(options.short_asset_tolerance_sec or 0.0),
        )
        ranked_meta, duration_excluded = filter_candidates_by_duration(
            ranked_meta, min_duration=min_duration
        )
        if duration_excluded:
            gap_report.message = (
                f"Dauer-Vorfilter: {len(duration_excluded)} Kandidat(en) "
                f"ausgeschlossen"
                + (
                    f" (min {min_duration:.2f}s)."
                    if min_duration is not None
                    else "."
                )
            )
        pool = select_provider_balanced_candidates(
            ranked_meta,
            enabled_providers=enabled,
            preferred_media_type=gap.preferred_media_type,
            limit=top_n,
            provider_status=results.provider_status or {},
        )
        selected = list(pool.candidates)
        gap_report.candidate_pool_limit = pool.candidate_pool_limit
        gap_report.eligible_providers = list(pool.eligible_providers)
        gap_report.provider_candidate_counts = dict(pool.provider_candidate_counts)
        by_id = {c.candidate_id: c for c in selected}
        records: list[FunnelCandidateRecord] = [
            FunnelCandidateRecord(
                candidate_id=c.candidate_id,
                provider=c.provider,
                provider_asset_id=c.provider_asset_id,
                funnel_status="discovered",
                license_name=c.license,
                creator=c.creator,
                source_page=c.source_page,
                attribution=c.attribution,
            )
            for c in selected
        ]
        record_by_id = {r.candidate_id: r for r in records}

        if not selected:
            gap_report.message = "Keine geeigneten Kandidaten."
            report.gaps.append(gap_report)
            report.open_gap_ids.append(gap.gap_id)
            continue

        distribution = format_provider_distribution(pool.provider_candidate_counts)
        _emit(
            progress_callback,
            FunnelProgressEvent(
                phase="candidates",
                gap_id=gap.gap_id,
                gap_index=gap_index,
                gap_total=total,
                message=(
                    f"Gap {gap_index}/{total} · "
                    f"{len(selected)} Kandidaten ausgewählt"
                ),
                fraction=(gap_index - 0.92) / max(1, total),
            ),
        )
        if distribution:
            _emit(
                progress_callback,
                FunnelProgressEvent(
                    phase="provider_distribution",
                    gap_id=gap.gap_id,
                    gap_index=gap_index,
                    gap_total=total,
                    message=f"Gap {gap_index}/{total} · Providerverteilung: {distribution}",
                    fraction=(gap_index - 0.9) / max(1, total),
                ),
            )

        if should_stop and should_stop():
            report.stopped = True
            gap_report.candidates = records
            gap_report.message = "Abgebrochen vor Textprüfung."
            if gap.gap_id not in report.open_gap_ids:
                report.open_gap_ids.append(gap.gap_id)
            _upsert_gap_report(report, gap_report)
            _emit(
                progress_callback,
                FunnelProgressEvent(
                    phase="stopped",
                    gap_id=gap.gap_id,
                    gap_index=gap_index,
                    gap_total=total,
                    message=f"Gap {gap_index}/{total} · Abbruch…",
                    fraction=gap_index / max(1, total),
                ),
            )
            break

        # Textbewertung
        _emit(
            progress_callback,
            FunnelProgressEvent(
                phase="text_rank",
                gap_id=gap.gap_id,
                gap_index=gap_index,
                gap_total=total,
                message=f"Gap {gap_index}/{total} · Textprüfung",
                fraction=(gap_index - 0.8) / max(1, total),
            ),
        )
        try:
            text_scores = run_text_ranking(
                gap=gap,
                candidates=selected,
                context=context,
                text_llm=text_llm,
            )
        except FunnelRankError as exc:
            gap_report.message = f"Textprüfung fehlgeschlagen: {exc}"
            report.gaps.append(gap_report)
            report.open_gap_ids.append(gap.gap_id)
            continue

        if should_stop and should_stop():
            report.stopped = True
            gap_report.candidates = records
            gap_report.message = "Abgebrochen nach Textprüfung."
            if gap.gap_id not in report.open_gap_ids:
                report.open_gap_ids.append(gap.gap_id)
            _upsert_gap_report(report, gap_report)
            _emit(
                progress_callback,
                FunnelProgressEvent(
                    phase="stopped",
                    gap_id=gap.gap_id,
                    gap_index=gap_index,
                    gap_total=total,
                    message=f"Gap {gap_index}/{total} · Abbruch…",
                    fraction=gap_index / max(1, total),
                ),
            )
            break

        for cid, scores in text_scores.items():
            record = record_by_id[cid]
            record.text_scores = scores
            record.funnel_status = transition(record.funnel_status, "text_ranked")

        # Previews + Thumbnail-Batches
        preview_bytes: dict[str, bytes] = {}
        scored_ids: list[str] = []
        for record in records:
            if should_stop and should_stop():
                report.stopped = True
                break
            candidate = by_id[record.candidate_id]
            record.funnel_status = transition(record.funnel_status, "thumbnail_pending")
            data, preview_status = fetch_preview_bytes_for_candidate(
                candidate, fetch_callable=preview_fetch
            )
            if data is None:
                record.preview_status = "unavailable"
                record.funnel_status = transition(
                    record.funnel_status, "thumbnail_unavailable"
                )
            else:
                preview_bytes[record.candidate_id] = data
                record.preview_status = "scored"
                scored_ids.append(record.candidate_id)

        if report.stopped:
            preview_bytes.clear()
            gap_report.candidates = records
            gap_report.message = "Abgebrochen während Preview-Download."
            if gap.gap_id not in report.open_gap_ids:
                report.open_gap_ids.append(gap.gap_id)
            _upsert_gap_report(report, gap_report)
            _emit(
                progress_callback,
                FunnelProgressEvent(
                    phase="stopped",
                    gap_id=gap.gap_id,
                    gap_index=gap_index,
                    gap_total=total,
                    message=f"Gap {gap_index}/{total} · Abbruch…",
                    fraction=gap_index / max(1, total),
                ),
            )
            break

        batches = split_thumbnail_batches(scored_ids)
        _emit(
            progress_callback,
            FunnelProgressEvent(
                phase="thumbnail",
                gap_id=gap.gap_id,
                gap_index=gap_index,
                gap_total=total,
                message=(
                    f"Gap {gap_index}/{total} · Thumbnailprüfung "
                    f"Batch 0/{len(batches)}"
                ),
                fraction=(gap_index - 0.6) / max(1, total),
            ),
        )
        for batch_index, batch in enumerate(batches, start=1):
            if should_stop and should_stop():
                report.stopped = True
                break
            batch_candidates = [by_id[cid] for cid in batch]
            _emit(
                progress_callback,
                FunnelProgressEvent(
                    phase="thumbnail",
                    gap_id=gap.gap_id,
                    gap_index=gap_index,
                    gap_total=total,
                    message=(
                        f"Gap {gap_index}/{total} · Thumbnailprüfung "
                        f"Batch {batch_index}/{len(batches)}"
                    ),
                    fraction=(
                        gap_index - 0.55 + 0.1 * batch_index / max(1, len(batches))
                    )
                    / max(1, total),
                ),
            )
            try:
                thumb_scores = run_thumbnail_batch(
                    gap=gap,
                    candidates=batch_candidates,
                    preview_bytes=preview_bytes,
                    context=context,
                    vision_llm=vision_llm,
                )
            except FunnelRankError as exc:
                gap_report.message = f"Thumbnailprüfung fehlgeschlagen: {exc}"
                preview_bytes.clear()
                report.gaps.append(gap_report)
                report.open_gap_ids.append(gap.gap_id)
                batches = []
                break
            for cid, scores in thumb_scores.items():
                record = record_by_id[cid]
                record.thumbnail_scores = scores
                record.preliminary_score = compute_preliminary_score(
                    text_relevance=record.text_scores.text_relevance,
                    semantic_fit=scores.semantic_fit,
                    editorial_function_fit=scores.editorial_function_fit,
                    style_fit=scores.style_fit,
                    continuity_fit=scores.continuity_fit,
                    composition_quality=scores.composition_quality,
                    visual_quality=scores.visual_quality,
                    misrepresentation_risk=max(
                        scores.misrepresentation_risk,
                        record.text_scores.misrepresentation_risk,
                    ),
                )
                record.funnel_status = transition(
                    record.funnel_status, "thumbnail_scored"
                )

        if report.stopped:
            preview_bytes.clear()
            gap_report.candidates = records
            gap_report.message = "Abgebrochen während Thumbnailprüfung."
            if gap.gap_id not in report.open_gap_ids:
                report.open_gap_ids.append(gap.gap_id)
            _upsert_gap_report(report, gap_report)
            break
        if gap.gap_id in report.open_gap_ids and gap_report.message.startswith(
            "Thumbnail"
        ):
            continue

        for record in records:
            if record.preview_status != "scored":
                record.preliminary_score = compute_preliminary_score(
                    text_relevance=record.text_scores.text_relevance,
                    semantic_fit=0,
                    editorial_function_fit=0,
                    style_fit=0,
                    continuity_fit=0,
                    composition_quality=0,
                    visual_quality=0,
                    misrepresentation_risk=record.text_scores.misrepresentation_risk,
                )

        if should_stop and should_stop():
            report.stopped = True
            preview_bytes.clear()
            gap_report.candidates = records
            gap_report.message = "Abgebrochen vor Finalvergleich."
            if gap.gap_id not in report.open_gap_ids:
                report.open_gap_ids.append(gap.gap_id)
            _upsert_gap_report(report, gap_report)
            break

        finalist_ids = pick_finalists_from_batches(records, batch_ids=batches)
        if not finalist_ids:
            gap_report.candidates = records
            gap_report.message = "Keine Thumbnail-Finalisten (Previews fehlen)."
            report.gaps.append(gap_report)
            report.open_gap_ids.append(gap.gap_id)
            preview_bytes.clear()
            continue

        _emit(
            progress_callback,
            FunnelProgressEvent(
                phase="final_compare",
                gap_id=gap.gap_id,
                gap_index=gap_index,
                gap_total=total,
                message=f"Gap {gap_index}/{total} · Finalvergleich",
                fraction=(gap_index - 0.35) / max(1, total),
            ),
        )
        try:
            final_payload = run_final_comparison(
                gap=gap,
                candidates=[by_id[cid] for cid in finalist_ids],
                preview_bytes=preview_bytes,
                context=context,
                vision_llm=vision_llm,
            )
            ordered_finalists = order_by_final_scores(
                [record_by_id[cid] for cid in finalist_ids],
                final_payload,
            )
        except FunnelRankError as exc:
            gap_report.candidates = records
            gap_report.message = f"Finalvergleich fehlgeschlagen: {exc}"
            report.gaps.append(gap_report)
            report.open_gap_ids.append(gap.gap_id)
            preview_bytes.clear()
            continue

        # Previewbytes nach Calls verwerfen (keine Persistenz).
        preview_bytes.clear()

        download_order = [
            r
            for r in ordered_finalists
            if r.decision in {"winner", "fallback"} and r.fit_bucket != "reject"
        ]
        if not download_order:
            gap_report.candidates = records
            gap_report.message = "Nur manual_review — kein automatischer Download."
            report.gaps.append(gap_report)
            report.open_gap_ids.append(gap.gap_id)
            continue

        gap_report.winner_candidate_id = download_order[0].candidate_id
        accepted_id: str | None = None

        for rank_index, finalist in enumerate(download_order[:max_downloads], start=1):
            if should_stop and should_stop():
                report.stopped = True
                break
            gap_report.full_download_attempts += 1
            report.full_download_count += 1
            if rank_index > 1:
                gap_report.fallback_used = True
            candidate = by_id[finalist.candidate_id]
            record = record_by_id[finalist.candidate_id]
            record.funnel_status = transition(record.funnel_status, "download_pending")
            record.download_status = "pending"
            _emit(
                progress_callback,
                FunnelProgressEvent(
                    phase="download",
                    gap_id=gap.gap_id,
                    gap_index=gap_index,
                    gap_total=total,
                    message=(
                        f"Gap {gap_index}/{total} · Download Rang "
                        f"{rank_index}/{max_downloads}"
                    ),
                    fraction=(gap_index - 0.25) / max(1, total),
                ),
            )
            media_path: Path | None = None
            try:
                if download_callable is not None:
                    media_path = download_callable(
                        project, candidate, gap_id=gap.gap_id
                    )
                else:
                    media_path = download_full_candidate_safe(
                        project, candidate, gap_id=gap.gap_id
                    )
            except Exception as exc:  # noqa: BLE001
                record.funnel_status = transition(
                    record.funnel_status, "download_failed"
                )
                record.download_status = "failed"
                record.reason = str(exc)
                _cleanup_candidate_dir(media_path)
                continue

            outcome = _try_auto_accept_candidate(
                project,
                report=report,
                gap_report=gap_report,
                record=record,
                candidate=candidate,
                media_path=media_path,
                gap=gap,
                locked=locked,
                progress_callback=progress_callback,
                gap_index=gap_index,
                gap_total=total,
            )
            if outcome == "local_media_invalid":
                gap_report.technically_invalid_count += 1
                report.technically_invalid_count += 1
                continue
            # accepted (Lizenzstatus nur informativ)
            accepted_id = candidate.candidate_id
            break

        if gap_report.fallback_used:
            report.fallback_used_count += 1

        gap_report.candidates = records
        if accepted_id:
            license_note = ""
            if gap_report.license_metadata_status == "partial":
                license_note = " · Lizenzdaten teilweise vorhanden"
            elif gap_report.license_metadata_status == "missing":
                license_note = " · Keine Lizenzmetadaten geliefert"
            elif gap_report.license_metadata_status == "complete":
                license_note = " · Lizenzdaten vollständig"
            gap_report.message = (
                f"export_ready: {accepted_id} (automatisch übernommen)"
                f"{license_note}"
            )
            if gap.gap_id not in report.filled_gap_ids:
                report.filled_gap_ids.append(gap.gap_id)
        else:
            gap_report.message = (
                f"Kein export_ready nach {gap_report.full_download_attempts} "
                "Download-Versuch(en)."
            )
            report.open_gap_ids.append(gap.gap_id)
        _upsert_gap_report(report, gap_report)

    report.message = (
        f"Funnel {run_id}: {len(report.requested_gap_ids)} angefordert · "
        f"{len(report.filled_gap_ids)} erfüllt · "
        f"{len(report.open_gap_ids)} offen · "
        f"{report.full_download_count} Voll-Downloads · "
        f"{report.technically_invalid_count} technisch ungültig · "
        f"{report.fallback_used_count} Fallbacks"
        + (" · abgebrochen" if report.stopped else "")
    )
    write_json(supplement_funnel_report_path(project), report)
    _emit(
        progress_callback,
        FunnelProgressEvent(
            phase="finished",
            message=report.message,
            fraction=1.0,
        ),
    )
    return report
