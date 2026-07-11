"""UI-Tests: "Voraussetzungen"-Kennzahl "Style Profile" zeigt den Namen des
geladenen Bibliothekseintrags statt eines nicht-identifizierenden Häkchens.

Nutzerfeedback: "Können wir in der UI in Dramaturgie das geladene Profil
anzeigen, also den Namen anstatt einem Haken?" — konsistent auch für Intro
und Folder Voice-overs umgesetzt, die dieselbe Kennzahl zeigen."""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

PROJECT_ID = "repro-project"
SCRIPT_PATH = Path(__file__).parent / "_apptest_scripts" / "voiceover_gen_model_settings_repro.py"


def _run_repro(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    render_function: str,
    *,
    setup: str = "none",
    library_name: str | None = None,
) -> AppTest:
    monkeypatch.setenv("REPRO_ROOT", str(tmp_path))
    monkeypatch.setenv("REPRO_PROJECT_ID", PROJECT_ID)
    monkeypatch.setenv("REPRO_RENDER_FUNCTION", render_function)
    monkeypatch.setenv("REPRO_SETUP", setup)
    if library_name is not None:
        monkeypatch.setenv("REPRO_STYLE_PROFILE_LIBRARY_NAME", library_name)
    else:
        monkeypatch.delenv("REPRO_STYLE_PROFILE_LIBRARY_NAME", raising=False)
    at = AppTest.from_file(str(SCRIPT_PATH))
    at.run()
    assert not at.exception, at.exception
    return at


@pytest.mark.parametrize(
    "render_function,setup",
    [
        ("otio_app.ui.voiceover_generation.dramaturgy_tab:render_dramaturgy_page", "none"),
        (
            "otio_app.ui.voiceover_generation.intro_tab:render_intro_page",
            "dramaturgy_and_voiceovers_confirmed",
        ),
        (
            "otio_app.ui.voiceover_generation.folder_voiceovers_tab:render_folder_voiceovers_page",
            "dramaturgy_and_voiceovers_confirmed",
        ),
    ],
)
def test_style_profile_metric_shows_dash_when_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, render_function: str, setup: str
) -> None:
    at = _run_repro(tmp_path, monkeypatch, render_function, setup=setup)
    style_profile_metric = next(m for m in at.metric if m.label == "Style Profile")
    assert style_profile_metric.value == "—"


@pytest.mark.parametrize(
    "render_function,setup",
    [
        ("otio_app.ui.voiceover_generation.dramaturgy_tab:render_dramaturgy_page", "none"),
        (
            "otio_app.ui.voiceover_generation.intro_tab:render_intro_page",
            "dramaturgy_and_voiceovers_confirmed",
        ),
        (
            "otio_app.ui.voiceover_generation.folder_voiceovers_tab:render_folder_voiceovers_page",
            "dramaturgy_and_voiceovers_confirmed",
        ),
    ],
)
def test_style_profile_metric_shows_library_name_when_loaded_from_library(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, render_function: str, setup: str
) -> None:
    at = _run_repro(
        tmp_path,
        monkeypatch,
        render_function,
        setup=setup,
        library_name="Ruhige Dokumentation",
    )
    style_profile_metric = next(m for m in at.metric if m.label == "Style Profile")
    assert style_profile_metric.value == "Ruhige Dokumentation"
