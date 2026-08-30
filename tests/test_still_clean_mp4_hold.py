"""Fotos, die Clean als 5s-MP4 ablegt, müssen den Slot halten — kein Shortfall."""

from __future__ import annotations

from pathlib import Path

import pytest

from otio_app.analysis_models import (
    AssetFolderAnalysis,
    AssetMediaAnalysis,
    CleanMediaEntry,
    CleanMediaManifest,
)
from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import clean_output_path_for_media, get_folder_inventory_path
from otio_app.services.clean_media import (
    CLEAN_STATUS_CLEAN,
    original_still_image_for_clean,
    save_clean_media_manifest,
)
from otio_app.services.inventory_loader import save_folder_inventory
from otio_app.services.without_voiceover_enhanced.cut_slot_duration_guard import (
    is_still_asset,
    planning_usable_seconds,
)
from otio_app.services.without_voiceover_enhanced.timeline_resolver import (
    TimelineResolveError,
    _resolve_shot_media,
    build_asset_catalog,
    still_image_path_from_catalog_entry,
)
from otio_app.services.without_voiceover_enhanced.unified_timeline_service import (
    usable_media_duration_seconds,
)


_MIN_JPEG = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb004300080606070605080707"
    "070909080a0c140d0c0b0b0c1912130f141d1a1f1e1d1a1c1c20242e2720222c231c"
    "1c2837292c30313434341f27393d38323c2e333432ffdb0043010909090c0b0c180d"
    "0d1832211c2132323232323232323232323232323232323232323232323232323232"
    "323232323232323232323232323232323232323232ffc00011080001000103011100"
    "021101031101ffc40014000100000000000000000000000000000000ffc400141001"
    "00000000000000000000000000000000ffda000c0301000210031000003f00bf80ffd9"
)


def _jpeg(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_MIN_JPEG)
    return path


def _project(tmp_path: Path, folder: str) -> Project:
    root = tmp_path / "proj"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    (root / folder).mkdir(parents=True)
    return Project(
        id="still-clean",
        name="still-clean",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        asset_subdir_names=[folder],
        selected_asset_subdirs=[folder],
        fps=25.0,
    )


def test_original_still_image_for_clean_follows_manifest(tmp_path: Path) -> None:
    folder = "Škocjan-Höhlen"
    project = _project(tmp_path, folder)
    jpeg = _jpeg(
        Path(project.project_root) / folder / "Škocjan-Höhlen_Asset00009.jpeg"
    )
    clean = clean_output_path_for_media(
        project.work_dir_path, folder, jpeg
    )
    clean.parent.mkdir(parents=True, exist_ok=True)
    clean.write_bytes(b"\x00" * 64)
    from otio_app.services.clean_media import folder_manifest_path

    save_clean_media_manifest(
        folder_manifest_path(project, folder),
        CleanMediaManifest(
            project_id=project.id,
            folder=folder,
            entries=[
                CleanMediaEntry(
                    original_path=str(jpeg.resolve()),
                    clean_path=str(clean.resolve()),
                    status=CLEAN_STATUS_CLEAN,
                )
            ],
        ),
    )
    found = original_still_image_for_clean(project, folder, clean)
    assert found is not None
    assert found.resolve() == jpeg.resolve()


def test_catalog_keeps_photo_kind_when_clean_is_five_second_mp4(
    tmp_path: Path,
) -> None:
    folder = "Škocjan-Höhlen"
    project = _project(tmp_path, folder)
    jpeg = _jpeg(
        Path(project.project_root) / folder / "Škocjan-Höhlen_Asset00009.jpeg"
    )
    clean = clean_output_path_for_media(project.work_dir_path, folder, jpeg)
    clean.parent.mkdir(parents=True, exist_ok=True)
    clean.write_bytes(b"\x00" * 128)

    save_folder_inventory(
        get_folder_inventory_path(project.work_dir_path, folder),
        AssetFolderAnalysis(
            folder=folder,
            description="",
            media_files=[str(jpeg)],
            assets=[
                AssetMediaAnalysis(
                    path=str(jpeg),
                    description="cave walkway",
                    asset_id="asset_kocjan_h_hlen_asset00009",
                    media_type="",
                    duration_seconds=5.0,
                    analysis_status="complete",
                )
            ],
        ),
    )

    catalog = build_asset_catalog(project, fps=25.0, folder_names=[folder])
    entry, err = None, None
    from otio_app.services.without_voiceover_enhanced.timeline_resolver import (
        lookup_catalog_entry,
    )

    entry, err = lookup_catalog_entry(catalog, "asset_kocjan_h_hlen_asset00009")
    assert err is None
    assert entry is not None
    assert Path(entry["path"]).suffix.lower() == ".mp4"
    assert "clean" in str(entry["path"]).replace("\\", "/").lower()
    assert entry["media_kind"] == "image"
    assert entry["media_type"] == "photo"
    assert Path(entry["original_image_path"]).resolve() == jpeg.resolve()
    assert still_image_path_from_catalog_entry(entry) == jpeg.resolve()
    assert usable_media_duration_seconds(entry, head_trim=1.0) is None


def test_catalog_recovers_jpeg_when_inventory_already_lists_clean_mp4(
    tmp_path: Path,
) -> None:
    folder = "Škocjan-Höhlen"
    project = _project(tmp_path, folder)
    jpeg = _jpeg(
        Path(project.project_root) / folder / "Škocjan-Höhlen_Asset00009.jpeg"
    )
    clean = clean_output_path_for_media(project.work_dir_path, folder, jpeg)
    clean.parent.mkdir(parents=True, exist_ok=True)
    clean.write_bytes(b"\x00" * 128)
    from otio_app.services.clean_media import folder_manifest_path

    save_clean_media_manifest(
        folder_manifest_path(project, folder),
        CleanMediaManifest(
            project_id=project.id,
            folder=folder,
            entries=[
                CleanMediaEntry(
                    original_path=str(jpeg.resolve()),
                    clean_path=str(clean.resolve()),
                    status=CLEAN_STATUS_CLEAN,
                )
            ],
        ),
    )
    save_folder_inventory(
        get_folder_inventory_path(project.work_dir_path, folder),
        AssetFolderAnalysis(
            folder=folder,
            description="",
            media_files=[str(clean)],
            assets=[
                AssetMediaAnalysis(
                    path=str(clean),
                    description="cave walkway",
                    asset_id="asset_kocjan_h_hlen_asset00009",
                    media_type="video",
                    duration_seconds=5.0,
                    analysis_status="complete",
                )
            ],
        ),
    )
    catalog = build_asset_catalog(project, fps=25.0, folder_names=[folder])
    from otio_app.services.without_voiceover_enhanced.timeline_resolver import (
        lookup_catalog_entry,
    )

    entry, err = lookup_catalog_entry(catalog, "asset_kocjan_h_hlen_asset00009")
    assert err is None
    assert entry is not None
    assert entry["media_kind"] == "image"
    assert Path(entry["original_image_path"]).resolve() == jpeg.resolve()


def test_resolve_holds_clean_still_mp4_to_full_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = "Škocjan-Höhlen"
    project = _project(tmp_path, folder)
    jpeg = _jpeg(
        Path(project.project_root) / folder / "Škocjan-Höhlen_Asset00009.jpeg"
    )
    clean = tmp_path / "Škocjan-Höhlen_Asset00009.mp4"
    clean.write_bytes(b"\x00" * 64)
    hold = tmp_path / "still_hold_slot010.mp4"
    hold.write_bytes(b"hold")
    seen: list[tuple[Path, float]] = []

    def _fake_hold(_project, source, *, duration_seconds: float, fps: float):
        seen.append((Path(source), float(duration_seconds)))
        return hold

    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.timeline_resolver.ensure_still_hold_video",
        _fake_hold,
    )
    entry = {
        "path": str(clean),
        "duration_seconds": 5.0,
        "usable_in_s": 0.0,
        "media_kind": "video",
        "media_type": "video",
        "original_image_path": str(jpeg),
        "available_start_seconds": 0.0,
        "folder": folder,
        "canonical_id": "asset_kocjan_h_hlen_asset00009",
    }
    repairs: list[str] = []
    shot = _resolve_shot_media(
        project,
        shot_id="Škocjan-Höhlen_slot_010",
        asset_id="asset_kocjan_h_hlen_asset00009",
        entry=entry,
        timeline_start=0.0,
        timeline_end=10.3,
        fps=25.0,
        head_trim=1.0,
        short_tolerance=1.0,
        editorial_function="evidence",
        may_overlap_pause=False,
        repairs=repairs,
    )
    assert seen
    assert seen[0][0].resolve() == jpeg.resolve()
    assert seen[0][1] == pytest.approx(10.3)
    assert shot.hold_mode == "freeze_video"
    assert shot.source_start_seconds == pytest.approx(0.0)
    assert shot.source_end_seconds == pytest.approx(10.3)
    assert shot.resolved_media_path == str(hold)
    assert any("Still → Hold-Video" in note for note in repairs)


def test_five_second_motion_mp4_still_errors_when_slot_needs_more(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Echtes kurzes Video bleibt Shortfall — nur Fotos halten."""
    work = tmp_path / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir()
    media = tmp_path / "walk_through.mp4"
    media.write_bytes(b"x")
    project = Project(
        id="p",
        name="p",
        project_root=str(tmp_path),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        asset_subdir_names=["Caves"],
        selected_asset_subdirs=["Caves"],
    )
    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.timeline_resolver.ensure_still_hold_video",
        lambda *args, **kwargs: pytest.fail("Motion-MP4 darf nicht Still-Hold werden"),
    )
    entry = {
        "path": str(media),
        "duration_seconds": 5.0,
        "usable_in_s": 0.0,
        "media_kind": "video",
        "media_type": "video",
        "available_start_seconds": 0.0,
        "folder": "Caves",
        "canonical_id": "walk_through",
    }
    with pytest.raises(TimelineResolveError, match="zu kurz"):
        _resolve_shot_media(
            project,
            shot_id="Caves_slot_010",
            asset_id="walk_through",
            entry=entry,
            timeline_start=0.0,
            timeline_end=10.3,
            fps=25.0,
            head_trim=1.0,
            short_tolerance=1.0,
            editorial_function="evidence",
            may_overlap_pause=False,
            repairs=[],
        )


def test_duration_guard_treats_clean_still_mp4_as_unlimited() -> None:
    entry = {
        "path": "/clean/Škocjan-Höhlen_Asset00009.mp4",
        "media_type": "video",
        "media_kind": "video",
        "duration_seconds": 5.0,
        "original_image_path": "/media/Škocjan-Höhlen_Asset00009.jpeg",
    }
    assert is_still_asset(entry)
    assert planning_usable_seconds(entry, head_trim_sec=1.0) is None
    assert usable_media_duration_seconds(entry, head_trim=1.0) is None
