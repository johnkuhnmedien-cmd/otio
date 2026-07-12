"""Clean Media + Analyse für `{folder}/_supplemental/_provider/`."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from otio_app.analysis_models import CleanMediaEntry, SupplementAssetSidecar
from otio_app.defaults import SUPPLEMENTAL_FOLDER_NAME
from otio_app.models import Project
from otio_app.project_layout import (
    clean_output_path_for_media,
    get_folder_supplemental_dir,
    get_provider_supplemental_dir,
)
from otio_app.services.clean_media import (
    CLEAN_STATUS_CLEAN,
    discover_supplemental_media_paths,
    find_clean_file_for_media,
    list_folder_media,
    resolve_effective_media_path,
)
from otio_app.services.media_inventory_cache import discover_folder_media_paths
from otio_app.services.supplement_pipeline import analyze_supplement_asset, save_sidecar


def _project(tmp_path: Path, *, folder_name: str = "Florida Keys") -> Project:
    root = tmp_path / "USA"
    folder = root / folder_name
    folder.mkdir(parents=True)
    (folder / "clip.mp4").write_bytes(b"video")
    work = root / "_otio"
    work.mkdir(parents=True)
    return Project(
        id="supp-clean-test",
        name="USA",
        project_root=str(root),
        work_dir=str(work),
        asset_subdir_names=[folder_name],
        selected_asset_subdirs=[folder_name],
    )


def _write_supplement(project: Project, folder_name: str, *, provider: str = "pexels") -> Path:
    dest = get_provider_supplemental_dir(project.project_root_path, folder_name, provider)
    dest.mkdir(parents=True)
    media = dest / "stock_clip.mp4"
    media.write_bytes(b"supp-video")
    sidecar = SupplementAssetSidecar(
        asset_id="supp-1",
        provider=provider,
        local_path=str(media),
        supplement_request_id="req-1",
        media_type="video",
    )
    save_sidecar(sidecar)
    return media


def test_discover_supplemental_media_paths(tmp_path: Path) -> None:
    project = _project(tmp_path)
    media = _write_supplement(project, "Florida Keys")
    found = discover_supplemental_media_paths(project, "Florida Keys")
    assert media in found
    assert get_folder_supplemental_dir(project.project_root_path, "Florida Keys").is_dir()


def test_list_folder_media_includes_supplemental(tmp_path: Path) -> None:
    project = _project(tmp_path)
    supp = _write_supplement(project, "Florida Keys")
    all_media = list_folder_media(project, "Florida Keys")
    names = {path.name for path in all_media}
    assert "clip.mp4" in names
    assert "stock_clip.mp4" in names
    assert any(SUPPLEMENTAL_FOLDER_NAME in path.parts for path in all_media)
    assert supp in all_media

    primary_only = list_folder_media(project, "Florida Keys", include_supplemental=False)
    assert all(SUPPLEMENTAL_FOLDER_NAME not in path.parts for path in primary_only)


def test_primary_discovery_excludes_supplemental(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _write_supplement(project, "Florida Keys")
    discovered = discover_folder_media_paths(project, "Florida Keys")
    assert all(SUPPLEMENTAL_FOLDER_NAME not in path.parts for path in discovered)
    assert any(path.name == "clip.mp4" for path in discovered)


def test_clean_output_path_isolates_supplemental(tmp_path: Path) -> None:
    project = _project(tmp_path)
    primary = project.project_root_path / "Florida Keys" / "clip.mp4"
    supp = _write_supplement(project, "Florida Keys")

    primary_clean = clean_output_path_for_media(project.work_dir_path, "Florida Keys", primary)
    supp_clean = clean_output_path_for_media(project.work_dir_path, "Florida Keys", supp)

    assert primary_clean.parent.name == "Florida_Keys"
    assert SUPPLEMENTAL_FOLDER_NAME in supp_clean.parts
    assert "_pexels" in supp_clean.parts
    assert primary_clean != supp_clean


def test_find_clean_does_not_cross_match_primary_and_supplement(tmp_path: Path) -> None:
    project = _project(tmp_path)
    folder = "Florida Keys"
    primary = project.project_root_path / folder / "clip.mp4"
    # Gleicher Stem wie Primary, aber unter Supplemental.
    dest = get_provider_supplemental_dir(project.project_root_path, folder, "pexels")
    dest.mkdir(parents=True)
    supp = dest / "clip.mp4"
    supp.write_bytes(b"supp")

    primary_clean = clean_output_path_for_media(project.work_dir_path, folder, primary)
    supp_clean = clean_output_path_for_media(project.work_dir_path, folder, supp)
    primary_clean.parent.mkdir(parents=True, exist_ok=True)
    supp_clean.parent.mkdir(parents=True, exist_ok=True)
    primary_clean.write_bytes(b"clean-primary")
    supp_clean.write_bytes(b"clean-supp")

    assert find_clean_file_for_media(project, folder, primary) == primary_clean.resolve()
    assert find_clean_file_for_media(project, folder, supp) == supp_clean.resolve()
    assert resolve_effective_media_path(project, folder, primary) == primary_clean.resolve()
    assert resolve_effective_media_path(project, folder, supp) == supp_clean.resolve()


def test_analyze_supplement_prefers_clean_media(tmp_path: Path) -> None:
    project = _project(tmp_path)
    folder = "Florida Keys"
    media = _write_supplement(project, folder)
    clean = clean_output_path_for_media(project.work_dir_path, folder, media)
    clean.parent.mkdir(parents=True, exist_ok=True)
    clean.write_bytes(b"clean-bytes")

    sidecar = SupplementAssetSidecar(
        asset_id="supp-1",
        provider="pexels",
        local_path=str(media),
        supplement_request_id="req-1",
        media_type="video",
    )

    with (
        patch(
            "otio_app.services.supplement_pipeline.extract_frames",
            return_value=[tmp_path / "frame.jpg"],
        ) as extract_mock,
        patch(
            "otio_app.services.supplement_pipeline.is_gemini_configured",
            return_value=False,
        ),
        patch(
            "otio_app.services.supplement_pipeline.revalidate_supplement_asset_against_request",
            return_value={"status": "PASS", "score": 1.0, "reason": "ok"},
        ),
    ):
        (tmp_path / "frame.jpg").write_bytes(b"jpg")
        asset = analyze_supplement_asset(
            project,
            folder_name=folder,
            local_path=media,
            sidecar=sidecar,
        )

    assert extract_mock.call_args.args[0] == clean.resolve()
    assert asset.path == str(media)
    assert asset.approved_for_cut_plan is True
