"""Phase 10.1: Lade-/Gate-Funktionen für das Production-EditPlan-Staging.

Prüft, ob ein bestätigter EditPlan-Bridge-Snapshot als Grundlage für ein
Production-EditPlan-Staging-Paket taugt. Schreibt in Phase 10.1 NOCH KEIN
Staging-Paket auf Disk — das folgt erst in Phase 10.2 (vollständiger
Staging-Service inkl. Schreiben)."""

from __future__ import annotations

from otio_app.analysis_models import EditPlanDocument
from otio_app.defaults import EDIT_PLAN_BRIDGE_CONFIRM_STATUS_CONFIRMED
from otio_app.models import Project
from otio_app.services.voiceover_generation.cut_plan_edit_plan_bridge import _scan_for_leaked_secrets
from otio_app.services.voiceover_generation.cut_plan_edit_plan_confirm_service import (
    is_confirmed_edit_plan_bridge_stale,
    load_confirmed_bridge_audio_plan,
    load_confirmed_bridge_trace,
    load_confirmed_edit_plan_bridge,
    load_edit_plan_bridge_confirm_manifest,
)
from otio_app.services.voiceover_generation.cut_plan_edit_plan_models import (
    BridgeAudioPlanDocument,
    EditPlanBridgeConfirmManifest,
    EditPlanBridgeTraceDocument,
)

__all__ = [
    "load_confirmed_bridge_inputs",
    "can_build_production_edit_plan_staging",
]


def load_confirmed_bridge_inputs(
    project: Project,
) -> tuple[EditPlanDocument, BridgeAudioPlanDocument, EditPlanBridgeTraceDocument, EditPlanBridgeConfirmManifest]:
    """Lädt alle vier bestätigten Bridge-Artefakte. Wirft ValueError mit
    konkreter Ursache, wenn eine Datei fehlt — Aufrufer sollten VORHER
    can_build_production_edit_plan_staging prüfen, um eine klare
    Fehlermeldung in der UI anzeigen zu können, statt diese Exception zu
    behandeln."""
    edit_plan = load_confirmed_edit_plan_bridge(project)
    if edit_plan is None:
        raise ValueError(
            "Kein bestätigter EditPlan-Bridge-Draft (edit_plan_from_cut_plan.confirmed.json) vorhanden."
        )
    audio_plan = load_confirmed_bridge_audio_plan(project)
    if audio_plan is None:
        raise ValueError("Kein bestätigter Bridge Audio Plan (bridge_audio_plan.confirmed.json) vorhanden.")
    trace = load_confirmed_bridge_trace(project)
    if trace is None:
        raise ValueError("Kein bestätigter Bridge Trace (edit_plan_bridge_trace.confirmed.json) vorhanden.")
    manifest = load_edit_plan_bridge_confirm_manifest(project)
    if manifest is None:
        raise ValueError(
            "Kein EditPlan-Bridge-Confirm-Manifest (edit_plan_bridge_confirm_manifest.json) vorhanden."
        )
    return edit_plan, audio_plan, trace, manifest


def can_build_production_edit_plan_staging(project: Project) -> tuple[bool, list[str]]:
    """Prüft alle Voraussetzungen für ein Production-EditPlan-Staging und
    gibt (eligible, reasons) zurück — reasons ist leer, wenn eligible True
    ist. Reine Funktion, kein Seiteneffekt."""
    reasons: list[str] = []

    edit_plan = load_confirmed_edit_plan_bridge(project)
    if edit_plan is None:
        reasons.append("Kein bestätigter EditPlan-Bridge-Draft (edit_plan_from_cut_plan.confirmed.json) vorhanden.")
    audio_plan = load_confirmed_bridge_audio_plan(project)
    if audio_plan is None:
        reasons.append("Kein bestätigter Bridge Audio Plan (bridge_audio_plan.confirmed.json) vorhanden.")
    trace = load_confirmed_bridge_trace(project)
    if trace is None:
        reasons.append("Kein bestätigter Bridge Trace (edit_plan_bridge_trace.confirmed.json) vorhanden.")
    manifest = load_edit_plan_bridge_confirm_manifest(project)
    if manifest is None:
        reasons.append(
            "Kein EditPlan-Bridge-Confirm-Manifest (edit_plan_bridge_confirm_manifest.json) vorhanden."
        )

    if reasons:
        return False, reasons

    if manifest.status != EDIT_PLAN_BRIDGE_CONFIRM_STATUS_CONFIRMED:
        reasons.append(f"Manifest-Status ist '{manifest.status}', erwartet '{EDIT_PLAN_BRIDGE_CONFIRM_STATUS_CONFIRMED}'.")

    if is_confirmed_edit_plan_bridge_stale(project):
        reasons.append("Der bestätigte EditPlan-Bridge-Snapshot ist veraltet. Bitte zuerst neu bestätigen.")

    leaks = _scan_for_leaked_secrets(edit_plan)
    if leaks:
        reasons.append("Bestätigter Bridge-Snapshot enthält möglicherweise sensible Daten: " + "; ".join(leaks))

    return not reasons, reasons
