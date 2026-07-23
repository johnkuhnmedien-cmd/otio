"""Persistente Issue-Schwelle für die Asset-Readiness Magic-Pipeline."""

from __future__ import annotations

from pathlib import Path

from otio_app.defaults import FOLDER_ASSET_READINESS_HIGH_ISSUE_REGEN_THRESHOLD
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import get_asset_readiness_pipeline_settings_path
from otio_app.services.voiceover_generation.asset_readiness_pipeline_settings_service import (
    load_asset_readiness_pipeline_settings,
    save_asset_readiness_pipeline_settings,
)
from otio_app.services.voiceover_generation.models import AssetReadinessPipelineSettings


def _make_project(tmp_path: Path) -> Project:
    project_root = tmp_path / "USA"
    project_root.mkdir(parents=True)
    return Project(
        id="pipeline-settings-project",
        name="Pipeline Settings",
        project_root=str(project_root),
        work_dir=str(project_root / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )


def test_load_defaults_when_missing(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    settings = load_asset_readiness_pipeline_settings(project)
    assert settings.high_issue_regen_threshold == FOLDER_ASSET_READINESS_HIGH_ISSUE_REGEN_THRESHOLD
    assert not get_asset_readiness_pipeline_settings_path(project.work_dir_path).is_file()


def test_save_and_reload_threshold(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    save_asset_readiness_pipeline_settings(
        project,
        AssetReadinessPipelineSettings(project_id=project.id, high_issue_regen_threshold=7),
    )
    path = get_asset_readiness_pipeline_settings_path(project.work_dir_path)
    assert path.is_file()
    reloaded = load_asset_readiness_pipeline_settings(project)
    assert reloaded.high_issue_regen_threshold == 7


def test_save_clamps_threshold_to_at_least_one(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    saved = save_asset_readiness_pipeline_settings(
        project,
        AssetReadinessPipelineSettings(project_id=project.id, high_issue_regen_threshold=0),
    )
    assert saved.high_issue_regen_threshold == 1
