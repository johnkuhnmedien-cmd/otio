"""Tests für Analyse-Protokoll."""

from __future__ import annotations

from otio_app.services.gemini_client import MediaFrameAnalysis

from pathlib import Path

import pytest

from otio_app.analysis_models import AssetMediaAnalysis
from otio_app.services.analysis_log import analysis_log_path, read_analysis_log_tail
from otio_app.services.asset_analyzer import analyze_asset_folders
from otio_app.services.media_inventory_cache import media_cache_path, save_cached_media
from tests.test_partial_asset_analysis import _project


def test_analysis_log_records_missing_asset_run(
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
        AssetMediaAnalysis(path=str(clip1), description="OK"),
    )

    def fake_extract(media_path: Path, output_dir: Path, count: int, *, should_cancel=None) -> list[Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        frame = output_dir / "frame_001.jpg"
        frame.write_bytes(b"jpeg")
        return [frame]

    def fake_describe(
        media_name: str,
        folder_name: str,
        frame_paths: list[Path],
        language: str,
        *,
        model: str | None = None,
    ) -> MediaFrameAnalysis:
        return MediaFrameAnalysis.successful(description=f"Beschreibung für {media_name}")
    monkeypatch.setattr("otio_app.services.asset_analyzer.extract_frames", fake_extract)
    monkeypatch.setattr(
        "otio_app.services.asset_analyzer.analyze_media_from_frames",
        fake_describe,
    )

    analyze_asset_folders(project, ["Grand Canyon"], use_api=True)

    log_path = analysis_log_path(project)
    assert log_path.is_file()
    log_text = read_analysis_log_tail(project, max_lines=200)
    assert "Florida_Keys_Asset15.mp4" in log_text
    assert "OK (Gemini)" in log_text or "START" in log_text
