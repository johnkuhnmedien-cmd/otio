"""E2E-2.2a: Still-Hold scale/pad mit geraden Maßen."""

from __future__ import annotations

from pathlib import Path

import pytest

from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.services.without_voiceover_enhanced.media_hold import (
    MediaHoldError,
    ensure_still_hold_video,
    still_hold_video_filter,
)


def test_still_hold_filter_letterboxes_to_even_project_size() -> None:
    vf = still_hold_video_filter(width=1920, height=1080)
    assert "scale=1920:1080:force_original_aspect_ratio=decrease" in vf
    assert "pad=1920:1080:" in vf
    assert "ceil(iw/2)*2" in vf


def test_still_hold_filter_rounds_odd_project_size_even() -> None:
    vf = still_hold_video_filter(width=1921, height=1081)
    assert "scale=1920:1080:force_original_aspect_ratio=decrease" in vf
    assert "pad=1920:1080:" in vf


def test_still_hold_filter_fallback_even_scale_without_target() -> None:
    assert still_hold_video_filter() == "scale=ceil(iw/2)*2:ceil(ih/2)*2"


def test_recover_hold_cache_file_from_language_exports(tmp_path: Path) -> None:
    from otio_app.services.without_voiceover_enhanced.media_hold import (
        _hold_cache_dir,
        _recover_hold_cache_file,
    )

    work = tmp_path / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir()
    project = Project(
        id="p",
        name="p",
        project_root=str(tmp_path),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        language="de",
        asset_subdir_names=["Győr"],
        selected_asset_subdirs=["Győr"],
    )
    stolen = work / "IT" / "exports" / "hold_cache"
    stolen.mkdir(parents=True)
    name = "still_hold_ccf71abc1234def0.mp4"
    (stolen / name).write_bytes(b"from-it")

    found = _recover_hold_cache_file(project, name)
    assert found is not None
    assert found == _hold_cache_dir(project) / name
    assert found.read_bytes() == b"from-it"


def test_still_hold_rejects_mp4_without_loop(tmp_path: Path) -> None:
    """Regression Győr_slot_012: -loop 1 gilt nur für Fotos, nicht für MP4."""
    work = tmp_path / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir()
    video = tmp_path / "Győr_Asset00014_3840x2160.mp4"
    video.write_bytes(b"not-an-image")
    project = Project(
        id="p",
        name="p",
        project_root=str(tmp_path),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        asset_subdir_names=["Győr"],
        selected_asset_subdirs=["Győr"],
    )
    with pytest.raises(MediaHoldError, match="nur für Fotos"):
        ensure_still_hold_video(project, video, duration_seconds=4.52, fps=25.0)



def test_still_hold_filter_letterboxes_to_even_project_size() -> None:
    vf = still_hold_video_filter(width=1920, height=1080)
    assert "scale=1920:1080:force_original_aspect_ratio=decrease" in vf
    assert "pad=1920:1080:" in vf
    assert "ceil(iw/2)*2" in vf


def test_still_hold_filter_rounds_odd_project_size_even() -> None:
    vf = still_hold_video_filter(width=1921, height=1081)
    assert "scale=1920:1080:force_original_aspect_ratio=decrease" in vf
    assert "pad=1920:1080:" in vf


def test_still_hold_filter_fallback_even_scale_without_target() -> None:
    assert still_hold_video_filter() == "scale=ceil(iw/2)*2:ceil(ih/2)*2"
