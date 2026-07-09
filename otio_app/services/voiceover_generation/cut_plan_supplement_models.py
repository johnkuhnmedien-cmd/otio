"""Phase 8.6: Datenmodelle für die isolierte Cut-Plan-Supplement-Bridge.

Bewusst GETRENNT von `otio_app.analysis_models.SupplementRequest`/
`SupplementCandidate` (Produktion) — diese Modelle werden ausschließlich
unter `_otio/voiceover_generation/cut_plan/` persistiert und dürfen niemals
mit `_otio/supplement/supplement_requests.json` vermischt werden. Die
produktionsseitigen Modelle werden intern nur als technische Adapter-
Schnittstelle (siehe `cut_plan_supplement_bridge.py`) verwendet, niemals
direkt persistiert."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from otio_app.defaults import (
    AUDIO_SCOPE_FOLDER,
    CUT_PLAN_SUPPLEMENT_ASSET_STATUS_ACQUIRED,
    CUT_PLAN_SUPPLEMENT_CANDIDATES_STATUS_READY,
    CUT_PLAN_SUPPLEMENT_REQUEST_STATUS_OPEN,
)

__all__ = [
    "CutPlanSupplementRequest",
    "CutPlanSupplementRequestsDocument",
    "CutPlanSupplementCandidate",
    "CutPlanSupplementCandidatesDocument",
    "CutPlanSupplementAsset",
]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CutPlanSupplementRequest(BaseModel):
    """Isolierter Supplement-Bedarf für GENAU EIN CutPlanItem."""

    request_id: str
    cut_item_id: str
    source_scope: str = AUDIO_SCOPE_FOLDER  # intro|folder
    folder_name: str = ""
    source_sentence_id: str = ""
    source_hook_beat_id: str = ""
    text: str = ""
    visual_intent: str = ""
    needed_duration_sec: float = 0.0
    reason: str = ""
    status: str = CUT_PLAN_SUPPLEMENT_REQUEST_STATUS_OPEN  # OPEN|CANDIDATES_FOUND|ACCEPTED|REJECTED|FAILED
    created_at: datetime = Field(default_factory=_utcnow)
    accepted_candidate_id: str = ""
    accepted_asset_id: str = ""
    accepted_asset_path: str = ""
    warnings: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    # Phase 11.1: Traceability der zuletzt per LLM erzeugten Pexels-
    # Suchqueries für diesen Request — wird bei jedem Klick auf „Supplement-
    # Kandidaten suchen“ neu gesetzt. llm_query_status ist "" solange nie
    # gesucht wurde, sonst PASS|FAIL|PARSE_FAILED (siehe llm_trace_service).
    llm_queries: list[str] = Field(default_factory=list)
    llm_query_status: str = ""
    llm_query_run_id: str = ""
    llm_query_error: str = ""


class CutPlanSupplementRequestsDocument(BaseModel):
    project_id: str
    generated_at: datetime = Field(default_factory=_utcnow)
    source_cut_plan_hash: str = ""
    requests: list[CutPlanSupplementRequest] = Field(default_factory=list)


class CutPlanSupplementCandidate(BaseModel):
    """Ein Suchtreffer für GENAU EINEN CutPlanSupplementRequest."""

    candidate_id: str
    request_id: str
    provider: str
    title: str = ""
    description: str = ""
    preview_url: str = ""
    download_url: str = ""
    asset_type: str = "video"  # video|image
    width: int = 0
    height: int = 0
    duration_sec: float = 0.0
    license: str = ""
    source_url: str = ""
    score: float = 0.0
    risks: list[str] = Field(default_factory=list)
    # Interner Implementierungsdetail, KEIN Teil der redaktionellen Spec:
    # vollständiger Snapshot des produktionsseitigen SupplementCandidate
    # (technischer Adapter), damit `accept_cut_plan_supplement_candidate`
    # denselben Provider-Adapter (adapter.acquire) erneut aufrufen kann,
    # ohne eine zweite Suche auszulösen oder Produktions-Requests zu berühren.
    provider_candidate_snapshot: dict[str, Any] = Field(default_factory=dict)


class CutPlanSupplementCandidatesDocument(BaseModel):
    """Ergebnis EINES Suchlaufs für EINEN Request. Persistiert wird eine
    Sammlung solcher Dokumente (eines pro Request) in derselben Datei
    `supplement_candidates.json`."""

    project_id: str
    generated_at: datetime = Field(default_factory=_utcnow)
    request_id: str
    provider: str = ""
    candidates: list[CutPlanSupplementCandidate] = Field(default_factory=list)
    status: str = CUT_PLAN_SUPPLEMENT_CANDIDATES_STATUS_READY  # READY|NO_RESULTS|FAILED
    error_message: str = ""


class CutPlanSupplementAsset(BaseModel):
    """Ein tatsächlich heruntergeladenes/lokal übernommenes Supplement-Asset."""

    asset_id: str  # cut_supplement_{safe_request_id}_{safe_candidate_id}
    request_id: str
    candidate_id: str
    provider: str
    asset_path: str = ""
    asset_type: str = "video"  # video|image
    duration_sec: float = 0.0
    width: int = 0
    height: int = 0
    license: str = ""
    source_url: str = ""
    created_at: datetime = Field(default_factory=_utcnow)
    status: str = CUT_PLAN_SUPPLEMENT_ASSET_STATUS_ACQUIRED  # ACQUIRED|FAILED
