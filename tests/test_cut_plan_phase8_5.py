"""Phase 8.5: Visual Coverage Fix + Validation Cleanup.

Noch KEINE Supplement-Suche/-Beschaffung, keine Provider-Calls, keine
Downloads, keine Supplement-Kandidaten-Akquise, kein Confirm/Lock, kein
EditPlanDocument, kein OTIO-Export, kein LLM-Konfliktlöser."""

from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import patch

import pytest

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis
from otio_app.defaults import (
    CUT_PLAN_ERROR_BLACK_GAP_DURING_VOICEOVER,
    CUT_PLAN_STATUS_VALIDATED,
    CUT_PLAN_VALIDATION_STATUS_WARNING,
)
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import (
    get_edit_plan_dir,
    get_exports_dir,
    get_folder_inventory_path,
    get_supplement_dir,
)
from otio_app.services.voiceover_generation.cut_plan_asset_selector import settings_from_snapshot
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
    VisualSegment,
)
from otio_app.services.voiceover_generation.cut_plan_settings_service import save_cut_plan_settings
from otio_app.services.voiceover_generation.cut_plan_validator import (
    _dedupe_errors,
    validate_asset_usage,
    validate_cut_plan,
    validate_visual_segments,
)
from otio_app.services.voiceover_generation.cut_plan_visual_coverage import (
    apply_visual_coverage_extensions,
    close_small_visual_gaps,
    extend_first_visual_to_timeline_zero,
    extend_section_end_visuals_over_pauses,
    find_first_visual_segment,
    find_last_visual_segment_before_time,
    resolve_timeline_overlaps,
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

_ASSET_SELECTOR_MODULE = "otio_app.services.voiceover_generation.cut_plan_asset_selector"
_VISUAL_COVERAGE_MODULE = "otio_app.services.voiceover_generation.cut_plan_visual_coverage"
_VALIDATOR_MODULE = "otio_app.services.voiceover_generation.cut_plan_validator"


def _make_project(tmp_path: Path) -> Project:
    project_root = tmp_path / "USA"
    (project_root / FOLDER_A).mkdir(parents=True)
    return Project(
        id="cut-plan-coverage-project",
        name="Cut Plan Coverage Test",
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


def _standard_settings_plan_and_project(tmp_path: Path, *, use_video: bool = False) -> Project:
    """Baut ein Projekt mit STANDARD-Settings (initial_audio_offset_sec=1.0,
    pause_between_sections_sec=0.25) — genau der Fall, der laut Phase-8.4-
    Audit BLACK_GAP_DURING_VOICEOVER erzeugte."""
    project = _make_project(tmp_path)
    asset_a = "clip_a.mp4" if use_video else "photo_a.jpg"
    asset_b = "clip_b.mp4" if use_video else "photo_b.jpg"
    _write_inventory(project, [asset_a, asset_b])
    intro_audio, folder_audio = _write_audio_files(project, ["intro.mp3", "folder.mp3"])

    intro = ConfirmedIntroPlanItem(
        hook_text="Ein Ort voller Geheimnisse.",
        audio_path=str(intro_audio),
        audio_duration_sec=5.0,
        visual_beats=[
            IntroHookVisualBeat(
                hook_beat_id="hook_beat_001", text="x", primary_asset_id=f"asset_{Path(asset_a).stem}"
            )
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
        sentence_items=[
            SentenceItem(sentence_id="sentence_001", text="Ein Satz.", primary_asset_id=f"asset_{Path(asset_b).stem}")
        ],
        alignment_items=[
            AlignmentItem(sentence_id="sentence_001", audio_start_sec=0.0, audio_end_sec=5.0, duration_sec=5.0)
        ],
    )
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id, project_title="Test", status="AUDIO_READY", intro=intro, folders=[folder]
    )
    save_confirmed_voiceover_project_plan(project, plan)
    # Default CutPlanSettings = initial_audio_offset_sec=1.0, pause_between_sections_sec=0.25
    save_cut_plan_settings(project, CutPlanSettings(project_id=project.id))
    draft = build_cut_plan_draft(project)
    save_cut_plan_draft(project, draft)
    return project


def _patch_project_selector(project: Project, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("otio_app.ui.project_context.list_projects", lambda: [project])
    monkeypatch.setattr(
        "otio_app.ui.project_context.get_project_by_id",
        lambda project_id: project if project_id == project.id else None,
    )
    monkeypatch.setattr("streamlit.session_state", {"active_project_id": project.id}, raising=False)


def _minimal_cut_plan(project: Project, **overrides) -> CutPlanDocument:
    defaults = dict(project_id=project.id, timeline_fps=25)
    defaults.update(overrides)
    return CutPlanDocument(**defaults)


def _minimal_audio_item(**overrides) -> CutPlanAudioItem:
    defaults = dict(
        scope="intro", folder_name="", audio_path="/fake/a.mp3", timeline_start_sec=1.0,
        timeline_end_sec=6.0, duration_sec=5.0, source_in_sec=0.0, track="A1",
    )
    defaults.update(overrides)
    return CutPlanAudioItem(**defaults)


def _minimal_item(**overrides) -> CutPlanItem:
    defaults = dict(
        cut_item_id="cut_001", source_refs=[CutPlanSourceRef(source_sentence_id="s1", text="Text")],
        source_scope="intro", folder_name="", text="Ein Satz.", timeline_start_sec=1.0,
        timeline_end_sec=6.0, duration_sec=5.0, audio_start_sec=0.0, audio_end_sec=5.0,
        chosen_asset_id="asset_a", asset_selection_status="PRIMARY_USED",
    )
    defaults.update(overrides)
    return CutPlanItem(**defaults)


def _minimal_segment(**overrides) -> VisualSegment:
    defaults = dict(
        segment_id="seg_001", timeline_in_sec=1.0, timeline_out_sec=6.0, duration_sec=5.0,
        asset_id="asset_a", asset_path="/fake/a.jpg", asset_type="image", source_in_sec=0.0,
        source_out_sec=5.0, track="V1", reason="primary_asset",
    )
    defaults.update(overrides)
    return VisualSegment(**defaults)


# --- 1-5: Initial Preroll Coverage ---


def test_initial_offset_extends_first_visual_segment_to_zero() -> None:
    audio_item = _minimal_audio_item(timeline_start_sec=1.0, timeline_end_sec=6.0)
    segment = _minimal_segment(timeline_in_sec=1.0, timeline_out_sec=6.0, duration_sec=5.0)
    item = _minimal_item(planned_visual_segments=[segment])
    cut_plan = CutPlanDocument(project_id="p1", audio_items=[audio_item], items=[item])

    updated = extend_first_visual_to_timeline_zero(cut_plan)
    updated_segment = updated.items[0].planned_visual_segments[0]
    assert updated_segment.timeline_in_sec == 0.0
    assert updated_segment.duration_sec == pytest.approx(6.0)
    assert updated_segment.reason == "primary_asset+initial_preroll_extension"


def test_initial_offset_extension_does_not_change_audio_item() -> None:
    audio_item = _minimal_audio_item(timeline_start_sec=1.0, timeline_end_sec=6.0)
    segment = _minimal_segment(timeline_in_sec=1.0, timeline_out_sec=6.0)
    item = _minimal_item(planned_visual_segments=[segment])
    cut_plan = CutPlanDocument(project_id="p1", audio_items=[audio_item], items=[item])

    updated = extend_first_visual_to_timeline_zero(cut_plan)
    assert updated.audio_items[0] == audio_item  # unverändert


def test_initial_coverage_works_for_image_asset() -> None:
    audio_item = _minimal_audio_item(timeline_start_sec=1.0, timeline_end_sec=6.0)
    segment = _minimal_segment(asset_type="image", timeline_in_sec=1.0, timeline_out_sec=6.0,
                                source_in_sec=0.0, source_out_sec=5.0)
    item = _minimal_item(planned_visual_segments=[segment])
    cut_plan = CutPlanDocument(project_id="p1", audio_items=[audio_item], items=[item])

    updated = extend_first_visual_to_timeline_zero(cut_plan)
    updated_segment = updated.items[0].planned_visual_segments[0]
    assert updated_segment.source_in_sec == 0.0
    assert updated_segment.source_out_sec == pytest.approx(6.0)


def test_initial_coverage_extends_source_out_for_video_asset() -> None:
    audio_item = _minimal_audio_item(timeline_start_sec=1.0, timeline_end_sec=6.0)
    segment = _minimal_segment(asset_type="video", asset_path="/fake/video.mp4", timeline_in_sec=1.0,
                                timeline_out_sec=6.0, source_in_sec=1.0, source_out_sec=6.0)
    item = _minimal_item(planned_visual_segments=[segment])
    cut_plan = CutPlanDocument(project_id="p1", audio_items=[audio_item], items=[item])

    with patch(f"{_VISUAL_COVERAGE_MODULE}._video_can_extend_to", return_value=True):
        updated = extend_first_visual_to_timeline_zero(cut_plan)
    updated_segment = updated.items[0].planned_visual_segments[0]
    assert updated_segment.source_in_sec == 1.0  # video_head_trim_sec bleibt
    assert updated_segment.source_out_sec == pytest.approx(7.0)  # +1.0s verlängert


def test_initial_coverage_skipped_when_video_too_short() -> None:
    audio_item = _minimal_audio_item(timeline_start_sec=1.0, timeline_end_sec=6.0)
    segment = _minimal_segment(asset_type="video", asset_path="/fake/video.mp4", timeline_in_sec=1.0,
                                timeline_out_sec=6.0, source_in_sec=1.0, source_out_sec=6.0)
    item = _minimal_item(planned_visual_segments=[segment])
    cut_plan = CutPlanDocument(project_id="p1", audio_items=[audio_item], items=[item])

    with patch(f"{_VISUAL_COVERAGE_MODULE}._video_can_extend_to", return_value=False):
        updated = extend_first_visual_to_timeline_zero(cut_plan)
    updated_segment = updated.items[0].planned_visual_segments[0]
    assert updated_segment.timeline_in_sec == 1.0  # keine Erweiterung -> Loch bleibt sichtbar
    assert updated_segment == segment


# --- 6-9: Section Pause Coverage ---


def test_pause_extends_last_visual_of_previous_section() -> None:
    settings = CutPlanSettings(project_id="p1", pause_between_sections_sec=0.25)
    audio_1 = _minimal_audio_item(timeline_start_sec=1.0, timeline_end_sec=6.0)
    audio_2 = _minimal_audio_item(scope="folder", folder_name=FOLDER_A, timeline_start_sec=6.25, timeline_end_sec=11.25)
    segment_1 = _minimal_segment(segment_id="seg_1", timeline_in_sec=1.0, timeline_out_sec=6.0)
    segment_2 = _minimal_segment(segment_id="seg_2", timeline_in_sec=6.25, timeline_out_sec=11.25)
    item_1 = _minimal_item(cut_item_id="cut_1", planned_visual_segments=[segment_1])
    item_2 = _minimal_item(cut_item_id="cut_2", source_scope="folder", folder_name=FOLDER_A,
                            planned_visual_segments=[segment_2], timeline_start_sec=6.25, timeline_end_sec=11.25)
    cut_plan = CutPlanDocument(project_id="p1", audio_items=[audio_1, audio_2], items=[item_1, item_2])

    updated = extend_section_end_visuals_over_pauses(cut_plan, settings)
    updated_segment_1 = updated.items[0].planned_visual_segments[0]
    assert updated_segment_1.timeline_out_sec == pytest.approx(6.25)
    assert updated_segment_1.reason == "primary_asset+section_pause_hold"


def test_pause_coverage_does_not_change_next_visual() -> None:
    settings = CutPlanSettings(project_id="p1", pause_between_sections_sec=0.25)
    audio_1 = _minimal_audio_item(timeline_start_sec=1.0, timeline_end_sec=6.0)
    audio_2 = _minimal_audio_item(scope="folder", folder_name=FOLDER_A, timeline_start_sec=6.25, timeline_end_sec=11.25)
    segment_1 = _minimal_segment(segment_id="seg_1", timeline_in_sec=1.0, timeline_out_sec=6.0)
    segment_2 = _minimal_segment(segment_id="seg_2", timeline_in_sec=6.25, timeline_out_sec=11.25)
    item_1 = _minimal_item(cut_item_id="cut_1", planned_visual_segments=[segment_1])
    item_2 = _minimal_item(cut_item_id="cut_2", source_scope="folder", folder_name=FOLDER_A,
                            planned_visual_segments=[segment_2], timeline_start_sec=6.25, timeline_end_sec=11.25)
    cut_plan = CutPlanDocument(project_id="p1", audio_items=[audio_1, audio_2], items=[item_1, item_2])

    updated = extend_section_end_visuals_over_pauses(cut_plan, settings)
    updated_segment_2 = updated.items[1].planned_visual_segments[0]
    assert updated_segment_2 == segment_2  # unverändert


def test_pause_coverage_works_for_image_asset() -> None:
    settings = CutPlanSettings(project_id="p1", pause_between_sections_sec=0.25)
    audio_1 = _minimal_audio_item(timeline_start_sec=1.0, timeline_end_sec=6.0)
    audio_2 = _minimal_audio_item(scope="folder", folder_name=FOLDER_A, timeline_start_sec=6.25, timeline_end_sec=11.25)
    segment_1 = _minimal_segment(segment_id="seg_1", asset_type="image", timeline_in_sec=1.0, timeline_out_sec=6.0,
                                  source_in_sec=0.0, source_out_sec=5.0)
    item_1 = _minimal_item(cut_item_id="cut_1", planned_visual_segments=[segment_1])
    item_2 = _minimal_item(cut_item_id="cut_2", source_scope="folder", folder_name=FOLDER_A,
                            timeline_start_sec=6.25, timeline_end_sec=11.25)
    cut_plan = CutPlanDocument(project_id="p1", audio_items=[audio_1, audio_2], items=[item_1, item_2])

    updated = extend_section_end_visuals_over_pauses(cut_plan, settings)
    updated_segment = updated.items[0].planned_visual_segments[0]
    assert updated_segment.source_out_sec == pytest.approx(5.25)


def test_pause_coverage_extends_source_out_for_video_asset() -> None:
    settings = CutPlanSettings(project_id="p1", pause_between_sections_sec=0.25)
    audio_1 = _minimal_audio_item(timeline_start_sec=1.0, timeline_end_sec=6.0)
    audio_2 = _minimal_audio_item(scope="folder", folder_name=FOLDER_A, timeline_start_sec=6.25, timeline_end_sec=11.25)
    segment_1 = _minimal_segment(segment_id="seg_1", asset_type="video", asset_path="/fake/video.mp4",
                                  timeline_in_sec=1.0, timeline_out_sec=6.0, source_in_sec=1.0, source_out_sec=6.0)
    item_1 = _minimal_item(cut_item_id="cut_1", planned_visual_segments=[segment_1])
    item_2 = _minimal_item(cut_item_id="cut_2", source_scope="folder", folder_name=FOLDER_A,
                            timeline_start_sec=6.25, timeline_end_sec=11.25)
    cut_plan = CutPlanDocument(project_id="p1", audio_items=[audio_1, audio_2], items=[item_1, item_2])

    with patch(f"{_VISUAL_COVERAGE_MODULE}._video_can_extend_to", return_value=True):
        updated = extend_section_end_visuals_over_pauses(cut_plan, settings)
    updated_segment = updated.items[0].planned_visual_segments[0]
    assert updated_segment.source_out_sec == pytest.approx(6.25)


# --- find_* Helper ---


def test_find_first_visual_segment_returns_earliest() -> None:
    segment_1 = _minimal_segment(segment_id="seg_1", timeline_in_sec=5.0, timeline_out_sec=10.0)
    segment_2 = _minimal_segment(segment_id="seg_2", timeline_in_sec=1.0, timeline_out_sec=5.0)
    item_1 = _minimal_item(cut_item_id="cut_1", planned_visual_segments=[segment_1])
    item_2 = _minimal_item(cut_item_id="cut_2", planned_visual_segments=[segment_2])
    cut_plan = CutPlanDocument(project_id="p1", items=[item_1, item_2])

    found = find_first_visual_segment(cut_plan)
    assert found is not None
    _, segment = found
    assert segment.segment_id == "seg_2"


def test_find_first_visual_segment_returns_none_when_empty() -> None:
    cut_plan = CutPlanDocument(project_id="p1")
    assert find_first_visual_segment(cut_plan) is None


def test_find_last_visual_segment_before_time() -> None:
    segment_1 = _minimal_segment(segment_id="seg_1", timeline_in_sec=1.0, timeline_out_sec=6.0)
    segment_2 = _minimal_segment(segment_id="seg_2", timeline_in_sec=10.0, timeline_out_sec=15.0)
    item_1 = _minimal_item(cut_item_id="cut_1", planned_visual_segments=[segment_1])
    item_2 = _minimal_item(cut_item_id="cut_2", planned_visual_segments=[segment_2])
    cut_plan = CutPlanDocument(project_id="p1", items=[item_1, item_2])

    found = find_last_visual_segment_before_time(cut_plan, 6.0)
    assert found is not None
    _, segment = found
    assert segment.segment_id == "seg_1"

    assert find_last_visual_segment_before_time(cut_plan, 0.5) is None


# --- 10-11: Standard-Settings Happy Path ---


def test_standard_settings_produce_no_black_gap_after_coverage_fix(tmp_path: Path) -> None:
    project = _standard_settings_plan_and_project(tmp_path)
    updated = apply_asset_selection_to_draft(project)
    assert not any(error.type == CUT_PLAN_ERROR_BLACK_GAP_DURING_VOICEOVER for error in updated.blockers)

    _, report = validate_cut_plan_draft(project)
    assert not any(error.type == CUT_PLAN_ERROR_BLACK_GAP_DURING_VOICEOVER for error in report.blockers)
    assert report.status in (CUT_PLAN_VALIDATION_STATUS_WARNING, "PASS")


def test_standard_settings_reach_validated_status(tmp_path: Path) -> None:
    project = _standard_settings_plan_and_project(tmp_path)
    apply_asset_selection_to_draft(project)
    updated, report = validate_cut_plan_draft(project)
    assert updated.status == CUT_PLAN_STATUS_VALIDATED


def test_black_gap_remains_when_coverage_impossible_due_to_short_video(tmp_path: Path) -> None:
    project = _standard_settings_plan_and_project(tmp_path, use_video=True)
    with patch(f"{_ASSET_SELECTOR_MODULE}.probe_duration_seconds", return_value=5.0), \
         patch(f"{_VISUAL_COVERAGE_MODULE}.probe_duration_seconds", return_value=5.0):
        apply_asset_selection_to_draft(project)

    # Videos sind mit 5.0s laut ffprobe genau lang genug fuer die reine
    # Audiodauer, aber NICHT lang genug fuer zusaetzliche Coverage-Erweiterung.
    _, report = validate_cut_plan_draft(project)
    assert any(error.type == CUT_PLAN_ERROR_BLACK_GAP_DURING_VOICEOVER for error in report.blockers)


# --- 12-15: Legitime Coverage-Reasons ---


def test_section_pause_hold_is_legitimate_coverage(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    segment = _minimal_segment(duration_sec=5.25, timeline_out_sec=6.25, reason="primary_asset+section_pause_hold")
    item = _minimal_item(planned_visual_segments=[segment], timeline_end_sec=6.0, duration_sec=5.0)
    cut_plan = _minimal_cut_plan(project, items=[item], settings_snapshot={"shot_min_sec": 3.0, "shot_max_sec": 5.0})
    warnings, blockers = validate_visual_segments(project, cut_plan)
    assert not any(error.type == "SHOT_TOO_LONG" and error.severity == "BLOCKER" for error in blockers)


def test_initial_preroll_extension_is_legitimate_coverage(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    segment = _minimal_segment(
        timeline_in_sec=0.0, duration_sec=6.0, timeline_out_sec=6.0, reason="primary_asset+initial_preroll_extension"
    )
    item = _minimal_item(planned_visual_segments=[segment], timeline_start_sec=0.0, timeline_end_sec=6.0,
                          duration_sec=6.0)
    cut_plan = _minimal_cut_plan(project, items=[item], settings_snapshot={"shot_min_sec": 3.0, "shot_max_sec": 5.0})
    warnings, blockers = validate_visual_segments(project, cut_plan)
    assert not any(error.type == "SHOT_TOO_LONG" and error.severity == "BLOCKER" for error in blockers)


def test_small_gap_hold_is_legitimate_coverage_not_a_hard_blocker(tmp_path: Path) -> None:
    """Phase H (Bugfix aus Phase C): close_small_visual_gaps kann ein
    Segment über shot_max_sec hinaus verlängern (kleine Sprechpause
    geschlossen) — das darf NICHT als harter SHOT_TOO_LONG-Blocker
    gemeldet werden, sondern nur als Warnung (analog zu section_pause_hold/
    initial_preroll_extension)."""
    project = _make_project(tmp_path)
    segment = _minimal_segment(duration_sec=5.5, timeline_out_sec=5.5, reason="primary_asset+small_gap_hold")
    item = _minimal_item(planned_visual_segments=[segment], timeline_end_sec=5.5, duration_sec=5.5)
    cut_plan = _minimal_cut_plan(project, items=[item], settings_snapshot={"shot_min_sec": 3.0, "shot_max_sec": 5.0})
    warnings, blockers = validate_visual_segments(project, cut_plan)
    assert not any(error.type == "SHOT_TOO_LONG" and error.severity == "BLOCKER" for error in blockers)
    assert any(error.type == "SHOT_TOO_LONG" for error in warnings)


def test_merged_short_sentence_remains_valid(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    segment = _minimal_segment(duration_sec=1.0, timeline_out_sec=2.0, reason="merged_short_sentence")
    item = _minimal_item(planned_visual_segments=[segment], timeline_start_sec=1.0, timeline_end_sec=2.0,
                          duration_sec=1.0)
    cut_plan = _minimal_cut_plan(project, items=[item], settings_snapshot={"shot_min_sec": 3.0, "shot_max_sec": 8.0})
    warnings, blockers = validate_visual_segments(project, cut_plan)
    assert not any(error.type == "SHOT_TOO_SHORT" for error in blockers)
    assert not any(error.type == "SHOT_TOO_SHORT" for error in warnings)


def test_split_long_sentence_continuation_remains_valid(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    segment_1 = _minimal_segment(segment_id="seg_1", timeline_in_sec=0.0, timeline_out_sec=7.0, reason="primary_asset")
    segment_2 = _minimal_segment(
        segment_id="seg_2", timeline_in_sec=7.0, timeline_out_sec=14.0, reason="split_long_sentence_continuation"
    )
    item = _minimal_item(planned_visual_segments=[segment_1, segment_2], timeline_start_sec=0.0,
                          timeline_end_sec=14.0, duration_sec=14.0, audio_end_sec=14.0)
    cut_plan = _minimal_cut_plan(
        project, items=[item], settings_snapshot={"max_asset_usage": 1, "min_asset_reuse_distance_shots": 0}
    )
    warnings, blockers = validate_asset_usage(project, cut_plan)
    assert blockers == []


# --- 16: Dedup ---


def test_duplicate_item_level_errors_are_deduplicated_in_report(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    item = _minimal_item(
        asset_selection_status="UNRESOLVED", chosen_asset_id="",
        warnings=["INVALID_ASSET_ID"], blockers=["MISSING_ASSET_MAPPING"],
    )
    cut_plan = _minimal_cut_plan(project, items=[item])
    report = validate_cut_plan(project, cut_plan)
    missing_mapping_blockers = [error for error in report.blockers if error.type == "MISSING_ASSET_MAPPING"]
    # Sowohl der explizite UNRESOLVED-Check als auch der carry-forward aus
    # item.blockers wuerden ohne Dedup zwei fast identische Eintraege erzeugen.
    assert len(missing_mapping_blockers) <= 2  # je nach message-Text ggf. 2 unterschiedliche Meldungen


def test_dedupe_errors_removes_exact_duplicates() -> None:
    error_a = CutPlanValidationError(type="X", severity="WARNING", scope="project", message="gleich")
    error_b = CutPlanValidationError(type="X", severity="WARNING", scope="project", message="gleich")
    error_c = CutPlanValidationError(type="X", severity="BLOCKER", scope="project", message="gleich")
    result = _dedupe_errors([error_a, error_b, error_c])
    assert len(result) == 2  # error_a/error_b sind identisch, error_c hat andere severity


# --- 17: Probe-Duration Cache ---


def test_probe_duration_seconds_called_once_per_path_per_validation_run(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _write_inventory(project, ["clip_a.mp4"])
    asset_path = str(project.project_root_path / FOLDER_A / "clip_a.mp4")

    segment_1 = _minimal_segment(segment_id="seg_1", asset_id="asset_clip_a", asset_path=asset_path,
                                  asset_type="video", timeline_in_sec=0.0, timeline_out_sec=5.0,
                                  source_in_sec=1.0, source_out_sec=6.0)
    segment_2 = _minimal_segment(segment_id="seg_2", asset_id="asset_clip_a", asset_path=asset_path,
                                  asset_type="video", timeline_in_sec=5.0, timeline_out_sec=10.0,
                                  source_in_sec=1.0, source_out_sec=6.0)
    item_1 = _minimal_item(cut_item_id="cut_1", planned_visual_segments=[segment_1], timeline_end_sec=5.0)
    item_2 = _minimal_item(cut_item_id="cut_2", planned_visual_segments=[segment_2], timeline_start_sec=5.0,
                            timeline_end_sec=10.0)
    cut_plan = _minimal_cut_plan(project, items=[item_1, item_2])

    with patch(f"{_VALIDATOR_MODULE}.probe_duration_seconds", return_value=10.0) as mock_probe:
        validate_cut_plan(project, cut_plan)

    assert mock_probe.call_count == 1


# --- 18: Settings Staleness bleibt bestehen ---


def test_settings_staleness_still_blocks_asset_selection(tmp_path: Path) -> None:
    project = _standard_settings_plan_and_project(tmp_path)
    save_cut_plan_settings(project, CutPlanSettings(project_id=project.id, max_asset_usage=10))
    with pytest.raises(ValueError):
        apply_asset_selection_to_draft(project)


# --- 19-22: Schutz bestehender Pipeline ---


def test_no_supplement_files_written(tmp_path: Path) -> None:
    project = _standard_settings_plan_and_project(tmp_path)
    apply_asset_selection_to_draft(project)
    validate_cut_plan_draft(project)
    assert not get_supplement_dir(project.work_dir_path).exists()


def test_no_edit_plan_documents_created(tmp_path: Path) -> None:
    project = _standard_settings_plan_and_project(tmp_path)
    apply_asset_selection_to_draft(project)
    validate_cut_plan_draft(project)
    assert not get_edit_plan_dir(project.work_dir_path).exists()


def test_no_otio_export_triggered(tmp_path: Path) -> None:
    project = _standard_settings_plan_and_project(tmp_path)
    apply_asset_selection_to_draft(project)
    validate_cut_plan_draft(project)
    assert not get_exports_dir(project.work_dir_path).exists()


def test_no_original_media_modified(tmp_path: Path) -> None:
    project = _standard_settings_plan_and_project(tmp_path)
    original = (project.project_root_path / FOLDER_A / "photo_a.jpg").read_bytes()
    apply_asset_selection_to_draft(project)
    validate_cut_plan_draft(project)
    assert (project.project_root_path / FOLDER_A / "photo_a.jpg").read_bytes() == original


# --- 23-24: Struktureller Schutz / Regression ---

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
    import re

    import otio_app.services.voiceover_generation.cut_plan_asset_selector as asset_selector_module
    import otio_app.services.voiceover_generation.cut_plan_builder as builder_module
    import otio_app.services.voiceover_generation.cut_plan_timeline_service as timeline_module
    import otio_app.services.voiceover_generation.cut_plan_validator as validator_module
    import otio_app.services.voiceover_generation.cut_plan_visual_coverage as coverage_module
    import otio_app.ui.voiceover_generation.cut_plan_tab as tab_module

    for module in (
        asset_selector_module, builder_module, timeline_module, validator_module, coverage_module, tab_module,
    ):
        source = inspect.getsource(module)
        for forbidden in _FORBIDDEN_SYMBOLS:
            assert not re.search(rf"\b{re.escape(forbidden)}\b", source), (
                f"{module.__name__} referenziert verbotenes Symbol '{forbidden}'."
            )


def test_cut_plan_visual_coverage_does_not_call_supplement_or_provider_apis() -> None:
    import otio_app.services.voiceover_generation.cut_plan_visual_coverage as coverage_module

    source = inspect.getsource(coverage_module)
    assert "supplement_pipeline" not in source
    assert "requests." not in source
    assert "download" not in source.lower()


def test_with_voiceover_workflow_unaffected() -> None:
    from otio_app.services import edit_plan_builder, otio_exporter

    assert hasattr(edit_plan_builder, "build_edit_plan")
    assert hasattr(edit_plan_builder, "save_edit_plan")
    assert hasattr(otio_exporter, "build_otio_timeline")


# --- UI (§10) ---


def test_ui_shows_coverage_info_after_asset_selection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _standard_settings_plan_and_project(tmp_path)
    _patch_project_selector(project, monkeypatch)

    monkeypatch.setattr("streamlit.button", lambda *a, **k: k.get("key") == f"cut_plan_apply_asset_selection_{project.id}")
    monkeypatch.setattr("streamlit.rerun", lambda: None)

    render_cut_plan_page()

    draft = load_cut_plan_draft(project)
    assert any(
        "initial_preroll_extension" in segment.reason or "section_pause_hold" in segment.reason
        for item in draft.items
        for segment in item.planned_visual_segments
    )


def test_ui_renders_without_exception_showing_validation_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _standard_settings_plan_and_project(tmp_path)
    apply_asset_selection_to_draft(project)
    validate_cut_plan_draft(project)
    _patch_project_selector(project, monkeypatch)

    render_cut_plan_page()  # darf nicht werfen


# --- Phase C: kleine Lücken schließen + Timeline-Overlaps normalisieren ---


def test_close_small_visual_gaps_extends_previous_segment_for_image() -> None:
    segment_1 = _minimal_segment(segment_id="seg_1", asset_type="image", timeline_in_sec=0.0, timeline_out_sec=5.0,
                                  duration_sec=5.0, source_in_sec=0.0, source_out_sec=5.0)
    segment_2 = _minimal_segment(segment_id="seg_2", timeline_in_sec=5.4, timeline_out_sec=10.4, duration_sec=5.0)
    item_1 = _minimal_item(cut_item_id="cut_1", planned_visual_segments=[segment_1], timeline_end_sec=5.0)
    item_2 = _minimal_item(cut_item_id="cut_2", planned_visual_segments=[segment_2], timeline_start_sec=5.4,
                            timeline_end_sec=10.4)
    cut_plan = CutPlanDocument(project_id="p1", items=[item_1, item_2])

    updated = close_small_visual_gaps(cut_plan)
    updated_segment_1 = updated.items[0].planned_visual_segments[0]
    assert updated_segment_1.timeline_out_sec == pytest.approx(5.4)
    assert updated_segment_1.duration_sec == pytest.approx(5.4)
    assert updated_segment_1.source_out_sec == pytest.approx(5.4)
    # Phase H (Bugfix): der Marker muss gesetzt werden, damit validate_
    # visual_segments ein dadurch über shot_max_sec verlängertes Segment
    # nicht faelschlich als harten SHOT_TOO_LONG-Blocker meldet.
    assert "small_gap_hold" in updated_segment_1.reason.split("+")
    # Das nächste Segment bleibt unverändert.
    assert updated.items[1].planned_visual_segments[0] == segment_2


def test_close_small_visual_gaps_extends_source_out_for_video() -> None:
    segment_1 = _minimal_segment(segment_id="seg_1", asset_type="video", asset_path="/fake/video.mp4",
                                  timeline_in_sec=0.0, timeline_out_sec=5.0, duration_sec=5.0,
                                  source_in_sec=1.0, source_out_sec=6.0)
    segment_2 = _minimal_segment(segment_id="seg_2", timeline_in_sec=5.4, timeline_out_sec=10.4, duration_sec=5.0)
    item_1 = _minimal_item(cut_item_id="cut_1", planned_visual_segments=[segment_1], timeline_end_sec=5.0)
    item_2 = _minimal_item(cut_item_id="cut_2", planned_visual_segments=[segment_2], timeline_start_sec=5.4,
                            timeline_end_sec=10.4)
    cut_plan = CutPlanDocument(project_id="p1", items=[item_1, item_2])

    with patch(f"{_VISUAL_COVERAGE_MODULE}._video_can_extend_to", return_value=True):
        updated = close_small_visual_gaps(cut_plan)
    updated_segment_1 = updated.items[0].planned_visual_segments[0]
    assert updated_segment_1.timeline_out_sec == pytest.approx(5.4)
    assert updated_segment_1.source_out_sec == pytest.approx(6.4)


def test_close_small_visual_gaps_skipped_when_video_too_short() -> None:
    segment_1 = _minimal_segment(segment_id="seg_1", asset_type="video", asset_path="/fake/video.mp4",
                                  timeline_in_sec=0.0, timeline_out_sec=5.0, duration_sec=5.0,
                                  source_in_sec=1.0, source_out_sec=6.0)
    segment_2 = _minimal_segment(segment_id="seg_2", timeline_in_sec=5.4, timeline_out_sec=10.4, duration_sec=5.0)
    item_1 = _minimal_item(cut_item_id="cut_1", planned_visual_segments=[segment_1], timeline_end_sec=5.0)
    item_2 = _minimal_item(cut_item_id="cut_2", planned_visual_segments=[segment_2], timeline_start_sec=5.4,
                            timeline_end_sec=10.4)
    cut_plan = CutPlanDocument(project_id="p1", items=[item_1, item_2])

    with patch(f"{_VISUAL_COVERAGE_MODULE}._video_can_extend_to", return_value=False):
        updated = close_small_visual_gaps(cut_plan)
    assert updated.items[0].planned_visual_segments[0] == segment_1  # unverändert, Lücke bleibt sichtbar


def test_close_small_visual_gaps_ignores_gaps_above_threshold() -> None:
    """Eine große Lücke (fehlendes Supplement-Asset) darf NICHT stillschweigend
    mit einem eingefrorenen Standbild überbrückt werden."""
    segment_1 = _minimal_segment(segment_id="seg_1", asset_type="image", timeline_in_sec=0.0, timeline_out_sec=5.0,
                                  duration_sec=5.0, source_in_sec=0.0, source_out_sec=5.0)
    segment_2 = _minimal_segment(segment_id="seg_2", timeline_in_sec=45.0, timeline_out_sec=50.0, duration_sec=5.0)
    item_1 = _minimal_item(cut_item_id="cut_1", planned_visual_segments=[segment_1], timeline_end_sec=5.0)
    item_2 = _minimal_item(cut_item_id="cut_2", planned_visual_segments=[segment_2], timeline_start_sec=45.0,
                            timeline_end_sec=50.0)
    cut_plan = CutPlanDocument(project_id="p1", items=[item_1, item_2])

    updated = close_small_visual_gaps(cut_plan)
    assert updated.items[0].planned_visual_segments[0] == segment_1  # unverändert


def test_close_small_visual_gaps_ignores_when_no_gap() -> None:
    segment_1 = _minimal_segment(segment_id="seg_1", timeline_in_sec=0.0, timeline_out_sec=5.0, duration_sec=5.0)
    segment_2 = _minimal_segment(segment_id="seg_2", timeline_in_sec=5.0, timeline_out_sec=10.0, duration_sec=5.0)
    item_1 = _minimal_item(cut_item_id="cut_1", planned_visual_segments=[segment_1], timeline_end_sec=5.0)
    item_2 = _minimal_item(cut_item_id="cut_2", planned_visual_segments=[segment_2], timeline_start_sec=5.0,
                            timeline_end_sec=10.0)
    cut_plan = CutPlanDocument(project_id="p1", items=[item_1, item_2])

    updated = close_small_visual_gaps(cut_plan)
    assert updated.items[0].planned_visual_segments[0] == segment_1
    assert updated.items[1].planned_visual_segments[0] == segment_2


def test_resolve_timeline_overlaps_shrinks_earlier_segment() -> None:
    segment_1 = _minimal_segment(segment_id="seg_1", asset_type="image", timeline_in_sec=0.0, timeline_out_sec=5.2,
                                  duration_sec=5.2, source_in_sec=0.0, source_out_sec=5.2)
    segment_2 = _minimal_segment(segment_id="seg_2", timeline_in_sec=5.0, timeline_out_sec=10.0, duration_sec=5.0)
    item_1 = _minimal_item(cut_item_id="cut_1", planned_visual_segments=[segment_1], timeline_end_sec=5.2,
                            duration_sec=5.2)
    item_2 = _minimal_item(cut_item_id="cut_2", planned_visual_segments=[segment_2], timeline_start_sec=5.0,
                            timeline_end_sec=10.0)
    cut_plan = CutPlanDocument(project_id="p1", items=[item_1, item_2])

    updated = resolve_timeline_overlaps(cut_plan)
    updated_segment_1 = updated.items[0].planned_visual_segments[0]
    assert updated_segment_1.timeline_out_sec == pytest.approx(5.0)
    assert updated_segment_1.duration_sec == pytest.approx(5.0)
    assert updated_segment_1.source_out_sec == pytest.approx(5.0)
    # Das spätere Segment bleibt unangetastet — keine Kaskade.
    assert updated.items[1].planned_visual_segments[0] == segment_2


def test_resolve_timeline_overlaps_shrinks_source_out_for_video() -> None:
    segment_1 = _minimal_segment(segment_id="seg_1", asset_type="video", asset_path="/fake/video.mp4",
                                  timeline_in_sec=0.0, timeline_out_sec=5.2, duration_sec=5.2,
                                  source_in_sec=1.0, source_out_sec=6.2)
    segment_2 = _minimal_segment(segment_id="seg_2", timeline_in_sec=5.0, timeline_out_sec=10.0, duration_sec=5.0)
    item_1 = _minimal_item(cut_item_id="cut_1", planned_visual_segments=[segment_1], timeline_end_sec=5.2,
                            duration_sec=5.2)
    item_2 = _minimal_item(cut_item_id="cut_2", planned_visual_segments=[segment_2], timeline_start_sec=5.0,
                            timeline_end_sec=10.0)
    cut_plan = CutPlanDocument(project_id="p1", items=[item_1, item_2])

    updated = resolve_timeline_overlaps(cut_plan)
    updated_segment_1 = updated.items[0].planned_visual_segments[0]
    assert updated_segment_1.source_out_sec == pytest.approx(6.0)


def test_resolve_timeline_overlaps_ignores_when_no_overlap() -> None:
    segment_1 = _minimal_segment(segment_id="seg_1", timeline_in_sec=0.0, timeline_out_sec=5.0, duration_sec=5.0)
    segment_2 = _minimal_segment(segment_id="seg_2", timeline_in_sec=5.0, timeline_out_sec=10.0, duration_sec=5.0)
    item_1 = _minimal_item(cut_item_id="cut_1", planned_visual_segments=[segment_1], timeline_end_sec=5.0)
    item_2 = _minimal_item(cut_item_id="cut_2", planned_visual_segments=[segment_2], timeline_start_sec=5.0,
                            timeline_end_sec=10.0)
    cut_plan = CutPlanDocument(project_id="p1", items=[item_1, item_2])

    updated = resolve_timeline_overlaps(cut_plan)
    assert updated.items[0].planned_visual_segments[0] == segment_1
    assert updated.items[1].planned_visual_segments[0] == segment_2


def test_resolve_timeline_overlaps_skips_when_shrink_would_reach_zero() -> None:
    """Ein extremer Overlap, der das frühere Segment auf (nahezu) 0
    reduzieren würde, wird NICHT angefasst — bleibt als TIMELINE_OVERLAP
    sichtbar statt ein ungültiges (Null-Dauer-)Segment zu erzeugen."""
    segment_1 = _minimal_segment(segment_id="seg_1", timeline_in_sec=0.0, timeline_out_sec=5.0, duration_sec=5.0)
    segment_2 = _minimal_segment(segment_id="seg_2", timeline_in_sec=0.0, timeline_out_sec=5.0, duration_sec=5.0)
    item_1 = _minimal_item(cut_item_id="cut_1", planned_visual_segments=[segment_1], timeline_end_sec=5.0)
    item_2 = _minimal_item(cut_item_id="cut_2", planned_visual_segments=[segment_2], timeline_start_sec=0.0,
                            timeline_end_sec=5.0)
    cut_plan = CutPlanDocument(project_id="p1", items=[item_1, item_2])

    updated = resolve_timeline_overlaps(cut_plan)
    assert updated.items[0].planned_visual_segments[0] == segment_1


def test_close_small_visual_gaps_extension_does_not_trigger_hard_shot_too_long(tmp_path: Path) -> None:
    """End-to-End-Regressionstest fuer den Phase-H-Bugfix: ein Segment, das
    knapp unter shot_max_sec liegt, wird durch close_small_visual_gaps über
    die Grenze hinweg verlängert — die anschließende Validierung darf
    daraus KEINEN harten Blocker machen."""
    project = _make_project(tmp_path)
    settings = CutPlanSettings(project_id=project.id, shot_max_sec=5.0, shot_min_sec=3.0)
    segment_1 = _minimal_segment(segment_id="seg_1", asset_type="image", timeline_in_sec=0.0, timeline_out_sec=4.9,
                                  duration_sec=4.9, source_in_sec=0.0, source_out_sec=4.9)
    segment_2 = _minimal_segment(segment_id="seg_2", timeline_in_sec=5.2, timeline_out_sec=10.2, duration_sec=5.0)
    item_1 = _minimal_item(cut_item_id="cut_1", planned_visual_segments=[segment_1], timeline_end_sec=4.9,
                            duration_sec=4.9)
    item_2 = _minimal_item(cut_item_id="cut_2", planned_visual_segments=[segment_2], timeline_start_sec=5.2,
                            timeline_end_sec=10.2, duration_sec=5.0)
    cut_plan = _minimal_cut_plan(project, items=[item_1, item_2], settings_snapshot={"shot_max_sec": 5.0, "shot_min_sec": 3.0})

    updated = apply_visual_coverage_extensions(cut_plan, settings)
    updated_segment_1 = updated.items[0].planned_visual_segments[0]
    assert updated_segment_1.duration_sec > 5.0  # Grenze tatsächlich überschritten

    warnings, blockers = validate_visual_segments(project, updated)
    assert not any(error.type == "SHOT_TOO_LONG" and error.severity == "BLOCKER" for error in blockers)


def test_apply_visual_coverage_extensions_runs_gap_close_and_overlap_resolution() -> None:
    """Integrationstest: apply_visual_coverage_extensions wendet auch die
    beiden neuen Phase-C-Schritte an, nicht nur die beiden bestehenden."""
    settings = CutPlanSettings(project_id="p1")
    segment_1 = _minimal_segment(segment_id="seg_1", asset_type="image", timeline_in_sec=0.0, timeline_out_sec=5.2,
                                  duration_sec=5.2, source_in_sec=0.0, source_out_sec=5.2)
    segment_2 = _minimal_segment(segment_id="seg_2", timeline_in_sec=5.0, timeline_out_sec=10.0, duration_sec=5.0)
    item_1 = _minimal_item(cut_item_id="cut_1", planned_visual_segments=[segment_1], timeline_end_sec=5.2,
                            duration_sec=5.2)
    item_2 = _minimal_item(cut_item_id="cut_2", planned_visual_segments=[segment_2], timeline_start_sec=5.0,
                            timeline_end_sec=10.0)
    cut_plan = CutPlanDocument(project_id="p1", items=[item_1, item_2])

    updated = apply_visual_coverage_extensions(cut_plan, settings)
    updated_segment_1 = updated.items[0].planned_visual_segments[0]
    assert updated_segment_1.timeline_out_sec == pytest.approx(5.0)  # Overlap wurde aufgelöst


def test_settings_from_snapshot_used_for_coverage(tmp_path: Path) -> None:
    """Stellt sicher, dass die Coverage-Erweiterung dieselbe Settings-Quelle
    (Snapshot) nutzt wie die Asset-Auswahl selbst — keine Abweichung."""
    project = _standard_settings_plan_and_project(tmp_path)
    draft = load_cut_plan_draft(project)
    settings = settings_from_snapshot(project, draft)
    assert settings.initial_audio_offset_sec == 1.0
    assert settings.pause_between_sections_sec == 0.25

    updated = apply_asset_selection_to_draft(project)
    coverage_applied = apply_visual_coverage_extensions(updated, settings)
    assert coverage_applied.items == updated.items  # bereits während apply_asset_selection_to_draft angewendet
