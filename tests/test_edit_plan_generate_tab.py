"""Tests für _current_timing_settings und _missing_asset_breakdown."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from otio_app.analysis_models import EditPlanDocument, EditPlanShot
from otio_app.models import Project
from otio_app.services.edit_plan_timing_settings import (
    EditPlanTimingSettings,
    save_edit_plan_timing_settings,
)
from otio_app.services.supplement_coverage import COVERAGE_SUPPLEMENT_REQUIRED
from otio_app.ui.edit_plan import (
    _current_timing_settings,
    _missing_asset_breakdown,
    _persist_timing_widgets,
    _seed_timing_widgets,
)


def _project(tmp_path: Path) -> Project:
    root = tmp_path / "USA"
    root.mkdir()
    return Project(
        id="generate-tab-test",
        name="Test",
        project_root=str(root),
        work_dir=str(root / "_otio"),
        asset_subdir_names=["Big Sur"],
        selected_asset_subdirs=["Big Sur"],
    )


def test_current_timing_settings_uses_session_state_when_present(tmp_path: Path) -> None:
    project = _project(tmp_path)
    st.session_state.clear()
    save_edit_plan_timing_settings(
        project, EditPlanTimingSettings(gemini_model="gemini-3.1-flash-lite")
    )
    st.session_state[f"plan_gemini_{project.id}"] = "gemini-3.1-pro-preview"
    st.session_state[f"plan_min_{project.id}"] = 3.5
    st.session_state[f"plan_max_{project.id}"] = 7.0

    timing = _current_timing_settings(project)
    assert timing.gemini_model == "gemini-3.1-pro-preview"
    assert timing.shot_min_sec == 3.5
    assert timing.shot_max_sec == 7.0


def test_current_timing_settings_falls_back_to_saved_file_when_session_missing(
    tmp_path: Path,
) -> None:
    """Regression: Wenn der session_state-Key für das Gemini-Modell (oder
    andere Timing-Werte) aus irgendeinem Grund fehlt, darf NICHT unbemerkt
    auf einen generischen App-Default zurückgefallen werden — sondern auf
    das zuletzt tatsächlich GESPEICHERTE Modell (z. B. gemini-3.1-pro-preview),
    das der Nutzer bewusst gewählt hat."""
    project = _project(tmp_path)
    st.session_state.clear()
    save_edit_plan_timing_settings(
        project,
        EditPlanTimingSettings(
            shot_min_sec=3.5,
            shot_max_sec=7.0,
            gemini_model="gemini-3.1-pro-preview",
        ),
    )
    # Kein session_state gesetzt — simuliert einen Fall, in dem die Widgets
    # (aus welchem Grund auch immer) nicht geseedet wurden.
    timing = _current_timing_settings(project)
    assert timing.gemini_model == "gemini-3.1-pro-preview"
    assert timing.shot_min_sec == 3.5
    assert timing.shot_max_sec == 7.0


def _shot(*, asset_path: str | None, coverage_status: str = "") -> EditPlanShot:
    return EditPlanShot(
        voice_file="v",
        folder="Big Sur",
        voice_start_sec=0.0,
        voice_end_sec=3.0,
        duration_sec=3.0,
        asset_path=asset_path,
        coverage_status=coverage_status,
    )


def test_missing_asset_breakdown_distinguishes_coverage_gap_from_rule_blocked() -> None:
    """Regression: Unterschiedliche Zahlen in Vorschlag-/Prüfen & Speichern-/
    Supplement-Assets-Tab wirkten wie ein Fehler, zählten aber unterschiedliche
    Dinge (Shots vs. Beats/Requests) UND vermischten zwei verschiedene
    Ursachen für fehlende Assets (Coverage-Lücke vs. durch
    Wiederverwendungsregeln blockiert)."""
    draft = EditPlanDocument(
        project_id="p1",
        folder_name="Big Sur",
        shots=[
            _shot(asset_path="/a.mp4"),
            _shot(asset_path=None, coverage_status=COVERAGE_SUPPLEMENT_REQUIRED),
            _shot(asset_path=None, coverage_status=COVERAGE_SUPPLEMENT_REQUIRED),
            _shot(asset_path=None, coverage_status=""),
            _shot(asset_path=None, coverage_status=""),
            _shot(asset_path=None, coverage_status=""),
        ],
    )
    total, coverage_gap, rule_blocked = _missing_asset_breakdown(draft)
    assert total == 5
    assert coverage_gap == 2
    assert rule_blocked == 3


def test_missing_asset_breakdown_zero_when_all_shots_have_assets() -> None:
    draft = EditPlanDocument(
        project_id="p1",
        folder_name="Big Sur",
        shots=[_shot(asset_path="/a.mp4"), _shot(asset_path="/b.mp4")],
    )
    total, coverage_gap, rule_blocked = _missing_asset_breakdown(draft)
    assert (total, coverage_gap, rule_blocked) == (0, 0, 0)


def test_seed_timing_widgets_heals_reset_artifact_against_saved_file(tmp_path: Path) -> None:
    """Regression: Nutzer berichtete, dass Min./Max. Shot, Audio-Start und
    Ausklingen manchmal gleichzeitig auf 1.0/1.0/0.0/0.0 (den Widget-
    Untergrenzen) stehen — ein praktisch unbrauchbarer Zustand ('1 Sekunde
    pro Asset'). Die exakte Ursache für das erstmalige Auftreten dieses
    Widget-Zustands konnte nicht reproduziert werden (mehrere gezielte
    AppTest-Simulationen des kompletten 'Schnittplan vorschlagen'-Ablaufs
    inkl. Tab-Wechsel zeigten kein solches Verhalten) — als Schutzmaßnahme
    erkennt _seed_timing_widgets dieses Muster jetzt und stellt die zuletzt
    gespeicherten (plausiblen) Werte wieder her, statt es unverändert zu
    übernehmen."""
    project = _project(tmp_path)
    st.session_state.clear()
    save_edit_plan_timing_settings(
        project,
        EditPlanTimingSettings(
            shot_min_sec=3.5,
            shot_max_sec=7.0,
            audio_offset_sec=1.0,
            section_outro_sec=5.0,
            gemini_model="gemini-3.1-pro-preview",
        ),
    )
    # Simuliert den beobachteten Reset-Zustand direkt im session_state.
    st.session_state[f"plan_min_{project.id}"] = 1.0
    st.session_state[f"plan_max_{project.id}"] = 1.0
    st.session_state[f"plan_offset_{project.id}"] = 0.0
    st.session_state[f"plan_outro_{project.id}"] = 0.0
    st.session_state[f"plan_gemini_{project.id}"] = "gemini-3-flash-preview"

    _seed_timing_widgets(project)

    assert st.session_state[f"plan_min_{project.id}"] == 3.5
    assert st.session_state[f"plan_max_{project.id}"] == 7.0
    assert st.session_state[f"plan_offset_{project.id}"] == 1.0
    assert st.session_state[f"plan_outro_{project.id}"] == 5.0


def test_persist_timing_widgets_refuses_to_save_reset_artifact(tmp_path: Path) -> None:
    """Selbst wenn die Widgets (aus welchem Grund auch immer) den
    Reset-Zustand zeigen, darf _persist_timing_widgets ihn NICHT in die
    Datei schreiben — sonst würde eine zuvor korrekt gespeicherte
    Konfiguration dauerhaft überschrieben."""
    project = _project(tmp_path)
    st.session_state.clear()
    save_edit_plan_timing_settings(
        project,
        EditPlanTimingSettings(shot_min_sec=3.5, shot_max_sec=7.0, audio_offset_sec=1.0, section_outro_sec=5.0),
    )
    st.session_state[f"plan_min_{project.id}"] = 1.0
    st.session_state[f"plan_max_{project.id}"] = 1.0
    st.session_state[f"plan_offset_{project.id}"] = 0.0
    st.session_state[f"plan_outro_{project.id}"] = 0.0

    _persist_timing_widgets(project)

    reloaded = load_edit_plan_timing_settings_helper(project)
    assert reloaded.shot_min_sec == 3.5
    assert reloaded.shot_max_sec == 7.0


def load_edit_plan_timing_settings_helper(project):
    from otio_app.services.edit_plan_timing_settings import load_edit_plan_timing_settings

    return load_edit_plan_timing_settings(project)
