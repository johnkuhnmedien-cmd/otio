"""Visual-Window Phase 1 (Nutzervorgabe Juli 2026): reine Berechnung des
visuellen Fensters bis zum Start des nächsten Satzes — noch NICHT in
choose_asset_for_cut_item/die Split-Segment-Dauer verdrahtet (folgt in
Phase 2). Diese Tests prüfen ausschließlich `compute_visual_window_end_sec`
und `compute_visual_window_duration_sec`."""

from __future__ import annotations

from otio_app.defaults import AUDIO_SCOPE_FOLDER, AUDIO_SCOPE_INTRO, CUT_PLAN_ERROR_MISSING_ALIGNMENT
from otio_app.services.voiceover_generation.cut_plan_asset_selector import (
    compute_visual_window_duration_sec,
    compute_visual_window_end_sec,
)
from otio_app.services.voiceover_generation.cut_plan_models import CutPlanItem, CutPlanSettings

FOLDER_A = "Grand Canyon"
FOLDER_B = "Antelope Canyon"


def _settings(**overrides) -> CutPlanSettings:
    defaults = dict(
        project_id="p1",
        extend_visual_window_to_next_sentence=True,
        max_sentence_pause_extension_sec=3.0,
    )
    defaults.update(overrides)
    return CutPlanSettings(**defaults)


def _item(**overrides) -> CutPlanItem:
    defaults = dict(
        cut_item_id="cut_1", source_scope=AUDIO_SCOPE_FOLDER, folder_name=FOLDER_A,
        timeline_start_sec=0.0, timeline_end_sec=12.0, duration_sec=12.0,
    )
    defaults.update(overrides)
    return CutPlanItem(**defaults)


def test_disabled_setting_returns_unchanged_timeline_end() -> None:
    settings = _settings(extend_visual_window_to_next_sentence=False)
    item = _item(timeline_end_sec=12.0)
    next_item = _item(cut_item_id="cut_2", timeline_start_sec=14.0, timeline_end_sec=20.0)

    assert compute_visual_window_end_sec(item, next_item, settings) == 12.0
    assert compute_visual_window_duration_sec(item, next_item, settings) == 12.0


def test_no_next_item_returns_unchanged_timeline_end() -> None:
    settings = _settings()
    item = _item(timeline_end_sec=12.0)

    assert compute_visual_window_end_sec(item, None, settings) == 12.0


def test_extends_into_pause_up_to_next_sentence_start() -> None:
    """12s Satz, nächster Satz startet bei 14s -> Fenster wächst auf 14s."""
    settings = _settings(max_sentence_pause_extension_sec=3.0)
    item = _item(timeline_start_sec=0.0, timeline_end_sec=12.0, duration_sec=12.0)
    next_item = _item(cut_item_id="cut_2", timeline_start_sec=14.0, timeline_end_sec=20.0)

    assert compute_visual_window_end_sec(item, next_item, settings) == 14.0
    assert compute_visual_window_duration_sec(item, next_item, settings) == 14.0


def test_caps_extension_at_max_sentence_pause_extension_sec() -> None:
    """Pause ist 8s lang, aber max_sentence_pause_extension_sec begrenzt auf 3s."""
    settings = _settings(max_sentence_pause_extension_sec=3.0)
    item = _item(timeline_start_sec=0.0, timeline_end_sec=12.0, duration_sec=12.0)
    next_item = _item(cut_item_id="cut_2", timeline_start_sec=20.0, timeline_end_sec=25.0)

    assert compute_visual_window_end_sec(item, next_item, settings) == 15.0


def test_no_extension_when_no_positive_pause() -> None:
    """Nächster Satz beginnt direkt am (oder vor dem) Ende des aktuellen -> nichts zu füllen."""
    settings = _settings()
    item = _item(timeline_start_sec=0.0, timeline_end_sec=12.0, duration_sec=12.0)
    next_item = _item(cut_item_id="cut_2", timeline_start_sec=12.0, timeline_end_sec=18.0)

    assert compute_visual_window_end_sec(item, next_item, settings) == 12.0


def test_does_not_extend_across_folder_boundary() -> None:
    settings = _settings()
    item = _item(folder_name=FOLDER_A, timeline_start_sec=0.0, timeline_end_sec=12.0)
    next_item = _item(cut_item_id="cut_2", folder_name=FOLDER_B, timeline_start_sec=14.0, timeline_end_sec=20.0)

    assert compute_visual_window_end_sec(item, next_item, settings) == 12.0


def test_does_not_extend_across_intro_folder_boundary() -> None:
    settings = _settings()
    item = _item(source_scope=AUDIO_SCOPE_INTRO, timeline_start_sec=0.0, timeline_end_sec=12.0)
    next_item = _item(
        cut_item_id="cut_2", source_scope=AUDIO_SCOPE_FOLDER, timeline_start_sec=14.0, timeline_end_sec=20.0
    )

    assert compute_visual_window_end_sec(item, next_item, settings) == 12.0


def test_does_not_extend_when_next_item_is_blocked() -> None:
    """Ein blockiertes Folgeitem (z. B. MISSING_ALIGNMENT) hat keine
    verlässliche timeline_start_sec -> keine Streckung in dessen Richtung."""
    settings = _settings()
    item = _item(timeline_start_sec=0.0, timeline_end_sec=12.0)
    next_item = _item(
        cut_item_id="cut_2", timeline_start_sec=14.0, timeline_end_sec=20.0,
        blockers=[CUT_PLAN_ERROR_MISSING_ALIGNMENT],
    )

    assert compute_visual_window_end_sec(item, next_item, settings) == 12.0


def test_duration_helper_matches_end_minus_start() -> None:
    settings = _settings(max_sentence_pause_extension_sec=5.0)
    item = _item(timeline_start_sec=3.0, timeline_end_sec=15.0, duration_sec=12.0)
    next_item = _item(cut_item_id="cut_2", timeline_start_sec=17.0, timeline_end_sec=22.0)

    assert compute_visual_window_end_sec(item, next_item, settings) == 17.0
    assert compute_visual_window_duration_sec(item, next_item, settings) == 14.0
