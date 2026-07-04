"""Zentrale Standardwerte — unabhängig von Pfad- und DB-Konfiguration."""

from __future__ import annotations

DEFAULT_WORK_SUBDIR = "_otio"
DEFAULT_VOICE_OVER_SUBDIR = "Voice over"
DEFAULT_FRAMES_PER_SHOT = 3
DEFAULT_GEMINI_MODEL = "gemini-2.0-flash"
DEFAULT_VOICE_BACKEND = "whisper"
DEFAULT_WHISPER_MODEL = "small"
VOICE_BACKEND_WHISPER = "whisper"
VOICE_BACKEND_GEMINI = "gemini"
VOICE_BACKEND_CHOICES = (
    VOICE_BACKEND_WHISPER,
    VOICE_BACKEND_GEMINI,
)
VOICE_BACKEND_LABELS = {
    VOICE_BACKEND_WHISPER: "Whisper (lokal, kostenlos)",
    VOICE_BACKEND_GEMINI: "Gemini (Cloud, kostenpflichtig)",
}
WHISPER_MODEL_CHOICES = (
    "tiny",
    "base",
    "small",
    "medium",
    "large-v3",
)
WHISPER_MODEL_LABELS = {
    "tiny": "Whisper tiny — am schnellsten, grober",
    "base": "Whisper base — schnell",
    "small": "Whisper small — empfohlen (Standard)",
    "medium": "Whisper medium — genauer, langsamer",
    "large-v3": "Whisper large-v3 — beste Qualität, am langsamsten",
}
GEMINI_MODEL_CHOICES = (
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-3-flash-preview",
    "gemini-3.1-flash-lite",
    "gemini-3.1-pro-preview",
)
GEMINI_MODEL_LABELS = {
    "gemini-2.0-flash": "Gemini 2.0 Flash — schnell, günstig (Standard)",
    "gemini-2.0-flash-lite": "Gemini 2.0 Flash Lite — sehr günstig",
    "gemini-2.5-flash": "Gemini 2.5 Flash — ausgewogen",
    "gemini-2.5-pro": "Gemini 2.5 Pro — höhere Qualität, teurer",
    "gemini-3-flash-preview": "Gemini 3 Flash Preview — schnell, Pro-Niveau (Preview)",
    "gemini-3.1-flash-lite": "Gemini 3.1 Flash Lite — günstig, Preview",
    "gemini-3.1-pro-preview": "Gemini 3.1 Pro Preview — beste Qualität (Preview, teurer)",
}
INVENTORY_FILENAME = "inventory.json"
VOICE_ANALYSIS_FILENAME = "voice_over_analysis.json"
VOICE_FOLDER_MAPPING_FILENAME = "voice_folder_mapping.json"


def resolve_voice_backend(backend: str | None) -> str:
    if backend and backend.strip() in VOICE_BACKEND_CHOICES:
        return backend.strip()
    return DEFAULT_VOICE_BACKEND


def resolve_whisper_model(model: str | None) -> str:
    if model and model.strip() in WHISPER_MODEL_CHOICES:
        return model.strip()
    return DEFAULT_WHISPER_MODEL
