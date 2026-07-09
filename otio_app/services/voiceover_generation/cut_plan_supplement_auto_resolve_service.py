"""Phase 11.3: Auto-Resolver für GENAU EINEN Cut-Plan-Supplement-Request.

Kombiniert die bestehenden Bausteine der isolierten Cut-Plan-Supplement-
Bridge (LLM-Suchqueries, Pexels-Suche über Video+Foto, Download) mit einer
kombinierten Gemini-Beschreibung+-Validierung des heruntergeladenen
Materials zu einem einzigen automatischen Ablauf für EINEN Request:

  1. LLM-Suchqueries erzeugen + Pexels-Kandidaten suchen (bis zu 5, Video+Foto)
  2. Kandidaten der Reihe nach (Suchreihenfolge) herunterladen
  3. EIN kombinierter Gemini-Aufruf pro Kandidat: Frames + Satz/Visual
     Intent/Reason gemeinsam im selben Request — Gemini beschreibt UND
     beurteilt in einem Zug (siehe describe_and_validate_supplement_asset in
     gemini_client.py). Bewusst NICHT zwei getrennte Aufrufe (beschreiben,
     dann validieren) — spart Latenz/Kosten und die Bildinformation geht
     nicht über den Umweg einer separaten Text-Beschreibung verloren.
  4. beim ERSTEN Kandidaten mit Status PASS: automatisch akzeptieren (siehe
     accept_cut_plan_supplement_candidate) und dem Cut-Item zuordnen —
     Schleife bricht ab
  5. kein Kandidat erreicht PASS: Ergebnis NO_MATCH. KEIN automatischer
     Fallback auf ein generisches Ordner-Asset in dieser Phase — das ist
     eine spätere, separate Phase (11.4).

Ohne konfigurierten GEMINI_API_KEY wird NIE automatisch akzeptiert (kein
heuristischer Ersatz-Fallback, der versehentlich PASS liefern könnte) —
safe by default: ohne echte KI-Prüfung wird nichts automatisch übernommen.

Läuft ausschließlich bei explizitem Aufruf (Einzel- oder späterer Batch-
Button im Cut-Plan-Tab) — niemals automatisch beim Draft-Bau oder bei der
Validierung. Schreibt ausschließlich unter
`_otio/voiceover_generation/cut_plan/` (Frames unter
`supplement_assets/{request_id}/frames/{candidate_id}/`) — niemals unter
`_otio/supplement/`, keine Sidecar-/Cache-Dateien der Produktions-Pipeline,
KEINE Erweiterung regulärer Folder-Inventories (bewusste Entscheidung, um
die Isolation dieser Pipeline von "Projekt mit Voice-Over" zu erhalten)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from otio_app.defaults import CUT_PLAN_SUPPLEMENT_CANDIDATES_STATUS_READY, SUPPLEMENT_SOURCE_PEXELS
from otio_app.models import Project
from otio_app.project_layout import get_cut_plan_supplement_asset_request_dir
from otio_app.services.frame_extract import extract_frames
from otio_app.services.gemini_client import (
    GeminiNotConfiguredError,
    describe_and_validate_supplement_asset,
    is_gemini_configured,
)
from otio_app.services.media_utils import is_image_media
from otio_app.services.supplement_coverage import derive_must_show_keywords
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
    "DEFAULT_AUTO_RESOLVE_VALIDATION_MODEL",
    "CutPlanSupplementAutoResolveResult",
    "auto_resolve_cut_plan_supplement_request",
]

AUTO_RESOLVE_STATUS_ACCEPTED = "ACCEPTED"
AUTO_RESOLVE_STATUS_NO_MATCH = "NO_MATCH"
AUTO_RESOLVE_STATUS_FAILED = "FAILED"

# Bewusst nur EXAKT "PASS" (nicht auch WEAK_PASS) wird automatisch
# akzeptiert — nur ein Asset, das die Prüfung WIRKLICH besteht, wird
# automatisch übernommen. WEAK_PASS/NEEDS_USER_REVIEW/FAIL führen zum
# nächsten Kandidaten bzw. am Ende zu NO_MATCH.
VALIDATION_STATUS_PASS = "PASS"

# Nutzerwunsch (Juli 2026): für die automatische Bild-/Video-Prüfung fest
# Gemini 3 Flash Preview verwenden statt des allgemeinen App-Standards
# (get_default_gemini_model(), der z. B. auch 3.1 Flash Lite sein kann).
DEFAULT_AUTO_RESOLVE_VALIDATION_MODEL = "gemini-3-flash-preview"


@dataclass
class CutPlanSupplementAutoResolveResult:
    status: str  # ACCEPTED | NO_MATCH | FAILED
    request_id: str
    accepted_candidate_id: str = ""
    accepted_asset_id: str = ""
    attempts: list[CutPlanSupplementAutoResolveAttempt] = field(default_factory=list)
    error: str = ""


def _describe_and_validate_downloaded_asset(
    project: Project,
    *,
    request: CutPlanSupplementRequest,
    candidate_id: str,
    asset_path: str,
    validation_model: str,
) -> dict:
    """EIN kombinierter Gemini-Aufruf pro Kandidat (siehe Modul-Docstring):
    extrahiert Frames aus dem heruntergeladenen Asset und lässt Gemini sie
    IM SELBEN Request beschreiben und gegen Satz/Visual Intent/Reason
    beurteilen. Liefert IMMER ein dict mit description/status/score/reason
    — nie eine Exception, damit der Auto-Resolver bei jedem Fehlerfall
    (fehlender API-Key, fehlende Datei, Frame-Extraktion, Netzwerk) einfach
    zum nächsten Kandidaten weitergehen kann.

    Ohne GEMINI_API_KEY wird status=FAIL zurückgegeben, OHNE überhaupt
    Frames zu extrahieren — es gibt keinen heuristischen Ersatz-Fallback,
    der versehentlich automatisch PASS liefern könnte (safe by default)."""
    if not is_gemini_configured():
        return {
            "description": "",
            "status": "FAIL",
            "score": 0.0,
            "reason": "GEMINI_API_KEY fehlt — automatische Prüfung nicht möglich.",
        }

    local_path = Path(asset_path)
    if not local_path.is_file() or local_path.stat().st_size <= 0:
        return {
            "description": "",
            "status": "FAIL",
            "score": 0.0,
            "reason": "Heruntergeladene Datei fehlt oder ist leer.",
        }

    frames_dir = (
        get_cut_plan_supplement_asset_request_dir(project.work_dir_path, request.request_id)
        / "frames"
        / candidate_id
    )
    frame_count = 1 if is_image_media(local_path) else max(1, project.frames_per_shot)
    try:
        frames = extract_frames(local_path, frames_dir, frame_count)
    except Exception as exc:  # noqa: BLE001 — Frame-Extraktion darf den Auto-Resolver nicht crashen
        return {
            "description": "",
            "status": "FAIL",
            "score": 0.0,
            "reason": f"Frame-Extraktion fehlgeschlagen: {exc}",
        }
    if not frames:
        return {"description": "", "status": "FAIL", "score": 0.0, "reason": "Keine Frames extrahiert."}

    must_show = derive_must_show_keywords(request.visual_intent or request.reason)
    try:
        return describe_and_validate_supplement_asset(
            media_name=local_path.name,
            folder_name=request.folder_name,
            frame_paths=frames,
            passage_text=request.text,
            visual_requirement=request.visual_intent or request.reason,
            location_name=request.folder_name,
            must_show=must_show,
            language="de",
            model=validation_model,
        )
    except GeminiNotConfiguredError:
        return {
            "description": "",
            "status": "FAIL",
            "score": 0.0,
            "reason": "GEMINI_API_KEY fehlt — automatische Prüfung nicht möglich.",
        }
    except Exception as exc:  # noqa: BLE001 — ein Gemini-Fehler darf den Auto-Resolver nicht crashen
        return {"description": "", "status": "FAIL", "score": 0.0, "reason": f"Gemini-Aufruf fehlgeschlagen: {exc}"}


def auto_resolve_cut_plan_supplement_request(
    project: Project,
    request_id: str,
    *,
    query_llm_provider: str,
    query_llm_model: str,
    validation_model: str = DEFAULT_AUTO_RESOLVE_VALIDATION_MODEL,
) -> CutPlanSupplementAutoResolveResult:
    """Führt den vollständigen Auto-Resolve-Ablauf (siehe Modul-Docstring)
    für GENAU EINEN Request aus. Bricht bei einem Kandidaten mit PASS ab und
    akzeptiert ihn automatisch; sonst NO_MATCH. Wirft KEINE Exception nach
    außen — jeder Fehlerfall (Suche, Download, Beschreibung+Validierung)
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
        update_cut_plan_supplement_request(
            project, request_id, auto_resolve_status=AUTO_RESOLVE_STATUS_FAILED, auto_resolve_attempts=[]
        )
        return CutPlanSupplementAutoResolveResult(
            status=AUTO_RESOLVE_STATUS_FAILED, request_id=request_id, error=str(exc)
        )

    if (
        candidates_document.status != CUT_PLAN_SUPPLEMENT_CANDIDATES_STATUS_READY
        or not candidates_document.candidates
    ):
        update_cut_plan_supplement_request(
            project, request_id, auto_resolve_status=AUTO_RESOLVE_STATUS_NO_MATCH, auto_resolve_attempts=[]
        )
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

        analysis = _describe_and_validate_downloaded_asset(
            project,
            request=request,
            candidate_id=candidate.candidate_id,
            asset_path=downloaded_asset.asset_path,
            validation_model=validation_model,
        )
        attempts.append(
            CutPlanSupplementAutoResolveAttempt(
                candidate_id=candidate.candidate_id,
                provider=candidate.provider,
                asset_type=candidate.asset_type,
                validation_status=str(analysis.get("status", "")),
                validation_score=float(analysis.get("score", 0.0)),
                validation_reason=str(analysis.get("reason", "")),
                description=str(analysis.get("description", "")),
            )
        )

        if str(analysis.get("status")) == VALIDATION_STATUS_PASS:
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
