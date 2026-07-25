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
