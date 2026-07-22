"""Slim Sentence-Timings für LLM-Lauf 2/3 aus segment_alignments.json."""

from __future__ import annotations

import json
from typing import Any

from otio_app.models import Project
from otio_app.services.without_voiceover_enhanced.models import (
    SegmentAlignmentsDocument,
    SentenceTiming,
)
from otio_app.services.without_voiceover_enhanced.segment_alignment_service import (
    load_segment_alignments,
)

__all__ = [
    "build_sentence_timings_json_for_segments",
    "sentence_index_by_id",
    "slim_sentence_row",
]


def slim_sentence_row(sentence: SentenceTiming) -> dict[str, Any]:
    return {
        "sentence_id": sentence.sentence_id,
        "segment_id": sentence.segment_id,
        "text": sentence.text,
        "start_seconds": round(float(sentence.start_seconds), 3),
        "end_seconds": round(float(sentence.end_seconds), 3),
    }


def build_sentence_timings_json_for_segments(
    project: Project,
    *,
    segment_ids: list[str] | set[str],
    indent: int = 2,
) -> str:
    """Kompakte Satzliste für Prompt (~id/text/start/end, segmentrelativ)."""
    wanted = set(segment_ids)
    doc = load_segment_alignments(project)
    rows: list[dict[str, Any]] = []
    if doc is not None:
        for alignment in doc.segments:
            if alignment.segment_id not in wanted:
                continue
            for sentence in alignment.sentences:
                rows.append(slim_sentence_row(sentence))
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
