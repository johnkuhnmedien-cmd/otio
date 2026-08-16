"""Intro-Hook-Einstellungen (Phase 5)."""

from __future__ import annotations

import json

from otio_app.models import Project
from otio_app.project_layout import get_intro_hook_settings_path
from otio_app.services.voiceover_generation.intro_hook_defaults_service import (
    apply_language_defaults_to_settings,
    load_language_intro_defaults,
)
from otio_app.services.voiceover_generation.models import IntroHookSettings
from otio_app.services.voiceover_generation.project_brief_defaults_service import (
    normalize_brief_language,
)
from otio_app.services.voiceover_generation.project_brief_service import load_project_brief

__all__ = [
    "default_intro_hook_settings",
    "load_intro_hook_settings",
    "save_intro_hook_settings",
]


def default_intro_hook_settings(project: Project) -> IntroHookSettings:
    """Sprache aus dem Projekt; Tonalität aus Sprachstandard, sonst Brief."""
    language = normalize_brief_language(project.language)
    brief = load_project_brief(project)
    tone = brief.tone_tags[0] if brief.tone_tags else "cinematic"
    settings = IntroHookSettings(
        project_id=project.id,
        language=language,
        tone=tone,
    )
    language_defaults = load_language_intro_defaults(language)
    if language_defaults is None:
        return settings
    return apply_language_defaults_to_settings(settings, language_defaults)


def load_intro_hook_settings(project: Project) -> IntroHookSettings:
    path = get_intro_hook_settings_path(project.language_work_dir_path)
    if not path.is_file():
        return default_intro_hook_settings(project)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return IntroHookSettings.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return default_intro_hook_settings(project)


def save_intro_hook_settings(project: Project, settings: IntroHookSettings) -> IntroHookSettings:
    normalized = settings.model_copy(update={"project_id": project.id})
    path = get_intro_hook_settings_path(project.language_work_dir_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(normalized.model_dump_json(indent=2), encoding="utf-8")
    return normalized
