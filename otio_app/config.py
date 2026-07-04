"""Anwendungskonfiguration — alle Pfade relativ zum Repository-Root."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from otio_app.defaults import (
    DEFAULT_FRAMES_PER_SHOT,
    DEFAULT_GEMINI_MODEL,
    DEFAULT_VOICE_BACKEND,
    DEFAULT_VOICE_OVER_SUBDIR,
    DEFAULT_WHISPER_MODEL,
    DEFAULT_WORK_SUBDIR,
    GEMINI_MODEL_CHOICES,
    INVENTORY_FILENAME,
    VOICE_ANALYSIS_FILENAME,
    VOICE_BACKEND_CHOICES,
    WHISPER_MODEL_CHOICES,
    resolve_voice_backend,
    resolve_whisper_model,
)

PACKAGE_DIR = Path(__file__).resolve().parent
APP_ROOT = PACKAGE_DIR.parent
DATA_DIR = APP_ROOT / "data"

load_dotenv(APP_ROOT / ".env")


def get_db_path() -> Path:
    """Pfad zur SQLite-Datenbank für das Projektregister."""
    return DATA_DIR / "projects.db"


def ensure_data_dir() -> Path:
    """Stellt sicher, dass das Laufzeitverzeichnis existiert."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR


def get_env(key: str, default: str | None = None) -> str | None:
    """Liest eine Umgebungsvariable (z. B. API-Schlüssel)."""
    return os.environ.get(key, default)


def get_gemini_model_from_env() -> str:
    """Liest das Standard-Gemini-Modell aus .env oder liefert den App-Default."""
    raw = get_env("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
    if not raw:
        return DEFAULT_GEMINI_MODEL
    model = raw.strip()
    if model in GEMINI_MODEL_CHOICES:
        return model
    return DEFAULT_GEMINI_MODEL


def get_voice_backend_from_env() -> str:
    """Liest die Standard-Engine für Voice-over aus .env."""
    raw = get_env("VOICE_OVER_BACKEND", DEFAULT_VOICE_BACKEND)
    return resolve_voice_backend(raw)


def get_whisper_model_from_env() -> str:
    """Liest das Standard-Whisper-Modell aus .env."""
    raw = get_env("WHISPER_MODEL", DEFAULT_WHISPER_MODEL)
    return resolve_whisper_model(raw)


__all__ = [
    "APP_ROOT",
    "DATA_DIR",
    "DEFAULT_FRAMES_PER_SHOT",
    "DEFAULT_GEMINI_MODEL",
    "DEFAULT_VOICE_OVER_SUBDIR",
    "DEFAULT_WORK_SUBDIR",
    "GEMINI_MODEL_CHOICES",
    "INVENTORY_FILENAME",
    "PACKAGE_DIR",
    "VOICE_ANALYSIS_FILENAME",
    "ensure_data_dir",
    "get_db_path",
    "get_env",
    "get_gemini_model_from_env",
    "get_voice_backend_from_env",
    "get_whisper_model_from_env",
]
