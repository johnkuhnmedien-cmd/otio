"""Globaler Dramaturgie-Standard (unter ``data/``).

Nicht pro Sprache — der automatische Durchlauf nutzt überall denselben
Planungsmodus. Werkseitig: visuell stärkste Orte zuerst.
"""

from __future__ import annotations

import json
from pathlib import Path

from otio_app.config import ensure_data_dir
from otio_app.defaults import (
    DRAMATURGY_DEFAULTS_FILENAME,
    DRAMATURGY_PLANNING_MODE_CHOICES,
    DRAMATURGY_PLANNING_MODE_DEFAULT,
)
from otio_app.services.voiceover_generation.models import DramaturgyDefaults

__all__ = [
    "auto_run_dramaturgy_planning_mode",
    "get_dramaturgy_defaults_path",
    "load_dramaturgy_defaults",
    "normalize_dramaturgy_planning_mode",
    "resolve_dramaturgy_planning_mode",
    "save_dramaturgy_defaults",
]


def get_dramaturgy_defaults_path() -> Path:
    return ensure_data_dir() / DRAMATURGY_DEFAULTS_FILENAME


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
            update={"planning_mode": normalize_dramaturgy_planning_mode(loaded.planning_mode)}
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return DramaturgyDefaults()


def save_dramaturgy_defaults(
    defaults: DramaturgyDefaults | str,
) -> DramaturgyDefaults:
    if isinstance(defaults, str):
        entry = DramaturgyDefaults(
            planning_mode=normalize_dramaturgy_planning_mode(defaults)
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


def resolve_dramaturgy_planning_mode(explicit: str | None = None) -> str:
    """Expliziter Modus gewinnt; sonst der gespeicherte bzw. werkseitige Standard."""
    cleaned = (explicit or "").strip().lower()
    if cleaned in DRAMATURGY_PLANNING_MODE_CHOICES:
        return cleaned
    return normalize_dramaturgy_planning_mode(load_dramaturgy_defaults().planning_mode)


def auto_run_dramaturgy_planning_mode() -> str:
    """Planungsmodus für den automatischen Durchlauf."""
    return resolve_dramaturgy_planning_mode()
