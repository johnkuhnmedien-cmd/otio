"""Optional OTIO Sound Effects track helpers (fail-soft, additive)."""

from __future__ import annotations

from pathlib import Path

import opentimelineio as otio

from otio_app.models import Project
from otio_app.services.media_utils import probe_duration_seconds
from otio_app.services.without_voiceover_enhanced.intro_script_bridge import (
    ENHANCED_INTRO_FOLDER_NAME,
    is_intro_folder_name,
)
from otio_app.services.without_voiceover_enhanced.models import ResolvedTimelineDocument
from otio_app.services.without_voiceover_enhanced.sfx_service import (
    usable_sfx_placements_for_otio,
)


def collect_sfx_placements(
    project: Project,
    resolved: ResolvedTimelineDocument,
) -> list[tuple[Path, float, float, str]]:
    """Return (wav_path, timeline_start, duration, label) for usable SFX.

    Fail-soft: missing/stale/invalid effects are skipped.
    """
    placements: list[tuple[Path, float, float, str]] = []
    chapters = list(resolved.chapters or [])
    if chapters:
        for env in chapters:
            folder = str(env.folder_name or env.chapter_id or "").strip()
            if not folder:
                continue
            scope = "intro" if is_intro_folder_name(folder) else "chapter"
            chapter_origin = float(env.chapter_video_start)
            for effect in usable_sfx_placements_for_otio(
                project, scope=scope, folder_name=folder
            ):
                path = Path(str(effect.get("wav_path") or ""))
                if not path.is_file():
                    continue
                # Chapter-local SFX times → global timeline via chapter origin.
                local_start = float(effect.get("timeline_start") or 0.0)
                duration = float(effect.get("duration") or 0.0)
                if duration <= 0:
                    continue
                start = chapter_origin + local_start
                sfx_id = str(effect.get("sfx_id") or path.stem)
                placements.append((path, start, duration, f"sfx:{folder}:{sfx_id}"))
        return placements

    # Single-scope resolved without chapter envelopes.
    for scope, folder in (
        ("intro", ENHANCED_INTRO_FOLDER_NAME),
        ("chapter", ""),
    ):
        for effect in usable_sfx_placements_for_otio(
            project, scope=scope, folder_name=folder  # type: ignore[arg-type]
        ):
            path = Path(str(effect.get("wav_path") or ""))
            if not path.is_file():
                continue
            start = float(effect.get("timeline_start") or 0.0)
            duration = float(effect.get("duration") or 0.0)
            if duration <= 0:
                continue
            sfx_id = str(effect.get("sfx_id") or path.stem)
            placements.append((path, start, duration, f"sfx:{sfx_id}"))
        if placements:
            break
    return placements


def build_optional_sfx_track(
    project: Project,
    resolved: ResolvedTimelineDocument,
    *,
    fps: float,
    time_range_fn,
) -> otio.schema.Track | None:
    """Build a ``Sound Effects`` audio track or None when nothing usable exists."""
    placements = collect_sfx_placements(project, resolved)
    if not placements:
        return None
    track = otio.schema.Track(name="Sound Effects", kind=otio.schema.TrackKind.Audio)
    cursor = 0.0
    for path, start, duration, label in sorted(placements, key=lambda p: p[1]):
        if start > cursor + 1e-6:
            track.append(otio.schema.Gap(source_range=time_range_fn(start - cursor, fps)))
            cursor = start
        elif start < cursor - 1e-6:
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
        clip.metadata["enhanced_sfx"] = True
        clip.metadata["resolved_media_path"] = str(path)
        track.append(clip)
        cursor = start + duration
    return track
