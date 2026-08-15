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
    normalize_dramaturgy_planning_mode,
    resolve_dramaturgy_planning_mode,
    save_dramaturgy_defaults,
)


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


def test_invalid_mode_falls_back_to_spectacle_first() -> None:
    assert normalize_dramaturgy_planning_mode("nope") == DRAMATURGY_PLANNING_MODE_SPECTACLE_FIRST
    assert normalize_dramaturgy_planning_mode(None) == DRAMATURGY_PLANNING_MODE_SPECTACLE_FIRST
    assert resolve_dramaturgy_planning_mode("  ") == DRAMATURGY_PLANNING_MODE_SPECTACLE_FIRST
