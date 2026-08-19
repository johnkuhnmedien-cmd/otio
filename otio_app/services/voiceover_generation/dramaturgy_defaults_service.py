"""Dramaturgie-Standards unter ``data/``.

Planungsmodus ist global (Auto-Lauf). Zielwortzahl und Toleranz liegen
pro Sprache — analog Brief- und Style-Defaults.
"""

from __future__ import annotations

import json
from pathlib import Path

from otio_app.config import ensure_data_dir
from otio_app.defaults import (
    DRAMATURGY_DEFAULTS_FILENAME,
    DRAMATURGY_PLANNING_MODE_CHOICES,
    DRAMATURGY_PLANNING_MODE_DEFAULT,
    DRAMATURGY_TARGET_WORDS_INPUT_MAX,
    VOICEOVER_GEN_DEFAULT_FOLDER_TARGET_WORDS,
    VOICEOVER_GEN_DEFAULT_WORD_TOLERANCE_PERCENT,
    VOICEOVER_GEN_MIN_FOLDER_WORDS,
)
from otio_app.services.voiceover_generation.models import (
    DramaturgyDefaults,
    DramaturgySettings,
    DramaturgyWordDefaults,
)
from otio_app.services.voiceover_generation.project_brief_defaults_service import (
    normalize_brief_language,
)

__all__ = [
    "auto_run_dramaturgy_planning_mode",
    "clamp_dramaturgy_target_words",
    "clamp_dramaturgy_tolerance_percent",
    "get_dramaturgy_defaults_path",
    "load_dramaturgy_defaults",
    "load_language_dramaturgy_word_defaults",
    "normalize_dramaturgy_planning_mode",
    "resolve_dramaturgy_planning_mode",
    "save_dramaturgy_defaults",
    "save_language_dramaturgy_word_defaults",
    "word_defaults_from_settings",
]


def get_dramaturgy_defaults_path() -> Path:
    return ensure_data_dir() / DRAMATURGY_DEFAULTS_FILENAME


def clamp_dramaturgy_target_words(value: int | None) -> int:
    try:
        raw = int(value or 0)
    except (TypeError, ValueError):
        raw = VOICEOVER_GEN_DEFAULT_FOLDER_TARGET_WORDS
    if raw <= 0:
        raw = VOICEOVER_GEN_DEFAULT_FOLDER_TARGET_WORDS
    return max(VOICEOVER_GEN_MIN_FOLDER_WORDS, min(DRAMATURGY_TARGET_WORDS_INPUT_MAX, raw))


def clamp_dramaturgy_tolerance_percent(value: int | None) -> int:
    try:
        raw = int(value if value is not None else VOICEOVER_GEN_DEFAULT_WORD_TOLERANCE_PERCENT)
    except (TypeError, ValueError):
        raw = VOICEOVER_GEN_DEFAULT_WORD_TOLERANCE_PERCENT
    return max(0, min(100, raw))


def normalize_dramaturgy_planning_mode(mode: str | None) -> str:
    cleaned = (mode or "").strip().lower()
    if cleaned in DRAMATURGY_PLANNING_MODE_CHOICES:
        return cleaned
    return DRAMATURGY_PLANNING_MODE_DEFAULT


def load_dramaturgy_defaults() -> DramaturgyDefaults:
    path = get_dramaturgy_defaults_path()
    if not path.is_file():
        return DramaturgyDefaults()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        loaded = DramaturgyDefaults.model_validate(payload)
        return loaded.model_copy(
            update={
                "planning_mode": normalize_dramaturgy_planning_mode(loaded.planning_mode),
                "by_language": {
                    key: DramaturgyWordDefaults(
                        target_words=clamp_dramaturgy_target_words(entry.target_words),
                        word_tolerance_percent=clamp_dramaturgy_tolerance_percent(
                            entry.word_tolerance_percent
                        ),
                    )
                    for key, entry in loaded.by_language.items()
                },
            }
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return DramaturgyDefaults()


def save_dramaturgy_defaults(
    defaults: DramaturgyDefaults | str,
) -> DramaturgyDefaults:
    current = load_dramaturgy_defaults()
    if isinstance(defaults, str):
        entry = current.model_copy(
            update={"planning_mode": normalize_dramaturgy_planning_mode(defaults)}
        )
    else:
        entry = defaults.model_copy(
            update={
                "planning_mode": normalize_dramaturgy_planning_mode(defaults.planning_mode)
            }
        )
    path = get_dramaturgy_defaults_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(entry.model_dump_json(indent=2), encoding="utf-8")
    return entry


def load_language_dramaturgy_word_defaults(language: str) -> DramaturgyWordDefaults | None:
    key = normalize_brief_language(language)
    return load_dramaturgy_defaults().by_language.get(key)


def word_defaults_from_settings(
    settings: DramaturgySettings | DramaturgyWordDefaults,
) -> DramaturgyWordDefaults:
    return DramaturgyWordDefaults(
        target_words=clamp_dramaturgy_target_words(settings.target_words),
        word_tolerance_percent=clamp_dramaturgy_tolerance_percent(
            settings.word_tolerance_percent
        ),
    )


def save_language_dramaturgy_word_defaults(
    language: str,
    settings: DramaturgySettings | DramaturgyWordDefaults,
) -> DramaturgyWordDefaults:
    key = normalize_brief_language(language)
    entry = word_defaults_from_settings(settings)
    document = load_dramaturgy_defaults()
    updated = dict(document.by_language)
    updated[key] = entry
    save_dramaturgy_defaults(document.model_copy(update={"by_language": updated}))
    return entry


def resolve_dramaturgy_planning_mode(explicit: str | None = None) -> str:
    """Expliziter Modus gewinnt; sonst der gespeicherte bzw. werkseitige Standard."""
    cleaned = (explicit or "").strip().lower()
    if cleaned in DRAMATURGY_PLANNING_MODE_CHOICES:
        return cleaned
    return normalize_dramaturgy_planning_mode(load_dramaturgy_defaults().planning_mode)


def auto_run_dramaturgy_planning_mode() -> str:
    """Planungsmodus für den automatischen Durchlauf."""
    return resolve_dramaturgy_planning_mode()
