"""Gemeinsame Test-Fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def temp_project_layout(tmp_path: Path) -> dict[str, Path]:
    """Simuliert einen Projektordner wie .../USA mit Asset- und Voice-over-Unterordnern."""
    project_root = tmp_path / "USA"
    project_root.mkdir()

    grand_canyon = project_root / "Grand Canyon"
    yellowstone = project_root / "Yellowstone"
    grand_canyon.mkdir()
    yellowstone.mkdir()
    (grand_canyon / "clip.mp4").write_bytes(b"video")
    (yellowstone / "photo.jpg").write_bytes(b"image")

    voice_over_dir = project_root / "Voice over" / "DE"
    voice_over_dir.mkdir(parents=True)
    voice_file = voice_over_dir / "voiceover.wav"
    voice_file.write_bytes(b"RIFF")

    work_dir = project_root / "_otio"

    return {
        "root": tmp_path,
        "project_root": project_root,
        "work_dir": work_dir,
        "voice_over_dir": voice_over_dir,
        "voice_file": voice_file,
        "asset_dirs": [grand_canyon, yellowstone],
    }


@pytest.fixture(autouse=True)
def _default_stock_watermark_check_clean(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    """Bestehende Analyse-Tests sollen nicht an Gemini für Wasserzeichen hängen."""
    test_path = getattr(request.node, "path", None)
    if test_path is not None and Path(test_path).name == "test_asset_watermark_check.py":
        return

    from otio_app.services.asset_watermark_check import StockWatermarkCheckResult

    def _clean(*_args, **_kwargs) -> StockWatermarkCheckResult:
        return StockWatermarkCheckResult(blocked=False)

    monkeypatch.setattr(
        "otio_app.services.asset_analyzer.check_frames_for_stock_watermark",
        _clean,
    )
    monkeypatch.setattr(
        "otio_app.services.asset_watermark_check.check_frames_for_stock_watermark",
        _clean,
    )


@pytest.fixture
def temp_db_path(tmp_path: Path) -> Path:
    return tmp_path / "test_projects.db"
