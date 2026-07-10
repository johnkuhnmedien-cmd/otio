"""Phase 8.4: Vollständige Cut-Plan-Validierung.

Liest AUSSCHLIESSLICH den bestehenden `cut_plan.draft.json` sowie lesend den
bestätigten Voice-over-Projektplan/Inventories. Erzeugt/aktualisiert
`cut_plan.validation_report.json` und den Draft-Status. Kein Confirm/Lock,
kein EditPlanDocument, kein OTIO-Export, keine Supplement-Suche/-Beschaffung,
kein LLM-Aufruf (nur `is_retryable_by_llm`-Metadaten für spätere Phasen)."""

from __future__ import annotations

import json
from pathlib import Path

from otio_app.defaults import (
    AUDIO_SCOPE_FOLDER,
    CUT_PLAN_ERROR_AMBIGUOUS_ASSET_ID,
    CUT_PLAN_ERROR_ASSET_FILE_MISSING,
    CUT_PLAN_ERROR_ASSET_REUSE_DISTANCE_TOO_SHORT,
    CUT_PLAN_ERROR_ASSET_TOO_SHORT,
    CUT_PLAN_ERROR_AUDIO_GAP_UNEXPECTED,
    CUT_PLAN_ERROR_BLACK_GAP_DURING_VOICEOVER,
    CUT_PLAN_ERROR_CATEGORY_LABELS,
    CUT_PLAN_ERROR_CATEGORY_OTHER,
    CUT_PLAN_ERROR_FRAME_ROUNDING_ERROR,
    CUT_PLAN_ERROR_INVALID_ASSET_ID,
    CUT_PLAN_ERROR_INVALID_AUDIO_PATH,
    CUT_PLAN_ERROR_MAX_ASSET_USAGE_EXCEEDED,
    CUT_PLAN_ERROR_MISSING_ALIGNMENT,
    CUT_PLAN_ERROR_MISSING_ASSET_MAPPING,
    CUT_PLAN_ERROR_MISSING_AUDIO,
    CUT_PLAN_ERROR_SHOT_TOO_LONG,
    CUT_PLAN_ERROR_SHOT_TOO_SHORT,
    CUT_PLAN_ERROR_SOURCE_PLAN_NOT_READY,
    CUT_PLAN_ERROR_SOURCE_RANGE_INVALID,
    CUT_PLAN_ERROR_SUPPLEMENT_REASON_MISSING,
    CUT_PLAN_ERROR_SUPPLEMENT_REQUIRED,
    CUT_PLAN_ERROR_TIMELINE_OVERLAP,
    CUT_PLAN_ASSET_SELECTION_SUPPLEMENT_REQUIRED,
    CUT_PLAN_ASSET_SELECTION_UNRESOLVED,
    CUT_PLAN_DURATION_STRATEGY_SPLIT,
    CUT_PLAN_FIX_BY_PYTHON,
    CUT_PLAN_FIX_BY_USER,
    CUT_PLAN_STATUS_BLOCKED,
    CUT_PLAN_STATUS_NEEDS_REVIEW,
    CUT_PLAN_STATUS_VALIDATED,
    CUT_PLAN_VALIDATION_STATUS_BLOCKED,
    CUT_PLAN_VALIDATION_STATUS_PASS,
    CUT_PLAN_VALIDATION_STATUS_WARNING,
    PLAN_STATUS_AUDIO_READY,
    PLAN_STATUS_READY_FOR_CUT,
    READINESS_SEVERITY_BLOCKER,
    READINESS_SEVERITY_WARNING,
)
from otio_app.models import Project
from otio_app.project_layout import get_cut_plan_validation_report_path
from otio_app.services.media_utils import probe_duration_seconds
from otio_app.services.voiceover_generation.cut_plan_asset_selector import settings_from_snapshot
from otio_app.services.voiceover_generation.cut_plan_models import (
    CutPlanDocument,
    CutPlanItem,
    CutPlanValidationError,
    CutPlanValidationReport,
    VisualSegment,
)
from otio_app.services.voiceover_generation.llm_trace_service import content_hash, content_hash_of_model
from otio_app.services.voiceover_generation.final_plan_service import load_confirmed_voiceover_project_plan

__all__ = [
    "validate_cut_plan",
    "validate_cut_plan_draft",
    "save_cut_plan_validation_report",
    "load_cut_plan_validation_report",
    "classify_cut_plan_status",
    "attach_validation_to_cut_plan",
    "content_hash_of_cut_plan_content",
    "validate_source_plan_readiness",
    "validate_audio_items",
    "validate_cut_items",
    "validate_visual_segments",
    "validate_asset_usage",
    "validate_timeline_continuity",
    "validate_no_black_gap_during_voiceover",
    "validate_frame_rounding",
    "group_cut_plan_errors_by_type",
]

_DURATION_EPSILON = 0.01
_TIME_TOLERANCE = 0.05  # Sekunden Toleranz für Pausen-/Offset-Vergleiche

# Blocker-Typen, die durch Nutzeraktion oder spätere LLM-/Supplement-
# Konfliktlösung lösbar sind -> CutPlanDocument.status = NEEDS_REVIEW.
# Alles andere (interne Timeline-/Builder-Inkonsistenzen) -> BLOCKED.
#
# Hinweis zur Auflösung eines Zielkonflikts in der Spezifikation: §5 verlangt
# für die Asset-bezogenen Typen (INVALID_ASSET_ID, MISSING_ASSET_MAPPING,
# ASSET_FILE_MISSING, ASSET_TOO_SHORT, MAX_ASSET_USAGE_EXCEEDED,
# ASSET_REUSE_DISTANCE_TOO_SHORT) must_be_fixed_by="python" +
# is_retryable_by_llm=True, während §3 dieselben Typen explizit als
# NEEDS_REVIEW-Beispiele auflistet. Diese Liste (aus §3) entscheidet über die
# CutPlanDocument-Statusmaschine; must_be_fixed_by/is_retryable_by_llm bleiben
# als separate, informative Metadaten pro Fehler erhalten (§5).
_NEEDS_REVIEW_BLOCKER_TYPES = frozenset(
    {
        CUT_PLAN_ERROR_SOURCE_PLAN_NOT_READY,
        CUT_PLAN_ERROR_MISSING_AUDIO,
        CUT_PLAN_ERROR_MISSING_ALIGNMENT,
        CUT_PLAN_ERROR_INVALID_AUDIO_PATH,
        CUT_PLAN_ERROR_INVALID_ASSET_ID,
        CUT_PLAN_ERROR_MISSING_ASSET_MAPPING,
        CUT_PLAN_ERROR_ASSET_FILE_MISSING,
        CUT_PLAN_ERROR_ASSET_TOO_SHORT,
        CUT_PLAN_ERROR_MAX_ASSET_USAGE_EXCEEDED,
        CUT_PLAN_ERROR_ASSET_REUSE_DISTANCE_TOO_SHORT,
        CUT_PLAN_ERROR_SUPPLEMENT_REQUIRED,
    }
)

# (severity, must_be_fixed_by, is_retryable_by_llm) je Fehlertyp — §5.
_DEFAULT_CLASSIFICATION: dict[str, tuple[str, str, bool]] = {
    CUT_PLAN_ERROR_SOURCE_PLAN_NOT_READY: (READINESS_SEVERITY_BLOCKER, CUT_PLAN_FIX_BY_USER, False),
    CUT_PLAN_ERROR_MISSING_AUDIO: (READINESS_SEVERITY_BLOCKER, CUT_PLAN_FIX_BY_USER, False),
    CUT_PLAN_ERROR_MISSING_ALIGNMENT: (READINESS_SEVERITY_BLOCKER, CUT_PLAN_FIX_BY_USER, False),
    CUT_PLAN_ERROR_INVALID_AUDIO_PATH: (READINESS_SEVERITY_BLOCKER, CUT_PLAN_FIX_BY_USER, False),
    CUT_PLAN_ERROR_SUPPLEMENT_REQUIRED: (READINESS_SEVERITY_BLOCKER, CUT_PLAN_FIX_BY_USER, False),
    CUT_PLAN_ERROR_SOURCE_RANGE_INVALID: (READINESS_SEVERITY_BLOCKER, CUT_PLAN_FIX_BY_PYTHON, False),
    CUT_PLAN_ERROR_BLACK_GAP_DURING_VOICEOVER: (READINESS_SEVERITY_BLOCKER, CUT_PLAN_FIX_BY_PYTHON, False),
    CUT_PLAN_ERROR_TIMELINE_OVERLAP: (READINESS_SEVERITY_BLOCKER, CUT_PLAN_FIX_BY_PYTHON, False),
    CUT_PLAN_ERROR_INVALID_ASSET_ID: (READINESS_SEVERITY_WARNING, CUT_PLAN_FIX_BY_PYTHON, True),
    CUT_PLAN_ERROR_MISSING_ASSET_MAPPING: (READINESS_SEVERITY_BLOCKER, CUT_PLAN_FIX_BY_PYTHON, True),
    CUT_PLAN_ERROR_ASSET_FILE_MISSING: (READINESS_SEVERITY_BLOCKER, CUT_PLAN_FIX_BY_PYTHON, True),
    CUT_PLAN_ERROR_ASSET_TOO_SHORT: (READINESS_SEVERITY_BLOCKER, CUT_PLAN_FIX_BY_PYTHON, True),
    CUT_PLAN_ERROR_MAX_ASSET_USAGE_EXCEEDED: (READINESS_SEVERITY_BLOCKER, CUT_PLAN_FIX_BY_PYTHON, True),
    CUT_PLAN_ERROR_ASSET_REUSE_DISTANCE_TOO_SHORT: (READINESS_SEVERITY_BLOCKER, CUT_PLAN_FIX_BY_PYTHON, True),
    CUT_PLAN_ERROR_SHOT_TOO_SHORT: (READINESS_SEVERITY_WARNING, CUT_PLAN_FIX_BY_PYTHON, False),
    CUT_PLAN_ERROR_SHOT_TOO_LONG: (READINESS_SEVERITY_WARNING, CUT_PLAN_FIX_BY_PYTHON, False),
    CUT_PLAN_ERROR_SUPPLEMENT_REASON_MISSING: (READINESS_SEVERITY_WARNING, CUT_PLAN_FIX_BY_USER, False),
    CUT_PLAN_ERROR_AUDIO_GAP_UNEXPECTED: (READINESS_SEVERITY_WARNING, CUT_PLAN_FIX_BY_PYTHON, False),
    CUT_PLAN_ERROR_FRAME_ROUNDING_ERROR: (READINESS_SEVERITY_WARNING, CUT_PLAN_FIX_BY_PYTHON, False),
    CUT_PLAN_ERROR_AMBIGUOUS_ASSET_ID: (READINESS_SEVERITY_WARNING, CUT_PLAN_FIX_BY_USER, False),
}

_CONTINUATION_REASONS = frozenset({"split_long_sentence_continuation", "merged_short_sentence"})


def _make_error(
    error_type: str,
    *,
    scope: str,
    cut_item_id: str = "",
    folder_name: str = "",
    message: str = "",
    fix_hint: str = "",
    severity_override: str | None = None,
    must_be_fixed_by_override: str | None = None,
    is_retryable_override: bool | None = None,
) -> CutPlanValidationError:
    default_severity, default_fix_by, default_retryable = _DEFAULT_CLASSIFICATION.get(
        error_type, (READINESS_SEVERITY_WARNING, CUT_PLAN_FIX_BY_PYTHON, False)
    )
    return CutPlanValidationError(
        type=error_type,
        severity=severity_override or default_severity,
        scope=scope,
        cut_item_id=cut_item_id,
        folder_name=folder_name,
        message=message,
        fix_hint=fix_hint,
        is_retryable_by_llm=default_retryable if is_retryable_override is None else is_retryable_override,
        must_be_fixed_by=must_be_fixed_by_override or default_fix_by,
    )


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _dedupe_errors(errors: list[CutPlanValidationError]) -> list[CutPlanValidationError]:
    """Phase 8.5 §7: Zwei Fehler gelten als identisch, wenn type, severity,
    scope, cut_item_id, folder_name UND message übereinstimmen — reduziert
    nur doppelte Anzeige, ändert nicht die Semantik (z. B. bleiben eine
    WARNING und ein BLOCKER desselben Typs für dasselbe Item bewusst
    getrennt, da severity abweicht)."""
    seen: set[tuple[str, str, str, str, str, str]] = set()
    result: list[CutPlanValidationError] = []
    for error in errors:
        key = (error.type, error.severity, error.scope, error.cut_item_id, error.folder_name, error.message)
        if key in seen:
            continue
        seen.add(key)
        result.append(error)
    return result


def _reason_has_marker(reason: str, marker: str) -> bool:
    """VisualSegment.reason kann mehrere '+'-getrennte Marker enthalten,
    wenn ein Segment sowohl den initialen Vorlauf als auch eine Sektions-
    Pause abdeckt (siehe cut_plan_visual_coverage._combine_reason,
    Phase 8.5)."""
    return marker in reason.split("+")


def group_cut_plan_errors_by_type(errors: list[CutPlanValidationError]) -> list[dict[str, object]]:
    """Phase D (Nutzervorgabe): fasst eine — potenziell sehr lange, siehe
    Cut-Plan-Diagnose mit hunderten bis tausenden Einzelmeldungen bei vielen
    offenen Items — Fehlerliste nach `type` zusammen: Anzahl, grobe
    Root-Cause-Kategorie (CUT_PLAN_ERROR_CATEGORY_LABELS) und eine
    Beispielmeldung, absteigend nach Häufigkeit sortiert. Reine,
    UI-unabhängige Funktion — die UI (cut_plan_tab.py) rendert das Ergebnis
    nur noch als kompakte Tabelle statt jede Einzelmeldung als eigene Zeile
    (st.error/st.warning) anzuzeigen. Erwartet EINE Liste einer einzigen
    Severity (Aufrufer übergibt blockers/warnings getrennt) — severity ist
    deshalb bewusst keine eigene Spalte im Ergebnis."""
    counts: dict[str, int] = {}
    first_seen_order: list[str] = []
    example_message_by_type: dict[str, str] = {}
    for error in errors:
        if error.type not in counts:
            first_seen_order.append(error.type)
            example_message_by_type[error.type] = error.message
        counts[error.type] = counts.get(error.type, 0) + 1

    ordered_types = sorted(first_seen_order, key=lambda error_type: (-counts[error_type], error_type))
    return [
        {
            "type": error_type,
            "category": CUT_PLAN_ERROR_CATEGORY_LABELS.get(error_type, CUT_PLAN_ERROR_CATEGORY_OTHER),
            "count": counts[error_type],
            "example_message": example_message_by_type[error_type],
        }
        for error_type in ordered_types
    ]


def content_hash_of_cut_plan_content(cut_plan: CutPlanDocument) -> str:
    """Hash NUR des redaktionellen/technischen Cut-Plan-Inhalts (Items,
    VisualSegments, Audio-Platzierung, Settings-Snapshot) — schließt die
    Validierungsergebnisse SELBST aus (status, warnings, blockers auf
    Dokument- UND Item-Ebene, generated_at, confirmed_at).

    Ohne diesen Ausschluss würde ein frisch validierter Cut Plan sofort als
    „seit der Validierung geändert“ gelten: attach_validation_to_cut_plan
    schreibt genau diese Felder (status/warnings/blockers), wodurch ein
    naiver Hash-Vergleich (content_hash_of_model) IMMER einen Unterschied
    zwischen dem beim Validieren berechneten Hash und dem Hash des
    anschließend gespeicherten Drafts gefunden hätte — das hätte Confirm
    (Phase 8.7) permanent blockiert. Diese Funktion ist daher die einzige
    Quelle für CutPlanValidationReport.cut_plan_hash UND für jeden späteren
    Staleness-Vergleich (UI, can_confirm_cut_plan), damit beide Seiten
    konsistent denselben Hash berechnen."""
    payload = cut_plan.model_dump(
        mode="json",
        exclude={
            "status": True,
            "warnings": True,
            "blockers": True,
            "generated_at": True,
            "confirmed_at": True,
            "items": {"__all__": {"warnings", "blockers"}},
        },
    )
    return content_hash(json.dumps(payload, sort_keys=True, ensure_ascii=False))


def _cached_probe_duration(asset_path: str, cache: dict[str, float | None]) -> float | None:
    """Phase 8.5 §8: ein kleiner In-Memory-Cache pro Validierungslauf, damit
    dieselbe Videodatei nicht mehrfach per ffprobe abgefragt wird. Keine
    Persistenz, keine Architekturänderung."""
    if asset_path not in cache:
        cache[asset_path] = probe_duration_seconds(Path(asset_path))
    return cache[asset_path]


# --- 1. Source Plan Readiness (§6) ---


def validate_source_plan_readiness(
    project: Project, cut_plan: CutPlanDocument
) -> tuple[list[CutPlanValidationError], list[CutPlanValidationError]]:
    warnings: list[CutPlanValidationError] = []
    blockers: list[CutPlanValidationError] = []

    if not cut_plan.source_plan_path or not Path(cut_plan.source_plan_path).is_file():
        blockers.append(
            _make_error(
                CUT_PLAN_ERROR_SOURCE_PLAN_NOT_READY,
                scope="project",
                message="source_plan_path fehlt oder die Datei existiert nicht mehr.",
                fix_hint="Im Tab „⑦ Final Output“ einen finalen Plan erstellen.",
            )
        )
        return warnings, blockers

    current_source_plan = load_confirmed_voiceover_project_plan(project)
    if current_source_plan is None:
        blockers.append(
            _make_error(
                CUT_PLAN_ERROR_SOURCE_PLAN_NOT_READY,
                scope="project",
                message="confirmed_voiceover_project_plan.json existiert nicht mehr.",
                fix_hint="Im Tab „⑦ Final Output“ einen finalen Plan erstellen.",
            )
        )
        return warnings, blockers

    current_hash = content_hash_of_model(current_source_plan)
    if cut_plan.source_plan_hash != current_hash:
        blockers.append(
            _make_error(
                CUT_PLAN_ERROR_SOURCE_PLAN_NOT_READY,
                scope="project",
                message="Der bestätigte Voice-over-Projektplan hat sich seit der Cut-Plan-Erzeugung "
                "geändert — der Cut Plan ist nicht mehr verlässlich.",
                fix_hint="Cut Plan Draft neu erzeugen.",
            )
        )

    # AUDIO_READY_WITH_WARNINGS ist für Draft/Validation okay, READY_FOR_CUT später nicht (Hinweis §6).
    if current_source_plan.status not in (PLAN_STATUS_AUDIO_READY, PLAN_STATUS_READY_FOR_CUT):
        blockers.append(
            _make_error(
                CUT_PLAN_ERROR_SOURCE_PLAN_NOT_READY,
                scope="project",
                message=f"Voice-over-Projektplan-Status '{current_source_plan.status}' ist nicht "
                "mindestens AUDIO_READY.",
                fix_hint="Im Tab „⑦ Final Output“ Voice-overs vertonen/vervollständigen.",
            )
        )

    return warnings, blockers


# --- 2. Audio Items (§7) ---


def validate_audio_items(
    project: Project, cut_plan: CutPlanDocument
) -> tuple[list[CutPlanValidationError], list[CutPlanValidationError]]:
    warnings: list[CutPlanValidationError] = []
    blockers: list[CutPlanValidationError] = []

    for audio_item in cut_plan.audio_items:
        label = audio_item.folder_name or "intro"
        if not audio_item.audio_path:
            blockers.append(
                _make_error(CUT_PLAN_ERROR_MISSING_AUDIO, scope="audio", folder_name=audio_item.folder_name,
                            message=f"AudioItem '{label}': audio_path ist leer.")
            )
            continue
        if not Path(audio_item.audio_path).is_file():
            blockers.append(
                _make_error(CUT_PLAN_ERROR_INVALID_AUDIO_PATH, scope="audio", folder_name=audio_item.folder_name,
                            message=f"AudioItem '{label}': Datei '{audio_item.audio_path}' existiert nicht.")
            )
        if audio_item.duration_sec <= 0:
            blockers.append(
                _make_error(CUT_PLAN_ERROR_SOURCE_RANGE_INVALID, scope="audio", folder_name=audio_item.folder_name,
                            message=f"AudioItem '{label}': duration_sec <= 0.")
            )
        if audio_item.timeline_start_sec < 0:
            blockers.append(
                _make_error(CUT_PLAN_ERROR_SOURCE_RANGE_INVALID, scope="audio", folder_name=audio_item.folder_name,
                            message=f"AudioItem '{label}': timeline_start_sec < 0.")
            )
        if audio_item.timeline_end_sec <= audio_item.timeline_start_sec:
            blockers.append(
                _make_error(CUT_PLAN_ERROR_SOURCE_RANGE_INVALID, scope="audio", folder_name=audio_item.folder_name,
                            message=f"AudioItem '{label}': timeline_end_sec <= timeline_start_sec.")
            )
        if abs(audio_item.source_in_sec) > _DURATION_EPSILON:
            blockers.append(
                _make_error(CUT_PLAN_ERROR_SOURCE_RANGE_INVALID, scope="audio", folder_name=audio_item.folder_name,
                            message=f"AudioItem '{label}': source_in_sec != 0.0.")
            )
        if audio_item.track != "A1":
            blockers.append(
                _make_error(CUT_PLAN_ERROR_SOURCE_RANGE_INVALID, scope="audio", folder_name=audio_item.folder_name,
                            message=f"AudioItem '{label}': track != 'A1' ({audio_item.track}).")
            )

    return warnings, blockers


# --- 3. CutPlanItem (§8) ---


def validate_cut_items(
    project: Project, cut_plan: CutPlanDocument
) -> tuple[list[CutPlanValidationError], list[CutPlanValidationError]]:
    warnings: list[CutPlanValidationError] = []
    blockers: list[CutPlanValidationError] = []

    for item in cut_plan.items:
        scope = "sentence" if item.source_scope == AUDIO_SCOPE_FOLDER else "intro"
        cid, folder = item.cut_item_id, item.folder_name

        if not item.cut_item_id:
            blockers.append(_make_error(CUT_PLAN_ERROR_SOURCE_RANGE_INVALID, scope=scope, message="cut_item_id ist leer."))
        if not item.source_refs:
            blockers.append(_make_error(CUT_PLAN_ERROR_SOURCE_RANGE_INVALID, scope=scope, cut_item_id=cid,
                                         folder_name=folder, message="source_refs ist leer."))
        if not item.text.strip():
            blockers.append(_make_error(CUT_PLAN_ERROR_SOURCE_RANGE_INVALID, scope=scope, cut_item_id=cid,
                                         folder_name=folder, message="text ist leer."))
        if item.timeline_start_sec < 0:
            blockers.append(_make_error(CUT_PLAN_ERROR_SOURCE_RANGE_INVALID, scope=scope, cut_item_id=cid,
                                         folder_name=folder, message="timeline_start_sec < 0."))
        if item.timeline_end_sec <= item.timeline_start_sec:
            blockers.append(_make_error(CUT_PLAN_ERROR_SOURCE_RANGE_INVALID, scope=scope, cut_item_id=cid,
                                         folder_name=folder, message="timeline_end_sec <= timeline_start_sec."))
        if item.duration_sec <= 0:
            blockers.append(_make_error(CUT_PLAN_ERROR_SOURCE_RANGE_INVALID, scope=scope, cut_item_id=cid,
                                         folder_name=folder, message="duration_sec <= 0."))
        expected_duration = item.timeline_end_sec - item.timeline_start_sec
        if abs(item.duration_sec - expected_duration) > _TIME_TOLERANCE:
            blockers.append(_make_error(CUT_PLAN_ERROR_SOURCE_RANGE_INVALID, scope=scope, cut_item_id=cid,
                                         folder_name=folder,
                                         message="duration_sec stimmt nicht mit timeline_end - timeline_start überein."))
        if item.audio_start_sec < 0:
            blockers.append(_make_error(CUT_PLAN_ERROR_SOURCE_RANGE_INVALID, scope=scope, cut_item_id=cid,
                                         folder_name=folder, message="audio_start_sec < 0."))
        if item.audio_end_sec <= item.audio_start_sec:
            blockers.append(_make_error(CUT_PLAN_ERROR_SOURCE_RANGE_INVALID, scope=scope, cut_item_id=cid,
                                         folder_name=folder, message="audio_end_sec <= audio_start_sec."))

        if not item.asset_selection_status:
            blockers.append(_make_error(CUT_PLAN_ERROR_MISSING_ASSET_MAPPING, scope=scope, cut_item_id=cid,
                                         folder_name=folder, message="asset_selection_status ist leer.",
                                         fix_hint="Asset-Auswahl anwenden."))
        elif item.asset_selection_status == CUT_PLAN_ASSET_SELECTION_UNRESOLVED:
            blockers.append(_make_error(CUT_PLAN_ERROR_MISSING_ASSET_MAPPING, scope=scope, cut_item_id=cid,
                                         folder_name=folder, message="asset_selection_status ist UNRESOLVED.",
                                         fix_hint="Asset-Auswahl anwenden."))
        elif item.asset_selection_status == CUT_PLAN_ASSET_SELECTION_SUPPLEMENT_REQUIRED:
            blockers.append(_make_error(CUT_PLAN_ERROR_SUPPLEMENT_REQUIRED, scope=scope, cut_item_id=cid,
                                         folder_name=folder, message="Kein passendes Asset — Supplement nötig.",
                                         fix_hint="Supplement-Beschaffung folgt in Phase 8.5."))

        if not item.chosen_asset_id and not item.needs_supplement_asset:
            blockers.append(_make_error(CUT_PLAN_ERROR_MISSING_ASSET_MAPPING, scope=scope, cut_item_id=cid,
                                         folder_name=folder,
                                         message="chosen_asset_id ist leer und needs_supplement_asset=false."))
        if item.needs_supplement_asset and not item.supplement_reason.strip():
            warnings.append(_make_error(CUT_PLAN_ERROR_SUPPLEMENT_REASON_MISSING, scope=scope, cut_item_id=cid,
                                         folder_name=folder,
                                         message="needs_supplement_asset ist gesetzt, aber supplement_reason fehlt."))

        # Aus Phase 8.3 übernommene Item-Warnungen/-Blocker mit übernehmen (kein
        # stillschweigendes Verwerfen), aber dedupliziert gegen bereits oben
        # erzeugte Einträge desselben Typs für dieses Item.
        already_warned = {error.type for error in warnings if error.cut_item_id == cid}
        already_blocked = {error.type for error in blockers if error.cut_item_id == cid}
        for warning_type in item.warnings:
            if warning_type in already_warned:
                continue
            warnings.append(_make_error(warning_type, scope=scope, cut_item_id=cid, folder_name=folder,
                                         message=f"{cid}: {warning_type} (aus Asset-Auswahl übernommen)."))
            already_warned.add(warning_type)
        for blocker_type in item.blockers:
            if blocker_type in already_blocked:
                continue
            blockers.append(_make_error(blocker_type, scope=scope, cut_item_id=cid, folder_name=folder,
                                         message=f"{cid}: {blocker_type} (aus Asset-Auswahl übernommen)."))
            already_blocked.add(blocker_type)

    return warnings, blockers


# --- 4. VisualSegment (§9) + Shot Duration (§12) ---


def validate_visual_segments(
    project: Project,
    cut_plan: CutPlanDocument,
    *,
    duration_cache: dict[str, float | None] | None = None,
) -> tuple[list[CutPlanValidationError], list[CutPlanValidationError]]:
    """duration_cache (Phase 8.5 §8): optionaler In-Memory-Cache für
    probe_duration_seconds, damit dieselbe Videodatei innerhalb eines
    Validierungslaufs nicht mehrfach per ffprobe abgefragt wird. Wird nicht
    übergeben, wird ein frischer (leerer) Cache nur für diesen Aufruf
    verwendet — kein Verhaltensunterschied nach außen, nur Performance."""
    warnings: list[CutPlanValidationError] = []
    blockers: list[CutPlanValidationError] = []
    settings = settings_from_snapshot(project, cut_plan)
    duration_cache = {} if duration_cache is None else duration_cache

    for item in cut_plan.items:
        scope = "sentence" if item.source_scope == AUDIO_SCOPE_FOLDER else "intro"
        cid, folder = item.cut_item_id, item.folder_name

        for segment in item.planned_visual_segments:
            sid = segment.segment_id
            if not sid:
                blockers.append(_make_error(CUT_PLAN_ERROR_SOURCE_RANGE_INVALID, scope=scope, cut_item_id=cid,
                                             folder_name=folder, message="VisualSegment: segment_id ist leer."))
            if not segment.asset_id:
                blockers.append(_make_error(CUT_PLAN_ERROR_MISSING_ASSET_MAPPING, scope=scope, cut_item_id=cid,
                                             folder_name=folder, message=f"{sid}: asset_id ist leer."))
            if not segment.asset_path:
                blockers.append(_make_error(CUT_PLAN_ERROR_ASSET_FILE_MISSING, scope=scope, cut_item_id=cid,
                                             folder_name=folder, message=f"{sid}: asset_path ist leer."))
            elif not Path(segment.asset_path).is_file():
                blockers.append(_make_error(CUT_PLAN_ERROR_ASSET_FILE_MISSING, scope=scope, cut_item_id=cid,
                                             folder_name=folder,
                                             message=f"{sid}: Datei '{segment.asset_path}' existiert nicht."))
            if segment.asset_type not in ("video", "image"):
                blockers.append(_make_error(CUT_PLAN_ERROR_SOURCE_RANGE_INVALID, scope=scope, cut_item_id=cid,
                                             folder_name=folder,
                                             message=f"{sid}: asset_type unbekannt ('{segment.asset_type}')."))
            if segment.timeline_in_sec < 0:
                blockers.append(_make_error(CUT_PLAN_ERROR_SOURCE_RANGE_INVALID, scope=scope, cut_item_id=cid,
                                             folder_name=folder, message=f"{sid}: timeline_in_sec < 0."))
            if segment.timeline_out_sec <= segment.timeline_in_sec:
                blockers.append(_make_error(CUT_PLAN_ERROR_SOURCE_RANGE_INVALID, scope=scope, cut_item_id=cid,
                                             folder_name=folder,
                                             message=f"{sid}: timeline_out_sec <= timeline_in_sec."))
            if segment.duration_sec <= 0:
                blockers.append(_make_error(CUT_PLAN_ERROR_SOURCE_RANGE_INVALID, scope=scope, cut_item_id=cid,
                                             folder_name=folder, message=f"{sid}: duration_sec <= 0."))
            expected = segment.timeline_out_sec - segment.timeline_in_sec
            if abs(segment.duration_sec - expected) > _TIME_TOLERANCE:
                blockers.append(_make_error(CUT_PLAN_ERROR_SOURCE_RANGE_INVALID, scope=scope, cut_item_id=cid,
                                             folder_name=folder,
                                             message=f"{sid}: duration_sec stimmt nicht mit timeline überein."))
            if segment.source_in_sec < 0:
                blockers.append(_make_error(CUT_PLAN_ERROR_SOURCE_RANGE_INVALID, scope=scope, cut_item_id=cid,
                                             folder_name=folder, message=f"{sid}: source_in_sec < 0."))
            if segment.source_out_sec <= segment.source_in_sec:
                blockers.append(_make_error(CUT_PLAN_ERROR_SOURCE_RANGE_INVALID, scope=scope, cut_item_id=cid,
                                             folder_name=folder, message=f"{sid}: source_out_sec <= source_in_sec."))
            if segment.track != "V1":
                blockers.append(_make_error(CUT_PLAN_ERROR_SOURCE_RANGE_INVALID, scope=scope, cut_item_id=cid,
                                             folder_name=folder, message=f"{sid}: track != 'V1' ({segment.track})."))

            asset_path = Path(segment.asset_path) if segment.asset_path else None
            if segment.asset_type == "video" and asset_path is not None and asset_path.is_file():
                real_duration = _cached_probe_duration(segment.asset_path, duration_cache)
                if real_duration is not None and segment.source_out_sec > real_duration + _TIME_TOLERANCE:
                    blockers.append(_make_error(CUT_PLAN_ERROR_ASSET_TOO_SHORT, scope=scope, cut_item_id=cid,
                                                 folder_name=folder,
                                                 message=f"{sid}: source_out_sec ({segment.source_out_sec:.2f}s) "
                                                 f"> Video-Dauer ({real_duration:.2f}s)."))
            if segment.asset_type == "image" and abs(segment.source_in_sec) > _DURATION_EPSILON:
                blockers.append(_make_error(CUT_PLAN_ERROR_SOURCE_RANGE_INVALID, scope=scope, cut_item_id=cid,
                                             folder_name=folder, message=f"{sid}: Bild mit source_in_sec != 0.0."))

            # Shot-Dauer (§12) — Merge-/Split-Fortsetzungen UND die Phase-8.5-
            # Coverage-Erweiterungen (initial_preroll_extension,
            # section_pause_hold) sind ausdrücklich erlaubt/legitim. Ein
            # Segment kann mehrere '+'-getrennte Marker tragen (siehe
            # _reason_has_marker), z. B. wenn dasselbe Segment sowohl den
            # Vorlauf als auch eine anschließende Pause abdeckt.
            if segment.duration_sec < settings.shot_min_sec - _DURATION_EPSILON:
                if _reason_has_marker(segment.reason, "merged_short_sentence"):
                    pass  # Merge ist erlaubt (§12)
                elif segment.duration_sec < 1.0:
                    blockers.append(_make_error(CUT_PLAN_ERROR_SHOT_TOO_SHORT, scope=scope, cut_item_id=cid,
                                                 folder_name=folder,
                                                 message=f"{sid}: duration_sec ({segment.duration_sec:.2f}s) < 1.0s."))
                else:
                    warnings.append(_make_error(CUT_PLAN_ERROR_SHOT_TOO_SHORT, scope=scope, cut_item_id=cid,
                                                 folder_name=folder,
                                                 message=f"{sid}: duration_sec ({segment.duration_sec:.2f}s) < "
                                                 f"shot_min_sec ({settings.shot_min_sec}s)."))
            if segment.duration_sec > settings.shot_max_sec + _DURATION_EPSILON:
                if any(
                    _reason_has_marker(segment.reason, marker)
                    for marker in (
                        "split_long_sentence",
                        "split_long_sentence_continuation",
                        "initial_preroll_extension",
                        "section_pause_hold",
                    )
                ):
                    warnings.append(_make_error(CUT_PLAN_ERROR_SHOT_TOO_LONG, scope=scope, cut_item_id=cid,
                                                 folder_name=folder,
                                                 message=f"{sid}: duration_sec > shot_max_sec, aber als Split/"
                                                 "Coverage-Erweiterung markiert."))
                elif item.duration_strategy == CUT_PLAN_DURATION_STRATEGY_SPLIT:
                    warnings.append(_make_error(CUT_PLAN_ERROR_SHOT_TOO_LONG, scope=scope, cut_item_id=cid,
                                                 folder_name=folder,
                                                 message=f"{sid}: duration_sec > shot_max_sec (Split-Item)."))
                else:
                    blockers.append(_make_error(CUT_PLAN_ERROR_SHOT_TOO_LONG, scope=scope, cut_item_id=cid,
                                                 folder_name=folder,
                                                 message=f"{sid}: duration_sec ({segment.duration_sec:.2f}s) > "
                                                 f"shot_max_sec ({settings.shot_max_sec}s) ohne Split-Strategie."))

    return warnings, blockers


# --- 5. Asset Usage (§13) ---


def validate_asset_usage(
    project: Project, cut_plan: CutPlanDocument
) -> tuple[list[CutPlanValidationError], list[CutPlanValidationError]]:
    warnings: list[CutPlanValidationError] = []
    blockers: list[CutPlanValidationError] = []
    settings = settings_from_snapshot(project, cut_plan)

    all_segments: list[tuple[VisualSegment, CutPlanItem]] = [
        (segment, item) for item in cut_plan.items for segment in item.planned_visual_segments
    ]
    all_segments.sort(key=lambda pair: pair[0].timeline_in_sec)

    counts: dict[str, int] = {}
    last_index: dict[str, int] = {}
    recomputed_summary: dict[str, int] = {}

    for index, (segment, item) in enumerate(all_segments):
        if not segment.asset_id:
            continue
        recomputed_summary[segment.asset_id] = recomputed_summary.get(segment.asset_id, 0) + 1
        is_continuation = any(_reason_has_marker(segment.reason, marker) for marker in _CONTINUATION_REASONS)
        scope = "sentence" if item.source_scope == AUDIO_SCOPE_FOLDER else "intro"

        if not is_continuation:
            counts[segment.asset_id] = counts.get(segment.asset_id, 0) + 1
            if counts[segment.asset_id] > settings.max_asset_usage:
                blockers.append(
                    _make_error(CUT_PLAN_ERROR_MAX_ASSET_USAGE_EXCEEDED, scope=scope, cut_item_id=item.cut_item_id,
                                folder_name=item.folder_name,
                                message=f"Asset '{segment.asset_id}' wird {counts[segment.asset_id]}x verwendet "
                                f"(max_asset_usage={settings.max_asset_usage}).")
                )
            if segment.asset_id in last_index:
                distance = index - last_index[segment.asset_id]
                min_required = max(1, settings.min_asset_reuse_distance_shots)
                if distance <= min_required:
                    blockers.append(
                        _make_error(CUT_PLAN_ERROR_ASSET_REUSE_DISTANCE_TOO_SHORT, scope=scope,
                                    cut_item_id=item.cut_item_id, folder_name=item.folder_name,
                                    message=f"Asset '{segment.asset_id}' zu früh wiederverwendet "
                                    f"(Abstand {distance}, min. {min_required}).")
                    )
            last_index[segment.asset_id] = index
        else:
            last_index[segment.asset_id] = index

    if recomputed_summary != cut_plan.asset_usage_summary:
        warnings.append(
            _make_error(
                "ASSET_USAGE_SUMMARY_MISMATCH",
                scope="project",
                message="asset_usage_summary mismatch — die gespeicherte Zusammenfassung stimmt nicht mit den "
                "tatsächlich platzierten VisualSegments überein.",
                fix_hint="Asset-Auswahl erneut anwenden.",
                severity_override=READINESS_SEVERITY_WARNING,
            )
        )

    return warnings, blockers


# --- 6. Timeline Continuity (§10) ---


def validate_timeline_continuity(
    project: Project, cut_plan: CutPlanDocument
) -> tuple[list[CutPlanValidationError], list[CutPlanValidationError]]:
    warnings: list[CutPlanValidationError] = []
    blockers: list[CutPlanValidationError] = []
    settings = settings_from_snapshot(project, cut_plan)

    audio_items = sorted(cut_plan.audio_items, key=lambda item: item.timeline_start_sec)
    for i in range(len(audio_items) - 1):
        current, nxt = audio_items[i], audio_items[i + 1]
        if nxt.timeline_start_sec < current.timeline_end_sec - _DURATION_EPSILON:
            blockers.append(
                _make_error(CUT_PLAN_ERROR_TIMELINE_OVERLAP, scope="audio",
                            message=f"AudioItem '{current.folder_name or 'intro'}' überlappt mit "
                            f"'{nxt.folder_name or 'intro'}'.")
            )
            continue
        gap = nxt.timeline_start_sec - current.timeline_end_sec
        if abs(gap - settings.pause_between_sections_sec) > _TIME_TOLERANCE:
            warnings.append(
                _make_error(CUT_PLAN_ERROR_AUDIO_GAP_UNEXPECTED, scope="audio",
                            message=f"Pause zwischen '{current.folder_name or 'intro'}' und "
                            f"'{nxt.folder_name or 'intro'}' ist {gap:.2f}s statt erwarteter "
                            f"{settings.pause_between_sections_sec:.2f}s.")
            )

    if audio_items:
        first = audio_items[0]
        if abs(first.timeline_start_sec - settings.initial_audio_offset_sec) > _TIME_TOLERANCE:
            warnings.append(
                _make_error(CUT_PLAN_ERROR_AUDIO_GAP_UNEXPECTED, scope="audio",
                            message=f"Erstes AudioItem startet bei {first.timeline_start_sec:.2f}s statt "
                            f"initial_audio_offset_sec ({settings.initial_audio_offset_sec:.2f}s).")
            )

    all_segments = sorted(
        (segment for item in cut_plan.items for segment in item.planned_visual_segments),
        key=lambda segment: segment.timeline_in_sec,
    )
    for i in range(len(all_segments) - 1):
        current, nxt = all_segments[i], all_segments[i + 1]
        if nxt.timeline_in_sec < current.timeline_out_sec - _DURATION_EPSILON:
            blockers.append(
                _make_error(CUT_PLAN_ERROR_TIMELINE_OVERLAP, scope="timeline",
                            message=f"VisualSegment '{current.segment_id}' überlappt mit '{nxt.segment_id}'.")
            )

    return warnings, blockers


# --- 7. Kein Schwarzbild während Voice-over (§11) ---


def _merge_intervals(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if not intervals:
        return []
    ordered = sorted(intervals)
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end + _DURATION_EPSILON:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def _is_span_covered(merged_intervals: list[tuple[float, float]], span_start: float, span_end: float) -> bool:
    if span_end <= span_start + _DURATION_EPSILON:
        return True
    return any(
        start <= span_start + _DURATION_EPSILON and end >= span_end - _DURATION_EPSILON
        for start, end in merged_intervals
    )


def validate_no_black_gap_during_voiceover(
    project: Project, cut_plan: CutPlanDocument
) -> tuple[list[CutPlanValidationError], list[CutPlanValidationError]]:
    warnings: list[CutPlanValidationError] = []
    blockers: list[CutPlanValidationError] = []

    all_segments = [
        (segment.timeline_in_sec, segment.timeline_out_sec)
        for item in cut_plan.items
        for segment in item.planned_visual_segments
    ]
    coverage = _merge_intervals(all_segments)
    audio_items = sorted(cut_plan.audio_items, key=lambda item: item.timeline_start_sec)

    if audio_items:
        first_audio = audio_items[0]
        if not _is_span_covered(coverage, 0.0, first_audio.timeline_start_sec):
            blockers.append(
                _make_error(CUT_PLAN_ERROR_BLACK_GAP_DURING_VOICEOVER, scope="timeline",
                            message=f"V1 deckt den Videoanfang (0.0s bis {first_audio.timeline_start_sec:.2f}s) "
                            "nicht durchgehend ab.")
            )

    for audio_item in audio_items:
        label = audio_item.folder_name or "intro"
        if not _is_span_covered(coverage, audio_item.timeline_start_sec, audio_item.timeline_end_sec):
            blockers.append(
                _make_error(CUT_PLAN_ERROR_BLACK_GAP_DURING_VOICEOVER, scope="audio", folder_name=audio_item.folder_name,
                            message=f"Visuelles Loch während aktivem Voice-over '{label}' "
                            f"({audio_item.timeline_start_sec:.2f}s–{audio_item.timeline_end_sec:.2f}s).")
            )

    for i in range(len(audio_items) - 1):
        gap_start = audio_items[i].timeline_end_sec
        gap_end = audio_items[i + 1].timeline_start_sec
        if gap_end > gap_start + _DURATION_EPSILON and not _is_span_covered(coverage, gap_start, gap_end):
            blockers.append(
                _make_error(CUT_PLAN_ERROR_BLACK_GAP_DURING_VOICEOVER, scope="timeline",
                            message=f"Pause zwischen Sektionen ({gap_start:.2f}s–{gap_end:.2f}s) ist visuell "
                            "nicht abgedeckt.")
            )

    return warnings, blockers


# --- 8. Frame Rounding (§14) ---


def validate_frame_rounding(
    project: Project, cut_plan: CutPlanDocument
) -> tuple[list[CutPlanValidationError], list[CutPlanValidationError]]:
    """Nur Warnungen (§14) — echte Frame-Normalisierung kommt erst vor
    EditPlan/OTIO in einer späteren Phase.

    Hinweis zur Formel: `abs(sec - round(sec*fps)/fps) > 0.5/fps` kann durch
    die Rundungsdefinition rechnerisch NIE zutreffen (der Abstand zum
    nächsten Frame ist immer <= 0.5/fps). Diese Funktion verwendet daher —
    analog zur bestehenden Produktions-Validierung in
    `edit_plan_validator.py` — den Bruchteils-Frame-Abstand
    `abs(frames - round(frames))` mit Toleranz 0.02 Frames, was tatsächlich
    zwischen frame-genauen und nicht-frame-genauen Werten unterscheidet."""
    warnings: list[CutPlanValidationError] = []
    blockers: list[CutPlanValidationError] = []

    fps = cut_plan.timeline_fps or 25
    frame_duration = 1.0 / fps

    def _check(value: float, label: str, cut_item_id: str, folder_name: str, scope: str) -> None:
        frames = value / frame_duration
        if abs(frames - round(frames)) > 0.02:
            warnings.append(
                _make_error(CUT_PLAN_ERROR_FRAME_ROUNDING_ERROR, scope=scope, cut_item_id=cut_item_id,
                            folder_name=folder_name,
                            message=f"{label}={value:.4f}s liegt nicht exakt auf einem Frame bei {fps}fps "
                            f"({frames:.3f} Frames).")
            )

    for item in cut_plan.items:
        scope = "sentence" if item.source_scope == AUDIO_SCOPE_FOLDER else "intro"
        _check(item.timeline_start_sec, "timeline_start_sec", item.cut_item_id, item.folder_name, scope)
        _check(item.timeline_end_sec, "timeline_end_sec", item.cut_item_id, item.folder_name, scope)
        for segment in item.planned_visual_segments:
            _check(segment.timeline_in_sec, f"{segment.segment_id}.timeline_in_sec", item.cut_item_id,
                   item.folder_name, scope)
            _check(segment.timeline_out_sec, f"{segment.segment_id}.timeline_out_sec", item.cut_item_id,
                   item.folder_name, scope)
            _check(segment.source_in_sec, f"{segment.segment_id}.source_in_sec", item.cut_item_id,
                   item.folder_name, scope)
            _check(segment.source_out_sec, f"{segment.segment_id}.source_out_sec", item.cut_item_id,
                   item.folder_name, scope)

    return warnings, blockers


# --- Orchestrierung ---


def validate_cut_plan(project: Project, cut_plan: CutPlanDocument) -> CutPlanValidationReport:
    """Führt alle Teil-Validatoren aus und aggregiert das Ergebnis. Reine
    Funktion — speichert nichts (siehe save_cut_plan_validation_report).

    Phase 8.5 §8: ein einziger duration_cache wird über den gesamten Lauf
    geteilt, damit dieselbe Videodatei nicht mehrfach per ffprobe abgefragt
    wird (aktuell nur von validate_visual_segments genutzt)."""
    warnings: list[CutPlanValidationError] = []
    blockers: list[CutPlanValidationError] = []
    duration_cache: dict[str, float | None] = {}

    for validator in (
        validate_source_plan_readiness,
        validate_audio_items,
        validate_cut_items,
        lambda project, cut_plan: validate_visual_segments(project, cut_plan, duration_cache=duration_cache),
        validate_asset_usage,
        validate_timeline_continuity,
        validate_no_black_gap_during_voiceover,
        validate_frame_rounding,
    ):
        item_warnings, item_blockers = validator(project, cut_plan)
        warnings.extend(item_warnings)
        blockers.extend(item_blockers)

    # Phase 8.5 §7: doppelte Fehler (identisch in type/severity/scope/
    # cut_item_id/folder_name/message) reduzieren — reine Anzeige-
    # Bereinigung, keine Semantikänderung.
    warnings = _dedupe_errors(warnings)
    blockers = _dedupe_errors(blockers)

    if blockers:
        report_status = CUT_PLAN_VALIDATION_STATUS_BLOCKED
    elif warnings:
        report_status = CUT_PLAN_VALIDATION_STATUS_WARNING
    else:
        report_status = CUT_PLAN_VALIDATION_STATUS_PASS

    return CutPlanValidationReport(
        project_id=project.id,
        cut_plan_hash=content_hash_of_cut_plan_content(cut_plan),
        status=report_status,
        errors=warnings + blockers,
        warnings=warnings,
        blockers=blockers,
    )


def classify_cut_plan_status(report: CutPlanValidationReport) -> str:
    """Klassifiziert den CutPlanDocument-Status (VALIDATED|NEEDS_REVIEW|
    BLOCKED) aus einem bereits gebauten CutPlanValidationReport. Siehe
    Kommentar bei _NEEDS_REVIEW_BLOCKER_TYPES zur Auflösung des
    Zielkonflikts zwischen §3 und §5 der Spezifikation."""
    if not report.blockers:
        return CUT_PLAN_STATUS_VALIDATED
    if all(blocker.type in _NEEDS_REVIEW_BLOCKER_TYPES for blocker in report.blockers):
        return CUT_PLAN_STATUS_NEEDS_REVIEW
    return CUT_PLAN_STATUS_BLOCKED


def attach_validation_to_cut_plan(cut_plan: CutPlanDocument, report: CutPlanValidationReport) -> CutPlanDocument:
    """Schreibt Validierungsergebnis auf Dokument- UND Item-Ebene fort.
    Reine Funktion — gibt eine aktualisierte Kopie zurück."""
    status = classify_cut_plan_status(report)

    updated_items: list[CutPlanItem] = []
    for item in cut_plan.items:
        item_warning_types = _dedupe([error.type for error in report.warnings if error.cut_item_id == item.cut_item_id])
        item_blocker_types = _dedupe([error.type for error in report.blockers if error.cut_item_id == item.cut_item_id])
        updated_items.append(item.model_copy(update={"warnings": item_warning_types, "blockers": item_blocker_types}))

    return cut_plan.model_copy(
        update={
            "items": updated_items,
            "status": status,
            "warnings": report.warnings,
            "blockers": report.blockers,
        }
    )


def save_cut_plan_validation_report(project: Project, report: CutPlanValidationReport) -> CutPlanValidationReport:
    normalized = report.model_copy(update={"project_id": project.id})
    path = get_cut_plan_validation_report_path(project.work_dir_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(normalized.model_dump_json(indent=2), encoding="utf-8")
    return normalized


def load_cut_plan_validation_report(project: Project) -> CutPlanValidationReport | None:
    path = get_cut_plan_validation_report_path(project.work_dir_path)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return CutPlanValidationReport.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None


def validate_cut_plan_draft(project: Project) -> tuple[CutPlanDocument, CutPlanValidationReport]:
    """Lädt den bestehenden cut_plan.draft.json, validiert ihn und gibt
    (aktualisierter Draft, Report) zurück — speichert NICHTS (siehe
    cut_plan_builder.validate_cut_plan_draft für die speichernde Variante,
    die zusätzlich cut_plan.draft.json und cut_plan.validation_report.json
    schreibt)."""
    from otio_app.services.voiceover_generation.cut_plan_builder import load_cut_plan_draft

    cut_plan = load_cut_plan_draft(project)
    if cut_plan is None:
        raise ValueError("Kein Cut Plan Draft vorhanden — bitte zuerst einen Draft erzeugen.")

    report = validate_cut_plan(project, cut_plan)
    updated_cut_plan = attach_validation_to_cut_plan(cut_plan, report)
    return updated_cut_plan, report
