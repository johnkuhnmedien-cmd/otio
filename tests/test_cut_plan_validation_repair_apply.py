"""Validation Repair Phase 4/5: Reparatur-Fenster-Berechnung (compute_
black_gap_repair_plan) und Anwendung (apply_black_gap_repair) — inklusive
Kürzung angrenzender VisualSegments, damit ein neu eingefügtes
Reparatur-Segment nie unter shot_min_sec fällt."""

from __future__ import annotations

import pytest

from otio_app.services.voiceover_generation.cut_plan_models import (
    CutPlanDocument,
    CutPlanItem,
    CutPlanSettings,
    CutPlanSourceRef,
    VisualSegment,
)
from otio_app.services.voiceover_generation.cut_plan_supplement_models import CutPlanSupplementAsset
from otio_app.services.voiceover_generation.cut_plan_validation_repair_apply import (
    REASON_BLACK_GAP_REPAIR_SUPPLEMENT,
    REASON_BLACK_GAP_REPAIR_TRIM,
    apply_black_gap_repair,
    compute_black_gap_repair_plan,
)

FOLDER_A = "Grand Canyon"


def _settings(**overrides) -> CutPlanSettings:
    defaults = dict(project_id="p1", shot_min_sec=2.0, shot_max_sec=10.0, video_head_trim_sec=1.0)
    defaults.update(overrides)
    return CutPlanSettings(**defaults)


def _minimal_item(**overrides) -> CutPlanItem:
    defaults = dict(
        cut_item_id="cut_gap", source_refs=[CutPlanSourceRef(source_sentence_id="s1", text="Text")],
        source_scope="folder", folder_name=FOLDER_A, text="Ein Satz.",
        timeline_start_sec=0.0, timeline_end_sec=5.0, duration_sec=5.0,
        chosen_asset_id="", asset_selection_status="SUPPLEMENT_REQUIRED",
    )
    defaults.update(overrides)
    return CutPlanItem(**defaults)


def _minimal_segment(**overrides) -> VisualSegment:
    defaults = dict(
        segment_id="seg_1", timeline_in_sec=0.0, timeline_out_sec=5.0, duration_sec=5.0,
        asset_id="asset_a", asset_path="/fake/a.jpg", asset_type="image", source_in_sec=0.0,
        source_out_sec=5.0, track="V1", reason="primary_asset",
    )
    defaults.update(overrides)
    return VisualSegment(**defaults)


def _supplement_asset(**overrides) -> CutPlanSupplementAsset:
    defaults = dict(
        asset_id="supplement_pexels_1", request_id="repair_black_gap_cut_gap", candidate_id="cand_1",
        provider="pexels", asset_path="/fake/repair.jpg", asset_type="image", duration_sec=0.0,
    )
    defaults.update(overrides)
    return CutPlanSupplementAsset(**defaults)


# --- compute_black_gap_repair_plan ---


def test_gap_already_long_enough_needs_no_trimming() -> None:
    settings = _settings()
    prev_item = _minimal_item(cut_item_id="cut_prev", planned_visual_segments=[
        _minimal_segment(segment_id="seg_prev", timeline_in_sec=0.0, timeline_out_sec=5.0, duration_sec=5.0)
    ])
    next_item = _minimal_item(cut_item_id="cut_next", planned_visual_segments=[
        _minimal_segment(segment_id="seg_next", timeline_in_sec=7.5, timeline_out_sec=12.5, duration_sec=5.0)
    ])
    cut_plan = CutPlanDocument(project_id="p1", items=[prev_item, next_item])

    plan = compute_black_gap_repair_plan(cut_plan, 5.0, 7.5, settings)
    assert plan is not None
    assert plan.window_start_sec == pytest.approx(5.0)
    assert plan.window_end_sec == pytest.approx(7.5)
    assert plan.take_from_prev_sec == 0.0
    assert plan.take_from_next_sec == 0.0


def test_gap_too_short_splits_trim_between_prev_and_next() -> None:
    settings = _settings()  # shot_min_sec=2.0
    prev_item = _minimal_item(cut_item_id="cut_prev", planned_visual_segments=[
        _minimal_segment(segment_id="seg_prev", timeline_in_sec=0.0, timeline_out_sec=5.0, duration_sec=5.0)
    ])
    next_item = _minimal_item(cut_item_id="cut_next", planned_visual_segments=[
        _minimal_segment(segment_id="seg_next", timeline_in_sec=5.4, timeline_out_sec=10.4, duration_sec=5.0)
    ])
    cut_plan = CutPlanDocument(project_id="p1", items=[prev_item, next_item])

    plan = compute_black_gap_repair_plan(cut_plan, 5.0, 5.4, settings)
    assert plan is not None
    assert plan.take_from_prev_sec == pytest.approx(0.8)
    assert plan.take_from_next_sec == pytest.approx(0.8)
    assert plan.window_start_sec == pytest.approx(4.2)
    assert plan.window_end_sec == pytest.approx(6.2)
    assert plan.window_duration_sec == pytest.approx(2.0)
    assert plan.prev_segment_id == "seg_prev"
    assert plan.next_segment_id == "seg_next"


def test_gap_too_short_takes_only_from_prev_when_next_has_no_room() -> None:
    settings = _settings()  # shot_min_sec=2.0
    prev_item = _minimal_item(cut_item_id="cut_prev", planned_visual_segments=[
        _minimal_segment(segment_id="seg_prev", timeline_in_sec=0.0, timeline_out_sec=5.0, duration_sec=5.0)
    ])
    next_item = _minimal_item(cut_item_id="cut_next", planned_visual_segments=[
        _minimal_segment(segment_id="seg_next", timeline_in_sec=5.4, timeline_out_sec=7.4, duration_sec=2.0)
    ])
    cut_plan = CutPlanDocument(project_id="p1", items=[prev_item, next_item])

    plan = compute_black_gap_repair_plan(cut_plan, 5.0, 5.4, settings)
    assert plan is not None
    assert plan.take_from_next_sec == pytest.approx(0.0)
    assert plan.take_from_prev_sec == pytest.approx(1.6)
    assert plan.window_start_sec == pytest.approx(3.4)
    assert plan.window_end_sec == pytest.approx(5.4)


def test_gap_too_short_takes_only_from_next_when_prev_has_no_room() -> None:
    settings = _settings()
    prev_item = _minimal_item(cut_item_id="cut_prev", planned_visual_segments=[
        _minimal_segment(segment_id="seg_prev", timeline_in_sec=0.0, timeline_out_sec=2.0, duration_sec=2.0)
    ])
    next_item = _minimal_item(cut_item_id="cut_next", planned_visual_segments=[
        _minimal_segment(segment_id="seg_next", timeline_in_sec=2.4, timeline_out_sec=7.4, duration_sec=5.0)
    ])
    cut_plan = CutPlanDocument(project_id="p1", items=[prev_item, next_item])

    plan = compute_black_gap_repair_plan(cut_plan, 2.0, 2.4, settings)
    assert plan is not None
    assert plan.take_from_prev_sec == pytest.approx(0.0)
    assert plan.take_from_next_sec == pytest.approx(1.6)
    assert plan.window_start_sec == pytest.approx(2.0)
    assert plan.window_end_sec == pytest.approx(4.0)


def test_returns_none_when_neighbors_have_insufficient_room() -> None:
    settings = _settings()  # shot_min_sec=2.0
    prev_item = _minimal_item(cut_item_id="cut_prev", planned_visual_segments=[
        _minimal_segment(segment_id="seg_prev", timeline_in_sec=0.0, timeline_out_sec=2.0, duration_sec=2.0)
    ])
    next_item = _minimal_item(cut_item_id="cut_next", planned_visual_segments=[
        _minimal_segment(segment_id="seg_next", timeline_in_sec=2.4, timeline_out_sec=4.4, duration_sec=2.0)
    ])
    cut_plan = CutPlanDocument(project_id="p1", items=[prev_item, next_item])

    plan = compute_black_gap_repair_plan(cut_plan, 2.0, 2.4, settings)
    assert plan is None


def test_returns_none_when_gap_exceeds_shot_max_sec() -> None:
    settings = _settings()  # shot_max_sec=10.0
    cut_plan = CutPlanDocument(project_id="p1", items=[])
    plan = compute_black_gap_repair_plan(cut_plan, 0.0, 11.0, settings)
    assert plan is None


def test_returns_none_when_gap_has_no_duration() -> None:
    settings = _settings()
    cut_plan = CutPlanDocument(project_id="p1", items=[])
    plan = compute_black_gap_repair_plan(cut_plan, 5.0, 5.0, settings)
    assert plan is None


def test_missing_prev_segment_falls_back_to_next_only() -> None:
    """Kein Segment endet vor der Lücke (z. B. Lücke am Timeline-Anfang) —
    der gesamte Kürzungsbedarf muss vom NÄCHSTEN Segment kommen."""
    settings = _settings()
    next_item = _minimal_item(cut_item_id="cut_next", planned_visual_segments=[
        _minimal_segment(segment_id="seg_next", timeline_in_sec=0.4, timeline_out_sec=5.4, duration_sec=5.0)
    ])
    cut_plan = CutPlanDocument(project_id="p1", items=[next_item])

    plan = compute_black_gap_repair_plan(cut_plan, 0.0, 0.4, settings)
    assert plan is not None
    assert plan.take_from_prev_sec == 0.0
    assert plan.take_from_next_sec == pytest.approx(1.6)
    assert plan.window_start_sec == pytest.approx(0.0)


# --- apply_black_gap_repair ---


def test_apply_inserts_repair_segment_and_trims_both_neighbors() -> None:
    settings = _settings()
    prev_item = _minimal_item(cut_item_id="cut_prev", planned_visual_segments=[
        _minimal_segment(segment_id="seg_prev", timeline_in_sec=0.0, timeline_out_sec=5.0, duration_sec=5.0,
                          asset_type="image", source_in_sec=0.0, source_out_sec=5.0)
    ])
    target_item = _minimal_item(cut_item_id="cut_gap")
    next_item = _minimal_item(cut_item_id="cut_next", planned_visual_segments=[
        _minimal_segment(segment_id="seg_next", timeline_in_sec=5.4, timeline_out_sec=10.4, duration_sec=5.0,
                          asset_type="image", source_in_sec=0.0, source_out_sec=5.0)
    ])
    cut_plan = CutPlanDocument(project_id="p1", items=[prev_item, target_item, next_item])

    plan = compute_black_gap_repair_plan(cut_plan, 5.0, 5.4, settings)
    assert plan is not None
    asset = _supplement_asset(asset_type="image")

    updated = apply_black_gap_repair(cut_plan, settings, cut_item_id="cut_gap", repair_plan=plan, accepted_asset=asset)

    updated_prev = next(i for i in updated.items if i.cut_item_id == "cut_prev").planned_visual_segments[0]
    updated_target_segments = next(i for i in updated.items if i.cut_item_id == "cut_gap").planned_visual_segments
    updated_next = next(i for i in updated.items if i.cut_item_id == "cut_next").planned_visual_segments[0]

    assert updated_prev.timeline_out_sec == pytest.approx(4.2)
    assert updated_prev.duration_sec == pytest.approx(4.2)
    assert "black_gap_repair_trim" in updated_prev.reason.split("+")

    assert updated_next.timeline_in_sec == pytest.approx(6.2)
    assert updated_next.duration_sec == pytest.approx(4.2)
    assert updated_next.source_in_sec == pytest.approx(0.0)  # Bild: source_in_sec bleibt immer 0.0
    assert "black_gap_repair_trim" in updated_next.reason.split("+")

    assert len(updated_target_segments) == 1
    repair_segment = updated_target_segments[0]
    assert repair_segment.timeline_in_sec == pytest.approx(4.2)
    assert repair_segment.timeline_out_sec == pytest.approx(6.2)
    assert repair_segment.duration_sec == pytest.approx(2.0)
    assert repair_segment.asset_id == asset.asset_id
    assert repair_segment.reason == REASON_BLACK_GAP_REPAIR_SUPPLEMENT

    # Keine Overlaps: prev endet exakt dort, wo das Reparatur-Segment beginnt.
    assert updated_prev.timeline_out_sec == pytest.approx(repair_segment.timeline_in_sec)
    assert updated_next.timeline_in_sec == pytest.approx(repair_segment.timeline_out_sec)


def test_apply_trims_video_neighbor_by_shifting_source_out() -> None:
    settings = _settings()
    prev_item = _minimal_item(cut_item_id="cut_prev", planned_visual_segments=[
        _minimal_segment(segment_id="seg_prev", timeline_in_sec=0.0, timeline_out_sec=5.0, duration_sec=5.0,
                          asset_type="video", asset_path="/fake/prev.mp4", source_in_sec=1.0, source_out_sec=6.0)
    ])
    target_item = _minimal_item(cut_item_id="cut_gap")
    next_item = _minimal_item(cut_item_id="cut_next", planned_visual_segments=[
        _minimal_segment(segment_id="seg_next", timeline_in_sec=5.4, timeline_out_sec=10.4, duration_sec=5.0,
                          asset_type="video", asset_path="/fake/next.mp4", source_in_sec=1.0, source_out_sec=6.0)
    ])
    cut_plan = CutPlanDocument(project_id="p1", items=[prev_item, target_item, next_item])

    plan = compute_black_gap_repair_plan(cut_plan, 5.0, 5.4, settings)
    assert plan is not None
    asset = _supplement_asset(asset_type="image")

    updated = apply_black_gap_repair(cut_plan, settings, cut_item_id="cut_gap", repair_plan=plan, accepted_asset=asset)

    updated_prev = next(i for i in updated.items if i.cut_item_id == "cut_prev").planned_visual_segments[0]
    updated_next = next(i for i in updated.items if i.cut_item_id == "cut_next").planned_visual_segments[0]

    # Video-Kürzung am ENDE: source_out_sec wandert um denselben Betrag zurück.
    assert updated_prev.source_out_sec == pytest.approx(6.0 - 0.8)
    assert updated_prev.source_in_sec == pytest.approx(1.0)  # unverändert

    # Video-Kürzung am ANFANG: source_in_sec wandert um denselben Betrag vor,
    # source_out_sec bleibt (derselbe Endpunkt des Quellmaterials).
    assert updated_next.source_in_sec == pytest.approx(1.0 + 0.8)
    assert updated_next.source_out_sec == pytest.approx(6.0)


def test_apply_with_video_asset_uses_video_head_trim_sec() -> None:
    settings = _settings(video_head_trim_sec=0.5)
    target_item = _minimal_item(cut_item_id="cut_gap")
    cut_plan = CutPlanDocument(project_id="p1", items=[target_item])

    # window_duration = 2.0 (kein Nachbar -> gap == shot_min_sec, kein Trimmen nötig)
    plan = compute_black_gap_repair_plan(cut_plan, 5.0, 7.0, settings)
    assert plan is not None
    assert plan.window_duration_sec == pytest.approx(2.0)

    asset = _supplement_asset(asset_type="video", asset_path="/fake/repair.mp4", duration_sec=3.0)
    updated = apply_black_gap_repair(cut_plan, settings, cut_item_id="cut_gap", repair_plan=plan, accepted_asset=asset)

    segment = next(i for i in updated.items if i.cut_item_id == "cut_gap").planned_visual_segments[0]
    assert segment.source_in_sec == pytest.approx(0.5)
    assert segment.source_out_sec == pytest.approx(2.5)


def test_apply_raises_when_video_asset_too_short_for_window() -> None:
    settings = _settings(video_head_trim_sec=0.5)
    target_item = _minimal_item(cut_item_id="cut_gap")
    cut_plan = CutPlanDocument(project_id="p1", items=[target_item])

    plan = compute_black_gap_repair_plan(cut_plan, 5.0, 7.0, settings)
    assert plan is not None

    # 3.0s Rohdauer - 0.5s Head Trim = 2.5s verfügbar, benötigt aber 2.0s -> ok.
    # Jetzt absichtlich zu kurz: 2.0s Rohdauer - 0.5s = 1.5s < 2.0s benötigt.
    asset = _supplement_asset(asset_type="video", asset_path="/fake/repair.mp4", duration_sec=2.0)

    with pytest.raises(ValueError, match="zu kurz"):
        apply_black_gap_repair(cut_plan, settings, cut_item_id="cut_gap", repair_plan=plan, accepted_asset=asset)


def test_apply_only_trims_the_specific_anchor_segment_in_split_item() -> None:
    """Ein Item mit mehreren Segmenten (Split) — nur das EINE als Anker
    identifizierte Segment darf gekürzt werden, alle anderen Segmente
    desselben Items bleiben unverändert."""
    settings = _settings()
    other_segment = _minimal_segment(
        segment_id="seg_other", timeline_in_sec=-5.0, timeline_out_sec=0.0, duration_sec=5.0
    )
    anchor_segment = _minimal_segment(segment_id="seg_prev", timeline_in_sec=0.0, timeline_out_sec=5.0, duration_sec=5.0)
    prev_item = _minimal_item(
        cut_item_id="cut_prev", planned_visual_segments=[other_segment, anchor_segment],
        timeline_start_sec=-5.0, timeline_end_sec=5.0, duration_sec=10.0,
    )
    target_item = _minimal_item(cut_item_id="cut_gap")
    next_item = _minimal_item(cut_item_id="cut_next", planned_visual_segments=[
        _minimal_segment(segment_id="seg_next", timeline_in_sec=5.4, timeline_out_sec=10.4, duration_sec=5.0)
    ])
    cut_plan = CutPlanDocument(project_id="p1", items=[prev_item, target_item, next_item])

    plan = compute_black_gap_repair_plan(cut_plan, 5.0, 5.4, settings)
    assert plan is not None
    asset = _supplement_asset(asset_type="image")

    updated = apply_black_gap_repair(cut_plan, settings, cut_item_id="cut_gap", repair_plan=plan, accepted_asset=asset)
    updated_prev_item = next(i for i in updated.items if i.cut_item_id == "cut_prev")
    updated_other = next(s for s in updated_prev_item.planned_visual_segments if s.segment_id == "seg_other")
    updated_anchor = next(s for s in updated_prev_item.planned_visual_segments if s.segment_id == "seg_prev")

    assert updated_other == other_segment  # unverändert
    assert updated_anchor.timeline_out_sec == pytest.approx(4.2)
