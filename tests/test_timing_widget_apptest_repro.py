"""AppTest-Reproduktion: Timing-Widgets über Tab-Wechsel + Button-Klick.

Versucht, das vom Nutzer gemeldete Verhalten ('Timing/Gemini-Settings
werden zurückgesetzt, sobald ich auf Schnittplan vorschlagen klicke') mit
Streamlits eigenem Test-Framework (AppTest) nachzustellen — echte
Widget-/Rerun-Mechanik statt reiner Funktionsaufrufe.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from otio_app.analysis_models import EditPlanDocument

SCRIPT_PATH = Path(__file__).parent / "_apptest_scripts" / "timing_widget_repro.py"


def _fake_build_edit_plan(project, settings, **kwargs):
    return EditPlanDocument(
        project_id=project.id,
        folder_name="Folder",
        confirmed=False,
        settings=settings,
        shots=[],
    )


def test_timing_widgets_survive_tab_switch_and_button_click(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("REPRO_ROOT", str(tmp_path))

    at = AppTest.from_file(str(SCRIPT_PATH))
    at.run()
    assert not at.exception

    # Werte ändern (wie ein Nutzer, der Min/Max/Modell im Regeln-Tab setzt).
    at.number_input(key="plan_min_repro-project").set_value(3.5).run()
    at.number_input(key="plan_max_repro-project").set_value(7.0).run()
    at.selectbox(key="plan_gemini_repro-project").select("gemini-3.1-pro-preview").run()
    assert not at.exception

    assert st_value(at, "MIN=") == "3.5"
    assert st_value(at, "MAX=") == "7.0"
    assert st_value(at, "GEMINI=") == "gemini-3.1-pro-preview"

    # Zu "Vorschlag" wechseln.
    at.radio(key="edit_plan_active_tab_repro-project").set_value("▶️ Vorschlag").run()
    assert not at.exception
    assert st_value(at, "MIN=") == "3.5"
    assert st_value(at, "MAX=") == "7.0"
    assert st_value(at, "GEMINI=") == "gemini-3.1-pro-preview"

    # "Schnittplan vorschlagen" klicken (build_edit_plan gemockt, damit der
    # Button-Handler bis zum Ende durchläuft, inkl. _set_draft + st.rerun()).
    with patch("otio_app.ui.edit_plan.build_edit_plan", side_effect=_fake_build_edit_plan):
        at.button(key="build_plan_repro-project").click().run()
    assert not at.exception, at.exception
    assert st_value(at, "MIN=") == "3.5"
    assert st_value(at, "MAX=") == "7.0"
    assert st_value(at, "GEMINI=") == "gemini-3.1-pro-preview"

    # Zu "Prüfen & Speichern" wechseln (wie im gemeldeten Ablauf).
    at.radio(key="edit_plan_active_tab_repro-project").set_value("✅ Prüfen & Speichern").run()
    assert not at.exception, at.exception

    # Zurück zu "Regeln" wechseln — DAS ist der Moment, in dem der Nutzer
    # den Reset beobachtet hat.
    at.radio(key="edit_plan_active_tab_repro-project").set_value("⚙️ Regeln").run()
    assert not at.exception
    assert st_value(at, "MIN=") == "3.5", "Min. Shot wurde zurückgesetzt!"
    assert st_value(at, "MAX=") == "7.0", "Max. Shot wurde zurückgesetzt!"
    assert st_value(at, "GEMINI=") == "gemini-3.1-pro-preview", "Gemini-Modell wurde zurückgesetzt!"


def st_value(at: AppTest, prefix: str) -> str:
    for element in at.markdown:
        if element.value.startswith(prefix):
            return element.value[len(prefix):]
    raise AssertionError(f"Keine Markdown-Ausgabe mit Prefix {prefix!r} gefunden")


def test_generate_button_shows_error_for_any_exception_type(
    tmp_path: Path, monkeypatch
) -> None:
    """Regression: Nutzer berichtete, dass beim Bestätigen/Vorschlagen
    scheinbar 'nichts passiert'. Vorher wurden nur GeminiNotConfiguredError/
    ValueError/FileNotFoundError abgefangen — jeder ANDERE Fehler (z. B.
    aus dem Opening-Title-Rendering) führte zu einem unbehandelten Absturz
    (sichtbar als at.exception) statt einer klaren Fehlermeldung. Jetzt wird
    JEDE Exception abgefangen und als Fehlermeldung angezeigt."""
    monkeypatch.setenv("REPRO_ROOT", str(tmp_path))

    at = AppTest.from_file(str(SCRIPT_PATH))
    at.run()
    at.radio(key="edit_plan_active_tab_repro-project").set_value("▶️ Vorschlag").run()
    assert not at.exception

    def _boom(*_args, **_kwargs):
        raise RuntimeError("Simulierter unerwarteter Fehler beim Bauen des Plans")

    with patch("otio_app.ui.edit_plan.build_edit_plan", side_effect=_boom):
        at.button(key="build_plan_repro-project").click().run()

    assert not at.exception, (
        f"Unbehandelte Exception statt sichtbarer Fehlermeldung: {at.exception}"
    )
    error_texts = [element.value for element in at.error]
    assert any("Simulierter unerwarteter Fehler" in text for text in error_texts), error_texts
