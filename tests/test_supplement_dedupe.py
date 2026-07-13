"""Deduplizierung von Supplement-Downloads unter `_supplemental/`."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from otio_app.analysis_models import SupplementAssetSidecar, SupplementCandidate, SupplementRequest
from otio_app.defaults import SUPPLEMENT_SOURCE_PEXELS
from otio_app.models import Project
from otio_app.project_layout import get_provider_supplemental_dir
from otio_app.services.supplement_dedupe import (
    cleanup_supplement_duplicates,
    find_existing_provider_asset,
    provider_asset_already_downloaded,
    scan_supplement_duplicates,
)
from otio_app.services.supplement_pipeline import acquire_supplement_candidate, acquire_top_candidates
from otio_app.services.supplement_sources.base import SupplementAsset


def _project(tmp_path: Path, *, folder_name: str = "Antelope Canyon") -> Project:
    root = tmp_path / "USA"
    folder = root / folder_name
    folder.mkdir(parents=True)
    work = root / "_otio"
    work.mkdir(parents=True)
    return Project(
        id="dedupe-test",
        name="USA",
        project_root=str(root),
        work_dir=str(work),
        asset_subdir_names=[folder_name],
        selected_asset_subdirs=[folder_name],
    )


def _write_dup(
    project: Project,
    folder_name: str,
    *,
    provider_asset_id: str,
    request_id: str,
    approved: bool = False,
) -> Path:
    dest = get_provider_supplemental_dir(project.project_root_path, folder_name, "pexels")
    dest.mkdir(parents=True, exist_ok=True)
    media = dest / f"Antelope_Canyon_{request_id}_pexels_{provider_asset_id}.mp4"
    media.write_bytes(b"video-" + request_id.encode())
    sidecar = SupplementAssetSidecar(
        asset_id=f"asset_pexels_{provider_asset_id}",
        supplement_request_id=request_id,
        provider=SUPPLEMENT_SOURCE_PEXELS,
        provider_asset_id=provider_asset_id,
        local_path=str(media),
        media_type="video",
        approved_for_cut_plan=approved,
        supplement_validation_status="PASS" if approved else "",
        downloaded_at=None,
    )
    media.with_suffix(media.suffix + ".asset.json").write_text(
        sidecar.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return media


def _candidate(provider_asset_id: str = "27608379") -> SupplementCandidate:
    return SupplementCandidate(
        candidate_id=f"cand_{provider_asset_id}",
        supplement_request_id="supp_req_new",
        provider=SUPPLEMENT_SOURCE_PEXELS,
        provider_asset_id=provider_asset_id,
        download_url="https://example.com/video.mp4",
        download_enabled=True,
        is_mock=False,
        location_match="exact",
        media_type="video",
    )


def _request(folder_name: str = "Antelope Canyon") -> SupplementRequest:
    return SupplementRequest(
        supplement_request_id="supp_req_new",
        section_id="section_antelope_canyon",
        folder_name=folder_name,
        beat_id="beat_1",
        passage_text="test passage",
        status="CANDIDATES_FOUND",
        selected_source=SUPPLEMENT_SOURCE_PEXELS,
    )


def test_find_existing_by_provider_asset_id(tmp_path: Path) -> None:
    project = _project(tmp_path)
    kept = _write_dup(project, "Antelope Canyon", provider_asset_id="27608379", request_id="supp_req_aaa")
    _write_dup(project, "Antelope Canyon", provider_asset_id="27608379", request_id="supp_req_bbb")

    found = find_existing_provider_asset(
        project,
        "Antelope Canyon",
        provider="pexels",
        provider_asset_id="27608379",
    )
    assert found is not None
    assert found.parent == kept.parent
    assert provider_asset_already_downloaded(
        project,
        "Antelope Canyon",
        provider="pexels",
        provider_asset_id="27608379",
    )


def test_scan_and_cleanup_keeps_approved(tmp_path: Path) -> None:
    project = _project(tmp_path)
    older = _write_dup(
        project,
        "Antelope Canyon",
        provider_asset_id="27608379",
        request_id="supp_req_old",
        approved=False,
    )
    keeper = _write_dup(
        project,
        "Antelope Canyon",
        provider_asset_id="27608379",
        request_id="supp_req_new",
        approved=True,
    )
    _write_dup(
        project,
        "Antelope Canyon",
        provider_asset_id="999",
        request_id="supp_req_unique",
        approved=True,
    )

    groups = scan_supplement_duplicates(project, "Antelope Canyon")
    assert len(groups) == 1
    assert groups[0].keep == keeper
    assert older in groups[0].remove

    report = cleanup_supplement_duplicates(project, "Antelope Canyon", dry_run=False)
    assert not older.exists()
    assert keeper.exists()
    assert len(report.deleted_media) == 1
    assert find_existing_provider_asset(
        project,
        "Antelope Canyon",
        provider="pexels",
        provider_asset_id="27608379",
    ) == keeper


def test_acquire_reuses_existing_without_download(tmp_path: Path) -> None:
    project = _project(tmp_path)
    existing = _write_dup(
        project,
        "Antelope Canyon",
        provider_asset_id="27608379",
        request_id="supp_req_old",
    )
    candidate = _candidate()
    request = _request()

    mock_adapter = MagicMock()
    mock_adapter.readiness.return_value = MagicMock(
        status="READY",
        is_mock=False,
        message="ok",
    )
    mock_adapter.acquire.side_effect = AssertionError("should not download")

    with patch(
        "otio_app.services.supplement_pipeline.get_supplement_adapter",
        return_value=mock_adapter,
    ), patch(
        "otio_app.services.supplement_pipeline.update_request",
    ):
        asset = acquire_supplement_candidate(project, candidate, request)

    assert asset.local_path == existing
    mock_adapter.acquire.assert_not_called()
    # Nur die eine Originaldatei — kein neues Duplikat
    dest = get_provider_supplemental_dir(project.project_root_path, "Antelope Canyon", "pexels")
    assert len(list(dest.glob("*.mp4"))) == 1


def test_acquire_top_skips_already_downloaded(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _write_dup(
        project,
        "Antelope Canyon",
        provider_asset_id="111",
        request_id="supp_req_old",
    )
    request = _request()
    candidates = [_candidate("111"), _candidate("222"), _candidate("333")]

    downloaded: list[str] = []

    def fake_acquire(project_arg, candidate, request_arg):
        downloaded.append(candidate.provider_asset_id)
        path = get_provider_supplemental_dir(
            project_arg.project_root_path, request_arg.folder_name, "pexels"
        ) / f"new_{candidate.provider_asset_id}.mp4"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"new")
        sidecar = SupplementAssetSidecar(
            asset_id=f"asset_pexels_{candidate.provider_asset_id}",
            supplement_request_id=request_arg.supplement_request_id,
            provider="pexels",
            provider_asset_id=candidate.provider_asset_id,
            local_path=str(path),
        )
        return SupplementAsset(local_path=path, sidecar=sidecar)

    with patch(
        "otio_app.services.supplement_pipeline.acquire_supplement_candidate",
        side_effect=fake_acquire,
    ):
        results = acquire_top_candidates(project, candidates, request, max_count=2)

    assert downloaded == ["222", "333"]
    assert len(results) == 2
