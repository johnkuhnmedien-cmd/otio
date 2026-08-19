"""Tests für Inventar-Laden und pro-Ordner-JSON."""

from __future__ import annotations

from pathlib import Path

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis, InventoryDocument
from otio_app.models import Project
from otio_app.project_layout import get_folder_inventory_path
from otio_app.services.inventory_loader import (
    folder_has_usable_inventory_data,
    folder_inventory_matches_media,
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


def test_folder_inventory_matches_media_ignores_supplemental_assets(
    temp_project_layout: dict[str, Path],
) -> None:
    """Regression: Supplement-Assets liegen unter `<Ordner>/_supplemental/
    <provider>/` und werden vom Top-Level-Medien-Scan (discover_folder_
    media_paths) nicht erfasst. Ohne Ausschluss dieser Pfade wurde JEDES
    gespeicherte Inventar mit Supplement-Assets fälschlich als 'nicht mehr
    aktuell' erkannt — mit der Folge, dass build_edit_plan() und die
    Stale-Hash-Prüfung unterschiedliche Inventar-Stände lasen und sofort
    nach einem frischen, korrekten Schnittplan ein 'Inventory changed'-
    Fehler auftauchte."""
    folder = temp_project_layout["project_root"] / "Grand Canyon"
    original_path = str(folder / "clip.mp4")
    supplement_path = str(folder / "_supplemental" / "_pexels" / "new_asset.mp4")

    item = AssetFolderAnalysis(
        folder="Grand Canyon",
        media_files=[original_path, supplement_path],
        assets=[
            AssetMediaAnalysis(path=original_path, description="Original"),
            AssetMediaAnalysis(path=supplement_path, description="Supplement", asset_origin="pexels"),
        ],
    )

    # discover_folder_media_paths würde hier NUR die Top-Level-Datei finden
    # (das Supplement-Asset liegt in einem Unterordner und wird von einem
    # nicht-rekursiven Scan nicht erfasst).
    media_paths = [Path(original_path)]

    assert folder_inventory_matches_media(item, media_paths) is True


def test_load_folder_inventory_keeps_supplement_assets(
    temp_project_layout: dict[str, Path],
) -> None:
    project = _sample_project(temp_project_layout)
    folder = temp_project_layout["project_root"] / "Grand Canyon"
    original_path = folder / "clip.mp4"
    supplement_dir = folder / "_supplemental" / "_pexels"
    supplement_dir.mkdir(parents=True, exist_ok=True)
    supplement_path = supplement_dir / "new_asset.mp4"
    supplement_path.write_bytes(b"mp4")

    item = AssetFolderAnalysis(
        folder="Grand Canyon",
        media_files=[str(original_path), str(supplement_path)],
        assets=[
            AssetMediaAnalysis(path=str(original_path), description="Original", asset_id="asset_original"),
            AssetMediaAnalysis(
                path=str(supplement_path),
                description="Supplement",
                asset_origin="pexels",
                asset_id="asset_supplement",
            ),
        ],
    )
    save_folder_inventory(get_folder_inventory_path(project.work_dir_path, "Grand Canyon"), item)

    loaded = load_folder_inventory(project, "Grand Canyon")
    loaded_ids = {asset.asset_id for asset in loaded.assets}
    assert "asset_supplement" in loaded_ids, (
        "Supplement-Asset wurde beim Laden fälschlich verworfen — "
        f"geladene assets: {loaded.assets}"
    )
    assert "asset_original" in loaded_ids


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


def test_folder_has_usable_inventory_data_false_when_nothing_exists(
    temp_project_layout: dict[str, Path],
) -> None:
    project = _sample_project(temp_project_layout)
    (temp_project_layout["project_root"] / "Grand Canyon").mkdir(parents=True, exist_ok=True)
    assert folder_has_usable_inventory_data(project, "Grand Canyon") is False


def test_folder_has_usable_inventory_data_true_from_flat_inventory_file(
    temp_project_layout: dict[str, Path],
) -> None:
    project = _sample_project(temp_project_layout)
    media_path = str(temp_project_layout["project_root"] / "Grand Canyon" / "clip.mp4")
    item = AssetFolderAnalysis(
        folder="Grand Canyon",
        media_files=[media_path],
        assets=[AssetMediaAnalysis(path=media_path, description="Steile Felswand")],
    )
    save_folder_inventory(get_folder_inventory_path(project.work_dir_path, "Grand Canyon"), item)

    assert folder_has_usable_inventory_data(project, "Grand Canyon") is True


def test_folder_has_usable_inventory_data_true_from_cache_when_flat_file_missing(
    temp_project_layout: dict[str, Path],
) -> None:
    """Regression (Nutzerfeedback Juli 2026): Dramaturgie zeigte 'für keinen
    Ordner liegt ein Inventory vor', obwohl alle Assets erfolgreich analysiert
    waren. Ursache: sync_folder_inventory_with_status() löscht die flache
    Inventar-Datei wieder, sobald auch nur ein Asset im Ordner nicht als
    vollständig ("grün") analysiert gilt — obwohl im Cache bereits
    erfolgreich analysierte Assets liegen. folder_has_usable_inventory_data()
    muss diesen Fall trotzdem als 'hat Inventory' erkennen, weil genau dieser
    Cache-Fallback auch von der Dramaturgie-Planung selbst verwendet wird."""
    project = _sample_project(temp_project_layout)
    folder_dir = temp_project_layout["project_root"] / "Grand Canyon"
    folder_dir.mkdir(parents=True, exist_ok=True)
    media_path = folder_dir / "clip.mp4"
    media_path.write_bytes(b"mp4")

    # Keine flache Inventar-JSON vorhanden — nur der Analyse-Cache.
    inventory_path = get_folder_inventory_path(project.work_dir_path, "Grand Canyon")
    assert not inventory_path.is_file()

    save_cached_media(
        media_cache_path(project, "Grand Canyon", media_path),
        AssetMediaAnalysis(path=str(media_path), description="Steile Felswand aus Cache"),
    )

    assert folder_has_usable_inventory_data(project, "Grand Canyon") is True


def test_folder_has_usable_inventory_data_false_when_cache_has_only_placeholder(
    temp_project_layout: dict[str, Path],
) -> None:
    project = _sample_project(temp_project_layout)
    folder_dir = temp_project_layout["project_root"] / "Grand Canyon"
    folder_dir.mkdir(parents=True, exist_ok=True)
    media_path = folder_dir / "clip.mp4"
    media_path.write_bytes(b"mp4")

    # Kein Cache-Eintrag und keine Inventar-JSON — nur eine leere Platzhalter-
    # Beschreibung, wie sie load_folder_inventory() für nicht-gecachte Medien
    # synthetisiert.
    assert folder_has_usable_inventory_data(project, "Grand Canyon") is False


def test_materialize_refreshes_inventory_when_cache_is_newer(
    temp_project_layout: dict[str, Path],
) -> None:
    """Eine bezahlte Neuanalyse muss in der Inventar-JSON ankommen.

    ``should_skip_folder_analysis`` akzeptiert Legacy-Analysen als erfolgreich.
    Ohne Frische-Vergleich bliebe die alte Zeile stehen, obwohl der Cache
    bereits die neue v3-Fassung hält — der Cut-LLM läse weiter den alten Stand.
    """
    from otio_app.services.asset_analysis_signature import build_analysis_signature
    from otio_app.services.inventory_loader import (
        materialize_folder_inventory_from_cache,
    )

    project = _sample_project(temp_project_layout)
    media_path = temp_project_layout["project_root"] / "Grand Canyon" / "clip.mp4"

    legacy = AssetMediaAnalysis(
        path=str(media_path),
        description="Alte Beschreibung",
        asset_id="clip",
        analysis_status="complete",
    )
    save_cached_media(media_cache_path(project, "Grand Canyon", media_path), legacy)
    save_folder_inventory(
        get_folder_inventory_path(project.work_dir_path, "Grand Canyon"),
        AssetFolderAnalysis(
            folder="Grand Canyon",
            media_files=[str(media_path)],
            assets=[legacy],
        ),
    )

    fresh = legacy.model_copy(
        update={
            "description": "Neue v3-Beschreibung",
            "caption": "Steile Felswand",
            "content_tags": ["canyon", "rock"],
            "analysis_schema_version": "asset-analysis-v3",
            "analysis_parse_ok": True,
            "analysis_signature": build_analysis_signature(
                media_path, resolved_model_id="gemini-test"
            ),
        }
    )
    save_cached_media(media_cache_path(project, "Grand Canyon", media_path), fresh)

    item, error = materialize_folder_inventory_from_cache(project, "Grand Canyon")

    assert error is None
    assert item is not None
    assert item.assets[0].description == "Neue v3-Beschreibung"
    assert item.assets[0].content_tags == ["canyon", "rock"]
    # Auch auf der Platte, nicht nur im Rückgabewert.
    reloaded = load_folder_inventory(project, "Grand Canyon")
    assert reloaded.assets[0].caption == "Steile Felswand"


def test_materialize_reuses_inventory_when_cache_matches(
    temp_project_layout: dict[str, Path],
) -> None:
    """Ohne neuen Cache-Stand bleibt die vorhandene JSON unangetastet."""
    from otio_app.services.inventory_loader import (
        materialize_folder_inventory_from_cache,
    )

    project = _sample_project(temp_project_layout)
    media_path = temp_project_layout["project_root"] / "Grand Canyon" / "clip.mp4"
    entry = AssetMediaAnalysis(
        path=str(media_path),
        description="Unveraendert",
        asset_id="clip",
        analysis_status="complete",
    )
    save_cached_media(media_cache_path(project, "Grand Canyon", media_path), entry)
    inventory_path = get_folder_inventory_path(project.work_dir_path, "Grand Canyon")
    save_folder_inventory(
        inventory_path,
        AssetFolderAnalysis(
            folder="Grand Canyon", media_files=[str(media_path)], assets=[entry]
        ),
    )
    before = inventory_path.read_text(encoding="utf-8")

    item, error = materialize_folder_inventory_from_cache(project, "Grand Canyon")

    assert error is None
    assert item is not None
    assert inventory_path.read_text(encoding="utf-8") == before
