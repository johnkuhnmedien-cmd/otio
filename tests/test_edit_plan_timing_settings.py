"""Tests für die persistente Speicherung der Timing-/Gemini-Einstellungen.

Regression: Min./Max. Shot, Text-Trenner und Gemini-Modell wurden bisher nur
im `st.session_state` gehalten und nirgends persistiert. Nach einem
Seitenwechsel, Browser-Reload oder App-Neustart fielen sie stillschweigend
auf die Hardcoded-Defaults zurück, egal was der Nutzer eingestellt hatte.
"""

from __future__ import annotations

from pathlib import Path

from otio_app.defaults import DEFAULT_SHOT_MAX_SEC, DEFAULT_SHOT_MIN_SEC
from otio_app.models import Project
from otio_app.services.edit_plan_timing_settings import (
    EditPlanTimingSettings,
    edit_plan_timing_settings_path,
    load_edit_plan_timing_settings,
    save_edit_plan_timing_settings,
)


def _project(tmp_path: Path) -> Project:
    root = tmp_path / "USA"
    root.mkdir()
    return Project(
        id="timing-test",
        name="Test",
        project_root=str(root),
        work_dir=str(root / "_otio"),
        asset_subdir_names=["Antelope Canyon"],
        selected_asset_subdirs=["Antelope Canyon"],
    )


def test_load_defaults_when_no_file_exists(tmp_path: Path) -> None:
    project = _project(tmp_path)
    settings = load_edit_plan_timing_settings(project)
    assert settings.shot_min_sec == DEFAULT_SHOT_MIN_SEC
    assert settings.shot_max_sec == DEFAULT_SHOT_MAX_SEC


def test_save_then_load_roundtrip(tmp_path: Path) -> None:
    project = _project(tmp_path)
    custom = EditPlanTimingSettings(
        shot_min_sec=4.5,
        shot_max_sec=12.0,
        audio_offset_sec=2.0,
        section_outro_sec=7.5,
        text_splitters=", danach , dann ",
        gemini_model="gemini-3.1-pro",
    )
    save_edit_plan_timing_settings(project, custom)

    reloaded = load_edit_plan_timing_settings(project)
    assert reloaded.shot_min_sec == 4.5
    assert reloaded.shot_max_sec == 12.0
    assert reloaded.audio_offset_sec == 2.0
    assert reloaded.section_outro_sec == 7.5
    assert reloaded.text_splitters == ", danach , dann "
    assert reloaded.gemini_model == "gemini-3.1-pro"


def test_settings_persist_to_project_work_dir(tmp_path: Path) -> None:
    project = _project(tmp_path)
    save_edit_plan_timing_settings(project, EditPlanTimingSettings(shot_min_sec=6.0))
    path = edit_plan_timing_settings_path(project)
    assert path.is_file()
    assert path.parent == project.work_dir_path


def test_load_survives_corrupt_json(tmp_path: Path) -> None:
    project = _project(tmp_path)
    path = edit_plan_timing_settings_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not valid json {{{", encoding="utf-8")

    settings = load_edit_plan_timing_settings(project)
    assert settings.shot_min_sec == DEFAULT_SHOT_MIN_SEC


def test_seeded_widgets_survive_fresh_session(tmp_path: Path) -> None:
    """Regression: Nach Seitenwechsel/App-Neustart (= neuer session_state) müssen
    Min./Max. Shot & Co. aus der gespeicherten Datei geladen werden, statt
    stillschweigend auf die Hardcoded-Defaults zurückzufallen."""
    import streamlit as st

    from otio_app.ui.edit_plan import (
        _persist_timing_widgets,
        _plan_number_setting,
        _plan_text_setting,
        _seed_timing_widgets,
    )

    project = _project(tmp_path)

    # Session 1: Nutzer stellt individuelle Werte ein und die Auto-Persistenz greift.
    st.session_state.clear()
    _seed_timing_widgets(project)
    st.session_state[f"plan_min_{project.id}"] = 4.5
    st.session_state[f"plan_max_{project.id}"] = 15.0
    st.session_state[f"plan_split_{project.id}"] = ", danach , dann "
    _persist_timing_widgets(project)

    # Session 2 (frischer session_state, z.B. nach Seitenwechsel/Neustart).
    st.session_state.clear()
    _seed_timing_widgets(project)

    assert _plan_number_setting(project.id, "min", DEFAULT_SHOT_MIN_SEC) == 4.5
    assert _plan_number_setting(project.id, "max", DEFAULT_SHOT_MAX_SEC) == 15.0
    assert _plan_text_setting(project.id, "split", "") == ", danach , dann "
