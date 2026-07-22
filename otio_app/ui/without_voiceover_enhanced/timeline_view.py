"""Echtzeit-Timeline (Sekunden) für Enhanced Cut Plan — Narration + Shots."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape

import streamlit as st

from otio_app.services.without_voiceover_enhanced.models import (
    EditorialAnchor,
    FinalCutPlanDocument,
    NarrationAnchor,
    NarrationTimelineDocument,
    ResolvedTimelineDocument,
    RoughCutPlanDocument,
    SentenceTiming,
)
from otio_app.services.without_voiceover_enhanced.pause_resolver import (
    source_seconds_to_timeline,
)

_POSITION_FRACTION = {
    "start": 0.0,
    "early": 0.25,
    "middle": 0.5,
    "late": 0.75,
    "end": 1.0,
}
_PAUSE_POSITION_FRACTION = {
    "start": 0.0,
    "middle": 0.5,
    "end": 1.0,
}


@dataclass(frozen=True)
class TimelineBlock:
    start_seconds: float
    end_seconds: float
    label: str
    kind: str  # narration | pause | shot_rough | shot_final | shot_resolved
    detail: str = ""


def editorial_anchor_to_seconds(
    anchor: EditorialAnchor,
    timeline: NarrationTimelineDocument,
    *,
    sentence_index: dict[str, SentenceTiming] | None = None,
) -> float | None:
    """Mappt Editorial-Anker auf absolute Timeline-Sekunden."""
    by_id = {entry.segment_id: entry for entry in timeline.entries}
    if anchor.type == "pause":
        seg_id = (anchor.after_segment_id or anchor.segment_id or "").strip()
        entry = by_id.get(seg_id)
        if entry is None:
            return None
        frac = _PAUSE_POSITION_FRACTION.get(anchor.position, 0.0)
        return float(entry.end_seconds + entry.pause_after_seconds * frac)

    entry = by_id.get((anchor.segment_id or "").strip())
    if entry is None:
        return None

    sentence_id = str(anchor.sentence_id or "").strip()
    if (anchor.type == "sentence" or sentence_id) and sentence_index:
        sentence = sentence_index.get(sentence_id)
        if sentence is None:
            return None
        span = max(0.0, float(sentence.end_seconds) - float(sentence.start_seconds))
        frac = _POSITION_FRACTION.get(anchor.position, 0.0)
        source = float(sentence.start_seconds) + span * frac
        return float(source_seconds_to_timeline(entry, source))

    # Segment-Anker: Position über Roh-Audio (Intra-Pausen werden gemappt).
    audio_dur = entry.audio_duration_seconds
    if audio_dur is None:
        audio_dur = max(0.0, float(entry.end_seconds) - float(entry.start_seconds))
        for pause in entry.intra_pauses:
            audio_dur = max(0.0, audio_dur - float(pause.pause_seconds))
    frac = _POSITION_FRACTION.get(anchor.position, 0.0)
    return float(source_seconds_to_timeline(entry, float(audio_dur) * frac))


def narration_blocks(timeline: NarrationTimelineDocument) -> list[TimelineBlock]:
    blocks: list[TimelineBlock] = []
    for entry in timeline.entries:
        blocks.append(
            TimelineBlock(
                start_seconds=float(entry.start_seconds),
                end_seconds=float(entry.end_seconds),
                label=entry.segment_id,
                kind="narration",
                detail=f"{entry.end_seconds - entry.start_seconds:.2f}s",
            )
        )
        if entry.pause_after_seconds > 0.01:
            pause_start = float(entry.end_seconds)
            pause_end = pause_start + float(entry.pause_after_seconds)
            blocks.append(
                TimelineBlock(
                    start_seconds=pause_start,
                    end_seconds=pause_end,
                    label="pause",
                    kind="pause",
                    detail=f"{entry.pause_after_seconds:.2f}s after {entry.segment_id}",
                )
            )
    return blocks


def rough_shot_blocks(
    rough: RoughCutPlanDocument,
    timeline: NarrationTimelineDocument,
    *,
    sentence_index: dict[str, SentenceTiming] | None = None,
) -> list[TimelineBlock]:
    blocks: list[TimelineBlock] = []
    for shot in rough.shots:
        start = editorial_anchor_to_seconds(
            shot.start_anchor, timeline, sentence_index=sentence_index
        )
        end = editorial_anchor_to_seconds(
            shot.end_anchor, timeline, sentence_index=sentence_index
        )
        if start is None or end is None:
            continue
        if end < start:
            start, end = end, start
        if end - start < 0.05:
            end = start + 0.05
        asset = shot.local_asset_id or shot.asset_id or "gap"
        blocks.append(
            TimelineBlock(
                start_seconds=start,
                end_seconds=end,
                label=shot.shot_id,
                kind="shot_rough",
                detail=f"{asset} · {shot.narrative_function}",
            )
        )
    return blocks


def _narration_anchor_to_seconds(
    timeline: NarrationTimelineDocument,
    anchor: NarrationAnchor,
    *,
    sentence_index: dict[str, SentenceTiming] | None = None,
) -> float | None:
    """Wie timeline_resolver._anchor_to_seconds — Offset in Source-/Satz-Sekunden."""
    by_id = {entry.segment_id: entry for entry in timeline.entries}
    entry = by_id.get(anchor.segment_id)
    if entry is None:
        return None
    sentence_id = str(anchor.sentence_id or "").strip()
    if sentence_id and sentence_index:
        sentence = sentence_index.get(sentence_id)
        if sentence is None:
            return None
        span = max(0.0, float(sentence.end_seconds) - float(sentence.start_seconds))
        offset = max(0.0, min(float(anchor.offset_seconds), span))
        return float(
            source_seconds_to_timeline(entry, float(sentence.start_seconds) + offset)
        )
    audio_dur = entry.audio_duration_seconds
    if audio_dur is None:
        audio_dur = max(0.0, float(entry.end_seconds) - float(entry.start_seconds))
        for pause in entry.intra_pauses:
            audio_dur = max(0.0, audio_dur - float(pause.pause_seconds))
    offset = max(0.0, min(float(anchor.offset_seconds), float(audio_dur)))
    return float(source_seconds_to_timeline(entry, offset))


def final_shot_blocks(
    final: FinalCutPlanDocument,
    timeline: NarrationTimelineDocument,
    *,
    sentence_index: dict[str, SentenceTiming] | None = None,
) -> list[TimelineBlock]:
    blocks: list[TimelineBlock] = []
    for shot in final.shots:
        start = _narration_anchor_to_seconds(
            timeline,
            shot.narration_start_anchor,
            sentence_index=sentence_index,
        )
        end = _narration_anchor_to_seconds(
            timeline,
            shot.narration_end_anchor,
            sentence_index=sentence_index,
        )
        if start is None or end is None:
            continue
        if end < start:
            start, end = end, start
        if end - start < 0.05:
            end = start + 0.05
        blocks.append(
            TimelineBlock(
                start_seconds=float(start),
                end_seconds=float(end),
                label=shot.shot_id,
                kind="shot_final",
                detail=shot.asset_id or "",
            )
        )
    return blocks


def resolved_blocks(
    resolved: ResolvedTimelineDocument,
) -> tuple[list[TimelineBlock], list[TimelineBlock]]:
    audio: list[TimelineBlock] = []
    for entry in resolved.audio_segments:
        audio.append(
            TimelineBlock(
                start_seconds=float(entry.timeline_start_seconds),
                end_seconds=float(entry.timeline_end_seconds),
                label=entry.segment_id,
                kind="narration",
                detail=f"{entry.timeline_end_seconds - entry.timeline_start_seconds:.2f}s",
            )
        )
        if entry.pause_after_seconds > 0.01:
            pause_start = float(entry.timeline_end_seconds)
            pause_end = pause_start + float(entry.pause_after_seconds)
            audio.append(
                TimelineBlock(
                    start_seconds=pause_start,
                    end_seconds=pause_end,
                    label="pause",
                    kind="pause",
                    detail=f"{entry.pause_after_seconds:.2f}s",
                )
            )
    shots = [
        TimelineBlock(
            start_seconds=float(shot.timeline_start_seconds),
            end_seconds=float(shot.timeline_end_seconds),
            label=shot.shot_id,
            kind="shot_resolved",
            detail=shot.asset_id or "",
        )
        for shot in resolved.shots
    ]
    return audio, shots


def _format_clock(seconds: float) -> str:
    total = max(0, int(seconds))
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:d}:{secs:02d}"


def _track_html(blocks: list[TimelineBlock], total: float, track_label: str) -> str:
    if total <= 0:
        total = 1.0
    colors = {
        "narration": "#2f6f4e",
        "pause": "#5a5a5a",
        "shot_rough": "#3b6ea5",
        "shot_final": "#6b4f9a",
        "shot_resolved": "#1f7a8c",
    }
    parts = [
        '<div style="margin:0 0 10px 0;">',
        f'<div style="font-size:12px;opacity:0.85;margin-bottom:4px;">{escape(track_label)}</div>',
        '<div style="position:relative;height:30px;background:#1b1b1b;'
        'border:1px solid #333;border-radius:4px;overflow:hidden;">',
    ]
    for block in blocks:
        width = max(0.15, (block.end_seconds - block.start_seconds) / total * 100.0)
        left = max(0.0, block.start_seconds / total * 100.0)
        if left + width > 100.0:
            width = max(0.15, 100.0 - left)
        color = colors.get(block.kind, "#666")
        title = (
            f"{block.label} · {_format_clock(block.start_seconds)}–"
            f"{_format_clock(block.end_seconds)}"
        )
        if block.detail:
            title += f" · {block.detail}"
        parts.append(
            f'<div title="{escape(title)}" style="position:absolute;left:{left:.3f}%;'
            f"width:{width:.3f}%;top:2px;bottom:2px;background:{color};"
            f'border-radius:3px;overflow:hidden;white-space:nowrap;'
            f'font-size:10px;line-height:26px;padding:0 4px;color:#f3f3f3;">'
            f"{escape(block.label)}</div>"
        )
    parts.append("</div></div>")
    return "".join(parts)


def _ruler_html(total: float) -> str:
    if total <= 0:
        total = 1.0
    ticks = 8
    parts = [
        '<div style="position:relative;height:18px;margin:0 0 8px 0;'
        'font-size:10px;opacity:0.75;">'
    ]
    for index in range(ticks + 1):
        frac = index / ticks
        left = frac * 100.0
        label = _format_clock(total * frac)
        parts.append(
            f'<span style="position:absolute;left:{left:.2f}%;'
            f'transform:translateX(-50%);">{escape(label)}</span>'
        )
    parts.append("</div>")
    return "".join(parts)


def build_timeline_html(
    *,
    total_seconds: float,
    narration: list[TimelineBlock],
    shots: list[TimelineBlock],
    shots_label: str,
) -> str:
    return (
        '<div style="font-family:ui-monospace,SFMono-Regular,Menlo,monospace;">'
        + _ruler_html(total_seconds)
        + _track_html(narration, total_seconds, "Audio / Narration (+ Pausen)")
        + _track_html(shots, total_seconds, shots_label)
        + "</div>"
    )


def render_realtime_timeline(
    *,
    narration_timeline: NarrationTimelineDocument | None,
    rough: RoughCutPlanDocument | None = None,
    final: FinalCutPlanDocument | None = None,
    resolved: ResolvedTimelineDocument | None = None,
) -> None:
    """Rendert eine Echtzeit-Timeline in Sekunden (2 Spuren)."""
    st.subheader("Echtzeit-Timeline")
    if resolved is not None and resolved.total_duration_seconds > 0:
        narration, shots = resolved_blocks(resolved)
        total = float(resolved.total_duration_seconds)
        if shots:
            last = max(b.end_seconds for b in shots)
            total = max(total, last)
        st.caption(
            f"Quelle: Resolved Timeline · Dauer {_format_clock(total)} "
            f"({total:.2f}s) · {len(resolved.shots)} Shots · fps={resolved.fps:g}"
        )
        st.markdown(
            build_timeline_html(
                total_seconds=total,
                narration=narration,
                shots=shots,
                shots_label="Video / Resolved Shots",
            ),
            unsafe_allow_html=True,
        )
        return

    if narration_timeline is None or narration_timeline.total_duration_seconds <= 0:
        st.info(
            "Noch keine Narrationstimeline — erscheint nach LLM-Lauf 2 "
            "(Pausen + grober Cut)."
        )
        return

    narration = narration_blocks(narration_timeline)
    total = float(narration_timeline.total_duration_seconds)
    if final is not None and final.shots:
        shots = final_shot_blocks(final, narration_timeline)
        shots_label = "Video / Final Cut (Anchors → Sekunden)"
        source = "Final Cut Plan"
    elif rough is not None and rough.shots:
        shots = rough_shot_blocks(rough, narration_timeline)
        shots_label = "Video / Rough Cut (Editorial-Anker → Sekunden)"
        source = "Rough Cut Plan"
    else:
        shots = []
        shots_label = "Video / Shots (noch leer)"
        source = "Narrationstimeline"

    if shots:
        total = max(total, max(b.end_seconds for b in shots))

    st.caption(
        f"Quelle: {source} · Dauer {_format_clock(total)} ({total:.2f}s) · "
        f"{len(narration_timeline.entries)} Segmente · {len(shots)} Shots"
    )
    st.markdown(
        build_timeline_html(
            total_seconds=total,
            narration=narration,
            shots=shots,
            shots_label=shots_label,
        ),
        unsafe_allow_html=True,
    )
    with st.expander("Segmentliste (Sekunden)", expanded=False):
        for entry in narration_timeline.entries:
            st.caption(
                f"`{entry.segment_id}` · "
                f"{entry.start_seconds:.2f}–{entry.end_seconds:.2f}s"
                + (
                    f" · pause {entry.pause_after_seconds:.2f}s"
                    if entry.pause_after_seconds > 0
                    else ""
                )
            )
