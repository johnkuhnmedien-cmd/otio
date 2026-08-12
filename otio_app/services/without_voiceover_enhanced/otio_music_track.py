"""Optional OTIO Music-track helpers (fail-soft, additive)."""

from __future__ import annotations

from pathlib import Path

import opentimelineio as otio

from otio_app.models import Project
from otio_app.services.media_utils import probe_duration_seconds
from otio_app.services.without_voiceover_enhanced.elevenlabs_music_service import (
    usable_music_path_for_otio,
)
from otio_app.services.without_voiceover_enhanced.intro_script_bridge import (
    ENHANCED_INTRO_FOLDER_NAME,
    is_intro_folder_name,
)
from otio_app.services.without_voiceover_enhanced.models import ResolvedTimelineDocument


def collect_music_placements(
    project: Project,
    resolved: ResolvedTimelineDocument,
) -> list[tuple[Path, float, float, str]]:
    """Return (wav_path, timeline_start, duration, label) for usable music.

    Fail-soft: missing/stale/invalid music is skipped.
    """
    placements: list[tuple[Path, float, float, str]] = []
    chapters = list(resolved.chapters or [])
    if chapters:
        for env in chapters:
            folder = str(env.folder_name or env.chapter_id or "").strip()
            if not folder:
                continue
            scope = "intro" if is_intro_folder_name(folder) else "chapter"
            path = usable_music_path_for_otio(
                project, scope=scope, folder_name=folder
            )
            if path is None:
                continue
            start = float(env.chapter_video_start)
            duration = max(
                0.01, float(env.chapter_video_end) - float(env.chapter_video_start)
            )
            placements.append((path, start, duration, f"music:{folder}"))
        return placements

    # Single-scope resolved without chapter envelopes (rare): try intro then total.
    intro_path = usable_music_path_for_otio(
        project, scope="intro", folder_name=ENHANCED_INTRO_FOLDER_NAME
    )
    total = float(resolved.total_duration_seconds or 0.0)
    if intro_path is not None and total > 1e-6:
        placements.append((intro_path, 0.0, total, "music:Intro"))
    return placements


def build_optional_music_track(
    project: Project,
    resolved: ResolvedTimelineDocument,
    *,
    fps: float,
    time_range_fn,
) -> otio.schema.Track | None:
    """Build a ``Music`` audio track or None when no usable music exists."""
    placements = collect_music_placements(project, resolved)
    if not placements:
        return None
    track = otio.schema.Track(name="Music", kind=otio.schema.TrackKind.Audio)
    cursor = 0.0
    for path, start, duration, label in sorted(placements, key=lambda p: p[1]):
        if start > cursor + 1e-6:
            track.append(otio.schema.Gap(source_range=time_range_fn(start - cursor, fps)))
            cursor = start
        elif start < cursor - 1e-6:
            # Overlap — skip fail-soft.
            continue
        audio_dur = probe_duration_seconds(path) or max(duration, 0.01)
        clip = otio.schema.Clip(
            name=label,
            media_reference=otio.schema.ExternalReference(
                target_url=str(path),
                available_range=time_range_fn(audio_dur, fps, start_sec=0.0),
            ),
            source_range=time_range_fn(max(0.01, duration), fps, start_sec=0.0),
        )
        clip.metadata["enhanced_music"] = True
        clip.metadata["resolved_media_path"] = str(path)
        track.append(clip)
        cursor = start + duration
    return track
