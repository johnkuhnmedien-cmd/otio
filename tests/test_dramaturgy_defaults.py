"""Globaler Dramaturgie-Standard für den automatischen Durchlauf."""

from __future__ import annotations

from pathlib import Path

import pytest

from otio_app.defaults import (
    DRAMATURGY_PLANNING_MODE_GEOGRAPHY,
    DRAMATURGY_PLANNING_MODE_SPECTACLE_FIRST,
    DRAMATURGY_PLANNING_MODE_VARIETY,
)
from otio_app.services.voiceover_generation.dramaturgy_defaults_service import (
    auto_run_dramaturgy_planning_mode,
    get_dramaturgy_defaults_path,
    load_dramaturgy_defaults,
    load_language_dramaturgy_word_defaults,
    normalize_dramaturgy_planning_mode,
    resolve_dramaturgy_planning_mode,
    save_dramaturgy_defaults,
    save_language_dramaturgy_word_defaults,
)
from otio_app.services.voiceover_generation.models import DramaturgyWordDefaults


@pytest.fixture()
def dramaturgy_defaults_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(
        "otio_app.services.voiceover_generation.dramaturgy_defaults_service.ensure_data_dir",
        lambda: data_dir,
    )
    return data_dir


def test_factory_auto_run_mode_is_spectacle_first(dramaturgy_defaults_dir: Path) -> None:
    assert auto_run_dramaturgy_planning_mode() == DRAMATURGY_PLANNING_MODE_SPECTACLE_FIRST
    assert resolve_dramaturgy_planning_mode() == DRAMATURGY_PLANNING_MODE_SPECTACLE_FIRST
    assert load_dramaturgy_defaults().planning_mode == DRAMATURGY_PLANNING_MODE_SPECTACLE_FIRST


def test_explicit_mode_wins_over_global_standard(dramaturgy_defaults_dir: Path) -> None:
    save_dramaturgy_defaults(DRAMATURGY_PLANNING_MODE_SPECTACLE_FIRST)
    assert (
        resolve_dramaturgy_planning_mode(DRAMATURGY_PLANNING_MODE_GEOGRAPHY)
        == DRAMATURGY_PLANNING_MODE_GEOGRAPHY
    )


def test_saved_override_is_used_for_auto_run(dramaturgy_defaults_dir: Path) -> None:
    save_dramaturgy_defaults(DRAMATURGY_PLANNING_MODE_VARIETY)
    assert auto_run_dramaturgy_planning_mode() == DRAMATURGY_PLANNING_MODE_VARIETY
    assert get_dramaturgy_defaults_path().is_relative_to(dramaturgy_defaults_dir)


def test_language_word_defaults_roundtrip(dramaturgy_defaults_dir: Path) -> None:
    save_language_dramaturgy_word_defaults(
        "pt",
        DramaturgyWordDefaults(target_words=140, word_tolerance_percent=15),
    )
    loaded = load_language_dramaturgy_word_defaults("PT")
    assert loaded is not None
    assert loaded.target_words == 140
    assert loaded.word_tolerance_percent == 15
    assert load_language_dramaturgy_word_defaults("DE") is None
    # Planungsmodus bleibt global unberührt.
    assert (
        load_dramaturgy_defaults().planning_mode
        == DRAMATURGY_PLANNING_MODE_SPECTACLE_FIRST
    )


def test_saving_planning_mode_keeps_language_word_defaults(
    dramaturgy_defaults_dir: Path,
) -> None:
    save_language_dramaturgy_word_defaults(
        "de",
        DramaturgyWordDefaults(target_words=160, word_tolerance_percent=10),
    )
    save_dramaturgy_defaults(DRAMATURGY_PLANNING_MODE_GEOGRAPHY)
    assert load_language_dramaturgy_word_defaults("DE") is not None
    assert load_language_dramaturgy_word_defaults("DE").target_words == 160
    assert resolve_dramaturgy_planning_mode() == DRAMATURGY_PLANNING_MODE_GEOGRAPHY


def test_invalid_mode_falls_back_to_spectacle_first() -> None:
    assert normalize_dramaturgy_planning_mode("nope") == DRAMATURGY_PLANNING_MODE_SPECTACLE_FIRST
    assert normalize_dramaturgy_planning_mode(None) == DRAMATURGY_PLANNING_MODE_SPECTACLE_FIRST
    assert resolve_dramaturgy_planning_mode("  ") == DRAMATURGY_PLANNING_MODE_SPECTACLE_FIRST
