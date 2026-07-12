"""Section-pause attribution, diagnosis, and manual black-gap repair."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from otio_app.defaults import (
    AUDIO_SCOPE_FOLDER,
    CUT_PLAN_ASSET_SELECTION_PRIMARY_USED,
    CUT_PLAN_DURATION_STRATEGY_SINGLE_SHOT,
    CUT_PLAN_ERROR_BLACK_GAP_DURING_VOICEOVER,
)
from otio_app.models import Project, ProjectMode
from otio_app.services.voiceover_generation.cut_plan_models import (
    CutPlanAudioItem,
    CutPlanDocument,
    CutPlanItem,
    CutPlanSettings,
    VisualSegment,
)
from otio_app.services.voiceover_generation.cut_plan_validator import validate_no_black_gap_during_voiceover
from otio_app.services.voiceover_generation.cut_plan_visual_coverage import (
    diagnose_section_pause_hold_failure,
    extend_section_end_visuals_over_pauses,
    find_section_pause_responsible_item,
)

import pytest


def _project(tmp_path: Path) -> Project:
    root = tmp_path / "proj"
    root.mkdir()
    return Project(
        id="bg-repair",
        name="BG",
        project_root=str(root),
        work_dir=str(root / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=["Zion"],
        selected_asset_subdirs=["Zion"],
    )


def _item(
    cut_item_id: str,
    *,
    folder: str,
    start: float,
    end: float,
    is_closing: bool = False,
    asset_id: str = "asset_x",
    segments: list[VisualSegment] | None = None,
) -> CutPlanItem:
    return CutPlanItem(
        cut_item_id=cut_item_id,
        source_scope=AUDIO_SCOPE_FOLDER,
        folder_name=folder,
        text="" if is_closing else "Satz",
        timeline_start_sec=start,
        timeline_end_sec=end,
        duration_sec=end - start,
        duration_strategy=CUT_PLAN_DURATION_STRATEGY_SINGLE_SHOT,
        planned_visual_segments=segments or [],
        chosen_asset_id=asset_id,
        asset_selection_status=CUT_PLAN_ASSET_SELECTION_PRIMARY_USED,
        is_closing_shot=is_closing,
    )


def test_find_section_pause_prefers_closing_shot() -> None:
    closing = _item("cut_001_closing", folder="Zion", start=10.0, end=11.0, is_closing=True)
    sentence = _item("cut_001_sentence_001", folder="Zion", start=0.0, end=10.0)
    cut_plan = CutPlanDocument(project_id="p", items=[sentence, closing])
    preceding = CutPlanAudioItem(
        scope=AUDIO_SCOPE_FOLDER, folder_name="Zion", timeline_start_sec=0.0, timeline_end_sec=11.0, duration_sec=11.0
    )
    found = find_section_pause_responsible_item(cut_plan, 11.0, preceding_audio=preceding)
    assert found is not None
    assert found.cut_item_id == "cut_001_closing"


def test_section_pause_black_gap_attributed_to_closing(tmp_path: Path) -> None:
    project = _project(tmp_path)
    segment = VisualSegment(
        segment_id="seg",
        timeline_in_sec=10.0,
        timeline_out_sec=11.0,
        duration_sec=1.0,
        asset_id="asset_zion_short",
        asset_path=str(tmp_path / "missing.mp4"),
        asset_type="video",
        source_in_sec=0.0,
        source_out_sec=1.0,
    )
    closing = _item(
        "cut_001_closing",
        folder="Zion",
        start=10.0,
        end=11.0,
        is_closing=True,
        asset_id="asset_zion_short",
        segments=[segment],
    )
    next_sentence = _item("cut_002_sentence_001", folder="Havasu", start=16.0, end=20.0)
    cut_plan = CutPlanDocument(
        project_id=project.id,
        items=[closing, next_sentence],
        audio_items=[
            CutPlanAudioItem(
                scope=AUDIO_SCOPE_FOLDER,
                folder_name="Zion",
                timeline_start_sec=0.0,
                timeline_end_sec=11.0,
                duration_sec=11.0,
            ),
            CutPlanAudioItem(
                scope=AUDIO_SCOPE_FOLDER,
                folder_name="Havasu",
                timeline_start_sec=16.0,
                timeline_end_sec=20.0,
                duration_sec=4.0,
            ),
        ],
        settings_snapshot=CutPlanSettings(project_id=project.id, pause_between_sections_sec=5.0).model_dump(
            mode="json", exclude={"project_id", "generated_at"}
        ),
    )
    with patch(
        "otio_app.services.voiceover_generation.cut_plan_visual_coverage.probe_duration_seconds",
        return_value=1.0,
    ):
        _warnings, blockers = validate_no_black_gap_during_voiceover(project, cut_plan)

    pause_blockers = [
        error
        for error in blockers
        if error.type == CUT_PLAN_ERROR_BLACK_GAP_DURING_VOICEOVER and error.cut_item_id == "cut_001_closing"
    ]
    assert pause_blockers, [error.message for error in blockers]
    assert pause_blockers[0].gap_start_sec == 11.0
    assert pause_blockers[0].gap_end_sec == 16.0
    assert "Sektionspause" in pause_blockers[0].message
    assert "cut_001_closing" in pause_blockers[0].message


def test_diagnose_video_too_short(tmp_path: Path) -> None:
    segment = VisualSegment(
        segment_id="seg",
        timeline_in_sec=10.0,
        timeline_out_sec=11.0,
        duration_sec=1.0,
        asset_id="asset_short",
        asset_path=str(tmp_path / "clip.mp4"),
        asset_type="video",
        source_in_sec=0.0,
        source_out_sec=1.0,
    )
    (tmp_path / "clip.mp4").write_bytes(b"x")
    closing = _item(
        "cut_001_closing",
        folder="Zion",
        start=10.0,
        end=11.0,
        is_closing=True,
        segments=[segment],
    )
    cut_plan = CutPlanDocument(project_id="p", items=[closing])
    with patch(
        "otio_app.services.voiceover_generation.cut_plan_visual_coverage.probe_duration_seconds",
        return_value=1.2,
    ):
        diagnosis = diagnose_section_pause_hold_failure(cut_plan, 11.0, 16.0, responsible_item=closing)
    assert diagnosis.failure_reason == "VIDEO_TOO_SHORT"
    assert diagnosis.responsible_is_closing_shot is True
    assert diagnosis.hold_candidate_asset_id == "asset_short"


def test_section_pause_residual_within_tolerance_is_not_blocker(tmp_path: Path) -> None:
    """4s Hold + 1s Rest bei 5s Pause und Toleranz 1.5s → kein BLACK_GAP."""
    project = _project(tmp_path)
    clip = tmp_path / "zion.mp4"
    clip.write_bytes(b"x")
    segment = VisualSegment(
        segment_id="seg",
        timeline_in_sec=10.0,
        timeline_out_sec=15.0,
        duration_sec=5.0,
        asset_id="asset_zion",
        asset_path=str(clip),
        asset_type="video",
        source_in_sec=0.0,
        source_out_sec=5.0,
    )
    closing = _item(
        "cut_001_closing",
        folder="Zion",
        start=10.0,
        end=15.0,
        is_closing=True,
        asset_id="asset_zion",
        segments=[segment],
    )
    next_sentence = _item("cut_002_sentence_001", folder="Havasu", start=16.0, end=20.0)
    cut_plan = CutPlanDocument(
        project_id=project.id,
        items=[closing, next_sentence],
        audio_items=[
            CutPlanAudioItem(
                scope=AUDIO_SCOPE_FOLDER,
                folder_name="Zion",
                timeline_start_sec=0.0,
                timeline_end_sec=11.0,
                duration_sec=11.0,
            ),
            CutPlanAudioItem(
                scope=AUDIO_SCOPE_FOLDER,
                folder_name="Havasu",
                timeline_start_sec=16.0,
                timeline_end_sec=20.0,
                duration_sec=4.0,
            ),
        ],
        settings_snapshot=CutPlanSettings(
            project_id=project.id,
            pause_between_sections_sec=5.0,
            section_pause_hold_tolerance_sec=1.5,
        ).model_dump(mode="json", exclude={"project_id", "generated_at"}),
    )
    _warnings, blockers = validate_no_black_gap_during_voiceover(project, cut_plan)
    assert not any(
        error.type == CUT_PLAN_ERROR_BLACK_GAP_DURING_VOICEOVER and error.cut_item_id == "cut_001_closing"
        for error in blockers
    ), [error.message for error in blockers]


def test_partial_section_pause_hold_extends_to_video_limit(tmp_path: Path) -> None:
    clip = tmp_path / "zion.mp4"
    clip.write_bytes(b"x")
    settings = CutPlanSettings(project_id="p", pause_between_sections_sec=5.0)
    segment = VisualSegment(
        segment_id="seg",
        timeline_in_sec=10.0,
        timeline_out_sec=11.0,
        duration_sec=1.0,
        asset_id="asset_zion",
        asset_path=str(clip),
        asset_type="video",
        source_in_sec=0.0,
        source_out_sec=1.0,
    )
    closing = _item(
        "cut_001_closing",
        folder="Zion",
        start=10.0,
        end=11.0,
        is_closing=True,
        segments=[segment],
    )
    next_sentence = _item("cut_002_sentence_001", folder="Havasu", start=16.0, end=20.0)
    cut_plan = CutPlanDocument(
        project_id="p",
        items=[closing, next_sentence],
        audio_items=[
            CutPlanAudioItem(
                scope=AUDIO_SCOPE_FOLDER,
                folder_name="Zion",
                timeline_start_sec=0.0,
                timeline_end_sec=11.0,
                duration_sec=11.0,
            ),
            CutPlanAudioItem(
                scope=AUDIO_SCOPE_FOLDER,
                folder_name="Havasu",
                timeline_start_sec=16.0,
                timeline_end_sec=20.0,
                duration_sec=4.0,
            ),
        ],
    )
    with patch(
        "otio_app.services.voiceover_generation.cut_plan_visual_coverage.probe_duration_seconds",
        return_value=5.0,
    ):
        updated = extend_section_end_visuals_over_pauses(cut_plan, settings)
    updated_seg = updated.items[0].planned_visual_segments[0]
    assert updated_seg.timeline_out_sec == pytest.approx(15.0)
    assert updated_seg.source_out_sec == pytest.approx(5.0)
    assert "section_pause_hold" in updated_seg.reason


def test_manual_asset_clears_stale_black_gap_blockers(tmp_path: Path) -> None:
    from otio_app.defaults import (
        CUT_PLAN_ASSET_SELECTION_MANUAL_USED,
        CUT_PLAN_ERROR_ASSET_TOO_SHORT,
        CUT_PLAN_ERROR_SUPPLEMENT_REASON_MISSING,
    )
    from otio_app.services.voiceover_generation.cut_plan_generic_fallback_service import (
        apply_manual_asset_to_cut_plan_item,
    )
    from otio_app.services.voiceover_generation.cut_plan_supplement_models import (
        CutPlanSupplementRequest,
    )

    project = _project(tmp_path)
    clip = tmp_path / "long.mp4"
    clip.write_bytes(b"x")
    segment = VisualSegment(
        segment_id="old",
        timeline_in_sec=0.0,
        timeline_out_sec=10.0,
        duration_sec=10.0,
        asset_id="asset_old",
        asset_path=str(clip),
        asset_type="video",
        source_in_sec=0.0,
        source_out_sec=10.0,
    )
    item = _item(
        "cut_004_sentence_002",
        folder="Zion",
        start=0.0,
        end=10.0,
        asset_id="asset_old",
        segments=[segment],
    )
    item = item.model_copy(
        update={
            "blockers": [CUT_PLAN_ERROR_BLACK_GAP_DURING_VOICEOVER],
            "warnings": [CUT_PLAN_ERROR_ASSET_TOO_SHORT, CUT_PLAN_ERROR_SUPPLEMENT_REASON_MISSING],
            "needs_supplement_asset": True,
            "supplement_reason": "zu kurz",
        }
    )
    cut_plan = CutPlanDocument(
        project_id=project.id,
        items=[item],
        settings_snapshot=CutPlanSettings(project_id=project.id).model_dump(
            mode="json", exclude={"project_id", "generated_at"}
        ),
    )
    request = CutPlanSupplementRequest(
        request_id="manual_replace_cut_004_sentence_002",
        cut_item_id="cut_004_sentence_002",
        source_scope=AUDIO_SCOPE_FOLDER,
        folder_name="Zion",
        text="Satz",
        needed_duration_sec=10.0,
        reason="test",
    )
    with patch(
        "otio_app.services.voiceover_generation.cut_plan_generic_fallback_service.probe_duration_seconds",
        return_value=35.0,
    ), patch(
        "otio_app.services.voiceover_generation.cut_plan_generic_fallback_service.apply_visual_coverage_extensions",
        side_effect=lambda plan, _settings: plan,
    ):
        updated = apply_manual_asset_to_cut_plan_item(
            project, cut_plan, request, asset_id="asset_new", asset_path=str(clip)
        )
    updated_item = updated.items[0]
    assert updated_item.asset_selection_status == CUT_PLAN_ASSET_SELECTION_MANUAL_USED
    assert CUT_PLAN_ERROR_BLACK_GAP_DURING_VOICEOVER not in updated_item.blockers
    assert CUT_PLAN_ERROR_ASSET_TOO_SHORT not in updated_item.warnings
    assert CUT_PLAN_ERROR_SUPPLEMENT_REASON_MISSING not in updated_item.warnings
    assert updated_item.needs_supplement_asset is False
