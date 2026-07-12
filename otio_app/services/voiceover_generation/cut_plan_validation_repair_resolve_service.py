"""Validation Repair (Nutzervorgabe, Juli 2026): Phase 6/7 — automatische
Auflösung EINES Validation-Repair-Requests.

Kombiniert die Bausteine der bestehenden isolierten Cut-Plan-Supplement-
Bridge (Provider-Suche/-Download, Supplement-Manifest-Wiederverwendung,
Gemini-Prüfung) mit der in Phase 4/5 entwickelten Reparatur-Fenster-
Berechnung (cut_plan_validation_repair_apply.py) zu einem einzigen
automatischen Ablauf PRO Repair Request:

  0. Lokale Wiederverwendung: wie beim regulären Auto-Resolver (Phase
     E/J/K) wird zuerst geprüft, ob ein bereits im selben Ordner
     heruntergeladenes Supplement-Manifest-Asset passt — spart externe
     Suche/Lizenzkosten. Läuft durch DIESELBE Download-/Gemini-Prüfungs-
     Pipeline wie jeder andere Kandidat.
  1. Externe Suche über CUT_PLAN_SUPPLEMENT_PROVIDER_SEARCH_ORDER (Adobe
     Stock, dann Pexels), je Provider über die passende Medientyp-
     Reihenfolge:
     - BLACK_GAP: Foto VOR Video (Nutzervorgabe — Fotos liefern für
       kurze Lücken-Reparaturen erfahrungsgemäß bessere/passendere
       Treffer und haben nie ein Zu-kurz-Risiko).
     - ASSET_REUSE_DISTANCE: Video VOR Foto (bestehende redaktionelle
       Präferenz, hier bleibt ein VOLLES Ersatz-Asset für das Segment
       nötig).
  2. EIN kombinierter Gemini-Aufruf pro Kandidat (Beschreibung +
     Beurteilung im selben Request, wie im regulären Auto-Resolver).
  3. Beim ersten Kandidaten mit Status PASS: Reparatur anwenden.
     - BLACK_GAP: compute_black_gap_repair_plan (Phase 4) + apply_
       black_gap_repair (Phase 5) — fügt ein neues, mindestens
       shot_min_sec langes Segment ein und kürzt bei Bedarf die
       angrenzenden Segmente.
     - ASSET_REUSE_DISTANCE: apply_accepted_supplement_to_cut_plan_item
       (bestehende Funktion) — ersetzt das GESAMTE VisualSegment des
       betroffenen Items durch das neue Asset (hier ist "Ersatz für das
       ganze Segment" tatsächlich richtig, anders als bei BLACK_GAP).

Schlägt die Anwendung trotz Gemini-PASS fehl (z. B. reale Videodauer zu
kurz für das Fenster), wird das protokolliert und der NÄCHSTE Kandidat
versucht — bricht NIE den gesamten Lauf ab.

BLACK_GAP-Requests, deren Nachbar-Segmente nicht genug Kürzungs-
Spielraum haben (compute_black_gap_repair_plan gibt None zurück), werden
sofort mit Status UNSAFE_TO_REPAIR beendet — OHNE jede Suche/Download,
da eine Reparatur hier prinzipiell nicht sicher möglich ist (der Nutzer
sollte stattdessen einen normalen Supplement Request für das gesamte
Item erzeugen).

Läuft ausschließlich bei explizitem Aufruf — niemals automatisch beim
Draft-Bau, bei der Asset-Auswahl oder bei der Validierung. Schreibt
ausschließlich unter `_otio/voiceover_generation/cut_plan/` — niemals
unter `_otio/supplement/`."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from otio_app.analysis_models import SupplementRequest
from otio_app.defaults import (
    AUDIO_SCOPE_FOLDER,
    CUT_PLAN_SUPPLEMENT_MAX_CANDIDATES,
    CUT_PLAN_SUPPLEMENT_PROVIDER_SEARCH_ORDER,
    CUT_PLAN_VALIDATION_REPAIR_ASSET_TYPE_ORDER_ASSET_REUSE_DISTANCE,
    CUT_PLAN_VALIDATION_REPAIR_ASSET_TYPE_ORDER_BLACK_GAP,
    CUT_PLAN_VALIDATION_REPAIR_STATUS_ACCEPTED,
    CUT_PLAN_VALIDATION_REPAIR_STATUS_NO_MATCH,
    CUT_PLAN_VALIDATION_REPAIR_STATUS_UNSAFE_TO_REPAIR,
    CUT_PLAN_VALIDATION_REPAIR_TYPE_BLACK_GAP,
)
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
from otio_app.services.supplement_sources import get_supplement_adapter
from otio_app.services.voiceover_generation.cut_plan_asset_selector import settings_from_snapshot
from otio_app.services.voiceover_generation.cut_plan_builder import load_cut_plan_draft, save_cut_plan_draft
from otio_app.services.voiceover_generation.cut_plan_models import CutPlanDocument, CutPlanSettings
from otio_app.services.voiceover_generation.cut_plan_supplement_auto_resolve_service import (
    DEFAULT_AUTO_RESOLVE_VALIDATION_MODEL,
    VALIDATION_STATUS_ACCEPT_FAILED,
    VALIDATION_STATUS_PASS,
    VALIDATION_STATUS_TOO_SHORT,
)
from otio_app.services.voiceover_generation.cut_plan_supplement_bridge import (
    apply_accepted_supplement_to_cut_plan_item,
    download_cut_plan_supplement_candidate,
    load_cut_plan_supplement_manifest,
    record_supplement_manifest_validation,
    stable_supplement_asset_id,
    to_cut_plan_candidate,
)
from otio_app.services.voiceover_generation.cut_plan_supplement_models import (
    CutPlanSupplementAsset,
    CutPlanSupplementAutoResolveAttempt,
    CutPlanSupplementCandidate,
    CutPlanSupplementManifestEntry,
    CutPlanSupplementRequest,
)
from otio_app.services.voiceover_generation.cut_plan_validation_repair import (
    load_cut_plan_validation_repair_requests,
    update_cut_plan_validation_repair_request,
)
from otio_app.services.voiceover_generation.cut_plan_validation_repair_apply import (
    BlackGapRepairPlan,
    apply_black_gap_repair,
    compute_black_gap_repair_plan,
)
from otio_app.services.voiceover_generation.cut_plan_validation_repair_models import (
    CutPlanValidationRepairRequest,
)

__all__ = [
    "CutPlanValidationRepairResult",
    "auto_resolve_validation_repair_request",
    "auto_resolve_all_validation_repair_requests",
]

_EPSILON = 0.05
_MAX_LOCAL_REUSE_CANDIDATES = 5
_MAX_CANDIDATES_PER_STAGE = 2


@dataclass
class CutPlanValidationRepairResult:
    status: str  # ACCEPTED|NO_MATCH|FAILED|UNSAFE_TO_REPAIR
    repair_id: str
    accepted_candidate_id: str = ""
    accepted_asset_id: str = ""
    attempts: list[CutPlanSupplementAutoResolveAttempt] = field(default_factory=list)
    error: str = ""


def _asset_type_order_for_repair_type(repair_type: str) -> tuple[str, ...]:
    if repair_type == CUT_PLAN_VALIDATION_REPAIR_TYPE_BLACK_GAP:
        return CUT_PLAN_VALIDATION_REPAIR_ASSET_TYPE_ORDER_BLACK_GAP
    return CUT_PLAN_VALIDATION_REPAIR_ASSET_TYPE_ORDER_ASSET_REUSE_DISTANCE


def _candidate_is_too_short(
    candidate: CutPlanSupplementCandidate, *, needed_duration_sec: float, cut_plan_settings: CutPlanSettings
) -> tuple[bool, float]:
    if candidate.asset_type != "video" or candidate.duration_sec <= 0:
        return False, 0.0
    usable_duration_sec = max(0.0, candidate.duration_sec - cut_plan_settings.video_head_trim_sec)
    is_too_short = needed_duration_sec > usable_duration_sec + _EPSILON
    return is_too_short, usable_duration_sec


def _find_reusable_manifest_entries_for_repair(
    project: Project,
    request: CutPlanValidationRepairRequest,
    *,
    cut_plan_settings: CutPlanSettings,
    cut_plan_draft: CutPlanDocument,
    needed_duration_sec: float,
    asset_type_order: tuple[str, ...],
) -> list[CutPlanSupplementManifestEntry]:
    """Analog zu find_reusable_local_supplement_candidates (Phase E/J/K,
    cut_plan_supplement_auto_resolve_service.py), aber entkoppelt von
    CutPlanSupplementRequest — nimmt stattdessen needed_duration_sec und
    asset_type_order direkt entgegen, damit BLACK_GAP (Fenster-Dauer,
    Foto-first) und ASSET_REUSE_DISTANCE (Item-Dauer, Video-first)
    dieselbe Suchlogik mit unterschiedlichen Prioritäten nutzen können."""
    if request.source_scope != AUDIO_SCOPE_FOLDER or not request.folder_name:
        return []
    manifest = load_cut_plan_supplement_manifest(project)
    if not manifest.entries:
        return []

    max_asset_usage = cut_plan_settings.max_asset_usage
    min_reuse_distance = cut_plan_settings.min_asset_reuse_distance_shots
    video_head_trim_sec = cut_plan_settings.video_head_trim_sec

    folder_items = [item for item in cut_plan_draft.items if item.folder_name == request.folder_name]
    target_index = next(
        (index for index, item in enumerate(folder_items) if item.cut_item_id == request.cut_item_id), None
    )
    asset_usage_summary = cut_plan_draft.asset_usage_summary

    def _violates_reuse_distance(stable_asset_id: str) -> bool:
        if target_index is None:
            return False
        min_required = max(1, min_reuse_distance)
        for index, item in enumerate(folder_items):
            if item.chosen_asset_id != stable_asset_id:
                continue
            if index == target_index:
                continue
            if abs(index - target_index) <= min_required:
                return True
        return False

    def _validation_tier(entry: CutPlanSupplementManifestEntry) -> int | None:
        own_validations = [v for v in entry.validations if v.request_id == request.repair_id]
        if any(v.accepted or v.validation_status == VALIDATION_STATUS_PASS for v in own_validations):
            return 0
        if any(v.validation_status == "WEAK_PASS" for v in own_validations):
            return 1
        if any(v.validation_status == "FAIL" for v in own_validations):
            return None  # bereits für DIESE Reparatur gescheitert -> ausschließen
        return 2

    ranked: list[tuple[int, int, CutPlanSupplementManifestEntry]] = []
    for entry in manifest.entries:
        if entry.folder_name != request.folder_name:
            continue
        if not Path(entry.asset_path).is_file():
            continue
        if entry.asset_type == "video":
            usable_duration_sec = max(0.0, entry.duration_sec - video_head_trim_sec)
            if needed_duration_sec > usable_duration_sec + _EPSILON:
                continue
        stable_id = stable_supplement_asset_id(entry.provider, entry.provider_asset_id, "", "")
        if asset_usage_summary.get(stable_id, 0) >= max_asset_usage:
            continue
        if _violates_reuse_distance(stable_id):
            continue
        tier = _validation_tier(entry)
        if tier is None:
            continue
        type_rank = asset_type_order.index(entry.asset_type) if entry.asset_type in asset_type_order else len(
            asset_type_order
        )
        ranked.append((tier, type_rank, entry))

    ranked.sort(key=lambda triple: (triple[0], triple[1]))
    return [entry for _tier, _type_rank, entry in ranked[:_MAX_LOCAL_REUSE_CANDIDATES]]


def _candidate_from_manifest_entry(
    entry: CutPlanSupplementManifestEntry, repair_id: str
) -> CutPlanSupplementCandidate:
    snapshot = {
        "candidate_id": f"reuse_{entry.provider}_{entry.provider_asset_id or entry.asset_id}",
        "supplement_request_id": repair_id,
        "provider": entry.provider,
        "provider_asset_id": entry.provider_asset_id,
        "media_type": entry.asset_type,
        "width": entry.width,
        "height": entry.height,
        "duration_sec": entry.duration_sec,
        "download_url": "",
        "download_enabled": True,
        "is_mock": False,
        "requires_user_approval": False,
        "license": entry.license,
        "source_page_url": entry.source_url,
        "folder_name": entry.folder_name,
        "match_score": 1.0,
    }
    return CutPlanSupplementCandidate(
        candidate_id=str(snapshot["candidate_id"]),
        request_id=repair_id,
        provider=entry.provider,
        title=f"Wiederverwendetes Supplement-Asset ({entry.provider})",
        description="Bereits heruntergeladenes Stock-Asset, wiederverwendet ohne erneute Lizenzierung.",
        asset_type=entry.asset_type,
        width=entry.width,
        height=entry.height,
        duration_sec=entry.duration_sec,
        license=entry.license,
        source_url=entry.source_url,
        score=1.0,
        provider_candidate_snapshot=snapshot,
    )


def _describe_and_validate_repair_asset(
    project: Project,
    *,
    request: CutPlanValidationRepairRequest,
    candidate_id: str,
    asset_path: str,
    validation_model: str,
) -> dict:
    """Analog zu _describe_and_validate_downloaded_asset im regulären
    Auto-Resolver — hier dupliziert (statt importiert), da jene Funktion
    request.request_id (CutPlanSupplementRequest-Feld) verwendet, unser
    CutPlanValidationRepairRequest aber repair_id heißt."""
    if not is_gemini_configured():
        return {
            "description": "",
            "status": "FAIL",
            "score": 0.0,
            "reason": "GEMINI_API_KEY fehlt — automatische Prüfung nicht möglich.",
        }

    local_path = Path(asset_path)
    if not local_path.is_file() or local_path.stat().st_size <= 0:
        return {"description": "", "status": "FAIL", "score": 0.0, "reason": "Heruntergeladene Datei fehlt oder ist leer."}

    frames_dir = (
        get_cut_plan_supplement_asset_request_dir(project.language_work_dir_path, request.repair_id) / "frames" / candidate_id
    )
    frame_count = 1 if is_image_media(local_path) else max(1, project.frames_per_shot)
    try:
        frames = extract_frames(local_path, frames_dir, frame_count)
    except Exception as exc:  # noqa: BLE001 — Frame-Extraktion darf den Auto-Resolver nicht crashen
        return {"description": "", "status": "FAIL", "score": 0.0, "reason": f"Frame-Extraktion fehlgeschlagen: {exc}"}
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


def auto_resolve_validation_repair_request(
    project: Project,
    repair_id: str,
    *,
    validation_model: str = DEFAULT_AUTO_RESOLVE_VALIDATION_MODEL,
) -> CutPlanValidationRepairResult:
    """Führt den vollständigen Auto-Resolve-Ablauf (siehe Modul-Docstring)
    für GENAU EINEN Validation-Repair-Request aus. Wirft ValueError, wenn
    keine Requests-Datei bzw. kein Request mit dieser repair_id existiert,
    oder kein Cut Plan Draft vorhanden ist."""
    requests_document = load_cut_plan_validation_repair_requests(project)
    if requests_document is None:
        raise ValueError("Keine Validation Repair Requests vorhanden.")
    request = next((entry for entry in requests_document.requests if entry.repair_id == repair_id), None)
    if request is None:
        raise ValueError(f"Validation Repair Request '{repair_id}' nicht gefunden.")

    cut_plan = load_cut_plan_draft(project)
    if cut_plan is None:
        raise ValueError("Kein Cut Plan Draft vorhanden.")
    settings = settings_from_snapshot(project, cut_plan)

    attempts: list[CutPlanSupplementAutoResolveAttempt] = []

    repair_plan: BlackGapRepairPlan | None = None
    if request.repair_type == CUT_PLAN_VALIDATION_REPAIR_TYPE_BLACK_GAP:
        repair_plan = compute_black_gap_repair_plan(cut_plan, request.gap_start_sec, request.gap_end_sec, settings)
        if repair_plan is None:
            update_cut_plan_validation_repair_request(
                project, repair_id, status=CUT_PLAN_VALIDATION_REPAIR_STATUS_UNSAFE_TO_REPAIR
            )
            return CutPlanValidationRepairResult(status=CUT_PLAN_VALIDATION_REPAIR_STATUS_UNSAFE_TO_REPAIR, repair_id=repair_id)
        needed_duration_sec = repair_plan.window_duration_sec
    else:
        needed_duration_sec = request.needed_duration_sec

    asset_type_order = _asset_type_order_for_repair_type(request.repair_type)

    def _apply_repair(accepted_asset: CutPlanSupplementAsset) -> CutPlanDocument:
        if request.repair_type == CUT_PLAN_VALIDATION_REPAIR_TYPE_BLACK_GAP:
            assert repair_plan is not None
            return apply_black_gap_repair(
                cut_plan, settings, cut_item_id=request.cut_item_id, repair_plan=repair_plan, accepted_asset=accepted_asset
            )
        transient_request = CutPlanSupplementRequest(request_id=repair_id, cut_item_id=request.cut_item_id)
        return apply_accepted_supplement_to_cut_plan_item(project, cut_plan, transient_request, accepted_asset)

    def _try_candidate(candidate: CutPlanSupplementCandidate) -> CutPlanValidationRepairResult | None:
        too_short, usable_duration_sec = _candidate_is_too_short(
            candidate, needed_duration_sec=needed_duration_sec, cut_plan_settings=settings
        )
        if too_short:
            attempts.append(
                CutPlanSupplementAutoResolveAttempt(
                    candidate_id=candidate.candidate_id,
                    provider=candidate.provider,
                    asset_type=candidate.asset_type,
                    validation_status=VALIDATION_STATUS_TOO_SHORT,
                    validation_reason=(
                        f"Kandidat laut Provider-Metadaten zu kurz: benötigt {needed_duration_sec:.2f}s, "
                        f"verfügbar {usable_duration_sec:.2f}s nach video_head_trim_sec."
                    ),
                )
            )
            return None

        try:
            downloaded_asset = download_cut_plan_supplement_candidate(project, repair_id, candidate)
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
            return None

        analysis = _describe_and_validate_repair_asset(
            project,
            request=request,
            candidate_id=candidate.candidate_id,
            asset_path=downloaded_asset.asset_path,
            validation_model=validation_model,
        )
        attempt = CutPlanSupplementAutoResolveAttempt(
            candidate_id=candidate.candidate_id,
            provider=candidate.provider,
            asset_type=candidate.asset_type,
            validation_status=str(analysis.get("status", "")),
            validation_score=float(analysis.get("score", 0.0)),
            validation_reason=str(analysis.get("reason", "")),
            description=str(analysis.get("description", "")),
        )
        attempts.append(attempt)

        provider_asset_id = str(candidate.provider_candidate_snapshot.get("provider_asset_id", ""))
        record_supplement_manifest_validation(
            project,
            provider=candidate.provider,
            provider_asset_id=provider_asset_id,
            request_id=repair_id,
            validation_status=attempt.validation_status,
            validation_score=attempt.validation_score,
            validation_reason=attempt.validation_reason,
            description=attempt.description,
            accepted=False,
        )

        if str(analysis.get("status")) != VALIDATION_STATUS_PASS:
            return None

        try:
            updated_cut_plan = _apply_repair(downloaded_asset)
        except ValueError as exc:
            accept_failed_reason = f"Gemini-Prüfung bestanden, aber Reparatur fehlgeschlagen: {exc}"
            attempts[-1] = attempts[-1].model_copy(
                update={"validation_status": VALIDATION_STATUS_ACCEPT_FAILED, "validation_reason": accept_failed_reason}
            )
            return None

        save_cut_plan_draft(project, updated_cut_plan)
        record_supplement_manifest_validation(
            project,
            provider=candidate.provider,
            provider_asset_id=provider_asset_id,
            request_id=repair_id,
            validation_status=attempt.validation_status,
            validation_score=attempt.validation_score,
            validation_reason=attempt.validation_reason,
            description=attempt.description,
            accepted=True,
        )
        update_cut_plan_validation_repair_request(
            project,
            repair_id,
            status=CUT_PLAN_VALIDATION_REPAIR_STATUS_ACCEPTED,
            accepted_asset_id=downloaded_asset.asset_id,
            accepted_asset_path=downloaded_asset.asset_path,
            auto_resolve_status=CUT_PLAN_VALIDATION_REPAIR_STATUS_ACCEPTED,
            auto_resolve_attempts=attempts,
        )
        return CutPlanValidationRepairResult(
            status=CUT_PLAN_VALIDATION_REPAIR_STATUS_ACCEPTED,
            repair_id=repair_id,
            accepted_candidate_id=candidate.candidate_id,
            accepted_asset_id=downloaded_asset.asset_id,
            attempts=attempts,
        )

    reusable_entries = _find_reusable_manifest_entries_for_repair(
        project,
        request,
        cut_plan_settings=settings,
        cut_plan_draft=cut_plan,
        needed_duration_sec=needed_duration_sec,
        asset_type_order=asset_type_order,
    )
    for entry in reusable_entries:
        result = _try_candidate(_candidate_from_manifest_entry(entry, repair_id))
        if result is not None:
            return result

    for provider in CUT_PLAN_SUPPLEMENT_PROVIDER_SEARCH_ORDER:
        for asset_type in asset_type_order:
            transient_request = SupplementRequest(
                supplement_request_id=repair_id,
                section_id=request.cut_item_id,
                folder_name=request.folder_name,
                location_name=request.folder_name,
                beat_id=request.cut_item_id,
                passage_text=request.text,
                visual_requirement=request.visual_intent or request.reason,
                required_asset_type=asset_type,
                duration_needed_sec=needed_duration_sec,
                reason=request.reason,
                max_candidates=CUT_PLAN_SUPPLEMENT_MAX_CANDIDATES,
            )
            try:
                adapter = get_supplement_adapter(provider)
                raw_candidates = adapter.search(transient_request)
            except Exception:  # noqa: BLE001 — Suche darf den Auto-Resolver nicht crashen
                continue

            candidates = [to_cut_plan_candidate(repair_id, provider, raw) for raw in raw_candidates]
            for candidate in candidates[:_MAX_CANDIDATES_PER_STAGE]:
                result = _try_candidate(candidate)
                if result is not None:
                    return result

    update_cut_plan_validation_repair_request(
        project,
        repair_id,
        status=CUT_PLAN_VALIDATION_REPAIR_STATUS_NO_MATCH,
        auto_resolve_status=CUT_PLAN_VALIDATION_REPAIR_STATUS_NO_MATCH,
        auto_resolve_attempts=attempts,
    )
    return CutPlanValidationRepairResult(status=CUT_PLAN_VALIDATION_REPAIR_STATUS_NO_MATCH, repair_id=repair_id, attempts=attempts)


def auto_resolve_all_validation_repair_requests(
    project: Project, *, validation_model: str = DEFAULT_AUTO_RESOLVE_VALIDATION_MODEL
) -> list[CutPlanValidationRepairResult]:
    """Sequenzielle Batch-Variante — läuft über ALLE Requests, deren
    status noch nicht ACCEPTED ist (ein bereits akzeptierter Request wird
    nicht stillschweigend erneut versucht — ein 'Ersetzen' bliebe eine
    bewusste Einzel-Aktion, analog zum bestehenden Supplement-Flow)."""
    requests_document = load_cut_plan_validation_repair_requests(project)
    if requests_document is None:
        return []
    results: list[CutPlanValidationRepairResult] = []
    for request in requests_document.requests:
        if request.status == CUT_PLAN_VALIDATION_REPAIR_STATUS_ACCEPTED:
            continue
        results.append(auto_resolve_validation_repair_request(project, request.repair_id, validation_model=validation_model))
    return results
