"""Keyword Flow Free: flat continuous word-flow input from ElevenLabs data."""

from __future__ import annotations

import json
from typing import Any

from otio_app.models import Project
from otio_app.services.without_voiceover_enhanced.sentence_timing_prompt import (
    build_sentence_timings_json_for_segments,
    chapter_has_usable_keyword_flow_words,
    clean_words_for_keyword_flow_prompt,
)

__all__ = [
    "build_continuous_word_flow",
    "build_continuous_word_flow_from_sentence_rows",
    "build_continuous_word_flow_json_for_segments",
    "chapter_has_usable_keyword_flow_free_words",
    "load_cleaned_sentence_rows_for_segments",
]


def build_continuous_word_flow_from_sentence_rows(
    sentence_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Flatten cleaned sentence words into one chronological word list.

    ``sentence_id`` remains only as a technical timing address for Python.
    Offsets stay sentence-relative and use real ElevenLabs onsets only.
    """
    flow: list[dict[str, Any]] = []
    for row in sentence_rows:
        sentence_id = str(row.get("sentence_id") or "").strip()
        words = list(row.get("words") or [])
        # Rows from keyword_flow_clean already cleaned; clean again is idempotent
        # for safety when callers pass raw attached words.
        cleaned = clean_words_for_keyword_flow_prompt(words, sentence_id=sentence_id)
        for word in cleaned:
            original_index = int(word.get("original_word_index", 0))
            word_ref = str(word.get("word_ref") or "").strip()
            if not word_ref and sentence_id:
                word_ref = f"{sentence_id}#{original_index}"
            flow.append(
                {
                    "word_ref": word_ref,
                    "text": str(word.get("text") or ""),
                    "sentence_id": sentence_id,
                    "offset_seconds": round(float(word.get("offset_seconds") or 0.0), 3),
                }
            )
    return flow


def build_continuous_word_flow(
    sentence_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Public alias for the flat chronological word representation."""
    return build_continuous_word_flow_from_sentence_rows(sentence_rows)


def load_cleaned_sentence_rows_for_segments(
    project: Project,
    *,
    segment_ids: list[str] | set[str],
) -> list[dict[str, Any]]:
    """Load nested sentence rows with cleaned real word onsets (technical only)."""
    raw = build_sentence_timings_json_for_segments(
        project,
        segment_ids=segment_ids,
        keyword_flow_clean=True,
    )
    rows = json.loads(raw or "[]")
    return rows if isinstance(rows, list) else []


def build_continuous_word_flow_json_for_segments(
    project: Project,
    *,
    segment_ids: list[str] | set[str],
    indent: int = 2,
) -> str:
    """JSON array of continuous words for the Keyword Flow Free prompt."""
    rows = load_cleaned_sentence_rows_for_segments(project, segment_ids=segment_ids)
    flow = build_continuous_word_flow_from_sentence_rows(rows)
    return json.dumps(flow, ensure_ascii=False, indent=indent)


def chapter_has_usable_keyword_flow_free_words(rows: list[dict[str, Any]]) -> bool:
    """True when at least one real cleaned spoken word exists."""
    return chapter_has_usable_keyword_flow_words(rows)
