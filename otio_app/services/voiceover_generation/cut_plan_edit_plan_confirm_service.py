"""Phase 9.3: EditPlan Bridge Confirm/Freeze.

Bestätigt einen bereits vollständig validierten EditPlan-Bridge-Draft als
unveränderlichen Snapshot (`edit_plan_from_cut_plan.confirmed.json` +
`bridge_audio_plan.confirmed.json` + `edit_plan_bridge_trace.confirmed.json`
+ `edit_plan_bridge_confirm_manifest.json`). Reiner Snapshot der bereits
vorhandenen, validierten Bridge-Dateien — KEIN Rebuild, KEINE erneute
Übersetzung, KEINE erneute Validierung, KEINE Neuberechnung. Weiterhin ein
isolierter Bridge-Snapshot: KEIN Produktions-EditPlan unter
`_otio/edit_plan/`, KEIN locked Produktionsplan, KEIN OTIO-Export, KEIN
Render, keine neue LLM-Planung."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from otio_app.analysis_models import EditPlanDocument
from otio_app.defaults import (
    EDIT_PLAN_BRIDGE_CONFIRM_STATUS_CONFIRMED,
    EDIT_PLAN_BRIDGE_TIMELINE_ITEM_TYPE_VOICEOVER_AUDIO,
    EDIT_PLAN_BRIDGE_VALIDATION_STATUS_BLOCKED,
)
from otio_app.models import Project
from otio_app.project_layout import (
    get_cut_plan_edit_plan_bridge_audio_plan_path,
    get_cut_plan_edit_plan_bridge_confirm_manifest_path,
    get_cut_plan_edit_plan_bridge_confirmed_audio_plan_path,
    get_cut_plan_edit_plan_bridge_confirmed_draft_path,
    get_cut_plan_edit_plan_bridge_confirmed_trace_path,
    get_cut_plan_edit_plan_bridge_draft_path,
    get_cut_plan_edit_plan_bridge_trace_path,
    get_cut_plan_edit_plan_bridge_validation_report_path,
)
from otio_app.services.voiceover_generation.cut_plan_confirm_service import (
    is_confirmed_cut_plan_stale,
    load_confirmed_cut_plan,
)
from otio_app.services.voiceover_generation.cut_plan_edit_plan_bridge import (
    _scan_for_leaked_secrets,
    load_bridge_audio_plan,
    load_edit_plan_bridge_draft,
    load_edit_plan_bridge_validation_report,
)
from otio_app.services.voiceover_generation.cut_plan_edit_plan_models import (
    BridgeAudioPlanDocument,
    EditPlanBridgeConfirmManifest,
    EditPlanBridgeTraceDocument,
)
from otio_app.services.voiceover_generation.cut_plan_edit_plan_trace import load_edit_plan_bridge_trace
from otio_app.services.voiceover_generation.llm_trace_service import content_hash_of_model

__all__ = [
    "can_confirm_edit_plan_bridge",
    "confirm_edit_plan_bridge",
    "load_confirmed_edit_plan_bridge",
    "load_confirmed_bridge_audio_plan",
    "load_confirmed_bridge_trace",
    "load_edit_plan_bridge_confirm_manifest",
    "unconfirm_edit_plan_bridge",
    "is_confirmed_edit_plan_bridge_stale",
]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def can_confirm_edit_plan_bridge(project: Project) -> tuple[bool, list[str]]:
    """Prüft alle Confirm-Bedingungen (§4) und gibt (eligible, reasons)
    zurück — reasons ist leer, wenn eligible True ist. Reine Funktion, kein
    Seiteneffekt."""
    reasons: list[str] = []

    draft = load_edit_plan_bridge_draft(project)
    if draft is None:
        reasons.append("Kein EditPlan Bridge Draft (edit_plan_from_cut_plan.draft.json) vorhanden.")
        return False, reasons

    audio_plan = load_bridge_audio_plan(project)
    if audio_plan is None:
        reasons.append("Kein bridge_audio_plan.json vorhanden.")
        return False, reasons

    trace = load_edit_plan_bridge_trace(project)
    if trace is None:
        reasons.append("Kein edit_plan_bridge_trace.json vorhanden.")
        return False, reasons

    report = load_edit_plan_bridge_validation_report(project)
    if report is None:
        reasons.append("Kein edit_plan_bridge_validation_report.json vorhanden — bitte zuerst validieren.")
        return False, reasons

    if report.status == EDIT_PLAN_BRIDGE_VALIDATION_STATUS_BLOCKED:
        reasons.append("Validation Report ist BLOCKED.")
    if report.blockers:
        reasons.append(f"Validation Report enthält {len(report.blockers)} Blocker.")

    current_edit_plan_hash = content_hash_of_model(draft)
    if report.edit_plan_hash != current_edit_plan_hash:
        reasons.append(
            "Validation Report ist veraltet — der Bridge Draft hat sich seit der letzten Validierung geändert."
        )

    confirmed_cut_plan = load_confirmed_cut_plan(project)
    if confirmed_cut_plan is None:
        reasons.append("Kein bestätigter Cut Plan (cut_plan.confirmed.json) vorhanden.")
    else:
        if report.source_cut_plan_hash != content_hash_of_model(confirmed_cut_plan):
            reasons.append("Validation Report bezieht sich auf einen anderen bestätigten Cut Plan.")
        if is_confirmed_cut_plan_stale(project, confirmed_cut_plan):
            reasons.append("Der bestätigte Cut Plan ist veraltet.")

    audio_timeline_ids = {
        item.timeline_item_id
        for item in draft.timeline_items
        if item.type == EDIT_PLAN_BRIDGE_TIMELINE_ITEM_TYPE_VOICEOVER_AUDIO
    }
    audio_plan_ids = {item.timeline_item_id for item in audio_plan.items}
    if audio_timeline_ids != audio_plan_ids:
        reasons.append("bridge_audio_plan.json stimmt nicht mit den voiceover_audio TimelineItems überein.")

    trace_ids = {entry.timeline_item_id for entry in trace.entries if entry.timeline_item_id}
    draft_ids = {item.timeline_item_id for item in draft.timeline_items}
    if trace_ids != draft_ids:
        reasons.append("edit_plan_bridge_trace.json stimmt nicht mit den TimelineItems im Draft überein.")

    zero_duration_ids = [
        item.timeline_item_id for item in draft.timeline_items if item.timeline_out_sec - item.timeline_in_sec <= 0
    ]
    if zero_duration_ids:
        reasons.append(
            f"{len(zero_duration_ids)} TimelineItem(s) mit Dauer <= 0: {', '.join(zero_duration_ids)}."
        )

    leaks = _scan_for_leaked_secrets(draft)
    if leaks:
        reasons.append("Bridge Draft enthält möglicherweise sensible Daten: " + "; ".join(leaks))

    return not reasons, reasons


def confirm_edit_plan_bridge(project: Project) -> EditPlanBridgeConfirmManifest:
    """Lädt Draft/Audio-Plan/Trace/Validation-Report, prüft
    can_confirm_edit_plan_bridge, schreibt bei Erfolg die vier confirmed-
    Snapshot-Dateien (§5). Keine Neuberechnung, kein Rebuild, kein OTIO,
    kein Schreiben unter _otio/edit_plan/.

    Wirft ValueError mit den konkreten Gründen, wenn can_confirm_edit_plan_
    bridge False zurückgibt."""
    eligible, reasons = can_confirm_edit_plan_bridge(project)
    if not eligible:
        raise ValueError("EditPlan Bridge kann nicht bestätigt werden: " + " ".join(reasons))

    draft = load_edit_plan_bridge_draft(project)
    audio_plan = load_bridge_audio_plan(project)
    trace = load_edit_plan_bridge_trace(project)
    report = load_edit_plan_bridge_validation_report(project)
    assert draft is not None and audio_plan is not None and trace is not None and report is not None

    confirmed_draft_path = get_cut_plan_edit_plan_bridge_confirmed_draft_path(project.work_dir_path)
    confirmed_draft_path.parent.mkdir(parents=True, exist_ok=True)
    confirmed_draft_path.write_text(draft.model_dump_json(indent=2), encoding="utf-8")

    confirmed_audio_plan_path = get_cut_plan_edit_plan_bridge_confirmed_audio_plan_path(project.work_dir_path)
    confirmed_audio_plan_path.write_text(audio_plan.model_dump_json(indent=2), encoding="utf-8")

    confirmed_trace_path = get_cut_plan_edit_plan_bridge_confirmed_trace_path(project.work_dir_path)
    confirmed_trace_path.write_text(trace.model_dump_json(indent=2), encoding="utf-8")

    manifest = EditPlanBridgeConfirmManifest(
        project_id=project.id,
        confirmed_at=_utcnow(),
        status=EDIT_PLAN_BRIDGE_CONFIRM_STATUS_CONFIRMED,
        source_cut_plan_hash=report.source_cut_plan_hash,
        edit_plan_hash=content_hash_of_model(draft),
        bridge_audio_plan_hash=content_hash_of_model(audio_plan),
        bridge_trace_hash=content_hash_of_model(trace),
        validation_report_hash=content_hash_of_model(report),
        source_files={
            "edit_plan_draft_path": str(get_cut_plan_edit_plan_bridge_draft_path(project.work_dir_path)),
            "bridge_audio_plan_path": str(get_cut_plan_edit_plan_bridge_audio_plan_path(project.work_dir_path)),
            "bridge_trace_path": str(get_cut_plan_edit_plan_bridge_trace_path(project.work_dir_path)),
            "validation_report_path": str(
                get_cut_plan_edit_plan_bridge_validation_report_path(project.work_dir_path)
            ),
        },
        confirmed_files={
            "confirmed_edit_plan_path": str(confirmed_draft_path),
            "confirmed_bridge_audio_plan_path": str(confirmed_audio_plan_path),
            "confirmed_bridge_trace_path": str(confirmed_trace_path),
        },
        warnings=[warning.message for warning in report.warnings],
    )
    manifest_path = get_cut_plan_edit_plan_bridge_confirm_manifest_path(project.work_dir_path)
    manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")

    return manifest


def load_edit_plan_bridge_confirm_manifest(project: Project) -> EditPlanBridgeConfirmManifest | None:
    path = get_cut_plan_edit_plan_bridge_confirm_manifest_path(project.work_dir_path)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return EditPlanBridgeConfirmManifest.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None


def load_confirmed_edit_plan_bridge(project: Project) -> EditPlanDocument | None:
    path = get_cut_plan_edit_plan_bridge_confirmed_draft_path(project.work_dir_path)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return EditPlanDocument.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None


def load_confirmed_bridge_audio_plan(project: Project) -> BridgeAudioPlanDocument | None:
    path = get_cut_plan_edit_plan_bridge_confirmed_audio_plan_path(project.work_dir_path)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return BridgeAudioPlanDocument.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None


def load_confirmed_bridge_trace(project: Project) -> EditPlanBridgeTraceDocument | None:
    path = get_cut_plan_edit_plan_bridge_confirmed_trace_path(project.work_dir_path)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return EditPlanBridgeTraceDocument.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None


def unconfirm_edit_plan_bridge(project: Project) -> None:
    """Nimmt eine Bestätigung zurück, indem alle vier confirmed-Dateien
    entfernt werden (§6). Die Draft-/Trace-/Validation-Dateien im
    edit_plan_bridge/-Wurzelverzeichnis bleiben unverändert. Minimal &
    testbar: kein Archivieren, nur Löschen."""
    for path in (
        get_cut_plan_edit_plan_bridge_confirmed_draft_path(project.work_dir_path),
        get_cut_plan_edit_plan_bridge_confirmed_audio_plan_path(project.work_dir_path),
        get_cut_plan_edit_plan_bridge_confirmed_trace_path(project.work_dir_path),
        get_cut_plan_edit_plan_bridge_confirm_manifest_path(project.work_dir_path),
    ):
        if path.is_file():
            path.unlink()


def is_confirmed_edit_plan_bridge_stale(project: Project) -> bool:
    """True, wenn sich der bestätigte Cut Plan, der Bridge Draft, der Audio
    Plan, der Trace oder der Validation Report seit der Bridge-Bestätigung
    geändert haben — oder wenn gar kein bestätigter Bridge-Snapshot
    existiert. Kein Blocker — der bestätigte Bridge-Snapshot bleibt ein
    bewusster Snapshot und wird NICHT automatisch überschrieben (§7)."""
    manifest = load_edit_plan_bridge_confirm_manifest(project)
    if manifest is None:
        return True

    confirmed_cut_plan = load_confirmed_cut_plan(project)
    if confirmed_cut_plan is None:
        return True
    if content_hash_of_model(confirmed_cut_plan) != manifest.source_cut_plan_hash:
        return True
    if is_confirmed_cut_plan_stale(project, confirmed_cut_plan):
        return True

    current_draft = load_edit_plan_bridge_draft(project)
    if current_draft is None or content_hash_of_model(current_draft) != manifest.edit_plan_hash:
        return True

    current_audio_plan = load_bridge_audio_plan(project)
    if current_audio_plan is None or content_hash_of_model(current_audio_plan) != manifest.bridge_audio_plan_hash:
        return True

    current_trace = load_edit_plan_bridge_trace(project)
    if current_trace is None or content_hash_of_model(current_trace) != manifest.bridge_trace_hash:
        return True

    current_report = load_edit_plan_bridge_validation_report(project)
    if current_report is None or content_hash_of_model(current_report) != manifest.validation_report_hash:
        return True

    return False
