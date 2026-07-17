"""OTIO reparse and semantic comparison for Discovery V2 exports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import opentimelineio as otio

from otio_app.discovery_v2.adapters.otio_adapter import read_timeline
from otio_app.discovery_v2.domain.export import (
    ExportContract,
    OtioReparseReport,
    OtioReparseReportStatus,
)
from otio_app.discovery_v2.persistence import export_repository as repo


@dataclass(frozen=True)
class ReparseResult:
    ok: bool
    report: OtioReparseReport


def _now() -> datetime:
    return datetime.now(timezone.utc)


def reparse_otio_file(
    *,
    path: Path,
    contract: ExportContract,
    export_run_id: str,
    artifact_id: str | None = None,
) -> ReparseResult:
    report_id = repo.new_otio_reparse_report_id()
    deviations: list[str] = []
    parseable = False
    track_count = 0
    clip_count = 0
    total_frames = 0
    total_seconds = 0.0
    timebase = f"{contract.fps_numerator}/{contract.fps_denominator}"
    try:
        timeline = read_timeline(path)
        parseable = True
        tracks = list(timeline.tracks)
        track_count = len(tracks)
        if track_count != 2:
            deviations.append(f"track_count:{track_count}")
        track_by_name = {track.name: track for track in tracks}
        video = track_by_name.get("V1")
        audio = track_by_name.get("A1")
        if video is None or getattr(video, "kind", None) != otio.schema.TrackKind.Video:
            deviations.append("missing_video_track")
        if audio is None or getattr(audio, "kind", None) != otio.schema.TrackKind.Audio:
            deviations.append("missing_audio_track")
        if video is not None:
            _compare_video(video, contract, deviations)
        if audio is not None:
            _compare_audio(audio, contract, deviations)
        transition_count = sum(1 for item in _children(video) if isinstance(item, otio.schema.Transition)) if video else 0
        if transition_count != len(contract.transitions):
            deviations.append(f"transition_count:{transition_count}")
        clip_count = sum(
            1
            for track in tracks
            for item in _children(track)
            if isinstance(item, (otio.schema.Clip, otio.schema.Gap))
        )
        video_frames = _track_item_frames(video)
        audio_frames = _track_item_frames(audio)
        total_frames = max(video_frames, audio_frames)
        total_seconds = total_frames / contract.fps
        if abs(video_frames - audio_frames) > 1:
            deviations.append(f"v_a_duration:{video_frames}:{audio_frames}")
        if abs(video_frames - contract.total_frames) > 1:
            deviations.append(f"video_duration:{video_frames}")
        if abs(audio_frames - contract.total_frames) > 1:
            deviations.append(f"audio_duration:{audio_frames}")
    except Exception as exc:  # noqa: BLE001
        deviations.append(f"parse_error:{type(exc).__name__}")
    equivalent = parseable and not deviations
    report = OtioReparseReport(
        report_id=report_id,
        export_run_id=export_run_id,
        artifact_id=artifact_id,
        parseable=parseable,
        semantically_equivalent=equivalent,
        deviations=deviations,
        track_count=track_count,
        clip_count=clip_count,
        total_duration_seconds=total_seconds,
        total_frames=total_frames,
        timebase=timebase,
        status=OtioReparseReportStatus.COMPLETED if equivalent else OtioReparseReportStatus.FAILED,
        created_at=_now(),
    )
    return ReparseResult(ok=equivalent, report=report)


def _compare_video(track, contract: ExportContract, deviations: list[str]) -> None:
    items = [item for item in _children(track) if isinstance(item, (otio.schema.Clip, otio.schema.Gap))]
    if len(items) != len(contract.video_items):
        deviations.append(f"video_item_count:{len(items)}")
        return
    for item, expected in zip(items, contract.video_items):
        meta = dict(item.metadata.get("discovery_v2", {}))
        if meta.get("shot_id") != expected.shot_id:
            deviations.append(f"shot_id:{meta.get('shot_id')}:{expected.shot_id}")
        if meta.get("asset_id") != expected.asset_id:
            deviations.append(f"asset_id:{expected.shot_id}")
        if meta.get("working_media_id") != expected.working_media_id:
            deviations.append(f"working_media_id:{expected.shot_id}")
        duration = _duration_frames(item)
        if duration != expected.duration_frames:
            deviations.append(f"video_duration_item:{expected.shot_id}:{duration}")
        if isinstance(item, otio.schema.Clip):
            sr = item.source_range
            start = int(round(sr.start_time.value)) if sr is not None else None
            if start != (expected.source_in_frame or 0):
                deviations.append(f"source_in:{expected.shot_id}:{start}")
            target = getattr(item.media_reference, "target_url", "")
            refs = {ref.media_id: ref for ref in contract.media_references}
            ref = refs.get(expected.media_reference_id or "")
            if ref is not None and target != ref.absolute_target_url:
                deviations.append(f"target_url:{expected.shot_id}")


def _compare_audio(track, contract: ExportContract, deviations: list[str]) -> None:
    items = [item for item in _children(track) if isinstance(item, (otio.schema.Clip, otio.schema.Gap))]
    if len(items) != len(contract.audio_items):
        deviations.append(f"audio_item_count:{len(items)}")
        return
    refs = {ref.media_id: ref for ref in contract.media_references}
    for item, expected in zip(items, contract.audio_items):
        meta = dict(item.metadata.get("discovery_v2", {}))
        if meta.get("voice_segment_id") != expected.voice_segment_id:
            deviations.append(f"voice_segment_id:{expected.entry_id}")
        duration = _duration_frames(item)
        if duration != expected.duration_frames:
            deviations.append(f"audio_duration_item:{expected.entry_id}:{duration}")
        if isinstance(item, otio.schema.Clip):
            target = getattr(item.media_reference, "target_url", "")
            ref = refs.get(expected.media_reference_id or "")
            if ref is not None and target != ref.absolute_target_url:
                deviations.append(f"audio_target_url:{expected.entry_id}")


def _children(track) -> list:
    if track is None:
        return []
    try:
        return list(track)
    except TypeError:
        return []


def _duration_frames(item) -> int:
    source_range = getattr(item, "source_range", None)
    if source_range is not None:
        return int(round(source_range.duration.value))
    return int(round(item.duration().value))


def _track_item_frames(track) -> int:
    return sum(_duration_frames(item) for item in _children(track) if isinstance(item, (otio.schema.Clip, otio.schema.Gap)))


__all__ = ["ReparseResult", "reparse_otio_file"]
