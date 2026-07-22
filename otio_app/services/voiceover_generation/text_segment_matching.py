"""Gemeinsame Text-Segment-Suche für Alignment UND TTS-Pause-Einfügung.

Beide Anwendungsfälle müssen dieselbe Segment-Grenze finden: das Alignment
sucht sentence_items-Text in den von ElevenLabs zurückgelieferten Character-
Timestamps, die Pause-Einfügung sucht dieselben Grenzen im Text, der VOR dem
TTS-Call gesendet wird. Eine gemeinsame, einmal getestete Implementierung
verhindert, dass beide Stellen unterschiedliche Grenzen "sehen".
"""

from __future__ import annotations

__all__ = [
    "build_normalized_index_map",
    "find_segment_span",
]


def build_normalized_index_map(text: str) -> tuple[str, list[int]]:
    """Baut eine kompakte, case-/whitespace-/punctuation-normalisierte Version
    von `text` plus eine Rückabbildung jedes behaltenen Zeichens auf seinen
    ORIGINAL-Index — ElevenLabs' Character-Timestamps sind exakt an die
    Originalzeichen von `text` gekoppelt, deshalb muss die Rückabbildung
    exakt sein, auch wenn für den Vergleich Satzzeichen ignoriert werden."""
    normalized_chars: list[str] = []
    index_map: list[int] = []
    previous_was_space = False
    for index, ch in enumerate(text):
        if ch.isspace():
            if normalized_chars and not previous_was_space:
                normalized_chars.append(" ")
                index_map.append(index)
            previous_was_space = True
            continue
        if not ch.isalnum():
            previous_was_space = False
            continue
        previous_was_space = False
        normalized_chars.append(ch.lower())
        index_map.append(index)
    while normalized_chars and normalized_chars[0] == " ":
        normalized_chars.pop(0)
        index_map.pop(0)
    while normalized_chars and normalized_chars[-1] == " ":
        normalized_chars.pop()
        index_map.pop()
    return "".join(normalized_chars), index_map


def find_segment_span(
    normalized_full: str,
    index_map: list[int],
    normalized_segment: str,
    *,
    search_from: int,
) -> tuple[int, int, int] | None:
    """Sucht normalized_segment in normalized_full ab Position search_from.

    Gibt (original_start_index, original_end_index_exclusive, neue
    normalisierte Cursor-Position) zurück, oder None wenn nicht gefunden."""
    if not normalized_segment:
        return None
    position = normalized_full.find(normalized_segment, search_from)
    if position == -1:
        return None
    start = index_map[position]
    end_normalized_index = position + len(normalized_segment) - 1
    end = index_map[end_normalized_index] + 1
    return start, end, position + len(normalized_segment)
