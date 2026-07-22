"""Inventar-Vorbereitung: Dauern + Slim vor Cut Plan."""

from __future__ import annotations

import json
from pathlib import Path

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis
from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import get_folder_inventory_path
from otio_app.services.inventory_loader import load_folder_inventory
from otio_app.services.inventory_prepare_service import (
    inventory_duration_coverage,
    prepare_inventories_for_cut_plan,
)
from otio_app.services.inventory_prompt_view import slim_inventory_path_for
from otio_app.services.voiceover_generation.dramaturgy_service import (
    save_confirmed_dramaturgy,
)
from otio_app.services.voiceover_generation.models import (
    DramaturgyFolderEntry,
    DramaturgyPlan,
)


def _project(tmp_path: Path) -> Project:
    root = tmp_path / "USA"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    folder = root / "Canyon"
    folder.mkdir()
    media = folder / "clip.mp4"
    media.write_bytes(b"fake-video")
    photo = folder / "still.jpg"
    photo.write_bytes(b"fake-image")
    project = Project(
        id="prep-inv",
        name="Prep",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        language="en",
        asset_subdir_names=["Canyon"],
        selected_asset_subdirs=["Canyon"],
    )
    save_confirmed_dramaturgy(
        project,
        DramaturgyPlan(
            project_id=project.id,
            recommended_folder_order=[
                DramaturgyFolderEntry(
                    folder_name="Canyon", order_index=1, enabled=True
                )
            ],
        ),
    )
    inv = AssetFolderAnalysis(
        folder="Canyon",
        media_files=[str(media), str(photo)],
        assets=[
            AssetMediaAnalysis(
                path=str(media),
                description="Canyon walls",
                asset_id="asset_canyon_clip",
                media_type="video",
            ),
            AssetMediaAnalysis(
                path=str(photo),
                description="Still photo",
                asset_id="asset_canyon_still",
                media_type="image",
            ),
        ],
    )
    # Direkter Write ohne Slim-Probe-Nebenwirkung in Setup — save nutzt Monkeypatch später.
    path = get_folder_inventory_path(project.work_dir_path, "Canyon")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(inv.model_dump_json(indent=2), encoding="utf-8")
    return project


def test_prepare_writes_duration_and_slim(tmp_path: Path, monkeypatch) -> None:
    project = _project(tmp_path)
    monkeypatch.setattr(
        "otio_app.services.inventory_prepare_service.probe_duration_seconds",
        lambda path: 14.25 if path.suffix == ".mp4" else None,
    )
    monkeypatch.setattr(
        "otio_app.services.inventory_prompt_view.probe_duration_seconds",
        lambda path: 14.25 if path.suffix == ".mp4" else None,
    )

    with_dur, total, folders = inventory_duration_coverage(project)
    assert folders == 1
    assert total == 1
    assert with_dur == 0

    report = prepare_inventories_for_cut_plan(project)
    assert report.folders_touched == 1
    assert report.durations_newly_measured == 1
    assert report.assets_with_duration == 1
    assert report.assets_non_video == 1
    assert report.slim_files_written

    inventory = load_folder_inventory(project, "Canyon")
    assert inventory is not None
    video = next(a for a in inventory.assets if a.asset_id == "asset_canyon_clip")
    assert video.duration_seconds == 14.25
    still = next(a for a in inventory.assets if a.asset_id == "asset_canyon_still")
    assert still.duration_seconds is None

    slim_path = slim_inventory_path_for(
        get_folder_inventory_path(project.work_dir_path, "Canyon")
    )
    slim = json.loads(slim_path.read_text(encoding="utf-8"))
    by_id = {a["id"]: a for a in slim["assets"]}
    assert by_id["asset_canyon_clip"]["dauer_s"] == 14.25
    assert by_id["asset_canyon_still"]["dauer_s"] is None

    # Zweiter Lauf ohne force: nichts neu messen.
    report2 = prepare_inventories_for_cut_plan(project)
    assert report2.durations_newly_measured == 0
    assert report2.assets_with_duration == 1


def test_slim_prefers_stored_duration_without_probe(monkeypatch) -> None:
    from otio_app.services.inventory_prompt_view import build_slim_folder_inventory

    def _boom(path):  # noqa: ANN001
        raise AssertionError("probe should not run when duration_seconds set")

    monkeypatch.setattr(
        "otio_app.services.inventory_prompt_view.probe_duration_seconds", _boom
    )
    folder = AssetFolderAnalysis(
        folder="Canyon",
        assets=[
            AssetMediaAnalysis(
                path="/x/clip.mp4",
                description="Walls",
                asset_id="asset_x",
                media_type="video",
                duration_seconds=9.5,
            )
        ],
    )
    slim = build_slim_folder_inventory(folder, probe_duration=True)
    assert slim["assets"][0]["dauer_s"] == 9.5
