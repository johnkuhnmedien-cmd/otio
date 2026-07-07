"""Persistente Timing-/Gemini-Einstellungen für den Schnittplan-Vorschlag.

Diese Werte (Min./Max. Shot, Audio-Start, Ordner-Ausklingen, Text-Trenner,
Gemini-Modell) wurden bisher NUR im Streamlit-`session_state` gehalten und
nirgends dauerhaft gespeichert. Nach einem Seitenwechsel, Browser-Reload oder
App-Neustart fielen sie stillschweigend auf die Hardcoded-Defaults zurück —
unabhängig davon, was der Nutzer zuvor eingestellt hatte. Dieses Modul macht
sie projektbezogen persistent, analog zu `edit_plan_rules.json`.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

from otio_app.defaults import (
    DEFAULT_AUDIO_OFFSET_SEC,
    DEFAULT_GEMINI_MODEL,
    DEFAULT_SECTION_OUTRO_SEC,
    DEFAULT_SHOT_MAX_SEC,
    DEFAULT_SHOT_MIN_SEC,
)
from otio_app.models import Project

DEFAULT_TEXT_SPLIT_INPUT = ", und ,, , und "


class EditPlanTimingSettings(BaseModel):
    shot_min_sec: float = DEFAULT_SHOT_MIN_SEC
    shot_max_sec: float = DEFAULT_SHOT_MAX_SEC
    audio_offset_sec: float = DEFAULT_AUDIO_OFFSET_SEC
    section_outro_sec: float = DEFAULT_SECTION_OUTRO_SEC
    text_splitters: str = DEFAULT_TEXT_SPLIT_INPUT
    gemini_model: str = DEFAULT_GEMINI_MODEL


def edit_plan_timing_settings_path(project: Project) -> Path:
    return project.work_dir_path / "edit_plan_timing_settings.json"


def load_edit_plan_timing_settings(project: Project) -> EditPlanTimingSettings:
    path = edit_plan_timing_settings_path(project)
    if not path.is_file():
        return EditPlanTimingSettings()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return EditPlanTimingSettings.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return EditPlanTimingSettings()


def save_edit_plan_timing_settings(project: Project, settings: EditPlanTimingSettings) -> Path:
    path = edit_plan_timing_settings_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(settings.model_dump_json(indent=2), encoding="utf-8")
    return path
