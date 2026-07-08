"""Phase 10.1/10.2: Production-EditPlan-Staging — Gate-Funktionen,
Section-Reconciliation und Staging-Paket-Erzeugung.

Übersetzt einen bestätigten EditPlan-Bridge-Snapshot in ein isoliertes,
produktionskompatibles Staging-Paket unter
`_otio/voiceover_generation/cut_plan/production_edit_plan_staging/`.

KEIN Rebuild, KEINE Neuberechnung des Bridge-Snapshots, KEINE erneute
Asset-Auswahl, KEINE LLM-Aufrufe. Ruft NIEMALS eine der Save- oder Build-
Funktionen (weder die einzelne noch die mehrfache Variante) oder eine
andere höherstufige Funktion der bestehenden
Produktions-EditPlan-Pipeline auf — nur direkte JSON-Schreibfunktionen in
den isolierten Staging-Pfad. Bestehende Dateien unter `_otio/edit_plan/`
werden weder gelesen noch geschrieben noch verändert.

Phase 10.2 §1 (Vorab-Hardening): die Reihenfolge/Existenz der Sektionen wird
AUSSCHLIESSLICH aus `bridge_audio_plan.confirmed.json` abgeleitet (Audio-
Plan-Reihenfolge ist autoritativ) — Visual-Items werden per Bridge-Trace in
diese bereits feststehenden Sektionen einsortiert, nicht umgekehrt."""

from __future__ import annotations

import json

from otio_app.analysis_models import EditPlanDocument, TimelineItem
from otio_app.defaults import (
    AUDIO_SCOPE_FOLDER,
    AUDIO_SCOPE_INTRO,
    EDIT_PLAN_BRIDGE_CONFIRM_STATUS_CONFIRMED,
    EDIT_PLAN_BRIDGE_TIMELINE_ITEM_TYPE_VOICEOVER_AUDIO,
    PRODUCTION_EDIT_PLAN_ERROR_MISSING_VOICEOVER_PLAN,
    PRODUCTION_EDIT_PLAN_ERROR_NO_VISUAL_ITEMS_FOR_SECTION,
    PRODUCTION_EDIT_PLAN_ERROR_SHOT_SYNTHESIS_FAILED,
    PRODUCTION_EDIT_PLAN_ERROR_VOICEOVER_AUDIO_ITEM_LEAKED,
    PRODUCTION_EDIT_PLAN_ERROR_ZERO_OR_NEGATIVE_DURATION,
    PRODUCTION_EDIT_PLAN_STATUS_BLOCKED,
    PRODUCTION_EDIT_PLAN_STATUS_NEEDS_REVIEW,
    PRODUCTION_EDIT_PLAN_STATUS_STAGED,
)
from otio_app.models import Project
from otio_app.project_layout import (
    get_production_edit_plan_mapping_trace_path,
    get_production_edit_plan_package_path,
    get_staged_edit_plan_path,
)
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
    BridgeAudioPlanItem,
    EditPlanBridgeConfirmManifest,
    EditPlanBridgeTraceDocument,
    EditPlanBridgeTraceEntry,
)
from otio_app.services.voiceover_generation.llm_trace_service import content_hash_of_model
from otio_app.services.voiceover_generation.production_edit_plan_mapper import (
    SectionIdentity,
    build_production_edit_plan_document_skeleton,
    build_section_identity_from_bridge_trace_entry,
    compute_section_start_offset,
    map_bridge_audio_to_voiceover_plan,
    map_bridge_visual_item_to_production_timeline_item,
)
from otio_app.services.voiceover_generation.production_edit_plan_models import (
    ProductionEditPlanPackage,
    ProductionEditPlanSection,
)
from otio_app.services.voiceover_generation.production_edit_plan_shots import synthesize_edit_plan_shots_for_section

__all__ = [
    "load_confirmed_bridge_inputs",
    "can_build_production_edit_plan_staging",
    "build_production_edit_plan_staging_package",
    "save_production_edit_plan_staging_package",
    "load_production_edit_plan_staging_package",
    "build_and_save_production_edit_plan_staging",
    "load_staged_edit_plan",
    "save_staged_edit_plan",
    "is_production_edit_plan_staging_stale",
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
        reasons.append(
            f"Manifest-Status ist '{manifest.status}', erwartet '{EDIT_PLAN_BRIDGE_CONFIRM_STATUS_CONFIRMED}'."
        )

    if is_confirmed_edit_plan_bridge_stale(project):
        reasons.append("Der bestätigte EditPlan-Bridge-Snapshot ist veraltet. Bitte zuerst neu bestätigen.")

    leaks = _scan_for_leaked_secrets(edit_plan)
    if leaks:
        reasons.append("Bestätigter Bridge-Snapshot enthält möglicherweise sensible Daten: " + "; ".join(leaks))

    return not reasons, reasons


# --- Section-Reconciliation (Phase 10.2 §1) ---


def _transient_trace_entry(scope: str, folder_name: str) -> EditPlanBridgeTraceEntry:
    """Baut einen minimalen, rein transienten Trace-Eintrag NUR zur
    Wiederverwendung von build_section_identity_from_bridge_trace_entry —
    wird nirgends persistiert."""
    return EditPlanBridgeTraceEntry(trace_id="", source_scope=scope, folder_name=folder_name)


def _reconcile_sections(
    edit_plan_bridge: EditPlanDocument,
    bridge_audio_plan: BridgeAudioPlanDocument,
    bridge_trace: EditPlanBridgeTraceDocument,
) -> tuple[
    list[SectionIdentity],
    dict[str, list[TimelineItem]],
    dict[str, BridgeAudioPlanItem],
    list[str],
]:
    """Audio-Plan-Reihenfolge ist AUTORITATIV (§1): die Menge und Reihenfolge
    der Sektionen kommt ausschließlich aus bridge_audio_plan (Intro zuerst,
    dann Folder nach source_cut_plan_audio_index). Visual-Items werden
    anschließend per Bridge-Trace (source_scope/folder_name) in diese
    bereits feststehenden Sektionen einsortiert.

    Gibt (section_identities, visual_by_section, audio_by_section,
    reconciliation_blockers) zurück. reconciliation_blockers enthält
    Klartext-Meldungen für Visual-Items, die keiner Audio-Sektion zugeordnet
    werden konnten (§1 Regel 3) — der Aufrufer MUSS das Staging in diesem
    Fall komplett verweigern (kein halbfertiges Paket schreiben)."""
    sorted_audio_items = sorted(bridge_audio_plan.items, key=lambda item: item.source_cut_plan_audio_index)

    section_identities: list[SectionIdentity] = []
    audio_by_section: dict[str, BridgeAudioPlanItem] = {}
    identity_by_key: dict[tuple[str, str], SectionIdentity] = {}
    folder_counter = 0

    for audio_item in sorted_audio_items:
        if audio_item.scope == AUDIO_SCOPE_INTRO:
            identity = build_section_identity_from_bridge_trace_entry(
                _transient_trace_entry(AUDIO_SCOPE_INTRO, "")
            )
            key = (AUDIO_SCOPE_INTRO, "")
        else:
            folder_counter += 1
            identity = build_section_identity_from_bridge_trace_entry(
                _transient_trace_entry(AUDIO_SCOPE_FOLDER, audio_item.folder_name), order_index=folder_counter
            )
            key = (AUDIO_SCOPE_FOLDER, audio_item.folder_name)
        section_identities.append(identity)
        identity_by_key[key] = identity
        audio_by_section[identity.staging_section_id] = audio_item

    trace_by_timeline_item_id = {
        entry.timeline_item_id: entry for entry in bridge_trace.entries if entry.visual_segment_id
    }
    visual_by_section: dict[str, list[TimelineItem]] = {}
    reconciliation_blockers: list[str] = []

    for item in edit_plan_bridge.timeline_items:
        if item.track != "V1" or item.type == EDIT_PLAN_BRIDGE_TIMELINE_ITEM_TYPE_VOICEOVER_AUDIO:
            continue
        entry = trace_by_timeline_item_id.get(item.timeline_item_id)
        if entry is None:
            continue
        key = (AUDIO_SCOPE_INTRO, "") if entry.source_scope == AUDIO_SCOPE_INTRO else (AUDIO_SCOPE_FOLDER, entry.folder_name)
        identity = identity_by_key.get(key)
        if identity is None:
            reconciliation_blockers.append(
                f"Visual-TimelineItem '{item.timeline_item_id}' (Folder '{entry.folder_name}', scope "
                f"'{entry.source_scope}') hat keinen passenden BridgeAudioPlanItem — Staging wird verweigert."
            )
            continue
        visual_by_section.setdefault(identity.staging_section_id, []).append(item)

    return section_identities, visual_by_section, audio_by_section, reconciliation_blockers


def _build_staging_artifacts(project: Project) -> tuple[ProductionEditPlanPackage, dict[str, EditPlanDocument]]:
    """Interner, gemeinsamer Kern für build_production_edit_plan_staging_
    package UND build_and_save_production_edit_plan_staging — läuft EINMAL,
    damit beide Ergebnisse garantiert konsistent aus DENSELBEN Sektionen
    abgeleitet werden. Führt NUR Mapping-Sanity-Checks durch (§8) — keine
    vollständige Timeline-/Voiceover-Validierung (folgt in Phase 10.3)."""
    eligible, reasons = can_build_production_edit_plan_staging(project)
    if not eligible:
        raise ValueError("Production EditPlan Staging kann nicht erzeugt werden: " + " ".join(reasons))

    edit_plan_bridge, bridge_audio_plan, bridge_trace, manifest = load_confirmed_bridge_inputs(project)

    section_identities, visual_by_section, audio_by_section, reconciliation_blockers = _reconcile_sections(
        edit_plan_bridge, bridge_audio_plan, bridge_trace
    )
    if reconciliation_blockers:
        raise ValueError(
            "Production EditPlan Staging kann nicht erzeugt werden (Section-Reconciliation fehlgeschlagen): "
            + " ".join(reconciliation_blockers)
        )

    trace_by_timeline_item_id = {
        entry.timeline_item_id: entry for entry in bridge_trace.entries if entry.visual_segment_id
    }

    section_documents: dict[str, EditPlanDocument] = {}
    sections: list[ProductionEditPlanSection] = []
    package_warnings: list[str] = []
    package_blockers: list[str] = []

    for identity in section_identities:
        visual_items = visual_by_section.get(identity.staging_section_id, [])
        audio_item = audio_by_section.get(identity.staging_section_id)
        section_warnings: list[str] = []
        section_blockers: list[str] = []

        # §1 Regel 4: AudioPlanItem ohne Visuals -> Section wird BLOCKED
        # (kein sinnvoller Produktions-EditPlan ohne Visuals, kein Promote
        # später möglich).
        if not visual_items:
            section_blockers.append(PRODUCTION_EDIT_PLAN_ERROR_NO_VISUAL_ITEMS_FOR_SECTION)
        if audio_item is None:
            section_blockers.append(PRODUCTION_EDIT_PLAN_ERROR_MISSING_VOICEOVER_PLAN)

        offset = compute_section_start_offset(visual_items, audio_item)

        localized_items: list[TimelineItem] = []
        trace_entries_in_order: list[EditPlanBridgeTraceEntry | None] = []
        for item in visual_items:
            entry = trace_by_timeline_item_id.get(item.timeline_item_id)
            localized = map_bridge_visual_item_to_production_timeline_item(
                item, entry, offset, identity.production_section_id, identity.folder_name
            )
            localized_items.append(localized)
            trace_entries_in_order.append(entry)

        voiceover_plan = None
        if audio_item is not None:
            voiceover_plan = map_bridge_audio_to_voiceover_plan(audio_item, offset)

        # §8 Mapping-Sanity-Checks (keine vollständige Validierung).
        for localized_item in localized_items:
            if localized_item.type == EDIT_PLAN_BRIDGE_TIMELINE_ITEM_TYPE_VOICEOVER_AUDIO:
                section_blockers.append(PRODUCTION_EDIT_PLAN_ERROR_VOICEOVER_AUDIO_ITEM_LEAKED)
            if localized_item.timeline_out_sec - localized_item.timeline_in_sec <= 0:
                section_blockers.append(PRODUCTION_EDIT_PLAN_ERROR_ZERO_OR_NEGATIVE_DURATION)

        shots = synthesize_edit_plan_shots_for_section(localized_items, voiceover_plan, trace_entries_in_order)
        if localized_items and not shots:
            section_blockers.append(PRODUCTION_EDIT_PLAN_ERROR_SHOT_SYNTHESIS_FAILED)

        document = build_production_edit_plan_document_skeleton(project, identity, localized_items, voiceover_plan)
        document = document.model_copy(update={"shots": shots})

        section_documents[identity.staging_section_id] = document
        staged_path = get_staged_edit_plan_path(project.work_dir_path, identity.staging_section_id)
        sections.append(
            ProductionEditPlanSection(
                staging_section_id=identity.staging_section_id,
                production_section_id=identity.production_section_id,
                folder_name=identity.folder_name,
                is_intro=identity.is_intro,
                staged_edit_plan_path=str(staged_path),
                shot_count=len(shots),
                timeline_item_count=len(localized_items),
                has_voiceover=voiceover_plan is not None,
                staged_edit_plan_hash=content_hash_of_model(document),
                warnings=section_warnings,
                blockers=section_blockers,
            )
        )
        package_warnings.extend(f"{identity.staging_section_id}: {warning}" for warning in section_warnings)
        package_blockers.extend(f"{identity.staging_section_id}: {blocker}" for blocker in section_blockers)

    if package_blockers:
        status = PRODUCTION_EDIT_PLAN_STATUS_BLOCKED
    elif package_warnings:
        status = PRODUCTION_EDIT_PLAN_STATUS_NEEDS_REVIEW
    else:
        status = PRODUCTION_EDIT_PLAN_STATUS_STAGED

    package = ProductionEditPlanPackage(
        project_id=project.id,
        source_bridge_manifest_hash=content_hash_of_model(manifest),
        source_cut_plan_hash=manifest.source_cut_plan_hash,
        status=status,
        sections=sections,
        warnings=package_warnings,
        blockers=package_blockers,
    )

    return package, section_documents


def build_production_edit_plan_staging_package(project: Project) -> ProductionEditPlanPackage:
    """Reine Funktion — baut das Staging-Paket-Manifest (inkl. aller
    Sektions-Metadaten), speichert aber NICHTS (siehe
    build_and_save_production_edit_plan_staging für den vollständigen,
    schreibenden Ablauf)."""
    package, _section_documents = _build_staging_artifacts(project)
    return package


def save_production_edit_plan_staging_package(
    project: Project, package: ProductionEditPlanPackage
) -> ProductionEditPlanPackage:
    normalized = package.model_copy(update={"project_id": project.id})
    path = get_production_edit_plan_package_path(project.work_dir_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(normalized.model_dump_json(indent=2), encoding="utf-8")
    return normalized


def load_production_edit_plan_staging_package(project: Project) -> ProductionEditPlanPackage | None:
    path = get_production_edit_plan_package_path(project.work_dir_path)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return ProductionEditPlanPackage.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None


def save_staged_edit_plan(project: Project, staging_section_id: str, document: EditPlanDocument) -> EditPlanDocument:
    normalized = document.model_copy(update={"project_id": project.id})
    path = get_staged_edit_plan_path(project.work_dir_path, staging_section_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(normalized.model_dump_json(indent=2), encoding="utf-8")
    return normalized


def load_staged_edit_plan(project: Project, staging_section_id: str) -> EditPlanDocument | None:
    path = get_staged_edit_plan_path(project.work_dir_path, staging_section_id)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return EditPlanDocument.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None


def build_and_save_production_edit_plan_staging(project: Project) -> ProductionEditPlanPackage:
    """Vollständiger, schreibender Ablauf (§2/§5/§6): prüft can_build_
    production_edit_plan_staging zuerst, baut alle Sektions-EditPlanDocuments
    + das Paket-Manifest + den Mapping-Trace, und schreibt alle drei
    Artefakt-Arten ausschließlich unter production_edit_plan_staging/.

    Ruft NIEMALS eine der Save- oder Build-Funktionen der bestehenden
    Produktionspipeline auf — nur direkte
    JSON-Schreibfunktionen in den isolierten Staging-Pfad. Schreibt NICHT
    production_edit_plan_validation_report.json (folgt erst in Phase 10.3
    mit vollständiger Revalidierung)."""
    from otio_app.services.voiceover_generation.production_edit_plan_trace import (
        build_production_edit_plan_mapping_trace,
        save_production_edit_plan_mapping_trace,
    )

    package, section_documents = _build_staging_artifacts(project)

    for staging_section_id, document in section_documents.items():
        save_staged_edit_plan(project, staging_section_id, document)

    saved_package = save_production_edit_plan_staging_package(project, package)

    _edit_plan_bridge, bridge_audio_plan, bridge_trace, _manifest = load_confirmed_bridge_inputs(project)
    trace_document = build_production_edit_plan_mapping_trace(
        project, saved_package, section_documents, bridge_trace, bridge_audio_plan
    )
    save_production_edit_plan_mapping_trace(project, trace_document)

    return saved_package


def is_production_edit_plan_staging_stale(project: Project, package: ProductionEditPlanPackage) -> bool:
    """True, wenn sich der bestätigte Bridge-Snapshot, eine gestagte
    edit_plan.json oder der Mapping-Trace seit dem Staging geändert haben —
    oder wenn Artefakte fehlen. Kein Blocker — das Staging-Paket bleibt ein
    bewusster Snapshot und wird NICHT automatisch überschrieben."""
    if is_confirmed_edit_plan_bridge_stale(project):
        return True

    manifest = load_edit_plan_bridge_confirm_manifest(project)
    if manifest is None:
        return True
    if content_hash_of_model(manifest) != package.source_bridge_manifest_hash:
        return True
    if manifest.source_cut_plan_hash != package.source_cut_plan_hash:
        return True

    if not get_production_edit_plan_mapping_trace_path(project.work_dir_path).is_file():
        return True

    for section in package.sections:
        staged_document = load_staged_edit_plan(project, section.staging_section_id)
        if staged_document is None:
            return True
        if section.staged_edit_plan_hash and content_hash_of_model(staged_document) != section.staged_edit_plan_hash:
            return True

    return False
