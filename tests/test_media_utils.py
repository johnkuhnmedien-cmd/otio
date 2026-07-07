"""Tests für Medien-Hilfsfunktionen."""

from __future__ import annotations

from pathlib import Path

from otio_app.services.media_utils import list_media_files


def test_list_media_files(temp_project_layout: dict[str, Path]) -> None:
    assets = temp_project_layout["asset_dirs"][0]
    files = list_media_files(assets)
    assert len(files) == 1
    assert files[0].name == "clip.mp4"
