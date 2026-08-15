"""UI-Tests für den Project-Brief-Tab — insbesondere die neuen Standard-
Negativregeln und die verbesserten Erklärungen (Nutzerfeedback Juli 2026:
"globale Negativregeln sind missverständlich")."""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from otio_app.defaults import BRIEF_NEGATIVE_RULE_INSTRUCTIONS, BRIEF_NEGATIVE_RULE_LABELS

PROJECT_ID = "repro-project"
SCRIPT_PATH = Path(__file__).parent / "_apptest_scripts" / "project_brief_repro.py"


def _run_repro(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AppTest:
    monkeypatch.setenv("REPRO_ROOT", str(tmp_path))
    monkeypatch.setenv("REPRO_PROJECT_ID", PROJECT_ID)
    at = AppTest.from_file(str(SCRIPT_PATH))
    at.run()
    assert not at.exception, at.exception
    return at


def test_ui_shows_all_negative_rule_checkboxes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    at = _run_repro(tmp_path, monkeypatch)
    checkbox_labels = {checkbox.label for checkbox in at.checkbox}
    for label in BRIEF_NEGATIVE_RULE_LABELS.values():
        assert label in checkbox_labels


def test_ui_shows_new_standard_negative_rule_checkboxes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    at = _run_repro(tmp_path, monkeypatch)
    checkbox_labels = {checkbox.label for checkbox in at.checkbox}
    assert "Keine Partyszenen" in checkbox_labels
    assert "Voice-over darf nicht nach KI klingen" in checkbox_labels
    assert "Keine Floskeln / abgenutzte Redewendungen" in checkbox_labels
    assert "Zeitangaben müssen mit der biblischen Zeitrechnung übereinstimmen" in checkbox_labels


def test_ui_checkboxes_have_llm_instruction_as_help_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    at = _run_repro(tmp_path, monkeypatch)
    checkboxes_by_label = {checkbox.label: checkbox for checkbox in at.checkbox}
    for flag, label in BRIEF_NEGATIVE_RULE_LABELS.items():
        checkbox = checkboxes_by_label[label]
        assert checkbox.help == BRIEF_NEGATIVE_RULE_INSTRUCTIONS[flag]


def test_ui_new_negative_rules_are_checked_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    at = _run_repro(tmp_path, monkeypatch)
    checkboxes_by_label = {checkbox.label: checkbox for checkbox in at.checkbox}
    assert checkboxes_by_label["Keine Partyszenen"].value is True
    assert checkboxes_by_label["Voice-over darf nicht nach KI klingen"].value is True


def test_ui_shows_title_reference_fields(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    at = _run_repro(tmp_path, monkeypatch)
    labels = {field.label for field in at.text_input}
    assert "Referenz-Titel 1" in labels
    assert "Referenz-Titel 2" in labels
    assert "Referenz-Titel 3" in labels
    button_labels = {button.label for button in at.button}
    assert "Videotitel erzeugen" in button_labels
    assert any("Als Standard für" in label for label in button_labels)


def test_ui_shows_explanation_of_three_mechanisms(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    at = _run_repro(tmp_path, monkeypatch)
    combined_captions = " ".join(caption.value for caption in at.caption)
    assert "Standard-Regeln" in combined_captions
    assert "Freitext" in combined_captions
    assert "Verbotene Wörter" in combined_captions


def test_ui_forbidden_phrases_and_freetext_have_help_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    at = _run_repro(tmp_path, monkeypatch)
    text_areas_by_label = {text_area.label: text_area for text_area in at.text_area}
    assert text_areas_by_label["Verbotene Wörter / Phrasen (eine pro Zeile)"].help
    assert text_areas_by_label["Globale Negativregeln — Freitext"].help
    assert text_areas_by_label["Globaler Zusatzprompt"].help


def test_save_persists_new_negative_rule_flags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from otio_app.models import Project, ProjectMode
    from otio_app.services.voiceover_generation.project_brief_service import load_project_brief

    at = _run_repro(tmp_path, monkeypatch)
    save_button = next(b for b in at.button if b.label == "Speichern")
    at = save_button.click().run()
    assert not at.exception, at.exception

    project = Project(
        id=PROJECT_ID,
        name="Repro",
        project_root=str(tmp_path / "USA"),
        work_dir=str(tmp_path / "USA" / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )
    brief = load_project_brief(project)
    assert brief.negative_rule_flags.get("no_party_scenes") is True
    assert brief.negative_rule_flags.get("biblical_chronology_required") is True
    assert brief.negative_rule_flags.get("voice_not_ai_sounding") is True
    assert brief.negative_rule_flags.get("no_cliches") is True
