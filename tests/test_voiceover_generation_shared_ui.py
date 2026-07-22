"""Tests für reine (nicht-Widget-)Hilfsfunktionen aus _shared.py."""

from __future__ import annotations

from pathlib import Path

from otio_app.models import Project, ProjectMode
from otio_app.services.voiceover_generation.models import (
    STYLE_MODE_RAW_TEXT,
    VoiceoverStyleProfile,
    VoiceoverStyleReferences,
)
from otio_app.services.voiceover_generation.style_reference_service import (
    save_style_references,
)
from otio_app.ui.voiceover_generation._shared import (
    style_profile_metric_value,
    style_source_metric_value,
)


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


def test_style_source_metric_value_shows_raw_text(tmp_path: Path) -> None:
    project_root = tmp_path / "USA"
    project_root.mkdir()
    project = Project(
        id="raw-metric",
        name="Raw",
        project_root=str(project_root),
        work_dir=str(project_root / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )
    save_style_references(
        project,
        VoiceoverStyleReferences(
            project_id=project.id,
            style_mode=STYLE_MODE_RAW_TEXT,
            raw_reference_text="Quiet trail guide.",
        ),
    )
    assert style_source_metric_value(project) == "Raw text"
