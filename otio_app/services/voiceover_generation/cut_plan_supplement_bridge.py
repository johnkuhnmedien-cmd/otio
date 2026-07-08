"""Phase 8.6: Isolierte Supplement Bridge für den Cut Plan.

Erzeugt isolierte Supplement Requests aus SUPPLEMENT_REQUIRED-Items eines
Cut-Plan-Entwurfs, ermöglicht eine (ausschließlich per explizitem Nutzerklick
ausgelöste) Kandidatensuche und das Übernehmen eines akzeptierten Kandidaten
als Cut-Plan-Supplement-Asset. Alle Artefakte liegen ausschließlich unter
`_otio/voiceover_generation/cut_plan/` — NIEMALS unter `_otio/supplement/`,
`_otio/edit_plan/` oder `_otio/exports/`.

Wiederverwendet ausschließlich die TECHNISCHEN Adapter der bestehenden
Supplement-Provider (`SupplementSourceAdapter.search`/`.acquire`) sowie deren
reine Datenmodelle (`SupplementRequest`, `SupplementCandidate` aus
`otio_app.analysis_models`) als Ein-/Ausgabeformat für die Adapter-Schnittstelle.
Ruft NIEMALS eine der höherstufigen Orchestrierungs-Funktionen der
bestehenden Produktions-Supplement-Pipeline auf (weder die Mehrfach-Suche
noch die Mehrfach-Beschaffung, weder die automatische Inventory-Erweiterung
noch das automatische Neu-Planen eines Ordners) und schreibt NIEMALS in
`_otio/supplement/supplement_requests.json` oder reguläre Folder-Inventories.

Kein LLM-Aufruf. Suchqueries werden deterministisch aus vorhandenen Feldern
gebaut. Externe Provider-Suche/-Downloads laufen ausschließlich bei
explizitem Aufruf von `search_candidates_for_cut_plan_request` bzw.
`accept_cut_plan_supplement_candidate` — niemals automatisch beim Draft-Bau,
bei der Asset-Auswahl oder bei der Validierung."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from otio_app.analysis_models import SupplementCandidate, SupplementRequest
from otio_app.defaults import (
    CUT_PLAN_ASSET_SELECTION_SUPPLEMENT_USED,
    CUT_PLAN_ERROR_SUPPLEMENT_REQUIRED,
    CUT_PLAN_STATUS_DRAFT,
    CUT_PLAN_STATUS_NEEDS_REVIEW,
    CUT_PLAN_SUPPLEMENT_ASSET_STATUS_ACQUIRED,
    CUT_PLAN_SUPPLEMENT_CANDIDATES_STATUS_FAILED,
    CUT_PLAN_SUPPLEMENT_CANDIDATES_STATUS_NO_RESULTS,
    CUT_PLAN_SUPPLEMENT_CANDIDATES_STATUS_READY,
    CUT_PLAN_SUPPLEMENT_REQUEST_STATUS_ACCEPTED,
    CUT_PLAN_SUPPLEMENT_REQUEST_STATUS_CANDIDATES_FOUND,
    CUT_PLAN_SUPPLEMENT_REQUEST_STATUS_FAILED,
    CUT_PLAN_ASSET_SELECTION_SUPPLEMENT_REQUIRED as _STATUS_SUPPLEMENT_REQUIRED,
    SUPPLEMENT_SOURCE_PEXELS,
)
from otio_app.models import Project
from otio_app.project_layout import (
    get_cut_plan_supplement_asset_request_dir,
    get_cut_plan_supplement_candidates_path,
    get_cut_plan_supplement_requests_path,
)
from otio_app.services.api_keys import get_api_key
from otio_app.services.media_utils import probe_duration_seconds
from otio_app.services.supplement_sources import get_supplement_adapter
from otio_app.services.voiceover_generation.cut_plan_asset_selector import (
    aggregate_item_level_errors,
    settings_from_snapshot,
    update_asset_usage_summary,
)
from otio_app.services.voiceover_generation.cut_plan_builder import load_cut_plan_draft, save_cut_plan_draft
from otio_app.services.voiceover_generation.cut_plan_models import CutPlanDocument, VisualSegment
from otio_app.services.voiceover_generation.cut_plan_supplement_models import (
    CutPlanSupplementAsset,
    CutPlanSupplementCandidate,
    CutPlanSupplementCandidatesDocument,
    CutPlanSupplementRequest,
    CutPlanSupplementRequestsDocument,
)
from otio_app.services.voiceover_generation.cut_plan_visual_coverage import apply_visual_coverage_extensions
from otio_app.services.voiceover_generation.llm_trace_service import content_hash_of_model

__all__ = [
    "build_supplement_requests_from_cut_plan",
    "save_cut_plan_supplement_requests",
    "load_cut_plan_supplement_requests",
    "load_cut_plan_supplement_candidates_for_request",
    "search_candidates_for_cut_plan_request",
    "accept_cut_plan_supplement_candidate",
    "apply_accepted_supplement_to_cut_plan_item",
]

_DURATION_EPSILON = 0.05


def _safe_path_component(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in "-_" else "_" for char in value)
    return cleaned or "item"


def _sanitize_error_message(message: str, *, env_keys: tuple[str, ...] = ("PEXELS_API_KEY",)) -> str:
    """Entfernt rohe API-Key-Werte aus Fehlermeldungen, bevor sie persistiert
    oder in der UI angezeigt werden — defensiv, da die bestehenden Adapter
    Keys normalerweise nicht in Fehlermeldungen spiegeln, aber Provider-
    Antworten sind nicht vollständig kontrollierbar."""
    sanitized = message
    for env_key in env_keys:
        value = get_api_key(env_key)
        if value and value in sanitized:
            sanitized = sanitized.replace(value, "[REDACTED]")
    return sanitized


# --- Requests (§4) ---


def build_supplement_requests_from_cut_plan(
    project: Project, cut_plan: CutPlanDocument
) -> CutPlanSupplementRequestsDocument:
    """Reine Funktion — erzeugt EINEN CutPlanSupplementRequest je CutPlanItem
    mit asset_selection_status=SUPPLEMENT_REQUIRED, needs_supplement_asset=true
    oder dem Blocker SUPPLEMENT_REQUIRED. Dedupliziert nach cut_item_id.
    Speichert nichts (siehe save_cut_plan_supplement_requests)."""
    requests: list[CutPlanSupplementRequest] = []
    seen_cut_item_ids: set[str] = set()

    for item in cut_plan.items:
        needs_supplement = (
            item.asset_selection_status == _STATUS_SUPPLEMENT_REQUIRED
            or item.needs_supplement_asset
            or CUT_PLAN_ERROR_SUPPLEMENT_REQUIRED in item.blockers
        )
        if not needs_supplement or item.cut_item_id in seen_cut_item_ids:
            continue
        seen_cut_item_ids.add(item.cut_item_id)

        source_sentence_id = ""
        source_hook_beat_id = ""
        for source_ref in item.source_refs:
            source_sentence_id = source_sentence_id or source_ref.source_sentence_id
            source_hook_beat_id = source_hook_beat_id or source_ref.source_hook_beat_id

        reason = (
            item.supplement_reason.strip()
            or item.asset_selection_reason.strip()
            or "No usable existing asset found for this cut item."
        )

        requests.append(
            CutPlanSupplementRequest(
                request_id=f"cutreq_{_safe_path_component(item.cut_item_id)}",
                cut_item_id=item.cut_item_id,
                source_scope=item.source_scope,
                folder_name=item.folder_name,
                source_sentence_id=source_sentence_id,
                source_hook_beat_id=source_hook_beat_id,
                text=item.text,
                visual_intent=item.visual_intent,
                needed_duration_sec=item.duration_sec,
                reason=reason,
            )
        )

    return CutPlanSupplementRequestsDocument(
        project_id=project.id,
        source_cut_plan_hash=content_hash_of_model(cut_plan),
        requests=requests,
    )


def save_cut_plan_supplement_requests(
    project: Project, document: CutPlanSupplementRequestsDocument
) -> Path:
    normalized = document.model_copy(update={"project_id": project.id})
    path = get_cut_plan_supplement_requests_path(project.work_dir_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(normalized.model_dump_json(indent=2), encoding="utf-8")
    return path


def load_cut_plan_supplement_requests(project: Project) -> CutPlanSupplementRequestsDocument | None:
    path = get_cut_plan_supplement_requests_path(project.work_dir_path)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return CutPlanSupplementRequestsDocument.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None


def _update_request(
    project: Project, request_id: str, **updates: Any
) -> CutPlanSupplementRequest | None:
    document = load_cut_plan_supplement_requests(project)
    if document is None:
        return None
    updated_request: CutPlanSupplementRequest | None = None
    new_requests: list[CutPlanSupplementRequest] = []
    for request in document.requests:
        if request.request_id == request_id:
            updated_request = request.model_copy(update=updates)
            new_requests.append(updated_request)
        else:
            new_requests.append(request)
    save_cut_plan_supplement_requests(project, document.model_copy(update={"requests": new_requests}))
    return updated_request


# --- Kandidaten-Speicher (§1, ein File für alle Requests) ---


def _load_candidates_store(project: Project) -> dict[str, CutPlanSupplementCandidatesDocument]:
    path = get_cut_plan_supplement_candidates_path(project.work_dir_path)
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    documents = payload.get("documents", []) if isinstance(payload, dict) else []
    store: dict[str, CutPlanSupplementCandidatesDocument] = {}
    for entry in documents:
        try:
            document = CutPlanSupplementCandidatesDocument.model_validate(entry)
        except ValueError:
            continue
        store[document.request_id] = document
    return store


def _save_candidates_store(project: Project, store: dict[str, CutPlanSupplementCandidatesDocument]) -> Path:
    path = get_cut_plan_supplement_candidates_path(project.work_dir_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "project_id": project.id,
        "documents": [document.model_dump(mode="json") for document in store.values()],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load_cut_plan_supplement_candidates_for_request(
    project: Project, request_id: str
) -> CutPlanSupplementCandidatesDocument | None:
    return _load_candidates_store(project).get(request_id)


def _save_candidates_document(
    project: Project, document: CutPlanSupplementCandidatesDocument
) -> CutPlanSupplementCandidatesDocument:
    store = _load_candidates_store(project)
    store[document.request_id] = document
    _save_candidates_store(project, store)
    return document


# --- Kandidatensuche (§5) — NUR bei explizitem Aufruf, nie automatisch ---


def _to_cut_plan_candidate(
    request_id: str, provider: str, raw: SupplementCandidate
) -> CutPlanSupplementCandidate:
    risks: list[str] = []
    if raw.is_mock:
        risks.append("MOCK_CANDIDATE")
    if not raw.download_enabled:
        risks.append("DOWNLOAD_DISABLED")
    if raw.requires_user_approval:
        risks.append("REQUIRES_USER_APPROVAL")
    if raw.location_match == "missing":
        risks.append("LOCATION_MATCH_MISSING")
    return CutPlanSupplementCandidate(
        candidate_id=raw.candidate_id,
        request_id=request_id,
        provider=provider,
        title=raw.title,
        description=raw.description,
        preview_url=raw.preview_url,
        download_url=raw.download_url,
        asset_type=raw.media_type,
        width=raw.width,
        height=raw.height,
        duration_sec=raw.duration_sec,
        license=raw.license,
        source_url=raw.source_page_url,
        score=raw.match_score,
        risks=risks,
        provider_candidate_snapshot=raw.model_dump(mode="json"),
    )


def search_candidates_for_cut_plan_request(
    project: Project,
    request_id: str,
    provider_settings: dict[str, Any] | None = None,
) -> CutPlanSupplementCandidatesDocument:
    """Sucht Kandidaten für GENAU EINEN Request — läuft ausschließlich bei
    explizitem Aufruf (UI-Button „Supplement-Kandidaten suchen“), NIEMALS
    automatisch beim Draft-Bau, bei der Asset-Auswahl oder Validierung.

    Nutzt ausschließlich `SupplementSourceAdapter.search` (technischer
    Adapter) — NICHT die höherstufige Produktions-Suchorchestrierung, die
    _otio/supplement/supplement_requests.json mutiert. Speichert das
    Ergebnis in supplement_candidates.json."""
    provider_settings = dict(provider_settings or {})
    provider = str(provider_settings.get("provider") or SUPPLEMENT_SOURCE_PEXELS)

    requests_document = load_cut_plan_supplement_requests(project)
    if requests_document is None:
        raise ValueError("Keine Supplement Requests vorhanden — bitte zuerst erzeugen.")
    request = next((entry for entry in requests_document.requests if entry.request_id == request_id), None)
    if request is None:
        raise ValueError(f"Supplement Request '{request_id}' nicht gefunden.")

    transient_request = SupplementRequest(
        supplement_request_id=request.request_id,
        section_id=request.cut_item_id,
        folder_name=request.folder_name,
        location_name=request.folder_name,
        beat_id=request.source_hook_beat_id or request.source_sentence_id or request.cut_item_id,
        passage_text=request.text,
        visual_requirement=request.visual_intent or request.reason,
        required_asset_type=str(provider_settings.get("required_asset_type", "video_preferred")),
        duration_needed_sec=request.needed_duration_sec,
        reason=request.reason,
    )

    try:
        adapter = get_supplement_adapter(provider)
        raw_candidates = adapter.search(transient_request)
    except Exception as exc:  # pragma: no cover - defensiv gegen Adapter-/Netzwerkfehler
        document = CutPlanSupplementCandidatesDocument(
            project_id=project.id,
            request_id=request_id,
            provider=provider,
            candidates=[],
            status=CUT_PLAN_SUPPLEMENT_CANDIDATES_STATUS_FAILED,
            error_message=_sanitize_error_message(str(exc)),
        )
        _save_candidates_document(project, document)
        _update_request(project, request_id, status=CUT_PLAN_SUPPLEMENT_REQUEST_STATUS_FAILED)
        return document

    candidates = [_to_cut_plan_candidate(request_id, provider, raw) for raw in raw_candidates]
    status = (
        CUT_PLAN_SUPPLEMENT_CANDIDATES_STATUS_READY
        if candidates
        else CUT_PLAN_SUPPLEMENT_CANDIDATES_STATUS_NO_RESULTS
    )
    document = CutPlanSupplementCandidatesDocument(
        project_id=project.id,
        request_id=request_id,
        provider=provider,
        candidates=candidates,
        status=status,
        error_message="",
    )
    _save_candidates_document(project, document)

    if candidates:
        _update_request(project, request_id, status=CUT_PLAN_SUPPLEMENT_REQUEST_STATUS_CANDIDATES_FOUND)
    return document


# --- Kandidat akzeptieren (§6, §7) ---


def apply_accepted_supplement_to_cut_plan_item(
    project: Project,
    cut_plan: CutPlanDocument,
    request: CutPlanSupplementRequest,
    accepted_asset: CutPlanSupplementAsset,
) -> CutPlanDocument:
    """Reine Funktion: aktualisiert GENAU das CutPlanItem, das zu
    request.cut_item_id gehört, mit dem bereits heruntergeladenen/lokal
    übernommenen accepted_asset. Löst KEINE Suche und KEINEN Download aus —
    das übernimmt der Aufrufer (accept_cut_plan_supplement_candidate).

    Wirft ValueError, wenn ein Video-Supplement nach video_head_trim_sec zu
    kurz für die benötigte Item-Dauer ist (§7) — kein stilles Akzeptieren."""
    settings = settings_from_snapshot(project, cut_plan)

    target_item = next((item for item in cut_plan.items if item.cut_item_id == request.cut_item_id), None)
    if target_item is None:
        raise ValueError(f"CutPlanItem '{request.cut_item_id}' nicht im Cut Plan gefunden.")

    if accepted_asset.asset_type == "video":
        source_in_sec = settings.video_head_trim_sec
        usable_duration_sec = max(0.0, accepted_asset.duration_sec - settings.video_head_trim_sec)
        if target_item.duration_sec > usable_duration_sec + _DURATION_EPSILON:
            raise ValueError(
                f"Supplement-Kandidat zu kurz für Item '{target_item.cut_item_id}': benötigt "
                f"{target_item.duration_sec:.2f}s, verfügbar {usable_duration_sec:.2f}s nach "
                f"video_head_trim_sec ({settings.video_head_trim_sec:.2f}s)."
            )
        source_out_sec = source_in_sec + target_item.duration_sec
    else:
        source_in_sec = 0.0
        source_out_sec = target_item.duration_sec

    segment = VisualSegment(
        segment_id=f"{target_item.cut_item_id}_seg_01",
        timeline_in_sec=target_item.timeline_start_sec,
        timeline_out_sec=target_item.timeline_end_sec,
        duration_sec=target_item.duration_sec,
        asset_id=accepted_asset.asset_id,
        asset_path=accepted_asset.asset_path,
        asset_type=accepted_asset.asset_type,
        source_in_sec=source_in_sec,
        source_out_sec=source_out_sec,
        track="V1",
        reason="supplement_asset",
    )

    remaining_blockers = [
        blocker for blocker in target_item.blockers if blocker != CUT_PLAN_ERROR_SUPPLEMENT_REQUIRED
    ]

    updated_item = target_item.model_copy(
        update={
            "chosen_asset_id": accepted_asset.asset_id,
            "asset_selection_status": CUT_PLAN_ASSET_SELECTION_SUPPLEMENT_USED,
            "asset_selection_reason": (
                f"Accepted supplement candidate '{accepted_asset.candidate_id}' from {accepted_asset.provider}."
            ),
            "fallback_reason": "Supplement asset used because no existing asset was suitable.",
            "supplement_request_id": request.request_id,
            "needs_supplement_asset": False,
            "planned_visual_segments": [segment],
            "blockers": remaining_blockers,
        }
    )

    updated_items = [
        updated_item if item.cut_item_id == target_item.cut_item_id else item for item in cut_plan.items
    ]
    updated_cut_plan = cut_plan.model_copy(update={"items": updated_items})

    # Visual Coverage (Phase 8.5) erneut anwenden — das neu eingesetzte
    # Supplement-Segment könnte selbst am Timeline-Rand liegen (z. B. erstes
    # Item) und muss denselben Vorlauf-/Pausen-Schutz erhalten wie jedes
    # andere Segment.
    updated_cut_plan = apply_visual_coverage_extensions(updated_cut_plan, settings)

    asset_usage_summary = update_asset_usage_summary(updated_cut_plan)
    warnings, blockers = aggregate_item_level_errors(updated_cut_plan.items)
    status = CUT_PLAN_STATUS_NEEDS_REVIEW if blockers else CUT_PLAN_STATUS_DRAFT

    return updated_cut_plan.model_copy(
        update={
            "asset_usage_summary": asset_usage_summary,
            "warnings": warnings,
            "blockers": blockers,
            "status": status,
        }
    )


def accept_cut_plan_supplement_candidate(
    project: Project, request_id: str, candidate_id: str
) -> CutPlanDocument:
    """I/O-Orchestrator: lädt Request/Kandidat/Draft, lädt den Kandidaten
    über den technischen Provider-Adapter herunter (NUR bei diesem expliziten
    Aufruf — niemals automatisch), speichert die Datei unter
    cut_plan/supplement_assets/{request_id}/, aktualisiert das CutPlanItem
    und speichert den Draft sowie den Request-Status neu.

    Nutzt ausschließlich `SupplementSourceAdapter.acquire` (technischer
    Adapter) — NICHT die höherstufige Produktions-Beschaffungsorchestrierung.
    Schreibt niemals unter _otio/supplement/, keine regulären Inventory-
    Dateien, keine Originalmedien."""
    requests_document = load_cut_plan_supplement_requests(project)
    if requests_document is None:
        raise ValueError("Keine Supplement Requests vorhanden.")
    request = next((entry for entry in requests_document.requests if entry.request_id == request_id), None)
    if request is None:
        raise ValueError(f"Supplement Request '{request_id}' nicht gefunden.")

    candidates_document = load_cut_plan_supplement_candidates_for_request(project, request_id)
    if candidates_document is None:
        raise ValueError(f"Keine Kandidaten für Request '{request_id}' vorhanden — bitte zuerst suchen.")
    candidate = next(
        (entry for entry in candidates_document.candidates if entry.candidate_id == candidate_id), None
    )
    if candidate is None:
        raise ValueError(f"Kandidat '{candidate_id}' nicht gefunden.")

    draft = load_cut_plan_draft(project)
    if draft is None:
        raise ValueError("Kein Cut Plan Draft vorhanden.")

    raw_candidate_data = dict(candidate.provider_candidate_snapshot)
    raw_candidate_data.setdefault("supplement_request_id", request_id)
    try:
        production_candidate = SupplementCandidate.model_validate(raw_candidate_data)
    except ValueError as exc:
        raise ValueError(f"Kandidat '{candidate_id}' konnte nicht rekonstruiert werden: {exc}") from exc

    adapter = get_supplement_adapter(candidate.provider)
    destination_folder = get_cut_plan_supplement_asset_request_dir(project.work_dir_path, request_id)
    try:
        acquired = adapter.acquire(production_candidate, destination_folder)
    except Exception as exc:
        _update_request(project, request_id, status=CUT_PLAN_SUPPLEMENT_REQUEST_STATUS_FAILED)
        raise ValueError(f"Supplement-Download fehlgeschlagen: {_sanitize_error_message(str(exc))}") from exc

    synthetic_asset_id = (
        f"cut_supplement_{_safe_path_component(request_id)}_{_safe_path_component(candidate_id)}"
    )
    accepted_asset = CutPlanSupplementAsset(
        asset_id=synthetic_asset_id,
        request_id=request_id,
        candidate_id=candidate_id,
        provider=candidate.provider,
        asset_path=str(acquired.local_path),
        asset_type=candidate.asset_type,
        duration_sec=(
            candidate.duration_sec
            if candidate.asset_type == "video"
            else (probe_duration_seconds(acquired.local_path) or candidate.duration_sec)
        ),
        width=candidate.width,
        height=candidate.height,
        license=candidate.license,
        source_url=candidate.source_url,
        status=CUT_PLAN_SUPPLEMENT_ASSET_STATUS_ACQUIRED,
    )
    # Für Video die tatsächlich per ffprobe gemessene Dauer verwenden, falls
    # verfügbar — die Provider-Metadaten (candidate.duration_sec) können von
    # der realen Datei abweichen (z. B. gekürzte Segmente bei manchen Quellen).
    if candidate.asset_type == "video":
        probed_duration = probe_duration_seconds(acquired.local_path)
        if probed_duration is not None:
            accepted_asset = accepted_asset.model_copy(update={"duration_sec": probed_duration})

    try:
        updated_cut_plan = apply_accepted_supplement_to_cut_plan_item(project, draft, request, accepted_asset)
    except ValueError:
        _update_request(project, request_id, status=CUT_PLAN_SUPPLEMENT_REQUEST_STATUS_FAILED)
        raise

    save_cut_plan_draft(project, updated_cut_plan)
    _update_request(
        project,
        request_id,
        status=CUT_PLAN_SUPPLEMENT_REQUEST_STATUS_ACCEPTED,
        accepted_candidate_id=candidate_id,
        accepted_asset_id=synthetic_asset_id,
        accepted_asset_path=accepted_asset.asset_path,
    )
    return updated_cut_plan
