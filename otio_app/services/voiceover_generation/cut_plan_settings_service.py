"""Cut-Plan-Einstellungen (Phase 8) — eigenständig, NICHT edit_plan_rules.json.

Getrennte Datei unter _otio/voiceover_generation/cut_plan/cut_plan_settings.json,
damit "Projekt ohne Voice-Over" nie an die Produktions-Regel-Datei der
WITH_VOICEOVER-Pipeline gekoppelt wird (schützt die bestehende Pipeline)."""

from __future__ import annotations

import json

from otio_app.models import Project
from otio_app.project_layout import get_cut_plan_settings_path
from otio_app.services.voiceover_generation.cut_plan_models import CutPlanSettings

__all__ = [
    "default_cut_plan_settings",
    "load_cut_plan_settings",
    "save_cut_plan_settings",
]


def default_cut_plan_settings(project: Project) -> CutPlanSettings:
    return CutPlanSettings(project_id=project.id)


def load_cut_plan_settings(project: Project) -> CutPlanSettings:
    path = get_cut_plan_settings_path(project.language_work_dir_path)
    if not path.is_file():
        return default_cut_plan_settings(project)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return CutPlanSettings.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return default_cut_plan_settings(project)


def save_cut_plan_settings(project: Project, settings: CutPlanSettings) -> CutPlanSettings:
    normalized = settings.model_copy(update={"project_id": project.id})
    path = get_cut_plan_settings_path(project.language_work_dir_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(normalized.model_dump_json(indent=2), encoding="utf-8")
    return normalized
