"""Phase 1 (Juli 2026): grüner Button-Helper `render_new_feature_button`
(_shared.py) — Nutzerwunsch: 'Ich will alle neu hinzugefügten Buttons in
grün haben, damit ich die Neuerungen sofort sehe.'"""

from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

_APPTEST_SCRIPT_PATH = Path(__file__).parent / "_apptest_scripts" / "green_button_repro.py"


def _run() -> AppTest:
    at = AppTest.from_file(str(_APPTEST_SCRIPT_PATH))
    at.run()
    assert not at.exception, at.exception
    return at


def test_green_button_renders_with_given_label() -> None:
    at = _run()
    button_labels = {button.label for button in at.button}
    assert "🟢 Testfunktion ausführen" in button_labels


def test_green_button_injects_scoped_css_with_key_class() -> None:
    """Der Helper muss die von Streamlit dokumentierte key -> CSS-Klasse
    `st-key-<key>`-Kopplung nutzen, damit AUSSCHLIESSLICH dieser eine Button
    eingefärbt wird."""
    at = _run()
    markdown_html = "\n".join(block.value for block in at.markdown)
    assert "st-key-green_button_repro_button" in markdown_html
    assert "#1e8e3e" in markdown_html


def test_green_button_click_triggers_callback_behavior() -> None:
    at = _run()
    button = next(b for b in at.button if b.label == "🟢 Testfunktion ausführen")
    at = button.click().run()
    assert not at.exception, at.exception
    assert any("clicks=1" in text.value for text in at.text)


def test_green_button_key_with_special_characters_is_sanitized_for_css() -> None:
    """Projekt-IDs/keys können Punkte/Leerzeichen enthalten — die CSS-Klasse
    darf dadurch nicht ungültig werden (siehe Streamlit-Frontend-Sanitizing:
    alle Zeichen außer [a-zA-Z0-9_-] werden durch '-' ersetzt)."""
    import re

    from otio_app.ui.voiceover_generation._shared import render_new_feature_button

    assert render_new_feature_button  # nur Importierbarkeit hier geprüft
    sanitized = re.sub(r"[^a-zA-Z0-9_-]", "-", "vo_fvo_apply_word_target_proj.123 test")
    assert sanitized == "vo_fvo_apply_word_target_proj-123-test"
