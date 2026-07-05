"""Tests für OTIO-Export."""

from __future__ import annotations

from pathlib import Path

import opentimelineio as otio

from otio_app.analysis_models import (
    EditPlanDocument,
    EditPlanShot,
    VoiceFolderMappingDocument,
    VoiceFolderMappingEntry,
)
from otio_app.models import Project
from otio_app.services.edit_plan_builder import save_edit_plan
from otio_app.services.otio_exporter import (
    build_otio_timeline,
    export_otio_timeline,
    merge_confirmed_edit_plans,
)


def _project(tmp_path: Path) -> Project:
    root = tmp_path / "USA"
    root.mkdir()
    return Project(
        id="otio-test",
        name="USA",
        project_root=str(root),
        work_dir=str(root / "_otio"),
        asset_subdir_names=["Florida Keys", "Grand Canyon"],
        selected_asset_subdirs=["Florida Keys", "Grand Canyon"],
    )


def _shot(folder: str, voice_file: str, index: int) -> EditPlanShot:
    return EditPlanShot(
        voice_file=voice_file,
        folder=folder,
        voice_start_sec=float(index * 3),
        voice_end_sec=float(index * 3 + 3),
        duration_sec=3.0,
        asset_path=str(Path(f"/media/{folder.replace(' ', '_')}_{index}.mp4")),
        motif=f"motif {index}",
        passage_text=f"text {index}",
    )


def _setup_mapping_and_plans(project: Project, tmp_path: Path) -> None:
    voice_a = str(tmp_path / "USA" / "Voice over" / "DE" / "USA_Florida Keys_VO.wav")
    voice_b = str(tmp_path / "USA" / "Voice over" / "DE" / "USA_Grand Canyon_VO.wav")
    Path(voice_a).parent.mkdir(parents=True, exist_ok=True)
    Path(voice_a).write_bytes(b"wav")
    Path(voice_b).write_bytes(b"wav")

    mapping = VoiceFolderMappingDocument(
        project_id=project.id,
        confirmed=True,
        entries=[
            VoiceFolderMappingEntry(
                voice_file=voice_a,
                folder="Florida Keys",
                confirmed=True,
            ),
            VoiceFolderMappingEntry(
                voice_file=voice_b,
                folder="Grand Canyon",
                confirmed=True,
            ),
        ],
    )
    project.voice_folder_mapping_path.write_text(
        mapping.model_dump_json(indent=2),
        encoding="utf-8",
    )

    save_edit_plan(
        project,
        EditPlanDocument(
            project_id=project.id,
            folder_name="Florida Keys",
            confirmed=True,
            shots=[_shot("Florida Keys", voice_a, 1), _shot("Florida Keys", voice_a, 2)],
        ),
        "Florida Keys",
    )
    save_edit_plan(
        project,
        EditPlanDocument(
            project_id=project.id,
            folder_name="Grand Canyon",
            confirmed=True,
            shots=[_shot("Grand Canyon", voice_b, 1)],
        ),
        "Grand Canyon",
    )


def test_merge_confirmed_edit_plans_in_mapping_order(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _setup_mapping_and_plans(project, tmp_path)

    merged = merge_confirmed_edit_plans(project)
    assert merged.ready is True
    assert merged.included_folders == ["Florida Keys", "Grand Canyon"]
    assert len(merged.shots) == 3
    assert merged.shots[0].folder == "Florida Keys"
    assert merged.shots[-1].folder == "Grand Canyon"


def test_clip_durations_use_seconds_not_frames(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _setup_mapping_and_plans(project, tmp_path)
    merged = merge_confirmed_edit_plans(project)

    timeline = build_otio_timeline(project, merged)
    video_track = timeline.tracks[0]
    clips = [item for item in video_track if isinstance(item, otio.schema.Clip)]
    assert clips
    first = clips[0]
    duration_sec = first.source_range.duration.to_seconds()
    assert duration_sec == 3.0

    clip_total = sum(
        item.source_range.duration.to_seconds()
        for item in video_track
        if isinstance(item, otio.schema.Clip)
    )
    assert clip_total == 9.0


def test_export_otio_timeline_writes_file(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _setup_mapping_and_plans(project, tmp_path)
    merged = merge_confirmed_edit_plans(project)

    export_path = export_otio_timeline(project, merged)
    assert export_path.is_file()

    timeline = otio.adapters.read_from_file(str(export_path))
    assert timeline.name == "USA"
    assert len(timeline.tracks) == 2
    assert timeline.tracks[0].name == "V1"
    assert timeline.tracks[1].name == "A1"
    assert all(not isinstance(item, otio.schema.Stack) for item in timeline.tracks[0])
    assert all(isinstance(item, (otio.schema.Clip, otio.schema.Gap)) for item in timeline.tracks[0])

    built = build_otio_timeline(project, merged)
    assert list(built.metadata["included_folders"]) == ["Florida Keys", "Grand Canyon"]
