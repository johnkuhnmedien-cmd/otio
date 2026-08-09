"""Kapiteltext: gesprochene Segmente vs. sichtbare Autorenpausen-Marker."""

from __future__ import annotations

import math
import re

from otio_app.project_layout import safe_folder_slug
from otio_app.services.without_voiceover_enhanced.models import (
    EnhancedScriptDocument,
    ScriptSegment,
)

__all__ = [
    "MAX_AUTHOR_PAUSE_SECONDS",
    "AUTHOR_PAUSE_MARKER_RE",
    "ChapterDisplayTextError",
    "normalize_author_pause_seconds",
    "format_author_pause_marker",
    "strip_author_pause_markers_from_text",
    "join_spoken_segment_texts",
    "chapter_display_text",
    "parse_chapter_display_text",
    "migrate_inline_pause_markers_in_segment",
    "flatten_folder_segments_to_pause_blocks",
    "canonicalize_script_document_to_pause_blocks",
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


def strip_author_pause_markers_from_text(text: str) -> str:
    """Entfernt `[pause N seconds]` aus einem Textblock (für narration_full)."""
    cleaned = AUTHOR_PAUSE_MARKER_RE.sub(" ", text or "")
    # Marker-Zeilen und doppelte Leerzeichen glätten, Absätze erhalten.
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n[ \t]+", "\n", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def join_spoken_segment_texts(segments: list[ScriptSegment]) -> str:
    """Nur gesprochener Text — ohne Pausemarker, mit optionalen Absatzgrenzen."""
    nonempty = [seg for seg in segments if (seg.text or "").strip()]
    if not nonempty:
        return ""

    def _spoken(seg: ScriptSegment) -> str:
        return strip_author_pause_markers_from_text(seg.text or "")

    first = _spoken(nonempty[0])
    chunks: list[str] = [first] if first else []
    for prev, nxt in zip(nonempty, nonempty[1:]):
        spoken = _spoken(nxt)
        if not spoken:
            continue
        if not chunks:
            chunks.append(spoken)
            continue
        sep = "\n\n" if bool(getattr(prev, "paragraph_break_after", False)) else " "
        chunks.append(sep)
        chunks.append(spoken)
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


def migrate_inline_pause_markers_in_segment(
    segment: ScriptSegment,
) -> list[ScriptSegment]:
    """Wandelt zeilenweise `[pause N seconds]` in author_pause_after_seconds um.

    Schlägt die Darstellung fehl (Marker mitten im Satz), bleibt das Segment
    unverändert — die Marker gehen dann über den TTS-Text-Builder an eleven_v3.
    """
    text = segment.text or ""
    if not AUTHOR_PAUSE_MARKER_RE.search(text):
        return [segment]
    prefix = segment.segment_id.rsplit("_", 1)[0] if "_" in segment.segment_id else segment.segment_id
    try:
        parsed = parse_chapter_display_text(
            text,
            folder_name=segment.folder_name,
            folder_order_index=segment.folder_order_index,
            segment_id_prefix=prefix or "segment",
            default_semantic_function=segment.semantic_function or "narration",
        )
    except ChapterDisplayTextError:
        return [segment]

    migrated: list[ScriptSegment] = []
    for index, item in enumerate(parsed):
        segment_id = segment.segment_id if index == 0 else f"{prefix}_{index + 1:03d}"
        pause = normalize_author_pause_seconds(item.author_pause_after_seconds)
        migrated.append(
            segment.model_copy(
                update={
                    "segment_id": segment_id,
                    "text": item.text,
                    "sequence_index": item.sequence_index,
                    "author_pause_after_seconds": pause,
                    "paragraph_break_after": bool(
                        item.paragraph_break_after or pause > 0
                    ),
                }
            )
        )
    return migrated or [segment]


def _join_pause_block_text(group: list[ScriptSegment]) -> str:
    """Joins fine-grained segments the same way ``chapter_display_text`` would."""
    parts: list[str] = []
    nonempty = [seg for seg in group if (seg.text or "").strip()]
    for index, segment in enumerate(nonempty):
        parts.append((segment.text or "").strip())
        if index < len(nonempty) - 1 and bool(segment.paragraph_break_after):
            parts.append("")
    return "\n".join(parts).strip()


def flatten_folder_segments_to_pause_blocks(
    segments: list[ScriptSegment],
    *,
    folder_name: str,
    segment_id_prefix: str,
) -> tuple[list[ScriptSegment], dict[str, str]]:
    """Collapse LLM sentence segments into pause-delimited chapter blocks.

    Canonical chapter storage is spoken text + author pauses. Consecutive
    segments without ``author_pause_after_seconds`` become one block. Cut
    planning still uses ElevenLabs sentence/word timestamps after chapter TTS.
    """
    segs = [seg for seg in segments if (seg.text or "").strip()]
    if not segs:
        return [], {}

    # Inline markers (e.g. intro hooks) stay on the eleven_v3 path.
    if any(AUTHOR_PAUSE_MARKER_RE.search(seg.text or "") for seg in segs):
        return list(segs), {seg.segment_id: seg.segment_id for seg in segs}

    groups: list[list[ScriptSegment]] = []
    current: list[ScriptSegment] = []
    for segment in segs:
        current.append(segment)
        if float(segment.author_pause_after_seconds or 0.0) > 0:
            groups.append(current)
            current = []
    if current:
        groups.append(current)

    order_index = segs[0].folder_order_index
    default_semantic = segs[0].semantic_function or "narration"
    flattened: list[ScriptSegment] = []
    id_map: dict[str, str] = {}

    used_ids: set[str] = set()
    for index, group in enumerate(groups, start=1):
        pause = normalize_author_pause_seconds(
            group[-1].author_pause_after_seconds if group else 0.0
        )
        intent_ids: list[str] = []
        for segment in group:
            for intent_id in segment.visual_intent_ids:
                if intent_id not in intent_ids:
                    intent_ids.append(intent_id)
        text = _join_pause_block_text(group)
        # Prefer the first segment id (keeps Intro-/LLM-IDs stable); fall back
        # to a deterministic prefix only on collision.
        candidate = (group[0].segment_id or "").strip()
        if not candidate or candidate in used_ids:
            candidate = f"{segment_id_prefix}_{index:03d}"
            suffix = index
            while candidate in used_ids:
                suffix += 1
                candidate = f"{segment_id_prefix}_{suffix:03d}"
        segment_id = candidate
        used_ids.add(segment_id)

        if (
            len(group) == 1
            and (group[0].text or "").strip() == text
            and normalize_author_pause_seconds(group[0].author_pause_after_seconds)
            == pause
        ):
            block = group[0].model_copy(
                update={
                    "segment_id": segment_id,
                    "text": text,
                    "sequence_index": index,
                    "folder_name": folder_name,
                    "folder_order_index": order_index,
                    "visual_intent_ids": intent_ids or list(group[0].visual_intent_ids),
                    "fact_check_required": bool(group[0].fact_check_required),
                    "author_pause_after_seconds": pause,
                    "paragraph_break_after": pause > 0
                    or bool(group[0].paragraph_break_after),
                }
            )
        else:
            block = ScriptSegment(
                segment_id=segment_id,
                text=text,
                sequence_index=index,
                semantic_function=group[0].semantic_function or default_semantic,
                visual_intent_ids=intent_ids,
                fact_check_required=any(seg.fact_check_required for seg in group),
                text_changed=any(seg.text_changed for seg in group),
                folder_name=folder_name,
                folder_order_index=order_index,
                paragraph_break_after=pause > 0,
                author_pause_after_seconds=pause,
            )
        flattened.append(block)
        for segment in group:
            id_map[segment.segment_id] = segment_id

    return flattened, id_map


def canonicalize_script_document_to_pause_blocks(
    document: EnhancedScriptDocument,
) -> dict[str, str]:
    """Normalize a script to pause-delimited chapter blocks (in place).

    Returns mapping ``old_segment_id -> new_segment_id``.
    """
    migrated: list[ScriptSegment] = []
    for segment in document.segments:
        migrated.extend(migrate_inline_pause_markers_in_segment(segment))

    folder_order: list[str] = []
    by_folder: dict[str, list[ScriptSegment]] = {}
    for segment in migrated:
        key = segment.folder_name or ""
        if key not in by_folder:
            folder_order.append(key)
            by_folder[key] = []
        by_folder[key].append(segment)

    new_segments: list[ScriptSegment] = []
    id_map: dict[str, str] = {}
    for key in folder_order:
        folder_segs = by_folder[key]
        prefix = f"{safe_folder_slug(key)}_segment" if key else "segment"
        flat, mapping = flatten_folder_segments_to_pause_blocks(
            folder_segs,
            folder_name=key,
            segment_id_prefix=prefix,
        )
        new_segments.extend(flat)
        id_map.update(mapping)

    for index, segment in enumerate(new_segments, start=1):
        segment.sequence_index = index

    for beat in document.visual_beats:
        remapped: list[str] = []
        for segment_id in beat.related_segment_ids:
            mapped = id_map.get(segment_id, segment_id)
            if mapped and mapped not in remapped:
                remapped.append(mapped)
        beat.related_segment_ids = remapped

    for hint in document.fact_check_hints:
        if hint.related_segment_id in id_map:
            hint.related_segment_id = id_map[hint.related_segment_id]

    document.segments = new_segments
    document.narration_full = join_spoken_segment_texts(document.segments)
    return id_map
