"""Persistente Einstellungen für den Clean-Media-Schritt (Schritt ⓪)."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from otio_app.models import Project

RULE_AUTO_ZOOM_FILL_LEGACY = "auto_zoom_fill"


class CleanMediaSettings(BaseModel):
    auto_zoom_fill: bool = False


def clean_media_settings_path(project: Project) -> Path:
    return project.work_dir_path / "clean_media_settings.json"


def _consume_legacy_auto_zoom_rule(project: Project) -> bool | None:
    """Übernimmt die alte Auto-Zoom-Regel aus edit_plan_rules.json (einmalig)."""
    from otio_app.services.edit_plan_rules import load_edit_plan_rules, save_edit_plan_rules

    document = load_edit_plan_rules(project)
    legacy_rule = next(
        (rule for rule in document.rules if rule.rule_type == RULE_AUTO_ZOOM_FILL_LEGACY),
        None,
    )
    if legacy_rule is None:
        return None

    enabled = legacy_rule.enabled
    remaining = [rule for rule in document.rules if rule.rule_type != RULE_AUTO_ZOOM_FILL_LEGACY]
    if len(remaining) != len(document.rules):
        save_edit_plan_rules(project, document.model_copy(update={"rules": remaining}))
    return enabled


def load_clean_media_settings(project: Project) -> CleanMediaSettings:
    path = clean_media_settings_path(project)
    if path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return CleanMediaSettings.model_validate(payload)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            pass

    legacy_enabled = _consume_legacy_auto_zoom_rule(project)
    if legacy_enabled is not None:
        settings = CleanMediaSettings(auto_zoom_fill=legacy_enabled)
        save_clean_media_settings(project, settings)
        return settings

    return CleanMediaSettings()


def save_clean_media_settings(project: Project, settings: CleanMediaSettings) -> Path:
    path = clean_media_settings_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(settings.model_dump_json(indent=2), encoding="utf-8")
    return path
