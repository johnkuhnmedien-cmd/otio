"""OTIO-Export: kein Inventar-ffprobe, parallele Still-Hold-/Fill-Vorbereitung."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from otio_app.defaults import (
    DEFAULT_ENHANCED_WORK_SUBDIR,
    ENHANCED_OTIO_MEDIA_MAX_WORKERS,
    ENHANCED_OTIO_MEDIA_MAX_WORKERS_4K,
)
from otio_app.models import Project, ProjectMode
from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis
from otio_app.project_layout import get_folder_inventory_path
from otio_app.services.inventory_loader import save_folder_inventory
from otio_app.services.media_utils import MediaTiming
from otio_app.services.without_voiceover_enhanced.otio_export_service import (
    EnhancedOtioExportCancelled,
    _prepare_export_shot_media,
    otio_export_media_workers,
)
from otio_app.services.without_voiceover_enhanced.timeline_resolver import (
    build_asset_catalog,
)
from otio_app.services.without_voiceover_enhanced import (
    otio_export_service as otio_export,
)


def _project(tmp_path: Path) -> Project:
    root = tmp_path / "Hungary"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    folder = "Budapest"
    (root / folder).mkdir()
    return Project(
        id="otio-speed",
        name="EN_Ungarn",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        language="en",
        asset_subdir_names=[folder],
        selected_asset_subdirs=[folder],
        width=1920,
        height=1080,
        fps=25.0,
    )


def test_otio_media_workers_cap_hd_and_4k() -> None:
    hd = MagicMock(height=1080)
    uhd = MagicMock(height=2160)
    assert otio_export_media_workers(hd, 40) == ENHANCED_OTIO_MEDIA_MAX_WORKERS
    assert otio_export_media_workers(uhd, 40) == ENHANCED_OTIO_MEDIA_MAX_WORKERS_4K
    assert otio_export_media_workers(hd, 1) == 1


def test_export_catalog_skips_inventory_ffprobe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path)
    folder = "Budapest"
    unused = Path(project.project_root) / folder / "unused_clip.mp4"
    unused.write_bytes(b"\x00" * 64)
    still = Path(project.project_root) / folder / "photo.jpg"
    still.write_bytes(b"not-a-real-jpeg")
    save_folder_inventory(
        get_folder_inventory_path(project.work_dir_path, folder),
        AssetFolderAnalysis(
            folder=folder,
            assets=[
                AssetMediaAnalysis(
                    path=str(unused),
                    description="unused",
                    asset_id="unused_clip",
                    media_type="video",
                ),
                AssetMediaAnalysis(
                    path=str(still),
                    description="still",
                    asset_id="photo_still",
                    media_type="photo",
                ),
            ],
            media_files=[str(unused), str(still)],
        ),
    )
    probed: list[str] = []

    def spy(path, default_rate=25.0):
        probed.append(Path(path).name)
        return MediaTiming(start_sec=0.0, duration_sec=8.0, rate=float(default_rate))

    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.timeline_resolver.probe_media_timing",
        spy,
    )
    catalog = build_asset_catalog(project, fps=25.0, probe_media=False)
    assert catalog.by_id
    assert probed == []

    catalog_probed = build_asset_catalog(project, fps=25.0, probe_media=True)
    assert catalog_probed.by_id
    assert "unused_clip.mp4" in probed


def test_prepare_export_shot_media_runs_in_parallel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path)
    active = 0
    max_active = 0
    lock = threading.Lock()

    def fake_ensure(*_a, **_k):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.25)
        with lock:
            active -= 1
        return (Path("/tmp/hold.mp4"), 0.0, 0.0, 4.0, 25.0)

    monkeypatch.setattr(otio_export, "_ensure_shot_media_for_export", fake_ensure)
    shots = [
        MagicMock(folder_name=f"Ch{index}", shot_id=f"s{index}", chapter_id=f"Ch{index}")
        for index in range(4)
    ]
    started = time.monotonic()
    prepared = _prepare_export_shot_media(
        project,
        shots,
        fps=25.0,
        catalog=MagicMock(),
        media_fill_cache={},
        allow_errors=True,
        max_workers=4,
    )
    elapsed = time.monotonic() - started
    assert len(prepared) == 4
    assert max_active >= 2
    assert elapsed < 0.7


def test_prepare_export_shot_media_honours_cancel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path)
    monkeypatch.setattr(
        otio_export,
        "_ensure_shot_media_for_export",
        lambda *_a, **_k: (Path("/tmp/hold.mp4"), 0.0, 0.0, 1.0, 25.0),
    )
    shots = [
        MagicMock(folder_name="A", shot_id="s1", chapter_id="A"),
        MagicMock(folder_name="B", shot_id="s2", chapter_id="B"),
    ]
    with pytest.raises(EnhancedOtioExportCancelled):
        _prepare_export_shot_media(
            project,
            shots,
            fps=25.0,
            catalog=MagicMock(),
            media_fill_cache={},
            allow_errors=True,
            should_cancel=lambda: True,
            max_workers=2,
        )
