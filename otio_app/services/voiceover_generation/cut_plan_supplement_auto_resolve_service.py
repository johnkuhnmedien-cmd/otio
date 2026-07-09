"""Phase 11.3: Auto-Resolver für GENAU EINEN Cut-Plan-Supplement-Request.

Kombiniert die bestehenden Bausteine der isolierten Cut-Plan-Supplement-
Bridge (LLM-Suchqueries, Pexels-Suche über Video+Foto, Download) mit einer
Gemini-Beschreibung/-Validierung des heruntergeladenen Materials zu einem
einzigen automatischen Ablauf für EINEN Request:

  1. LLM-Suchqueries erzeugen + Pexels-Kandidaten suchen (bis zu 5, Video+Foto)
  2. Kandidaten der Reihe nach (Suchreihenfolge) herunterladen
  3. heruntergeladenes Asset per Gemini aus Frames beschreiben
  4. Beschreibung gegen Satz/Visual Intent/Reason validieren (Gemini, sonst
     heuristischer Fallback ohne API-Key — identisch zur Fallback-Logik der
     bestehenden Produktions-Pipeline in supplement_pipeline.py)
  5. beim ERSTEN Kandidaten mit Validierungsstatus PASS: automatisch
     akzeptieren (siehe accept_cut_plan_supplement_candidate) und dem
     Cut-Item zuordnen — Schleife bricht ab
  6. kein Kandidat erreicht PASS: Ergebnis NO_MATCH. KEIN automatischer
     Fallback auf ein generisches Ordner-Asset in dieser Phase — das ist
     eine spätere, separate Phase (11.4).

Läuft ausschließlich bei explizitem Aufruf (Einzel- oder späterer Batch-
Button im Cut-Plan-Tab) — niemals automatisch beim Draft-Bau oder bei der
Validierung. Schreibt ausschließlich unter
`_otio/voiceover_generation/cut_plan/` (Frames unter
`supplement_assets/{request_id}/frames/{candidate_id}/`) — niemals unter
`_otio/supplement/`, keine Sidecar-/Cache-Dateien der Produktions-Pipeline,
keine Änderung an regulären Folder-Inventories.

Nutzt für Beschreibung/Validierung dieselben Gemini-Funktionen wie die
Produktions-Pipeline (`describe_media_from_frames`/
`validate_supplement_asset_match` aus `gemini_client.py`) — das ist die
einzige Stelle im Codebase, die Bild-/Video-Inhalte tatsächlich gegenüber
einem LLM beurteilen kann (`plan_llm_client.py`, das sonst von dieser
Pipeline genutzt wird, unterstützt keine Bild-/Video-Eingaben)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from otio_app.defaults import CUT_PLAN_SUPPLEMENT_CANDIDATES_STATUS_READY, SUPPLEMENT_SOURCE_PEXELS
from otio_app.models import Project
from otio_app.project_layout import get_cut_plan_supplement_asset_request_dir
from otio_app.services.frame_extract import extract_frames
from otio_app.services.gemini_client import (
    GeminiNotConfiguredError,
    describe_media_from_frames,
    get_default_gemini_model,
    is_gemini_configured,
    validate_supplement_asset_match,
)
from otio_app.services.media_utils import is_image_media
from otio_app.services.supplement_coverage import derive_must_show_keywords, score_asset_match
from otio_app.services.voiceover_generation.cut_plan_supplement_bridge import (
    accept_cut_plan_supplement_candidate,
    download_cut_plan_supplement_candidate,
    load_cut_plan_supplement_candidates_for_request,
    load_cut_plan_supplement_requests,
    search_candidates_for_cut_plan_request,
    update_cut_plan_supplement_request,
)
from otio_app.services.voiceover_generation.cut_plan_supplement_models import (
    CutPlanSupplementAsset,
    CutPlanSupplementAutoResolveAttempt,
    CutPlanSupplementRequest,
)

__all__ = [
    "AUTO_RESOLVE_STATUS_ACCEPTED",
    "AUTO_RESOLVE_STATUS_NO_MATCH",
    "AUTO_RESOLVE_STATUS_FAILED",
    "VALIDATION_STATUS_PASS",
    "CutPlanSupplementAutoResolveResult",
    "auto_resolve_cut_plan_supplement_request",
]

AUTO_RESOLVE_STATUS_ACCEPTED = "ACCEPTED"
AUTO_RESOLVE_STATUS_NO_MATCH = "NO_MATCH"
AUTO_RESOLVE_STATUS_FAILED = "FAILED"

# Bewusst nur EXAKT "PASS" (nicht auch WEAK_PASS) wird automatisch
# akzeptiert — der Nutzer wollte, dass nur ein Asset, das die Prüfung
# WIRKLICH besteht, automatisch übernommen wird. WEAK_PASS/NEEDS_USER_REVIEW/
# FAIL führen zum nächsten Kandidaten bzw. am Ende zu NO_MATCH.
VALIDATION_STATUS_PASS = "PASS"


@dataclass
class CutPlanSupplementAutoResolveResult:
    status: str  # ACCEPTED | NO_MATCH | FAILED
    request_id: str
    accepted_candidate_id: str = ""
    accepted_asset_id: str = ""
    attempts: list[CutPlanSupplementAutoResolveAttempt] = field(default_factory=list)
    error: str = ""


def _describe_downloaded_asset(
    project: Project,
    *,
    request_id: str,
    candidate_id: str,
    folder_name: str,
    asset_path: str,
    gemini_model: str,
) -> str:
    """Extrahiert Frames aus dem heruntergeladenen Asset und lässt Gemini sie
    beschreiben — analog zu analyze_supplement_asset() in
    supplement_pipeline.py, aber mit eigenem, isoliertem Frame-Verzeichnis
    unter der Cut-Plan-Supplement-Struktur statt unter _otio/frames/."""
    local_path = Path(asset_path)
    if not local_path.is_file() or local_path.stat().st_size <= 0:
        return ""
    frames_dir = (
        get_cut_plan_supplement_asset_request_dir(project.work_dir_path, request_id)
        / "frames"
        / candidate_id
    )
    frame_count = 1 if is_image_media(local_path) else max(1, project.frames_per_shot)
    try:
        frames = extract_frames(local_path, frames_dir, frame_count)
    except Exception:  # noqa: BLE001 — Frame-Extraktion darf den Auto-Resolver nicht crashen
        return ""
    if not frames or not is_gemini_configured():
        return ""
    try:
        return describe_media_from_frames(local_path.name, folder_name, frames, "de", model=gemini_model)
    except GeminiNotConfiguredError:
        return ""


def _validate_description(
    *, description: str, request: CutPlanSupplementRequest, gemini_model: str
) -> dict:
    """Prüft die Beschreibung gegen Satz/Visual Intent/Reason — bei
    konfiguriertem Gemini per LLM (wie die Produktions-Pipeline), sonst per
    heuristischer Keyword-Überlappung (score_asset_match), damit der
    Auto-Resolver auch ohne GEMINI_API_KEY nutzbar bleibt (dann konservativer:
    maximal WEAK_PASS, nie automatisch PASS)."""
    if not description.strip():
        return {"status": "FAIL", "score": 0.0, "reason": "Keine Beschreibung verfügbar."}

    must_show = derive_must_show_keywords(request.visual_intent or request.reason)
    if is_gemini_configured():
        try:
            return validate_supplement_asset_match(
                passage_text=request.text,
                visual_requirement=request.visual_intent or request.reason,
                description=description,
                location_name=request.folder_name,
                must_show=must_show,
                language="de",
                model=gemini_model,
            )
        except GeminiNotConfiguredError:
            pass

    score = score_asset_match(
        passage_text=request.text,
        visual_requirement=request.visual_intent or request.reason,
        description=description,
        must_show=must_show,
    )
    if score >= 0.7:
        status = "WEAK_PASS"
    elif score >= 0.35:
        status = "NEEDS_USER_REVIEW"
    else:
        status = "FAIL"
    return {
        "status": status,
        "score": score,
        "reason": "Heuristische Prüfung ohne Gemini (Keyword-Überlappung).",
    }


def auto_resolve_cut_plan_supplement_request(
    project: Project,
    request_id: str,
    *,
    query_llm_provider: str,
    query_llm_model: str,
) -> CutPlanSupplementAutoResolveResult:
    """Führt den vollständigen Auto-Resolve-Ablauf (siehe Modul-Docstring)
    für GENAU EINEN Request aus. Bricht bei einem Kandidaten mit PASS ab und
    akzeptiert ihn automatisch; sonst NO_MATCH. Wirft KEINE Exception nach
    außen — jeder Fehlerfall (Suche, Download, Beschreibung, Validierung)
    wird im Ergebnis abgebildet, damit ein späterer Batch-Lauf über viele
    Requests niemals an einem einzigen Request abbricht."""
    try:
        candidates_document = search_candidates_for_cut_plan_request(
            project,
            request_id,
            {"provider": SUPPLEMENT_SOURCE_PEXELS},
            query_llm_provider=query_llm_provider,
            query_llm_model=query_llm_model,
        )
    except Exception as exc:  # noqa: BLE001 — Suche darf den Auto-Resolver nicht crashen
        update_cut_plan_supplement_request(project, request_id, auto_resolve_status=AUTO_RESOLVE_STATUS_FAILED, auto_resolve_attempts=[])
        return CutPlanSupplementAutoResolveResult(
            status=AUTO_RESOLVE_STATUS_FAILED, request_id=request_id, error=str(exc)
        )

    if (
        candidates_document.status != CUT_PLAN_SUPPLEMENT_CANDIDATES_STATUS_READY
        or not candidates_document.candidates
    ):
        update_cut_plan_supplement_request(project, request_id, auto_resolve_status=AUTO_RESOLVE_STATUS_NO_MATCH, auto_resolve_attempts=[])
        return CutPlanSupplementAutoResolveResult(
            status=AUTO_RESOLVE_STATUS_NO_MATCH,
            request_id=request_id,
            error=candidates_document.error_message,
        )

    requests_document = load_cut_plan_supplement_requests(project)
    request = (
        next((entry for entry in requests_document.requests if entry.request_id == request_id), None)
        if requests_document is not None
        else None
    )
    if request is None:
        raise ValueError(f"Supplement Request '{request_id}' nicht gefunden.")

    gemini_model = get_default_gemini_model()
    attempts: list[CutPlanSupplementAutoResolveAttempt] = []

    for candidate in candidates_document.candidates:
        try:
            downloaded_asset: CutPlanSupplementAsset = download_cut_plan_supplement_candidate(
                project, request_id, candidate
            )
        except Exception as exc:  # noqa: BLE001 — ein fehlgeschlagener Download darf nicht abbrechen
            attempts.append(
                CutPlanSupplementAutoResolveAttempt(
                    candidate_id=candidate.candidate_id,
                    provider=candidate.provider,
                    asset_type=candidate.asset_type,
                    validation_status="DOWNLOAD_FAILED",
                    validation_reason=str(exc),
                )
            )
            continue

        description = _describe_downloaded_asset(
            project,
            request_id=request_id,
            candidate_id=candidate.candidate_id,
            folder_name=request.folder_name,
            asset_path=downloaded_asset.asset_path,
            gemini_model=gemini_model,
        )
        validation = _validate_description(description=description, request=request, gemini_model=gemini_model)
        attempts.append(
            CutPlanSupplementAutoResolveAttempt(
                candidate_id=candidate.candidate_id,
                provider=candidate.provider,
                asset_type=candidate.asset_type,
                validation_status=str(validation.get("status", "")),
                validation_score=float(validation.get("score", 0.0)),
                validation_reason=str(validation.get("reason", "")),
                description=description,
            )
        )

        if str(validation.get("status")) == VALIDATION_STATUS_PASS:
            accept_cut_plan_supplement_candidate(
                project, request_id, candidate.candidate_id, downloaded_asset=downloaded_asset
            )
            update_cut_plan_supplement_request(
                project,
                request_id,
                auto_resolve_status=AUTO_RESOLVE_STATUS_ACCEPTED,
                auto_resolve_attempts=attempts,
            )
            return CutPlanSupplementAutoResolveResult(
                status=AUTO_RESOLVE_STATUS_ACCEPTED,
                request_id=request_id,
                accepted_candidate_id=candidate.candidate_id,
                accepted_asset_id=downloaded_asset.asset_id,
                attempts=attempts,
            )

    update_cut_plan_supplement_request(
        project, request_id, auto_resolve_status=AUTO_RESOLVE_STATUS_NO_MATCH, auto_resolve_attempts=attempts
    )
    return CutPlanSupplementAutoResolveResult(
        status=AUTO_RESOLVE_STATUS_NO_MATCH, request_id=request_id, attempts=attempts
    )
