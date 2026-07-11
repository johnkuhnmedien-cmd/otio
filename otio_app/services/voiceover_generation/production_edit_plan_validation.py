"""Phase 10.3: Production-EditPlan-Staging — vollständige Revalidierung.

Liest AUSSCHLIESSLICH bereits vorhandene Staging-Artefakte
(`production_edit_plan_package.json`, `staged_edit_plans/{id}/edit_plan.json`,
`production_edit_plan_mapping_trace.json`) und erzeugt einen separaten
Prüf-Bericht (`production_edit_plan_validation_report.json`).

Rein prüfend — KEINE automatische Reparatur, KEINE Produktions-Promotion,
KEIN OTIO-Export, KEIN Render, kein Aufruf der Save- oder Build-Funktionen der
bestehenden Produktions-EditPlan-Pipeline. `production_edit_plan_package.json`
wird durch diese Datei NIEMALS verändert — das Staging-Paket bleibt ein
bewusster Build-Snapshot, der Validation Report ist ein separates, rein
additives Prüf-Artefakt.

Wiederverwendung bestehender Produktionsvalidatoren:
- `validate_timeline_items` (rules_doc=None, work_dir_path=None,
  require_rendered_media=False) — garantiert side-effect-frei (siehe
  edit_plan_validator.validate_timeline_items: die einzigen Schreibpfade
  hängen an rules_doc/work_dir_path, die hier bewusst nicht gesetzt werden).
- `validate_voiceover_plan` — reine In-Memory-Prüfung, kein I/O.

Bewusst NICHT verwendet: `validate_shot_duration_rules`,
`validate_asset_usage_rules`, `validate_final_edit_plan`. Shot-Dauer-Regeln
wurden bereits vollständig in der Cut-Plan-Validierung (Phase 8.4) auf Basis
der CutPlan-eigenen Settings geprüft; `validate_asset_usage_rules` benötigt ein
`EditPlanRulesDocument` (Produktions-Konzept aus dem Tab „Regeln“), das für
CutPlan-getriebene Staging-Inhalte nicht existiert. `validate_final_edit_plan`
würde beide Themen zusätzlich hereinziehen.

Bekannte, bewusste Abweichung (siehe Docstring von
`_relaxed_validation_settings`): `EditPlanDocument.settings` eines gestagten
Dokuments ist immer `EditPlanSettings()` (Phase 10.2 befüllt dieses Feld
nicht aus den CutPlan-Settings) — die dortigen With-Voice-over-Pipeline-
Defaults (audio_offset_sec=1.0, section_outro_sec=5.0,
video_head_trim_sec=0.5, shot_min/max=3.0/8.0s) passen strukturell NICHT zu
Phase 10s CutPlan-getriebenen Inhalten. Für die Validatoraufrufe wird daher
eine eigens abgeleitete, permissive Settings-Instanz je Sektion verwendet
(siehe `_relaxed_validation_settings`) statt `edit_plan.settings` blind zu
übernehmen."""

from __future__ import annotations

import json
import re
from pathlib import Path

from otio_app.analysis_models import EditPlanDocument, EditPlanSettings, TimelineItem, VoiceoverPlan
from otio_app.defaults import (
    PRODUCTION_EDIT_PLAN_CANDIDATE_STATUS_STAGING_DRAFT,
    PRODUCTION_EDIT_PLAN_ERROR_MAPPING_TRACE_ITEM_MISSING,
    PRODUCTION_EDIT_PLAN_ERROR_MAPPING_TRACE_ROUNDTRIP_FAILED,
    PRODUCTION_EDIT_PLAN_ERROR_PRODUCTION_STAGING_MISSING,
    PRODUCTION_EDIT_PLAN_ERROR_PRODUCTION_STAGING_STALE,
    PRODUCTION_EDIT_PLAN_ERROR_PRODUCTION_STAGING_TRACE_MISSING,
    PRODUCTION_EDIT_PLAN_ERROR_PRODUCTION_TIMELINE_VALIDATION_FAILED,
    PRODUCTION_EDIT_PLAN_ERROR_SECRET_LEAK_DETECTED,
    PRODUCTION_EDIT_PLAN_ERROR_SHOT_COUNT_MISMATCH,
    PRODUCTION_EDIT_PLAN_ERROR_SHOT_DURATION_INVALID,
    PRODUCTION_EDIT_PLAN_ERROR_SHOT_TIMING_OUTSIDE_VOICEOVER,
    PRODUCTION_EDIT_PLAN_ERROR_STAGED_EDIT_PLAN_CONFIRMED_TRUE,
    PRODUCTION_EDIT_PLAN_ERROR_STAGED_EDIT_PLAN_EMPTY_SHOTS,
    PRODUCTION_EDIT_PLAN_ERROR_STAGED_EDIT_PLAN_EMPTY_TIMELINE,
    PRODUCTION_EDIT_PLAN_ERROR_STAGED_EDIT_PLAN_HASH_MISMATCH,
    PRODUCTION_EDIT_PLAN_ERROR_STAGED_EDIT_PLAN_MISSING,
    PRODUCTION_EDIT_PLAN_ERROR_STAGED_EDIT_PLAN_STATUS_INVALID,
    PRODUCTION_EDIT_PLAN_ERROR_TIMELINE_ITEM_ASSET_MISSING,
    PRODUCTION_EDIT_PLAN_ERROR_TIMELINE_ITEM_DURATION_INVALID,
    PRODUCTION_EDIT_PLAN_ERROR_TIMELINE_ITEM_SOURCE_RANGE_INVALID,
    PRODUCTION_EDIT_PLAN_ERROR_TIMELINE_ITEM_TYPE_INVALID,
    PRODUCTION_EDIT_PLAN_ERROR_VOICEOVER_AUDIO_ITEM_LEAKED,
    PRODUCTION_EDIT_PLAN_ERROR_VOICEOVER_AUDIO_PATH_MISSING,
    PRODUCTION_EDIT_PLAN_ERROR_VOICEOVER_DURATION_INVALID,
    PRODUCTION_EDIT_PLAN_ERROR_VOICEOVER_DURATION_SOURCE_INVALID,
    PRODUCTION_EDIT_PLAN_ERROR_VOICEOVER_PLAN_MISSING,
    PRODUCTION_EDIT_PLAN_ERROR_VOICEOVER_TIMING_INVALID,
    PRODUCTION_EDIT_PLAN_ERROR_VOICEOVER_TRIM_POLICY_INVALID,
    PRODUCTION_EDIT_PLAN_VALIDATION_STATUS_BLOCKED,
    PRODUCTION_EDIT_PLAN_VALIDATION_STATUS_PASS,
    PRODUCTION_EDIT_PLAN_VALIDATION_STATUS_WARNING,
    READINESS_SEVERITY_BLOCKER,
    READINESS_SEVERITY_WARNING,
)
from otio_app.models import Project
from otio_app.project_layout import get_production_edit_plan_validation_report_path
from otio_app.services.edit_plan_validator import validate_timeline_items, validate_voiceover_plan
from otio_app.services.media_utils import probe_duration_seconds
from otio_app.services.voiceover_generation.cut_plan_edit_plan_bridge import _scan_for_leaked_secrets
from otio_app.services.voiceover_generation.llm_trace_service import content_hash_of_model
from otio_app.services.voiceover_generation.production_edit_plan_models import (
    ProductionEditPlanMappingTraceDocument,
    ProductionEditPlanPackage,
    ProductionEditPlanSection,
    ProductionEditPlanValidationError,
    ProductionEditPlanValidationReport,
)
from otio_app.services.voiceover_generation.production_edit_plan_staging_service import (
    is_production_edit_plan_staging_stale,
    load_production_edit_plan_staging_package,
    load_staged_edit_plan,
)
from otio_app.services.voiceover_generation.production_edit_plan_trace import load_production_edit_plan_mapping_trace

__all__ = [
    "validate_production_edit_plan_staging",
    "validate_staged_section",
    "save_production_edit_plan_validation_report",
    "load_production_edit_plan_validation_report",
    "is_production_edit_plan_validation_report_stale",
    "classify_production_edit_plan_validation_status",
    "build_production_validation_error_from_existing_error",
    "normalize_existing_validator_error",
]

_ROUNDTRIP_TOLERANCE_SEC = 0.05
# Phase 8.5 „Visual Coverage Fix“ erweitert Visual-Segmente bewusst über die
# reine Audio-Dauer hinaus (initial_audio_offset_sec-Preroll, Pause-Hold am
# Sektionsende, Rundung auf Shot-Mindest-/Höchstdauer) — ein Shot darf daher
# das VoiceoverPlan-Zeitfenster um einen moderaten Betrag über-/unterschreiten,
# OHNE dass das ein Defekt ist. Diese Toleranz blockiert nur grobe, strukturell
# unplausible Ausreißer (z. B. ein um mehrere Sekunden verschobenes/
# manipuliertes voice_end_sec), nicht die durch das Coverage-Design erwartete
# Overlap-Verlängerung.
_SHOT_VOICEOVER_OVERRUN_TOLERANCE_SEC = 5.0
_VALID_VISUAL_TYPES = frozenset({"video_shot", "image_shot", "image_with_background"})
_VOICEOVER_AUDIO_TYPE = "voiceover_audio"

# Nachrichtenfragmente, die validate_voiceover_plan/validate_timeline_items
# intern IMMER auf gestagten Phase-10-Inhalten auslösen würden, obwohl es
# sich strukturell um keinen Defekt handelt — siehe Modul-Docstring. Werden
# aus den rohen Validator-Meldungen gefiltert, BEVOR sie in
# ProductionEditPlanValidationError übersetzt werden. Die jeweils korrekte,
# Phase-10-eigene Prüfung erfolgt explizit weiter unten (siehe
# _validate_voiceover_plan_explicit).
_KNOWN_INCOMPATIBLE_MESSAGE_MARKERS = (
    # duration_source="bridge_audio_plan" ist für Phase-10-Voiceover-Pläne
    # KORREKT (siehe _validate_voiceover_plan_explicit) — der Produktions-
    # Validator kennt nur die With-Voice-over-Konvention "ffprobe".
    "duration_source muss ffprobe sein",
)


def _matches_known_incompatible(message: str) -> bool:
    return any(marker in message for marker in _KNOWN_INCOMPATIBLE_MESSAGE_MARKERS)


def _relaxed_validation_settings(voiceover: VoiceoverPlan | None) -> EditPlanSettings:
    """Eigens für Phase 10.3 abgeleitete, permissive Settings-Instanz für die
    Aufrufe von validate_timeline_items/validate_voiceover_plan — siehe
    Modul-Docstring für die Begründung je Feld."""
    matched_offset = voiceover.timeline_start_sec if voiceover is not None else 0.0
    return EditPlanSettings(
        shot_min_sec=0.0,
        shot_max_sec=1_000_000.0,
        audio_offset_sec=max(0.0, matched_offset),
        section_outro_sec=0.0,
        video_head_trim_sec=0.0,
        video_head_trim_policy="fixed_trim",
        voiceover_trim_policy="disabled",
    )


def _timeline_item_id_from_message(message: str) -> str:
    match = re.match(r"^([\w.-]+):\s", message)
    return match.group(1) if match else ""


def normalize_existing_validator_error(
    message: str,
    *,
    error_type: str,
    severity: str,
    scope: str,
    staging_section_id: str = "",
    production_section_id: str = "",
    timeline_item_id: str = "",
    fix_hint: str = "",
) -> ProductionEditPlanValidationError:
    """Übersetzt EINE rohe String-Meldung aus validate_timeline_items/
    validate_voiceover_plan (`TimelineValidationResult.errors`/`.warnings`)
    in eine strukturierte ProductionEditPlanValidationError. Viele dieser
    Meldungen beginnen mit `"{timeline_item_id}: ..."` — wird hier best-effort
    extrahiert, falls der Aufrufer keine explizite timeline_item_id kennt."""
    resolved_item_id = timeline_item_id or _timeline_item_id_from_message(message)
    return ProductionEditPlanValidationError(
        type=error_type,
        severity=severity,
        scope=scope,
        staging_section_id=staging_section_id,
        production_section_id=production_section_id,
        timeline_item_id=resolved_item_id,
        message=message,
        fix_hint=fix_hint,
    )


def build_production_validation_error_from_existing_error(
    plan_error,
    *,
    error_type: str,
    severity: str,
    scope: str,
    staging_section_id: str = "",
    production_section_id: str = "",
    fix_hint: str = "",
) -> ProductionEditPlanValidationError:
    """Übersetzt EINEN strukturierten PlanValidationError (aus
    validate_shot_duration_rules/validate_asset_usage_rules/
    validate_final_edit_plan) in eine ProductionEditPlanValidationError."""
    from otio_app.services.edit_plan_validator import plan_validation_error_to_message

    message = plan_error.message or plan_validation_error_to_message(plan_error)
    return ProductionEditPlanValidationError(
        type=error_type,
        severity=severity,
        scope=scope,
        staging_section_id=staging_section_id,
        production_section_id=production_section_id,
        timeline_item_id=plan_error.timeline_item_id or "",
        message=message,
        fix_hint=fix_hint,
    )


def classify_production_edit_plan_validation_status(
    warnings: list[ProductionEditPlanValidationError], blockers: list[ProductionEditPlanValidationError]
) -> str:
    if blockers:
        return PRODUCTION_EDIT_PLAN_VALIDATION_STATUS_BLOCKED
    if warnings:
        return PRODUCTION_EDIT_PLAN_VALIDATION_STATUS_WARNING
    return PRODUCTION_EDIT_PLAN_VALIDATION_STATUS_PASS


def _dedupe_errors(errors: list[ProductionEditPlanValidationError]) -> list[ProductionEditPlanValidationError]:
    seen: set[tuple[str, str, str, str, str, str, str]] = set()
    deduped: list[ProductionEditPlanValidationError] = []
    for error in errors:
        key = (
            error.type,
            error.severity,
            error.scope,
            error.staging_section_id,
            error.production_section_id,
            error.timeline_item_id,
            error.message,
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(error)
    return deduped


# --- Dokument-Struktur (§Dokument-Struktur) ---


def _validate_document_structure(
    section: ProductionEditPlanSection, edit_plan: EditPlanDocument
) -> tuple[list[ProductionEditPlanValidationError], list[ProductionEditPlanValidationError]]:
    warnings: list[ProductionEditPlanValidationError] = []
    blockers: list[ProductionEditPlanValidationError] = []

    def _blocker(error_type: str, message: str) -> None:
        blockers.append(
            ProductionEditPlanValidationError(
                type=error_type,
                severity=READINESS_SEVERITY_BLOCKER,
                scope="section",
                staging_section_id=section.staging_section_id,
                production_section_id=section.production_section_id,
                message=message,
            )
        )

    if edit_plan.confirmed:
        _blocker(
            PRODUCTION_EDIT_PLAN_ERROR_STAGED_EDIT_PLAN_CONFIRMED_TRUE,
            "Gestagtes EditPlanDocument hat confirmed=true — ein Staging-Draft darf niemals bestätigt sein.",
        )
    if edit_plan.candidate_status != PRODUCTION_EDIT_PLAN_CANDIDATE_STATUS_STAGING_DRAFT:
        _blocker(
            PRODUCTION_EDIT_PLAN_ERROR_STAGED_EDIT_PLAN_STATUS_INVALID,
            f"candidate_status ist {edit_plan.candidate_status!r}, erwartet "
            f"{PRODUCTION_EDIT_PLAN_CANDIDATE_STATUS_STAGING_DRAFT!r}.",
        )
    if not (edit_plan.folder_name or "").strip():
        _blocker(PRODUCTION_EDIT_PLAN_ERROR_STAGED_EDIT_PLAN_STATUS_INVALID, "folder_name fehlt.")
    if not edit_plan.allow_black_outro:
        _blocker(
            PRODUCTION_EDIT_PLAN_ERROR_STAGED_EDIT_PLAN_STATUS_INVALID,
            "allow_black_outro muss True sein (Phase 10 synthetisiert keine Outro-Elemente).",
        )
    if edit_plan.voiceover is None:
        _blocker(PRODUCTION_EDIT_PLAN_ERROR_VOICEOVER_PLAN_MISSING, "voiceover fehlt im gestagten EditPlanDocument.")
    if not edit_plan.timeline_items:
        _blocker(PRODUCTION_EDIT_PLAN_ERROR_STAGED_EDIT_PLAN_EMPTY_TIMELINE, "timeline_items ist leer.")
    if not edit_plan.shots:
        _blocker(PRODUCTION_EDIT_PLAN_ERROR_STAGED_EDIT_PLAN_EMPTY_SHOTS, "shots ist leer.")

    return warnings, blockers


# --- Kein Bridge-Leak (§Kein Bridge-Leak) ---


def _validate_no_bridge_leak(
    section: ProductionEditPlanSection, edit_plan: EditPlanDocument
) -> tuple[list[ProductionEditPlanValidationError], list[ProductionEditPlanValidationError]]:
    warnings: list[ProductionEditPlanValidationError] = []
    blockers: list[ProductionEditPlanValidationError] = []

    for item in edit_plan.timeline_items:
        if item.type == _VOICEOVER_AUDIO_TYPE:
            blockers.append(
                ProductionEditPlanValidationError(
                    type=PRODUCTION_EDIT_PLAN_ERROR_VOICEOVER_AUDIO_ITEM_LEAKED,
                    severity=READINESS_SEVERITY_BLOCKER,
                    scope="timeline",
                    staging_section_id=section.staging_section_id,
                    production_section_id=section.production_section_id,
                    timeline_item_id=item.timeline_item_id,
                    message=f"{item.timeline_item_id}: TimelineItem vom Typ 'voiceover_audio' im "
                    "Produktions-Staging — Audio läuft ausschließlich über VoiceoverPlan.",
                )
            )

    for leak in _scan_for_leaked_secrets(edit_plan):
        blockers.append(
            ProductionEditPlanValidationError(
                type=PRODUCTION_EDIT_PLAN_ERROR_SECRET_LEAK_DETECTED,
                severity=READINESS_SEVERITY_BLOCKER,
                scope="section",
                staging_section_id=section.staging_section_id,
                production_section_id=section.production_section_id,
                message=leak,
            )
        )

    return warnings, blockers


# --- VoiceoverPlan (§VoiceoverPlan) ---


def _validate_voiceover_plan_explicit(
    section: ProductionEditPlanSection, voiceover: VoiceoverPlan
) -> tuple[list[ProductionEditPlanValidationError], list[ProductionEditPlanValidationError]]:
    warnings: list[ProductionEditPlanValidationError] = []
    blockers: list[ProductionEditPlanValidationError] = []

    def _blocker(error_type: str, message: str) -> None:
        blockers.append(
            ProductionEditPlanValidationError(
                type=error_type,
                severity=READINESS_SEVERITY_BLOCKER,
                scope="voiceover",
                staging_section_id=section.staging_section_id,
                production_section_id=section.production_section_id,
                message=message,
            )
        )

    if not voiceover.path or not Path(voiceover.path).is_file():
        _blocker(
            PRODUCTION_EDIT_PLAN_ERROR_VOICEOVER_AUDIO_PATH_MISSING,
            f"voiceover.path nicht lesbar: {voiceover.path!r}.",
        )
    if not (voiceover.timeline_end_sec > voiceover.timeline_start_sec):
        _blocker(
            PRODUCTION_EDIT_PLAN_ERROR_VOICEOVER_TIMING_INVALID,
            f"voiceover.timeline_end_sec ({voiceover.timeline_end_sec:.3f}) muss > "
            f"timeline_start_sec ({voiceover.timeline_start_sec:.3f}) sein.",
        )
    if not (voiceover.source_out_sec > voiceover.source_in_sec):
        _blocker(
            PRODUCTION_EDIT_PLAN_ERROR_VOICEOVER_TIMING_INVALID,
            f"voiceover.source_out_sec ({voiceover.source_out_sec:.3f}) muss > "
            f"source_in_sec ({voiceover.source_in_sec:.3f}) sein.",
        )
    if not (voiceover.duration_sec > 0):
        _blocker(
            PRODUCTION_EDIT_PLAN_ERROR_VOICEOVER_DURATION_INVALID,
            f"voiceover.duration_sec muss > 0 sein (ist {voiceover.duration_sec:.3f}).",
        )
    if voiceover.duration_source != "bridge_audio_plan":
        _blocker(
            PRODUCTION_EDIT_PLAN_ERROR_VOICEOVER_DURATION_SOURCE_INVALID,
            f"voiceover.duration_source muss 'bridge_audio_plan' sein (ist {voiceover.duration_source!r}).",
        )
    if voiceover.trim_policy != "disabled":
        _blocker(
            PRODUCTION_EDIT_PLAN_ERROR_VOICEOVER_TRIM_POLICY_INVALID,
            f"voiceover.trim_policy muss 'disabled' sein (ist {voiceover.trim_policy!r}).",
        )

    return warnings, blockers


# --- TimelineItems (§TimelineItems) ---


def _validate_timeline_items_explicit(
    section: ProductionEditPlanSection,
    items: list[TimelineItem],
    voiceover: VoiceoverPlan | None,
    duration_cache: dict[str, float | None],
) -> tuple[list[ProductionEditPlanValidationError], list[ProductionEditPlanValidationError]]:
    warnings: list[ProductionEditPlanValidationError] = []
    blockers: list[ProductionEditPlanValidationError] = []

    def _cached_probe_duration(path_str: str) -> float | None:
        if path_str not in duration_cache:
            duration_cache[path_str] = probe_duration_seconds(Path(path_str))
        return duration_cache[path_str]

    for item in items:
        def _blocker(error_type: str, message: str) -> None:
            blockers.append(
                ProductionEditPlanValidationError(
                    type=error_type,
                    severity=READINESS_SEVERITY_BLOCKER,
                    scope="timeline",
                    staging_section_id=section.staging_section_id,
                    production_section_id=section.production_section_id,
                    timeline_item_id=item.timeline_item_id,
                    message=message,
                )
            )

        duration = item.timeline_out_sec - item.timeline_in_sec
        if duration <= 0:
            _blocker(
                PRODUCTION_EDIT_PLAN_ERROR_TIMELINE_ITEM_DURATION_INVALID,
                f"{item.timeline_item_id}: duration ({duration:.3f}s) muss > 0 sein "
                f"(timeline_in_sec={item.timeline_in_sec:.3f}, timeline_out_sec={item.timeline_out_sec:.3f}).",
            )
        if item.source_out_sec <= item.source_in_sec:
            _blocker(
                PRODUCTION_EDIT_PLAN_ERROR_TIMELINE_ITEM_SOURCE_RANGE_INVALID,
                f"{item.timeline_item_id}: source_out_sec ({item.source_out_sec:.3f}) muss > "
                f"source_in_sec ({item.source_in_sec:.3f}) sein.",
            )
        if item.timeline_in_sec < -0.001 or item.timeline_out_sec < -0.001:
            _blocker(
                PRODUCTION_EDIT_PLAN_ERROR_TIMELINE_ITEM_SOURCE_RANGE_INVALID,
                f"{item.timeline_item_id}: negative Timeline-Zeiten sind unzulässig.",
            )
        if not item.resolved_media_path:
            _blocker(
                PRODUCTION_EDIT_PLAN_ERROR_TIMELINE_ITEM_ASSET_MISSING,
                f"{item.timeline_item_id}: resolved_media_path fehlt.",
            )
        if item.type not in _VALID_VISUAL_TYPES:
            _blocker(
                PRODUCTION_EDIT_PLAN_ERROR_TIMELINE_ITEM_TYPE_INVALID,
                f"{item.timeline_item_id}: type={item.type!r} ist in Phase 10.3 kein gültiger "
                f"Produktions-Visual-Typ (erwartet einer aus {sorted(_VALID_VISUAL_TYPES)}).",
            )
        if item.type == "video_shot" and item.resolved_media_path:
            real_duration = _cached_probe_duration(item.resolved_media_path)
            if real_duration is not None and item.source_out_sec > real_duration + 0.05:
                _blocker(
                    PRODUCTION_EDIT_PLAN_ERROR_TIMELINE_ITEM_SOURCE_RANGE_INVALID,
                    f"{item.timeline_item_id}: source_out_sec ({item.source_out_sec:.3f}s) > "
                    f"tatsächliche Video-Dauer ({real_duration:.3f}s).",
                )

    # Timeline-Overlaps innerhalb derselben Sektion (V1 ist die einzige
    # Visual-Spur, die Phase 10 erzeugt).
    sorted_items = sorted(items, key=lambda entry: entry.timeline_in_sec)
    for previous_item, next_item in zip(sorted_items, sorted_items[1:]):
        if next_item.timeline_in_sec + 0.02 < previous_item.timeline_out_sec:
            blockers.append(
                ProductionEditPlanValidationError(
                    type=PRODUCTION_EDIT_PLAN_ERROR_PRODUCTION_TIMELINE_VALIDATION_FAILED,
                    severity=READINESS_SEVERITY_BLOCKER,
                    scope="timeline",
                    staging_section_id=section.staging_section_id,
                    production_section_id=section.production_section_id,
                    timeline_item_id=next_item.timeline_item_id,
                    message=f"{previous_item.timeline_item_id}/{next_item.timeline_item_id}: "
                    "Timeline-Overlap zwischen aufeinanderfolgenden V1-Items.",
                )
            )

    settings = _relaxed_validation_settings(voiceover)
    result = validate_timeline_items(
        items,
        settings=settings,
        allow_black_outro=True,
        voiceover=voiceover,
        opening_title_required=False,
        require_rendered_media=False,
        rules_doc=None,
        work_dir_path=None,
    )
    for message in result.errors:
        if _matches_known_incompatible(message):
            continue
        blockers.append(
            normalize_existing_validator_error(
                message,
                error_type=PRODUCTION_EDIT_PLAN_ERROR_PRODUCTION_TIMELINE_VALIDATION_FAILED,
                severity=READINESS_SEVERITY_BLOCKER,
                scope="timeline",
                staging_section_id=section.staging_section_id,
                production_section_id=section.production_section_id,
            )
        )
    for message in result.warnings:
        if _matches_known_incompatible(message):
            continue
        warnings.append(
            normalize_existing_validator_error(
                message,
                error_type=PRODUCTION_EDIT_PLAN_ERROR_PRODUCTION_TIMELINE_VALIDATION_FAILED,
                severity=READINESS_SEVERITY_WARNING,
                scope="timeline",
                staging_section_id=section.staging_section_id,
                production_section_id=section.production_section_id,
            )
        )

    if voiceover is not None:
        voice_result = validate_voiceover_plan(voiceover, settings=settings, items=items)
        for message in voice_result.errors:
            if _matches_known_incompatible(message):
                continue
            blockers.append(
                normalize_existing_validator_error(
                    message,
                    error_type=PRODUCTION_EDIT_PLAN_ERROR_VOICEOVER_TIMING_INVALID,
                    severity=READINESS_SEVERITY_BLOCKER,
                    scope="voiceover",
                    staging_section_id=section.staging_section_id,
                    production_section_id=section.production_section_id,
                )
            )
        for message in voice_result.warnings:
            if _matches_known_incompatible(message):
                continue
            warnings.append(
                normalize_existing_validator_error(
                    message,
                    error_type=PRODUCTION_EDIT_PLAN_ERROR_VOICEOVER_TIMING_INVALID,
                    severity=READINESS_SEVERITY_WARNING,
                    scope="voiceover",
                    staging_section_id=section.staging_section_id,
                    production_section_id=section.production_section_id,
                )
            )

    return warnings, blockers


# --- Shots (§Shots) ---


def _validate_shots(
    section: ProductionEditPlanSection, edit_plan: EditPlanDocument
) -> tuple[list[ProductionEditPlanValidationError], list[ProductionEditPlanValidationError]]:
    warnings: list[ProductionEditPlanValidationError] = []
    blockers: list[ProductionEditPlanValidationError] = []

    def _blocker(error_type: str, message: str, timeline_item_id: str = "") -> None:
        blockers.append(
            ProductionEditPlanValidationError(
                type=error_type,
                severity=READINESS_SEVERITY_BLOCKER,
                scope="shot",
                staging_section_id=section.staging_section_id,
                production_section_id=section.production_section_id,
                timeline_item_id=timeline_item_id,
                message=message,
            )
        )

    visual_item_count = len(edit_plan.timeline_items)
    if len(edit_plan.shots) != visual_item_count:
        _blocker(
            PRODUCTION_EDIT_PLAN_ERROR_SHOT_COUNT_MISMATCH,
            f"shot_count ({len(edit_plan.shots)}) != Anzahl Visual-TimelineItems ({visual_item_count}).",
        )

    voiceover = edit_plan.voiceover
    for index, shot in enumerate(edit_plan.shots):
        if shot.duration_sec <= 0:
            _blocker(
                PRODUCTION_EDIT_PLAN_ERROR_SHOT_DURATION_INVALID,
                f"Shot #{index}: duration_sec ({shot.duration_sec:.3f}) muss > 0 sein.",
            )
        if not (shot.asset_id or shot.asset_path):
            _blocker(
                PRODUCTION_EDIT_PLAN_ERROR_TIMELINE_ITEM_ASSET_MISSING,
                f"Shot #{index}: weder asset_id noch asset_path gesetzt.",
            )
        if voiceover is not None:
            tolerance = _SHOT_VOICEOVER_OVERRUN_TOLERANCE_SEC
            if shot.voice_start_sec < -tolerance or shot.voice_end_sec > voiceover.duration_sec + tolerance:
                _blocker(
                    PRODUCTION_EDIT_PLAN_ERROR_SHOT_TIMING_OUTSIDE_VOICEOVER,
                    f"Shot #{index}: voice_start_sec/voice_end_sec "
                    f"({shot.voice_start_sec:.3f}/{shot.voice_end_sec:.3f}) liegt außerhalb des "
                    f"VoiceoverPlan-Zeitbereichs (0..{voiceover.duration_sec:.3f}).",
                )

    return warnings, blockers


# --- Mapping Trace (§Mapping Trace) ---


def _validate_mapping_trace(
    section: ProductionEditPlanSection,
    edit_plan: EditPlanDocument,
    package: ProductionEditPlanPackage,
    trace: ProductionEditPlanMappingTraceDocument | None,
) -> tuple[list[ProductionEditPlanValidationError], list[ProductionEditPlanValidationError]]:
    warnings: list[ProductionEditPlanValidationError] = []
    blockers: list[ProductionEditPlanValidationError] = []

    def _blocker(error_type: str, message: str, timeline_item_id: str = "") -> None:
        blockers.append(
            ProductionEditPlanValidationError(
                type=error_type,
                severity=READINESS_SEVERITY_BLOCKER,
                scope="trace",
                staging_section_id=section.staging_section_id,
                production_section_id=section.production_section_id,
                timeline_item_id=timeline_item_id,
                message=message,
            )
        )

    if trace is None:
        # Wird bereits auf Paket-Ebene als PRODUCTION_STAGING_TRACE_MISSING
        # gemeldet — hier keine Doppelmeldung je Sektion.
        return warnings, blockers

    section_entries = [entry for entry in trace.entries if entry.resulting_staging_section_id == section.staging_section_id]
    known_section_ids = {entry.staging_section_id for entry in package.sections}

    visual_entries = {
        entry.resulting_timeline_item_id: entry for entry in section_entries if entry.source_bridge_timeline_item_id
    }
    audio_entries = [entry for entry in section_entries if entry.mapping_reason == "bridge_audio_plan_to_voiceover_plan"]

    for item in edit_plan.timeline_items:
        entry = visual_entries.get(item.timeline_item_id)
        if entry is None:
            _blocker(
                PRODUCTION_EDIT_PLAN_ERROR_MAPPING_TRACE_ITEM_MISSING,
                f"{item.timeline_item_id}: kein Visual-Mapping-Trace-Eintrag für dieses TimelineItem gefunden.",
                timeline_item_id=item.timeline_item_id,
            )
            continue
        if entry.resulting_staging_section_id not in known_section_ids:
            _blocker(
                PRODUCTION_EDIT_PLAN_ERROR_MAPPING_TRACE_ITEM_MISSING,
                f"{item.timeline_item_id}: Trace-Eintrag verweist auf unbekannte Section "
                f"'{entry.resulting_staging_section_id}', die nicht im Package existiert.",
                timeline_item_id=item.timeline_item_id,
            )

    if edit_plan.voiceover is not None and not audio_entries:
        _blocker(
            PRODUCTION_EDIT_PLAN_ERROR_MAPPING_TRACE_ITEM_MISSING,
            "Kein Audio-Mapping-Trace-Eintrag (bridge_audio_plan_to_voiceover_plan) für den VoiceoverPlan gefunden.",
        )

    # Roundtrip: alle Einträge EINER Sektion müssen denselben section_start_offset
    # teilen (original - local). Referenz kommt bevorzugt vom Audio-Eintrag
    # (autoritative Audio-Plan-Zeitbasis), sonst vom ersten Visual-Eintrag.
    reference_entry = audio_entries[0] if audio_entries else next(iter(visual_entries.values()), None)
    if reference_entry is not None:
        reference_offset = reference_entry.original_timeline_in_sec - reference_entry.local_timeline_in_sec
        for entry in section_entries:
            offset_in = entry.original_timeline_in_sec - entry.local_timeline_in_sec
            offset_out = entry.original_timeline_out_sec - entry.local_timeline_out_sec
            if abs(offset_in - reference_offset) > _ROUNDTRIP_TOLERANCE_SEC or abs(
                offset_out - reference_offset
            ) > _ROUNDTRIP_TOLERANCE_SEC:
                _blocker(
                    PRODUCTION_EDIT_PLAN_ERROR_MAPPING_TRACE_ROUNDTRIP_FAILED,
                    f"Trace-Eintrag '{entry.trace_id}': local_timeline_{{in,out}}_sec + section_start_offset "
                    f"({reference_offset:.3f}) != original_timeline_{{in,out}}_sec — Roundtrip fehlgeschlagen.",
                    timeline_item_id=entry.resulting_timeline_item_id,
                )

    return warnings, blockers


# --- Orchestrierung je Sektion ---


def validate_staged_section(
    project: Project,
    package_section: ProductionEditPlanSection,
    edit_plan: EditPlanDocument,
    *,
    package: ProductionEditPlanPackage | None = None,
    trace: ProductionEditPlanMappingTraceDocument | None = None,
    duration_cache: dict[str, float | None] | None = None,
) -> tuple[list[ProductionEditPlanValidationError], list[ProductionEditPlanValidationError]]:
    """Validiert EIN gestagtes EditPlanDocument vollständig. `package`/`trace`/
    `duration_cache` sind optionale Erweiterungen für den Mapping-Trace- bzw.
    Duration-Cache-Kontext eines vollen validate_production_edit_plan_staging-
    Laufs; ohne sie werden nur die dokument-/voiceover-/timeline-/shot-lokalen
    Prüfungen durchgeführt (Mapping-Trace-Prüfung wird übersprungen)."""
    warnings: list[ProductionEditPlanValidationError] = []
    blockers: list[ProductionEditPlanValidationError] = []
    local_duration_cache: dict[str, float | None] = duration_cache if duration_cache is not None else {}

    for step_warnings, step_blockers in (
        _validate_document_structure(package_section, edit_plan),
        _validate_no_bridge_leak(package_section, edit_plan),
    ):
        warnings.extend(step_warnings)
        blockers.extend(step_blockers)

    if edit_plan.voiceover is not None:
        step_warnings, step_blockers = _validate_voiceover_plan_explicit(package_section, edit_plan.voiceover)
        warnings.extend(step_warnings)
        blockers.extend(step_blockers)

    if edit_plan.timeline_items:
        step_warnings, step_blockers = _validate_timeline_items_explicit(
            package_section, edit_plan.timeline_items, edit_plan.voiceover, local_duration_cache
        )
        warnings.extend(step_warnings)
        blockers.extend(step_blockers)

    step_warnings, step_blockers = _validate_shots(package_section, edit_plan)
    warnings.extend(step_warnings)
    blockers.extend(step_blockers)

    if package is not None:
        step_warnings, step_blockers = _validate_mapping_trace(package_section, edit_plan, package, trace)
        warnings.extend(step_warnings)
        blockers.extend(step_blockers)

    return warnings, blockers


# --- Orchestrierung Gesamtlauf ---


def validate_production_edit_plan_staging(project: Project) -> ProductionEditPlanValidationReport:
    """Vollständige Revalidierung des Production-EditPlan-Staging-Pakets.

    Rein prüfend — schreibt AUSSCHLIESSLICH
    `production_edit_plan_validation_report.json`. Verändert niemals
    `production_edit_plan_package.json` oder eine `staged_edit_plans/*/
    edit_plan.json`-Datei."""
    warnings: list[ProductionEditPlanValidationError] = []
    blockers: list[ProductionEditPlanValidationError] = []
    duration_cache: dict[str, float | None] = {}

    package = load_production_edit_plan_staging_package(project)
    if package is None:
        blockers.append(
            ProductionEditPlanValidationError(
                type=PRODUCTION_EDIT_PLAN_ERROR_PRODUCTION_STAGING_MISSING,
                severity=READINESS_SEVERITY_BLOCKER,
                scope="project",
                message="production_edit_plan_package.json existiert nicht — bitte zuerst Staging ausführen.",
            )
        )
        report = ProductionEditPlanValidationReport(
            project_id=project.id,
            status=classify_production_edit_plan_validation_status(warnings, blockers),
            warnings=warnings,
            blockers=blockers,
        )
        return save_production_edit_plan_validation_report(project, report)

    if is_production_edit_plan_staging_stale(project, package):
        blockers.append(
            ProductionEditPlanValidationError(
                type=PRODUCTION_EDIT_PLAN_ERROR_PRODUCTION_STAGING_STALE,
                severity=READINESS_SEVERITY_BLOCKER,
                scope="project",
                message="Das Staging-Paket ist veraltet (Bridge-Snapshot oder gestagte Dateien haben sich "
                "geändert) — bitte zuerst neu stagen.",
            )
        )

    trace = load_production_edit_plan_mapping_trace(project)
    if trace is None:
        blockers.append(
            ProductionEditPlanValidationError(
                type=PRODUCTION_EDIT_PLAN_ERROR_PRODUCTION_STAGING_TRACE_MISSING,
                severity=READINESS_SEVERITY_BLOCKER,
                scope="project",
                message="production_edit_plan_mapping_trace.json existiert nicht.",
            )
        )

    for section in package.sections:
        edit_plan = load_staged_edit_plan(project, section.staging_section_id)
        if edit_plan is None:
            blockers.append(
                ProductionEditPlanValidationError(
                    type=PRODUCTION_EDIT_PLAN_ERROR_STAGED_EDIT_PLAN_MISSING,
                    severity=READINESS_SEVERITY_BLOCKER,
                    scope="section",
                    staging_section_id=section.staging_section_id,
                    production_section_id=section.production_section_id,
                    message=f"staged_edit_plans/{section.staging_section_id}/edit_plan.json existiert nicht.",
                )
            )
            continue

        if section.staged_edit_plan_hash and content_hash_of_model(edit_plan) != section.staged_edit_plan_hash:
            blockers.append(
                ProductionEditPlanValidationError(
                    type=PRODUCTION_EDIT_PLAN_ERROR_STAGED_EDIT_PLAN_HASH_MISMATCH,
                    severity=READINESS_SEVERITY_BLOCKER,
                    scope="section",
                    staging_section_id=section.staging_section_id,
                    production_section_id=section.production_section_id,
                    message="staged_edit_plan_hash im Package passt nicht zur aktuellen edit_plan.json — "
                    "Datei wurde nach dem Staging verändert.",
                )
            )

        section_warnings, section_blockers = validate_staged_section(
            project, section, edit_plan, package=package, trace=trace, duration_cache=duration_cache
        )
        warnings.extend(section_warnings)
        blockers.extend(section_blockers)

    warnings = _dedupe_errors(warnings)
    blockers = _dedupe_errors(blockers)

    report = ProductionEditPlanValidationReport(
        project_id=project.id,
        source_bridge_manifest_hash=package.source_bridge_manifest_hash,
        package_hash=content_hash_of_model(package),
        status=classify_production_edit_plan_validation_status(warnings, blockers),
        warnings=warnings,
        blockers=blockers,
    )
    return save_production_edit_plan_validation_report(project, report)


def save_production_edit_plan_validation_report(
    project: Project, report: ProductionEditPlanValidationReport
) -> ProductionEditPlanValidationReport:
    normalized = report.model_copy(update={"project_id": project.id})
    path = get_production_edit_plan_validation_report_path(project.work_dir_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(normalized.model_dump_json(indent=2), encoding="utf-8")
    return normalized


def load_production_edit_plan_validation_report(project: Project) -> ProductionEditPlanValidationReport | None:
    path = get_production_edit_plan_validation_report_path(project.work_dir_path)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return ProductionEditPlanValidationReport.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None


def is_production_edit_plan_validation_report_stale(project: Project, report: ProductionEditPlanValidationReport) -> bool:
    """True, wenn sich das Staging-Paket, eine gestagte edit_plan.json oder
    der bestätigte Bridge-Snapshot seit der letzten Revalidierung geändert
    haben — oder wenn das Paket fehlt. Reine Lesefunktion, kein Seiteneffekt."""
    package = load_production_edit_plan_staging_package(project)
    if package is None:
        return True
    if content_hash_of_model(package) != report.package_hash:
        return True
    if is_production_edit_plan_staging_stale(project, package):
        return True
    return False
