"""Gemini-Client — API-Schlüssel nur aus Umgebungsvariablen."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from otio_app.config import get_env

DEFAULT_MODEL = "gemini-2.0-flash"


class GeminiNotConfiguredError(RuntimeError):
    """GEMINI_API_KEY fehlt."""


def _get_client():
    api_key = get_env("GEMINI_API_KEY")
    if not api_key or not api_key.strip():
        raise GeminiNotConfiguredError(
            "GEMINI_API_KEY ist nicht gesetzt. Bitte in .env eintragen."
        )
    from google import genai

    return genai.Client(api_key=api_key.strip())


def _extract_json(text: str) -> Any:
    cleaned = text.strip()
    fence_match = re.search(r"```(?:json)?\s*(.*?)\s*```", cleaned, re.DOTALL)
    if fence_match:
        cleaned = fence_match.group(1)
    return json.loads(cleaned)


def describe_folder_from_frames(
    folder_name: str,
    frame_paths: list[Path],
    language: str,
) -> str:
    """Sendet nur Frame-Bilder an Gemini und liefert eine Ordnerbeschreibung."""
    if not frame_paths:
        return "Keine Frames verfügbar."

    client = _get_client()
    from google.genai import types

    parts: list[types.Part] = [
        types.Part.from_text(
            text=(
                f"Du analysierst Bildmaterial für den Ordner '{folder_name}'. "
                f"Sprache der Antwort: {language}. "
                "Beschreibe kurz und sachlich, was zu sehen ist (Ort, Motiv, Stimmung, "
                "Kameraperspektive). Maximal 6 Sätze."
            )
        )
    ]
    for frame_path in frame_paths:
        parts.append(
            types.Part.from_bytes(
                data=frame_path.read_bytes(),
                mime_type="image/jpeg",
            )
        )

    response = client.models.generate_content(
        model=DEFAULT_MODEL,
        contents=[types.Content(role="user", parts=parts)],
    )
    return (response.text or "").strip()


def analyze_voice_over_file(
    audio_path: Path,
    language: str,
) -> dict[str, Any]:
    """Analysiert Voice-over-Audio mit Timestamps pro Passage."""
    client = _get_client()
    from google.genai import types

    mime_map = {
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
        ".aac": "audio/aac",
        ".flac": "audio/flac",
        ".ogg": "audio/ogg",
    }
    mime_type = mime_map.get(audio_path.suffix.lower(), "audio/mpeg")
    prompt = (
        "Transkribiere diese Voice-over-Datei. "
        f"Sprache: {language}. "
        "Antworte NUR als JSON-Objekt mit diesem Schema:\n"
        '{"segments":[{"start_sec":0.0,"end_sec":1.2,"text":"..."}]}\n'
        "start_sec/end_sec in Sekunden (float). Jede Passage ein Segment."
    )
    parts = [
        types.Part.from_text(text=prompt),
        types.Part.from_bytes(data=audio_path.read_bytes(), mime_type=mime_type),
    ]
    response = client.models.generate_content(
        model=DEFAULT_MODEL,
        contents=[types.Content(role="user", parts=parts)],
    )
    text = response.text or "{}"
    try:
        payload = _extract_json(text)
    except json.JSONDecodeError:
        payload = {"segments": [{"start_sec": 0.0, "end_sec": 0.0, "text": text.strip()}]}
    return payload


def is_gemini_configured() -> bool:
    key = get_env("GEMINI_API_KEY")
    return bool(key and key.strip())
