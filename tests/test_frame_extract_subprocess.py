"""Tests für robuste FFmpeg/FFprobe-Ausgabe."""

from __future__ import annotations

from otio_app.services.gemini_client import MediaFrameAnalysis

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from otio_app.services.asset_analyzer import analyze_asset_folders
from otio_app.services.frame_extract import extract_frames
from otio_app.services.media_inventory_cache import media_cache_path, save_cached_media
from tests.test_partial_asset_analysis import _current_cache_entry, _project


def test_extract_frames_survives_binary_ffmpeg_stderr(
    temp_project_layout: dict[str, Path],
) -> None:
    media_path = temp_project_layout["project_root"] / "Grand Canyon" / "clip.mp4"
    output_dir = temp_project_layout["work_dir"] / "frames-test"

    def fake_run(cmd, **kwargs):
        frame_path = Path(cmd[-1])
        if str(frame_path).endswith(".jpg"):
            frame_path.parent.mkdir(parents=True, exist_ok=True)
            frame_path.write_bytes(b"jpeg")
        completed = MagicMock()
        completed.returncode = 0
        completed.stdout = b""
        completed.stderr = b"\xceinvalid stderr"
        return completed

    with patch("otio_app.services.frame_extract.subprocess.run", side_effect=fake_run):
        frames = extract_frames(media_path, output_dir, 1)

    assert len(frames) == 1
    assert frames[0].is_file()


def test_analyze_asset15_with_binary_ffmpeg_stderr(
    temp_project_layout: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project(temp_project_layout)
    folder = temp_project_layout["project_root"] / "Grand Canyon"
    clip1 = folder / "clip.mp4"
    clip2 = folder / "Florida_Keys_Asset15.mp4"
    clip2.write_bytes(b"video15")
    save_cached_media(
        media_cache_path(project, "Grand Canyon", clip1),
        _current_cache_entry(clip1, "OK"),
    )

    def fake_run(cmd, **kwargs):
        frame_path = Path(cmd[-1])
        if str(frame_path).endswith(".jpg"):
            frame_path.parent.mkdir(parents=True, exist_ok=True)
            frame_path.write_bytes(b"jpeg")
        completed = MagicMock()
        completed.returncode = 0
        completed.stdout = b""
        completed.stderr = b"\xceinvalid stderr"
        return completed

    monkeypatch.setattr(
        "otio_app.services.frame_extract.subprocess.run",
        fake_run,
    )
    def fake_describe(
        media_name: str,
        folder_name: str,
        frame_paths: list[Path],
        language: str,
        *,
        model: str | None = None,
    ) -> MediaFrameAnalysis:
        return MediaFrameAnalysis.successful(description=f"Beschreibung für {media_name}")
    monkeypatch.setattr(
        "otio_app.services.asset_analyzer.analyze_media_from_frames",
        fake_describe,
    )

    _, report = analyze_asset_folders(project, ["Grand Canyon"], use_api=True)

    assert report.media_analyzed == 1
    assert report.media_failed == 0
    assert media_cache_path(project, "Grand Canyon", clip2).is_file()
