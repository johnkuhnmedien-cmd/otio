"""Deterministische Python-Auflösung von Pause Directives."""

from __future__ import annotations

from otio_app.services.without_voiceover_enhanced.models import (
    IntraPauseMarker,
    NarrationTimelineDocument,
    NarrationTimelineEntry,
    PauseDirective,
    SegmentTiming,
    SentenceTiming,
)
from otio_app.services.without_voiceover_enhanced.pause_config import (
    resolve_pause_duration_seconds,
)


class PauseResolveError(ValueError):
    pass


def mid_silence_split_seconds(
    *,
    sentence: SentenceTiming,
    next_sentence: SentenceTiming | None,
    audio_duration_seconds: float,
) -> float:
    """Mitte der Original-Stille nach ``sentence`` (Segment-relativ)."""
    silence_start = max(0.0, float(sentence.end_seconds))
    if next_sentence is not None:
        silence_end = max(silence_start, float(next_sentence.start_seconds))
    else:
        silence_end = max(silence_start, float(audio_duration_seconds))
    return round((silence_start + silence_end) / 2.0, 6)


def source_seconds_to_timeline(
    entry: NarrationTimelineEntry,
    source_seconds: float,
) -> float:
    """Segment-lokale Audiozeit → absolute Timeline (inkl. Intra-Pausen)."""
    audio_dur = entry.audio_duration_seconds
    if audio_dur is None:
        # Legacy: end-start war die Audio-Spanne ohne Intra-Pausen.
        audio_dur = max(0.0, float(entry.end_seconds) - float(entry.start_seconds))
        for pause in entry.intra_pauses:
            audio_dur = max(0.0, audio_dur - float(pause.pause_seconds))
    local = max(0.0, min(float(source_seconds), float(audio_dur)))
    shift = 0.0
    for pause in sorted(entry.intra_pauses, key=lambda p: p.source_split_seconds):
        if local >= float(pause.source_split_seconds) - 1e-9:
            shift += float(pause.pause_seconds)
        else:
            break
    return round(float(entry.start_seconds) + local + shift, 6)


def _sentences_for_segment(
    sentence_index: dict[str, SentenceTiming],
    segment_id: str,
) -> list[SentenceTiming]:
    rows = [
        sentence
        for sentence in sentence_index.values()
        if sentence.segment_id == segment_id
    ]
    return sorted(rows, key=lambda item: (item.start_seconds, item.sentence_id))


def _build_intra_pauses(
    *,
    segment_id: str,
    audio_duration_seconds: float,
    sentence_directives: list[PauseDirective],
    sentence_index: dict[str, SentenceTiming],
) -> tuple[list[IntraPauseMarker], float]:
    """Intra-Pausen + trailing Pause nach dem letzten Satz (kein Split).

    Returns:
        (markers, trailing_pause_seconds) — trailing wird als Segment-
        ``pause_after`` behandelt (E2E-3: Pause nach letztem Satz ≠ Split).
    """
    if not sentence_directives:
        return [], 0.0
    sentences = _sentences_for_segment(sentence_index, segment_id)
    by_id = {item.sentence_id: item for item in sentences}
    markers: list[IntraPauseMarker] = []
    trailing_pause = 0.0
    seen: set[str] = set()
    for directive in sentence_directives:
        sentence_id = str(directive.after_sentence_id or "").strip()
        if not sentence_id or sentence_id in seen:
            continue
        sentence = by_id.get(sentence_id)
        if sentence is None:
            raise PauseResolveError(
                f"Unbekannte after_sentence_id für Pause: {sentence_id}"
            )
        # Nächster Satz im selben Segment (sonst Stille bis Audioende).
        next_sentence = None
        for candidate in sentences:
            if candidate.start_seconds > sentence.end_seconds + 1e-9:
                next_sentence = candidate
                break
        pause_seconds = resolve_pause_duration_seconds(
            directive.duration_class,
            pause_function=directive.pause_function,
        )
        if pause_seconds <= 0:
            continue
        seen.add(sentence_id)
        # E2E-3: Pause nach LETZTEM Satz → Gap nach Segmentende, kein Split.
        if next_sentence is None:
            trailing_pause = max(trailing_pause, float(pause_seconds))
            continue
        split = mid_silence_split_seconds(
            sentence=sentence,
            next_sentence=next_sentence,
            audio_duration_seconds=audio_duration_seconds,
        )
        markers.append(
            IntraPauseMarker(
                after_sentence_id=sentence_id,
                source_split_seconds=split,
                pause_seconds=round(pause_seconds, 6),
            )
        )
    markers.sort(key=lambda item: item.source_split_seconds)
    return markers, round(trailing_pause, 6)


def build_narration_timeline(
    *,
    script_version: str,
    segment_timings: list[SegmentTiming],
    pause_directives: list[PauseDirective],
    sentence_index: dict[str, SentenceTiming] | None = None,
) -> NarrationTimelineDocument:
    """Ende Voice-Segment + aufgelöste Pause = Beginn nächstes Segment.

    Pause-Directives sind deaktiviert (Intro + Kapitel): Eingaben werden
    ignoriert — keine Intra-Pausen, kein ``pause_after``. Schema/Parameter
    bleiben für API-Kompatibilität erhalten.
    """
    del pause_directives, sentence_index  # Pausen bewusst abgeschaltet.
    if not segment_timings:
        raise PauseResolveError("Keine Segment-Timings vorhanden.")

    ordered = list(segment_timings)
    entries: list[NarrationTimelineEntry] = []
    cursor = 0.0
    for index, timing in enumerate(ordered):
        if timing.audio_status != "valid":
            raise PauseResolveError(
                f"Segment {timing.segment_id} hat audio_status={timing.audio_status}"
            )
        if timing.script_version != script_version:
            raise PauseResolveError(
                f"Skriptversion passt nicht zur Audiodatei für {timing.segment_id}: "
                f"{timing.script_version} != {script_version}"
            )
        audio_duration = float(timing.duration_seconds)
        start = cursor
        end = start + audio_duration
        next_start = end
        entries.append(
            NarrationTimelineEntry(
                segment_id=timing.segment_id,
                start_seconds=round(start, 6),
                end_seconds=round(end, 6),
                pause_after_seconds=0.0,
                next_segment_start_seconds=(
                    round(next_start, 6) if index < len(ordered) - 1 else None
                ),
                audio_duration_seconds=round(audio_duration, 6),
                intra_pauses=[],
            )
        )
        cursor = next_start

    total = entries[-1].end_seconds
    return NarrationTimelineDocument(
        script_version=script_version,
        total_duration_seconds=round(total, 6),
        entries=entries,
    )
