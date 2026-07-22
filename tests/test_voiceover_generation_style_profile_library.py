"""Projektübergreifende Style-Profile-Bibliothek — Service-Tests.

Nutzerfeedback Juli 2026: "Ich will gespeicherte Style Profiles für alle
Projekte die ich erstelle aufrufbar machen." Die Bibliothek liegt global
unter data/ (siehe otio_app.config.ensure_data_dir), NICHT unter dem
Arbeitsordner eines einzelnen Projekts.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from otio_app.services.voiceover_generation import style_profile_library_service as service
from otio_app.services.voiceover_generation.models import VoiceoverStyleProfile


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isoliert JEDEN Test von der echten data/-Ablage der Anwendung."""
    data_dir = tmp_path / "global_data"
    monkeypatch.setattr(service, "ensure_data_dir", lambda: data_dir)
    return data_dir


def _profile(project_id: str = "some-project", tone: str = "calm, cinematic") -> VoiceoverStyleProfile:
    return VoiceoverStyleProfile(project_id=project_id, overall_tone=tone)


def test_library_path_is_under_global_data_dir_not_project(tmp_path: Path) -> None:
    path = service.get_style_profile_library_path()
    assert path.is_relative_to(tmp_path / "global_data")


def test_load_returns_empty_library_when_missing() -> None:
    library = service.load_style_profile_library()
    assert library.entries == []


def test_save_profile_to_library_persists_entry() -> None:
    service.save_profile_to_library("Ruhige Dokumentation", _profile())

    library = service.load_style_profile_library()
    assert len(library.entries) == 1
    assert library.entries[0].name == "Ruhige Dokumentation"
    assert library.entries[0].profile.overall_tone == "calm, cinematic"


def test_save_profile_to_library_rejects_empty_name() -> None:
    with pytest.raises(ValueError):
        service.save_profile_to_library("   ", _profile())


def test_save_profile_to_library_overwrites_entry_with_same_name() -> None:
    service.save_profile_to_library("Style A", _profile(tone="first"))
    service.save_profile_to_library("Style A", _profile(tone="second"))

    library = service.load_style_profile_library()
    assert len(library.entries) == 1
    assert library.entries[0].profile.overall_tone == "second"


def test_save_multiple_profiles_keeps_all_entries_sorted_by_name() -> None:
    service.save_profile_to_library("Zebra", _profile())
    service.save_profile_to_library("Alpha", _profile())

    library = service.load_style_profile_library()
    assert [entry.name for entry in library.entries] == ["Alpha", "Zebra"]


def test_get_profile_from_library_returns_none_when_not_found() -> None:
    assert service.get_profile_from_library("nicht vorhanden") is None


def test_get_profile_from_library_returns_saved_profile() -> None:
    service.save_profile_to_library("Style A", _profile(tone="calm"))
    profile = service.get_profile_from_library("Style A")
    assert profile is not None
    assert profile.overall_tone == "calm"


def test_delete_profile_from_library_removes_entry() -> None:
    service.save_profile_to_library("Style A", _profile())
    service.save_profile_to_library("Style B", _profile())

    service.delete_profile_from_library("Style A")

    library = service.load_style_profile_library()
    assert [entry.name for entry in library.entries] == ["Style B"]


def test_delete_profile_from_library_is_a_noop_when_missing() -> None:
    service.save_profile_to_library("Style A", _profile())
    service.delete_profile_from_library("does-not-exist")

    library = service.load_style_profile_library()
    assert [entry.name for entry in library.entries] == ["Style A"]


def test_library_is_shared_across_different_project_ids() -> None:
    """Ein Profil, das für Projekt A gespeichert wurde, muss unverändert für
    ein völlig anderes Projekt B ladbar sein — das ist der ganze Zweck der
    projektübergreifenden Bibliothek."""
    profile_from_project_a = _profile(project_id="project-a", tone="cinematic, calm")
    service.save_profile_to_library("Reiseformat", profile_from_project_a)

    loaded_for_project_b = service.get_profile_from_library("Reiseformat")
    assert loaded_for_project_b is not None
    assert loaded_for_project_b.overall_tone == "cinematic, calm"
    # project_id bleibt der des ursprünglichen Erzeuger-Projekts — wird beim
    # Anwenden in einem neuen Projekt von save_style_profile() umgeschrieben.
    assert loaded_for_project_b.project_id == "project-a"
