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
    "CutPlanSupplementAutoResolveAttempt",
    "CutPlanSupplementManifestEntry",
    "CutPlanSupplementManifestDocument",
]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CutPlanSupplementAutoResolveAttempt(BaseModel):
    """Phase 11.3: EIN geprüfter Kandidat innerhalb eines Auto-Resolve-Laufs
    (siehe cut_plan_supplement_auto_resolve_service.py) — rein informativ für
    Traceability/UI, kein redaktionelles Feld."""

    candidate_id: str
    provider: str = ""
    asset_type: str = ""
    validation_status: str = ""  # PASS|WEAK_PASS|NEEDS_USER_REVIEW|FAIL|DOWNLOAD_FAILED
    validation_score: float = 0.0
    validation_reason: str = ""
    description: str = ""


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
    # Phase 9: bereits beim Skriptschreiben vorbereiteter, ortsbezogener
    # Suchvorschlag (siehe CutPlanItem.supplement_search_hint) — wird als
    # bevorzugte Query in die Supplement-Suche eingespeist (siehe
    # cut_plan_supplement_bridge.search_candidates_for_cut_plan_request).
    # Leer für Intro-Requests und alle vor Phase 4/9 erzeugten Sentence-Items.
    supplement_search_hint: str = ""
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
    # Phase 11.3: Trace des letzten Auto-Resolve-Laufs (siehe
    # cut_plan_supplement_auto_resolve_service.py) — ACCEPTED, wenn ein
    # Kandidat die Gemini-Prüfung bestanden hat und automatisch akzeptiert
    # wurde, sonst NO_MATCH (kein Kandidat hat bestanden) oder "" (noch nie
    # ausgeführt). Kein automatischer generischer Ordner-Fallback in dieser
    # Phase — das folgt separat.
    auto_resolve_status: str = ""
    auto_resolve_attempts: list[CutPlanSupplementAutoResolveAttempt] = Field(default_factory=list)
    # Phase 11.6: vollständiger Snapshot (model_dump) des CutPlanItem VOR der
    # ERSTEN Übernahme (Stock-Akzeptanz, generischer Fallback ODER manuelle
    # Zuweisung) für diesen Request — ermöglicht ein exaktes "Übernahme
    # zurücknehmen" (siehe unaccept_cut_plan_supplement_request), statt den
    # Vorzustand zu erraten. Wird NUR beim allerersten Übernahme-Versuch
    # gesetzt (leer davor) und bei der Rücknahme wieder geleert — ein
    # nachfolgendes "Ersetzen" überschreibt ihn NICHT, damit die Rücknahme
    # immer zum tatsächlichen Ursprungszustand zurückführt.
    pre_accept_item_snapshot: dict[str, Any] = Field(default_factory=dict)


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
    """Ein tatsächlich heruntergeladenes/lokal übernommenes Supplement-Asset.

    asset_id ist entweder providerbasiert-stabil (supplement_{provider}_
    {provider_asset_id}, siehe stable_supplement_asset_id in
    cut_plan_supplement_bridge.py) — wenn der Provider eine stabile ID
    liefert (Adobe/Pexels) — oder pro-Request-eindeutig (cut_supplement_
    {safe_request_id}_{safe_candidate_id}) als Fallback, wenn keine
    provider_asset_id bekannt ist (z. B. zukünftige Provider ohne stabile
    ID). Die stabile Variante ermöglicht Phase E (Nutzervorgabe): dieselbe
    externe Stock-Datei wird bei jeder Wiederverwendung als DASSELBE Asset
    erkannt, wodurch die bestehende generische Reuse-Distance-/Max-Usage-
    Validierung automatisch greift, ohne Sonderfall-Code."""

    asset_id: str
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


class CutPlanSupplementManifestEntry(BaseModel):
    """Phase E (Nutzervorgabe, Juli 2026): EIN bereits erfolgreich
    heruntergeladenes Provider-Asset (Adobe Stock/Pexels) — unabhängig
    davon, für welchen Request es URSPRÜNGLICH beschafft wurde. Ermöglicht
    zwei Dinge:

    1. Dedup: dasselbe (provider, provider_asset_id) wird nie ein zweites
       Mal lizenziert/heruntergeladen (siehe stable_supplement_asset_id /
       find_reusable_supplement_manifest_entry in
       cut_plan_supplement_bridge.py).
    2. Wiederverwendung: ein bereits vorhandenes, zum selben Ordner
       passendes Asset kann für ein ANDERES Cut-Item übernommen werden,
       bevor eine neue externe Suche ausgelöst wird (siehe
       find_reusable_local_supplement_candidate in
       cut_plan_supplement_auto_resolve_service.py)."""

    asset_id: str
    provider: str
    provider_asset_id: str = ""
    asset_path: str
    asset_type: str = "video"  # video|image
    duration_sec: float = 0.0
    width: int = 0
    height: int = 0
    license: str = ""
    source_url: str = ""
    folder_name: str = ""
    first_request_id: str = ""
    first_candidate_id: str = ""
    created_at: datetime = Field(default_factory=_utcnow)


class CutPlanSupplementManifestDocument(BaseModel):
    """Persistiertes Gesamt-Manifest aller bisher heruntergeladenen
    Cut-Plan-Supplement-Assets (`supplement_manifest.json`) — EIN Dokument
    für das gesamte Projekt, nicht pro Request (anders als candidates_
    store, der pro-Request-Dokumente sammelt)."""

    project_id: str
    generated_at: datetime = Field(default_factory=_utcnow)
    entries: list[CutPlanSupplementManifestEntry] = Field(default_factory=list)
