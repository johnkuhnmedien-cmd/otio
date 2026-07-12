"""Projektweite Settings für die Asset-Readiness Bulk-Pipeline."""

from __future__ import annotations

import json

from otio_app.defaults import FOLDER_ASSET_READINESS_HIGH_ISSUE_REGEN_THRESHOLD
from otio_app.models import Project
from otio_app.project_layout import get_asset_readiness_pipeline_settings_path
from otio_app.services.voiceover_generation.models import AssetReadinessPipelineSettings

__all__ = [
    "default_asset_readiness_pipeline_settings",
    "load_asset_readiness_pipeline_settings",
    "save_asset_readiness_pipeline_settings",
]


def default_asset_readiness_pipeline_settings(project: Project) -> AssetReadinessPipelineSettings:
    return AssetReadinessPipelineSettings(
        project_id=project.id,
        high_issue_regen_threshold=FOLDER_ASSET_READINESS_HIGH_ISSUE_REGEN_THRESHOLD,
    )


def load_asset_readiness_pipeline_settings(project: Project) -> AssetReadinessPipelineSettings:
    path = get_asset_readiness_pipeline_settings_path(project.work_dir_path)
    if not path.is_file():
        return default_asset_readiness_pipeline_settings(project)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return AssetReadinessPipelineSettings.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return default_asset_readiness_pipeline_settings(project)


def save_asset_readiness_pipeline_settings(
    project: Project, settings: AssetReadinessPipelineSettings
) -> AssetReadinessPipelineSettings:
    threshold = max(1, int(settings.high_issue_regen_threshold))
    normalized = settings.model_copy(
        update={"project_id": project.id, "high_issue_regen_threshold": threshold}
    )
    path = get_asset_readiness_pipeline_settings_path(project.work_dir_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(normalized.model_dump_json(indent=2), encoding="utf-8")
    return normalized
