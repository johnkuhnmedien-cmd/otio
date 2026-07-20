"""ElevenLabs-Segment-Audio + echte Dauerablesung (without_voiceover_enhanced)."""

from __future__ import annotations

from pathlib import Path

from otio_app.models import Project
from otio_app.services.media_utils import probe_duration_seconds
from otio_app.services.voiceover_generation.elevenlabs_client import (
    ElevenLabsTtsError,
    audio_extension_for_output_format,
    is_elevenlabs_configured,
    synthesize_speech_with_timestamps,
)
from otio_app.services.voiceover_generation.elevenlabs_settings_service import (
    load_elevenlabs_settings,
)
from otio_app.services.without_voiceover_enhanced.io_utils import load_model, write_json
from otio_app.services.without_voiceover_enhanced.models import (
    SegmentTiming,
    SegmentTimingsDocument,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    audio_dir,
    segment_timings_path,
)
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    ScriptLockError,
    require_locked_script,
)


class AudioTimingError(RuntimeError):
    pass


def measure_audio_duration_seconds(path: Path) -> float:
    duration = probe_duration_seconds(path)
    if duration is None or duration <= 0:
        raise AudioTimingError(f"Audiodauer kann nicht gelesen werden: {path}")
    return float(duration)


def load_segment_timings(project: Project) -> SegmentTimingsDocument | None:
    return load_model(segment_timings_path(project), SegmentTimingsDocument)


def mark_audio_stale_for_changed_segments(project: Project) -> SegmentTimingsDocument | None:
    doc = load_segment_timings(project)
    if doc is None:
        return None
    locked = None
    try:
        locked = require_locked_script(project)
    except ScriptLockError:
        # No lock → all audio stale relative to editable draft.
        for item in doc.segments:
            item.audio_status = "stale"
        write_json(segment_timings_path(project), doc)
        return doc

    changed_ids = {s.segment_id for s in locked.segments if s.text_changed}
    for item in doc.segments:
        if item.segment_id in changed_ids or item.script_version != locked.script_version:
            item.audio_status = "stale"
    write_json(segment_timings_path(project), doc)
    return doc


def _synthesize_segments(
    project: Project,
    *,
    segments,
    existing: SegmentTimingsDocument | None,
) -> SegmentTimingsDocument:
    if not is_elevenlabs_configured():
        raise AudioTimingError("ElevenLabs ist nicht konfiguriert.")
    locked = require_locked_script(project)
    settings = load_elevenlabs_settings(project)
    out_dir = audio_dir(project) / "segments"
    out_dir.mkdir(parents=True, exist_ok=True)
    ext, _ = audio_extension_for_output_format(settings.output_format)

    by_id = {item.segment_id: item for item in (existing.segments if existing else [])}
    for segment in segments:
        try:
            result = synthesize_speech_with_timestamps(segment.text, settings)
        except ElevenLabsTtsError as exc:
            raise AudioTimingError(str(exc)) from exc
        audio_path = out_dir / f"{segment.segment_id}{ext}"
        audio_path.write_bytes(result.audio_bytes)
        duration = measure_audio_duration_seconds(audio_path)
        by_id[segment.segment_id] = SegmentTiming(
            segment_id=segment.segment_id,
            script_version=locked.script_version,
            audio_path=str(audio_path),
            duration_seconds=duration,
            audio_status="valid",
        )

    # Drop timings for segments that no longer exist in the locked script.
    live_ids = {seg.segment_id for seg in locked.segments}
    merged = [by_id[seg_id] for seg_id in by_id if seg_id in live_ids]
    # Stable order = locked script order
    order = {seg.segment_id: index for index, seg in enumerate(locked.segments)}
    merged.sort(key=lambda item: order.get(item.segment_id, 10_000))

    document = SegmentTimingsDocument(
        script_version=locked.script_version,
        segments=merged,
    )
    write_json(segment_timings_path(project), document)
    return document


def synthesize_locked_script_audio(project: Project) -> SegmentTimingsDocument:
    """Erzeugt eine Audiodatei pro Segment (alle Kapitel); misst echte Dauer."""
    locked = require_locked_script(project)
    return _synthesize_segments(project, segments=locked.segments, existing=None)


def synthesize_folder_script_audio(
    project: Project,
    folder_name: str,
) -> SegmentTimingsDocument:
    """Vertont nur die Segmente eines Dramaturgie-Kapitels (wie klassisch pro Ordner)."""
    locked = require_locked_script(project)
    folder_segments = [
        seg for seg in locked.segments if seg.folder_name == folder_name
    ]
    if not folder_segments:
        raise AudioTimingError(
            f"Keine Segmente für Kapitel „{folder_name}“ im gesperrten Skript."
        )
    existing = load_segment_timings(project)
    return _synthesize_segments(
        project, segments=folder_segments, existing=existing
    )


def validate_timings_against_script(
    project: Project,
    timings: SegmentTimingsDocument | None = None,
) -> list[str]:
    errors: list[str] = []
    locked = require_locked_script(project)
    doc = timings or load_segment_timings(project)
    if doc is None:
        return ["Audio fehlt (segment_timings.json)."]
    if doc.script_version != locked.script_version:
        errors.append(
            f"Skriptversion passt nicht zur Audiodatei: "
            f"{doc.script_version} != {locked.script_version}"
        )
    timing_ids = {item.segment_id: item for item in doc.segments}
    for segment in locked.segments:
        item = timing_ids.get(segment.segment_id)
        if item is None:
            errors.append(f"Audio fehlt für Segment {segment.segment_id}")
            continue
        if item.audio_status != "valid":
            errors.append(
                f"Audio für {segment.segment_id} ist {item.audio_status}"
            )
        if item.script_version != locked.script_version:
            errors.append(
                f"Falsche Skriptversion für {segment.segment_id}: {item.script_version}"
            )
        if not Path(item.audio_path).is_file():
            errors.append(f"Audiodatei fehlt: {item.audio_path}")
    return errors
