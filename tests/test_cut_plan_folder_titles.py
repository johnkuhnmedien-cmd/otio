"""Cut-Plan Ordner-Titel (Without-VO Opening Titles)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from otio_app.analysis_models import TimelineItem
from otio_app.models import Project
from otio_app.services.title_style import FontResolution
from otio_app.services.voiceover_generation.cut_plan_models import CutPlanSettings
from otio_app.services.voiceover_generation.cut_plan_settings_service import (
    load_cut_plan_settings,
    save_cut_plan_settings,
)
from otio_app.services.voiceover_generation.production_edit_plan_mapper import SectionIdentity
from otio_app.services.voiceover_generation.production_edit_plan_staging_service import (
    _maybe_prepend_folder_opening_title,
)


def _project(tmp_path: Path) -> Project:
    root = tmp_path / "USA"
    root.mkdir()
    work = root / "_otio"
    work.mkdir()
    return Project(
        id="cut-plan-titles",
        name="USA",
        project_root=str(root),
        work_dir=str(work),
        asset_subdir_names=["Florida Keys"],
        selected_asset_subdirs=["Florida Keys"],
    )


def _folder_identity() -> SectionIdentity:
    return SectionIdentity(
        staging_section_id="001_Florida_Keys",
        production_section_id="section_Florida_Keys",
        folder_name="Florida Keys",
        is_intro=False,
        order_index=1,
    )


def _intro_identity() -> SectionIdentity:
    return SectionIdentity(
        staging_section_id="000_intro",
        production_section_id="section_intro",
        folder_name="Intro",
        is_intro=True,
        order_index=0,
    )


def _visual(folder: str, section_id: str) -> TimelineItem:
    return TimelineItem(
        timeline_item_id="v1",
        type="video_shot",
        section_id=section_id,
        folder_name=folder,
        track="V1",
        timeline_in_sec=0.0,
        timeline_out_sec=2.0,
        duration_sec=2.0,
        final_duration_sec=2.0,
        resolved_media_path="/x.mp4",
    )


def test_cut_plan_settings_persist_folder_title_fields(tmp_path: Path) -> None:
    project = _project(tmp_path)
    settings = CutPlanSettings(
        project_id=project.id,
        folder_title_enabled=True,
        folder_title_font="Phosphate",
        folder_title_duration_sec=6.0,
        folder_title_font_size=124.0,
    )
    save_cut_plan_settings(project, settings)
    loaded = load_cut_plan_settings(project)
    assert loaded.folder_title_enabled is True
    assert loaded.folder_title_font == "Phosphate"
    assert loaded.folder_title_duration_sec == 6.0
    assert loaded.folder_title_font_size == 124.0


def test_prepend_folder_opening_title_skips_intro(tmp_path: Path) -> None:
    project = _project(tmp_path)
    save_cut_plan_settings(
        project,
        CutPlanSettings(project_id=project.id, folder_title_enabled=True),
    )
    result = _maybe_prepend_folder_opening_title(
        project,
        identity=_intro_identity(),
        voiceover_path="/vo.mp3",
        timeline_items=[_visual("Intro", "section_intro")],
    )
    assert len(result) == 1
    assert result[0].type == "video_shot"


def test_prepend_folder_opening_title_for_folder(tmp_path: Path) -> None:
    project = _project(tmp_path)
    save_cut_plan_settings(
        project,
        CutPlanSettings(
            project_id=project.id,
            folder_title_enabled=True,
            folder_title_font="Phosphate",
            folder_title_duration_sec=6.0,
            folder_title_font_size=124.0,
        ),
    )
    font = FontResolution(
        font_path=Path("/tmp/font.ttf"),
        requested_font_family="Phosphate",
        resolved_font_family="Phosphate",
        resolved_font_file_path="/tmp/font.ttf",
        font_fallback_used=False,
        font_resolution_warning="",
    )
    with patch(
        "otio_app.services.opening_title_renderer.ffmpeg_has_drawtext",
        return_value=True,
    ), patch(
        "otio_app.services.title_style.resolve_title_font",
        return_value=font,
    ):
        result = _maybe_prepend_folder_opening_title(
            project,
            identity=_folder_identity(),
            voiceover_path="/vo.mp3",
            timeline_items=[_visual("Florida Keys", "section_Florida_Keys")],
        )
    assert len(result) == 2
    assert result[0].type == "opening_title"
    assert result[0].track == "V2"
    assert result[0].duration_sec == 6.0
    assert result[0].folder_name == "Florida Keys"
    assert result[1].type == "video_shot"


def test_prepend_folder_opening_title_disabled_noop(tmp_path: Path) -> None:
    project = _project(tmp_path)
    save_cut_plan_settings(
        project,
        CutPlanSettings(project_id=project.id, folder_title_enabled=False),
    )
    result = _maybe_prepend_folder_opening_title(
        project,
        identity=_folder_identity(),
        voiceover_path="/vo.mp3",
        timeline_items=[_visual("Florida Keys", "section_Florida_Keys")],
    )
    assert [item.type for item in result] == ["video_shot"]
