"""Tests für OTIO-Export-Progress und Cancel."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from otio_app.analysis_models import (
    EditPlanDocument,
    EditPlanSettings,
    TimelineItem,
    VoiceFolderMappingDocument,
    VoiceFolderMappingEntry,
    VoiceoverPlan,
)
from otio_app.models import Project
from otio_app.services.edit_plan_builder import save_edit_plan
from otio_app.services.edit_plan_validator import TimelineValidationResult, ValidationStatus
from otio_app.services.otio_exporter import (
    OtioExportCancelled,
    OtioExportProgressEvent,
    export_otio_timeline,
    merge_confirmed_edit_plans,
)
from otio_app.defaults import PRODUCTION_EDIT_PLAN_CANDIDATE_STATUS_STAGING_DRAFT


def _project(tmp_path: Path) -> Project:
    root = tmp_path / "USA"
    root.mkdir()
    return Project(
        id="otio-progress",
        name="USA",
        project_root=str(root),
        work_dir=str(root / "_otio"),
        asset_subdir_names=["Florida Keys"],
        selected_asset_subdirs=["Florida Keys"],
    )


def test_export_emits_clip_progress_events(tmp_path: Path) -> None:
    project = _project(tmp_path)
    work = project.work_dir_path
    work.mkdir(parents=True, exist_ok=True)
    voice = str(tmp_path / "v.mp3")
    Path(voice).write_bytes(b"x")
    clip = tmp_path / "c.mp4"
    clip.write_bytes(b"x")
    mapping = VoiceFolderMappingDocument(
        project_id=project.id,
        confirmed=True,
        entries=[VoiceFolderMappingEntry(voice_file=voice, folder="Florida Keys", confirmed=True)],
    )
    project.voice_folder_mapping_path.parent.mkdir(parents=True, exist_ok=True)
    project.voice_folder_mapping_path.write_text(mapping.model_dump_json(indent=2), encoding="utf-8")
    items = [
        TimelineItem(
            timeline_item_id="seg_1",
            type="video_shot",
            section_id="cut_001",
            folder_name="Florida Keys",
            voice_file="",
            resolved_media_path=str(clip),
            timeline_in_sec=0.0,
            timeline_out_sec=3.0,
            duration_sec=3.0,
            final_duration_sec=3.0,
            source_in_sec=0.0,
            source_out_sec=3.0,
            track="V1",
            asset_type="video",
        )
    ]
    save_edit_plan(
        project,
        EditPlanDocument(
            project_id=project.id,
            folder_name="Florida Keys",
            confirmed=True,
            settings=EditPlanSettings(audio_offset_sec=0.0, section_outro_sec=0.0, shot_max_sec=100),
            voiceover=VoiceoverPlan(
                path=voice,
                timeline_start_sec=0.0,
                duration_sec=3.0,
                timeline_end_sec=3.0,
                duration_source="bridge_audio_plan",
                trim_policy="disabled",
            ),
            shots=[],
            timeline_items=items,
            candidate_status=PRODUCTION_EDIT_PLAN_CANDIDATE_STATUS_STAGING_DRAFT,
            allow_black_outro=True,
        ),
        "Florida Keys",
    )
    events: list[OtioExportProgressEvent] = []
    with patch("otio_app.services.otio_exporter.validate_timeline_items") as mock_validate, patch(
        "otio_app.services.otio_exporter.ensure_opening_titles_rendered",
        return_value=(items, []),
    ), patch(
        "otio_app.services.otio_exporter.collect_timeline_media_issues",
        return_value=[],
    ), patch(
        "otio_app.services.otio_exporter.build_otio_timeline",
    ) as mock_build, patch(
        "otio_app.services.otio_exporter.validate_otio_readback",
        return_value=[],
    ), patch(
        "otio_app.services.otio_exporter.otio.adapters.write_to_file",
    ), patch(
        "otio_app.services.otio_exporter.otio.adapters.read_from_file",
    ):
        mock_validate.return_value = TimelineValidationResult(
            status=ValidationStatus.OK, errors=[], warnings=[]
        )

        import opentimelineio as otio

        mock_build.return_value = otio.schema.Timeline(name="t")
        merged = merge_confirmed_edit_plans(project, folder_names=["Florida Keys"])
        assert merged.ready

        def on_progress(event: OtioExportProgressEvent) -> None:
            events.append(event)

        export_otio_timeline(project, merged, progress_callback=on_progress)
    stages = [e.stage for e in events]
    assert "titles" in stages
    assert "media_check" in stages
    assert "auto_clean" not in stages
    assert "write" in stages
    assert "done" in stages


def test_export_cancel_raises(tmp_path: Path) -> None:
    project = _project(tmp_path)
    from otio_app.services.otio_exporter import MergedEditPlanResult

    merged = MergedEditPlanResult(
        timeline_items=[
            TimelineItem(
                timeline_item_id="x",
                type="video_shot",
                section_id="s",
                folder_name="F",
                resolved_media_path="/x.mp4",
                timeline_in_sec=0,
                timeline_out_sec=1,
                duration_sec=1,
                final_duration_sec=1,
            )
        ],
        shots=[],
        settings=EditPlanSettings(),
        validation_status="OK",
    )
    with pytest.raises(OtioExportCancelled):
        export_otio_timeline(project, merged, should_cancel=lambda: True)
