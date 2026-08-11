"""UI-/Kosten-Sicherheit: Statuspfade starten keine Asset-Reanalyse."""

from __future__ import annotations

from pathlib import Path

import pytest

from otio_app.analysis_models import AssetMediaAnalysis
from otio_app.models import Project
from otio_app.services.folder_asset_status import folder_is_fully_analyzed
from otio_app.services.media_inventory_cache import (
    has_successful_asset_cache,
    is_successfully_analyzed,
    list_assets_missing_successful_cache,
    media_cache_path,
    save_cached_media,
)
from tests.test_partial_asset_analysis import _current_cache_entry, _project


def test_folder_green_uses_usable_legacy_without_api(
    temp_project_layout: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project(temp_project_layout)
    media = temp_project_layout["project_root"] / "Grand Canyon" / "clip.mp4"
    legacy = AssetMediaAnalysis(path=str(media), description="Legacy usable")
    save_cached_media(media_cache_path(project, "Grand Canyon", media), legacy)

    def boom(*args, **kwargs):
        raise AssertionError("analyze_media_from_frames must not run on status read")

    monkeypatch.setattr(
        "otio_app.services.asset_analyzer.analyze_media_from_frames",
        boom,
    )
    monkeypatch.setattr(
        "otio_app.services.gemini_client.analyze_media_from_frames",
        boom,
    )

    assert is_successfully_analyzed(legacy)
    assert folder_is_fully_analyzed(project, "Grand Canyon")
    # Expliziter Lauf würde reanalysieren:
    assert not has_successful_asset_cache(project, "Grand Canyon", media)
    missing = list_assets_missing_successful_cache(project, "Grand Canyon")
    assert [path.name for path in missing] == ["clip.mp4"]


def test_current_v3_not_listed_as_missing(temp_project_layout: dict[str, Path]) -> None:
    project = _project(temp_project_layout)
    media = temp_project_layout["project_root"] / "Grand Canyon" / "clip.mp4"
    save_cached_media(
        media_cache_path(project, "Grand Canyon", media),
        _current_cache_entry(media, "Current"),
    )
    assert has_successful_asset_cache(project, "Grand Canyon", media)
    assert list_assets_missing_successful_cache(project, "Grand Canyon") == []
    assert folder_is_fully_analyzed(project, "Grand Canyon")
