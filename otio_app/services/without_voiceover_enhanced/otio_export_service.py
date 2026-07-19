"""OTIO-Export ausschließlich aus der technisch aufgelösten Timeline."""

from __future__ import annotations

from pathlib import Path

import opentimelineio as otio

from otio_app.models import Project
from otio_app.services.generic_outro_selector import asset_id_for_path
from otio_app.services.inventory_loader import load_folder_inventory
from otio_app.services.without_voiceover_enhanced.io_utils import load_model
from otio_app.services.without_voiceover_enhanced.models import (
    AcceptedSupplementsDocument,
    ResolvedTimelineDocument,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    accepted_supplements_path,
    assert_enhanced_work_root,
    exports_dir,
    resolved_timeline_path,
)


class EnhancedOtioExportError(RuntimeError):
    pass


def _media_path_for_asset(project: Project, asset_id: str) -> str:
    for folder in project.selected_asset_subdirs:
        inventory = load_folder_inventory(project, folder)
        if inventory is None:
            continue
        for asset in getattr(inventory, "assets", []) or []:
            path = getattr(asset, "path", None) or getattr(asset, "source_path", None)
            if path is None:
                continue
            current_id = getattr(asset, "asset_id", None) or asset_id_for_path(str(path))
            if str(current_id) == asset_id:
                return str(path)
    accepted = load_model(accepted_supplements_path(project), AcceptedSupplementsDocument)
    if accepted is not None:
        for supplement in accepted.supplements:
            if supplement.candidate_id == asset_id:
                return supplement.preview_url or supplement.source_page
    raise EnhancedOtioExportError(f"Medienreferenz fehlt für Asset {asset_id}")


def export_otio_from_resolved_timeline(
    project: Project,
    *,
    basename: str = "enhanced_timeline",
) -> Path:
    assert_enhanced_work_root(project)
    resolved = load_model(resolved_timeline_path(project), ResolvedTimelineDocument)
    if resolved is None:
        raise EnhancedOtioExportError("Aufgelöste Timeline fehlt — kein OTIO-Export.")
    if resolved.errors:
        raise EnhancedOtioExportError(
            "Aufgelöste Timeline enthält Fehler: " + "; ".join(resolved.errors)
        )

    fps = resolved.fps or float(project.fps)
    timeline = otio.schema.Timeline(name=f"{project.name} enhanced")
    video_track = otio.schema.Track(name="Video", kind=otio.schema.TrackKind.Video)
    audio_track = otio.schema.Track(name="Narration", kind=otio.schema.TrackKind.Audio)

    # Video shots — intentionally NOT tied to sentence boundaries.
    cursor = 0.0
    for shot in sorted(resolved.shots, key=lambda s: s.timeline_start_seconds):
        if shot.timeline_start_seconds > cursor + 1e-6:
            gap = shot.timeline_start_seconds - cursor
            video_track.append(
                otio.schema.Gap(
                    source_range=otio.opentime.TimeRange(
                        start_time=otio.opentime.RationalTime(0, fps),
                        duration=otio.opentime.RationalTime(gap * fps, fps),
                    )
                )
            )
        media_path = _media_path_for_asset(project, shot.asset_id)
        available = otio.schema.ExternalReference(target_url=media_path)
        duration = shot.timeline_end_seconds - shot.timeline_start_seconds
        clip = otio.schema.Clip(
            name=shot.shot_id,
            media_reference=available,
            source_range=otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(
                    shot.source_start_seconds * fps, fps
                ),
                duration=otio.opentime.RationalTime(duration * fps, fps),
            ),
        )
        video_track.append(clip)
        cursor = shot.timeline_end_seconds

    # Audio with explicit pause gaps.
    audio_cursor = 0.0
    for segment in resolved.audio_segments:
        if segment.timeline_start_seconds > audio_cursor + 1e-6:
            gap = segment.timeline_start_seconds - audio_cursor
            audio_track.append(
                otio.schema.Gap(
                    source_range=otio.opentime.TimeRange(
                        start_time=otio.opentime.RationalTime(0, fps),
                        duration=otio.opentime.RationalTime(gap * fps, fps),
                    )
                )
            )
        duration = segment.timeline_end_seconds - segment.timeline_start_seconds
        clip = otio.schema.Clip(
            name=segment.segment_id,
            media_reference=otio.schema.ExternalReference(
                target_url=segment.audio_path
            ),
            source_range=otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(0, fps),
                duration=otio.opentime.RationalTime(duration * fps, fps),
            ),
        )
        audio_track.append(clip)
        audio_cursor = segment.timeline_end_seconds
        if segment.pause_after_seconds > 0:
            audio_track.append(
                otio.schema.Gap(
                    source_range=otio.opentime.TimeRange(
                        start_time=otio.opentime.RationalTime(0, fps),
                        duration=otio.opentime.RationalTime(
                            segment.pause_after_seconds * fps, fps
                        ),
                    )
                )
            )
            audio_cursor += segment.pause_after_seconds

    timeline.tracks.append(video_track)
    timeline.tracks.append(audio_track)

    out_dir = exports_dir(project)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{basename}.otio"
    otio.adapters.write_to_file(timeline, str(out_path))
    return out_path
