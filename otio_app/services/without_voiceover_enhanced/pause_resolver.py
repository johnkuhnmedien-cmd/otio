"""Deterministische Python-Auflösung von Pause Directives."""

from __future__ import annotations

from otio_app.services.without_voiceover_enhanced.models import (
    NarrationTimelineDocument,
    NarrationTimelineEntry,
    PauseDirective,
    SegmentTiming,
)
from otio_app.services.without_voiceover_enhanced.pause_config import (
    resolve_pause_duration_seconds,
)


class PauseResolveError(ValueError):
    pass


def build_narration_timeline(
    *,
    script_version: str,
    segment_timings: list[SegmentTiming],
    pause_directives: list[PauseDirective],
) -> NarrationTimelineDocument:
    """Ende Voice-Segment + aufgelöste Pause = Beginn nächstes Segment.

    Gleiche Inputs → identische Zeiten.
    """
    if not segment_timings:
        raise PauseResolveError("Keine Segment-Timings vorhanden.")

    ordered = sorted(
        segment_timings,
        key=lambda item: item.segment_id,
    )
    # Preserve sequence from timings list order if segment_ids are sequential;
    # otherwise keep input order for determinism.
    ordered = list(segment_timings)

    pause_by_segment: dict[str, PauseDirective] = {}
    for directive in pause_directives:
        if directive.pause_function == "no_pause":
            continue
        if directive.after_segment_id in pause_by_segment:
            raise PauseResolveError(
                f"Doppelte Pause nach Segment {directive.after_segment_id}"
            )
        # Validate duration class early (deterministic failure).
        resolve_pause_duration_seconds(directive.duration_class)
        pause_by_segment[directive.after_segment_id] = directive

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
        start = cursor
        end = start + float(timing.duration_seconds)
        pause_seconds = 0.0
        directive = pause_by_segment.get(timing.segment_id)
        if directive is not None:
            pause_seconds = resolve_pause_duration_seconds(directive.duration_class)
        next_start = end + pause_seconds
        entries.append(
            NarrationTimelineEntry(
                segment_id=timing.segment_id,
                start_seconds=round(start, 6),
                end_seconds=round(end, 6),
                pause_after_seconds=round(pause_seconds, 6),
                next_segment_start_seconds=(
                    round(next_start, 6) if index < len(ordered) - 1 else None
                ),
            )
        )
        cursor = next_start

    total = entries[-1].end_seconds + entries[-1].pause_after_seconds
    return NarrationTimelineDocument(
        script_version=script_version,
        total_duration_seconds=round(total, 6),
        entries=entries,
    )
