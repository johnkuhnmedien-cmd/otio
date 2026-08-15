"""Projektspezifische Dramaturgie-Wortziele (Ziel + Toleranz)."""

from __future__ import annotations

import json
from dataclasses import dataclass

from otio_app.defaults import (
    VOICEOVER_GEN_DEFAULT_FOLDER_TARGET_WORDS,
    VOICEOVER_GEN_DEFAULT_WORD_TOLERANCE_PERCENT,
    VOICEOVER_GEN_MIN_FOLDER_WORDS,
    intro_word_window,
)
from otio_app.models import Project
from otio_app.project_layout import get_dramaturgy_settings_path
from otio_app.services.voiceover_generation.dramaturgy_defaults_service import (
    clamp_dramaturgy_target_words,
    clamp_dramaturgy_tolerance_percent,
    load_language_dramaturgy_word_defaults,
)
from otio_app.services.voiceover_generation.models import DramaturgySettings

__all__ = [
    "DramaturgyWordBand",
    "default_dramaturgy_settings",
    "load_dramaturgy_settings",
    "save_dramaturgy_settings",
    "word_band_from_settings",
]


@dataclass(frozen=True)
class DramaturgyWordBand:
    target_words: int
    tolerance_percent: int
    min_words: int
    max_words: int

    @property
    def tolerance_words(self) -> int:
        return max(0, self.max_words - self.target_words)


def word_band_from_settings(settings: DramaturgySettings) -> DramaturgyWordBand:
    target = clamp_dramaturgy_target_words(settings.target_words)
    percent = clamp_dramaturgy_tolerance_percent(settings.word_tolerance_percent)
    window_min, window_max = intro_word_window(target, percent)
    return DramaturgyWordBand(
        target_words=target,
        tolerance_percent=percent,
        min_words=max(VOICEOVER_GEN_MIN_FOLDER_WORDS, window_min),
        max_words=max(target, window_max),
    )


def default_dramaturgy_settings(project: Project) -> DramaturgySettings:
    language_defaults = load_language_dramaturgy_word_defaults(project.language)
    if language_defaults is None:
        return DramaturgySettings(
            project_id=project.id,
            target_words=VOICEOVER_GEN_DEFAULT_FOLDER_TARGET_WORDS,
            word_tolerance_percent=VOICEOVER_GEN_DEFAULT_WORD_TOLERANCE_PERCENT,
        )
    return DramaturgySettings(
        project_id=project.id,
        target_words=clamp_dramaturgy_target_words(language_defaults.target_words),
        word_tolerance_percent=clamp_dramaturgy_tolerance_percent(
            language_defaults.word_tolerance_percent
        ),
    )


def load_dramaturgy_settings(project: Project) -> DramaturgySettings:
    path = get_dramaturgy_settings_path(project.language_work_dir_path)
    if not path.is_file():
        return default_dramaturgy_settings(project)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        loaded = DramaturgySettings.model_validate(payload)
        return loaded.model_copy(
            update={
                "project_id": project.id,
                "target_words": clamp_dramaturgy_target_words(loaded.target_words),
                "word_tolerance_percent": clamp_dramaturgy_tolerance_percent(
                    loaded.word_tolerance_percent
                ),
            }
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return default_dramaturgy_settings(project)


def save_dramaturgy_settings(
    project: Project, settings: DramaturgySettings
) -> DramaturgySettings:
    normalized = settings.model_copy(
        update={
            "project_id": project.id,
            "target_words": clamp_dramaturgy_target_words(settings.target_words),
            "word_tolerance_percent": clamp_dramaturgy_tolerance_percent(
                settings.word_tolerance_percent
            ),
        }
    )
    path = get_dramaturgy_settings_path(project.language_work_dir_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(normalized.model_dump_json(indent=2), encoding="utf-8")
    return normalized
