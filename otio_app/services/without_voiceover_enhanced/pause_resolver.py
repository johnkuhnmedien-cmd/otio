"""Deterministische Python-Auflösung von Pause Directives."""

from __future__ import annotations

from typing import Any

from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
    KEYWORD_FLOW_PAUSE_SAFETY_FRAMES,
)
from otio_app.services.without_voiceover_enhanced.models import (
    IntraPauseMarker,
    NarrationTimelineDocument,
    NarrationTimelineEntry,
    PauseDirective,
    SegmentTiming,
    SentenceTiming,
)
from otio_app.services.without_voiceover_enhanced.pause_config import (
    resolve_keyword_flow_pause_duration_seconds,
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


def _word_end_before(
    words: list[dict[str, Any]],
    *,
    at_or_before: float,
) -> float | None:
    best: float | None = None
    for word in words:
        end = float(word.get("end_seconds") or 0.0)
        if end <= at_or_before + 1e-9:
            if best is None or end > best:
                best = end
    return best


def _word_start_after(
    words: list[dict[str, Any]],
    *,
    at_or_after: float,
) -> float | None:
    best: float | None = None
    for word in words:
        start = float(word.get("start_seconds") or 0.0)
        if start >= at_or_after - 1e-9:
            if best is None or start < best:
                best = start
    return best


def _build_keyword_flow_intra_pauses(
    *,
    segment_id: str,
    audio_duration_seconds: float,
    sentence_directives: list[PauseDirective],
    sentence_index: dict[str, SentenceTiming],
    segment_words: list[dict[str, Any]],
    fps: float,
    repairs: list[str] | None = None,
) -> tuple[list[IntraPauseMarker], float]:
    """Fail-closed Pausenverlängerung mit 5-Frame-Sicherheitsabstand."""
    notes = repairs if repairs is not None else []
    if not sentence_directives:
        return [], 0.0
    rate = float(fps) if float(fps) > 0 else 25.0
    safety = KEYWORD_FLOW_PAUSE_SAFETY_FRAMES / rate
    sentences = _sentences_for_segment(sentence_index, segment_id)
    by_id = {item.sentence_id: item for item in sentences}
    markers: list[IntraPauseMarker] = []
    trailing_pause = 0.0
    seen: set[str] = set()

    for directive in sentence_directives:
        function = str(directive.pause_function or "").strip().lower()
        if function == "no_pause":
            continue
        sentence_id = str(directive.after_sentence_id or "").strip()
        if not sentence_id:
            # Segment-trailing Pause ohne Satzanker — nur nach Segmentende.
            try:
                extra = resolve_keyword_flow_pause_duration_seconds(
                    directive.duration_class,
                    pause_function=directive.pause_function,
                )
            except ValueError as exc:
                raise PauseResolveError(str(exc)) from exc
            if extra > 0:
                trailing_pause = max(trailing_pause, float(extra))
            continue
        if sentence_id in seen:
            continue
        sentence = by_id.get(sentence_id)
        if sentence is None:
            raise PauseResolveError(
                f"Keyword Flow: unbekannte after_sentence_id {sentence_id}."
            )
        next_sentence = None
        for candidate in sentences:
            if candidate.start_seconds > sentence.end_seconds + 1e-9:
                next_sentence = candidate
                break
        try:
            extra = resolve_keyword_flow_pause_duration_seconds(
                directive.duration_class,
                pause_function=directive.pause_function,
            )
        except ValueError as exc:
            raise PauseResolveError(str(exc)) from exc
        if extra <= 0:
            continue

        prev_end = _word_end_before(
            segment_words, at_or_before=float(sentence.end_seconds) + 0.05
        )
        if prev_end is None:
            raise PauseResolveError(
                f"Keyword Flow: Pause nach {sentence_id} ohne vorheriges Wortende."
            )
        if next_sentence is None:
            # Trailing nach letztem Satz: zusätzliche Stille nach Segmentende.
            seen.add(sentence_id)
            trailing_pause = max(trailing_pause, float(extra))
            notes.append(
                f"keyword_flow_pause: {sentence_id} trailing +{extra:.2f}s "
                f"(after last sentence)."
            )
            continue

        next_start = _word_start_after(
            segment_words, at_or_after=float(next_sentence.start_seconds) - 0.05
        )
        if next_start is None:
            raise PauseResolveError(
                f"Keyword Flow: Pause nach {sentence_id} ohne nächstes Wort."
            )
        natural_gap = float(next_start) - float(prev_end)
        # Überlappende / unsicher trennbare Wörter: fail-closed.
        if natural_gap + 1e-9 < 0:
            raise PauseResolveError(
                f"Keyword Flow: Pause nach {sentence_id} innerhalb unsicherem "
                f"Audioabschnitt (prev_end={prev_end:.3f}s > "
                f"next_start={next_start:.3f}s)."
            )
        # Zusätzliche Stille wird eingefügt — keine 10-Frame-Naturstille voraussetzen.
        # Nach Einfügen muss ±5 Frames Abstand zum vorherigen/nächsten Wort existieren.
        if natural_gap + float(extra) + 1e-9 < 2.0 * safety:
            raise PauseResolveError(
                f"Keyword Flow: Pause nach {sentence_id} ohne 5-Frame-"
                f"Sicherheitsbereich nach Einfügen "
                f"(natural_gap={natural_gap:.3f}s + extra={extra:.3f}s "
                f"< {2.0 * safety:.3f}s)."
            )
        # Split an belegter Wort-/Satzgrenze (Mitte der natürlichen Lücke,
        # auch wenn die Lücke kleiner als 2×Safety ist).
        split = mid_silence_split_seconds(
            sentence=sentence,
            next_sentence=next_sentence,
            audio_duration_seconds=audio_duration_seconds,
        )
        split = max(float(prev_end), min(float(split), float(next_start)))
        seen.add(sentence_id)
        markers.append(
            IntraPauseMarker(
                after_sentence_id=sentence_id,
                source_split_seconds=round(float(split), 6),
                pause_seconds=round(float(extra), 6),
            )
        )
        # Post-insert safe window (Timeline relativ zur Split-Nachbarschaft).
        safe_start, safe_end = safe_pause_window_timeline(
            previous_word_end_timeline=float(prev_end),
            next_word_start_timeline=float(next_start) + float(extra),
            fps=rate,
        )
        notes.append(
            f"keyword_flow_pause: {sentence_id} +{extra:.2f}s at source "
            f"{split:.3f}s (safety={safety:.3f}s @{rate:.0f}fps; "
            f"safe_window={safe_start:.3f}–{safe_end:.3f}s)."
        )
    markers.sort(key=lambda item: item.source_split_seconds)
    return markers, round(trailing_pause, 6)


def author_pause_after_map_from_script(script: Any) -> dict[str, float]:
    """segment_id → author_pause_after_seconds aus Locked/Draft-Script."""
    out: dict[str, float] = {}
    for segment in getattr(script, "segments", None) or []:
        sid = str(getattr(segment, "segment_id", "") or "").strip()
        if not sid:
            continue
        try:
            value = float(getattr(segment, "author_pause_after_seconds", 0.0) or 0.0)
        except (TypeError, ValueError):
            value = 0.0
        if value > 0:
            out[sid] = round(value, 2)
    return out


def build_narration_timeline(
    *,
    script_version: str,
    segment_timings: list[SegmentTiming],
    pause_directives: list[PauseDirective],
    sentence_index: dict[str, SentenceTiming] | None = None,
    enable_keyword_flow_pauses: bool = False,
    segment_words_by_id: dict[str, list[dict[str, Any]]] | None = None,
    fps: float = 25.0,
    repairs: list[str] | None = None,
    author_pause_after_by_segment: dict[str, float] | None = None,
) -> NarrationTimelineDocument:
    """Ende Voice-Segment + aufgelöste Pause = Beginn nächstes Segment.

    Default (Rhythm / Keyword-Sync / Intro): Pause-Directives werden ignoriert.
    Keyword Flow: fail-closed Verlängerung mit Wortgrenzen + 5-Frame-Safety.
    Autorenpausen aus dem Locked Script gelten in allen Cut-Stilen additiv.
    """
    if not segment_timings:
        raise PauseResolveError("Keine Segment-Timings vorhanden.")

    ordered = list(segment_timings)
    entries: list[NarrationTimelineEntry] = []
    cursor = 0.0
    index_map = sentence_index or {}
    words_by_seg = segment_words_by_id or {}
    author_pauses = author_pause_after_by_segment or {}
    repair_notes = repairs if repairs is not None else []

    # Directives nach Segment gruppieren (via after_sentence_id → segment).
    directives_by_segment: dict[str, list[PauseDirective]] = {}
    if enable_keyword_flow_pauses and pause_directives:
        for directive in pause_directives:
            sid = str(directive.after_sentence_id or "").strip()
            if sid and sid in index_map:
                seg = index_map[sid].segment_id
            else:
                seg = str(directive.after_segment_id or "").strip()
            if not seg:
                continue
            directives_by_segment.setdefault(seg, []).append(directive)

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
        intra: list[IntraPauseMarker] = []
        keyword_pause_after = 0.0
        if enable_keyword_flow_pauses:
            intra, keyword_pause_after = _build_keyword_flow_intra_pauses(
                segment_id=timing.segment_id,
                audio_duration_seconds=audio_duration,
                sentence_directives=list(
                    directives_by_segment.get(timing.segment_id) or []
                ),
                sentence_index=index_map,
                segment_words=list(words_by_seg.get(timing.segment_id) or []),
                fps=fps,
                repairs=repair_notes,
            )
        author_pause_after = max(
            0.0, float(author_pauses.get(timing.segment_id, 0.0) or 0.0)
        )
        if author_pause_after > 0:
            note = (
                f"author_pause: {timing.segment_id} +{author_pause_after:.2f}s"
            )
            if note not in repair_notes:
                repair_notes.append(note)
        pause_after = float(author_pause_after) + float(keyword_pause_after)
        start = cursor
        timeline_audio_end = start + audio_duration
        for pause in intra:
            timeline_audio_end += float(pause.pause_seconds)
        end = timeline_audio_end
        next_start = end + float(pause_after)
        entries.append(
            NarrationTimelineEntry(
                segment_id=timing.segment_id,
                start_seconds=round(start, 6),
                end_seconds=round(end, 6),
                pause_after_seconds=round(float(pause_after), 6),
                next_segment_start_seconds=(
                    round(next_start, 6) if index < len(ordered) - 1 else None
                ),
                audio_duration_seconds=round(audio_duration, 6),
                intra_pauses=intra,
            )
        )
        cursor = next_start

    total = entries[-1].end_seconds + float(entries[-1].pause_after_seconds or 0.0)
    return NarrationTimelineDocument(
        script_version=script_version,
        total_duration_seconds=round(total, 6),
        entries=entries,
    )


def safe_pause_window_timeline(
    *,
    previous_word_end_timeline: float,
    next_word_start_timeline: float,
    fps: float,
) -> tuple[float, float]:
    """safe_pause_start/end mit 5 Timelineframes Abstand."""
    rate = float(fps) if float(fps) > 0 else 25.0
    margin = KEYWORD_FLOW_PAUSE_SAFETY_FRAMES / rate
    return (
        round(float(previous_word_end_timeline) + margin, 6),
        round(float(next_word_start_timeline) - margin, 6),
    )
