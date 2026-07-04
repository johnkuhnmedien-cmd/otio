"""Tests für Inventar-Laden und pro-Ordner-JSON."""

from __future__ import annotations

from pathlib import Path

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis, InventoryDocument
from otio_app.models import Project
from otio_app.project_layout import get_folder_inventory_path
from otio_app.services.inventory_loader import (
    load_folder_inventory,
    migrate_legacy_inventory,
    save_folder_inventory,
    selected_folders_have_inventory,
    sync_folder_inventories_from_cache,
)
from otio_app.services.media_inventory_cache import media_cache_path, save_cached_media


def _sample_project(layout: dict[str, Path], *, selected: list[str] | None = None) -> Project:
    return Project(
        id="inv-test",
        name="Test",
        project_root=str(layout["project_root"]),
        work_dir=str(layout["work_dir"]),
        asset_subdir_names=["Grand Canyon", "Yellowstone"],
        selected_asset_subdirs=selected or ["Grand Canyon"],
    )


def test_save_and_load_folder_inventory(temp_project_layout: dict[str, Path]) -> None:
    project = _sample_project(temp_project_layout)
    media_path = str(temp_project_layout["project_root"] / "Grand Canyon" / "clip.mp4")
    item = AssetFolderAnalysis(
        folder="Grand Canyon",
        media_files=[media_path],
        assets=[
            AssetMediaAnalysis(
                path=media_path,
                description="Steile Felswand",
            )
        ],
    )
    out_path = get_folder_inventory_path(project.work_dir_path, "Grand Canyon")
    save_folder_inventory(out_path, item)

    loaded = load_folder_inventory(project, "Grand Canyon")
    assert loaded.folder == "Grand Canyon"
    assert loaded.assets[0].description == "Steile Felswand"


def test_migrate_legacy_inventory(temp_project_layout: dict[str, Path]) -> None:
    project = _sample_project(temp_project_layout)
    media_path = str(temp_project_layout["project_root"] / "Grand Canyon" / "clip.mp4")
    legacy = InventoryDocument(
        project_id=project.id,
        items=[
            AssetFolderAnalysis(
                folder="Grand Canyon",
                media_files=[media_path],
                assets=[
                    AssetMediaAnalysis(
                        path=media_path,
                        description="Legacy-Beschreibung",
                    )
                ],
            )
        ],
    )
    project.inventory_path.write_text(legacy.model_dump_json(indent=2), encoding="utf-8")

    migrate_legacy_inventory(project)

    out_path = get_folder_inventory_path(project.work_dir_path, "Grand Canyon")
    assert out_path.is_file()
    loaded = load_folder_inventory(project, "Grand Canyon")
    assert loaded.assets[0].description == "Legacy-Beschreibung"


def test_materialize_folder_inventory_from_cache(temp_project_layout: dict[str, Path]) -> None:
    project = Project(
        id="inv-test",
        name="Test",
        project_root=str(temp_project_layout["project_root"]),
        work_dir=str(temp_project_layout["work_dir"]),
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )
    media_path = temp_project_layout["project_root"] / "Grand Canyon" / "clip.mp4"
    from otio_app.services.media_inventory_cache import save_cached_media, media_cache_path

    save_cached_media(
        media_cache_path(project, "Grand Canyon", media_path),
        AssetMediaAnalysis(path=str(media_path), description="Aus Cache"),
    )

    from otio_app.services.inventory_loader import materialize_folder_inventory_from_cache

    item, error = materialize_folder_inventory_from_cache(project, "Grand Canyon")
    assert error is None
    assert item is not None
    assert project.inventory_dir.is_dir()
    assert project.folder_inventory_path("Grand Canyon").is_file()


def test_sync_creates_inventory_from_root_legacy_file(
    temp_project_layout: dict[str, Path],
) -> None:
    project = Project(
        id="legacy-root-test",
        name="Test",
        project_root=str(temp_project_layout["project_root"]),
        work_dir=str(temp_project_layout["work_dir"]),
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )
    media_path = str(temp_project_layout["project_root"] / "Grand Canyon" / "clip.mp4")
    legacy = InventoryDocument(
        project_id=project.id,
        items=[
            AssetFolderAnalysis(
                folder="Grand Canyon",
                media_files=[media_path],
                assets=[AssetMediaAnalysis(path=media_path, description="Aus Root-JSON")],
            )
        ],
    )
    project.inventory_path.write_text(legacy.model_dump_json(indent=2), encoding="utf-8")

    created, statuses = sync_folder_inventories_from_cache(project)
    assert project.folder_inventory_path("Grand Canyon").is_file()
    assert created == ["Grand Canyon"] or statuses[0].state in {"created", "exists"}


def test_selected_folders_have_inventory(temp_project_layout: dict[str, Path]) -> None:
    project = _sample_project(temp_project_layout)
    assert selected_folders_have_inventory(project) is False

    media_path = str(temp_project_layout["project_root"] / "Grand Canyon" / "clip.mp4")
    item = AssetFolderAnalysis(
        folder="Grand Canyon",
        media_files=[media_path],
        assets=[AssetMediaAnalysis(path=media_path, description="Fertig")],
    )
    save_folder_inventory(
        get_folder_inventory_path(project.work_dir_path, "Grand Canyon"),
        item,
    )
    save_cached_media(
        media_cache_path(project, "Grand Canyon", Path(media_path)),
        item.assets[0],
    )
    assert selected_folders_have_inventory(project) is True
