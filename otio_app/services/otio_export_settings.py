"""Persistente OTIO-Export-Einstellungen pro Projekt."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

from otio_app.defaults import DEFAULT_AUDIO_OFFSET_SEC, DEFAULT_SECTION_OUTRO_SEC
from otio_app.models import Project
from otio_app.services.still_image_export_style import (
    DEFAULT_STILL_IMAGE_ZOOM,
    STILL_BACKGROUND_VINTAGE,
)


class OtioExportSettings(BaseModel):
    audio_offset_sec: float = DEFAULT_AUDIO_OFFSET_SEC
    section_outro_sec: float = DEFAULT_SECTION_OUTRO_SEC
    # Still-Images: Zoom + Vintage-Hintergrund unmittelbar vor OTIO-Export
    still_image_style_enabled: bool = True
    still_image_zoom: float = Field(default=DEFAULT_STILL_IMAGE_ZOOM, ge=0.05, le=1.0)
    still_image_background_style: str = STILL_BACKGROUND_VINTAGE


def otio_export_settings_path(project: Project) -> Path:
    return project.language_work_dir_path / "otio_export_settings.json"


def load_otio_export_settings(project: Project) -> OtioExportSettings:
    path = otio_export_settings_path(project)
    if not path.is_file():
        return OtioExportSettings()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return OtioExportSettings.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return OtioExportSettings()


def save_otio_export_settings(project: Project, settings: OtioExportSettings) -> Path:
    path = otio_export_settings_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(settings.model_dump_json(indent=2), encoding="utf-8")
    return path
