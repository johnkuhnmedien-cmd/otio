"""Prüfschritt für kommerzielle Stock-Wasserzeichen in der Asset-Analyse."""

from __future__ import annotations

from pathlib import Path

import pytest

from otio_app.analysis_models import AssetDefect, AssetMediaAnalysis
from otio_app.models import Project
from otio_app.services.asset_analysis_signature import (
    ANALYSIS_SCHEMA_VERSION,
    classify_asset_cache_status,
    is_usable_asset_analysis,
)
from otio_app.services.asset_analyzer import analyze_asset_folders
from otio_app.services.asset_watermark_check import (
    WATERMARK_BLOCK_ERROR,
    WATERMARK_CHECK_VERSION,
    StockWatermarkCheckResult,
    check_frames_for_stock_watermark,
    format_watermark_review_banner,
    load_watermark_review_items,
    parse_stock_watermark_response,
    prune_stale_watermark_review,
    remove_watermark_review_item,
    stock_watermark_from_v3_defects,
    upsert_watermark_review_item,
    watermark_check_is_current,
    watermark_review_txt_path,
)
from otio_app.services.folder_analysis_status import FolderAnalysisState, get_folder_analysis_state
from otio_app.services.folder_asset_status import folder_is_fully_analyzed
from otio_app.services.inventory_loader import load_folder_inventory_file
from otio_app.services.inventory_prompt_view import slim_inventory_path_for
from otio_app.services.gemini_client import (
    ASSET_DESCRIPTION_PROMPT_VERSION,
    GeminiNotConfiguredError,
    MediaFrameAnalysis,
    resolve_gemini_model,
)
from otio_app.services.media_inventory_cache import (
    is_successfully_analyzed,
    load_cached_media,
    media_cache_path,
    save_cached_media,
)
from tests.test_partial_asset_analysis import _current_cache_entry


def _sample_project(layout: dict[str, Path]) -> Project:
    return Project(
        id="watermark-test",
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


def _blocked_adobe(*_args, **_kwargs) -> StockWatermarkCheckResult:
    return StockWatermarkCheckResult(
        blocked=True,
        provider="adobe_stock",
        note="Zentriertes Adobe-Stock-Logo und Schriftzug",
    )


def _clean_watermark(*_args, **_kwargs) -> StockWatermarkCheckResult:
    return StockWatermarkCheckResult(blocked=False)


def test_parse_blocks_centered_adobe_stock_overlay() -> None:
    result = parse_stock_watermark_response(
        '{"watermark": true, "provider": "adobe_stock", '
        '"note": "großes zentriertes Adobe-Stock-Logo"}'
    )
    assert result.blocked is True
    assert result.failed_open is False
    assert result.provider == "adobe_stock"


def test_parse_accepts_fenced_json_and_adobe_alias() -> None:
    raw = (
        "```json\n"
        '{"watermark": true, "provider": "Adobe Stock", "note": "Preview overlay"}\n'
        "```"
    )
    result = parse_stock_watermark_response(raw)
    assert result.blocked is True
    assert result.provider == "adobe_stock"


def test_parse_does_not_block_corner_channel_logo() -> None:
    result = parse_stock_watermark_response(
        '{"watermark": false, "provider": "", '
        '"note": "kleines Senderlogo oben rechts"}'
    )
    assert result.blocked is False
    assert result.failed_open is False


def test_parse_invalid_json_fails_open() -> None:
    result = parse_stock_watermark_response("not-json")
    assert result.blocked is False
    assert result.failed_open is True


def test_v3_defects_block_adobe_stock_watermark() -> None:
    hit = stock_watermark_from_v3_defects(
        [AssetDefect(type="watermark", severity=80, note="Adobe Stock overlay")],
        "",
    )
    assert hit is not None
    assert hit.blocked is True
    assert hit.provider == "adobe_stock"


def test_v3_defects_ignore_corner_logo() -> None:
    hit = stock_watermark_from_v3_defects(
        [AssetDefect(type="logo", severity=20, note="kleines CNN-Logo oben links")],
        "logo in der Ecke",
    )
    assert hit is None


def test_review_file_upsert_and_remove(temp_project_layout: dict[str, Path]) -> None:
    project = _sample_project(temp_project_layout)
    media = temp_project_layout["project_root"] / "Grand Canyon" / "clip.mp4"
    other = temp_project_layout["project_root"] / "Grand Canyon" / "clip2.mp4"
    other.write_bytes(b"video2")

    upsert_watermark_review_item(
        project,
        folder="Grand Canyon",
        media_path=media,
        provider="adobe_stock",
        note="Overlay",
    )
    upsert_watermark_review_item(
        project,
        folder="Grand Canyon",
        media_path=other,
        provider="shutterstock",
        note="Diagonal",
    )
    upsert_watermark_review_item(
        project,
        folder="Grand Canyon",
        media_path=media,
        provider="adobe_stock",
        note="aktualisiert",
    )

    items = load_watermark_review_items(project)
    assert len(items) == 2
    by_name = {item.filename: item for item in items}
    assert by_name["clip.mp4"].note == "aktualisiert"
    txt = watermark_review_txt_path(project).read_text(encoding="utf-8")
    assert "clip.mp4" in txt
    assert "Adobe Stock" in txt

    remove_watermark_review_item(project, media, folder="Grand Canyon")
    remaining = load_watermark_review_items(project)
    assert [item.filename for item in remaining] == ["clip2.mp4"]


def test_prune_stale_watermark_review_drops_replaced_filename(
    temp_project_layout: dict[str, Path],
) -> None:
    project = _sample_project(temp_project_layout)
    folder = temp_project_layout["project_root"] / "Grand Canyon"
    old = folder / "Rateče_Asset12.mp4"
    replacement = folder / "Rateče_Asset00012.mov"
    replacement.write_bytes(b"new")
    keep = folder / "clip.mp4"

    upsert_watermark_review_item(
        project,
        folder="Grand Canyon",
        media_path=old,
        provider="adobe_stock",
        note="altes Preview",
    )
    upsert_watermark_review_item(
        project,
        folder="Grand Canyon",
        media_path=keep,
        provider="adobe_stock",
        note="noch da",
    )

    dropped = prune_stale_watermark_review(project)
    assert dropped == 1
    remaining = load_watermark_review_items(project)
    assert [item.filename for item in remaining] == ["clip.mp4"]


def test_format_review_banner_points_to_txt(tmp_path: Path) -> None:
    path = tmp_path / "watermark_review.txt"
    text = format_watermark_review_banner(2, path)
    assert "2 Asset" in text
    assert str(path) in text


def test_watermarked_asset_fails_analysis_and_is_listed(
    temp_project_layout: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    v3_calls: list[str] = []

    def boom_v3(media_name: str, *args, **kwargs) -> MediaFrameAnalysis:
        v3_calls.append(media_name)
        raise AssertionError("v3 must not run after watermark block")

    monkeypatch.setattr("otio_app.services.asset_analyzer.extract_frames", _fake_extract)
    monkeypatch.setattr(
        "otio_app.services.asset_analyzer.check_frames_for_stock_watermark",
        _blocked_adobe,
    )
    monkeypatch.setattr(
        "otio_app.services.asset_analyzer.analyze_media_from_frames",
        boom_v3,
    )

    project = _sample_project(temp_project_layout)
    document, report = analyze_asset_folders(project, ["Grand Canyon"], use_api=True)
    media = temp_project_layout["project_root"] / "Grand Canyon" / "clip.mp4"
    cached = load_cached_media(media_cache_path(project, "Grand Canyon", media))

    assert v3_calls == []
    assert report.media_failed == 1
    assert cached is not None
    assert cached.watermark_blocked is True
    assert cached.watermark_provider == "adobe_stock"
    assert cached.watermark_check_version == WATERMARK_CHECK_VERSION
    assert cached.error == WATERMARK_BLOCK_ERROR
    assert not is_successfully_analyzed(cached)
    assert not is_usable_asset_analysis(cached)
    assert not folder_is_fully_analyzed(project, "Grand Canyon")
    # Kein erfolgreicher Clip → kein Ordner-Inventar.
    assert not project.folder_inventory_path("Grand Canyon").is_file()
    items = load_watermark_review_items(project)
    assert len(items) == 1
    assert items[0].filename == "clip.mp4"
    assert document.items[0].assets[0].error == WATERMARK_BLOCK_ERROR


def test_partial_folder_writes_inventory_without_watermarked_clips(
    temp_project_layout: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    folder = temp_project_layout["project_root"] / "Grand Canyon"
    clean = folder / "clip_clean.mp4"
    dirty = folder / "clip_wm.mp4"
    clean.write_bytes(b"video-clean")
    dirty.write_bytes(b"video-wm")
    (folder / "clip.mp4").unlink()

    def fake_wm(frame_paths, *, media_name, folder_name, model=None):
        if media_name == dirty.name:
            return _blocked_adobe()
        return _clean_watermark()

    def fake_v3(media_name, folder_name, frame_paths, language, *, model=None):
        return MediaFrameAnalysis.successful(description=f"Beschreibung für {media_name}")

    monkeypatch.setattr("otio_app.services.asset_analyzer.extract_frames", _fake_extract)
    monkeypatch.setattr(
        "otio_app.services.asset_analyzer.check_frames_for_stock_watermark",
        fake_wm,
    )
    monkeypatch.setattr(
        "otio_app.services.asset_analyzer.analyze_media_from_frames",
        fake_v3,
    )

    project = _sample_project(temp_project_layout)
    analyze_asset_folders(project, ["Grand Canyon"], use_api=True)

    assert get_folder_analysis_state(project, "Grand Canyon") == FolderAnalysisState.PARTIAL
    assert not folder_is_fully_analyzed(project, "Grand Canyon")
    inventory_path = project.folder_inventory_path("Grand Canyon")
    assert inventory_path.is_file()
    item = load_folder_inventory_file(inventory_path)
    assert item is not None
    names = {Path(asset.path).name for asset in item.assets}
    assert names == {"clip_clean.mp4"}
    assert slim_inventory_path_for(inventory_path).is_file()
    review = load_watermark_review_items(project)
    assert [entry.filename for entry in review] == ["clip_wm.mp4"]


def test_current_v3_cache_runs_only_watermark_check(
    temp_project_layout: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _sample_project(temp_project_layout)
    media = temp_project_layout["project_root"] / "Grand Canyon" / "clip.mp4"
    save_cached_media(
        media_cache_path(project, "Grand Canyon", media),
        _current_cache_entry(media, "Bereits fertig"),
    )
    v3_calls: list[str] = []
    wm_calls: list[str] = []

    def fake_wm(frame_paths, *, media_name, folder_name, model=None):
        wm_calls.append(media_name)
        return _clean_watermark()

    def boom_v3(media_name: str, *args, **kwargs) -> MediaFrameAnalysis:
        v3_calls.append(media_name)
        raise AssertionError("current v3 cache must not re-run description")

    monkeypatch.setattr("otio_app.services.asset_analyzer.extract_frames", _fake_extract)
    monkeypatch.setattr(
        "otio_app.services.asset_analyzer.check_frames_for_stock_watermark",
        fake_wm,
    )
    monkeypatch.setattr(
        "otio_app.services.asset_analyzer.analyze_media_from_frames",
        boom_v3,
    )

    analyze_asset_folders(project, ["Grand Canyon"], use_api=True)
    cached = load_cached_media(media_cache_path(project, "Grand Canyon", media))

    assert wm_calls == ["clip.mp4"]
    assert v3_calls == []
    assert cached is not None
    assert cached.description == "Bereits fertig"
    assert cached.watermark_blocked is False
    assert watermark_check_is_current(cached)
    assert is_successfully_analyzed(cached)
    assert folder_is_fully_analyzed(project, "Grand Canyon")


def test_current_v3_cache_watermark_block_skips_v3(
    temp_project_layout: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _sample_project(temp_project_layout)
    media = temp_project_layout["project_root"] / "Grand Canyon" / "clip.mp4"
    save_cached_media(
        media_cache_path(project, "Grand Canyon", media),
        _current_cache_entry(media, "Bereits fertig"),
    )
    v3_calls: list[str] = []

    def boom_v3(media_name: str, *args, **kwargs) -> MediaFrameAnalysis:
        v3_calls.append(media_name)
        raise AssertionError("blocked watermark must not run v3")

    monkeypatch.setattr("otio_app.services.asset_analyzer.extract_frames", _fake_extract)
    monkeypatch.setattr(
        "otio_app.services.asset_analyzer.check_frames_for_stock_watermark",
        _blocked_adobe,
    )
    monkeypatch.setattr(
        "otio_app.services.asset_analyzer.analyze_media_from_frames",
        boom_v3,
    )

    analyze_asset_folders(project, ["Grand Canyon"], use_api=True)
    cached = load_cached_media(media_cache_path(project, "Grand Canyon", media))
    assert v3_calls == []
    assert cached is not None
    assert cached.watermark_blocked is True
    assert not folder_is_fully_analyzed(project, "Grand Canyon")
    assert load_watermark_review_items(project)[0].filename == "clip.mp4"


def test_v3_defect_belt_blocks_stock_watermark(
    temp_project_layout: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_v3(media_name, folder_name, frame_paths, language, *, model=None):
        return MediaFrameAnalysis.successful(
            description=f"Beschreibung für {media_name}",
            defects="Adobe Stock Wasserzeichen mittig",
        )

    monkeypatch.setattr("otio_app.services.asset_analyzer.extract_frames", _fake_extract)
    monkeypatch.setattr(
        "otio_app.services.asset_analyzer.check_frames_for_stock_watermark",
        _clean_watermark,
    )
    monkeypatch.setattr(
        "otio_app.services.asset_analyzer.analyze_media_from_frames",
        fake_v3,
    )

    project = _sample_project(temp_project_layout)
    analyze_asset_folders(project, ["Grand Canyon"], use_api=True)
    media = temp_project_layout["project_root"] / "Grand Canyon" / "clip.mp4"
    cached = load_cached_media(media_cache_path(project, "Grand Canyon", media))
    assert cached is not None
    assert cached.watermark_blocked is True
    assert cached.watermark_provider == "adobe_stock"
    assert not is_usable_asset_analysis(cached)


def test_clean_recheck_removes_review_entry(
    temp_project_layout: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _sample_project(temp_project_layout)
    media = temp_project_layout["project_root"] / "Grand Canyon" / "clip.mp4"
    upsert_watermark_review_item(
        project,
        folder="Grand Canyon",
        media_path=media,
        provider="adobe_stock",
        note="alt",
    )

    def fake_v3(media_name, folder_name, frame_paths, language, *, model=None):
        return MediaFrameAnalysis.successful(description=f"Beschreibung für {media_name}")

    monkeypatch.setattr("otio_app.services.asset_analyzer.extract_frames", _fake_extract)
    monkeypatch.setattr(
        "otio_app.services.asset_analyzer.check_frames_for_stock_watermark",
        _clean_watermark,
    )
    monkeypatch.setattr(
        "otio_app.services.asset_analyzer.analyze_media_from_frames",
        fake_v3,
    )

    analyze_asset_folders(project, ["Grand Canyon"], use_api=True)
    assert load_watermark_review_items(project) == []
    cached = load_cached_media(media_cache_path(project, "Grand Canyon", media))
    assert cached is not None
    assert cached.watermark_blocked is False
    assert watermark_check_is_current(cached)


def test_check_frames_fail_open_on_transient(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = tmp_path / "frame.jpg"
    frame.write_bytes(b"jpeg")

    def boom(*_args, **_kwargs):
        raise TimeoutError("503 unavailable")

    monkeypatch.setattr(
        "otio_app.services.asset_watermark_check.generate_text_from_image_frames",
        boom,
    )
    result = check_frames_for_stock_watermark(
        [frame], media_name="clip.mp4", folder_name="Escó"
    )
    assert result.blocked is False
    assert result.failed_open is True


def test_check_frames_reraises_missing_api_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = tmp_path / "frame.jpg"
    frame.write_bytes(b"jpeg")

    def boom(*_args, **_kwargs):
        raise GeminiNotConfiguredError("missing")

    monkeypatch.setattr(
        "otio_app.services.asset_watermark_check.generate_text_from_image_frames",
        boom,
    )
    with pytest.raises(GeminiNotConfiguredError):
        check_frames_for_stock_watermark(
            [frame], media_name="clip.mp4", folder_name="Escó"
        )


def test_watermark_blocked_not_usable_even_with_description() -> None:
    entry = AssetMediaAnalysis(
        path="clip.mp4",
        description="Landschaft",
        watermark_blocked=True,
        error=WATERMARK_BLOCK_ERROR,
    )
    assert not is_usable_asset_analysis(entry)
    status = classify_asset_cache_status(
        entry, Path("clip.mp4"), resolved_model_id="gemini-test"
    )
    assert status.status == "failed"
    assert "watermark_blocked" in status.reasons


def test_legacy_json_without_watermark_fields_stays_usable() -> None:
    entry = AssetMediaAnalysis(path="clip.mp4", description="Legacy OK")
    assert entry.watermark_blocked is None
    assert entry.watermark_check_version == ""
    assert is_usable_asset_analysis(entry)
    assert not watermark_check_is_current(entry)
