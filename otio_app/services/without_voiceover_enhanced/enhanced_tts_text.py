"""ElevenLabs-TTS-Text für Enhanced: Autorenpausen → eleven_v3-Tags.

Wie im klassischen Folder-Voice-over (tts_text_builder): Nur `eleven_v3`
versteht Bracket-Pause-Tags. Exakte Sekundenzahlen wie
`[pause 3 seconds]` sind über die API nicht steuerbar — sie werden auf
`[short pause]` / `[pause]` / `[long pause]` abgebildet.

Für andere Modelle werden Marker entfernt (sonst würden sie vorgelesen).

Der Cut-LLM bekommt die gemessenen ElevenLabs-Timestamps der fertigen
Kapitel-WAV und braucht die Pause-Tags nicht als Schnittvorgabe.
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
    """Text, der tatsächlich an ElevenLabs gesendet wird."""
    body = replace_timed_pause_markers_for_tts(text, model_id=model_id)
    if model_id != ELEVENLABS_MODEL_ID_V3:
        return body
    pause = normalize_author_pause_seconds(author_pause_after_seconds)
    if pause <= 0:
        return body
    tag = map_author_pause_seconds_to_v3_tag(pause)
    if not tag:
        return body
    # Bereits am Ende vorhanden (z. B. Inline-Marker am Segmentende) → nicht doppelt.
    if body.rstrip().endswith(tag):
        return body
    if not body:
        return tag
    return f"{body} {tag}"


def build_chapter_tts_text(
    segments: list[object],
    *,
    model_id: str,
) -> tuple[str, list[tuple[str, str]]]:
    """Baut den Kapitel-TTS-Text für **einen** ElevenLabs-Call.

    Returns:
        full_tts_text: gesamter an ElevenLabs gesendeter Text inkl. v3-Pause-Tags
        align_parts: ``(segment_id, spoken_body)`` ohne trailing Pause-Tag —
            für Character-Timestamp-Alignment gegen ``full_tts_text``
    """
    pieces: list[str] = []
    align_parts: list[tuple[str, str]] = []
    for segment in segments:
        segment_id = str(getattr(segment, "segment_id", "") or "").strip()
        raw_text = str(getattr(segment, "text", "") or "")
        pause_after = float(
            getattr(segment, "author_pause_after_seconds", 0.0) or 0.0
        )
        body = replace_timed_pause_markers_for_tts(raw_text, model_id=model_id)
        if not body.strip() or not segment_id:
            continue
        full_piece = build_segment_tts_text(
            text=raw_text,
            author_pause_after_seconds=pause_after,
            model_id=model_id,
        )
        if not full_piece.strip():
            continue
        pieces.append(full_piece.strip())
        align_parts.append((segment_id, body.strip()))
    if not pieces:
        return "", []
    return " ".join(pieces), align_parts
