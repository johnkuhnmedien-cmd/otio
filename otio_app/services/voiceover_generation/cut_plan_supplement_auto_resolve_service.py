"""Phase 11.3: Auto-Resolver für GENAU EINEN Cut-Plan-Supplement-Request.

Kombiniert die bestehenden Bausteine der isolierten Cut-Plan-Supplement-
Bridge (LLM-Suchqueries, Provider-Suche über Video+Foto, Download) mit einer
kombinierten Gemini-Beschreibung+-Validierung des heruntergeladenen
Materials zu einem einzigen automatischen Ablauf für EINEN Request:

  0. Phase E (Nutzervorgabe, Juli 2026): BEVOR überhaupt extern gesucht
     wird, prüft der Auto-Resolver, ob bereits ein für DENSELBEN Ordner
     heruntergeladenes Supplement-Asset aus einem FRÜHEREN Request
     wiederverwendet werden kann (siehe find_reusable_local_supplement_
     candidate, supplement_manifest.json) — spart externe Suche/Lizenz-
     kosten. Läuft durch DIESELBE Download-/Gemini-Prüfungs-/Akzeptanz-
     Pipeline wie jeder andere Kandidat (kein automatisches PASS ohne
     echte KI-Prüfung für DIESE Szene) — schlägt die Prüfung fehl, geht es
     normal mit Schritt 1 weiter.
  1. Provider der Reihe nach durchsuchen (Phase 12.5:
     CUT_PLAN_SUPPLEMENT_PROVIDER_SEARCH_ORDER = Adobe Stock, dann Pexels —
     Nutzervorgabe: Adobe zuerst, weil unlimited Plan + sofortige
     Lizenzierung, Pexels als kostenlose Ausweichquelle). Phase 12.7,
     Nutzervorgabe: pro Provider wird NICHT mehr Video+Foto gemeinsam in
     EINER Suche abgefragt, sondern in ZWEI GETRENNTEN Suchstufen — zuerst
     bis zu _AUTO_RESOLVE_MAX_CANDIDATES_PER_STAGE (2) VIDEOS, erst wenn
     KEINES davon PASS erreicht, bis zu 2 FOTOS desselben Providers. Ergibt
     die Stufenreihenfolge Adobe-Video -> Adobe-Foto -> Pexels-Video ->
     Pexels-Foto. Grund: Videos sind redaktionell bevorzugt, und die
     Adobe-Lizenzierung soll nicht mehr Kandidaten kosten als nötig.
  2. Kandidaten dieser Suchstufe (Provider + Medientyp) der Reihe nach
     herunterladen — bei Adobe bedeutet das bereits sofortige Lizenzierung
     (siehe AdobeStockAdapter.acquire(), Phase 12.3). Phase 12.6: ein
     Video-Kandidat, der laut Provider-Metadaten (candidate.duration_sec)
     offensichtlich zu kurz für das Item ist, wird VORHER als TOO_SHORT
     protokolliert und NICHT herunterladen/lizenziert — schützt insbesondere
     Adobe-Lizenzkontingent vor offensichtlich untauglichen Treffern.
  3. EIN kombinierter Gemini-Aufruf pro Kandidat: Frames + Satz/Visual
     Intent/Reason gemeinsam im selben Request — Gemini beschreibt UND
     beurteilt in einem Zug (siehe describe_and_validate_supplement_asset in
     gemini_client.py). Bewusst NICHT zwei getrennte Aufrufe (beschreiben,
     dann validieren) — spart Latenz/Kosten und die Bildinformation geht
     nicht über den Umweg einer separaten Text-Beschreibung verloren.
  4. beim ERSTEN Kandidaten (über ALLE Suchstufen in der festgelegten
     Reihenfolge) mit Status PASS: automatisch akzeptieren (siehe
     accept_cut_plan_supplement_candidate) und dem Cut-Item zuordnen —
     Schleife bricht sofort ab, weitere Suchstufen werden nicht mehr
     versucht.
     Phase 12.6: schlägt diese Übernahme trotzdem fehl (z. B. weicht die
     per ffprobe gemessene REALE Dauer von den Provider-Metadaten ab und
     das Asset ist doch zu kurz), wird das als ACCEPT_FAILED protokolliert
     und mit dem NÄCHSTEN Kandidaten weitergemacht — bricht NICHT den
     gesamten Auto-Resolve-Lauf (vorher: unbehandelter ValueError, der den
     kompletten Batch abgebrochen hat).
  5. Kein Kandidat bei KEINEM Provider erreicht PASS (oder ein Provider
     liefert keine/fehlerhafte Suchergebnisse — wird protokolliert, bricht
     aber nicht ab, sondern der nächste Provider wird versucht): Phase
     11.4 — automatischer Fallback auf ein bereits vorhandenes, neutrales
     Asset aus demselben Ordner-Inventory (siehe
     cut_plan_generic_fallback_service.py). Erfolgreich -> Ergebnis
     GENERIC_FALLBACK_USED. Auch das schlägt fehl (z. B. keine Assets im
     Ordner oder alle zu kurz) -> Ergebnis NO_MATCH.

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
from typing import Callable, Literal

from otio_app.defaults import (
    AUDIO_SCOPE_FOLDER,
    CUT_PLAN_ASSET_SELECTION_BLOCKED,
    CUT_PLAN_ASSET_SELECTION_SUPPLEMENT_REQUIRED,
    CUT_PLAN_AUTO_RESOLVE_PROVIDER_LOCAL_REUSE,
    CUT_PLAN_DEFAULT_MAX_ASSET_USAGE,
    CUT_PLAN_DEFAULT_MIN_ASSET_REUSE_DISTANCE_SHOTS,
    CUT_PLAN_DEFAULT_VIDEO_HEAD_TRIM_SEC,
    CUT_PLAN_SUPPLEMENT_CANDIDATES_STATUS_READY,
    CUT_PLAN_SUPPLEMENT_PROVIDER_SEARCH_ORDER,
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
from otio_app.services.voiceover_generation.cut_plan_asset_selector import settings_from_snapshot
from otio_app.services.voiceover_generation.cut_plan_models import CutPlanDocument
from otio_app.services.voiceover_generation.cut_plan_builder import load_cut_plan_draft
from otio_app.services.voiceover_generation.cut_plan_generic_fallback_service import (
    apply_generic_fallback_for_cut_plan_request,
)
from otio_app.services.voiceover_generation.cut_plan_models import CutPlanSettings
from otio_app.services.voiceover_generation.cut_plan_supplement_bridge import (
    accept_cut_plan_supplement_candidate,
    download_cut_plan_supplement_candidate,
    load_cut_plan_supplement_manifest,
    load_cut_plan_supplement_requests,
    record_supplement_manifest_validation,
    search_candidates_for_cut_plan_request,
    stable_supplement_asset_id,
    update_cut_plan_supplement_request,
)
from otio_app.services.voiceover_generation.cut_plan_supplement_query_service import (
    generate_cut_plan_supplement_queries,
)
from otio_app.services.voiceover_generation.llm_trace_service import STATUS_PASS
from otio_app.services.voiceover_generation.cut_plan_supplement_models import (
    CutPlanSupplementAsset,
    CutPlanSupplementAutoResolveAttempt,
    CutPlanSupplementCandidate,
    CutPlanSupplementCandidatesDocument,
    CutPlanSupplementManifestEntry,
    CutPlanSupplementRequest,
)

__all__ = [
    "AUTO_RESOLVE_STATUS_ACCEPTED",
    "AUTO_RESOLVE_STATUS_GENERIC_FALLBACK_USED",
    "AUTO_RESOLVE_STATUS_NO_MATCH",
    "AUTO_RESOLVE_STATUS_FAILED",
    "VALIDATION_STATUS_PASS",
    "VALIDATION_STATUS_TOO_SHORT",
    "VALIDATION_STATUS_ACCEPT_FAILED",
    "DEFAULT_AUTO_RESOLVE_VALIDATION_MODEL",
    "AUTO_RESOLVE_PROGRESS_QUERY_GENERATION_STARTED",
    "AUTO_RESOLVE_PROGRESS_QUERY_GENERATION_FINISHED",
    "AUTO_RESOLVE_PROGRESS_REQUEST_STARTED",
    "AUTO_RESOLVE_PROGRESS_STAGE_STARTED",
    "AUTO_RESOLVE_PROGRESS_STAGE_FAILED",
    "AUTO_RESOLVE_PROGRESS_STAGE_NO_RESULTS",
    "AUTO_RESOLVE_PROGRESS_CANDIDATE_RESULT",
    "AUTO_RESOLVE_PROGRESS_GENERIC_FALLBACK_STARTED",
    "AUTO_RESOLVE_PROGRESS_REQUEST_FINISHED",
    "AutoResolveProgressEvent",
    "CutPlanSupplementAutoResolveResult",
    "auto_resolve_cut_plan_supplement_request",
    "auto_resolve_all_cut_plan_supplement_requests",
    "find_reusable_local_supplement_candidates",
]

AUTO_RESOLVE_PROGRESS_QUERY_GENERATION_STARTED = "query_generation_started"
AUTO_RESOLVE_PROGRESS_QUERY_GENERATION_FINISHED = "query_generation_finished"
AUTO_RESOLVE_PROGRESS_REQUEST_STARTED = "request_started"
AUTO_RESOLVE_PROGRESS_STAGE_STARTED = "stage_started"
AUTO_RESOLVE_PROGRESS_STAGE_FAILED = "stage_failed"
AUTO_RESOLVE_PROGRESS_STAGE_NO_RESULTS = "stage_no_results"
AUTO_RESOLVE_PROGRESS_CANDIDATE_RESULT = "candidate_result"
AUTO_RESOLVE_PROGRESS_GENERIC_FALLBACK_STARTED = "generic_fallback_started"
AUTO_RESOLVE_PROGRESS_REQUEST_FINISHED = "request_finished"

AutoResolveProgressEventType = Literal[
    "query_generation_started",
    "query_generation_finished",
    "request_started",
    "stage_started",
    "stage_failed",
    "stage_no_results",
    "candidate_result",
    "generic_fallback_started",
    "request_finished",
]


@dataclass(frozen=True)
class AutoResolveProgressEvent:
    """Phase 12.8: einzelnes Fortschritts-/Trace-Event für Live-UI während
    Auto-Resolve (Einzel- oder Batch-Button im Cut-Plan-Tab)."""

    event_type: AutoResolveProgressEventType
    request_id: str = ""
    request_index: int = 0
    request_total: int = 0
    stage_index: int = 0
    stage_total: int = 0
    provider: str = ""
    asset_type: str = ""
    query: str = ""
    candidate_id: str = ""
    candidate_title: str = ""
    duration_sec: float = 0.0
    validation_status: str = ""
    validation_reason: str = ""
    result_status: str = ""
    message: str = ""


AutoResolveProgressCallback = Callable[[AutoResolveProgressEvent], None]

AUTO_RESOLVE_STATUS_ACCEPTED = "ACCEPTED"
AUTO_RESOLVE_STATUS_GENERIC_FALLBACK_USED = "GENERIC_FALLBACK_USED"
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

# Ein per Provider-Suche gemeldeter Kandidat gilt als zu kurz, wenn nach
# Abzug von video_head_trim_sec weniger Material übrig bleibt, als das
# Cut-Plan-Item braucht (dieselbe Toleranz wie apply_accepted_supplement_to_
# cut_plan_item in cut_plan_supplement_bridge.py, §7).
_AUTO_RESOLVE_DURATION_EPSILON = 0.05

# Status-Werte für auto_resolve_attempts, die NICHT von Gemini stammen,
# sondern rein technische Vorab-/Nachab-Prüfungen dieses Moduls sind —
# analog zu "DOWNLOAD_FAILED" (bereits bestehend).
VALIDATION_STATUS_TOO_SHORT = "TOO_SHORT"
VALIDATION_STATUS_ACCEPT_FAILED = "ACCEPT_FAILED"

# Phase 12.7, Nutzervorgabe: pro Suchstufe (Provider + Medientyp) werden
# höchstens 2 Kandidaten herunterladen/lizenziert und geprüft — schützt
# insbesondere Adobe-Lizenzkontingent vor unnötig vielen Versuchen, bevor
# auf den nächsten Medientyp/Provider gewechselt wird. "video" wird IMMER
# vor "image" versucht (redaktionelle Präferenz: Video vor Foto).
_AUTO_RESOLVE_MAX_CANDIDATES_PER_STAGE = 2
_AUTO_RESOLVE_ASSET_TYPE_SEARCH_ORDER = ("video", "image")


def _emit_progress(
    progress_callback: AutoResolveProgressCallback | None, event: AutoResolveProgressEvent
) -> None:
    if progress_callback is not None:
        progress_callback(event)


def _queries_for_stage(
    project: Project,
    request_id: str,
    candidates_document: CutPlanSupplementCandidatesDocument | None,
) -> str:
    """Liefert eine lesbare Query-Zusammenfassung für die Live-UI — bevorzugt
    query_used aus den Suchtreffern, sonst llm_queries/supplement_search_hint
    des Requests (nach search_candidates_for_cut_plan_request persistiert)."""
    queries: list[str] = []
    if candidates_document is not None:
        for candidate in candidates_document.candidates:
            query_used = str(candidate.provider_candidate_snapshot.get("query_used", "")).strip()
            if query_used and query_used not in queries:
                queries.append(query_used)
    if not queries:
        requests_document = load_cut_plan_supplement_requests(project)
        if requests_document is not None:
            request = next(
                (entry for entry in requests_document.requests if entry.request_id == request_id), None
            )
            if request is not None:
                if request.llm_queries:
                    queries = [query.strip() for query in request.llm_queries if query.strip()]
                elif request.supplement_search_hint.strip():
                    queries = [request.supplement_search_hint.strip()]
    return " · ".join(queries) if queries else "—"


def _prepare_llm_queries_for_auto_resolve(
    project: Project,
    request: CutPlanSupplementRequest,
    *,
    request_id: str,
    query_llm_provider: str,
    query_llm_model: str,
    progress_callback: AutoResolveProgressCallback | None,
) -> None:
    """Phase 12.9: erzeugt LLM-Suchqueries GENAU EINMAL pro Auto-Resolve-Lauf
    und persistiert sie auf dem Request — alle folgenden Suchstufen nutzen
    skip_llm_query_generation=True und vermeiden so bis zu 3 redundante
    LLM-Aufrufe pro Request (vorher: ein Aufruf pro Suchstufe)."""
    if not (query_llm_provider and query_llm_model):
        return

    _emit_progress(
        progress_callback,
        AutoResolveProgressEvent(
            event_type=AUTO_RESOLVE_PROGRESS_QUERY_GENERATION_STARTED,
            request_id=request_id,
        ),
    )
    query_result = generate_cut_plan_supplement_queries(
        project,
        request,
        provider=query_llm_provider,
        model=query_llm_model,
    )
    llm_queries = query_result.queries if query_result.status == STATUS_PASS else []
    update_cut_plan_supplement_request(
        project,
        request_id,
        llm_queries=llm_queries,
        llm_query_status=query_result.status,
        llm_query_run_id=query_result.run_id,
        llm_query_error=query_result.error,
    )
    query_label = " · ".join(llm_queries) if llm_queries else "—"
    if request.supplement_search_hint.strip() and request.supplement_search_hint.strip() not in llm_queries:
        hint = request.supplement_search_hint.strip()
        query_label = f"{hint} · {query_label}" if llm_queries else hint
    _emit_progress(
        progress_callback,
        AutoResolveProgressEvent(
            event_type=AUTO_RESOLVE_PROGRESS_QUERY_GENERATION_FINISHED,
            request_id=request_id,
            query=query_label,
            validation_status=query_result.status,
            message=query_result.error,
        ),
    )


def _candidate_is_too_short(
    candidate: CutPlanSupplementCandidate,
    *,
    needed_duration_sec: float,
    cut_plan_settings: CutPlanSettings | None,
) -> tuple[bool, float]:
    """Phase 12.6: prüft NUR anhand der vom Provider gemeldeten Metadaten
    (candidate.duration_sec), OHNE Download/Lizenzierung auszulösen, ob ein
    Video-Kandidat das benötigte Item niemals füllen kann. Adobe-Video-
    Lizenzierung kostet echtes Kontingent — ein derart offensichtlich zu
    kurzer Kandidat soll diese Kosten gar nicht erst verursachen.

    Bewusst konservativ: Bilder werden nie als zu kurz behandelt (§3, siehe
    apply_accepted_supplement_to_cut_plan_item), und ein Video ohne bekannte
    Dauer (candidate.duration_sec <= 0, z. B. bei unvollständigen Provider-
    Metadaten) wird NICHT vorab verworfen — die tatsächliche, per ffprobe
    gemessene Dauer entscheidet dann nach dem Download (siehe
    download_cut_plan_supplement_candidate/apply_accepted_supplement_to_
    cut_plan_item)."""
    if candidate.asset_type != "video" or candidate.duration_sec <= 0 or cut_plan_settings is None:
        return False, 0.0
    usable_duration_sec = max(0.0, candidate.duration_sec - cut_plan_settings.video_head_trim_sec)
    is_too_short = needed_duration_sec > usable_duration_sec + _AUTO_RESOLVE_DURATION_EPSILON
    return is_too_short, usable_duration_sec


# Phase J (Nutzervorgabe, Juli 2026): Priorität, mit der wiederverwendbare
# Manifest-Kandidaten für EINEN Request versucht werden — niedrigere Zahl
# zuerst. 'Für diesen Request bereits akzeptiert/PASS' schlägt IMMER
# 'für einen anderen Request im selben Ordner PASS', weil es exakt für
# denselben Text/visual_intent bereits geprüft wurde.
_TIER_ACCEPTED_OR_PASS_SAME_REQUEST = 0
_TIER_WEAK_PASS_SAME_REQUEST = 1
_TIER_PASS_OTHER_REQUEST = 2
_TIER_WEAK_PASS_OTHER_REQUEST = 3
_TIER_UNVALIDATED = 4
_TIER_ONLY_FAIL_OTHER_REQUEST = 5

# Phase J: mehr als bei der externen Suche versucht werden darf, weil eine
# lokale Wiederverwendung kein zusätzliches Lizenzkontingent kostet — nur
# der Gemini-Aufruf ist hier der limitierende Faktor.
_AUTO_RESOLVE_MAX_LOCAL_REUSE_CANDIDATES = 5


def _manifest_entry_tier(entry: CutPlanSupplementManifestEntry, request_id: str) -> int | None:
    """Phase J: liefert die Versuchspriorität (siehe _TIER_*-Konstanten)
    für EINEN Manifest-Eintrag bezogen auf EINEN Request — oder None, wenn
    der Eintrag für GENAU DIESEN Request bereits als FAIL/NEEDS_USER_
    REVIEW erkannt wurde (Phase K: kein erneuter, mit demselben Ergebnis
    zu erwartender Gemini-Aufruf für dieselbe Text-/Bild-Kombination)."""
    own_validations = [v for v in entry.validations if v.request_id == request_id]
    if own_validations:
        if any(v.accepted or v.validation_status == VALIDATION_STATUS_PASS for v in own_validations):
            return _TIER_ACCEPTED_OR_PASS_SAME_REQUEST
        if any(v.validation_status == "WEAK_PASS" for v in own_validations):
            return _TIER_WEAK_PASS_SAME_REQUEST
        return None  # nur FAIL/NEEDS_USER_REVIEW für GENAU diesen Request -> nicht erneut versuchen

    other_validations = entry.validations  # bereits geprüft: kein Eintrag mit request_id == diesem Request
    if any(v.validation_status == VALIDATION_STATUS_PASS for v in other_validations):
        return _TIER_PASS_OTHER_REQUEST
    if any(v.validation_status == "WEAK_PASS" for v in other_validations):
        return _TIER_WEAK_PASS_OTHER_REQUEST
    if not other_validations:
        return _TIER_UNVALIDATED
    return _TIER_ONLY_FAIL_OTHER_REQUEST


def find_reusable_local_supplement_candidates(
    project: Project,
    request: CutPlanSupplementRequest,
    *,
    cut_plan_settings: CutPlanSettings | None,
    cut_plan_draft: CutPlanDocument | None,
) -> list[CutPlanSupplementManifestEntry]:
    """Phase E/J (Nutzervorgabe, Juli 2026): bevor eine neue externe Suche
    (Adobe/Pexels) ausgelöst wird, prüft der Auto-Resolver, ob bereits ein
    für DIESEN Ordner heruntergeladenes Supplement-Asset aus einem
    FRÜHEREN Request wiederverwendet werden kann — spart Kosten/
    Lizenzkontingent und verhindert, dass ein bereits vorhandenes,
    passendes Asset ignoriert wird, nur weil es ursprünglich für ein
    ANDERES Cut-Item beschafft wurde. Nur für Folder-Requests (source_
    scope == folder) — beim Intro gibt es keine sinnvolle Ordner-Zuordnung.

    Konservative Sicherheitsprüfungen (zusätzlich zur regulären, später
    laufenden vollständigen Cut-Plan-Validierung — hier soll aber schon
    VOR dem teuren Gemini-Aufruf ein offensichtlich ungeeigneter Kandidat
    ausgeschlossen werden):
    - Datei muss noch auf der Platte existieren.
    - Video muss nach video_head_trim_sec lang genug für die benötigte
      Dauer sein (Bilder gelten als beliebig haltbar, §3).
    - max_asset_usage darf laut aktuellem Draft (asset_usage_summary)
      nicht bereits erreicht sein.
    - Innerhalb von min_asset_reuse_distance_shots (gezählt in Cut-Plan-
      Items DESSELBEN Ordners, in Timeline-Reihenfolge) darf dasselbe
      Asset nicht bereits verwendet worden sein.
    - Ein für GENAU DIESEN Request bereits als FAIL erkanntes Asset wird
      ausgeschlossen (Phase K).

    Phase J: liefert bis zu _AUTO_RESOLVE_MAX_LOCAL_REUSE_CANDIDATES
    Einträge, sortiert nach Versuchspriorität (_manifest_entry_tier) —
    'PASS für diesen Request' zuerst, dann 'WEAK_PASS für diesen Request',
    dann 'PASS für einen anderen Request im selben Ordner' usw.; 'Video
    vor Foto' (Nutzervorgabe) bleibt als SEKUNDÄRES Sortierkriterium
    innerhalb derselben Priorität erhalten. Leere Liste, wenn kein
    Manifest-Eintrag geeignet ist — der Aufrufer fährt dann normal mit der
    externen Suche fort."""
    if request.source_scope != AUDIO_SCOPE_FOLDER or not request.folder_name:
        return []
    manifest = load_cut_plan_supplement_manifest(project)
    if not manifest.entries:
        return []

    max_asset_usage = cut_plan_settings.max_asset_usage if cut_plan_settings else CUT_PLAN_DEFAULT_MAX_ASSET_USAGE
    min_reuse_distance = (
        cut_plan_settings.min_asset_reuse_distance_shots
        if cut_plan_settings
        else CUT_PLAN_DEFAULT_MIN_ASSET_REUSE_DISTANCE_SHOTS
    )
    video_head_trim_sec = (
        cut_plan_settings.video_head_trim_sec if cut_plan_settings else CUT_PLAN_DEFAULT_VIDEO_HEAD_TRIM_SEC
    )

    folder_items = (
        [item for item in cut_plan_draft.items if item.folder_name == request.folder_name]
        if cut_plan_draft is not None
        else []
    )
    target_index = next(
        (index for index, item in enumerate(folder_items) if item.cut_item_id == request.cut_item_id), None
    )
    asset_usage_summary = cut_plan_draft.asset_usage_summary if cut_plan_draft is not None else {}

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

    ranked: list[tuple[int, int, CutPlanSupplementManifestEntry]] = []
    for entry in manifest.entries:
        if entry.folder_name != request.folder_name:
            continue
        if not Path(entry.asset_path).is_file():
            continue
        if entry.asset_type == "video":
            usable_duration_sec = max(0.0, entry.duration_sec - video_head_trim_sec)
            if request.needed_duration_sec > usable_duration_sec + _AUTO_RESOLVE_DURATION_EPSILON:
                continue
        stable_id = stable_supplement_asset_id(entry.provider, entry.provider_asset_id, "", "")
        if asset_usage_summary.get(stable_id, 0) >= max_asset_usage:
            continue
        if _violates_reuse_distance(stable_id):
            continue
        tier = _manifest_entry_tier(entry, request.request_id)
        if tier is None:
            continue
        type_rank = _AUTO_RESOLVE_ASSET_TYPE_SEARCH_ORDER.index(entry.asset_type) if (
            entry.asset_type in _AUTO_RESOLVE_ASSET_TYPE_SEARCH_ORDER
        ) else len(_AUTO_RESOLVE_ASSET_TYPE_SEARCH_ORDER)
        ranked.append((tier, type_rank, entry))

    ranked.sort(key=lambda triple: (triple[0], triple[1]))
    return [entry for _tier, _type_rank, entry in ranked[:_AUTO_RESOLVE_MAX_LOCAL_REUSE_CANDIDATES]]


def _candidate_from_manifest_entry(
    entry: CutPlanSupplementManifestEntry, request: CutPlanSupplementRequest
) -> CutPlanSupplementCandidate:
    """Baut einen Kandidaten aus einem bereits heruntergeladenen Manifest-
    Eintrag (Phase E) — fließt durch DIESELBE Download-/Gemini-Prüfungs-/
    Akzeptanz-Pipeline wie jeder frisch gesuchte Kandidat.
    provider_candidate_snapshot enthält bewusst dieselbe provider_
    asset_id, damit download_cut_plan_supplement_candidate automatisch die
    Kopie aus dem Manifest verwendet, statt erneut zu suchen/lizenzieren/
    herunterzuladen (siehe find_reusable_supplement_manifest_entry in
    cut_plan_supplement_bridge.py)."""
    snapshot = {
        "candidate_id": f"reuse_{entry.provider}_{entry.provider_asset_id or entry.asset_id}",
        "supplement_request_id": request.request_id,
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
        request_id=request.request_id,
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


@dataclass
class CutPlanSupplementAutoResolveResult:
    status: str  # ACCEPTED | GENERIC_FALLBACK_USED | NO_MATCH | FAILED
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
    progress_callback: AutoResolveProgressCallback | None = None,
) -> CutPlanSupplementAutoResolveResult:
    """Führt den vollständigen Auto-Resolve-Ablauf (siehe Modul-Docstring)
    für GENAU EINEN Request aus — über ALLE Provider in
    CUT_PLAN_SUPPLEMENT_PROVIDER_SEARCH_ORDER (Phase 12.5: Adobe Stock, dann
    Pexels). Bricht beim ERSTEN Kandidaten (egal von welchem Provider) mit
    PASS ab und akzeptiert ihn automatisch; erst wenn KEIN Provider einen
    bestehenden Kandidaten liefert, greift der generische Ordner-Fallback
    (Phase 11.4) — an JEDEM Ausstiegspunkt, an dem kein Stock-Kandidat zum
    Einsatz kommt (Suchfehler, keine Suchergebnisse, kein Kandidat bestanden),
    damit möglichst nie ein Item unversorgt bleibt. Wirft KEINE Exception
    nach außen — jeder Fehlerfall wird im Ergebnis abgebildet, damit ein
    späterer Batch-Lauf über viele Requests niemals an einem einzigen
    Request abbricht."""

    def _fallback_or_no_match(
        attempts: list[CutPlanSupplementAutoResolveAttempt], *, search_error: str = ""
    ) -> CutPlanSupplementAutoResolveResult:
        _emit_progress(
            progress_callback,
            AutoResolveProgressEvent(
                event_type=AUTO_RESOLVE_PROGRESS_GENERIC_FALLBACK_STARTED,
                request_id=request_id,
            ),
        )
        try:
            _updated_cut_plan, fallback_candidate = apply_generic_fallback_for_cut_plan_request(
                project, request_id
            )
        except Exception as exc:  # noqa: BLE001 — generischer Fallback darf nicht crashen
            fallback_candidate = None
            attempts = [
                *attempts,
                CutPlanSupplementAutoResolveAttempt(
                    candidate_id="generic_fallback",
                    validation_status="GENERIC_FALLBACK_FAILED",
                    validation_reason=str(exc),
                ),
            ]

        if fallback_candidate is not None:
            update_cut_plan_supplement_request(
                project,
                request_id,
                auto_resolve_status=AUTO_RESOLVE_STATUS_GENERIC_FALLBACK_USED,
                auto_resolve_attempts=attempts,
            )
            result = CutPlanSupplementAutoResolveResult(
                status=AUTO_RESOLVE_STATUS_GENERIC_FALLBACK_USED,
                request_id=request_id,
                accepted_asset_id=fallback_candidate.asset_id,
                attempts=attempts,
            )
            _emit_progress(
                progress_callback,
                AutoResolveProgressEvent(
                    event_type=AUTO_RESOLVE_PROGRESS_REQUEST_FINISHED,
                    request_id=request_id,
                    result_status=result.status,
                    message=f"Generisches Ordner-Asset `{fallback_candidate.asset_id}` verwendet.",
                ),
            )
            return result

        update_cut_plan_supplement_request(
            project, request_id, auto_resolve_status=AUTO_RESOLVE_STATUS_NO_MATCH, auto_resolve_attempts=attempts
        )
        result = CutPlanSupplementAutoResolveResult(
            status=AUTO_RESOLVE_STATUS_NO_MATCH, request_id=request_id, attempts=attempts, error=search_error
        )
        _emit_progress(
            progress_callback,
            AutoResolveProgressEvent(
                event_type=AUTO_RESOLVE_PROGRESS_REQUEST_FINISHED,
                request_id=request_id,
                result_status=result.status,
                message="Kein passendes Stock-Asset und kein generischer Fallback gefunden.",
            ),
        )
        return result

    requests_document = load_cut_plan_supplement_requests(project)
    request = (
        next((entry for entry in requests_document.requests if entry.request_id == request_id), None)
        if requests_document is not None
        else None
    )
    if request is None:
        raise ValueError(f"Supplement Request '{request_id}' nicht gefunden.")

    attempts: list[CutPlanSupplementAutoResolveAttempt] = []

    def _try_candidate(
        candidate: CutPlanSupplementCandidate,
        *,
        stage_index: int,
        stage_total: int,
        query_label: str,
        cut_plan_settings: CutPlanSettings | None,
        validation_model: str,
    ) -> CutPlanSupplementAutoResolveResult | None:
        """Lädt/prüft/akzeptiert GENAU EINEN Kandidaten (ein regulärer
        Stock-Suchtreffer ODER ein aus einem Manifest-Eintrag
        rekonstruierter Wiederverwendungs-Kandidat, siehe
        find_reusable_local_supplement_candidates/_candidate_from_manifest_
        entry) — extrahiert aus der ursprünglichen Inline-Schleife, damit
        Phase E (lokale Wiederverwendung VOR externer Suche) dieselbe
        Download-/Gemini-Prüfungs-/Akzeptanz-Logik nutzen kann, ohne sie
        zu duplizieren. Mutiert `attempts` (append/replace) genau wie die
        ursprüngliche Schleife. Gibt ein Ergebnis zurück, sobald DIESER
        Kandidat akzeptiert wurde — sonst None (Aufrufer versucht den
        nächsten Kandidaten/die nächste Stufe)."""
        too_short, usable_duration_sec = _candidate_is_too_short(
            candidate, needed_duration_sec=request.needed_duration_sec, cut_plan_settings=cut_plan_settings
        )
        if too_short:
            too_short_reason = (
                f"Kandidat laut Provider-Metadaten zu kurz: benötigt "
                f"{request.needed_duration_sec:.2f}s, verfügbar {usable_duration_sec:.2f}s nach "
                "video_head_trim_sec — kein Download/keine Lizenzierung ausgelöst."
            )
            attempts.append(
                CutPlanSupplementAutoResolveAttempt(
                    candidate_id=candidate.candidate_id,
                    provider=candidate.provider,
                    asset_type=candidate.asset_type,
                    validation_status=VALIDATION_STATUS_TOO_SHORT,
                    validation_reason=too_short_reason,
                )
            )
            _emit_progress(
                progress_callback,
                AutoResolveProgressEvent(
                    event_type=AUTO_RESOLVE_PROGRESS_CANDIDATE_RESULT,
                    request_id=request_id,
                    stage_index=stage_index,
                    stage_total=stage_total,
                    provider=candidate.provider,
                    asset_type=candidate.asset_type,
                    query=query_label,
                    candidate_id=candidate.candidate_id,
                    candidate_title=candidate.title,
                    duration_sec=candidate.duration_sec,
                    validation_status=VALIDATION_STATUS_TOO_SHORT,
                    validation_reason=too_short_reason,
                ),
            )
            return None

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
            _emit_progress(
                progress_callback,
                AutoResolveProgressEvent(
                    event_type=AUTO_RESOLVE_PROGRESS_CANDIDATE_RESULT,
                    request_id=request_id,
                    stage_index=stage_index,
                    stage_total=stage_total,
                    provider=candidate.provider,
                    asset_type=candidate.asset_type,
                    query=query_label,
                    candidate_id=candidate.candidate_id,
                    candidate_title=candidate.title,
                    duration_sec=candidate.duration_sec,
                    validation_status="DOWNLOAD_FAILED",
                    validation_reason=str(exc),
                ),
            )
            return None

        analysis = _describe_and_validate_downloaded_asset(
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

        # Phase I (Nutzervorgabe): das Gemini-Ergebnis für DIESEN Request auf
        # dem Manifest-Eintrag speichern (falls provider_asset_id bekannt) —
        # ermöglicht Phase J/K, bei einer künftigen Anfrage PASS/WEAK_PASS
        # bevorzugt wiederzuverwenden bzw. ein für denselben Request bereits
        # als FAIL erkanntes Asset nicht erneut per Gemini zu prüfen.
        candidate_provider_asset_id = str(
            candidate.provider_candidate_snapshot.get("provider_asset_id", "")
        )
        record_supplement_manifest_validation(
            project,
            provider=candidate.provider,
            provider_asset_id=candidate_provider_asset_id,
            request_id=request_id,
            validation_status=attempt.validation_status,
            validation_score=attempt.validation_score,
            validation_reason=attempt.validation_reason,
            description=attempt.description,
            accepted=False,
        )

        if str(analysis.get("status")) == VALIDATION_STATUS_PASS:
            # Phase 12.6: die tatsächliche, per ffprobe gemessene Dauer
            # (downloaded_asset.duration_sec) kann von den Provider-
            # Metadaten abweichen — apply_accepted_supplement_to_cut_
            # plan_item wirft in diesem Fall ValueError. Vorher hätte das
            # den gesamten Batch abgebrochen (unbehandelte Exception);
            # jetzt wird der Kandidat als ACCEPT_FAILED protokolliert und
            # mit dem NÄCHSTEN Kandidaten weitergemacht.
            try:
                accept_cut_plan_supplement_candidate(
                    project,
                    request_id,
                    candidate.candidate_id,
                    downloaded_asset=downloaded_asset,
                    candidate=candidate,
                )
            except ValueError as exc:
                accept_failed_reason = (
                    "Gemini-Prüfung bestanden, aber Übernahme fehlgeschlagen "
                    f"(tatsächliche Dauer abweichend?): {exc}"
                )
                attempts[-1] = attempts[-1].model_copy(
                    update={
                        "validation_status": VALIDATION_STATUS_ACCEPT_FAILED,
                        "validation_reason": accept_failed_reason,
                    }
                )
                _emit_progress(
                    progress_callback,
                    AutoResolveProgressEvent(
                        event_type=AUTO_RESOLVE_PROGRESS_CANDIDATE_RESULT,
                        request_id=request_id,
                        stage_index=stage_index,
                        stage_total=stage_total,
                        provider=candidate.provider,
                        asset_type=candidate.asset_type,
                        query=query_label,
                        candidate_id=candidate.candidate_id,
                        candidate_title=candidate.title,
                        duration_sec=downloaded_asset.duration_sec or candidate.duration_sec,
                        validation_status=VALIDATION_STATUS_ACCEPT_FAILED,
                        validation_reason=accept_failed_reason,
                    ),
                )
                return None
            record_supplement_manifest_validation(
                project,
                provider=candidate.provider,
                provider_asset_id=candidate_provider_asset_id,
                request_id=request_id,
                validation_status=attempt.validation_status,
                validation_score=attempt.validation_score,
                validation_reason=attempt.validation_reason,
                description=attempt.description,
                accepted=True,
            )
            update_cut_plan_supplement_request(
                project,
                request_id,
                auto_resolve_status=AUTO_RESOLVE_STATUS_ACCEPTED,
                auto_resolve_attempts=attempts,
            )
            _emit_progress(
                progress_callback,
                AutoResolveProgressEvent(
                    event_type=AUTO_RESOLVE_PROGRESS_CANDIDATE_RESULT,
                    request_id=request_id,
                    stage_index=stage_index,
                    stage_total=stage_total,
                    provider=candidate.provider,
                    asset_type=candidate.asset_type,
                    query=query_label,
                    candidate_id=candidate.candidate_id,
                    candidate_title=candidate.title,
                    duration_sec=downloaded_asset.duration_sec or candidate.duration_sec,
                    validation_status=VALIDATION_STATUS_PASS,
                    validation_reason=attempt.validation_reason,
                ),
            )
            result = CutPlanSupplementAutoResolveResult(
                status=AUTO_RESOLVE_STATUS_ACCEPTED,
                request_id=request_id,
                accepted_candidate_id=candidate.candidate_id,
                accepted_asset_id=downloaded_asset.asset_id,
                attempts=attempts,
            )
            _emit_progress(
                progress_callback,
                AutoResolveProgressEvent(
                    event_type=AUTO_RESOLVE_PROGRESS_REQUEST_FINISHED,
                    request_id=request_id,
                    result_status=result.status,
                    message=f"Kandidat `{candidate.candidate_id}` automatisch akzeptiert.",
                ),
            )
            return result

        _emit_progress(
            progress_callback,
            AutoResolveProgressEvent(
                event_type=AUTO_RESOLVE_PROGRESS_CANDIDATE_RESULT,
                request_id=request_id,
                stage_index=stage_index,
                stage_total=stage_total,
                provider=candidate.provider,
                asset_type=candidate.asset_type,
                query=query_label,
                candidate_id=candidate.candidate_id,
                candidate_title=candidate.title,
                duration_sec=downloaded_asset.duration_sec or candidate.duration_sec,
                validation_status=attempt.validation_status,
                validation_reason=attempt.validation_reason,
            ),
        )
        return None

    # Phase 12.6: Cut-Plan-Settings (insb. video_head_trim_sec) VORAB laden,
    # um Video-Kandidaten schon vor Download/Lizenzierung als offensichtlich
    # zu kurz erkennen zu können — kein Draft vorhanden (sollte praktisch
    # nicht vorkommen, da Requests nur aus einem Draft erzeugt werden) heißt
    # lediglich: keine Vorab-Prüfung, die tatsächliche Dauer entscheidet dann
    # wie bisher erst beim Übernehmen.
    cut_plan_draft = load_cut_plan_draft(project)
    cut_plan_settings = settings_from_snapshot(project, cut_plan_draft) if cut_plan_draft is not None else None

    search_errors: list[str] = []

    # Phase 12.7: jede Kombination aus Provider (Phase 12.5-Reihenfolge) und
    # Medientyp (Video vor Foto, Nutzervorgabe) ist eine eigene Suchstufe —
    # ergibt z. B. [(adobe_stock, video), (adobe_stock, image),
    # (pexels, video), (pexels, image)]. Jede Stufe fragt höchstens
    # _AUTO_RESOLVE_MAX_CANDIDATES_PER_STAGE (2) Kandidaten an. Vorab
    # berechnet (statt erst nach der LLM-Query-Generierung), damit Phase E
    # (lokale Wiederverwendung, siehe unten) bereits stage_total für ihre
    # Progress-Events kennt.
    stages = [
        (provider, asset_type)
        for provider in CUT_PLAN_SUPPLEMENT_PROVIDER_SEARCH_ORDER
        for asset_type in _AUTO_RESOLVE_ASSET_TYPE_SEARCH_ORDER
    ]
    stage_total = len(stages)

    # Phase E (Nutzervorgabe, Juli 2026): BEVOR überhaupt extern gesucht
    # wird (und damit auch VOR der LLM-Query-Generierung, die dafür
    # unnötig wäre) — prüfen, ob ein bereits heruntergeladenes Supplement-
    # Asset aus einem früheren Request für DIESEN Ordner wiederverwendet
    # werden kann. Läuft durch dieselbe Download-/Gemini-Prüfungs-/
    # Akzeptanz-Pipeline wie jeder andere Kandidat.
    reusable_entries = find_reusable_local_supplement_candidates(
        project, request, cut_plan_settings=cut_plan_settings, cut_plan_draft=cut_plan_draft
    )
    if reusable_entries:
        _emit_progress(
            progress_callback,
            AutoResolveProgressEvent(
                event_type=AUTO_RESOLVE_PROGRESS_STAGE_STARTED,
                request_id=request_id,
                stage_index=0,
                stage_total=stage_total,
                provider=CUT_PLAN_AUTO_RESOLVE_PROVIDER_LOCAL_REUSE,
                asset_type=reusable_entries[0].asset_type,
                query="—",
                message=(
                    f"{len(reusable_entries)} bereits heruntergeladene(s) Supplement-Asset(s) im Ordner "
                    "gefunden — werden vor neuer Suche geprüft (PASS/WEAK_PASS bevorzugt)."
                ),
            ),
        )
        # Phase J/K: ALLE lokal wiederverwendbaren Kandidaten der Reihe nach
        # versuchen (bereits nach Priorität sortiert — PASS für diesen
        # Request zuerst) — erst wenn KEINER von ihnen die Gemini-Prüfung
        # besteht, wird überhaupt extern gesucht.
        for reusable_entry in reusable_entries:
            reuse_candidate = _candidate_from_manifest_entry(reusable_entry, request)
            reuse_result = _try_candidate(
                reuse_candidate,
                stage_index=0,
                stage_total=stage_total,
                query_label="lokale Wiederverwendung",
                cut_plan_settings=cut_plan_settings,
                validation_model=validation_model,
            )
            if reuse_result is not None:
                return reuse_result

    _prepare_llm_queries_for_auto_resolve(
        project,
        request,
        request_id=request_id,
        query_llm_provider=query_llm_provider,
        query_llm_model=query_llm_model,
        progress_callback=progress_callback,
    )
    skip_llm_query_generation = bool(query_llm_provider and query_llm_model)

    for stage_index, (provider, asset_type) in enumerate(stages, start=1):
        stage_label = f"{provider}/{asset_type}"
        try:
            candidates_document = search_candidates_for_cut_plan_request(
                project,
                request_id,
                {
                    "provider": provider,
                    "required_asset_type": asset_type,
                    "max_candidates": _AUTO_RESOLVE_MAX_CANDIDATES_PER_STAGE,
                },
                query_llm_provider=query_llm_provider,
                query_llm_model=query_llm_model,
                skip_llm_query_generation=skip_llm_query_generation,
            )
        except Exception as exc:  # noqa: BLE001 — Suche darf den Auto-Resolver nicht crashen
            search_errors.append(f"{stage_label}: {exc}")
            _emit_progress(
                progress_callback,
                AutoResolveProgressEvent(
                    event_type=AUTO_RESOLVE_PROGRESS_STAGE_FAILED,
                    request_id=request_id,
                    stage_index=stage_index,
                    stage_total=stage_total,
                    provider=provider,
                    asset_type=asset_type,
                    message=str(exc),
                ),
            )
            continue

        query_label = _queries_for_stage(project, request_id, candidates_document)

        if (
            candidates_document.status != CUT_PLAN_SUPPLEMENT_CANDIDATES_STATUS_READY
            or not candidates_document.candidates
        ):
            if candidates_document.error_message:
                search_errors.append(f"{stage_label}: {candidates_document.error_message}")
            _emit_progress(
                progress_callback,
                AutoResolveProgressEvent(
                    event_type=AUTO_RESOLVE_PROGRESS_STAGE_NO_RESULTS,
                    request_id=request_id,
                    stage_index=stage_index,
                    stage_total=stage_total,
                    provider=provider,
                    asset_type=asset_type,
                    query=query_label,
                    message=candidates_document.error_message,
                ),
            )
            continue

        _emit_progress(
            progress_callback,
            AutoResolveProgressEvent(
                event_type=AUTO_RESOLVE_PROGRESS_STAGE_STARTED,
                request_id=request_id,
                stage_index=stage_index,
                stage_total=stage_total,
                provider=provider,
                asset_type=asset_type,
                query=query_label,
                message=f"{len(candidates_document.candidates)} Kandidat(en) gefunden.",
            ),
        )

        for candidate in candidates_document.candidates[:_AUTO_RESOLVE_MAX_CANDIDATES_PER_STAGE]:
            result = _try_candidate(
                candidate,
                stage_index=stage_index,
                stage_total=stage_total,
                query_label=query_label,
                cut_plan_settings=cut_plan_settings,
                validation_model=validation_model,
            )
            if result is not None:
                return result
        # Kein Kandidat dieser Suchstufe (Provider + Medientyp) hat bestanden
        # -> nächste Stufe in der Reihenfolge versuchen (Phase 12.7: erst
        # der andere Medientyp desselben Providers, dann der nächste
        # Provider), bevor auf den generischen Ordner-Fallback
        # zurückgegriffen wird.

    # Phase 11.4: kein Stock-Kandidat bei KEINER Suchstufe hat bestanden ->
    # generischer Ordner-Fallback, BEVOR endgültig NO_MATCH zurückgegeben wird.
    return _fallback_or_no_match(attempts, search_error="; ".join(search_errors))


def auto_resolve_all_cut_plan_supplement_requests(
    project: Project,
    *,
    query_llm_provider: str,
    query_llm_model: str,
    validation_model: str = DEFAULT_AUTO_RESOLVE_VALIDATION_MODEL,
    progress_callback: AutoResolveProgressCallback | None = None,
) -> list[CutPlanSupplementAutoResolveResult]:
    """Phase 11.5: läuft SEQUENZIELL (ein Request nach dem anderen, bessere
    Logs/Nachvollziehbarkeit — analog zu 'Alle Folder Voice-overs
    generieren' in der Voice-over-Pipeline) über alle NOCH NICHT versorgten
    Supplement Requests und wendet auto_resolve_cut_plan_supplement_request
    (siehe dort: Suche + Download + kombinierte Gemini-Prüfung + ggf.
    generischer Ordner-Fallback) auf jeden einzeln an.

    'Noch nicht versorgt' = request.accepted_asset_id ist leer — das gilt
    sowohl für eine Stock-Akzeptanz als auch für einen bereits genutzten
    generischen Fallback (beide setzen dieses Feld, siehe
    accept_cut_plan_supplement_candidate / apply_generic_fallback_for_cut_
    plan_request). Bereits versorgte Requests werden NICHT erneut
    versucht — ein erneuter Versuch ist weiterhin einzeln möglich (Klick
    auf 'Akzeptierten Candidate ersetzen' bzw. den Einzel-Button).

    Läuft — wie alle anderen 'Alle X'-Sammel-Aktionen dieser Pipeline —
    blockierend ohne Abbrechen-Möglichkeit. Wirft KEINE Exception nach
    außen für einen einzelnen Request (siehe auto_resolve_cut_plan_
    supplement_request) — ein Fehler bei einem Request stoppt den Batch
    nicht."""
    requests_document = load_cut_plan_supplement_requests(project)
    if requests_document is None:
        return []

    open_requests = [entry for entry in requests_document.requests if not entry.accepted_asset_id]
    results: list[CutPlanSupplementAutoResolveResult] = []
    total = len(open_requests)
    for index, request in enumerate(open_requests, start=1):
        if progress_callback is not None:
            progress_callback(
                AutoResolveProgressEvent(
                    event_type=AUTO_RESOLVE_PROGRESS_REQUEST_STARTED,
                    request_id=request.request_id,
                    request_index=index,
                    request_total=total,
                )
            )
        results.append(
            auto_resolve_cut_plan_supplement_request(
                project,
                request.request_id,
                query_llm_provider=query_llm_provider,
                query_llm_model=query_llm_model,
                validation_model=validation_model,
                progress_callback=progress_callback,
            )
        )
    return results
