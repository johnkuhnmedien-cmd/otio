"""Zentrale Standardwerte — unabhängig von Pfad- und DB-Konfiguration."""

from __future__ import annotations

DEFAULT_WORK_SUBDIR = "_otio"
DEFAULT_VOICE_OVER_SUBDIR = "Voice over"
DEFAULT_FRAMES_PER_SHOT = 3
DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-lite"
DEFAULT_VOICE_BACKEND = "whisper"
DEFAULT_WHISPER_MODEL = "large-v3"
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
    "small": "Whisper small — schnell, ausgewogen",
    "medium": "Whisper medium — genauer, langsamer",
    "large-v3": "Whisper large-v3 — beste Qualität, am langsamsten (Standard)",
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
    "gemini-2.0-flash": "Gemini 2.0 Flash — schnell, günstig",
    "gemini-2.0-flash-lite": "Gemini 2.0 Flash Lite — sehr günstig",
    "gemini-2.5-flash": "Gemini 2.5 Flash — ausgewogen",
    "gemini-2.5-pro": "Gemini 2.5 Pro — höhere Qualität, teurer",
    "gemini-3-flash-preview": "Gemini 3 Flash Preview — schnell, Pro-Niveau (Preview)",
    "gemini-3.1-flash-lite": "Gemini 3.1 Flash Lite — günstig, Preview (Standard)",
    "gemini-3.1-pro-preview": "Gemini 3.1 Pro Preview — beste Qualität (Preview, teurer)",
}
INVENTORY_FILENAME = "inventory.json"
INVENTORY_SUBDIR = "inventory"
MANUAL_FOLDER_COMPLETION_FILENAME = "manual_folder_completion.json"
VOICE_ANALYSIS_FILENAME = "voice_over_analysis.json"
VOICE_FOLDER_MAPPING_FILENAME = "voice_folder_mapping.json"
EDIT_PLAN_FILENAME = "edit_plan.json"
EDIT_PLAN_SUBDIR = "edit_plan"
EXPORTS_SUBDIR = "exports"
CLEAN_MEDIA_OUTPUT_SUBDIR = "clean"
CLEAN_MEDIA_MANIFEST_SUBDIR = "clean_media"
DEFAULT_SHOT_MIN_SEC = 3.0
DEFAULT_SHOT_MAX_SEC = 8.0
DEFAULT_AUDIO_OFFSET_SEC = 1.0
DEFAULT_SECTION_OUTRO_SEC = 5.0
DEFAULT_TEXT_SPLITTERS = (", und ", ", ", " und ")
FALLBACK_SOURCE_LOCAL = "local"
FALLBACK_SOURCE_MISSING = "missing"
FALLBACK_SOURCE_ADOBE_STOCK = "adobe_stock"
FALLBACK_SOURCE_PEXELS = "pexels"
FALLBACK_SOURCE_GEMINI_IMAGE = "gemini_image"
FALLBACK_SOURCE_CHOICES = (
    FALLBACK_SOURCE_LOCAL,
    FALLBACK_SOURCE_ADOBE_STOCK,
    FALLBACK_SOURCE_PEXELS,
    FALLBACK_SOURCE_GEMINI_IMAGE,
)
FALLBACK_SOURCE_LABELS = {
    FALLBACK_SOURCE_LOCAL: "Lokale Assets",
    FALLBACK_SOURCE_ADOBE_STOCK: "Adobe Stock",
    FALLBACK_SOURCE_PEXELS: "Pexels & Co.",
    FALLBACK_SOURCE_GEMINI_IMAGE: "KI-Bild (Gemini)",
}
DEFAULT_FALLBACK_ORDER = (
    FALLBACK_SOURCE_LOCAL,
    FALLBACK_SOURCE_ADOBE_STOCK,
    FALLBACK_SOURCE_PEXELS,
    FALLBACK_SOURCE_GEMINI_IMAGE,
)


def resolve_voice_backend(backend: str | None) -> str:
    if backend and backend.strip() in VOICE_BACKEND_CHOICES:
        return backend.strip()
    return DEFAULT_VOICE_BACKEND


def resolve_whisper_model(model: str | None) -> str:
    if model and model.strip() in WHISPER_MODEL_CHOICES:
        return model.strip()
    return DEFAULT_WHISPER_MODEL
