"""ElevenLabs-Timestamps speichern und Satzzeiten pro Segment ableiten.

Rohdaten landen unter ``audio/alignments/{segment_id}/``; der aggregierte
Index unter ``audio/segment_alignments.json`` für spätere Cut-/LLM-Nutzung.
"""

from __future__ import annotations

import json
import re
from typing import Any

from otio_app.models import Project
from otio_app.services.voiceover_generation.audio_alignment_service import (
    _align_segments,
)
from otio_app.services.without_voiceover_enhanced.io_utils import load_model, write_json
from otio_app.services.without_voiceover_enhanced.models import (
    SegmentAlignment,
    SegmentAlignmentsDocument,
    SentenceTiming,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    alignments_dir,
    segment_alignment_dir,
    segment_alignments_path,
    segment_sentence_alignment_path,
    segment_timestamps_path,
    segment_tts_metadata_path,
)

__all__ = [
    "build_segment_alignment",
    "load_segment_alignments",
    "persist_segment_tts_alignment",
    "rebuild_segment_alignments_index",
    "split_segment_into_sentences",
]

# Satzgrenzen: Punkt/Frage/Ausruf/Ellipse, danach Whitespace.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+")


def split_segment_into_sentences(text: str) -> list[str]:
    """Teilt Segmenttext in Satz-/Beat-Chunks für Alignment."""
    cleaned = (text or "").strip()
    if not cleaned:
        return []
    parts = [part.strip() for part in _SENTENCE_SPLIT_RE.split(cleaned) if part.strip()]
    return parts or [cleaned]


def build_segment_alignment(
    *,
    segment_id: str,
    script_version: str,
    audio_path: str,
    audio_duration_seconds: float,
    tts_text: str,
    timestamps_path: str,
    elevenlabs_alignment: dict[str, Any],
) -> SegmentAlignment:
    """Mappt Character-Timestamps auf abgeleitete Sätze innerhalb des Segments."""
    sentences_text = split_segment_into_sentences(tts_text)
    sentence_ids = [
        f"{segment_id}__s{index:03d}" for index in range(1, len(sentences_text) + 1)
    ]
    segments = list(zip(sentence_ids, sentences_text, strict=True))
    times_by_id, warnings = _align_segments(
        tts_text, segments, elevenlabs_alignment or {}
    )

    sentences: list[SentenceTiming] = []
    for sentence_id, sentence_text in segments:
        start_sec, end_sec = times_by_id.get(sentence_id, (0.0, 0.0))
        start_sec = float(start_sec)
        end_sec = float(end_sec)
        if end_sec < start_sec:
            end_sec = start_sec
        sentences.append(
            SentenceTiming(
                sentence_id=sentence_id,
                segment_id=segment_id,
                text=sentence_text,
                start_seconds=round(start_sec, 3),
                end_seconds=round(end_sec, 3),
                duration_seconds=round(max(0.0, end_sec - start_sec), 3),
            )
        )

    if audio_duration_seconds and sentences:
        last_end = max((item.end_seconds for item in sentences), default=0.0)
        if abs(float(audio_duration_seconds) - last_end) > 1.0:
            warnings.append("audio_duration_mismatch")

    return SegmentAlignment(
        segment_id=segment_id,
        script_version=script_version,
        audio_path=audio_path,
        audio_duration_seconds=float(audio_duration_seconds),
        tts_text=tts_text,
        timestamps_path=timestamps_path,
        sentences=sentences,
        alignment_warnings=list(warnings),
    )


def persist_segment_tts_alignment(
    project: Project,
    *,
    segment_id: str,
    script_version: str,
    audio_path: str,
    audio_duration_seconds: float,
    tts_text: str,
    alignment: dict[str, Any],
    normalized_alignment: dict[str, Any],
    response_metadata: dict[str, Any] | None = None,
) -> SegmentAlignment:
    """Schreibt Roh-Timestamps + Satz-Alignment für ein Segment."""
    out_dir = segment_alignment_dir(project, segment_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    timestamps_path = segment_timestamps_path(project, segment_id)
    timestamps_payload = {
        "alignment": alignment or {},
        "normalized_alignment": normalized_alignment or {},
    }
    timestamps_path.write_text(
        json.dumps(timestamps_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if response_metadata is not None:
        meta_path = segment_tts_metadata_path(project, segment_id)
        meta_path.write_text(
            json.dumps(response_metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # Prefer raw alignment; fall back to normalized if raw lacks character times.
    char_source = alignment or {}
    starts = char_source.get("character_start_times_seconds") or []
    if not starts and normalized_alignment:
        char_source = normalized_alignment

    segment_alignment = build_segment_alignment(
        segment_id=segment_id,
        script_version=script_version,
        audio_path=audio_path,
        audio_duration_seconds=audio_duration_seconds,
        tts_text=tts_text,
        timestamps_path=str(timestamps_path),
        elevenlabs_alignment=char_source,
    )
    write_json(segment_sentence_alignment_path(project, segment_id), segment_alignment)
    return segment_alignment


def load_segment_alignments(project: Project) -> SegmentAlignmentsDocument | None:
    return load_model(segment_alignments_path(project), SegmentAlignmentsDocument)


def rebuild_segment_alignments_index(
    project: Project,
    *,
    script_version: str,
    live_segment_ids: list[str],
) -> SegmentAlignmentsDocument:
    """Liest pro-Segment-Alignments, schreibt Index, entfernt verwaiste Ordner."""
    live_set = set(live_segment_ids)
    root = alignments_dir(project)
    by_id: dict[str, SegmentAlignment] = {}
    if root.is_dir():
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            segment_id = child.name
            if segment_id not in live_set:
                for path in child.glob("*"):
                    if path.is_file():
                        path.unlink()
                try:
                    child.rmdir()
                except OSError:
                    pass
                continue
            alignment_path = segment_sentence_alignment_path(project, segment_id)
            if not alignment_path.is_file():
                continue
            try:
                payload = json.loads(alignment_path.read_text(encoding="utf-8"))
                by_id[segment_id] = SegmentAlignment.model_validate(payload)
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
                continue

    order = {segment_id: index for index, segment_id in enumerate(live_segment_ids)}
    merged = sorted(by_id.values(), key=lambda item: order.get(item.segment_id, 10_000))
    document = SegmentAlignmentsDocument(
        script_version=script_version,
        segments=merged,
    )
    write_json(segment_alignments_path(project), document)
    return document
