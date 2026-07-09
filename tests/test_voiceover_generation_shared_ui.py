"""Tests für reine (nicht-Widget-)Hilfsfunktionen aus _shared.py."""

from __future__ import annotations

from otio_app.services.voiceover_generation.models import VoiceoverStyleProfile
from otio_app.ui.voiceover_generation._shared import style_profile_metric_value


def test_style_profile_metric_value_is_dash_when_missing() -> None:
    assert style_profile_metric_value(None) == "—"


def test_style_profile_metric_value_is_checkmark_when_no_library_name() -> None:
    profile = VoiceoverStyleProfile(project_id="p1")
    assert style_profile_metric_value(profile) == "✓"


def test_style_profile_metric_value_shows_library_name_when_set() -> None:
    """Nutzerfeedback: 'Können wir das geladene Profil anzeigen, also den
    Namen anstatt einem Haken?'"""
    profile = VoiceoverStyleProfile(project_id="p1", library_name="Ruhige Dokumentation")
    assert style_profile_metric_value(profile) == "Ruhige Dokumentation"
