"""Tests für Voice-over ↔ Ordner-Zuordnung per Dateiname."""

from __future__ import annotations

from pathlib import Path

from otio_app.models import Project
from otio_app.services.voice_folder_matcher import (
    filename_contains_folder,
    match_voice_file_to_folder,
    suggest_voice_folder_mappings,
)


def test_filename_contains_folder_with_spaces() -> None:
    assert filename_contains_folder("USA_Florida Keys_VO.wav", "Florida Keys")
    assert filename_contains_folder("Florida_Keys_voiceover.mp3", "Florida Keys")


def test_filename_contains_folder_compact() -> None:
    assert filename_contains_folder("USA_FloridaKeys_final.wav", "Florida Keys")


def test_match_prefers_longest_folder_name() -> None:
    folders = ["Arches", "Arches National Park", "Grand Canyon"]
    matched = match_voice_file_to_folder(
        "USA_Arches National Park_VO.wav",
        folders,
    )
    assert matched == "Arches National Park"


def test_match_returns_none_when_missing() -> None:
    assert match_voice_file_to_folder("intro.wav", ["Florida Keys"]) is None


def test_suggest_voice_folder_mappings(
    temp_project_layout: dict[str, Path],
) -> None:
    voice_dir = temp_project_layout["voice_over_dir"]
    (voice_dir / "USA_Florida Keys_VO.wav").write_bytes(b"audio")
    (voice_dir / "clip_Yellowstone_take2.mp3").write_bytes(b"audio")

    project = Project(
        id="test-project",
        name="Test",
        project_root=str(temp_project_layout["project_root"]),
        work_dir=str(temp_project_layout["work_dir"]),
        asset_subdir_names=["Grand Canyon", "Yellowstone", "Florida Keys"],
        selected_asset_subdirs=["Grand Canyon", "Yellowstone", "Florida Keys"],
    )

    entries = suggest_voice_folder_mappings(project)
    by_name = {Path(entry.voice_file).name: entry.folder for entry in entries}

    assert by_name["USA_Florida Keys_VO.wav"] == "Florida Keys"
    assert by_name["clip_Yellowstone_take2.mp3"] == "Yellowstone"
    assert by_name["voiceover.wav"] is None
