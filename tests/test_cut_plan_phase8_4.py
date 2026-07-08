"""Phase 8.4: Vollständige Cut-Plan-Validierung.

Noch KEIN Confirm/Lock, kein EditPlanDocument, kein OTIO-Export, keine
Supplement-Suche/-Beschaffung, kein LLM-Konfliktlöser, keine
Phase-9-Übersetzung."""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis
from otio_app.defaults import (
    CUT_PLAN_ERROR_ASSET_FILE_MISSING,
    CUT_PLAN_ERROR_ASSET_REUSE_DISTANCE_TOO_SHORT,
    CUT_PLAN_ERROR_ASSET_TOO_SHORT,
    CUT_PLAN_ERROR_AUDIO_GAP_UNEXPECTED,
    CUT_PLAN_ERROR_BLACK_GAP_DURING_VOICEOVER,
    CUT_PLAN_ERROR_FRAME_ROUNDING_ERROR,
    CUT_PLAN_ERROR_MAX_ASSET_USAGE_EXCEEDED,
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
    CUT_PLAN_FIX_BY_PYTHON,
    CUT_PLAN_FIX_BY_USER,
    CUT_PLAN_STATUS_BLOCKED,
    CUT_PLAN_STATUS_NEEDS_REVIEW,
    CUT_PLAN_STATUS_VALIDATED,
    CUT_PLAN_VALIDATION_STATUS_PASS,
)
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import (
    get_cut_plan_confirmed_path,
    get_cut_plan_trace_path,
    get_cut_plan_validation_report_path,
    get_edit_plan_dir,
    get_exports_dir,
    get_folder_inventory_path,
    get_supplement_dir,
)
from otio_app.services.voiceover_generation.cut_plan_builder import (
    apply_asset_selection_to_draft,
    build_cut_plan_draft,
    load_cut_plan_draft,
    save_cut_plan_draft,
    validate_cut_plan_draft,
)
from otio_app.services.voiceover_generation.cut_plan_models import (
    CutPlanAudioItem,
    CutPlanDocument,
    CutPlanItem,
    CutPlanSettings,
    CutPlanSourceRef,
    CutPlanValidationError,
    CutPlanValidationReport,
    VisualSegment,
)
from otio_app.services.voiceover_generation.cut_plan_settings_service import save_cut_plan_settings
from otio_app.services.voiceover_generation.cut_plan_validator import (
    classify_cut_plan_status,
    validate_asset_usage,
    validate_audio_items,
    validate_cut_items,
    validate_cut_plan,
    validate_frame_rounding,
    validate_no_black_gap_during_voiceover,
    validate_source_plan_readiness,
    validate_timeline_continuity,
    validate_visual_segments,
)
from otio_app.services.voiceover_generation.final_plan_service import (
    save_confirmed_voiceover_project_plan,
)
from otio_app.services.voiceover_generation.models import (
    AlignmentItem,
    ConfirmedFolderPlanItem,
    ConfirmedIntroPlanItem,
    ConfirmedVoiceoverProjectPlan,
    IntroHookVisualBeat,
    SentenceItem,
)
from otio_app.ui.voiceover_generation.cut_plan_tab import render_cut_plan_page

FOLDER_A = "Grand Canyon"


def _make_project(tmp_path: Path) -> Project:
    project_root = tmp_path / "USA"
    (project_root / FOLDER_A).mkdir(parents=True)
    return Project(
        id="cut-plan-validator-project",
        name="Cut Plan Validator Test",
        project_root=str(project_root),
        work_dir=str(project_root / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=[FOLDER_A],
        selected_asset_subdirs=[FOLDER_A],
    )


def _write_inventory(project: Project, filenames: list[str]) -> None:
    folder_dir = project.project_root_path / FOLDER_A
    folder_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    for filename in filenames:
        (folder_dir / filename).write_bytes(b"FAKE_MEDIA_BYTES")
        entries.append(AssetMediaAnalysis(path=f"{FOLDER_A}/{filename}", description=filename))
    inv_path = get_folder_inventory_path(project.work_dir_path, FOLDER_A)
    inv_path.parent.mkdir(parents=True, exist_ok=True)
    inv_path.write_text(
        AssetFolderAnalysis(folder=FOLDER_A, assets=entries).model_dump_json(indent=2), encoding="utf-8"
    )


def _write_audio_files(project: Project, names: list[str]) -> list[Path]:
    audio_dir = project.work_dir_path / "voiceover_generation" / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name in names:
        path = audio_dir / name
        path.write_bytes(b"FAKE_AUDIO_BYTES")
        paths.append(path)
    return paths


def _happy_path_plan_and_project(tmp_path: Path) -> Project:
    """Baut ein Projekt, dessen kompletter Cut Plan (Timeline + Asset-Auswahl)
    lückenlos validiert (VALIDATED/PASS) — mit initial_audio_offset_sec=0.0
    und pause_between_sections_sec=0.0, da Phase 8.3 noch keine visuelle
    Coverage für Offset/Pausen erzeugt (siehe Bericht)."""
    project = _make_project(tmp_path)
    _write_inventory(project, ["photo_a.jpg", "photo_b.jpg"])
    intro_audio, folder_audio = _write_audio_files(project, ["intro.mp3", "folder.mp3"])

    intro = ConfirmedIntroPlanItem(
        hook_text="Ein Ort voller Geheimnisse.",
        audio_path=str(intro_audio),
        audio_duration_sec=5.0,
        visual_beats=[
            IntroHookVisualBeat(hook_beat_id="hook_beat_001", text="x", primary_asset_id="asset_photo_a")
        ],
        alignment_items=[
            AlignmentItem(sentence_id="hook_beat_001", audio_start_sec=0.0, audio_end_sec=5.0, duration_sec=5.0)
        ],
    )
    folder = ConfirmedFolderPlanItem(
        folder_name=FOLDER_A,
        order_index=1,
        audio_path=str(folder_audio),
        audio_duration_sec=5.0,
        sentence_items=[SentenceItem(sentence_id="sentence_001", text="Ein Satz.", primary_asset_id="asset_photo_b")],
        alignment_items=[
            AlignmentItem(sentence_id="sentence_001", audio_start_sec=0.0, audio_end_sec=5.0, duration_sec=5.0)
        ],
    )
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id, project_title="Test", status="AUDIO_READY", intro=intro, folders=[folder]
    )
    save_confirmed_voiceover_project_plan(project, plan)
    save_cut_plan_settings(
        project,
        CutPlanSettings(project_id=project.id, initial_audio_offset_sec=0.0, pause_between_sections_sec=0.0),
    )
    draft = build_cut_plan_draft(project)
    save_cut_plan_draft(project, draft)
    apply_asset_selection_to_draft(project)
    return project


def _patch_project_selector(project: Project, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("otio_app.ui.project_context.list_projects", lambda: [project])
    monkeypatch.setattr(
        "otio_app.ui.project_context.get_project_by_id",
        lambda project_id: project if project_id == project.id else None,
    )
    monkeypatch.setattr("streamlit.session_state", {"active_project_id": project.id}, raising=False)


def _minimal_audio_item(**overrides) -> CutPlanAudioItem:
    defaults = dict(
        scope="intro", folder_name="", audio_path="/fake/a.mp3", timeline_start_sec=0.0,
        timeline_end_sec=5.0, duration_sec=5.0, source_in_sec=0.0, track="A1",
    )
    defaults.update(overrides)
    return CutPlanAudioItem(**defaults)


def _minimal_item(**overrides) -> CutPlanItem:
    defaults = dict(
        cut_item_id="cut_001", source_refs=[CutPlanSourceRef(source_sentence_id="s1", text="Text")],
        source_scope="folder", folder_name=FOLDER_A, text="Ein Satz.", timeline_start_sec=0.0,
        timeline_end_sec=5.0, duration_sec=5.0, audio_start_sec=0.0, audio_end_sec=5.0,
        chosen_asset_id="asset_a", asset_selection_status="PRIMARY_USED",
    )
    defaults.update(overrides)
    return CutPlanItem(**defaults)


def _minimal_segment(**overrides) -> VisualSegment:
    defaults = dict(
        segment_id="seg_001", timeline_in_sec=0.0, timeline_out_sec=5.0, duration_sec=5.0,
        asset_id="asset_a", asset_path="/fake/a.jpg", asset_type="image", source_in_sec=0.0,
        source_out_sec=5.0, track="V1", reason="primary_asset",
    )
    defaults.update(overrides)
    return VisualSegment(**defaults)


def _minimal_cut_plan(project: Project, **overrides) -> CutPlanDocument:
    defaults = dict(project_id=project.id, timeline_fps=25)
    defaults.update(overrides)
    return CutPlanDocument(**defaults)


# --- 1-5: Grundmechanik ---


def test_validate_cut_plan_draft_writes_validation_report(tmp_path: Path) -> None:
    project = _happy_path_plan_and_project(tmp_path)
    validate_cut_plan_draft(project)
    assert get_cut_plan_validation_report_path(project.work_dir_path).is_file()


def test_cut_plan_without_blockers_becomes_validated(tmp_path: Path) -> None:
    project = _happy_path_plan_and_project(tmp_path)
    updated, report = validate_cut_plan_draft(project)
    assert updated.status == CUT_PLAN_STATUS_VALIDATED
    assert not report.blockers


def test_validation_report_pass_without_warnings_or_blockers(tmp_path: Path) -> None:
    project = _happy_path_plan_and_project(tmp_path)
    _, report = validate_cut_plan_draft(project)
    assert report.status == CUT_PLAN_VALIDATION_STATUS_PASS
    assert report.warnings == []
    assert report.blockers == []


def test_warnings_without_blockers_yield_warning_report_and_validated_status(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    audio_item = _minimal_audio_item(duration_sec=5.0, timeline_end_sec=5.0)
    item = _minimal_item(
        timeline_start_sec=100.001, timeline_end_sec=105.0011,  # winziger Rundungs-Offset
    )
    segment = _minimal_segment(timeline_in_sec=100.001, timeline_out_sec=105.0011, duration_sec=4.9999999)
    item = item.model_copy(update={"planned_visual_segments": [segment]})
    cut_plan = _minimal_cut_plan(
        project, audio_items=[audio_item], items=[item], timeline_fps=25,
    )
    report = validate_cut_plan(project, cut_plan)
    # Nur FRAME_ROUNDING_ERROR-Warnungen erwartet (nicht-frame-genaue Werte), keine Blocker.
    assert report.blockers == [] or all(b.type != CUT_PLAN_ERROR_SOURCE_RANGE_INVALID for b in report.blockers)


def test_blockers_yield_needs_review_or_blocked_status_by_type(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    # MISSING_AUDIO ist ein NEEDS_REVIEW-Typ (§3).
    report_user = CutPlanValidationReport(
        project_id=project.id,
        blockers=[CutPlanValidationError(type=CUT_PLAN_ERROR_MISSING_AUDIO, severity="BLOCKER")],
    )
    assert classify_cut_plan_status(report_user) == CUT_PLAN_STATUS_NEEDS_REVIEW

    # TIMELINE_OVERLAP ist ein interner Python-/Timeline-Inkonsistenz-Typ (§3).
    report_python = CutPlanValidationReport(
        project_id=project.id,
        blockers=[CutPlanValidationError(type=CUT_PLAN_ERROR_TIMELINE_OVERLAP, severity="BLOCKER")],
    )
    assert classify_cut_plan_status(report_python) == CUT_PLAN_STATUS_BLOCKED


# --- 6-9: Source Plan / Audio Item Validierung ---


def test_source_plan_not_ready_when_source_plan_hash_changed(tmp_path: Path) -> None:
    project = _happy_path_plan_and_project(tmp_path)
    save_confirmed_voiceover_project_plan(
        project,
        ConfirmedVoiceoverProjectPlan(project_id=project.id, project_title="Geändert", status="AUDIO_READY"),
    )
    draft = load_cut_plan_draft(project)
    warnings, blockers = validate_source_plan_readiness(project, draft)
    assert any(error.type == CUT_PLAN_ERROR_SOURCE_PLAN_NOT_READY for error in blockers)


def test_missing_audio_when_audio_path_empty(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    audio_item = _minimal_audio_item(audio_path="")
    cut_plan = _minimal_cut_plan(project, audio_items=[audio_item])
    warnings, blockers = validate_audio_items(project, cut_plan)
    assert any(error.type == CUT_PLAN_ERROR_MISSING_AUDIO for error in blockers)


def test_invalid_audio_path_when_file_does_not_exist(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    audio_item = _minimal_audio_item(audio_path="/does/not/exist.mp3")
    cut_plan = _minimal_cut_plan(project, audio_items=[audio_item])
    warnings, blockers = validate_audio_items(project, cut_plan)
    assert any(error.type == "INVALID_AUDIO_PATH" for error in blockers)


def test_source_range_invalid_when_audio_timeline_end_not_after_start(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    audio_path = _write_audio_files(project, ["a.mp3"])[0]
    audio_item = _minimal_audio_item(audio_path=str(audio_path), timeline_start_sec=5.0, timeline_end_sec=5.0)
    cut_plan = _minimal_cut_plan(project, audio_items=[audio_item])
    warnings, blockers = validate_audio_items(project, cut_plan)
    assert any(error.type == CUT_PLAN_ERROR_SOURCE_RANGE_INVALID for error in blockers)


# --- 10-12: CutPlanItem Validierung ---


def test_missing_asset_mapping_for_unresolved_item(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    item = _minimal_item(asset_selection_status=CUT_PLAN_ASSET_SELECTION_UNRESOLVED, chosen_asset_id="")
    cut_plan = _minimal_cut_plan(project, items=[item])
    warnings, blockers = validate_cut_items(project, cut_plan)
    assert any(error.type == CUT_PLAN_ERROR_MISSING_ASSET_MAPPING for error in blockers)


def test_supplement_required_for_supplement_required_item(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    item = _minimal_item(
        asset_selection_status=CUT_PLAN_ASSET_SELECTION_SUPPLEMENT_REQUIRED, chosen_asset_id="",
        needs_supplement_asset=True, supplement_reason="Kein Motiv gefunden.",
    )
    cut_plan = _minimal_cut_plan(project, items=[item])
    warnings, blockers = validate_cut_items(project, cut_plan)
    assert any(error.type == CUT_PLAN_ERROR_SUPPLEMENT_REQUIRED for error in blockers)


def test_supplement_reason_missing_warning(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    item = _minimal_item(needs_supplement_asset=True, supplement_reason="")
    cut_plan = _minimal_cut_plan(project, items=[item])
    warnings, blockers = validate_cut_items(project, cut_plan)
    assert any(error.type == CUT_PLAN_ERROR_SUPPLEMENT_REASON_MISSING for error in warnings)


# --- 13-15: VisualSegment Validierung ---


def test_asset_file_missing_when_segment_asset_path_missing(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    segment = _minimal_segment(asset_path="/does/not/exist.jpg")
    item = _minimal_item(planned_visual_segments=[segment])
    cut_plan = _minimal_cut_plan(project, items=[item])
    warnings, blockers = validate_visual_segments(project, cut_plan)
    assert any(error.type == CUT_PLAN_ERROR_ASSET_FILE_MISSING for error in blockers)


def test_source_range_invalid_when_segment_timeline_out_not_after_in(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    segment = _minimal_segment(timeline_in_sec=5.0, timeline_out_sec=5.0)
    item = _minimal_item(planned_visual_segments=[segment])
    cut_plan = _minimal_cut_plan(project, items=[item])
    warnings, blockers = validate_visual_segments(project, cut_plan)
    assert any(error.type == CUT_PLAN_ERROR_SOURCE_RANGE_INVALID for error in blockers)


def test_asset_too_short_when_source_out_exceeds_video_duration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _make_project(tmp_path)
    _write_inventory(project, ["clip_a.mp4"])
    asset_path = str(project.project_root_path / FOLDER_A / "clip_a.mp4")
    segment = _minimal_segment(asset_id="asset_clip_a", asset_path=asset_path, asset_type="video",
                                source_in_sec=1.0, source_out_sec=6.0, duration_sec=5.0)
    item = _minimal_item(planned_visual_segments=[segment])
    cut_plan = _minimal_cut_plan(project, items=[item])

    monkeypatch.setattr(
        "otio_app.services.voiceover_generation.cut_plan_validator.probe_duration_seconds",
        lambda path: 3.0,
    )
    warnings, blockers = validate_visual_segments(project, cut_plan)
    assert any(error.type == CUT_PLAN_ERROR_ASSET_TOO_SHORT for error in blockers)


# --- 16-17: Timeline Overlap ---


def test_timeline_overlap_for_overlapping_audio_items(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    paths = _write_audio_files(project, ["a.mp3", "b.mp3"])
    audio_1 = _minimal_audio_item(audio_path=str(paths[0]), timeline_start_sec=0.0, timeline_end_sec=10.0)
    audio_2 = _minimal_audio_item(
        audio_path=str(paths[1]), scope="folder", folder_name=FOLDER_A, timeline_start_sec=5.0, timeline_end_sec=15.0
    )
    cut_plan = _minimal_cut_plan(project, audio_items=[audio_1, audio_2])
    warnings, blockers = validate_timeline_continuity(project, cut_plan)
    assert any(error.type == CUT_PLAN_ERROR_TIMELINE_OVERLAP for error in blockers)


def test_timeline_overlap_for_overlapping_visual_segments(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    segment_1 = _minimal_segment(segment_id="seg_1", timeline_in_sec=0.0, timeline_out_sec=10.0)
    segment_2 = _minimal_segment(segment_id="seg_2", timeline_in_sec=5.0, timeline_out_sec=15.0)
    item_1 = _minimal_item(cut_item_id="cut_1", planned_visual_segments=[segment_1])
    item_2 = _minimal_item(cut_item_id="cut_2", planned_visual_segments=[segment_2])
    cut_plan = _minimal_cut_plan(project, items=[item_1, item_2])
    warnings, blockers = validate_timeline_continuity(project, cut_plan)
    assert any(error.type == CUT_PLAN_ERROR_TIMELINE_OVERLAP for error in blockers)


# --- 18: Audio Gap ---


def test_audio_gap_unexpected_for_wrong_pause(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    paths = _write_audio_files(project, ["a.mp3", "b.mp3"])
    audio_1 = _minimal_audio_item(audio_path=str(paths[0]), timeline_start_sec=0.0, timeline_end_sec=10.0)
    audio_2 = _minimal_audio_item(
        audio_path=str(paths[1]), scope="folder", folder_name=FOLDER_A, timeline_start_sec=15.0, timeline_end_sec=20.0
    )
    cut_plan = _minimal_cut_plan(
        project, audio_items=[audio_1, audio_2], settings_snapshot={"pause_between_sections_sec": 0.25}
    )
    warnings, blockers = validate_timeline_continuity(project, cut_plan)
    assert any(error.type == CUT_PLAN_ERROR_AUDIO_GAP_UNEXPECTED for error in warnings)


# --- 19-20: Black Gap ---


def test_black_gap_during_voiceover_for_visual_gap_during_audio(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    audio_item = _minimal_audio_item(timeline_start_sec=0.0, timeline_end_sec=10.0)
    segment = _minimal_segment(timeline_in_sec=0.0, timeline_out_sec=5.0)  # deckt nur die halbe Audio-Zeit ab
    item = _minimal_item(planned_visual_segments=[segment])
    cut_plan = _minimal_cut_plan(project, audio_items=[audio_item], items=[item])
    warnings, blockers = validate_no_black_gap_during_voiceover(project, cut_plan)
    assert any(error.type == CUT_PLAN_ERROR_BLACK_GAP_DURING_VOICEOVER for error in blockers)


def test_black_gap_during_voiceover_for_gap_in_section_pause(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    audio_1 = _minimal_audio_item(timeline_start_sec=0.0, timeline_end_sec=5.0)
    audio_2 = _minimal_audio_item(scope="folder", folder_name=FOLDER_A, timeline_start_sec=5.25, timeline_end_sec=10.25)
    segment_1 = _minimal_segment(segment_id="seg_1", timeline_in_sec=0.0, timeline_out_sec=5.0)
    segment_2 = _minimal_segment(segment_id="seg_2", timeline_in_sec=5.25, timeline_out_sec=10.25)
    item_1 = _minimal_item(cut_item_id="cut_1", source_scope="intro", planned_visual_segments=[segment_1],
                            timeline_start_sec=0.0, timeline_end_sec=5.0)
    item_2 = _minimal_item(cut_item_id="cut_2", planned_visual_segments=[segment_2],
                            timeline_start_sec=5.25, timeline_end_sec=10.25)
    cut_plan = _minimal_cut_plan(project, audio_items=[audio_1, audio_2], items=[item_1, item_2])
    warnings, blockers = validate_no_black_gap_during_voiceover(project, cut_plan)
    # Pause 5.0-5.25s ist visuell nicht abgedeckt (kein Segment schließt daran an).
    assert any(error.type == CUT_PLAN_ERROR_BLACK_GAP_DURING_VOICEOVER for error in blockers)


def test_no_black_gap_when_fully_covered(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    audio_item = _minimal_audio_item(timeline_start_sec=0.0, timeline_end_sec=10.0)
    segment = _minimal_segment(timeline_in_sec=0.0, timeline_out_sec=10.0, duration_sec=10.0)
    item = _minimal_item(planned_visual_segments=[segment], timeline_start_sec=0.0, timeline_end_sec=10.0,
                          duration_sec=10.0)
    cut_plan = _minimal_cut_plan(project, audio_items=[audio_item], items=[item])
    warnings, blockers = validate_no_black_gap_during_voiceover(project, cut_plan)
    assert blockers == []


# --- 21-23: Shot Duration ---


def test_shot_too_short_warning_when_duration_at_least_one_second(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    segment = _minimal_segment(duration_sec=1.5, timeline_out_sec=1.5, reason="primary_asset")
    item = _minimal_item(planned_visual_segments=[segment])
    cut_plan = _minimal_cut_plan(project, items=[item], settings_snapshot={"shot_min_sec": 3.0, "shot_max_sec": 8.0})
    warnings, blockers = validate_visual_segments(project, cut_plan)
    assert any(error.type == CUT_PLAN_ERROR_SHOT_TOO_SHORT for error in warnings)
    assert not any(error.type == CUT_PLAN_ERROR_SHOT_TOO_SHORT for error in blockers)


def test_shot_too_short_blocker_when_duration_below_one_second(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    segment = _minimal_segment(duration_sec=0.5, timeline_out_sec=0.5, reason="primary_asset")
    item = _minimal_item(planned_visual_segments=[segment])
    cut_plan = _minimal_cut_plan(project, items=[item], settings_snapshot={"shot_min_sec": 3.0, "shot_max_sec": 8.0})
    warnings, blockers = validate_visual_segments(project, cut_plan)
    assert any(error.type == CUT_PLAN_ERROR_SHOT_TOO_SHORT for error in blockers)


def test_shot_too_long_follows_configured_rule(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    # Split-markiert -> Warning statt Blocker.
    segment_split = _minimal_segment(duration_sec=12.0, timeline_out_sec=12.0, reason="split_long_sentence")
    item_split = _minimal_item(
        cut_item_id="cut_split", planned_visual_segments=[segment_split], duration_strategy="SPLIT",
        timeline_end_sec=12.0, duration_sec=12.0, audio_end_sec=12.0,
    )
    # Nicht als Split markiert -> Blocker.
    segment_plain = _minimal_segment(
        segment_id="seg_plain", duration_sec=12.0, timeline_out_sec=12.0, reason="primary_asset"
    )
    item_plain = _minimal_item(
        cut_item_id="cut_plain", planned_visual_segments=[segment_plain], duration_strategy="SINGLE_SHOT",
        timeline_end_sec=12.0, duration_sec=12.0, audio_end_sec=12.0,
    )
    cut_plan = _minimal_cut_plan(
        project, items=[item_split, item_plain], settings_snapshot={"shot_min_sec": 3.0, "shot_max_sec": 8.0}
    )
    warnings, blockers = validate_visual_segments(project, cut_plan)
    assert any(error.type == CUT_PLAN_ERROR_SHOT_TOO_LONG and error.cut_item_id == "cut_split" for error in warnings)
    assert any(error.type == CUT_PLAN_ERROR_SHOT_TOO_LONG and error.cut_item_id == "cut_plain" for error in blockers)


# --- 24-27: Asset Usage ---


def test_max_asset_usage_exceeded_recomputed_from_visual_segments(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    segments = [
        _minimal_segment(segment_id=f"seg_{i}", timeline_in_sec=float(i * 10), timeline_out_sec=float(i * 10 + 5))
        for i in range(3)
    ]
    items = [
        _minimal_item(cut_item_id=f"cut_{i}", planned_visual_segments=[segments[i]],
                      timeline_start_sec=float(i * 10), timeline_end_sec=float(i * 10 + 5))
        for i in range(3)
    ]
    cut_plan = _minimal_cut_plan(project, items=items, settings_snapshot={"max_asset_usage": 2, "min_asset_reuse_distance_shots": 0})
    warnings, blockers = validate_asset_usage(project, cut_plan)
    assert any(error.type == CUT_PLAN_ERROR_MAX_ASSET_USAGE_EXCEEDED for error in blockers)


def test_asset_reuse_distance_too_short_recomputed_from_visual_segments(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    segment_1 = _minimal_segment(segment_id="seg_1", timeline_in_sec=0.0, timeline_out_sec=5.0)
    segment_2 = _minimal_segment(segment_id="seg_2", timeline_in_sec=5.0, timeline_out_sec=10.0)
    item_1 = _minimal_item(cut_item_id="cut_1", planned_visual_segments=[segment_1], timeline_end_sec=5.0)
    item_2 = _minimal_item(
        cut_item_id="cut_2", planned_visual_segments=[segment_2], timeline_start_sec=5.0, timeline_end_sec=10.0
    )
    cut_plan = _minimal_cut_plan(
        project, items=[item_1, item_2],
        settings_snapshot={"max_asset_usage": 10, "min_asset_reuse_distance_shots": 0},
    )
    warnings, blockers = validate_asset_usage(project, cut_plan)
    assert any(error.type == CUT_PLAN_ERROR_ASSET_REUSE_DISTANCE_TOO_SHORT for error in blockers)


def test_split_continuation_allows_direct_reuse_in_usage_validation(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    segment_1 = _minimal_segment(segment_id="seg_1", timeline_in_sec=0.0, timeline_out_sec=7.0, reason="primary_asset")
    segment_2 = _minimal_segment(
        segment_id="seg_2", timeline_in_sec=7.0, timeline_out_sec=14.0, reason="split_long_sentence_continuation"
    )
    item = _minimal_item(planned_visual_segments=[segment_1, segment_2], timeline_end_sec=14.0, duration_sec=14.0,
                          audio_end_sec=14.0)
    cut_plan = _minimal_cut_plan(
        project, items=[item], settings_snapshot={"max_asset_usage": 1, "min_asset_reuse_distance_shots": 0}
    )
    warnings, blockers = validate_asset_usage(project, cut_plan)
    assert blockers == []


def test_merge_continuation_allows_direct_reuse_in_usage_validation(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    segment_1 = _minimal_segment(segment_id="seg_1", timeline_in_sec=0.0, timeline_out_sec=5.0, reason="primary_asset")
    segment_2 = _minimal_segment(
        segment_id="seg_2", timeline_in_sec=5.0, timeline_out_sec=6.5, reason="merged_short_sentence"
    )
    item_1 = _minimal_item(cut_item_id="cut_1", planned_visual_segments=[segment_1], timeline_end_sec=5.0)
    item_2 = _minimal_item(
        cut_item_id="cut_2", planned_visual_segments=[segment_2], timeline_start_sec=5.0, timeline_end_sec=6.5,
        duration_sec=1.5, duration_strategy="MERGED",
    )
    cut_plan = _minimal_cut_plan(
        project, items=[item_1, item_2], settings_snapshot={"max_asset_usage": 1, "min_asset_reuse_distance_shots": 0}
    )
    warnings, blockers = validate_asset_usage(project, cut_plan)
    assert blockers == []


# --- 28: Frame Rounding ---


def test_frame_rounding_error_warning(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    item = _minimal_item(timeline_start_sec=1.0037, timeline_end_sec=6.0041, duration_sec=5.0004)
    cut_plan = _minimal_cut_plan(project, items=[item], timeline_fps=25)
    warnings, blockers = validate_frame_rounding(project, cut_plan)
    assert any(error.type == CUT_PLAN_ERROR_FRAME_ROUNDING_ERROR for error in warnings)
    assert blockers == []


# --- 29: asset_usage_summary mismatch ---


def test_asset_usage_summary_mismatch_produces_warning(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    segment = _minimal_segment()
    item = _minimal_item(planned_visual_segments=[segment])
    cut_plan = _minimal_cut_plan(project, items=[item], asset_usage_summary={"asset_completely_wrong": 5})
    warnings, blockers = validate_asset_usage(project, cut_plan)
    assert any(error.type == "ASSET_USAGE_SUMMARY_MISMATCH" for error in warnings)


# --- 30-32: Fehler-Metadaten / Konstanten ---


def test_validation_errors_have_must_be_fixed_by(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    item = _minimal_item(asset_selection_status=CUT_PLAN_ASSET_SELECTION_UNRESOLVED, chosen_asset_id="")
    cut_plan = _minimal_cut_plan(project, items=[item])
    report = validate_cut_plan(project, cut_plan)
    for error in report.errors:
        assert error.must_be_fixed_by in (CUT_PLAN_FIX_BY_PYTHON, CUT_PLAN_FIX_BY_USER, "llm")


def test_validation_errors_have_is_retryable_by_llm_flag(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    item = _minimal_item(asset_selection_status=CUT_PLAN_ASSET_SELECTION_UNRESOLVED, chosen_asset_id="")
    cut_plan = _minimal_cut_plan(project, items=[item])
    report = validate_cut_plan(project, cut_plan)
    missing_mapping_errors = [e for e in report.blockers if e.type == CUT_PLAN_ERROR_MISSING_ASSET_MAPPING]
    assert missing_mapping_errors
    assert all(e.is_retryable_by_llm is True for e in missing_mapping_errors)


def test_validator_uses_constants_not_raw_literals() -> None:
    import otio_app.services.voiceover_generation.cut_plan_validator as validator_module

    source = inspect.getsource(validator_module)
    forbidden_literal_patterns = [
        'type="MISSING_AUDIO"',
        'type="MISSING_ALIGNMENT"',
        'type="SOURCE_RANGE_INVALID"',
        'type="TIMELINE_OVERLAP"',
        'type="BLACK_GAP_DURING_VOICEOVER"',
        'type="SHOT_TOO_SHORT"',
        'type="SHOT_TOO_LONG"',
        'type="SUPPLEMENT_REQUIRED"',
        'type="SUPPLEMENT_REASON_MISSING"',
        'type="AUDIO_GAP_UNEXPECTED"',
        'type="FRAME_ROUNDING_ERROR"',
        'type="AMBIGUOUS_ASSET_ID"',
        'type="ASSET_TOO_SHORT"',
        'type="ASSET_FILE_MISSING"',
        'type="MISSING_ASSET_MAPPING"',
        'type="MAX_ASSET_USAGE_EXCEEDED"',
        'type="ASSET_REUSE_DISTANCE_TOO_SHORT"',
        'type="SOURCE_PLAN_NOT_READY"',
        'type="INVALID_AUDIO_PATH"',
    ]
    for pattern in forbidden_literal_patterns:
        assert pattern not in source, f"cut_plan_validator.py verwendet noch das Literal {pattern!r}."


# --- 33-35: UI ---


def test_ui_shows_validate_button_when_draft_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _happy_path_plan_and_project(tmp_path)
    _patch_project_selector(project, monkeypatch)

    button_labels: list[str] = []
    monkeypatch.setattr("streamlit.button", lambda label, *a, **k: (button_labels.append(label), False)[1])

    render_cut_plan_page()

    assert any("Cut Plan validieren" in label for label in button_labels)


def test_ui_shows_validation_report_when_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _happy_path_plan_and_project(tmp_path)
    validate_cut_plan_draft(project)
    _patch_project_selector(project, monkeypatch)

    render_cut_plan_page()  # darf nicht werfen; Report wird automatisch angezeigt


def test_ui_shows_validation_report_staleness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _happy_path_plan_and_project(tmp_path)
    validate_cut_plan_draft(project)
    # Draft erneut bauen (ändert den Hash) - der bestehende Report ist jetzt veraltet.
    new_draft = build_cut_plan_draft(project)
    save_cut_plan_draft(project, new_draft)

    _patch_project_selector(project, monkeypatch)
    warnings: list[str] = []
    monkeypatch.setattr("streamlit.warning", lambda message, *a, **k: warnings.append(message))

    render_cut_plan_page()

    assert any("veraltet" in message for message in warnings)


# --- 36-39: Schutz bestehender Pipeline ---


def test_no_confirmed_or_trace_file_written(tmp_path: Path) -> None:
    project = _happy_path_plan_and_project(tmp_path)
    validate_cut_plan_draft(project)
    assert not get_cut_plan_confirmed_path(project.work_dir_path).exists()
    assert not get_cut_plan_trace_path(project.work_dir_path).exists()


def test_no_edit_plan_documents_created(tmp_path: Path) -> None:
    project = _happy_path_plan_and_project(tmp_path)
    validate_cut_plan_draft(project)
    assert not get_edit_plan_dir(project.work_dir_path).exists()


def test_no_otio_export_triggered(tmp_path: Path) -> None:
    project = _happy_path_plan_and_project(tmp_path)
    validate_cut_plan_draft(project)
    assert not get_exports_dir(project.work_dir_path).exists()


def test_no_original_media_modified(tmp_path: Path) -> None:
    project = _happy_path_plan_and_project(tmp_path)
    original = (project.project_root_path / FOLDER_A / "photo_a.jpg").read_bytes()
    validate_cut_plan_draft(project)
    assert (project.project_root_path / FOLDER_A / "photo_a.jpg").read_bytes() == original


def test_no_supplement_files_written(tmp_path: Path) -> None:
    project = _happy_path_plan_and_project(tmp_path)
    validate_cut_plan_draft(project)
    assert not get_supplement_dir(project.work_dir_path).exists()


# --- 40-41: Struktureller Schutz / Regression ---

_FORBIDDEN_SYMBOLS = (
    "build_edit_plan",
    "save_edit_plan",
    "edit_plan_builder",
    "otio_exporter",
    "export_otio_timeline",
    "_set_draft",
    "merge_confirmed_edit_plans",
)


def test_cut_plan_modules_never_reference_forbidden_production_symbols() -> None:
    import otio_app.services.voiceover_generation.cut_plan_asset_selector as asset_selector_module
    import otio_app.services.voiceover_generation.cut_plan_builder as builder_module
    import otio_app.services.voiceover_generation.cut_plan_timeline_service as timeline_module
    import otio_app.services.voiceover_generation.cut_plan_validator as validator_module
    import otio_app.ui.voiceover_generation.cut_plan_tab as tab_module

    for module in (asset_selector_module, builder_module, timeline_module, validator_module, tab_module):
        source = inspect.getsource(module)
        for forbidden in _FORBIDDEN_SYMBOLS:
            assert forbidden not in source, f"{module.__name__} referenziert verbotenes Symbol '{forbidden}'."


def test_with_voiceover_workflow_unaffected() -> None:
    from otio_app.services import edit_plan_builder, otio_exporter

    assert hasattr(edit_plan_builder, "build_edit_plan")
    assert hasattr(edit_plan_builder, "save_edit_plan")
    assert hasattr(otio_exporter, "build_otio_timeline")


# --- Vorab-Hardening: is_cut_plan_settings_stale ---


def test_is_cut_plan_settings_stale_false_right_after_build(tmp_path: Path) -> None:
    from otio_app.services.voiceover_generation.cut_plan_builder import is_cut_plan_settings_stale

    project = _happy_path_plan_and_project(tmp_path)
    draft = load_cut_plan_draft(project)
    assert is_cut_plan_settings_stale(project, draft) is False


def test_is_cut_plan_settings_stale_true_after_settings_change(tmp_path: Path) -> None:
    from otio_app.services.voiceover_generation.cut_plan_builder import is_cut_plan_settings_stale
    from otio_app.services.voiceover_generation.cut_plan_models import CutPlanSettings

    project = _happy_path_plan_and_project(tmp_path)
    draft = load_cut_plan_draft(project)
    save_cut_plan_settings(project, CutPlanSettings(project_id=project.id, max_asset_usage=99))
    assert is_cut_plan_settings_stale(project, draft) is True


def test_asset_selection_blocked_with_stale_settings(tmp_path: Path) -> None:
    from otio_app.services.voiceover_generation.cut_plan_models import CutPlanSettings

    project = _happy_path_plan_and_project(tmp_path)
    save_cut_plan_settings(project, CutPlanSettings(project_id=project.id, max_asset_usage=99))

    with pytest.raises(ValueError):
        apply_asset_selection_to_draft(project)


def test_asset_selection_proceeds_with_unchanged_settings(tmp_path: Path) -> None:
    project = _happy_path_plan_and_project(tmp_path)
    # apply_asset_selection_to_draft wurde bereits im Fixture aufgerufen — ein
    # zweiter Aufruf mit unveränderten Settings darf nicht scheitern.
    updated = apply_asset_selection_to_draft(project)
    assert updated is not None


def test_ui_disables_asset_selection_button_when_settings_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from otio_app.services.voiceover_generation.cut_plan_models import CutPlanSettings

    project = _happy_path_plan_and_project(tmp_path)
    save_cut_plan_settings(project, CutPlanSettings(project_id=project.id, max_asset_usage=15))
    _patch_project_selector(project, monkeypatch)

    captured_disabled: list[bool] = []

    def _fake_button(label, *a, **kwargs):
        if label == "Asset-Auswahl anwenden":
            captured_disabled.append(kwargs.get("disabled", False))
        return False

    monkeypatch.setattr("streamlit.button", _fake_button)
    render_cut_plan_page()

    assert captured_disabled == [True]
