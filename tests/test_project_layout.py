"""Tests für Projektordner-Layout."""

from __future__ import annotations

from pathlib import Path

from otio_app.project_layout import (
    discover_asset_subdirs,
    get_inventory_path,
    get_voice_analysis_path,
    get_voice_over_dir,
    language_folder_name,
)


def test_language_folder_name() -> None:
    assert language_folder_name("de") == "DE"
    assert language_folder_name("en") == "EN"


def test_get_voice_over_dir(temp_project_layout: dict[str, Path]) -> None:
    project_root = temp_project_layout["project_root"]
    voice_dir = get_voice_over_dir(project_root, "Voice over", "de")
    assert voice_dir == project_root / "Voice over" / "DE"


def test_output_paths(temp_project_layout: dict[str, Path]) -> None:
    project_root = temp_project_layout["project_root"]
    assert get_inventory_path(project_root).name == "inventory.json"
    assert get_voice_analysis_path(project_root).name == "voice_over_analysis.json"


def test_discover_asset_subdirs_excludes_voice_over_and_work(
    temp_project_layout: dict[str, Path],
) -> None:
    project_root = temp_project_layout["project_root"]
    work_dir = project_root / "_otio"
    names = {p.name for p in discover_asset_subdirs(project_root, work_dir, "Voice over")}
    assert "Voice over" not in names
    assert "_otio" not in names
    assert "Grand Canyon" in names
