"""Phase 10.1: Production EditPlan Staging — Grundmodelle, Pfade, reine
Mapping-Funktionen.

Noch KEIN Schreiben nach _otio/edit_plan/, kein OTIO-Export, kein Render,
kein Promote, kein Lock, kein build_edit_plan()/save_edit_plan(), keine
shots-Synthese, keine UI, kein Überschreiben von Produktions-Dateien."""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis, TimelineItem
from otio_app.defaults import (
    EDIT_PLAN_BRIDGE_TIMELINE_ITEM_TYPE_VOICEOVER_AUDIO,
    PRODUCTION_EDIT_PLAN_CANDIDATE_STATUS_STAGING_DRAFT,
)
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import (
    get_edit_plan_dir,
    get_exports_dir,
    get_folder_inventory_path,
    get_production_edit_plan_mapping_trace_path,
    get_production_edit_plan_package_path,
    get_production_edit_plan_staging_dir,
    get_production_edit_plan_validation_report_path,
    get_staged_edit_plan_path,
    get_staged_edit_plans_dir,
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
from otio_app.services.voiceover_generation.production_edit_plan_mapper import (
    SectionIdentity,
    build_production_edit_plan_document_skeleton,
    build_section_identity_from_bridge_trace_entry,
    compute_section_start_offset,
    group_bridge_audio_plan_by_section,
    group_bridge_visual_items_by_section,
    localize_bridge_audio_item,
    localize_timeline_item,
    map_bridge_audio_to_voiceover_plan,
    map_bridge_visual_item_to_production_timeline_item,
    production_section_id_for_folder,
    production_section_id_for_intro,
    safe_staging_section_id_for_folder,
    safe_staging_section_id_for_intro,
)
from otio_app.services.voiceover_generation.production_edit_plan_models import (
    ProductionEditPlanMappingTraceDocument,
    ProductionEditPlanMappingTraceEntry,
    ProductionEditPlanPackage,
    ProductionEditPlanSection,
    ProductionEditPlanValidationError,
    ProductionEditPlanValidationReport,
)
from otio_app.services.voiceover_generation.production_edit_plan_staging_service import (
    can_build_production_edit_plan_staging,
    load_confirmed_bridge_inputs,
)

FOLDER_A = "Grand Canyon"


def _make_project(tmp_path: Path) -> Project:
    project_root = tmp_path / "USA"
    (project_root / FOLDER_A).mkdir(parents=True)
    return Project(
        id="production-edit-plan-staging-project",
        name="Production EditPlan Staging Test",
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
    audio_dir = project.language_work_dir_path / "voiceover_generation" / "audio"
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


def _visual_item(item_id: str, section_id: str, in_sec: float, out_sec: float) -> TimelineItem:
    return TimelineItem(
        timeline_item_id=item_id,
        type="image_shot",
        section_id=section_id,
        folder_name=FOLDER_A,
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


def _trace_entry(
    *, timeline_item_id: str, cut_item_id: str, visual_segment_id: str, source_scope: str, folder_name: str
) -> EditPlanBridgeTraceEntry:
    return EditPlanBridgeTraceEntry(
        trace_id=f"trace_{timeline_item_id}",
        cut_item_id=cut_item_id,
        visual_segment_id=visual_segment_id,
        source_scope=source_scope,
        folder_name=folder_name,
        timeline_item_id=timeline_item_id,
        timeline_item_type="image_shot",
        track="V1",
    )


# --- 1: Pfade ---


def test_production_staging_paths_under_cut_plan_production_edit_plan_staging(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    work_dir = project.work_dir_path
    for path in (
        get_production_edit_plan_staging_dir(work_dir),
        get_production_edit_plan_package_path(work_dir),
        get_staged_edit_plans_dir(work_dir),
        get_staged_edit_plan_path(work_dir, "000_intro"),
        get_production_edit_plan_mapping_trace_path(work_dir),
        get_production_edit_plan_validation_report_path(work_dir),
    ):
        normalized = str(path).replace("\\", "/")
        assert "voiceover_generation/cut_plan/production_edit_plan_staging" in normalized


# --- 2-4: Modell-Serialisierung ---


def test_production_edit_plan_package_round_trips(tmp_path: Path) -> None:
    package = ProductionEditPlanPackage(
        project_id="p1",
        source_bridge_manifest_hash="abc",
        sections=[
            ProductionEditPlanSection(
                staging_section_id="000_intro", production_section_id="section_intro",
                folder_name="Intro", is_intro=True,
            )
        ],
    )
    path = tmp_path / "package.json"
    path.write_text(package.model_dump_json(indent=2), encoding="utf-8")
    reloaded = ProductionEditPlanPackage.model_validate_json(path.read_text(encoding="utf-8"))
    assert reloaded.project_id == "p1"
    assert reloaded.sections[0].staging_section_id == "000_intro"


def test_production_edit_plan_mapping_trace_document_round_trips(tmp_path: Path) -> None:
    trace_doc = ProductionEditPlanMappingTraceDocument(
        project_id="p1",
        entries=[
            ProductionEditPlanMappingTraceEntry(
                trace_id="t1", source_bridge_timeline_item_id="edit_seg_1", source_cut_item_id="cut_1",
                resulting_staging_section_id="000_intro",
            )
        ],
    )
    path = tmp_path / "trace.json"
    path.write_text(trace_doc.model_dump_json(indent=2), encoding="utf-8")
    reloaded = ProductionEditPlanMappingTraceDocument.model_validate_json(path.read_text(encoding="utf-8"))
    assert reloaded.entries[0].source_cut_item_id == "cut_1"


def test_production_edit_plan_validation_report_round_trips(tmp_path: Path) -> None:
    report = ProductionEditPlanValidationReport(
        project_id="p1", status="BLOCKED",
        blockers=[ProductionEditPlanValidationError(type="MISSING_VOICEOVER_PLAN", severity="BLOCKER")],
    )
    path = tmp_path / "report.json"
    path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    reloaded = ProductionEditPlanValidationReport.model_validate_json(path.read_text(encoding="utf-8"))
    assert reloaded.status == "BLOCKED"
    assert reloaded.blockers[0].type == "MISSING_VOICEOVER_PLAN"


# --- 5-9: Section Identity ---


def test_intro_section_identity_uses_staging_section_id_000_intro() -> None:
    entry = _trace_entry(
        timeline_item_id="edit_intro_seg_01", cut_item_id="cut_intro", visual_segment_id="seg_01",
        source_scope="intro", folder_name="",
    )
    identity = build_section_identity_from_bridge_trace_entry(entry)
    assert identity.staging_section_id == "000_intro"


def test_intro_production_section_id_is_section_intro() -> None:
    entry = _trace_entry(
        timeline_item_id="edit_intro_seg_01", cut_item_id="cut_intro", visual_segment_id="seg_01",
        source_scope="intro", folder_name="",
    )
    identity = build_section_identity_from_bridge_trace_entry(entry)
    assert identity.production_section_id == "section_intro"


def test_intro_folder_name_is_intro_literal() -> None:
    entry = _trace_entry(
        timeline_item_id="edit_intro_seg_01", cut_item_id="cut_intro", visual_segment_id="seg_01",
        source_scope="intro", folder_name="",
    )
    identity = build_section_identity_from_bridge_trace_entry(entry)
    assert identity.folder_name == "Intro"
    assert identity.is_intro is True


def test_folder_section_identity_uses_order_index_in_staging_section_id() -> None:
    entry = _trace_entry(
        timeline_item_id="edit_seg_01", cut_item_id="cut_1", visual_segment_id="seg_01",
        source_scope="folder", folder_name=FOLDER_A,
    )
    identity = build_section_identity_from_bridge_trace_entry(entry, order_index=3)
    assert identity.staging_section_id == f"003_{FOLDER_A.replace(' ', '_')}"


def test_folder_production_section_id_uses_section_slug_convention() -> None:
    entry = _trace_entry(
        timeline_item_id="edit_seg_01", cut_item_id="cut_1", visual_segment_id="seg_01",
        source_scope="folder", folder_name=FOLDER_A,
    )
    identity = build_section_identity_from_bridge_trace_entry(entry, order_index=1)
    assert identity.production_section_id == f"section_{FOLDER_A.replace(' ', '_')}"
    assert safe_staging_section_id_for_folder(1, FOLDER_A) == f"001_{FOLDER_A.replace(' ', '_')}"
    assert production_section_id_for_folder(FOLDER_A) == f"section_{FOLDER_A.replace(' ', '_')}"
    assert safe_staging_section_id_for_intro() == "000_intro"
    assert production_section_id_for_intro() == "section_intro"


# --- 10-11: Grouping ---


def test_bridge_visual_items_grouped_by_trace_not_by_timeline_item_section_id() -> None:
    from otio_app.analysis_models import EditPlanDocument

    # Bridge TimelineItem.section_id ist bewusst der cut_item_id (irreführend
    # für Sektions-Gruppierung) — Gruppierung muss trotzdem korrekt sein.
    visual_intro = _visual_item("edit_intro_seg_01", section_id="cut_intro_hook_beat_001", in_sec=0.0, out_sec=5.0)
    visual_folder = _visual_item("edit_folder_seg_01", section_id="cut_001_sentence_001", in_sec=5.0, out_sec=10.0)
    audio_intro = TimelineItem(
        timeline_item_id="edit_audio_intro_intro", type=EDIT_PLAN_BRIDGE_TIMELINE_ITEM_TYPE_VOICEOVER_AUDIO,
        section_id="intro", folder_name="", track="A1", timeline_in_sec=0.0, timeline_out_sec=5.0,
        duration_sec=5.0, final_duration_sec=5.0,
    )
    edit_plan_bridge = EditPlanDocument(
        project_id="p1", timeline_items=[visual_intro, audio_intro, visual_folder]
    )
    trace = EditPlanBridgeTraceDocument(
        project_id="p1",
        entries=[
            _trace_entry(timeline_item_id="edit_intro_seg_01", cut_item_id="cut_intro_hook_beat_001",
                         visual_segment_id="seg_01", source_scope="intro", folder_name=""),
            _trace_entry(timeline_item_id="edit_folder_seg_01", cut_item_id="cut_001_sentence_001",
                         visual_segment_id="seg_01", source_scope="folder", folder_name=FOLDER_A),
        ],
    )
    grouped = group_bridge_visual_items_by_section(edit_plan_bridge, trace)
    assert set(grouped.keys()) == {"000_intro", f"001_{FOLDER_A.replace(' ', '_')}"}
    assert len(grouped["000_intro"]) == 1
    assert grouped["000_intro"][0].timeline_item_id == "edit_intro_seg_01"
    # Das voiceover_audio-Item darf in KEINER Gruppe auftauchen.
    all_grouped_ids = {item.timeline_item_id for items in grouped.values() for item in items}
    assert "edit_audio_intro_intro" not in all_grouped_ids


def test_bridge_audio_plan_grouped_by_intro_and_folder() -> None:
    audio_plan = BridgeAudioPlanDocument(
        project_id="p1",
        items=[
            _audio_bridge_item("intro", "", 0, 0.0, 5.0),
            _audio_bridge_item("folder", FOLDER_A, 1, 5.0, 10.0),
        ],
    )
    grouped = group_bridge_audio_plan_by_section(audio_plan)
    assert set(grouped.keys()) == {"000_intro", f"001_{FOLDER_A.replace(' ', '_')}"}
    assert grouped["000_intro"].scope == "intro"
    assert grouped[f"001_{FOLDER_A.replace(' ', '_')}"].folder_name == FOLDER_A


# --- 12-14: Local Time Mapping ---


def test_compute_section_start_offset_uses_minimum_of_audio_and_visual() -> None:
    visual_items = [_visual_item("v1", "s1", 6.5, 10.0), _visual_item("v2", "s1", 10.0, 12.0)]
    audio_item = _audio_bridge_item("folder", FOLDER_A, 1, 6.25, 11.25)
    offset = compute_section_start_offset(visual_items, audio_item)
    assert offset == pytest.approx(6.25)


def test_localize_timeline_item_subtracts_section_start_offset() -> None:
    item = _visual_item("v1", "cut_1", 6.25, 11.25)
    localized = localize_timeline_item(item, 6.25, "section_Grand_Canyon", FOLDER_A)
    assert localized.timeline_in_sec == pytest.approx(0.0)
    assert localized.timeline_out_sec == pytest.approx(5.0)
    assert localized.section_id == "section_Grand_Canyon"
    assert localized.folder_name == FOLDER_A


def test_roundtrip_local_plus_offset_equals_original_global() -> None:
    item = _visual_item("v1", "cut_1", 6.25, 11.25)
    offset = 6.25
    localized = localize_timeline_item(item, offset, "section_Grand_Canyon", FOLDER_A)
    assert localized.timeline_in_sec + offset == pytest.approx(item.timeline_in_sec)
    assert localized.timeline_out_sec + offset == pytest.approx(item.timeline_out_sec)

    audio_item = _audio_bridge_item("folder", FOLDER_A, 1, 6.25, 11.25)
    localized_audio = localize_bridge_audio_item(audio_item, offset)
    assert localized_audio.timeline_in_sec + offset == pytest.approx(audio_item.timeline_in_sec)
    assert localized_audio.timeline_out_sec + offset == pytest.approx(audio_item.timeline_out_sec)


# --- 15-16: Audio Mapping ---


def test_map_bridge_audio_to_voiceover_plan_copies_path_and_duration() -> None:
    audio_item = _audio_bridge_item("folder", FOLDER_A, 1, 6.25, 11.25)
    voiceover_plan = map_bridge_audio_to_voiceover_plan(audio_item, section_start_offset=6.25)
    assert voiceover_plan.path == audio_item.audio_path
    assert voiceover_plan.duration_sec == pytest.approx(audio_item.duration_sec)
    assert voiceover_plan.timeline_start_sec == pytest.approx(0.0)
    assert voiceover_plan.timeline_end_sec == pytest.approx(audio_item.duration_sec)


def test_map_bridge_audio_to_voiceover_plan_does_not_shorten_audio() -> None:
    audio_item = _audio_bridge_item("folder", FOLDER_A, 1, 6.25, 11.25)
    voiceover_plan = map_bridge_audio_to_voiceover_plan(audio_item, section_start_offset=6.25)
    assert voiceover_plan.source_in_sec == audio_item.source_in_sec
    assert voiceover_plan.source_out_sec == audio_item.source_out_sec
    assert voiceover_plan.trim_policy == "disabled"


# --- 17-19: Visual Mapping ---


def test_visual_mapping_never_accepts_voiceover_audio_item() -> None:
    audio_item = TimelineItem(
        timeline_item_id="edit_audio_1", type=EDIT_PLAN_BRIDGE_TIMELINE_ITEM_TYPE_VOICEOVER_AUDIO,
        section_id="intro", folder_name="", track="A1", timeline_in_sec=0.0, timeline_out_sec=5.0,
        duration_sec=5.0, final_duration_sec=5.0,
    )
    with pytest.raises(ValueError, match="voiceover_audio"):
        map_bridge_visual_item_to_production_timeline_item(audio_item, None, 0.0, "section_intro", "Intro")


def test_visual_mapping_sets_production_section_id() -> None:
    item = _visual_item("v1", "cut_1", 6.25, 11.25)
    mapped = map_bridge_visual_item_to_production_timeline_item(item, None, 6.25, "section_Grand_Canyon", FOLDER_A)
    assert mapped.section_id == "section_Grand_Canyon"


def test_visual_mapping_sets_folder_name() -> None:
    item = _visual_item("v1", "cut_1", 6.25, 11.25)
    mapped = map_bridge_visual_item_to_production_timeline_item(item, None, 6.25, "section_Grand_Canyon", FOLDER_A)
    assert mapped.folder_name == FOLDER_A


# --- 20-23: Document Mapping ---


def test_document_skeleton_confirmed_is_false(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    identity = SectionIdentity("000_intro", "section_intro", "Intro", True, 0)
    doc = build_production_edit_plan_document_skeleton(project, identity, [], None)
    assert doc.confirmed is False


def test_document_skeleton_allow_black_outro_is_true(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    identity = SectionIdentity("000_intro", "section_intro", "Intro", True, 0)
    doc = build_production_edit_plan_document_skeleton(project, identity, [], None)
    assert doc.allow_black_outro is True


def test_document_skeleton_shots_are_empty_in_phase10_1(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    identity = SectionIdentity("000_intro", "section_intro", "Intro", True, 0)
    doc = build_production_edit_plan_document_skeleton(project, identity, [], None)
    assert doc.shots == []


def test_document_skeleton_candidate_status_is_staging_draft(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    identity = SectionIdentity("000_intro", "section_intro", "Intro", True, 0)
    doc = build_production_edit_plan_document_skeleton(project, identity, [], None)
    assert doc.candidate_status == PRODUCTION_EDIT_PLAN_CANDIDATE_STATUS_STAGING_DRAFT


def test_document_skeleton_stamps_cut_plan_relaxed_settings(tmp_path: Path) -> None:
    """Skeleton darf nicht EditPlanSettings()-Defaults (shot_max=8, offset=1, …)
    speichern — sonst scheitert der spätere OTIO-Merge trotz Staging-PASS."""
    from otio_app.analysis_models import VoiceoverPlan

    project = _make_project(tmp_path)
    identity = SectionIdentity("001_folder_a", "section_folder_a", "Folder A", False, 1)
    voiceover = VoiceoverPlan(
        path="/audio/a.mp3",
        timeline_start_sec=0.0,
        duration_sec=12.0,
        timeline_end_sec=12.0,
        duration_source="bridge_audio_plan",
        trim_policy="disabled",
    )
    doc = build_production_edit_plan_document_skeleton(project, identity, [], voiceover)
    assert doc.settings.shot_max_sec >= 1_000_000.0
    assert doc.settings.audio_offset_sec == 0.0
    assert doc.settings.section_outro_sec == 0.0
    assert doc.settings.video_head_trim_sec == 0.0


# --- 24-26: can_build_production_edit_plan_staging ---


def test_cannot_build_staging_without_confirmed_bridge_inputs(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    eligible, reasons = can_build_production_edit_plan_staging(project)
    assert eligible is False
    assert any("Bridge" in r for r in reasons)


def test_cannot_build_staging_when_bridge_is_stale(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)

    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id, project_title="Geändert", status="AUDIO_READY",
        intro=ConfirmedIntroPlanItem(), folders=[],
    )
    save_confirmed_voiceover_project_plan(project, plan)

    eligible, reasons = can_build_production_edit_plan_staging(project)
    assert eligible is False
    assert any("veraltet" in r for r in reasons)


def test_can_build_staging_with_valid_confirmed_bridge_snapshot(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    eligible, reasons = can_build_production_edit_plan_staging(project)
    assert eligible is True
    assert reasons == []

    edit_plan, audio_plan, trace, manifest = load_confirmed_bridge_inputs(project)
    assert edit_plan is not None
    assert audio_plan is not None
    assert trace is not None
    assert manifest is not None


# --- 27-30: Schutz bestehender Pipeline ---


def test_new_modules_do_not_write_under_edit_plan_dir(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    can_build_production_edit_plan_staging(project)
    load_confirmed_bridge_inputs(project)
    assert not get_edit_plan_dir(project.language_work_dir_path).exists()


def test_new_modules_do_not_write_under_exports_dir(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    can_build_production_edit_plan_staging(project)
    assert not get_exports_dir(project.language_work_dir_path).exists()


def test_new_modules_do_not_write_under_supplement_dir(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    can_build_production_edit_plan_staging(project)
    assert not get_supplement_dir(project.language_work_dir_path).exists()


def test_no_original_media_modified(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    photo_path = project.project_root_path / FOLDER_A / "photo_a.jpg"
    original = photo_path.read_bytes()
    can_build_production_edit_plan_staging(project)
    load_confirmed_bridge_inputs(project)
    assert photo_path.read_bytes() == original


def test_no_staging_files_written_to_disk_in_phase10_1(tmp_path: Path) -> None:
    """Phase 10.1 liefert nur reine Funktionen — es darf noch KEIN
    production_edit_plan_package.json / staged edit plan auf Disk landen."""
    project = _build_confirmed_bridge_project(tmp_path)
    can_build_production_edit_plan_staging(project)
    load_confirmed_bridge_inputs(project)
    assert not get_production_edit_plan_staging_dir(project.language_work_dir_path).exists()


# --- 31-32: Struktureller Schutz / Regression ---

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
    import otio_app.services.voiceover_generation.production_edit_plan_staging_service as staging_module

    for module in (mapper_module, models_module, staging_module):
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
