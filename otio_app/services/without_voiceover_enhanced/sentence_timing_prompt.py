"""Slim Sentence- + Word-Timings für Unified-Cut-LLM aus ElevenLabs-Daten."""

from __future__ import annotations

import json
import re
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
    "clean_words_for_keyword_flow_prompt",
    "chapter_has_usable_keyword_flow_words",
    "keyword_flow_onset_offsets_for_sentence",
    "load_elevenlabs_alignment_for_segment",
    "sentence_index_by_id",
    "slim_sentence_row",
    "words_from_elevenlabs_alignment",
]

_DIRECTION_TAG_RE = re.compile(r"^\[[^\]]*\]?$")
_DASH_ONLY_RE = re.compile(r"^[\-–—…\.\,\;\:\!\?\']+$")
_PAUSE_TAG_PARTS = frozenset(
    {
        "[pause",
        "pause",
        "seconds]",
        "second]",
        "seconds",
        "second",
    }
)


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
    for word_index, word in enumerate(words):
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
                "original_word_index": int(
                    word.get("original_word_index", word_index)
                ),
            }
        )
    out = dict(sentence_row)
    out["words"] = attached
    return out


def is_direction_or_non_speech_token(text: str) -> bool:
    """True für Regie-Tags, Pause-Tag-Teile, Dash-only und reine Interpunktion."""
    raw = str(text or "").strip()
    if not raw:
        return True
    lowered = raw.lower()
    if _DIRECTION_TAG_RE.match(raw):
        return True
    if lowered in _PAUSE_TAG_PARTS:
        return True
    if lowered.startswith("[") and "pause" in lowered:
        return True
    if _DASH_ONLY_RE.match(raw):
        return True
    # Reine Zahl, die nur als Regie-Teil vorkommt (z. B. "2" in "[pause 2 seconds]").
    if re.fullmatch(r"\d+", raw):
        return True
    return False


def clean_words_for_keyword_flow_prompt(
    words: list[dict[str, Any]],
    *,
    sentence_id: str = "",
) -> list[dict[str, Any]]:
    """Bereinigte Prompt-Wortsicht; Zeiten unverändert, keine Schätzung."""
    cleaned: list[dict[str, Any]] = []
    for index, word in enumerate(words):
        text = str(word.get("text") or "")
        if is_direction_or_non_speech_token(text):
            continue
        original_index = int(word.get("original_word_index", index))
        entry = {
            "text": text,
            "start_seconds": float(word.get("start_seconds") or 0.0),
            "end_seconds": float(word.get("end_seconds") or 0.0),
            "offset_seconds": float(word.get("offset_seconds") or 0.0),
            "original_word_index": original_index,
        }
        if sentence_id:
            entry["sentence_id"] = sentence_id
            entry["word_ref"] = f"{sentence_id}#{original_index}"
        cleaned.append(entry)
    return cleaned


def keyword_flow_onset_offsets_for_sentence(
    sentence_row: dict[str, Any],
) -> set[float]:
    """Erlaubte Mid-Sentence-Offsets (exakte gelieferte Wort-Onsets)."""
    offsets: set[float] = set()
    for word in clean_words_for_keyword_flow_prompt(
        list(sentence_row.get("words") or []),
        sentence_id=str(sentence_row.get("sentence_id") or ""),
    ):
        offsets.add(round(float(word["offset_seconds"]), 3))
    return offsets


def chapter_has_usable_keyword_flow_words(rows: list[dict[str, Any]]) -> bool:
    """True wenn mindestens ein bereinigtes gesprochenes Wort vorhanden ist."""
    for row in rows:
        cleaned = clean_words_for_keyword_flow_prompt(
            list(row.get("words") or []),
            sentence_id=str(row.get("sentence_id") or ""),
        )
        if cleaned:
            return True
    return False


def build_sentence_timings_json_for_segments(
    project: Project,
    *,
    segment_ids: list[str] | set[str],
    indent: int = 2,
    include_words: bool = True,
    keyword_flow_clean: bool = False,
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
                segment_words = words_from_elevenlabs_alignment(raw)
                words = [
                    {**word, "original_word_index": index}
                    for index, word in enumerate(segment_words)
                ]
            for sentence in alignment.sentences:
                row = slim_sentence_row(sentence)
                if include_words and words:
                    row = attach_words_to_sentence_row(row, words)
                    if keyword_flow_clean:
                        row["words"] = clean_words_for_keyword_flow_prompt(
                            list(row.get("words") or []),
                            sentence_id=str(row.get("sentence_id") or ""),
                        )
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
