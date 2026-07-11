"""Commit 4: Residual Gap Apply — Übernahme eines akzeptierten Assets in
den Cut-Plan-Draft (PATCH_GAP_ONLY/REPLACE_ITEM_VISUAL) + Reapply-Button-
Logik (ohne erneute Suche)."""

from __future__ import annotations

from pathlib import Path

import pytest

from otio_app.defaults import (
    CUT_PLAN_ERROR_BLACK_GAP_DURING_VOICEOVER,
    CUT_PLAN_RESIDUAL_GAP_REPAIR_MODE_PATCH_GAP_ONLY,
    CUT_PLAN_RESIDUAL_GAP_REPAIR_MODE_REPLACE_ITEM_VISUAL,
)
from otio_app.models import Project, ProjectMode
from otio_app.services.voiceover_generation.cut_plan_builder import load_cut_plan_draft, save_cut_plan_draft
from otio_app.services.voiceover_generation.cut_plan_models import (
    CutPlanAudioItem,
    CutPlanDocument,
    CutPlanItem,
    CutPlanSourceRef,
    VisualSegment,
)
from otio_app.services.voiceover_generation.cut_plan_residual_gap_apply import (
    apply_residual_gap_asset,
    reapply_accepted_residual_gap_assets,
)
from otio_app.services.voiceover_generation.cut_plan_residual_gap_models import CutPlanResidualGapRequest
from otio_app.services.voiceover_generation.cut_plan_residual_gap_requests import (
    build_residual_gap_requests_from_cut_plan,
    save_residual_gap_requests,
)
from otio_app.services.voiceover_generation.cut_plan_supplement_models import CutPlanSupplementAsset

FOLDER_A = "Grand Canyon"


def _make_project(tmp_path: Path) -> Project:
    project_root = tmp_path / "USA"
    (project_root / FOLDER_A).mkdir(parents=True)
    return Project(
        id="cut-plan-residual-gap-apply-project",
        name="Residual Gap Apply Test",
        project_root=str(project_root),
        work_dir=str(project_root / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=[FOLDER_A],
        selected_asset_subdirs=[FOLDER_A],
    )


def _item(**overrides) -> CutPlanItem:
    defaults = dict(
        cut_item_id="cut_1", source_refs=[CutPlanSourceRef(source_sentence_id="s1", text="Text")],
        source_scope="folder", folder_name=FOLDER_A, text="Ein Satz über den Grand Canyon.",
        visual_intent="wide canyon shot", timeline_start_sec=0.0, timeline_end_sec=5.0, duration_sec=5.0,
        chosen_asset_id="supplement_pexels_1", asset_selection_status="SUPPLEMENT_USED",
    )
    defaults.update(overrides)
    return CutPlanItem(**defaults)


def _segment(**overrides) -> VisualSegment:
    defaults = dict(
        segment_id="seg_1", timeline_in_sec=0.0, timeline_out_sec=5.0, duration_sec=5.0,
        asset_id="supplement_pexels_1", asset_path="/fake/a.jpg", asset_type="image", source_in_sec=0.0,
        source_out_sec=5.0, track="V1", reason="supplement_asset",
    )
    defaults.update(overrides)
    return VisualSegment(**defaults)


def _audio_item(**overrides) -> CutPlanAudioItem:
    defaults = dict(scope="folder", folder_name=FOLDER_A, timeline_start_sec=0.0, timeline_end_sec=5.0, duration_sec=5.0)
    defaults.update(overrides)
    return CutPlanAudioItem(**defaults)


def _request(**overrides) -> CutPlanResidualGapRequest:
    defaults = dict(
        request_id="residual_cut_1", cut_item_id="cut_1", folder_name=FOLDER_A,
        gap_start_sec=5.0, gap_end_sec=20.0, needed_duration_sec=15.0,
        expected_start_sec=0.0, expected_end_sec=20.0,
        repair_mode=CUT_PLAN_RESIDUAL_GAP_REPAIR_MODE_PATCH_GAP_ONLY,
    )
    defaults.update(overrides)
    return CutPlanResidualGapRequest(**defaults)


def _asset(tmp_path: Path, *, name: str = "patch.jpg", asset_type: str = "image", asset_id: str = "supplement_pexels_9") -> CutPlanSupplementAsset:
    path = tmp_path / name
    path.write_bytes(b"img")
    return CutPlanSupplementAsset(
        asset_id=asset_id, request_id="residual_cut_1", candidate_id="cand_1", provider="pexels",
        asset_path=str(path), asset_type=asset_type, duration_sec=0.0,
    )


# --- apply_residual_gap_asset: PATCH_GAP_ONLY ---


def test_patch_gap_only_adds_segment_keeps_existing(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    item = _item(
        planned_visual_segments=[_segment(timeline_in_sec=0.0, timeline_out_sec=5.0)],
        blockers=[CUT_PLAN_ERROR_BLACK_GAP_DURING_VOICEOVER],
    )
    next_item = _item(cut_item_id="cut_2", timeline_start_sec=20.0, timeline_end_sec=25.0, planned_visual_segments=[])
    cut_plan = CutPlanDocument(project_id="p1", items=[item, next_item])
    request = _request()
    asset = _asset(tmp_path)

    updated = apply_residual_gap_asset(project, cut_plan, request, asset)
    updated_item = next(i for i in updated.items if i.cut_item_id == "cut_1")
    assert len(updated_item.planned_visual_segments) == 2
    original_segment = next(s for s in updated_item.planned_visual_segments if s.segment_id == "seg_1")
    assert original_segment.asset_id == "supplement_pexels_1"  # unverändert
    patch_segment = next(s for s in updated_item.planned_visual_segments if s.segment_id != "seg_1")
    assert patch_segment.timeline_in_sec == pytest.approx(5.0)
    assert patch_segment.timeline_out_sec == pytest.approx(20.0)
    assert patch_segment.asset_id == "supplement_pexels_9"
    assert CUT_PLAN_ERROR_BLACK_GAP_DURING_VOICEOVER not in updated_item.blockers
    assert updated.status == "NEEDS_REVIEW"


def test_patch_gap_only_raises_when_video_too_short(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    item = _item(planned_visual_segments=[_segment(timeline_in_sec=0.0, timeline_out_sec=5.0)])
    cut_plan = CutPlanDocument(
        project_id="p1", items=[item], settings_snapshot={"video_head_trim_sec": 1.0},
    )
    request = _request(gap_start_sec=5.0, gap_end_sec=20.0, needed_duration_sec=15.0)
    asset = _asset(tmp_path, name="short.mp4", asset_type="video")
    # duration_sec=0.0 -> usable = -1.0 -> immer zu kurz für 15s Bedarf.

    with pytest.raises(ValueError, match="zu kurz"):
        apply_residual_gap_asset(project, cut_plan, request, asset)


def test_patch_gap_only_raises_when_window_already_covered(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    item = _item(
        planned_visual_segments=[
            _segment(timeline_in_sec=0.0, timeline_out_sec=5.0),
            _segment(segment_id="seg_extra", timeline_in_sec=5.0, timeline_out_sec=20.0, asset_id="other"),
        ]
    )
    cut_plan = CutPlanDocument(project_id="p1", items=[item])
    request = _request()
    asset = _asset(tmp_path)

    with pytest.raises(ValueError, match="bereits durch ein VisualSegment belegt"):
        apply_residual_gap_asset(project, cut_plan, request, asset)


# --- apply_residual_gap_asset: REPLACE_ITEM_VISUAL ---


def test_replace_item_visual_replaces_all_segments(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    item = _item(
        timeline_start_sec=0.0, timeline_end_sec=8.0, duration_sec=8.0,
        planned_visual_segments=[
            _segment(segment_id="seg_short", timeline_in_sec=0.0, timeline_out_sec=3.0, duration_sec=3.0)
        ],
        blockers=[CUT_PLAN_ERROR_BLACK_GAP_DURING_VOICEOVER],
    )
    cut_plan = CutPlanDocument(project_id="p1", items=[item])
    request = _request(
        repair_mode=CUT_PLAN_RESIDUAL_GAP_REPAIR_MODE_REPLACE_ITEM_VISUAL,
        gap_start_sec=3.0, gap_end_sec=8.0, needed_duration_sec=5.0,
        expected_start_sec=0.0, expected_end_sec=8.0,
    )
    asset = _asset(tmp_path)

    updated = apply_residual_gap_asset(project, cut_plan, request, asset)
    updated_item = next(i for i in updated.items if i.cut_item_id == "cut_1")
    assert len(updated_item.planned_visual_segments) == 1
    segment = updated_item.planned_visual_segments[0]
    assert segment.timeline_in_sec == pytest.approx(0.0)
    assert segment.timeline_out_sec == pytest.approx(8.0)
    assert segment.asset_id == "supplement_pexels_9"
    assert updated_item.chosen_asset_id == "supplement_pexels_9"
    assert CUT_PLAN_ERROR_BLACK_GAP_DURING_VOICEOVER not in updated_item.blockers


def test_replace_item_visual_raises_when_video_shorter_than_own_duration(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    item = _item(timeline_start_sec=0.0, timeline_end_sec=8.0, duration_sec=8.0, planned_visual_segments=[])
    cut_plan = CutPlanDocument(project_id="p1", items=[item], settings_snapshot={"video_head_trim_sec": 1.0})
    request = _request(repair_mode=CUT_PLAN_RESIDUAL_GAP_REPAIR_MODE_REPLACE_ITEM_VISUAL)
    asset = _asset(tmp_path, name="short.mp4", asset_type="video")

    with pytest.raises(ValueError, match="zu kurz"):
        apply_residual_gap_asset(project, cut_plan, request, asset)


# --- reapply_accepted_residual_gap_assets ---


def test_reapply_applies_accepted_requests_and_saves_draft(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    item = _item(
        planned_visual_segments=[_segment(timeline_in_sec=0.0, timeline_out_sec=5.0)],
    )
    next_item = _item(cut_item_id="cut_2", timeline_start_sec=20.0, timeline_end_sec=25.0, planned_visual_segments=[])
    cut_plan = CutPlanDocument(
        project_id=project.id, items=[item, next_item],
        settings_snapshot={
            "extend_visual_window_to_next_sentence": True, "max_sentence_pause_extension_sec": 15.0,
            "shot_max_sec": 10.0,
        },
    )
    save_cut_plan_draft(project, cut_plan)

    document = build_residual_gap_requests_from_cut_plan(project, cut_plan)
    assert len(document.requests) == 1
    asset_path = tmp_path / "accepted.jpg"
    asset_path.write_bytes(b"img")
    document = document.model_copy(
        update={
            "requests": [
                document.requests[0].model_copy(
                    update={
                        "accepted_asset_id": "supplement_pexels_9", "accepted_asset_path": str(asset_path),
                        "status": "ACCEPTED",
                    }
                )
            ]
        }
    )
    save_residual_gap_requests(project, document)

    updated, applied, skipped = reapply_accepted_residual_gap_assets(project)
    assert applied == ["cut_1"]
    assert skipped == []
    item_after = next(i for i in updated.items if i.cut_item_id == "cut_1")
    assert any(s.asset_id == "supplement_pexels_9" for s in item_after.planned_visual_segments)

    reloaded_draft = load_cut_plan_draft(project)
    assert any(
        s.asset_id == "supplement_pexels_9"
        for i in reloaded_draft.items if i.cut_item_id == "cut_1"
        for s in i.planned_visual_segments
    )


def test_reapply_skips_when_already_applied(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    asset_path = tmp_path / "accepted.jpg"
    asset_path.write_bytes(b"img")
    item = _item(
        planned_visual_segments=[
            _segment(timeline_in_sec=0.0, timeline_out_sec=5.0),
            _segment(
                segment_id="patch", timeline_in_sec=5.0, timeline_out_sec=20.0,
                asset_id="supplement_pexels_9", asset_path=str(asset_path),
            ),
        ],
    )
    cut_plan = CutPlanDocument(project_id=project.id, items=[item])
    save_cut_plan_draft(project, cut_plan)

    document = build_residual_gap_requests_from_cut_plan(project, cut_plan)
    # Kein Rest-Gap mehr -> keine Requests zum Anwenden.
    save_residual_gap_requests(project, document)

    updated, applied, skipped = reapply_accepted_residual_gap_assets(project)
    assert applied == []
    assert skipped == []


def test_reapply_skips_when_file_missing(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    item = _item(planned_visual_segments=[_segment(timeline_in_sec=0.0, timeline_out_sec=5.0)])
    next_item = _item(cut_item_id="cut_2", timeline_start_sec=20.0, timeline_end_sec=25.0, planned_visual_segments=[])
    cut_plan = CutPlanDocument(
        project_id=project.id, items=[item, next_item],
        settings_snapshot={
            "extend_visual_window_to_next_sentence": True, "max_sentence_pause_extension_sec": 15.0,
            "shot_max_sec": 10.0,
        },
    )
    save_cut_plan_draft(project, cut_plan)

    document = build_residual_gap_requests_from_cut_plan(project, cut_plan)
    document = document.model_copy(
        update={
            "requests": [
                document.requests[0].model_copy(
                    update={
                        "accepted_asset_id": "supplement_pexels_9",
                        "accepted_asset_path": str(tmp_path / "missing.jpg"),
                    }
                )
            ]
        }
    )
    save_residual_gap_requests(project, document)

    updated, applied, skipped = reapply_accepted_residual_gap_assets(project)
    assert applied == []
    assert skipped == []  # kein Asset gefunden -> gar nicht versucht, kein "skip" nötig
