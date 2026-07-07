"""Tests für Default-Konsistenz in den Analyse-Modellen."""

from __future__ import annotations

from otio_app.analysis_models import EditPlanSettings
from otio_app.defaults import DEFAULT_GEMINI_MODEL


def test_edit_plan_settings_gemini_model_matches_app_default() -> None:
    """Regression: EditPlanSettings.gemini_model hatte einen abweichenden,
    hardcodierten Default ('gemini-2.0-flash'), der nichts mit
    DEFAULT_GEMINI_MODEL zu tun hatte. Jeder Code-Pfad, der EditPlanSettings()
    ohne explizites gemini_model konstruierte, fiel dadurch unbemerkt auf
    ein anderes Modell zurück als in der restlichen App konfiguriert."""
    assert EditPlanSettings().gemini_model == DEFAULT_GEMINI_MODEL
