"""Tests für Projektordner-Layout."""

from __future__ import annotations

from pathlib import Path

from otio_app.project_layout import (
    classify_subdirectories,
    detect_voice_over_folder,
    diagnose_project_root,
    discover_asset_subdir_names,
    get_folder_inventory_path,
    get_inventory_dir,
    get_inventory_path,
    get_voice_analysis_path,
    get_voice_over_dir,
    language_folder_name,
    resolve_voice_over_folder_name,
    scan_project_structure,
    safe_folder_slug,
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
    work_dir = temp_project_layout["work_dir"]
    assert get_inventory_path(project_root).name == "inventory.json"
    assert get_inventory_dir(work_dir).name == "inventory"
    assert safe_folder_slug("Florida Keys") == "Florida_Keys"
    assert get_folder_inventory_path(work_dir, "Florida Keys").name == "Florida_Keys.json"
    assert get_voice_analysis_path(project_root).name == "voice_over_analysis.json"


def test_discover_asset_subdir_names(temp_project_layout: dict[str, Path]) -> None:
    project_root = temp_project_layout["project_root"]
    work_dir = project_root / "_otio"
    names = discover_asset_subdir_names(project_root, work_dir, "Voice over")
    assert names == ["Grand Canyon", "Yellowstone"]


def test_resolve_voice_over_case_insensitive(temp_project_layout: dict[str, Path]) -> None:
    all_names = ["Grand Canyon", "voice over", "Yellowstone"]
    resolved = resolve_voice_over_folder_name(all_names, "Voice Over")
    assert resolved == "voice over"


def test_detect_voice_over_folder() -> None:
    names = ["Grand Canyon", "Voice Over", "Yellowstone"]
    assert detect_voice_over_folder(names) == "Voice Over"


def test_diagnose_project_root(temp_project_layout: dict[str, Path]) -> None:
    project_root = temp_project_layout["project_root"]
    diagnostic = diagnose_project_root(project_root)
    assert diagnostic.exists is True
    assert diagnostic.is_directory is True
    assert "Grand Canyon" in diagnostic.subdirectory_names
    assert "Voice over" in diagnostic.subdirectory_names


def test_scan_project_structure(temp_project_layout: dict[str, Path]) -> None:
    project_root = temp_project_layout["project_root"]
    work_dir = project_root / "_otio"
    scan = scan_project_structure(project_root, work_dir, "Voice over", "de")
    assert scan.ok
    assert scan.voice_over_folder_name == "Voice over"
    assert scan.voice_over_language_exists is True
    assert scan.asset_subdir_names == ["Grand Canyon", "Yellowstone"]
    assert scan.diagnostic is not None


def test_classify_excludes_selected_voice_over(temp_project_layout: dict[str, Path]) -> None:
    project_root = temp_project_layout["project_root"]
    work_dir = project_root / "_otio"
    all_names = ["Grand Canyon", "Voice over", "Yellowstone", "USA"]
    scan = classify_subdirectories(
        all_names,
        "Voice over",
        work_dir,
        project_root,
        "de",
    )
    assert "Voice over" not in scan.asset_subdir_names
    assert "USA" in scan.asset_subdir_names
