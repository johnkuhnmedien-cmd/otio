"""Slim Sentence- + Word-Timings für Unified-Cut-LLM aus ElevenLabs-Daten."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from otio_app.models import Project
from otio_app.services.without_voiceover_enhanced.models import (
    SegmentAlignmentsDocument,
    SentenceTiming,
)
from otio_app.services.without_voiceover_enhanced.paths import segment_timestamps_path
from otio_app.services.without_voiceover_enhanced.segment_alignment_service import (
    load_segment_alignments,
)

__all__ = [
    "attach_words_to_sentence_row",
    "build_sentence_timings_json_for_segments",
    "load_elevenlabs_alignment_for_segment",
    "sentence_index_by_id",
    "slim_sentence_row",
    "words_from_elevenlabs_alignment",
]


def slim_sentence_row(sentence: SentenceTiming) -> dict[str, Any]:
    return {
        "sentence_id": sentence.sentence_id,
        "segment_id": sentence.segment_id,
        "text": sentence.text,
        "start_seconds": round(float(sentence.start_seconds), 3),
        "end_seconds": round(float(sentence.end_seconds), 3),
    }


def load_elevenlabs_alignment_for_segment(
    project: Project, segment_id: str
) -> dict[str, Any]:
    """Roh-Alignment aus ``elevenlabs_timestamps.json`` (raw bevorzugt)."""
    path = segment_timestamps_path(project, segment_id)
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    alignment = payload.get("alignment") or {}
    if not isinstance(alignment, dict):
        alignment = {}
    starts = alignment.get("character_start_times_seconds") or []
    if starts:
        return alignment
    normalized = payload.get("normalized_alignment") or {}
    return normalized if isinstance(normalized, dict) else {}


def words_from_elevenlabs_alignment(alignment: dict[str, Any]) -> list[dict[str, Any]]:
    """Character-Timestamps → Wörter (Segment-relativ)."""
    chars = alignment.get("characters") or []
    starts = alignment.get("character_start_times_seconds") or []
    ends = alignment.get("character_end_times_seconds") or []
    n = min(len(chars), len(starts), len(ends))
    if n <= 0:
        return []

    words: list[dict[str, Any]] = []
    buf_chars: list[str] = []
    buf_start: float | None = None
    buf_end: float | None = None

    def _flush() -> None:
        nonlocal buf_chars, buf_start, buf_end
        text = "".join(buf_chars).strip()
        if text and buf_start is not None and buf_end is not None:
            words.append(
                {
                    "text": text,
                    "start_seconds": round(float(buf_start), 3),
                    "end_seconds": round(float(buf_end), 3),
                }
            )
        buf_chars = []
        buf_start = None
        buf_end = None

    for index in range(n):
        ch = str(chars[index])
        start = float(starts[index])
        end = float(ends[index])
        if ch.isspace():
            _flush()
            continue
        if buf_start is None:
            buf_start = start
        buf_chars.append(ch)
        buf_end = end
    _flush()
    return words


def attach_words_to_sentence_row(
    sentence_row: dict[str, Any],
    words: list[dict[str, Any]],
) -> dict[str, Any]:
    """Hängt Wörter an einen Sentence-Row (offset relativ zum Satzanfang)."""
    sent_start = float(sentence_row.get("start_seconds") or 0.0)
    sent_end = float(sentence_row.get("end_seconds") or sent_start)
    attached: list[dict[str, Any]] = []
    for word in words:
        w_start = float(word.get("start_seconds") or 0.0)
        w_end = float(word.get("end_seconds") or w_start)
        # Zugehörigkeit: Wortstart im Satzfenster (inkl. kleine Toleranz).
        if w_start < sent_start - 0.05:
            continue
        if w_start > sent_end + 0.05 and attached:
            # Nach Satzende: nur noch zuordnen wenn noch keine Wörter (Fallback).
            break
        if w_start > sent_end + 0.05:
            continue
        attached.append(
            {
                "text": str(word.get("text") or ""),
                "start_seconds": round(w_start, 3),
                "end_seconds": round(w_end, 3),
                "offset_seconds": round(max(0.0, w_start - sent_start), 3),
            }
        )
    out = dict(sentence_row)
    out["words"] = attached
    return out


def build_sentence_timings_json_for_segments(
    project: Project,
    *,
    segment_ids: list[str] | set[str],
    indent: int = 2,
    include_words: bool = True,
) -> str:
    """Kompakte Satzliste für Prompt; optional mit Word-Onsets aus ElevenLabs."""
    wanted = set(segment_ids)
    doc = load_segment_alignments(project)
    rows: list[dict[str, Any]] = []
    if doc is not None:
        for alignment in doc.segments:
            if alignment.segment_id not in wanted:
                continue
            words: list[dict[str, Any]] = []
            if include_words:
                raw = load_elevenlabs_alignment_for_segment(
                    project, alignment.segment_id
                )
                words = words_from_elevenlabs_alignment(raw)
            for sentence in alignment.sentences:
                row = slim_sentence_row(sentence)
                if include_words and words:
                    row = attach_words_to_sentence_row(row, words)
                elif include_words:
                    row["words"] = []
                rows.append(row)
    return json.dumps(rows, ensure_ascii=False, indent=indent)


def sentence_index_by_id(
    alignments: SegmentAlignmentsDocument | None,
) -> dict[str, SentenceTiming]:
    if alignments is None:
        return {}
    out: dict[str, SentenceTiming] = {}
    for alignment in alignments.segments:
        for sentence in alignment.sentences:
            out[sentence.sentence_id] = sentence
    return out
