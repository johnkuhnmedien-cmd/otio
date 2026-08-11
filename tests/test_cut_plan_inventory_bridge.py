"""Cut-Plan-Supplements ins Folder-Inventory übernehmen (ohne VO)."""

from __future__ import annotations

from otio_app.services.gemini_client import MediaFrameAnalysis

from pathlib import Path
from unittest.mock import patch

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis
from otio_app.defaults import (
    CUT_PLAN_SUPPLEMENT_REQUEST_STATUS_ACCEPTED,
    SUPPLEMENT_SOURCE_PEXELS,
)
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import (
    get_cut_plan_supplement_asset_request_dir,
    get_folder_inventory_path,
)
from otio_app.services.cut_plan_inventory_bridge import (
    import_accepted_cut_plan_supplements_into_inventory,
    is_external_inventory_media_path,
    list_accepted_cut_plan_supplements_pending_inventory,
)
from otio_app.services.inventory_loader import (
    folder_inventory_matches_media,
    load_folder_inventory,
    save_folder_inventory,
)
from otio_app.services.voiceover_generation.cut_plan_supplement_bridge import (
    save_cut_plan_supplement_manifest,
    save_cut_plan_supplement_requests,
)
from otio_app.services.voiceover_generation.cut_plan_supplement_models import (
    CutPlanSupplementManifestDocument,
    CutPlanSupplementManifestEntry,
    CutPlanSupplementManifestValidation,
    CutPlanSupplementRequest,
    CutPlanSupplementRequestsDocument,
)


def _project(tmp_path: Path) -> Project:
    root = tmp_path / "USA"
    folder = root / "Antelope Canyon"
    folder.mkdir(parents=True)
    (folder / "primary.mp4").write_bytes(b"primary")
    work = root / "_otio"
    work.mkdir(parents=True)
    return Project(
        id="cut-inv-test",
        name="USA",
        project_root=str(root),
        work_dir=str(work),
        asset_subdir_names=["Antelope Canyon"],
        selected_asset_subdirs=["Antelope Canyon"],
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        language="de",
    )


def test_is_external_inventory_media_path() -> None:
    assert is_external_inventory_media_path("/x/Antelope Canyon/_supplemental/_pexels/a.mp4")
    assert is_external_inventory_media_path(
        "/x/_otio/de/voiceover_generation/cut_plan/supplement_assets/req/a.mp4"
    )
    assert not is_external_inventory_media_path("/x/Antelope Canyon/primary.mp4")


def test_folder_inventory_matches_media_ignores_cut_plan_paths(tmp_path: Path) -> None:
    project = _project(tmp_path)
    primary = project.project_root_path / "Antelope Canyon" / "primary.mp4"
    supp = (
        get_cut_plan_supplement_asset_request_dir(project.language_work_dir_path, "req_1")
        / "clip.mp4"
    )
    item = AssetFolderAnalysis(
        folder="Antelope Canyon",
        media_files=[str(primary), str(supp)],
        assets=[
            AssetMediaAnalysis(path=str(primary), analysis_status="complete", description="p"),
            AssetMediaAnalysis(path=str(supp), analysis_status="complete", description="s"),
        ],
    )
    assert folder_inventory_matches_media(item, [primary]) is True


def test_import_accepted_cut_plan_supplements_into_inventory(tmp_path: Path) -> None:
    project = _project(tmp_path)
    folder = "Antelope Canyon"
    req_dir = get_cut_plan_supplement_asset_request_dir(project.language_work_dir_path, "req_1")
    req_dir.mkdir(parents=True)
    media = req_dir / "Antelope_Canyon_req_1_pexels_27608379.mp4"
    media.write_bytes(b"supplement-bytes")

    primary = project.project_root_path / folder / "primary.mp4"
    save_folder_inventory(
        get_folder_inventory_path(project.work_dir_path, folder),
        AssetFolderAnalysis(
            folder=folder,
            media_files=[str(primary)],
            assets=[
                AssetMediaAnalysis(
                    path=str(primary),
                    description="primary canyon",
                    frames_used=["f.jpg"],
                    analysis_status="complete",
                    asset_id="primary_1",
                )
            ],
        ),
    )

    save_cut_plan_supplement_requests(
        project,
        CutPlanSupplementRequestsDocument(
            project_id=project.id,
            requests=[
                CutPlanSupplementRequest(
                    request_id="req_1",
                    cut_item_id="item_1",
                    folder_name=folder,
                    text="slot canyon light",
                    status=CUT_PLAN_SUPPLEMENT_REQUEST_STATUS_ACCEPTED,
                    accepted_asset_id="supplement_pexels_27608379",
                    accepted_asset_path=str(media),
                )
            ],
        ),
    )
    save_cut_plan_supplement_manifest(
        project,
        CutPlanSupplementManifestDocument(
            project_id=project.id,
            entries=[
                CutPlanSupplementManifestEntry(
                    asset_id="supplement_pexels_27608379",
                    provider=SUPPLEMENT_SOURCE_PEXELS,
                    provider_asset_id="27608379",
                    asset_path=str(media),
                    folder_name=folder,
                    first_request_id="req_1",
                    validations=[
                        CutPlanSupplementManifestValidation(
                            request_id="req_1",
                            validation_status="PASS",
                            validation_score=0.91,
                            description="Sunbeams in a narrow sandstone canyon",
                            accepted=True,
                        )
                    ],
                )
            ],
        ),
    )

    pending = list_accepted_cut_plan_supplements_pending_inventory(project)
    assert len(pending) == 1

    fake_frame = tmp_path / "frame.jpg"
    fake_frame.write_bytes(b"jpg")

    with patch(
        "otio_app.services.cut_plan_inventory_bridge.extract_frames",
        return_value=[fake_frame],
    ):
        report = import_accepted_cut_plan_supplements_into_inventory(project)

    assert report.imported == 1
    assert report.imported_by_folder[folder] == 1
    inventory = load_folder_inventory(project, folder)
    paths = {asset.path for asset in inventory.assets}
    assert str(media) in paths
    supp = next(asset for asset in inventory.assets if asset.path == str(media))
    assert supp.description == "Sunbeams in a narrow sandstone canyon"
    assert supp.approved_for_cut_plan is True
    assert supp.supplement_validation_status == "PASS"
    assert media.with_suffix(media.suffix + ".asset.json").is_file()

    # Idempotent
    with patch(
        "otio_app.services.cut_plan_inventory_bridge.extract_frames",
        return_value=[fake_frame],
    ):
        again = import_accepted_cut_plan_supplements_into_inventory(project)
    assert again.imported == 0
    assert again.skipped_existing == 1
    assert list_accepted_cut_plan_supplements_pending_inventory(project) == []


def test_analyze_missing_supplements_from_disk(tmp_path: Path) -> None:
    from otio_app.services.cut_plan_inventory_bridge import (
        analyze_and_import_missing_supplement_assets,
        list_supplement_assets_missing_from_inventory,
    )

    project = _project(tmp_path)
    folder = "Antelope Canyon"
    req_dir = get_cut_plan_supplement_asset_request_dir(project.language_work_dir_path, "req_orphan")
    req_dir.mkdir(parents=True)
    media = req_dir / "Antelope_Canyon_req_orphan_pexels_999.mp4"
    media.write_bytes(b"orphan-supp")

    primary = project.project_root_path / folder / "primary.mp4"
    save_folder_inventory(
        get_folder_inventory_path(project.work_dir_path, folder),
        AssetFolderAnalysis(
            folder=folder,
            media_files=[str(primary)],
            assets=[
                AssetMediaAnalysis(
                    path=str(primary),
                    description="primary",
                    frames_used=["f.jpg"],
                    analysis_status="complete",
                    asset_id="primary_1",
                )
            ],
        ),
    )
    save_cut_plan_supplement_manifest(
        project,
        CutPlanSupplementManifestDocument(
            project_id=project.id,
            entries=[
                CutPlanSupplementManifestEntry(
                    asset_id="supplement_pexels_999",
                    provider=SUPPLEMENT_SOURCE_PEXELS,
                    provider_asset_id="999",
                    asset_path=str(media),
                    folder_name=folder,
                    first_request_id="req_orphan",
                )
            ],
        ),
    )

    missing = list_supplement_assets_missing_from_inventory(project)
    assert any(Path(entry["asset_path"]).name == media.name for entry in missing)

    fake_frame = tmp_path / "frame2.jpg"
    fake_frame.write_bytes(b"jpg")

    with (
        patch(
            "otio_app.services.cut_plan_inventory_bridge.extract_frames",
            return_value=[fake_frame],
        ),
        patch(
            "otio_app.services.gemini_client.analyze_media_from_frames",
            return_value=MediaFrameAnalysis.successful(description="Orphan canyon shot at golden hour"),
        ),
        patch(
            "otio_app.services.gemini_client.is_gemini_configured",
            return_value=True,
        ),
    ):
        report = analyze_and_import_missing_supplement_assets(project)

    assert report.imported == 1
    inventory = load_folder_inventory(project, folder)
    supp = next(asset for asset in inventory.assets if asset.path == str(media))
    assert "golden hour" in supp.description
