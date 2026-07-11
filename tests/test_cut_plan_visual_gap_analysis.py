"""Commit 2: Residual Visual Gap Analysis — Lücken direkt aus Draft-Daten
berechnen (Timeline-Zeiten, VisualSegments, Visual Window), nicht aus
Fehlermeldungen parsen. Reine Funktion, keine Reparaturlogik."""

from __future__ import annotations

import pytest

from otio_app.services.voiceover_generation.cut_plan_models import (
    CutPlanAudioItem,
    CutPlanDocument,
    CutPlanItem,
    CutPlanSettings,
    CutPlanSourceRef,
    VisualSegment,
)
from otio_app.services.voiceover_generation.cut_plan_visual_gap_analysis import (
    GAP_KIND_FULL_ITEM_MISSING,
    GAP_KIND_MINI_REPAIRABLE_GAP,
    GAP_KIND_RESIDUAL_ITEM_GAP,
    GAP_KIND_UNATTRIBUTED_GAP,
    RECOMMENDED_ACTION_MANUAL_REVIEW,
    RECOMMENDED_ACTION_RESIDUAL_GAP_SUPPLEMENT,
    RECOMMENDED_ACTION_SUPPLEMENT_REQUESTS,
    RECOMMENDED_ACTION_VALIDATION_REPAIR,
    analyze_visual_gaps,
)

FOLDER_A = "Grand Canyon"


def _settings(**overrides) -> CutPlanSettings:
    defaults = dict(
        project_id="p1", shot_min_sec=2.0, shot_max_sec=10.0, video_head_trim_sec=1.0,
        extend_visual_window_to_next_sentence=False, max_sentence_pause_extension_sec=3.0,
    )
    defaults.update(overrides)
    return CutPlanSettings(**defaults)


def _item(**overrides) -> CutPlanItem:
    defaults = dict(
        cut_item_id="cut_1", source_refs=[CutPlanSourceRef(source_sentence_id="s1", text="Text")],
        source_scope="folder", folder_name=FOLDER_A, text="Ein Satz.",
        timeline_start_sec=0.0, timeline_end_sec=5.0, duration_sec=5.0,
        chosen_asset_id="", asset_selection_status="SUPPLEMENT_REQUIRED",
    )
    defaults.update(overrides)
    return CutPlanItem(**defaults)


def _segment(**overrides) -> VisualSegment:
    defaults = dict(
        segment_id="seg_1", timeline_in_sec=0.0, timeline_out_sec=5.0, duration_sec=5.0,
        asset_id="asset_a", asset_path="/fake/a.jpg", asset_type="image", source_in_sec=0.0,
        source_out_sec=5.0, track="V1", reason="primary_asset",
    )
    defaults.update(overrides)
    return VisualSegment(**defaults)


def _audio_item(**overrides) -> CutPlanAudioItem:
    defaults = dict(scope="folder", folder_name=FOLDER_A, timeline_start_sec=0.0, timeline_end_sec=5.0, duration_sec=5.0)
    defaults.update(overrides)
    return CutPlanAudioItem(**defaults)


def test_no_gaps_when_fully_covered() -> None:
    item = _item(planned_visual_segments=[_segment()])
    cut_plan = CutPlanDocument(project_id="p1", items=[item], audio_items=[_audio_item()])
    gaps = analyze_visual_gaps(cut_plan, _settings())
    assert gaps == []


def test_full_item_missing_when_no_segments() -> None:
    item = _item(planned_visual_segments=[])
    cut_plan = CutPlanDocument(project_id="p1", items=[item], audio_items=[_audio_item()])
    gaps = analyze_visual_gaps(cut_plan, _settings())
    assert len(gaps) == 1
    gap = gaps[0]
    assert gap.cut_item_id == "cut_1"
    assert gap.gap_kind == GAP_KIND_FULL_ITEM_MISSING
    assert gap.recommended_action == RECOMMENDED_ACTION_SUPPLEMENT_REQUESTS
    assert gap.gap_start_sec == pytest.approx(0.0)
    assert gap.gap_end_sec == pytest.approx(5.0)


def test_mini_repairable_gap_within_items_own_window() -> None:
    """Das Zielsegment selbst deckt sein eigenes Item nicht vollständig ab
    (0.4s kürzer) — die Lücke liegt vollständig INNERHALB der eigenen
    Timeline des Items (keine Visual-Window-Erweiterung nötig), und die
    Nachbar-Segmente haben genug Trim-Spielraum, um sie auf shot_min_sec
    zu erweitern -> MINI_REPAIRABLE_GAP."""
    prev_item = _item(
        cut_item_id="cut_a", timeline_start_sec=0.0, timeline_end_sec=5.0,
        planned_visual_segments=[_segment(segment_id="seg_a", timeline_in_sec=0.0, timeline_out_sec=5.0)],
    )
    target_item = _item(
        cut_item_id="cut_b", timeline_start_sec=5.0, timeline_end_sec=8.0, duration_sec=3.0,
        asset_selection_status="SUPPLEMENT_USED",
        planned_visual_segments=[
            _segment(segment_id="seg_b", timeline_in_sec=5.0, timeline_out_sec=7.6, duration_sec=2.6, asset_id="asset_b")
        ],
    )
    next_item = _item(
        cut_item_id="cut_c", timeline_start_sec=8.0, timeline_end_sec=14.0, duration_sec=6.0,
        planned_visual_segments=[
            _segment(segment_id="seg_c", timeline_in_sec=8.0, timeline_out_sec=14.0, duration_sec=6.0, asset_id="asset_c")
        ],
    )
    cut_plan = CutPlanDocument(
        project_id="p1", items=[prev_item, target_item, next_item],
        audio_items=[
            _audio_item(timeline_start_sec=0.0, timeline_end_sec=5.0),
            _audio_item(timeline_start_sec=5.0, timeline_end_sec=8.0),
            _audio_item(timeline_start_sec=8.0, timeline_end_sec=14.0),
        ],
    )
    gaps = analyze_visual_gaps(cut_plan, _settings())
    target_gaps = [g for g in gaps if g.cut_item_id == "cut_b"]
    assert len(target_gaps) == 1
    gap = target_gaps[0]
    assert gap.gap_kind == GAP_KIND_MINI_REPAIRABLE_GAP
    assert gap.recommended_action == RECOMMENDED_ACTION_VALIDATION_REPAIR
    assert gap.gap_start_sec == pytest.approx(7.6)
    assert gap.gap_end_sec == pytest.approx(8.0)


def test_residual_item_gap_when_supplement_used_but_visual_window_not_covered() -> None:
    """Der Kernfall aus der Nutzerdiskussion: Item ist bereits SUPPLEMENT_
    USED, das Segment deckt aber nicht das vollständige (per Visual Window
    verlängerte) Fenster bis zum nächsten Satz ab — und die Lücke ist zu
    groß für eine sichere Nachbar-Kürzung (kein nachfolgendes Segment mit
    Trim-Spielraum in Reichweite)."""
    item = _item(
        cut_item_id="cut_1", timeline_start_sec=0.0, timeline_end_sec=5.0,
        asset_selection_status="SUPPLEMENT_USED", chosen_asset_id="supplement_pexels_1",
        planned_visual_segments=[_segment(segment_id="seg_1", timeline_in_sec=0.0, timeline_out_sec=5.0)],
    )
    next_item = _item(
        cut_item_id="cut_2", timeline_start_sec=20.0, timeline_end_sec=25.0,
        planned_visual_segments=[],
    )
    cut_plan = CutPlanDocument(
        project_id="p1", items=[item, next_item],
        audio_items=[_audio_item(timeline_end_sec=5.0), _audio_item(timeline_start_sec=20.0, timeline_end_sec=25.0)],
    )
    # max_sentence_pause_extension_sec (15.0) treibt die Lücke auf 15s —
    # deutlich über shot_max_sec (10.0) -> compute_black_gap_repair_plan
    # gibt None zurück (kein Mini-Patch mehr sinnvoll) -> RESIDUAL.
    settings = _settings(extend_visual_window_to_next_sentence=True, max_sentence_pause_extension_sec=15.0)
    gaps = analyze_visual_gaps(cut_plan, settings)

    residual_gaps = [g for g in gaps if g.cut_item_id == "cut_1"]
    assert len(residual_gaps) == 1
    gap = residual_gaps[0]
    assert gap.gap_kind == GAP_KIND_RESIDUAL_ITEM_GAP
    assert gap.recommended_action == RECOMMENDED_ACTION_RESIDUAL_GAP_SUPPLEMENT
    assert gap.gap_start_sec == pytest.approx(5.0)
    assert gap.gap_end_sec == pytest.approx(20.0)  # 5.0 + max_sentence_pause_extension_sec (15.0)
    assert gap.item_status == "SUPPLEMENT_USED"
    assert gap.chosen_asset_id == "supplement_pexels_1"
    assert gap.planned_segments_count == 1


def test_unattributed_gap_when_no_item_window_covers_it() -> None:
    """Lücke am Videoanfang, bevor das erste Item beginnt — kein Item
    beansprucht diese Zeitspanne."""
    item = _item(
        cut_item_id="cut_1", timeline_start_sec=3.0, timeline_end_sec=8.0,
        planned_visual_segments=[_segment(segment_id="seg_1", timeline_in_sec=3.0, timeline_out_sec=8.0)],
    )
    cut_plan = CutPlanDocument(
        project_id="p1", items=[item], audio_items=[_audio_item(timeline_start_sec=3.0, timeline_end_sec=8.0)],
    )
    gaps = analyze_visual_gaps(cut_plan, _settings())
    assert len(gaps) == 1
    gap = gaps[0]
    assert gap.gap_kind == GAP_KIND_UNATTRIBUTED_GAP
    assert gap.recommended_action == RECOMMENDED_ACTION_MANUAL_REVIEW
    assert gap.cut_item_id == ""
    assert gap.gap_start_sec == pytest.approx(0.0)
    assert gap.gap_end_sec == pytest.approx(3.0)


def test_gap_within_pause_extension_disabled_falls_back_to_unattributed() -> None:
    """Ohne aktiviertes Visual Window bleibt die Pause zwischen zwei Items
    unattributiert (das Item erhebt keinen Anspruch auf diese Zeitspanne) —
    dokumentiert den Unterschied zu test_residual_item_gap_when_supplement_
    used_but_visual_window_not_covered."""
    item = _item(
        cut_item_id="cut_1", timeline_start_sec=0.0, timeline_end_sec=5.0,
        planned_visual_segments=[_segment(segment_id="seg_1", timeline_in_sec=0.0, timeline_out_sec=5.0)],
    )
    next_item = _item(cut_item_id="cut_2", timeline_start_sec=8.0, timeline_end_sec=13.0, planned_visual_segments=[])
    cut_plan = CutPlanDocument(
        project_id="p1", items=[item, next_item],
        audio_items=[_audio_item(timeline_end_sec=5.0), _audio_item(timeline_start_sec=8.0, timeline_end_sec=13.0)],
    )
    settings = _settings(extend_visual_window_to_next_sentence=False)
    gaps = analyze_visual_gaps(cut_plan, settings)

    pause_gap = next(g for g in gaps if g.gap_start_sec == pytest.approx(5.0) and g.gap_end_sec == pytest.approx(8.0))
    assert pause_gap.gap_kind == GAP_KIND_UNATTRIBUTED_GAP


def test_multiple_gap_kinds_in_single_draft() -> None:
    """Realistischer Mix: ein Item ganz ohne Segment (FULL_ITEM_MISSING)
    und — in einer separaten Item-Gruppe — ein Item, dessen eigenes
    Segment etwas zu kurz ist, aber per Nachbar-Kürzung reparierbar bleibt
    (MINI_REPAIRABLE_GAP). Jede Lücke bekommt ihre eigene Klassifikation."""
    missing_item = _item(cut_item_id="cut_missing", timeline_start_sec=0.0, timeline_end_sec=5.0, planned_visual_segments=[])
    prev_item = _item(
        cut_item_id="cut_a", timeline_start_sec=5.0, timeline_end_sec=10.0,
        planned_visual_segments=[_segment(segment_id="seg_a", timeline_in_sec=5.0, timeline_out_sec=10.0)],
    )
    target_item = _item(
        cut_item_id="cut_b", timeline_start_sec=10.0, timeline_end_sec=13.0, duration_sec=3.0,
        planned_visual_segments=[
            _segment(segment_id="seg_b", timeline_in_sec=10.0, timeline_out_sec=12.6, duration_sec=2.6, asset_id="asset_b")
        ],
    )
    next_item = _item(
        cut_item_id="cut_c", timeline_start_sec=13.0, timeline_end_sec=19.0, duration_sec=6.0,
        planned_visual_segments=[
            _segment(segment_id="seg_c", timeline_in_sec=13.0, timeline_out_sec=19.0, duration_sec=6.0, asset_id="asset_c")
        ],
    )
    cut_plan = CutPlanDocument(
        project_id="p1", items=[missing_item, prev_item, target_item, next_item],
        audio_items=[
            _audio_item(timeline_start_sec=0.0, timeline_end_sec=5.0),
            _audio_item(timeline_start_sec=5.0, timeline_end_sec=10.0),
            _audio_item(timeline_start_sec=10.0, timeline_end_sec=13.0),
            _audio_item(timeline_start_sec=13.0, timeline_end_sec=19.0),
        ],
    )
    gaps = analyze_visual_gaps(cut_plan, _settings())
    kinds = {g.cut_item_id: g.gap_kind for g in gaps if g.cut_item_id}
    assert kinds.get("cut_missing") == GAP_KIND_FULL_ITEM_MISSING
    assert kinds.get("cut_b") == GAP_KIND_MINI_REPAIRABLE_GAP
