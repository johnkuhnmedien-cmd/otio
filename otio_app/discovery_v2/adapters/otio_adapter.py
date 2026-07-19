"""Discovery V2 OpenTimelineIO adapter.

This module is intentionally pure: it maps a validated ExportContract to OTIO
objects and does not make editorial decisions or call Classic exporters.
"""

from __future__ import annotations

from pathlib import Path

import opentimelineio as otio

from otio_app.discovery_v2.domain.export import (
    OTIO_LIBRARY_VERSION,
    ExportContract,
    ExportMediaReference,
    ExportTransitionItem,
)


def assert_otio_library_version() -> None:
    if getattr(otio, "__version__", None) != OTIO_LIBRARY_VERSION:
        raise RuntimeError(f"Discovery V2 requires opentimelineio=={OTIO_LIBRARY_VERSION}")


def build_timeline(contract: ExportContract) -> otio.schema.Timeline:
    assert_otio_library_version()
    timeline = otio.schema.Timeline(name=contract.timeline_name)
    timeline.global_start_time = _rt(0, contract)
    timeline.tracks = otio.schema.Stack(name="tracks")
    video = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    audio = otio.schema.Track(name="A1", kind=otio.schema.TrackKind.Audio)
    refs = {ref.media_id: ref for ref in contract.media_references}
    transitions_by_after_shot = {transition.from_shot_id: transition for transition in contract.transitions}
    for item in contract.video_items:
        if item.item_type == "gap":
            video.append(
                otio.schema.Gap(
                    name=item.name,
                    source_range=_range(0, item.duration_frames, contract),
                    metadata={"discovery_v2": dict(item.metadata)},
                )
            )
        else:
            ref = refs.get(item.media_reference_id or "")
            if ref is None:
                raise ValueError(f"Missing media reference for video item {item.shot_id}")
            video.append(
                otio.schema.Clip(
                    name=item.name,
                    media_reference=_external_ref(ref, item.source_in_frame or 0, item.duration_frames, contract),
                    source_range=_range(item.source_in_frame or 0, item.duration_frames, contract),
                    metadata={"discovery_v2": dict(item.metadata)},
                )
            )
        transition = transitions_by_after_shot.get(item.shot_id)
        if transition is not None:
            video.append(_transition(transition, contract))
    for item in contract.audio_items:
        if item.item_type == "gap":
            audio.append(
                otio.schema.Gap(
                    name=item.name,
                    source_range=_range(0, item.duration_frames, contract),
                    metadata={"discovery_v2": dict(item.metadata)},
                )
            )
        else:
            ref = refs.get(item.media_reference_id or "")
            if ref is None:
                raise ValueError(f"Missing media reference for audio item {item.entry_id}")
            audio.append(
                otio.schema.Clip(
                    name=item.name,
                    media_reference=_external_ref(ref, 0, item.duration_frames, contract),
                    source_range=_range(0, item.duration_frames, contract),
                    metadata={"discovery_v2": dict(item.metadata)},
                )
            )
    timeline.tracks.append(video)
    timeline.tracks.append(audio)
    return timeline


def write_timeline(contract: ExportContract, output_path: Path) -> None:
    timeline = build_timeline(contract)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    otio.adapters.write_to_file(timeline, str(output_path))


def read_timeline(path: Path):
    assert_otio_library_version()
    return otio.adapters.read_from_file(str(path))


def _external_ref(
    ref: ExportMediaReference,
    start_frame: int,
    duration_frames: int,
    contract: ExportContract,
) -> otio.schema.ExternalReference:
    return otio.schema.ExternalReference(
        target_url=ref.absolute_target_url,
        available_range=_range(start_frame, duration_frames, contract),
        metadata={
            "discovery_v2": {
                "media_id": ref.media_id,
                "asset_id": ref.asset_id,
                "working_media_id": ref.working_media_id,
                "voice_segment_id": ref.voice_segment_id,
                "relative_path": ref.relative_path,
                "sha256": ref.sha256,
            }
        },
    )


def _transition(transition: ExportTransitionItem, contract: ExportContract):
    offset = max(1, int(round(transition.duration_frames / 2)))
    return otio.schema.Transition(
        name=f"dissolve_{transition.transition_id[:8]}",
        transition_type="SMPTE_Dissolve",
        in_offset=_rt(offset, contract),
        out_offset=_rt(offset, contract),
        metadata={"discovery_v2": dict(transition.metadata)},
    )


def _range(start_frame: int, duration_frames: int, contract: ExportContract):
    return otio.opentime.TimeRange(_rt(start_frame, contract), _rt(duration_frames, contract))


def _rt(frames: int, contract: ExportContract):
    return otio.opentime.RationalTime(frames, contract.fps)


__all__ = ["assert_otio_library_version", "build_timeline", "read_timeline", "write_timeline"]
