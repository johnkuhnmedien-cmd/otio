"""Projektweite Optionen für Enhanced-Skripterzeugung (Schritt ④)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from otio_app.models import Project
from otio_app.services.without_voiceover_enhanced.io_utils import load_model, write_json
from otio_app.services.without_voiceover_enhanced.paths import script_options_path

SCRIPT_MODE_RESEARCH = "research"
SCRIPT_MODE_ASSET_GROUNDED = "asset_grounded"
ScriptMode = Literal["research", "asset_grounded"]
SCRIPT_MODE_CHOICES: tuple[str, ...] = (
    SCRIPT_MODE_RESEARCH,
    SCRIPT_MODE_ASSET_GROUNDED,
)

SCRIPT_MODE_LABELS_DE: dict[str, str] = {
    SCRIPT_MODE_RESEARCH: "Freies Skript (Brief + Dramaturgie)",
    SCRIPT_MODE_ASSET_GROUNDED: "Skript aus vorhandenem Material",
}


class ScriptOptions(BaseModel):
    """Gespeicherte Skript-Einstellungen unter ``_otio_enhanced/config``."""

    script_mode: ScriptMode = Field(default=SCRIPT_MODE_RESEARCH)


def default_script_options() -> ScriptOptions:
    return ScriptOptions()


def normalize_script_mode(value: str | None) -> ScriptMode:
    mode = str(value or "").strip().lower()
    if mode == SCRIPT_MODE_ASSET_GROUNDED:
        return SCRIPT_MODE_ASSET_GROUNDED
    return SCRIPT_MODE_RESEARCH


def is_asset_grounded_script_mode(value: str | None) -> bool:
    return normalize_script_mode(value) == SCRIPT_MODE_ASSET_GROUNDED


def load_script_options(project: Project) -> ScriptOptions:
    path = script_options_path(project)
    loaded = load_model(path, ScriptOptions)
    if loaded is None:
        return default_script_options()
    return loaded.model_copy(
        update={"script_mode": normalize_script_mode(loaded.script_mode)}
    )


def save_script_options(project: Project, options: ScriptOptions) -> ScriptOptions:
    normalized = options.model_copy(
        update={"script_mode": normalize_script_mode(options.script_mode)}
    )
    write_json(script_options_path(project), normalized)
    return normalized
