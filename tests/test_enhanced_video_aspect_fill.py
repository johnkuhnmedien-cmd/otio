"""Enhanced OTIO: Videos auf Projekt-16:9 cover-füllen (kein Letterbox)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.services.clean_media_settings import (
    CleanMediaSettings,
    save_clean_media_settings,
)
from otio_app.services.clean_media import probe_media
from otio_app.services.otio_media_transform import ensure_export_media_for_export
from otio_app.services.without_voiceover_enhanced.models import ResolvedShot
from otio_app.services.without_voiceover_enhanced.otio_export_service import (
    _ensure_shot_media_for_export,
)


def _project(tmp_path: Path) -> Project:
    root = tmp_path / "Irland"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    folder = root / "Ring of Kerry"
    folder.mkdir()
    return Project(
        id="enh-aspect-fill",
        name="Aspect Fill",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        language="en",
        fps=25.0,
        width=1920,
        height=1080,
        asset_subdir_names=["Ring of Kerry"],
        selected_asset_subdirs=["Ring of Kerry"],
    )


def _ffmpeg_ultrawide(path: Path, *, duration: float = 1.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c=green:s=2048x1080:d={duration}:r=25",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")[:400]


def test_ensure_export_media_override_fills_ultrawide_even_when_setting_off(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    # Setting aus — Enhanced-Export übergibt auto_zoom_fill=True trotzdem.
    save_clean_media_settings(project, CleanMediaSettings(auto_zoom_fill=False))
    source = Path(project.project_root) / "Ring of Kerry" / "Asset00003.mov"
    _ffmpeg_ultrawide(source)
    src = probe_media(source)
    assert src.width == 2048 and src.height == 1080

    filled = ensure_export_media_for_export(
        project,
        "Ring of Kerry",
        source,
        auto_zoom_fill=True,
    )
    assert filled.is_file()
    out = probe_media(filled)
    assert out.width == 1920
    assert out.height == 1080
    assert filled.resolve() != source.resolve()


def test_enhanced_shot_export_uses_aspect_filled_video(tmp_path: Path) -> None:
    project = _project(tmp_path)
    save_clean_media_settings(project, CleanMediaSettings(auto_zoom_fill=False))
    source = Path(project.project_root) / "Ring of Kerry" / "Asset00003.mov"
    _ffmpeg_ultrawide(source, duration=2.0)

    shot = ResolvedShot(
        shot_id="Ring_of_Kerry_slot_001",
        asset_id="asset_ring_of_kerry_asset00003",
        folder_name="Ring of Kerry",
        timeline_start_seconds=0.0,
        timeline_end_seconds=1.5,
        source_start_seconds=0.0,
        source_end_seconds=1.5,
        resolved_media_path=str(source),
    )
    path, _avail, src_start, src_end, _rate = _ensure_shot_media_for_export(
        project, shot, fps=25.0
    )
    assert path.is_file()
    out = probe_media(path)
    assert out.width == 1920
    assert out.height == 1080
    assert src_end > src_start
