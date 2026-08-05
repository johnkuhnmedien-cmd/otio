"""ElevenLabs-TTS-Text für Enhanced.

Kapitel werden als reiner Sprechtext vertont (eine WAV pro Kapitel).
Autorenpausen / Pause-Marker werden aus dem TTS-Text entfernt — Pausen
baut der Nutzer selbst in die Kapitel-Audiodatei ein.

Hilfsfunktionen für v3-Pause-Tags bleiben für Kompatibilität erhalten,
werden im Enhanced-Kapitel-Flow aber nicht mehr angewendet.
"""

from __future__ import annotations

import re

from otio_app.defaults import (
    ELEVENLABS_MODEL_ID_V3,
    ELEVENLABS_V3_PAUSE_TAGS,
    PAUSE_AFTER_LONG,
    PAUSE_AFTER_MEDIUM,
    PAUSE_AFTER_SHORT,
)
from otio_app.services.without_voiceover_enhanced.script_chapter_text import (
    AUTHOR_PAUSE_MARKER_RE,
    normalize_author_pause_seconds,
)

__all__ = [
    "map_author_pause_seconds_to_v3_tag",
    "replace_timed_pause_markers_for_tts",
    "build_segment_tts_text",
    "build_chapter_tts_text",
    "strip_author_pause_markers",
]


def map_author_pause_seconds_to_v3_tag(seconds: float) -> str:
    """Mappt gewünschte Sekunden auf den nächsten eleven_v3-Pause-Tag."""
    value = normalize_author_pause_seconds(seconds)
    if value <= 0:
        return ""
    if value <= 2.0:
        return ELEVENLABS_V3_PAUSE_TAGS[PAUSE_AFTER_SHORT]
    if value <= 3.5:
        return ELEVENLABS_V3_PAUSE_TAGS[PAUSE_AFTER_MEDIUM]
    return ELEVENLABS_V3_PAUSE_TAGS[PAUSE_AFTER_LONG]


def strip_author_pause_markers(text: str) -> str:
    """Entfernt `[pause N seconds]`-Marker aus Fließtext."""
    cleaned = AUTHOR_PAUSE_MARKER_RE.sub(" ", text or "")
    return re.sub(r"[ \t]+", " ", cleaned).strip()


def replace_timed_pause_markers_for_tts(text: str, *, model_id: str) -> str:
    """Ersetzt/entfernt numerische Pausemarker je nach ElevenLabs-Modell."""

    def _sub(match: re.Match[str]) -> str:
        raw = match.group("seconds").replace(",", ".")
        try:
            seconds = float(raw)
        except ValueError:
            return " "
        if model_id != ELEVENLABS_MODEL_ID_V3:
            return " "
        tag = map_author_pause_seconds_to_v3_tag(seconds)
        return f" {tag} " if tag else " "

    replaced = AUTHOR_PAUSE_MARKER_RE.sub(_sub, text or "")
    # Zeilenumbrüche um Marker herum zu Leerzeichen verdichten, Inhalt sonst
    # möglichst belassen (Absätze bleiben für TTS unkritisch).
    collapsed = re.sub(r"[ \t]*\n[ \t]*", "\n", replaced)
    collapsed = re.sub(r"[ \t]{2,}", " ", collapsed)
    return collapsed.strip()


def build_segment_tts_text(
    *,
    text: str,
    author_pause_after_seconds: float = 0.0,
    model_id: str,
) -> str:
    """Text, der tatsächlich an ElevenLabs gesendet wird.

    Autorenpausen werden nicht in Audio geschrieben — der Nutzer baut Pausen
    selbst in die Kapitel-WAV ein. ``author_pause_after_seconds`` und
    ``model_id`` bleiben für Aufrufkompatibilität erhalten.
    """
    del author_pause_after_seconds, model_id
    return strip_author_pause_markers(text)


def build_chapter_tts_text(
    segments: list[object],
    *,
    model_id: str,
) -> tuple[str, list[tuple[str, str]]]:
    """Baut den Kapitel-TTS-Text für **einen** ElevenLabs-Call.

    Eine Audiodatei pro Kapitel; keine Pause-Tags / keine Segment-Slices.
    Pausen baut der Nutzer selbst in die Kapitel-WAV ein.

    Returns:
        full_tts_text: gesamter an ElevenLabs gesendeter Sprechtext
        align_parts: ``(segment_id, spoken_body)`` für Character-Timestamp-Alignment
    """
    del model_id
    pieces: list[str] = []
    align_parts: list[tuple[str, str]] = []
    for segment in segments:
        segment_id = str(getattr(segment, "segment_id", "") or "").strip()
        raw_text = str(getattr(segment, "text", "") or "")
        body = strip_author_pause_markers(raw_text)
        if not body.strip() or not segment_id:
            continue
        pieces.append(body.strip())
        align_parts.append((segment_id, body.strip()))
    if not pieces:
        return "", []
    return " ".join(pieces), align_parts
