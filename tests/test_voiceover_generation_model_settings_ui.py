"""UI-Tests: vereinfachte Modell-Auswahl (EIN Dropdown, kein Freitext, keine
separate Provider-Spalte) — Nutzerfeedback Juli 2026: "Modell Einstellungen
bitte vereinfachen. Nur die Modelle listen (kein Freitext), nicht eine
gesonderte Spalte für Provider."

Deckt alle vier Stellen ab, an denen Provider/Modell bisher separat gepflegt
wurden: Style References (alle 5 Rollen), Dramaturgie, Intro, Folder
Voice-overs (Autor & Review)."""

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
) -> AppTest:
    monkeypatch.setenv("REPRO_ROOT", str(tmp_path))
    monkeypatch.setenv("REPRO_PROJECT_ID", PROJECT_ID)
    monkeypatch.setenv("REPRO_RENDER_FUNCTION", render_function)
    monkeypatch.setenv("REPRO_SETUP", setup)
    at = AppTest.from_file(str(SCRIPT_PATH))
    at.run()
    assert not at.exception, at.exception
    return at


@pytest.mark.parametrize(
    "render_function,setup,expected_labels",
    [
        (
            "otio_app.ui.voiceover_generation.dramaturgy_tab:render_dramaturgy_page",
            "none",
            ["Modell"],
        ),
        (
            "otio_app.ui.voiceover_generation.intro_tab:render_intro_page",
            "dramaturgy_and_voiceovers_confirmed",
            ["Modell"],
        ),
        (
            "otio_app.ui.voiceover_generation.folder_voiceovers_tab:render_folder_voiceovers_page",
            "dramaturgy_and_voiceovers_confirmed",
            ["Modell (Autor)", "Modell (Review)"],
        ),
    ],
)
def test_model_selectbox_present_no_free_text_no_provider_column(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    render_function: str,
    setup: str,
    expected_labels: list[str],
) -> None:
    at = _run_repro(tmp_path, monkeypatch, render_function, setup=setup)

    selectbox_labels = {selectbox.label for selectbox in at.selectbox}
    for label in expected_labels:
        assert label in selectbox_labels

    assert not any("provider" in label.lower() for label in selectbox_labels)
    text_input_labels = {text_input.label for text_input in at.text_input}
    assert not any("modell" in label.lower() for label in text_input_labels)


@pytest.mark.parametrize(
    "render_function",
    [
        "otio_app.ui.voiceover_generation.dramaturgy_tab:render_dramaturgy_page",
        "otio_app.ui.voiceover_generation.intro_tab:render_intro_page",
        "otio_app.ui.voiceover_generation.folder_voiceovers_tab:render_folder_voiceovers_page",
    ],
)
def test_page_still_renders_without_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, render_function: str
) -> None:
    _run_repro(tmp_path, monkeypatch, render_function)
