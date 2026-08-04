"""Kapiteltext: gesprochene Segmente vs. sichtbare Autorenpausen-Marker."""

from __future__ import annotations

import math
import re

from otio_app.services.without_voiceover_enhanced.models import ScriptSegment

__all__ = [
    "MAX_AUTHOR_PAUSE_SECONDS",
    "AUTHOR_PAUSE_MARKER_RE",
    "ChapterDisplayTextError",
    "normalize_author_pause_seconds",
    "format_author_pause_marker",
    "join_spoken_segment_texts",
    "chapter_display_text",
    "parse_chapter_display_text",
]

MAX_AUTHOR_PAUSE_SECONDS = 8.0

AUTHOR_PAUSE_MARKER_RE = re.compile(
    r"\[\s*pause\s+"
    r"(?P<seconds>\d+(?:[.,]\d+)?)\s*"
    r"(?:seconds?|sekunden?|secs?|s)?\s*\]",
    re.IGNORECASE,
)


class ChapterDisplayTextError(ValueError):
    """Ungültige sichtbare Kapiteltext-/Pausenstruktur."""


def normalize_author_pause_seconds(value: object) -> float:
    """Normalisiert Autorenpause auf 0..8 mit max. zwei Nachkommastellen."""
    if value is None or value is False:
        return 0.0
    if isinstance(value, bool):
        raise ChapterDisplayTextError("Autorenpause darf kein Boolean sein.")
    if isinstance(value, str):
        text = value.strip().replace(",", ".")
        if not text:
            return 0.0
        try:
            number = float(text)
        except ValueError as exc:
            raise ChapterDisplayTextError(
                f"Ungültige Autorenpause: {value!r}"
            ) from exc
    else:
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ChapterDisplayTextError(
                f"Ungültige Autorenpause: {value!r}"
            ) from exc
    if not math.isfinite(number):
        raise ChapterDisplayTextError("Autorenpause muss endlich sein.")
    if number < 0:
        raise ChapterDisplayTextError("Autorenpause darf nicht negativ sein.")
    if number > MAX_AUTHOR_PAUSE_SECONDS + 1e-9:
        raise ChapterDisplayTextError(
            f"Autorenpause maximal {MAX_AUTHOR_PAUSE_SECONDS:g} Sekunden."
        )
    return round(number, 2)


def format_author_pause_marker(seconds: float) -> str:
    value = normalize_author_pause_seconds(seconds)
    if value <= 0:
        return ""
    if abs(value - round(value)) < 1e-9:
        return f"[pause {int(round(value))} seconds]"
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return f"[pause {text} seconds]"


def join_spoken_segment_texts(segments: list[ScriptSegment]) -> str:
    """Nur gesprochener Text — ohne Pausemarker, mit optionalen Absatzgrenzen."""
    nonempty = [seg for seg in segments if (seg.text or "").strip()]
    if not nonempty:
        return ""
    chunks: list[str] = [nonempty[0].text.strip()]
    for prev, nxt in zip(nonempty, nonempty[1:]):
        sep = "\n\n" if bool(getattr(prev, "paragraph_break_after", False)) else " "
        chunks.append(sep)
        chunks.append(nxt.text.strip())
    return "".join(chunks)


def chapter_display_text(segments: list[ScriptSegment]) -> str:
    """Sichtbare Kapitelansicht inkl. [pause X seconds]-Marker."""
    parts: list[str] = []
    for segment in segments:
        text = (segment.text or "").strip()
        if not text:
            continue
        parts.append(text)
        pause = normalize_author_pause_seconds(
            getattr(segment, "author_pause_after_seconds", 0.0)
        )
        if pause > 0:
            parts.append("")
            parts.append(format_author_pause_marker(pause))
            parts.append("")
        elif bool(getattr(segment, "paragraph_break_after", False)):
            parts.append("")
    # Trim trailing blank lines from final pause marker padding
    while parts and parts[-1] == "":
        parts.pop()
    return "\n".join(parts)


def parse_chapter_display_text(
    text: str,
    *,
    folder_name: str,
    folder_order_index: int = 0,
    segment_id_prefix: str = "segment",
    default_semantic_function: str = "narration",
) -> list[ScriptSegment]:
    """Parst sichtbaren Kapiteltext inkl. Autorenpausen zu Segmenten."""
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        raise ChapterDisplayTextError("Kapitel-Text darf nicht leer sein.")

    segments: list[ScriptSegment] = []
    buffer: list[str] = []

    def _flush(*, pause_after: float = 0.0) -> None:
        body = "\n".join(buffer).strip()
        buffer.clear()
        if not body:
            if pause_after > 0:
                raise ChapterDisplayTextError(
                    "Pausenmarker ohne vorausgehenden Textblock."
                )
            return
        pause = normalize_author_pause_seconds(pause_after)
        index = len(segments) + 1
        segments.append(
            ScriptSegment(
                segment_id=f"{segment_id_prefix}_{index:03d}",
                text=body,
                sequence_index=index,
                semantic_function=default_semantic_function,
                folder_name=folder_name,
                folder_order_index=folder_order_index,
                paragraph_break_after=pause > 0,
                author_pause_after_seconds=pause,
                text_changed=True,
            )
        )

    lines = raw.split("\n")
    for line in lines:
        match = AUTHOR_PAUSE_MARKER_RE.fullmatch(line.strip())
        if match:
            seconds = normalize_author_pause_seconds(
                match.group("seconds").replace(",", ".")
            )
            if not buffer and not segments:
                raise ChapterDisplayTextError(
                    "Pausenmarker vor dem ersten Textblock ist nicht erlaubt."
                )
            if not any(part.strip() for part in buffer) and segments:
                # Marker unmittelbar nach vorherigem Flush (leere Zeilen) —
                # Pause gehört zum letzten Segment.
                last = segments[-1]
                if float(last.author_pause_after_seconds or 0.0) > 0:
                    raise ChapterDisplayTextError(
                        "Zwei Pausenmarker direkt hintereinander sind nicht erlaubt."
                    )
                segments[-1] = last.model_copy(
                    update={
                        "author_pause_after_seconds": seconds,
                        "paragraph_break_after": True,
                    }
                )
                continue
            _flush(pause_after=seconds)
            continue
        buffer.append(line)

    if any(part.strip() for part in buffer):
        _flush(pause_after=0.0)

    if not segments:
        raise ChapterDisplayTextError("Kapitel-Text enthält keinen sprechbaren Inhalt.")

    # Marker dürfen nicht im gesprochenen Text landen.
    for segment in segments:
        if AUTHOR_PAUSE_MARKER_RE.search(segment.text):
            raise ChapterDisplayTextError(
                "Pausenmarker müssen allein auf einer Zeile stehen und "
                "dürfen nicht im Fließtext liegen."
            )
    return segments
