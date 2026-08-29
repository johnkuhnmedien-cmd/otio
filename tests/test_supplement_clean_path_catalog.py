"""Supplement stock/downloads vs clean: Clean gewinnt, Accepted wird umgebogen."""

from __future__ import annotations

from pathlib import Path

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis
from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import get_folder_inventory_path
from otio_app.services.inventory_loader import save_folder_inventory
from otio_app.services.without_voiceover_enhanced.io_utils import load_model, write_json
from otio_app.services.without_voiceover_enhanced.local_media_service import (
    reconcile_accepted_supplement_paths,
)
from otio_app.services.without_voiceover_enhanced.models import (
    AcceptedSupplementsDocument,
    StockCandidate,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    accepted_supplements_path,
)
from otio_app.services.without_voiceover_enhanced.timeline_resolver import (
    build_asset_catalog,
)


def _project(tmp_path: Path) -> Project:
    root = tmp_path / "proj"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    (root / "clean" / "Yellowstone").mkdir(parents=True)
    return Project(
        id="clean-path",
        name="clean-path",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        asset_subdir_names=["Yellowstone"],
        selected_asset_subdirs=["Yellowstone"],
        fps=25.0,
    )


def test_catalog_prefers_clean_over_stock_download(tmp_path: Path) -> None:
    project = _project(tmp_path)
    cid = "manual_AdobeStock_106589320_fe779383f8"
    stock = (
        project.work_dir_path
        / "stock"
        / "downloads"
        / "Yellowstone_gap_001"
        / cid
        / f"{cid}.mov"
    )
    stock.parent.mkdir(parents=True)
    stock.write_bytes(b"\x00" * 64)
    clean = (
        Path(project.project_root)
        / "clean"
        / "Yellowstone"
        / f"{cid}_3840x2160.mp4"
    )
    clean.write_bytes(b"\x00" * 128)

    save_folder_inventory(
        get_folder_inventory_path(project.work_dir_path, "Yellowstone"),
        AssetFolderAnalysis(
            folder="Yellowstone",
            description="",
            media_files=[str(clean)],
            assets=[
                AssetMediaAnalysis(
                    path=str(clean),
                    description="geyser",
                    asset_id=cid,
                    media_type="video",
                    analysis_status="complete",
                )
            ],
        ),
    )
    write_json(
        accepted_supplements_path(project),
        AcceptedSupplementsDocument(
            script_version="v1",
            supplements=[
                StockCandidate(
                    candidate_id=cid,
                    provider="manual",
                    gap_id="Yellowstone_gap_001",
                    local_media_path=str(stock),
                    media_validation_status="export_ready",
                    media_type="video",
                    cut_plan_run_id="run1",
                )
            ],
        ),
    )

    catalog = build_asset_catalog(project, fps=25.0)
    assert not catalog.collisions
    assert cid in catalog.by_id
    assert "clean" in catalog.by_id[cid]["path"].replace("\\", "/")
    assert "stock/downloads" not in catalog.by_id[cid]["path"].replace("\\", "/")


def test_reconcile_accepted_points_to_clean(tmp_path: Path) -> None:
    project = _project(tmp_path)
    cid = "manual_AdobeStock_323917321_96318bb9b4"
    stock = (
        project.work_dir_path
        / "stock"
        / "downloads"
        / "Florida_Keys_gap_001"
        / cid
        / f"{cid}.mov"
    )
    stock.parent.mkdir(parents=True)
    stock.write_bytes(b"\x00" * 32)
    clean = (
        Path(project.project_root)
        / "clean"
        / "Florida_Keys"
        / f"{cid}_3840x2160.mp4"
    )
    clean.parent.mkdir(parents=True)
    clean.write_bytes(b"\x00" * 64)

    write_json(
        accepted_supplements_path(project),
        AcceptedSupplementsDocument(
            script_version="v1",
            supplements=[
                StockCandidate(
                    candidate_id=cid,
                    provider="manual",
                    gap_id="Florida_Keys_gap_001",
                    local_media_path=str(stock),
                    media_validation_status="export_ready",
                )
            ],
        ),
    )

    n = reconcile_accepted_supplement_paths(project)
    assert n == 1
    accepted = load_model(
        accepted_supplements_path(project), AcceptedSupplementsDocument
    )
    assert accepted is not None
    path = accepted.supplements[0].local_media_path.replace("\\", "/")
    assert "/clean/" in path
    assert "stock/downloads" not in path


def test_catalog_resolves_openverse_id_when_jpg_gone_and_clean_mp4_exists(
    tmp_path: Path,
) -> None:
    """Inventar zeigt die Openverse-ID, Datei ist aber nur noch als Clean-MP4 da.

    Typisch nach Funnel + Clean: Download-JPG gelöscht, Cut-Plan behält
    ``openverse_<uuid>``. Kapitel-Timing darf die ID nicht als unbekannt werten.
    """
    from otio_app.project_layout import get_folder_clean_output_dir
    from otio_app.services.without_voiceover_enhanced.timeline_resolver import (
        lookup_catalog_entry,
    )

    project = _project(tmp_path)
    folder = "Vogel"
    project.asset_subdir_names = [folder]
    project.selected_asset_subdirs = [folder]
    (Path(project.project_root) / folder).mkdir(parents=True)

    cid = "openverse_e61610da-6d0c-41f4-bf76-ecb24278f193"
    missing_jpg = (
        project.work_dir_path
        / "stock"
        / "downloads"
        / "Vogel_gap_009"
        / cid
        / f"{cid}.jpg"
    )
    clean = get_folder_clean_output_dir(project.work_dir_path, folder) / f"{cid}_3840x2160.mp4"
    clean.parent.mkdir(parents=True)
    clean.write_bytes(b"\x00" * 128)

    save_folder_inventory(
        get_folder_inventory_path(project.work_dir_path, folder),
        AssetFolderAnalysis(
            folder=folder,
            description="",
            media_files=[str(missing_jpg)],
            assets=[
                AssetMediaAnalysis(
                    path=str(missing_jpg),
                    description="birds over a lake",
                    asset_id=cid,
                    media_type="photo",
                    analysis_status="complete",
                    asset_origin="openverse",
                    approved_for_cut_plan=True,
                )
            ],
        ),
    )
    write_json(
        accepted_supplements_path(project),
        AcceptedSupplementsDocument(
            script_version="v1",
            supplements=[
                StockCandidate(
                    candidate_id=cid,
                    provider="openverse",
                    provider_asset_id="e61610da-6d0c-41f4-bf76-ecb24278f193",
                    gap_id="Vogel_gap_009",
                    local_media_path=str(missing_jpg),
                    media_validation_status="export_ready",
                    media_type="photo",
                    cut_plan_run_id="run-recut",
                )
            ],
        ),
    )

    catalog = build_asset_catalog(project, fps=25.0, folder_names=[folder])
    entry, err = lookup_catalog_entry(catalog, cid)
    assert err is None
    assert entry is not None
    assert Path(entry["path"]).resolve() == clean.resolve()


def test_catalog_aliases_slim_jpg_name_to_clean_mp4(tmp_path: Path) -> None:
    """Slim listet ``.jpg``, Disk-Index hat nur das Clean-``.mp4`` denselben Stem."""
    from otio_app.project_layout import get_folder_clean_output_dir
    from otio_app.services.inventory_prompt_view import slim_inventory_path_for
    from otio_app.services.without_voiceover_enhanced.timeline_resolver import (
        lookup_catalog_entry,
    )

    project = _project(tmp_path)
    folder = "Piran"
    project.asset_subdir_names = [folder]
    project.selected_asset_subdirs = [folder]
    (Path(project.project_root) / folder).mkdir(parents=True)

    cid = "wikimedia_5368375"
    inv_path = get_folder_inventory_path(project.work_dir_path, folder)
    missing_jpg = (
        project.work_dir_path / "stock" / "downloads" / "gone" / f"{cid}.jpg"
    )
    clean = get_folder_clean_output_dir(project.work_dir_path, folder) / f"{cid}.mp4"
    clean.parent.mkdir(parents=True)
    clean.write_bytes(b"\x00" * 96)

    save_folder_inventory(
        inv_path,
        AssetFolderAnalysis(
            folder=folder,
            description="",
            media_files=[str(missing_jpg)],
            assets=[
                AssetMediaAnalysis(
                    path=str(missing_jpg),
                    description="soca gorge",
                    asset_id=cid,
                    media_type="photo",
                    analysis_status="complete",
                    asset_origin="wikimedia",
                )
            ],
        ),
    )
    slim = slim_inventory_path_for(inv_path)
    assert slim.is_file()

    catalog = build_asset_catalog(project, fps=25.0, folder_names=[folder])
    entry, err = lookup_catalog_entry(catalog, cid)
    assert err is None
    assert entry is not None
    assert Path(entry["path"]).resolve() == clean.resolve()


def test_reconcile_accepted_finds_work_dir_clean(tmp_path: Path) -> None:
    from otio_app.project_layout import get_folder_clean_output_dir

    project = _project(tmp_path)
    cid = "openverse_2c687a9d-625d-4ee6-81c0-c7a32254217d"
    stock = (
        project.work_dir_path
        / "stock"
        / "downloads"
        / "Piran_gap_010"
        / cid
        / f"{cid}.jpg"
    )
    stock.parent.mkdir(parents=True)
    stock.write_bytes(b"\x00" * 32)
    clean = (
        get_folder_clean_output_dir(project.work_dir_path, "Piran")
        / f"{cid}_3840x2160.mp4"
    )
    clean.parent.mkdir(parents=True)
    clean.write_bytes(b"\x00" * 64)

    write_json(
        accepted_supplements_path(project),
        AcceptedSupplementsDocument(
            script_version="v1",
            supplements=[
                StockCandidate(
                    candidate_id=cid,
                    provider="openverse",
                    gap_id="Piran_gap_010",
                    local_media_path=str(stock),
                    media_validation_status="export_ready",
                    media_type="photo",
                )
            ],
        ),
    )

    n = reconcile_accepted_supplement_paths(project)
    assert n == 1
    accepted = load_model(
        accepted_supplements_path(project), AcceptedSupplementsDocument
    )
    assert accepted is not None
    path = accepted.supplements[0].local_media_path.replace("\\", "/")
    assert "/clean/" in path
    assert "stock/downloads" not in path


def test_catalog_finds_download_in_sibling_de_folder(tmp_path: Path) -> None:
    """IT-Timing: Datei liegt nur unter DE/voiceover_generation/stock/downloads."""
    from otio_app.defaults import VOICEOVER_GENERATION_SUBDIR
    from otio_app.services.without_voiceover_enhanced.paths import (
        STOCK_DOWNLOADS_SUBDIR,
        STOCK_SUBDIR,
    )
    from otio_app.services.without_voiceover_enhanced.timeline_resolver import (
        lookup_catalog_entry,
    )

    project = _project(tmp_path)
    project.language = "IT"
    folder = "Vogel"
    project.asset_subdir_names = [folder]
    project.selected_asset_subdirs = [folder]
    (Path(project.project_root) / folder).mkdir(parents=True)

    cid = "wikimedia_45709027"
    de_file = (
        project.work_dir_path
        / "DE"
        / VOICEOVER_GENERATION_SUBDIR
        / STOCK_SUBDIR
        / STOCK_DOWNLOADS_SUBDIR
        / "Vogel_gap_005"
        / cid
        / f"{cid}.jpg"
    )
    de_file.parent.mkdir(parents=True)
    de_file.write_bytes(b"\x00" * 80)
    missing_it = (
        project.work_dir_path
        / "IT"
        / VOICEOVER_GENERATION_SUBDIR
        / STOCK_SUBDIR
        / STOCK_DOWNLOADS_SUBDIR
        / "Vogel_gap_005"
        / cid
        / f"{cid}.jpg"
    )

    save_folder_inventory(
        get_folder_inventory_path(project.work_dir_path, folder),
        AssetFolderAnalysis(
            folder=folder,
            description="",
            media_files=[str(missing_it)],
            assets=[
                AssetMediaAnalysis(
                    path=str(missing_it),
                    description="vrsic pass",
                    asset_id=cid,
                    media_type="photo",
                    analysis_status="complete",
                    asset_origin="wikimedia",
                    approved_for_cut_plan=True,
                )
            ],
        ),
    )

    catalog = build_asset_catalog(project, fps=25.0, folder_names=[folder])
    entry, err = lookup_catalog_entry(catalog, cid)
    assert err is None
    assert entry is not None
    assert Path(entry["path"]).resolve() == de_file.resolve()


def test_resolve_relative_stock_path_via_sibling_language(tmp_path: Path) -> None:
    """Relativer Funnel-Pfad ohne DE/IT-Prefix — Datei liegt im DE-Ordner."""
    from otio_app.defaults import VOICEOVER_GENERATION_SUBDIR
    from otio_app.services.without_voiceover_enhanced.paths import (
        STOCK_DOWNLOADS_SUBDIR,
        STOCK_SUBDIR,
    )
    from otio_app.services.without_voiceover_enhanced.timeline_resolver import (
        _resolve_local_path,
    )

    project = _project(tmp_path)
    project.language = "IT"
    cid = "openverse_2c687a9d-625d-4ee6-81c0-c7a32254217d"
    rel = (
        f"{VOICEOVER_GENERATION_SUBDIR}/{STOCK_SUBDIR}/{STOCK_DOWNLOADS_SUBDIR}"
        f"/Piran_gap_010/{cid}/{cid}.jpg"
    )
    de_file = project.work_dir_path / "DE" / rel
    de_file.parent.mkdir(parents=True)
    de_file.write_bytes(b"\x00" * 48)

    resolved = _resolve_local_path(project, rel)
    assert resolved.is_file()
    assert resolved.resolve() == de_file.resolve()


def test_resolve_swaps_de_absolute_path_to_it_copy(tmp_path: Path) -> None:
    """Inventar zeigt auf DE-Absolutpfad, Datei liegt nur noch unter IT."""
    from otio_app.defaults import VOICEOVER_GENERATION_SUBDIR
    from otio_app.services.without_voiceover_enhanced.paths import (
        STOCK_DOWNLOADS_SUBDIR,
        STOCK_SUBDIR,
    )
    from otio_app.services.without_voiceover_enhanced.timeline_resolver import (
        _resolve_local_path,
    )

    project = _project(tmp_path)
    project.language = "IT"
    cid = "openverse_e2287d76-bcd0-4c86-bcde-932ee0422522"
    rel = (
        f"{VOICEOVER_GENERATION_SUBDIR}/{STOCK_SUBDIR}/{STOCK_DOWNLOADS_SUBDIR}"
        f"/Piran_gap_015/{cid}/{cid}.jpg"
    )
    de_missing = project.work_dir_path / "DE" / rel
    it_file = project.work_dir_path / "IT" / rel
    it_file.parent.mkdir(parents=True)
    it_file.write_bytes(b"\x00" * 48)

    resolved = _resolve_local_path(project, str(de_missing))
    assert resolved.is_file()
    assert resolved.resolve() == it_file.resolve()
