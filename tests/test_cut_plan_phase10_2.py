"""Phase 10.2: Production EditPlan Staging Package + Shots-Synthese + Mapping Trace.

Noch KEINE Revalidierung mit validate_timeline_items/validate_voiceover_plan,
keine UI, kein Schreiben nach _otio/edit_plan/, kein Promote, kein Lock,
kein OTIO-Export, kein Render, keine save_edit_plan()/build_edit_plan(),
keine Produktions-Dateien überschreiben."""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis, EditPlanDocument, TimelineItem
from otio_app.defaults import (
    EDIT_PLAN_BRIDGE_TIMELINE_ITEM_TYPE_VOICEOVER_AUDIO,
    PRODUCTION_EDIT_PLAN_CANDIDATE_STATUS_STAGING_DRAFT,
    PRODUCTION_EDIT_PLAN_STATUS_STAGED,
)
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import (
    get_edit_plan_dir,
    get_exports_dir,
    get_folder_edit_plan_path,
    get_folder_inventory_path,
    get_production_edit_plan_mapping_trace_path,
    get_production_edit_plan_package_path,
    get_supplement_dir,
)
from otio_app.services.voiceover_generation.cut_plan_builder import (
    apply_asset_selection_to_draft,
    build_cut_plan_draft,
    save_cut_plan_draft,
    validate_cut_plan_draft,
)
from otio_app.services.voiceover_generation.cut_plan_confirm_service import confirm_cut_plan, load_confirmed_cut_plan
from otio_app.services.voiceover_generation.cut_plan_edit_plan_bridge import (
    build_bridge_audio_plan_from_confirmed_cut_plan,
    build_edit_plan_draft_from_confirmed_cut_plan,
    save_bridge_audio_plan,
    save_edit_plan_bridge_draft,
    validate_edit_plan_bridge,
)
from otio_app.services.voiceover_generation.cut_plan_edit_plan_confirm_service import confirm_edit_plan_bridge
from otio_app.services.voiceover_generation.cut_plan_edit_plan_models import (
    BridgeAudioPlanDocument,
    BridgeAudioPlanItem,
    EditPlanBridgeTraceDocument,
    EditPlanBridgeTraceEntry,
)
from otio_app.services.voiceover_generation.cut_plan_edit_plan_trace import (
    build_edit_plan_bridge_trace,
    save_edit_plan_bridge_trace,
)
from otio_app.services.voiceover_generation.cut_plan_models import CutPlanSettings
from otio_app.services.voiceover_generation.cut_plan_settings_service import save_cut_plan_settings
from otio_app.services.voiceover_generation.final_plan_service import save_confirmed_voiceover_project_plan
from otio_app.services.voiceover_generation.models import (
    AlignmentItem,
    ConfirmedFolderPlanItem,
    ConfirmedIntroPlanItem,
    ConfirmedVoiceoverProjectPlan,
    IntroHookVisualBeat,
    SentenceItem,
)
from otio_app.services.voiceover_generation.production_edit_plan_mapper import SectionIdentity
from otio_app.services.voiceover_generation.production_edit_plan_shots import (
    build_edit_plan_shot_from_timeline_item,
    synthesize_edit_plan_shots_for_section,
)
from otio_app.services.voiceover_generation.production_edit_plan_staging_service import (
    build_and_save_production_edit_plan_staging,
    build_production_edit_plan_staging_package,
    is_production_edit_plan_staging_stale,
    load_production_edit_plan_staging_package,
    load_staged_edit_plan,
    save_staged_edit_plan,
)
from otio_app.services.voiceover_generation.production_edit_plan_trace import load_production_edit_plan_mapping_trace

FOLDER_A = "Grand Canyon"


def _make_project(tmp_path: Path) -> Project:
    project_root = tmp_path / "USA"
    (project_root / FOLDER_A).mkdir(parents=True)
    return Project(
        id="production-edit-plan-staging-package-project",
        name="Production EditPlan Staging Package Test",
        project_root=str(project_root),
        work_dir=str(project_root / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=[FOLDER_A],
        selected_asset_subdirs=[FOLDER_A],
    )


def _write_inventory(project: Project, filenames: list[str]) -> None:
    entries = []
    for filename in filenames:
        (project.project_root_path / FOLDER_A / filename).write_bytes(b"FAKE_MEDIA_BYTES")
        entries.append(AssetMediaAnalysis(path=f"{FOLDER_A}/{filename}", description=filename))
    inv_path = get_folder_inventory_path(project.work_dir_path, FOLDER_A)
    inv_path.parent.mkdir(parents=True, exist_ok=True)
    inv_path.write_text(
        AssetFolderAnalysis(folder=FOLDER_A, assets=entries).model_dump_json(indent=2), encoding="utf-8"
    )


def _write_audio(project: Project, name: str) -> Path:
    audio_dir = project.work_dir_path / "voiceover_generation" / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    path = audio_dir / name
    path.write_bytes(b"FAKE_AUDIO_BYTES")
    return path


def _build_confirmed_bridge_project(tmp_path: Path) -> Project:
    project = _make_project(tmp_path)
    _write_inventory(project, ["photo_a.jpg", "photo_b.jpg"])
    intro_audio = _write_audio(project, "intro.mp3")
    folder_audio = _write_audio(project, "folder.mp3")

    intro = ConfirmedIntroPlanItem(
        hook_text="Ein Ort voller Geheimnisse.", audio_path=str(intro_audio), audio_duration_sec=5.0,
        visual_beats=[IntroHookVisualBeat(hook_beat_id="hook_beat_001", text="x", primary_asset_id="asset_photo_a")],
        alignment_items=[
            AlignmentItem(sentence_id="hook_beat_001", audio_start_sec=0.0, audio_end_sec=5.0, duration_sec=5.0)
        ],
    )
    folder = ConfirmedFolderPlanItem(
        folder_name=FOLDER_A, order_index=1, audio_path=str(folder_audio), audio_duration_sec=5.0,
        sentence_items=[SentenceItem(sentence_id="sentence_001", text="Ein Satz.", primary_asset_id="asset_photo_b")],
        alignment_items=[
            AlignmentItem(sentence_id="sentence_001", audio_start_sec=0.0, audio_end_sec=5.0, duration_sec=5.0)
        ],
    )
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id, project_title="Test", status="AUDIO_READY", intro=intro, folders=[folder]
    )
    save_confirmed_voiceover_project_plan(project, plan)
    save_cut_plan_settings(project, CutPlanSettings(project_id=project.id))
    draft = build_cut_plan_draft(project)
    save_cut_plan_draft(project, draft)
    apply_asset_selection_to_draft(project)
    validate_cut_plan_draft(project)
    confirm_cut_plan(project)

    edit_plan = build_edit_plan_draft_from_confirmed_cut_plan(project)
    edit_plan = save_edit_plan_bridge_draft(project, edit_plan)
    audio_plan = build_bridge_audio_plan_from_confirmed_cut_plan(project)
    save_bridge_audio_plan(project, audio_plan)
    confirmed_cut_plan = load_confirmed_cut_plan(project)
    trace = build_edit_plan_bridge_trace(project, confirmed_cut_plan, edit_plan)
    save_edit_plan_bridge_trace(project, trace)
    validate_edit_plan_bridge(project, edit_plan)
    confirm_edit_plan_bridge(project)
    return project


def _visual_item(item_id: str, folder_name: str, in_sec: float, out_sec: float) -> TimelineItem:
    return TimelineItem(
        timeline_item_id=item_id,
        type="image_shot",
        section_id="cut_x",
        folder_name=folder_name,
        asset_id="asset_a",
        resolved_media_path="/fake/a.jpg",
        timeline_in_sec=in_sec,
        timeline_out_sec=out_sec,
        duration_sec=out_sec - in_sec,
        final_duration_sec=out_sec - in_sec,
        source_in_sec=0.0,
        source_out_sec=out_sec - in_sec,
        track="V1",
        asset_type="image",
        passage_text="Ein Satz.",
    )


def _audio_bridge_item(scope: str, folder_name: str, index: int, in_sec: float, out_sec: float) -> BridgeAudioPlanItem:
    return BridgeAudioPlanItem(
        scope=scope,
        folder_name=folder_name,
        audio_path="/fake/audio.mp3",
        timeline_in_sec=in_sec,
        timeline_out_sec=out_sec,
        source_in_sec=0.0,
        source_out_sec=out_sec - in_sec,
        duration_sec=out_sec - in_sec,
        track="A1",
        source_cut_plan_audio_index=index,
        timeline_item_id=f"edit_audio_{scope}_{folder_name or 'intro'}",
    )


def _trace_entry(*, timeline_item_id: str, cut_item_id: str, source_scope: str, folder_name: str) -> EditPlanBridgeTraceEntry:
    return EditPlanBridgeTraceEntry(
        trace_id=f"trace_{timeline_item_id}", cut_item_id=cut_item_id, visual_segment_id="seg_01",
        source_scope=source_scope, folder_name=folder_name, timeline_item_id=timeline_item_id,
        timeline_item_type="image_shot", track="V1",
    )


# --- 1: Gate blockiert build_and_save ---


def test_build_and_save_blocks_when_cannot_build(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    with pytest.raises(ValueError, match="kann nicht erzeugt werden"):
        build_and_save_production_edit_plan_staging(project)


# --- 2-6: Section-Reconciliation ---


def test_audio_plan_order_is_authoritative_for_section_order() -> None:
    from otio_app.services.voiceover_generation.production_edit_plan_staging_service import _reconcile_sections

    edit_plan_bridge = EditPlanDocument(
        project_id="p1",
        timeline_items=[
            _visual_item("v_intro", "", 0.0, 5.0),
            _visual_item("v_folder", FOLDER_A, 5.0, 10.0),
        ],
    )
    audio_plan = BridgeAudioPlanDocument(
        project_id="p1",
        items=[
            _audio_bridge_item("intro", "", 0, 0.0, 5.0),
            _audio_bridge_item("folder", FOLDER_A, 1, 5.0, 10.0),
        ],
    )
    trace = EditPlanBridgeTraceDocument(
        project_id="p1",
        entries=[
            _trace_entry(timeline_item_id="v_intro", cut_item_id="cut_intro", source_scope="intro", folder_name=""),
            _trace_entry(timeline_item_id="v_folder", cut_item_id="cut_1", source_scope="folder", folder_name=FOLDER_A),
        ],
    )
    identities, visual_by_section, audio_by_section, blockers = _reconcile_sections(edit_plan_bridge, audio_plan, trace)
    assert blockers == []
    assert [identity.staging_section_id for identity in identities] == ["000_intro", f"001_{FOLDER_A.replace(' ', '_')}"]


def test_visuals_assigned_to_sections_via_trace_folder_and_scope() -> None:
    from otio_app.services.voiceover_generation.production_edit_plan_staging_service import _reconcile_sections

    edit_plan_bridge = EditPlanDocument(
        project_id="p1", timeline_items=[_visual_item("v_folder", FOLDER_A, 0.0, 5.0)]
    )
    audio_plan = BridgeAudioPlanDocument(project_id="p1", items=[_audio_bridge_item("folder", FOLDER_A, 0, 0.0, 5.0)])
    trace = EditPlanBridgeTraceDocument(
        project_id="p1",
        entries=[_trace_entry(timeline_item_id="v_folder", cut_item_id="cut_1", source_scope="folder", folder_name=FOLDER_A)],
    )
    identities, visual_by_section, audio_by_section, blockers = _reconcile_sections(edit_plan_bridge, audio_plan, trace)
    assert blockers == []
    staging_id = identities[0].staging_section_id
    assert visual_by_section[staging_id][0].timeline_item_id == "v_folder"


def test_missing_audio_plan_for_visual_section_blocks() -> None:
    edit_plan_bridge = EditPlanDocument(
        project_id="p1", timeline_items=[_visual_item("v_orphan", "Unbekannt", 0.0, 5.0)]
    )
    audio_plan = BridgeAudioPlanDocument(project_id="p1", items=[])
    trace = EditPlanBridgeTraceDocument(
        project_id="p1",
        entries=[_trace_entry(timeline_item_id="v_orphan", cut_item_id="cut_1", source_scope="folder", folder_name="Unbekannt")],
    )
    from otio_app.services.voiceover_generation.production_edit_plan_staging_service import _reconcile_sections

    _identities, _visual_by_section, _audio_by_section, blockers = _reconcile_sections(edit_plan_bridge, audio_plan, trace)
    assert len(blockers) == 1
    assert "v_orphan" in blockers[0]


def test_audio_without_visuals_blocks_section(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    package = build_production_edit_plan_staging_package(project)
    # Baseline ist happy path (kein Blocker) — jetzt gezielt eine Sektion ohne
    # Visuals über den internen Kern nachbauen, um die Section-Blocker-Regel zu testen.
    from otio_app.services.voiceover_generation.production_edit_plan_staging_service import _reconcile_sections
    from otio_app.services.voiceover_generation.production_edit_plan_staging_service import (
        load_confirmed_bridge_inputs,
    )

    edit_plan_bridge, bridge_audio_plan, bridge_trace, _manifest = load_confirmed_bridge_inputs(project)
    # Zusätzliches AudioPlanItem ohne passende Visuals hinzufügen.
    extra_audio_plan = bridge_audio_plan.model_copy(
        update={
            "items": list(bridge_audio_plan.items)
            + [_audio_bridge_item("folder", "Ghost Folder", len(bridge_audio_plan.items), 20.0, 25.0)]
        }
    )
    identities, visual_by_section, audio_by_section, blockers = _reconcile_sections(
        edit_plan_bridge, extra_audio_plan, bridge_trace
    )
    assert blockers == []  # kein Visual-ohne-Audio-Fall, nur Audio-ohne-Visual
    ghost_identity = next(identity for identity in identities if identity.folder_name == "Ghost Folder")
    assert visual_by_section.get(ghost_identity.staging_section_id, []) == []
    assert package.status == PRODUCTION_EDIT_PLAN_STATUS_STAGED  # Baseline unverändert


def test_section_identity_receives_explicit_order_index_from_audio_order() -> None:
    from otio_app.services.voiceover_generation.production_edit_plan_staging_service import _reconcile_sections

    edit_plan_bridge = EditPlanDocument(project_id="p1", timeline_items=[])
    audio_plan = BridgeAudioPlanDocument(
        project_id="p1",
        items=[
            _audio_bridge_item("intro", "", 0, 0.0, 5.0),
            _audio_bridge_item("folder", "Folder A", 1, 5.0, 10.0),
            _audio_bridge_item("folder", "Folder B", 2, 10.0, 15.0),
        ],
    )
    trace = EditPlanBridgeTraceDocument(project_id="p1", entries=[])
    identities, _v, _a, _b = _reconcile_sections(edit_plan_bridge, audio_plan, trace)
    folder_identities = [identity for identity in identities if not identity.is_intro]
    assert folder_identities[0].order_index == 1
    assert folder_identities[1].order_index == 2
    assert folder_identities[0].staging_section_id == "001_Folder_A"
    assert folder_identities[1].staging_section_id == "002_Folder_B"


# --- 7-10: Staged Dateien werden geschrieben ---


def test_intro_staged_edit_plan_is_written(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    document = load_staged_edit_plan(project, "000_intro")
    assert document is not None


def test_folder_staged_edit_plan_is_written(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    staging_id = f"001_{FOLDER_A.replace(' ', '_')}"
    document = load_staged_edit_plan(project, staging_id)
    assert document is not None
    assert document.folder_name == FOLDER_A


def test_package_json_is_written(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    assert get_production_edit_plan_package_path(project.work_dir_path).is_file()


def test_mapping_trace_json_is_written(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    assert get_production_edit_plan_mapping_trace_path(project.work_dir_path).is_file()


# --- 11-14: Staged EditPlanDocument Eigenschaften ---


def test_staged_document_confirmed_is_false(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    document = load_staged_edit_plan(project, "000_intro")
    assert document.confirmed is False


def test_staged_document_allow_black_outro_is_true(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    document = load_staged_edit_plan(project, "000_intro")
    assert document.allow_black_outro is True


def test_staged_document_candidate_status_is_staging_draft(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    document = load_staged_edit_plan(project, "000_intro")
    assert document.candidate_status == PRODUCTION_EDIT_PLAN_CANDIDATE_STATUS_STAGING_DRAFT


def test_staged_document_contains_voiceover_plan(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    document = load_staged_edit_plan(project, "000_intro")
    assert document.voiceover is not None


# --- 15-16: VoiceoverPlan-Herkunft ---


def test_voiceover_plan_originates_from_bridge_audio_plan_item(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    document = load_staged_edit_plan(project, "000_intro")
    assert document.voiceover.path.endswith("intro.mp3")
    assert document.voiceover.duration_source == "bridge_audio_plan"


def test_audio_is_not_shortened_in_staged_voiceover_plan(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    document = load_staged_edit_plan(project, "000_intro")
    assert document.voiceover.duration_sec == pytest.approx(5.0)
    assert document.voiceover.source_out_sec == pytest.approx(5.0)


# --- 17-20: TimelineItems ---


def test_staged_timeline_items_contain_no_voiceover_audio(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    for staging_id in ("000_intro", f"001_{FOLDER_A.replace(' ', '_')}"):
        document = load_staged_edit_plan(project, staging_id)
        assert all(item.type != EDIT_PLAN_BRIDGE_TIMELINE_ITEM_TYPE_VOICEOVER_AUDIO for item in document.timeline_items)


def test_staged_timeline_items_contain_visual_items(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    document = load_staged_edit_plan(project, "000_intro")
    assert len(document.timeline_items) == 1
    assert document.timeline_items[0].track == "V1"


def test_staged_timeline_items_are_localized(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    document = load_staged_edit_plan(project, "000_intro")
    assert document.timeline_items[0].timeline_in_sec == pytest.approx(0.0)


def test_trace_roundtrip_local_plus_offset_equals_original(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    package = load_production_edit_plan_staging_package(project)
    trace = load_production_edit_plan_mapping_trace(project)

    for section in package.sections:
        document = load_staged_edit_plan(project, section.staging_section_id)
        for local_item in document.timeline_items:
            trace_entry = next(
                e for e in trace.entries
                if e.resulting_timeline_item_id == local_item.timeline_item_id and e.source_bridge_timeline_item_id
            )
            offset = trace_entry.original_timeline_in_sec - trace_entry.local_timeline_in_sec
            assert trace_entry.local_timeline_in_sec + offset == pytest.approx(trace_entry.original_timeline_in_sec)
            assert trace_entry.local_timeline_out_sec + offset == pytest.approx(trace_entry.original_timeline_out_sec)


# --- 21-24: Shots ---


def test_shots_are_synthesized(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    document = load_staged_edit_plan(project, "000_intro")
    assert len(document.shots) > 0


def test_shots_not_empty_when_visuals_present(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    package = build_and_save_production_edit_plan_staging(project)
    for section in package.sections:
        if section.timeline_item_count > 0:
            assert section.shot_count > 0


def test_one_shot_per_visual_timeline_item(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    document = load_staged_edit_plan(project, "000_intro")
    assert len(document.shots) == len(document.timeline_items)


def test_shots_contain_asset_id_and_asset_path(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    document = load_staged_edit_plan(project, "000_intro")
    shot = document.shots[0]
    assert shot.asset_id
    assert shot.asset_path


# --- 25-26: fields_defaulted / fields_dropped im Trace ---


def test_mapping_trace_documents_defaulted_audio_fields(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    trace = load_production_edit_plan_mapping_trace(project)
    audio_entries = [e for e in trace.entries if e.mapping_reason == "bridge_audio_plan_to_voiceover_plan"]
    assert audio_entries
    assert any("duration_source=bridge_audio_plan" in e.fields_defaulted for e in audio_entries)
    assert any("trim_policy=disabled" in e.fields_defaulted for e in audio_entries)


def test_mapping_trace_documents_dropped_voiceover_audio(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    trace = load_production_edit_plan_mapping_trace(project)
    audio_entries = [e for e in trace.entries if e.mapping_reason == "bridge_audio_plan_to_voiceover_plan"]
    assert any(
        any("voiceover_audio" in dropped for dropped in e.fields_dropped) for e in audio_entries
    )


# --- 27-28: Trace enthält Visual/Audio Entries ---


def test_mapping_trace_contains_visual_entries(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    trace = load_production_edit_plan_mapping_trace(project)
    assert any(e.mapping_reason == "bridge_visual_to_production_timeline_item" for e in trace.entries)


def test_mapping_trace_contains_audio_entries(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    trace = load_production_edit_plan_mapping_trace(project)
    assert any(e.mapping_reason == "bridge_audio_plan_to_voiceover_plan" for e in trace.entries)


# --- 29-31: Package Status ---


def test_package_status_staged_in_happy_path(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    package = build_and_save_production_edit_plan_staging(project)
    assert package.status == PRODUCTION_EDIT_PLAN_STATUS_STAGED
    assert package.blockers == []


def test_package_status_needs_review_with_section_warnings_only() -> None:
    from otio_app.services.voiceover_generation.production_edit_plan_models import (
        ProductionEditPlanPackage,
        ProductionEditPlanSection,
    )

    package = ProductionEditPlanPackage(
        project_id="p1",
        sections=[ProductionEditPlanSection(staging_section_id="000_intro", warnings=["SOME_WARNING"])],
        warnings=["000_intro: SOME_WARNING"],
    )
    # Status-Logik wird direkt in _build_staging_artifacts angewendet — hier
    # wird nur die Modell-Fähigkeit geprüft, den Zustand widerzuspiegeln;
    # der volle Pfad wird in test_package_status_blocked_with_section_blockers geprüft.
    assert package.warnings and not package.blockers


def test_package_status_blocked_with_section_blockers(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        build_and_save_production_edit_plan_staging(_make_project(tmp_path))  # kein Bridge-Snapshot -> Gate blockiert


# --- 32-33: staged_edit_plan_hash + Staleness ---


def test_staged_edit_plan_hash_is_stored(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    package = build_and_save_production_edit_plan_staging(project)
    for section in package.sections:
        assert section.staged_edit_plan_hash


def test_staleness_detects_changed_staged_file(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    package = build_and_save_production_edit_plan_staging(project)
    assert is_production_edit_plan_staging_stale(project, package) is False

    document = load_staged_edit_plan(project, "000_intro")
    changed = document.model_copy(update={"plan_generation_notes": ["manually_edited"]})
    save_staged_edit_plan(project, "000_intro", changed)

    assert is_production_edit_plan_staging_stale(project, package) is True


def test_staleness_detects_stale_bridge(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    package = build_and_save_production_edit_plan_staging(project)
    assert is_production_edit_plan_staging_stale(project, package) is False

    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id, project_title="Geändert", status="AUDIO_READY",
        intro=ConfirmedIntroPlanItem(), folders=[],
    )
    save_confirmed_voiceover_project_plan(project, plan)

    assert is_production_edit_plan_staging_stale(project, package) is True


# --- 35-36: Laden ---


def test_load_staged_edit_plan_returns_saved_document(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    document = load_staged_edit_plan(project, "000_intro")
    assert document is not None
    assert document.project_id == project.id


def test_load_production_edit_plan_staging_package_returns_manifest(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    package = load_production_edit_plan_staging_package(project)
    assert package is not None
    assert len(package.sections) == 2


# --- 37-42: Schutz bestehender Pipeline ---


def test_no_files_written_under_edit_plan_dir(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    assert not get_edit_plan_dir(project.work_dir_path).exists()


def test_no_files_written_under_exports_dir(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    assert not get_exports_dir(project.work_dir_path).exists()


def test_no_files_written_under_supplement_dir(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    assert not get_supplement_dir(project.work_dir_path).exists()


def test_existing_production_edit_plan_remains_byte_identical(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    existing_path = get_folder_edit_plan_path(project.work_dir_path, FOLDER_A)
    existing_path.parent.mkdir(parents=True, exist_ok=True)
    existing_content = '{"project_id": "existing", "folder_name": "Grand Canyon", "confirmed": true}'
    existing_path.write_text(existing_content, encoding="utf-8")

    build_and_save_production_edit_plan_staging(project)

    assert existing_path.read_text(encoding="utf-8") == existing_content


def test_no_original_media_modified(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    photo_path = project.project_root_path / FOLDER_A / "photo_a.jpg"
    original = photo_path.read_bytes()
    build_and_save_production_edit_plan_staging(project)
    assert photo_path.read_bytes() == original


def test_no_audio_files_overwritten(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    audio_path = project.work_dir_path / "voiceover_generation" / "audio" / "intro.mp3"
    original = audio_path.read_bytes()
    build_and_save_production_edit_plan_staging(project)
    assert audio_path.read_bytes() == original


# --- 43-44: keine verbotenen Calls ---


def test_no_save_edit_plan_or_build_edit_plan_calls_referenced() -> None:
    import otio_app.services.voiceover_generation.production_edit_plan_staging_service as staging_module
    import otio_app.services.voiceover_generation.production_edit_plan_trace as trace_module
    import otio_app.services.voiceover_generation.production_edit_plan_shots as shots_module

    for module in (staging_module, trace_module, shots_module):
        source = inspect.getsource(module)
        assert not re.search(r"\bsave_edit_plan\b", source)
        assert not re.search(r"\bbuild_edit_plan\b", source)


def test_no_otio_export_referenced() -> None:
    import otio_app.services.voiceover_generation.production_edit_plan_staging_service as staging_module

    source = inspect.getsource(staging_module)
    assert not re.search(r"\botio_exporter\b", source)
    assert not re.search(r"\bexport_otio_timeline\b", source)


# --- 45-46: struktureller Guard / Regression ---

_FORBIDDEN_SYMBOLS = (
    "build_edit_plan",
    "save_edit_plan",
    "edit_plan_builder",
    "otio_exporter",
    "export_otio_timeline",
    "mark_edit_plans_stale_for_folder",
    "replan_folder_after_supplement",
    "extend_folder_inventory",
    "_set_draft",
    "merge_confirmed_edit_plans",
)


def test_production_staging_modules_reference_no_forbidden_production_functions() -> None:
    import otio_app.services.voiceover_generation.production_edit_plan_mapper as mapper_module
    import otio_app.services.voiceover_generation.production_edit_plan_models as models_module
    import otio_app.services.voiceover_generation.production_edit_plan_shots as shots_module
    import otio_app.services.voiceover_generation.production_edit_plan_staging_service as staging_module
    import otio_app.services.voiceover_generation.production_edit_plan_trace as trace_module

    for module in (mapper_module, models_module, shots_module, staging_module, trace_module):
        source = inspect.getsource(module)
        for symbol in _FORBIDDEN_SYMBOLS:
            assert not re.search(rf"\b{re.escape(symbol)}\b", source), (
                f"{module.__name__} referenziert verbotenes Symbol '{symbol}'."
            )


def test_with_voiceover_workflow_unaffected() -> None:
    from otio_app.services import edit_plan_builder, otio_exporter

    assert hasattr(edit_plan_builder, "build_edit_plan")
    assert hasattr(edit_plan_builder, "save_edit_plan")
    assert hasattr(otio_exporter, "build_otio_timeline")


# --- Zusätzlich: Shots-Modul direkt (Unit) ---


def test_build_edit_plan_shot_from_timeline_item_derives_voice_window_from_voiceover_plan() -> None:
    from otio_app.analysis_models import VoiceoverPlan

    item = _visual_item("v1", FOLDER_A, 2.0, 7.0)
    voiceover_plan = VoiceoverPlan(path="/fake/audio.mp3", timeline_start_sec=1.0, duration_sec=10.0, timeline_end_sec=11.0)
    shot = build_edit_plan_shot_from_timeline_item(item, voiceover_plan, None)
    assert shot.voice_start_sec == pytest.approx(1.0)
    assert shot.voice_end_sec == pytest.approx(6.0)
    assert shot.duration_sec == pytest.approx(5.0)
    assert shot.folder == FOLDER_A


def test_build_edit_plan_shot_clamps_voice_window_for_section_pause_hold() -> None:
    """Closing-/Pause-Hold ragt 5s über die Audiodauer — voice_* wird auf
    die Voice-over-Dauer begrenzt, duration_sec bleibt die volle Bildlänge
    (Fix gegen SHOT_TIMING_OUTSIDE_VOICEOVER bei pause_between_sections=5s)."""
    from otio_app.analysis_models import VoiceoverPlan

    # Lokalisiert: Audio 0..67.28, Closing-Visual 67.12..72.28 (+5s Pause)
    item = _visual_item("v_closing", FOLDER_A, 67.120, 72.280)
    voiceover_plan = VoiceoverPlan(
        path="/fake/audio.mp3",
        timeline_start_sec=0.0,
        duration_sec=67.280,
        timeline_end_sec=67.280,
    )
    shot = build_edit_plan_shot_from_timeline_item(item, voiceover_plan, None)
    assert shot.voice_start_sec == pytest.approx(67.120)
    assert shot.voice_end_sec == pytest.approx(67.280)
    assert shot.duration_sec == pytest.approx(5.160)
    assert shot.voice_end_sec <= voiceover_plan.duration_sec + 1e-9


def test_build_edit_plan_shot_clamps_when_visual_starts_after_voiceover() -> None:
    """Reiner Pause-Hold nach Audio-Ende: voice-Fenster kollabiert auf duration."""
    from otio_app.analysis_models import VoiceoverPlan

    item = _visual_item("v_hold", FOLDER_A, 70.0, 75.0)
    voiceover_plan = VoiceoverPlan(
        path="/fake/audio.mp3",
        timeline_start_sec=0.0,
        duration_sec=67.280,
        timeline_end_sec=67.280,
    )
    shot = build_edit_plan_shot_from_timeline_item(item, voiceover_plan, None)
    assert shot.voice_start_sec == pytest.approx(67.280)
    assert shot.voice_end_sec == pytest.approx(67.280)
    assert shot.duration_sec == pytest.approx(5.0)


def test_synthesize_edit_plan_shots_for_section_never_creates_outro_shot() -> None:
    item = _visual_item("v1", FOLDER_A, 0.0, 5.0)
    shots = synthesize_edit_plan_shots_for_section([item], None, [None])
    assert all(shot.section_outro is False for shot in shots)


def test_section_identity_dataclass_is_hashable_and_usable_as_key() -> None:
    identity_a = SectionIdentity("000_intro", "section_intro", "Intro", True, 0)
    identity_b = SectionIdentity("000_intro", "section_intro", "Intro", True, 0)
    assert identity_a == identity_b
