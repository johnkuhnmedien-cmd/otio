"""Residual Gap Auto-Resolve (Nutzervorgabe, Juli 2026): automatische
Suche/Beschaffung für GENAU EINEN bzw. ALLE offenen Residual Gap Requests.

Läuft ausschließlich bei explizitem Aufruf (UI-Button) — niemals
automatisch beim Draft-Bau, der Asset-Auswahl oder der Validierung.

Ablauf pro Request (analog zu cut_plan_supplement_auto_resolve_service.py,
aber eigenständig, da Residual Gap Requests NICHT in
supplement_requests.from_cut_plan.json persistiert sind):

  0. Lokale Wiederverwendung zuerst (find_reusable_local_supplement_
     candidates, dasselbe Manifest wie die normale Supplement-Pipeline —
     spart externe Suche/Lizenzkosten).
  1. Externe Provider-Suche in CUT_PLAN_SUPPLEMENT_PROVIDER_SEARCH_ORDER
     (Adobe Stock, dann Pexels). Asset-Typ-Reihenfolge hängt vom
     repair_mode ab: PATCH_GAP_ONLY (reine Pause/Überhang) bevorzugt
     Foto vor Video (kein Risiko einer zu kurzen Videoquelle für eine
     oft kurze Rest-Lücke); REPLACE_ITEM_VISUAL (Satzmitte) bevorzugt
     Video vor Foto (redaktionell bevorzugt, analog normalem Supplement).
  2. EIN kombinierter Gemini-Aufruf pro Kandidat (describe_and_validate,
     wiederverwendet über _describe_and_validate_downloaded_asset — die
     Funktion ist duck-typed gegen `request.request_id/.folder_name/
     .text/.visual_intent/.reason`, die unser CutPlanResidualGapRequest
     alle trägt).
  3. Erster Kandidat mit Status PASS wird automatisch akzeptiert (siehe
     apply_residual_gap_asset) — Schleife bricht sofort ab.
  4. Kein Kandidat besteht -> NO_MATCH. KEIN generischer Ordner-Fallback
     in dieser Phase (das Item hat bereits ein Asset — ein neutraler
     Fallback für nur die Rest-Lücke wäre redaktionell fragwürdig)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from otio_app.analysis_models import SupplementCandidate, SupplementRequest
from otio_app.defaults import (
    CUT_PLAN_DEFAULT_VIDEO_HEAD_TRIM_SEC,
    CUT_PLAN_RESIDUAL_GAP_REPAIR_MODE_PATCH_GAP_ONLY,
    CUT_PLAN_RESIDUAL_GAP_STATUS_ACCEPTED,
    CUT_PLAN_RESIDUAL_GAP_STATUS_FAILED,
    CUT_PLAN_RESIDUAL_GAP_STATUS_NO_MATCH,
    CUT_PLAN_SUPPLEMENT_PROVIDER_SEARCH_ORDER,
)
from otio_app.models import Project
from otio_app.project_layout import get_cut_plan_supplement_asset_request_dir
from otio_app.services.media_utils import probe_duration_seconds
from otio_app.services.supplement_sources import get_supplement_adapter
from otio_app.services.voiceover_generation.cut_plan_asset_selector import settings_from_snapshot
from otio_app.services.voiceover_generation.cut_plan_builder import load_cut_plan_draft, save_cut_plan_draft
from otio_app.services.voiceover_generation.cut_plan_residual_gap_apply import apply_residual_gap_asset
from otio_app.services.voiceover_generation.cut_plan_residual_gap_models import CutPlanResidualGapRequest
from otio_app.services.voiceover_generation.cut_plan_residual_gap_requests import (
    cache_signature_for_residual_gap_request,
    load_residual_gap_requests,
    update_residual_gap_request,
)
from otio_app.services.voiceover_generation.cut_plan_supplement_auto_resolve_service import (
    DEFAULT_AUTO_RESOLVE_VALIDATION_MODEL,
    VALIDATION_STATUS_PASS,
    _describe_and_validate_downloaded_asset,
    find_reusable_local_supplement_candidates,
)
from otio_app.services.voiceover_generation.cut_plan_supplement_bridge import (
    find_reusable_supplement_manifest_entry,
    record_supplement_manifest_entry,
    record_supplement_manifest_validation,
    stable_supplement_asset_id,
)
from otio_app.services.voiceover_generation.cut_plan_supplement_models import (
    CutPlanSupplementAsset,
    CutPlanSupplementManifestEntry,
)

__all__ = [
    "AUTO_RESOLVE_STATUS_ACCEPTED",
    "AUTO_RESOLVE_STATUS_NO_MATCH",
    "AUTO_RESOLVE_STATUS_FAILED",
    "CutPlanResidualGapAutoResolveResult",
    "auto_resolve_residual_gap_request",
    "auto_resolve_all_residual_gap_requests",
]

AUTO_RESOLVE_STATUS_ACCEPTED = "ACCEPTED"
AUTO_RESOLVE_STATUS_NO_MATCH = "NO_MATCH"
AUTO_RESOLVE_STATUS_FAILED = "FAILED"

_MAX_CANDIDATES_PER_STAGE = 2
_DURATION_EPSILON = 0.05


@dataclass(frozen=True)
class CutPlanResidualGapAutoResolveResult:
    status: str
    request_id: str
    accepted_asset_id: str = ""
    accepted_asset_path: str = ""
    message: str = ""


def _asset_type_search_order(repair_mode: str) -> tuple[str, str]:
    if repair_mode == CUT_PLAN_RESIDUAL_GAP_REPAIR_MODE_PATCH_GAP_ONLY:
        return ("image", "video")
    return ("video", "image")


def _transient_supplement_request(request: CutPlanResidualGapRequest, *, required_asset_type: str) -> SupplementRequest:
    return SupplementRequest(
        supplement_request_id=request.request_id,
        section_id=request.cut_item_id,
        folder_name=request.folder_name,
        location_name=request.folder_name,
        beat_id=request.cut_item_id,
        passage_text=request.text,
        visual_requirement=request.visual_intent or request.reason,
        required_asset_type=required_asset_type,
        duration_needed_sec=request.needed_duration_sec,
        reason=request.reason,
        max_candidates=_MAX_CANDIDATES_PER_STAGE,
    )


def _candidate_too_short(candidate: SupplementCandidate, needed_duration_sec: float, video_head_trim_sec: float) -> bool:
    if candidate.media_type != "video" or not candidate.duration_sec:
        return False
    usable = candidate.duration_sec - video_head_trim_sec
    return usable < needed_duration_sec - _DURATION_EPSILON


def _copy_reused_asset(entry: CutPlanSupplementManifestEntry, destination_folder: Path, candidate_id: str) -> Path:
    source_path = Path(entry.asset_path)
    destination_folder.mkdir(parents=True, exist_ok=True)
    suffix = source_path.suffix or (".jpg" if entry.asset_type == "image" else ".mp4")
    safe_id = "".join(char if char.isalnum() or char in "-_" else "_" for char in candidate_id)
    target_path = destination_folder / f"reused_{safe_id}{suffix}"
    import shutil

    shutil.copy2(source_path, target_path)
    return target_path


def _accept(
    project: Project,
    request: CutPlanResidualGapRequest,
    *,
    asset_path: str,
    asset_id: str,
    asset_type: str,
    duration_sec: float,
    candidate_id: str,
) -> CutPlanResidualGapAutoResolveResult:
    draft = load_cut_plan_draft(project)
    if draft is None:
        return CutPlanResidualGapAutoResolveResult(
            status=AUTO_RESOLVE_STATUS_FAILED, request_id=request.request_id, message="Kein Cut Plan Draft vorhanden."
        )
    accepted_asset = CutPlanSupplementAsset(
        asset_id=asset_id, request_id=request.request_id, candidate_id=candidate_id, provider="",
        asset_path=asset_path, asset_type=asset_type, duration_sec=duration_sec,
    )
    try:
        updated_cut_plan = apply_residual_gap_asset(project, draft, request, accepted_asset)
    except ValueError as exc:
        update_residual_gap_request(project, request.request_id, status=CUT_PLAN_RESIDUAL_GAP_STATUS_FAILED)
        return CutPlanResidualGapAutoResolveResult(
            status=AUTO_RESOLVE_STATUS_FAILED, request_id=request.request_id, message=str(exc)
        )
    save_cut_plan_draft(project, updated_cut_plan)
    signature = cache_signature_for_residual_gap_request(
        request.cut_item_id, request.gap_start_sec, request.gap_end_sec, request.repair_mode
    )
    update_residual_gap_request(
        project,
        request.request_id,
        status=CUT_PLAN_RESIDUAL_GAP_STATUS_ACCEPTED,
        accepted_candidate_id=candidate_id,
        accepted_asset_id=asset_id,
        accepted_asset_path=asset_path,
        accepted_for_cache_signature=signature,
    )
    return CutPlanResidualGapAutoResolveResult(
        status=AUTO_RESOLVE_STATUS_ACCEPTED, request_id=request.request_id,
        accepted_asset_id=asset_id, accepted_asset_path=asset_path,
    )


def auto_resolve_residual_gap_request(
    project: Project,
    request_id: str,
    *,
    validation_model: str = DEFAULT_AUTO_RESOLVE_VALIDATION_MODEL,
) -> CutPlanResidualGapAutoResolveResult:
    """Führt den vollständigen Auto-Resolve-Ablauf (siehe Modul-Docstring)
    für GENAU EINEN Request aus. Wirft KEINE Exception nach außen — jeder
    Fehlerfall wird im Ergebnis abgebildet."""
    requests_document = load_residual_gap_requests(project)
    if requests_document is None:
        return CutPlanResidualGapAutoResolveResult(
            status=AUTO_RESOLVE_STATUS_FAILED, request_id=request_id, message="Keine Residual Gap Requests vorhanden."
        )
    request = next((entry for entry in requests_document.requests if entry.request_id == request_id), None)
    if request is None:
        return CutPlanResidualGapAutoResolveResult(
            status=AUTO_RESOLVE_STATUS_FAILED, request_id=request_id, message="Request nicht gefunden."
        )
    if request.accepted_asset_id:
        return CutPlanResidualGapAutoResolveResult(
            status=AUTO_RESOLVE_STATUS_ACCEPTED, request_id=request_id,
            accepted_asset_id=request.accepted_asset_id, accepted_asset_path=request.accepted_asset_path,
        )

    draft = load_cut_plan_draft(project)
    settings = settings_from_snapshot(project, draft) if draft is not None else None
    video_head_trim_sec = settings.video_head_trim_sec if settings else CUT_PLAN_DEFAULT_VIDEO_HEAD_TRIM_SEC

    reusable_entries = find_reusable_local_supplement_candidates(
        project, request, cut_plan_settings=settings, cut_plan_draft=draft
    )
    for entry in reusable_entries:
        candidate_id = f"reuse_{entry.asset_id}"
        analysis = _describe_and_validate_downloaded_asset(
            project, request=request, candidate_id=candidate_id, asset_path=entry.asset_path,
            validation_model=validation_model,
        )
        record_supplement_manifest_validation(
            project, provider=entry.provider, provider_asset_id=entry.provider_asset_id, request_id=request_id,
            validation_status=str(analysis.get("status", "")), validation_score=float(analysis.get("score", 0.0)),
            validation_reason=str(analysis.get("reason", "")), description=str(analysis.get("description", "")),
            accepted=False,
        )
        if str(analysis.get("status")) == VALIDATION_STATUS_PASS:
            return _accept(
                project, request, asset_path=entry.asset_path, asset_id=entry.asset_id,
                asset_type=entry.asset_type, duration_sec=entry.duration_sec, candidate_id=candidate_id,
            )

    asset_type_order = _asset_type_search_order(request.repair_mode)
    destination_folder = get_cut_plan_supplement_asset_request_dir(project.language_work_dir_path, request_id)

    for provider in CUT_PLAN_SUPPLEMENT_PROVIDER_SEARCH_ORDER:
        for asset_type in asset_type_order:
            transient_request = _transient_supplement_request(request, required_asset_type=asset_type)
            try:
                adapter = get_supplement_adapter(provider)
                raw_candidates = adapter.search(transient_request)
            except Exception:  # noqa: BLE001 — Provider-/Netzwerkfehler dürfen den Ablauf nicht abbrechen
                continue

            for raw in raw_candidates[:_MAX_CANDIDATES_PER_STAGE]:
                if _candidate_too_short(raw, request.needed_duration_sec, video_head_trim_sec):
                    continue
                try:
                    reused_entry = find_reusable_supplement_manifest_entry(project, provider, raw.provider_asset_id)
                    if reused_entry is not None and Path(reused_entry.asset_path).is_file():
                        local_path = _copy_reused_asset(reused_entry, destination_folder, raw.candidate_id)
                    else:
                        acquired = adapter.acquire(raw, destination_folder)
                        local_path = Path(acquired.local_path)
                        if raw.provider_asset_id:
                            record_supplement_manifest_entry(
                                project,
                                CutPlanSupplementManifestEntry(
                                    asset_id=stable_supplement_asset_id(
                                        provider, raw.provider_asset_id, request_id, raw.candidate_id
                                    ),
                                    provider=provider, provider_asset_id=raw.provider_asset_id,
                                    asset_path=str(local_path), asset_type=raw.media_type,
                                    duration_sec=raw.duration_sec, width=raw.width, height=raw.height,
                                    license=raw.license, source_url=raw.source_page_url,
                                    folder_name=request.folder_name, first_request_id=request_id,
                                    first_candidate_id=raw.candidate_id,
                                ),
                            )
                except Exception:  # noqa: BLE001 — Download-/Lizenzfehler: nächster Kandidat
                    continue

                asset_id = stable_supplement_asset_id(provider, raw.provider_asset_id, request_id, raw.candidate_id)
                analysis = _describe_and_validate_downloaded_asset(
                    project, request=request, candidate_id=raw.candidate_id, asset_path=str(local_path),
                    validation_model=validation_model,
                )
                record_supplement_manifest_validation(
                    project, provider=provider, provider_asset_id=raw.provider_asset_id, request_id=request_id,
                    validation_status=str(analysis.get("status", "")), validation_score=float(analysis.get("score", 0.0)),
                    validation_reason=str(analysis.get("reason", "")), description=str(analysis.get("description", "")),
                    accepted=False,
                )
                if str(analysis.get("status")) == VALIDATION_STATUS_PASS:
                    real_duration = raw.duration_sec
                    if raw.media_type == "video":
                        real_duration = probe_duration_seconds(local_path) or raw.duration_sec
                    return _accept(
                        project, request, asset_path=str(local_path), asset_id=asset_id, asset_type=raw.media_type,
                        duration_sec=real_duration, candidate_id=raw.candidate_id,
                    )

    update_residual_gap_request(project, request_id, status=CUT_PLAN_RESIDUAL_GAP_STATUS_NO_MATCH)
    return CutPlanResidualGapAutoResolveResult(
        status=AUTO_RESOLVE_STATUS_NO_MATCH, request_id=request_id,
        message="Kein Kandidat hat die automatische Prüfung bestanden.",
    )


def auto_resolve_all_residual_gap_requests(
    project: Project,
    *,
    validation_model: str = DEFAULT_AUTO_RESOLVE_VALIDATION_MODEL,
) -> list[CutPlanResidualGapAutoResolveResult]:
    """Läuft sequenziell über alle NOCH NICHT versorgten Residual Gap
    Requests (accepted_asset_id leer) — analog zu auto_resolve_all_cut_
    plan_supplement_requests. Wirft KEINE Exception nach außen für einen
    einzelnen Request."""
    requests_document = load_residual_gap_requests(project)
    if requests_document is None:
        return []
    open_requests = [entry for entry in requests_document.requests if not entry.accepted_asset_id]
    return [
        auto_resolve_residual_gap_request(project, request.request_id, validation_model=validation_model)
        for request in open_requests
    ]
