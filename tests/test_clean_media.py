"""Tests für Clean Media."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from otio_app.analysis_models import CleanMediaEntry, CleanMediaManifest, MediaProbeInfo
from otio_app.models import Project
from otio_app.services.clean_media import (
    CLEAN_STATUS_CLEAN,
    CLEAN_STATUS_NEEDS_TRANSCODE,
    CLEAN_STATUS_OK,
    folder_clean_media_ready,
    load_clean_media_manifest,
    needs_transcode,
    process_media_file,
    resolve_effective_media_path,
    save_clean_media_manifest,
    validate_media_file,
)
from otio_app.services.media_inventory_cache import resolve_media_for_analysis
from otio_app.services.otio_exporter import build_otio_timeline
from otio_app.analysis_models import EditPlanSettings, EditPlanShot
from otio_app.services.otio_exporter import MergedEditPlanResult


def _project(tmp_path: Path) -> Project:
    root = tmp_path / "USA"
    folder = root / "Florida Keys"
    folder.mkdir(parents=True)
    (folder / "clip.mp4").write_bytes(b"video")
    return Project(
        id="clean-test",
        name="USA",
        project_root=str(root),
        work_dir=str(root / "_otio"),
        asset_subdir_names=["Florida Keys"],
        selected_asset_subdirs=["Florida Keys"],
    )


def test_needs_transcode_detects_hevc() -> None:
    probe = MediaProbeInfo(video_codec="hevc", container="mp4")
    path = Path("/tmp/sample.mp4")
    assert needs_transcode(path, probe, decode_ok=True) is True


def test_needs_transcode_accepts_h264_mp4() -> None:
    probe = MediaProbeInfo(video_codec="h264", container="mp4", pixel_format="yuv420p")
    path = Path("/tmp/sample.mp4")
    assert needs_transcode(path, probe, decode_ok=True) is False


@patch("otio_app.services.clean_media.test_decode", return_value=(True, None))
@patch("otio_app.services.clean_media.probe_media")
def test_validate_media_file_ok(mock_probe, _mock_decode, tmp_path: Path) -> None:
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"x")
    mock_probe.return_value = MediaProbeInfo(
        video_codec="h264",
        container="mp4",
        pixel_format="yuv420p",
    )
    entry = validate_media_file(media)
    assert entry.status == CLEAN_STATUS_OK
    assert entry.needs_transcode is False


@patch("otio_app.services.clean_media.transcode_to_clean")
@patch("otio_app.services.clean_media.test_decode", return_value=(False, "decode error"))
@patch("otio_app.services.clean_media.probe_media")
def test_process_media_file_transcodes_on_decode_failure(
    mock_probe,
    _mock_decode,
    mock_transcode,
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    media = project.project_root_path / "Florida Keys" / "clip.mp4"

    def _fake_transcode(original: Path, output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"clean")

    mock_probe.return_value = MediaProbeInfo(video_codec="hevc", container="mp4")
    mock_transcode.side_effect = _fake_transcode

    entry = process_media_file(project, "Florida Keys", media)
    assert entry.status == CLEAN_STATUS_CLEAN
    assert entry.clean_path is not None
    assert Path(entry.clean_path).is_file()


def test_resolve_effective_media_path_uses_manifest(tmp_path: Path) -> None:
    project = _project(tmp_path)
    original = project.project_root_path / "Florida Keys" / "clip.mp4"
    clean = project.work_dir_path / "clean" / "Florida_Keys" / "clip.mp4"
    clean.parent.mkdir(parents=True, exist_ok=True)
    clean.write_bytes(b"clean")

    manifest = CleanMediaManifest(
        project_id=project.id,
        folder="Florida Keys",
        entries=[
            CleanMediaEntry(
                original_path=str(original.resolve()),
                clean_path=str(clean.resolve()),
                status=CLEAN_STATUS_CLEAN,
            )
        ],
    )
    manifest_path = project.work_dir_path / "clean_media" / "Florida_Keys.json"
    save_clean_media_manifest(manifest_path, manifest)

    resolved = resolve_effective_media_path(project, "Florida Keys", original)
    assert resolved == clean


def test_resolve_effective_media_path_falls_back_to_expected_clean_path(tmp_path: Path) -> None:
    project = _project(tmp_path)
    original = project.project_root_path / "Florida Keys" / "clip.mp4"
    expected_clean = project.work_dir_path / "clean" / "Florida_Keys" / "clip.mp4"
    expected_clean.parent.mkdir(parents=True, exist_ok=True)
    expected_clean.write_bytes(b"clean")

    manifest = CleanMediaManifest(
        project_id=project.id,
        folder="Florida Keys",
        entries=[
            CleanMediaEntry(
                original_path=str(original.resolve()),
                clean_path="/nonexistent/stale_clean.mp4",
                status=CLEAN_STATUS_CLEAN,
            )
        ],
    )
    save_clean_media_manifest(
        project.work_dir_path / "clean_media" / "Florida_Keys.json",
        manifest,
    )

    resolved = resolve_effective_media_path(project, "Florida Keys", original)
    assert resolved == expected_clean


def test_resolve_media_for_analysis_prefers_clean(tmp_path: Path) -> None:
    project = _project(tmp_path)
    original = project.project_root_path / "Florida Keys" / "clip.mp4"
    clean = project.work_dir_path / "clean" / "Florida_Keys" / "clip.mp4"
    clean.parent.mkdir(parents=True, exist_ok=True)
    clean.write_bytes(b"clean")

    manifest = CleanMediaManifest(
        project_id=project.id,
        folder="Florida Keys",
        entries=[
            CleanMediaEntry(
                original_path=str(original.resolve()),
                clean_path=str(clean.resolve()),
                status=CLEAN_STATUS_CLEAN,
            )
        ],
    )
    save_clean_media_manifest(
        project.work_dir_path / "clean_media" / "Florida_Keys.json",
        manifest,
    )

    resolved = resolve_media_for_analysis(project, "Florida Keys", original)
    assert resolved == clean


def test_folder_clean_media_ready_with_ok_manifest(tmp_path: Path) -> None:
    project = _project(tmp_path)
    original = project.project_root_path / "Florida Keys" / "clip.mp4"
    manifest = CleanMediaManifest(
        project_id=project.id,
        folder="Florida Keys",
        entries=[
            CleanMediaEntry(
                original_path=str(original.resolve()),
                status=CLEAN_STATUS_OK,
            )
        ],
    )
    save_clean_media_manifest(
        project.work_dir_path / "clean_media" / "Florida_Keys.json",
        manifest,
    )
    assert folder_clean_media_ready(project, "Florida Keys") is True


def test_otio_export_uses_clean_path(tmp_path: Path) -> None:
    project = _project(tmp_path)
    original = project.project_root_path / "Florida Keys" / "clip.mp4"
    clean = project.work_dir_path / "clean" / "Florida_Keys" / "clip.mp4"
    clean.parent.mkdir(parents=True, exist_ok=True)
    clean.write_bytes(b"clean")

    save_clean_media_manifest(
        project.work_dir_path / "clean_media" / "Florida_Keys.json",
        CleanMediaManifest(
            project_id=project.id,
            folder="Florida Keys",
            entries=[
                CleanMediaEntry(
                    original_path=str(original.resolve()),
                    clean_path=str(clean.resolve()),
                    status=CLEAN_STATUS_CLEAN,
                )
            ],
        ),
    )

    shot = EditPlanShot(
        voice_file="/voice/test.wav",
        folder="Florida Keys",
        voice_start_sec=0.0,
        voice_end_sec=3.0,
        duration_sec=3.0,
        asset_path=str(original),
        motif="test",
        passage_text="text",
    )
    merged = MergedEditPlanResult(
        shots=[shot],
        settings=EditPlanSettings(),
        included_folders=["Florida Keys"],
        skipped_folders=[],
        warnings=[],
    )

    with patch("otio_app.services.otio_exporter.probe_duration_seconds", return_value=10.0):
        timeline = build_otio_timeline(project, merged)

    video_clip = timeline.tracks[0][0]
    assert clean.name in video_clip.media_reference.target_url
    assert video_clip.name == clean.name
