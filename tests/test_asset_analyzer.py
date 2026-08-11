"""Tests für Asset-Ordner-Analyse."""

from __future__ import annotations

from pathlib import Path

import pytest

from otio_app.analysis_models import AssetMediaAnalysis
from otio_app.models import Project
from otio_app.services.asset_analysis_signature import (
    ANALYSIS_SCHEMA_VERSION,
    build_analysis_signature,
    is_current_asset_analysis,
)
from otio_app.services.asset_analyzer import analyze_asset_folders
from otio_app.services.gemini_client import (
    ASSET_DESCRIPTION_PROMPT_VERSION,
    MediaFrameAnalysis,
    resolve_gemini_model,
)
from otio_app.services.media_inventory_cache import (
    has_successful_asset_cache,
    load_cached_media,
    media_cache_path,
    save_cached_media,
)


def _sample_project(layout: dict[str, Path]) -> Project:
    return Project(
        id="test-project",
        name="Test",
        project_root=str(layout["project_root"]),
        work_dir=str(layout["work_dir"]),
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )


def _fake_extract(media_path: Path, output_dir: Path, count: int, *, should_cancel=None) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    frame = output_dir / "frame_001.jpg"
    frame.write_bytes(b"jpeg")
    return [frame]


def _ok_analysis(media_name: str) -> MediaFrameAnalysis:
    return MediaFrameAnalysis.successful(description=f"Beschreibung für {media_name}")


def test_analyze_asset_folders_processes_every_media_file(
    temp_project_layout: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    folder = temp_project_layout["project_root"] / "Grand Canyon"
    (folder / "clip2.mp4").write_bytes(b"video2")

    calls: list[str] = []

    def fake_describe(
        media_name: str,
        folder_name: str,
        frame_paths: list[Path],
        language: str,
        *,
        model: str | None = None,
    ) -> MediaFrameAnalysis:
        calls.append(media_name)
        return _ok_analysis(media_name)

    monkeypatch.setattr("otio_app.services.asset_analyzer.extract_frames", _fake_extract)
    monkeypatch.setattr(
        "otio_app.services.asset_analyzer.analyze_media_from_frames",
        fake_describe,
    )

    project = _sample_project(temp_project_layout)
    document, report = analyze_asset_folders(project, ["Grand Canyon"], use_api=True)

    assert calls == ["clip.mp4", "clip2.mp4"]
    item = document.items[0]
    assert len(item.assets) == 2
    assert all(asset.description.startswith("Beschreibung für") for asset in item.assets)
    assert "clip.mp4:" in item.description
    assert "clip2.mp4:" in item.description

    inventory_file = project.folder_inventory_path("Grand Canyon")
    assert inventory_file.is_file()
    assert inventory_file.parent.name == "inventory"


def test_analyze_asset_folders_skips_current_v3_cache(
    temp_project_layout: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_describe(
        media_name: str,
        folder_name: str,
        frame_paths: list[Path],
        language: str,
        *,
        model: str | None = None,
    ) -> MediaFrameAnalysis:
        calls.append(media_name)
        return _ok_analysis(media_name)

    monkeypatch.setattr("otio_app.services.asset_analyzer.extract_frames", _fake_extract)
    monkeypatch.setattr(
        "otio_app.services.asset_analyzer.analyze_media_from_frames",
        fake_describe,
    )

    project = _sample_project(temp_project_layout)
    analyze_asset_folders(project, ["Grand Canyon"], use_api=True)
    analyze_asset_folders(project, ["Grand Canyon"], use_api=True)

    assert calls == ["clip.mp4"]


def test_analyze_asset_folders_recovers_from_corrupt_cache(
    temp_project_layout: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _sample_project(temp_project_layout)
    media_path = temp_project_layout["project_root"] / "Grand Canyon" / "clip.mp4"
    cache_file = (
        project.work_dir_path
        / "cache"
        / "inventory"
        / "Grand_Canyon"
        / "clip.mp4.json"
    )
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_bytes(b"\xceinvalid")

    calls: list[str] = []

    def fake_describe(
        media_name: str,
        folder_name: str,
        frame_paths: list[Path],
        language: str,
        *,
        model: str | None = None,
    ) -> MediaFrameAnalysis:
        calls.append(media_name)
        return _ok_analysis(media_name)

    monkeypatch.setattr("otio_app.services.asset_analyzer.extract_frames", _fake_extract)
    monkeypatch.setattr(
        "otio_app.services.asset_analyzer.analyze_media_from_frames",
        fake_describe,
    )

    document, report = analyze_asset_folders(project, ["Grand Canyon"], use_api=True)

    assert calls == ["clip.mp4"]
    assert document.items[0].assets[0].description.startswith("Beschreibung für")
    assert not cache_file.exists() or cache_file.read_text(encoding="utf-8").startswith("{")


def test_analyze_asset_folders_skips_completed_folder_json(
    temp_project_layout: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_describe(
        media_name: str,
        folder_name: str,
        frame_paths: list[Path],
        language: str,
        *,
        model: str | None = None,
    ) -> MediaFrameAnalysis:
        calls.append(f"{folder_name}:{media_name}")
        return _ok_analysis(media_name)

    monkeypatch.setattr("otio_app.services.asset_analyzer.extract_frames", _fake_extract)
    monkeypatch.setattr(
        "otio_app.services.asset_analyzer.analyze_media_from_frames",
        fake_describe,
    )

    project = Project(
        id="test-project",
        name="Test",
        project_root=str(temp_project_layout["project_root"]),
        work_dir=str(temp_project_layout["work_dir"]),
        asset_subdir_names=["Grand Canyon", "Yellowstone"],
        selected_asset_subdirs=["Grand Canyon", "Yellowstone"],
    )

    analyze_asset_folders(project, ["Grand Canyon"], use_api=True)
    analyze_asset_folders(project, ["Grand Canyon", "Yellowstone"], use_api=True)

    assert calls == ["Grand Canyon:clip.mp4", "Yellowstone:photo.jpg"]
    assert project.folder_inventory_path("Grand Canyon").is_file()
    assert project.folder_inventory_path("Yellowstone").is_file()


def test_analyze_stores_requested_and_resolved_model(
    temp_project_layout: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_models: list[str | None] = []

    def fake_describe(
        media_name: str,
        folder_name: str,
        frame_paths: list[Path],
        language: str,
        *,
        model: str | None = None,
    ) -> MediaFrameAnalysis:
        seen_models.append(model)
        return _ok_analysis(media_name)

    monkeypatch.setattr("otio_app.services.asset_analyzer.extract_frames", _fake_extract)
    monkeypatch.setattr(
        "otio_app.services.asset_analyzer.analyze_media_from_frames",
        fake_describe,
    )

    project = _sample_project(temp_project_layout)
    analyze_asset_folders(project, ["Grand Canyon"], use_api=True, model=None)

    media_path = temp_project_layout["project_root"] / "Grand Canyon" / "clip.mp4"
    cached = load_cached_media(media_cache_path(project, "Grand Canyon", media_path))
    assert cached is not None
    resolved = resolve_gemini_model(None)
    assert seen_models == [resolved]
    assert cached.description_model_requested == ""
    assert cached.description_model_resolved == resolved
    assert cached.description_model == resolved
    assert cached.analysis_signature is not None
    assert cached.analysis_signature.resolved_model_id == resolved
    assert cached.analysis_parse_ok is True
    assert cached.analysis_schema_version == ANALYSIS_SCHEMA_VERSION
    assert cached.description_prompt_version == ASSET_DESCRIPTION_PROMPT_VERSION


def test_analyze_parse_failure_not_reported_as_success(
    temp_project_layout: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_describe(
        media_name: str,
        folder_name: str,
        frame_paths: list[Path],
        language: str,
        *,
        model: str | None = None,
    ) -> MediaFrameAnalysis:
        return MediaFrameAnalysis(
            parse_ok=False,
            raw_response="not-json",
            description="",
        )

    monkeypatch.setattr("otio_app.services.asset_analyzer.extract_frames", _fake_extract)
    monkeypatch.setattr(
        "otio_app.services.asset_analyzer.analyze_media_from_frames",
        fake_describe,
    )

    project = _sample_project(temp_project_layout)
    document, report = analyze_asset_folders(project, ["Grand Canyon"], use_api=True)
    media_path = temp_project_layout["project_root"] / "Grand Canyon" / "clip.mp4"
    cached = load_cached_media(media_cache_path(project, "Grand Canyon", media_path))

    assert report.media_analyzed == 0
    assert report.media_failed == 1
    assert cached is not None
    assert cached.analysis_parse_ok is False
    assert cached.analysis_raw_response == "not-json"
    assert cached.error
    assert not has_successful_asset_cache(project, "Grand Canyon", media_path)
    assert document.items[0].assets[0].analysis_parse_ok is False


def test_successful_cache_omits_raw_response(
    temp_project_layout: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_describe(
        media_name: str,
        folder_name: str,
        frame_paths: list[Path],
        language: str,
        *,
        model: str | None = None,
    ) -> MediaFrameAnalysis:
        return MediaFrameAnalysis.successful(
            description=f"Beschreibung für {media_name}",
            raw_response='{"description":"should-not-persist"}',
        )

    monkeypatch.setattr("otio_app.services.asset_analyzer.extract_frames", _fake_extract)
    monkeypatch.setattr(
        "otio_app.services.asset_analyzer.analyze_media_from_frames",
        fake_describe,
    )

    project = _sample_project(temp_project_layout)
    analyze_asset_folders(project, ["Grand Canyon"], use_api=True)
    media_path = temp_project_layout["project_root"] / "Grand Canyon" / "clip.mp4"
    cached = load_cached_media(media_cache_path(project, "Grand Canyon", media_path))
    assert cached is not None
    assert cached.analysis_parse_ok is True
    assert cached.analysis_raw_response == ""
    assert '"description"' not in media_cache_path(project, "Grand Canyon", media_path).read_text(
        encoding="utf-8"
    ).split('"analysis_raw_response":')[-1][:80] or cached.analysis_raw_response == ""


def test_parse_failure_raw_response_is_bounded(
    temp_project_layout: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    huge = "x" * 10_000

    def fake_describe(
        media_name: str,
        folder_name: str,
        frame_paths: list[Path],
        language: str,
        *,
        model: str | None = None,
    ) -> MediaFrameAnalysis:
        return MediaFrameAnalysis(parse_ok=False, raw_response=huge)

    monkeypatch.setattr("otio_app.services.asset_analyzer.extract_frames", _fake_extract)
    monkeypatch.setattr(
        "otio_app.services.asset_analyzer.analyze_media_from_frames",
        fake_describe,
    )

    project = _sample_project(temp_project_layout)
    analyze_asset_folders(project, ["Grand Canyon"], use_api=True)
    media_path = temp_project_layout["project_root"] / "Grand Canyon" / "clip.mp4"
    cached = load_cached_media(media_cache_path(project, "Grand Canyon", media_path))
    assert cached is not None
    assert cached.analysis_parse_ok is False
    assert len(cached.analysis_raw_response) < len(huge)
    assert cached.analysis_raw_response.endswith("…[truncated]")


def test_legacy_cache_not_current_and_reanalyzed_on_explicit_run(
    temp_project_layout: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _sample_project(temp_project_layout)
    media_path = temp_project_layout["project_root"] / "Grand Canyon" / "clip.mp4"
    save_cached_media(
        media_cache_path(project, "Grand Canyon", media_path),
        AssetMediaAnalysis(path=str(media_path), description="Legacy Beschreibung"),
    )
    assert not has_successful_asset_cache(project, "Grand Canyon", media_path)

    calls: list[str] = []

    def fake_describe(
        media_name: str,
        folder_name: str,
        frame_paths: list[Path],
        language: str,
        *,
        model: str | None = None,
    ) -> MediaFrameAnalysis:
        calls.append(media_name)
        return _ok_analysis(media_name)

    monkeypatch.setattr("otio_app.services.asset_analyzer.extract_frames", _fake_extract)
    monkeypatch.setattr(
        "otio_app.services.asset_analyzer.analyze_media_from_frames",
        fake_describe,
    )

    analyze_asset_folders(project, ["Grand Canyon"], use_api=True)
    assert calls == ["clip.mp4"]
    cached = load_cached_media(media_cache_path(project, "Grand Canyon", media_path))
    assert cached is not None
    assert cached.analysis_parse_ok is True
    assert is_current_asset_analysis(
        cached, media_path, resolved_model_id=resolve_gemini_model(None)
    )


def test_stale_v3_cache_reanalyzed_on_explicit_run(
    temp_project_layout: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _sample_project(temp_project_layout)
    media_path = temp_project_layout["project_root"] / "Grand Canyon" / "clip.mp4"
    signature = build_analysis_signature(
        media_path, resolved_model_id=resolve_gemini_model(None)
    )
    save_cached_media(
        media_cache_path(project, "Grand Canyon", media_path),
        AssetMediaAnalysis(
            path=str(media_path),
            description="Alte v3 Beschreibung",
            caption="caption",
            analysis_parse_ok=True,
            analysis_schema_version=ANALYSIS_SCHEMA_VERSION,
            description_prompt_version="asset_v2_structured",
            analysis_signature=signature.model_copy(
                update={"prompt_version": "asset_v2_structured"}
            ),
        ),
    )
    assert not has_successful_asset_cache(project, "Grand Canyon", media_path)

    calls: list[str] = []

    def fake_describe(
        media_name: str,
        folder_name: str,
        frame_paths: list[Path],
        language: str,
        *,
        model: str | None = None,
    ) -> MediaFrameAnalysis:
        calls.append(media_name)
        return _ok_analysis(media_name)

    monkeypatch.setattr("otio_app.services.asset_analyzer.extract_frames", _fake_extract)
    monkeypatch.setattr(
        "otio_app.services.asset_analyzer.analyze_media_from_frames",
        fake_describe,
    )

    analyze_asset_folders(project, ["Grand Canyon"], use_api=True)
    assert calls == ["clip.mp4"]
