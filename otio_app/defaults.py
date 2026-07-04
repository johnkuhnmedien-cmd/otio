"""Zentrale Standardwerte — unabhängig von Pfad- und DB-Konfiguration."""

from __future__ import annotations

DEFAULT_WORK_SUBDIR = "_otio"
DEFAULT_VOICE_OVER_SUBDIR = "Voice over"
DEFAULT_FRAMES_PER_SHOT = 3
DEFAULT_GEMINI_MODEL = "gemini-2.0-flash"
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
