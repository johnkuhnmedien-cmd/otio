"""ElevenLabs-Kapitel-Audio + abgeleitete Segment-Timestamps (Enhanced).

Jedes Dramaturgie-Kapitel (inkl. Intro) wird in **einem** ElevenLabs-Call
vertont — eine Audiodatei pro Kapitel, keine Segment-Slices. Aus dem
Chapter-Alignment werden Segment-Offsets + satzbezogene Timings abgeleitet,
damit Cut-Plan / Timeline / OTIO pro Segment arbeiten können. Pausen baut der
Nutzer selbst in die Kapitel-WAV ein.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from otio_app.models import Project
from otio_app.project_layout import safe_folder_slug
from otio_app.services.media_utils import probe_duration_seconds
from otio_app.services.voiceover_generation.audio_alignment_service import (
    _align_segments,
)
from otio_app.services.voiceover_generation.elevenlabs_client import (
    ElevenLabsTtsError,
    audio_extension_for_output_format,
    is_elevenlabs_configured,
    synthesize_speech_with_timestamps,
)
from otio_app.services.voiceover_generation.elevenlabs_settings_service import (
    load_elevenlabs_settings,
)
from otio_app.services.without_voiceover_enhanced.enhanced_tts_text import (
    build_chapter_tts_text,
)
from otio_app.services.without_voiceover_enhanced.io_utils import load_model, write_json
from otio_app.services.without_voiceover_enhanced.models import (
    SegmentTiming,
    SegmentTimingsDocument,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    chapter_audio_dir,
    chapter_audio_path,
    segment_sentence_alignment_path,
    segment_timings_path,
)
from otio_app.services.without_voiceover_enhanced.script_author_service import (
    group_segments_by_folder,
    list_enabled_dramaturgy_folders,
)
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    ScriptLockError,
    require_locked_script,
)
from otio_app.services.without_voiceover_enhanced.segment_alignment_service import (
    persist_segment_tts_alignment,
    rebase_alignment_to_slice,
    rebuild_segment_alignments_index,
)

# folder_name, chapter_index, chapter_total, segment_index, segment_total
# Bei Kapitel-TTS ist segment_index/segment_total immer 1/1 (ein Call).
TtsProgressCallback = Callable[[str, int, int, int, int], None]

CHAPTER_AUDIO_READY = "ready"
CHAPTER_AUDIO_OPEN = "open"
CHAPTER_AUDIO_STALE = "stale"
CHAPTER_AUDIO_PARTIAL = "partial"


class AudioTimingError(RuntimeError):
    pass


@dataclass(frozen=True)
class ChapterAudioStatus:
    """UI-/Orchestrierungsstatus eines Kapitels (nicht segmentweise)."""

    folder_name: str
    label: str
    status: str  # ready | open | stale | partial
    segment_count: int
    ready_segment_count: int
    duration_seconds: float
    chapter_audio_path: str = ""

    @property
    def is_open(self) -> bool:
        return self.status in {
            CHAPTER_AUDIO_OPEN,
            CHAPTER_AUDIO_STALE,
            CHAPTER_AUDIO_PARTIAL,
        }

    @property
    def status_label(self) -> str:
        if self.status == CHAPTER_AUDIO_READY:
            return "vertont"
        if self.status == CHAPTER_AUDIO_STALE:
            return "veraltet"
        if self.status == CHAPTER_AUDIO_PARTIAL:
            return "unvollständig"
        return "offen"


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
    # Ganzes Kapitel stale, sobald ein Segment darin geändert wurde.
    stale_folders = {
        seg.folder_name
        for seg in locked.segments
        if seg.segment_id in changed_ids
    }
    folder_by_segment = {
        seg.segment_id: seg.folder_name for seg in locked.segments
    }
    for item in doc.segments:
        folder = folder_by_segment.get(item.segment_id)
        if (
            item.segment_id in changed_ids
            or item.script_version != locked.script_version
            or (folder is not None and folder in stale_folders)
        ):
            item.audio_status = "stale"
    write_json(segment_timings_path(project), doc)
    return doc


def _char_alignment_source(
    alignment: dict,
    normalized_alignment: dict,
) -> dict:
    char_source = alignment or {}
    starts = char_source.get("character_start_times_seconds") or []
    if starts:
        return char_source
    if normalized_alignment:
        return normalized_alignment
    return {}


def _synthesize_chapter(
    project: Project,
    *,
    segments,
    existing: SegmentTimingsDocument | None,
    chapter_index: int = 1,
    chapter_total: int = 1,
    folder_name: str = "",
    progress_callback: TtsProgressCallback | None = None,
) -> SegmentTimingsDocument:
    """Ein ElevenLabs-Call für alle Segmente eines Kapitels."""
    if not is_elevenlabs_configured():
        raise AudioTimingError("ElevenLabs ist nicht konfiguriert.")
    locked = require_locked_script(project)
    settings = load_elevenlabs_settings(project)
    label = folder_name or (segments[0].folder_name if segments else "") or "(ohne Kapitel)"
    if progress_callback is not None:
        progress_callback(label, chapter_index, chapter_total, 1, 1)

    full_tts, align_parts = build_chapter_tts_text(
        list(segments), model_id=settings.model_id
    )
    if not full_tts.strip() or not align_parts:
        raise AudioTimingError(f"Kein sprechbarer Text für Kapitel „{label}“.")

    try:
        result = synthesize_speech_with_timestamps(full_tts, settings)
    except ElevenLabsTtsError as exc:
        raise AudioTimingError(str(exc)) from exc

    ext, _ = audio_extension_for_output_format(settings.output_format)
    chapter_audio_dir(project).mkdir(parents=True, exist_ok=True)
    chapter_path = chapter_audio_path(project, label, ext)
    chapter_path.write_bytes(result.audio_bytes)
    chapter_duration = measure_audio_duration_seconds(chapter_path)

    # Chapter-Rohdaten für Diagnose ablegen.
    chapter_meta_dir = chapter_audio_dir(project) / safe_folder_slug(label)
    chapter_meta_dir.mkdir(parents=True, exist_ok=True)
    (chapter_meta_dir / "chapter_tts_text.txt").write_text(full_tts, encoding="utf-8")
    (chapter_meta_dir / "elevenlabs_timestamps.json").write_text(
        json.dumps(
            {
                "alignment": result.alignment or {},
                "normalized_alignment": result.normalized_alignment or {},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    if result.response_metadata:
        (chapter_meta_dir / "elevenlabs_tts_response_metadata.json").write_text(
            json.dumps(result.response_metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    char_source = _char_alignment_source(
        result.alignment or {}, result.normalized_alignment or {}
    )
    times_by_id, align_warnings = _align_segments(full_tts, align_parts, char_source)
    if not times_by_id:
        # Ohne Character-Timestamps: proportionale Aufteilung über Kapiteldauer.
        total_chars = sum(len(body) for _, body in align_parts) or 1
        cursor = 0.0
        for segment_id, spoken_body in align_parts:
            share = len(spoken_body) / total_chars
            start = cursor
            end = min(chapter_duration, start + chapter_duration * share)
            times_by_id[segment_id] = (start, end)
            cursor = end
        align_warnings = list(align_warnings) + [
            "chapter_proportional_fallback_without_character_timestamps"
        ]
    if align_warnings:
        (chapter_meta_dir / "alignment_warnings.json").write_text(
            json.dumps(align_warnings, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # Eine Audiodatei pro Kapitel — keine Segment-Slices, keine Audio-Mutation.
    by_id = {item.segment_id: item for item in (existing.segments if existing else [])}

    for index, (segment_id, spoken_body) in enumerate(align_parts):
        spoken_start, spoken_end = times_by_id.get(
            segment_id, (0.0, chapter_duration)
        )
        start = max(0.0, float(spoken_start))
        if index + 1 < len(align_parts):
            next_id = align_parts[index + 1][0]
            next_start = float(times_by_id.get(next_id, (chapter_duration, 0.0))[0])
            end = max(start + 0.05, next_start)
        else:
            end = max(start + 0.05, float(spoken_end), chapter_duration)
        end = min(chapter_duration, max(end, float(spoken_end), start + 0.05))
        duration = max(0.05, end - start)

        sliced_alignment = rebase_alignment_to_slice(
            char_source, start_seconds=start, end_seconds=end
        )
        alignment_doc = persist_segment_tts_alignment(
            project,
            segment_id=segment_id,
            script_version=locked.script_version,
            audio_path=str(chapter_path),
            audio_duration_seconds=duration,
            tts_text=spoken_body,
            alignment=sliced_alignment,
            normalized_alignment={},
            response_metadata={
                **(result.response_metadata or {}),
                "chapter_tts": True,
                "chapter_folder": label,
                "chapter_audio_path": str(chapter_path),
                "chapter_slice_start_seconds": round(start, 6),
                "chapter_slice_end_seconds": round(end, 6),
                "chapter_tts_text_length": len(full_tts),
            },
        )
        by_id[segment_id] = SegmentTiming(
            segment_id=segment_id,
            script_version=locked.script_version,
            audio_path=str(chapter_path),
            duration_seconds=duration,
            audio_status="valid",
            timestamps_path=alignment_doc.timestamps_path,
            alignment_path=str(segment_sentence_alignment_path(project, segment_id)),
            source_start_seconds=round(start, 6),
            source_end_seconds=round(end, 6),
        )

    live_ids = {seg.segment_id for seg in locked.segments}
    live_order = [seg.segment_id for seg in locked.segments]
    merged = [by_id[seg_id] for seg_id in by_id if seg_id in live_ids]
    order = {seg.segment_id: index for index, seg in enumerate(locked.segments)}
    merged.sort(key=lambda item: order.get(item.segment_id, 10_000))

    document = SegmentTimingsDocument(
        script_version=locked.script_version,
        segments=merged,
    )
    write_json(segment_timings_path(project), document)
    rebuild_segment_alignments_index(
        project,
        script_version=locked.script_version,
        live_segment_ids=live_order,
    )
    return document


def list_chapter_audio_statuses(project: Project) -> list[ChapterAudioStatus]:
    """Kapitelstatus für die Audio-UI: vertont / offen / veraltet / unvollständig."""
    from otio_app.services.without_voiceover_enhanced.intro_script_bridge import (
        ENHANCED_INTRO_FOLDER_NAME,
        ensure_confirmed_intro_in_locked_script,
        is_intro_folder_name,
    )

    ensure_confirmed_intro_in_locked_script(project)
    locked = require_locked_script(project)
    folder_order = [
        entry.folder_name for entry in list_enabled_dramaturgy_folders(project)
    ]
    groups = group_segments_by_folder(locked, folder_order=folder_order)
    timings = load_segment_timings(project)
    timing_by_id = {
        item.segment_id: item for item in (timings.segments if timings else [])
    }
    script_version = locked.script_version
    ext, _ = audio_extension_for_output_format(
        load_elevenlabs_settings(project).output_format
    )

    rows: list[ChapterAudioStatus] = []
    for folder_name, segments in groups:
        label = folder_name or "(ohne Kapitelzuordnung)"
        if is_intro_folder_name(folder_name):
            label = f"{ENHANCED_INTRO_FOLDER_NAME} (Hook)"
        ready_count = 0
        stale_count = 0
        duration = 0.0
        for seg in segments:
            item = timing_by_id.get(seg.segment_id)
            if item is None:
                continue
            if item.script_version != script_version:
                stale_count += 1
                continue
            if item.audio_status == "valid" and Path(item.audio_path).is_file():
                ready_count += 1
                duration += float(item.duration_seconds or 0.0)
            elif item.audio_status == "stale":
                stale_count += 1
        total = len(segments)
        if total == 0:
            status = CHAPTER_AUDIO_OPEN
        elif ready_count == total:
            status = CHAPTER_AUDIO_READY
        elif stale_count > 0 and ready_count == 0:
            status = CHAPTER_AUDIO_STALE
        elif ready_count > 0:
            status = CHAPTER_AUDIO_PARTIAL
        else:
            status = CHAPTER_AUDIO_OPEN
        chapter_path = chapter_audio_path(project, folder_name or label, ext)
        rows.append(
            ChapterAudioStatus(
                folder_name=folder_name or "",
                label=label,
                status=status,
                segment_count=total,
                ready_segment_count=ready_count,
                duration_seconds=round(duration, 2),
                chapter_audio_path=str(chapter_path) if chapter_path.is_file() else "",
            )
        )
    return rows


def synthesize_open_chapters_audio(
    project: Project,
    *,
    progress_callback: TtsProgressCallback | None = None,
) -> SegmentTimingsDocument:
    """Vertont nur offene/veraltete/unvollständige Kapitel (je 1 Call)."""
    from otio_app.services.without_voiceover_enhanced.intro_script_bridge import (
        ensure_confirmed_intro_in_locked_script,
    )

    ensure_confirmed_intro_in_locked_script(project)
    locked = require_locked_script(project)
    statuses = list_chapter_audio_statuses(project)
    open_folders = [row.folder_name for row in statuses if row.is_open]
    if not open_folders:
        existing = load_segment_timings(project)
        if existing is None:
            raise AudioTimingError("Keine offenen Kapitel — und kein Audio vorhanden.")
        return existing

    folder_order = [
        entry.folder_name for entry in list_enabled_dramaturgy_folders(project)
    ]
    groups = dict(group_segments_by_folder(locked, folder_order=folder_order))
    timings = load_segment_timings(project)
    chapter_total = len(open_folders)
    for chapter_index, folder_name in enumerate(open_folders, start=1):
        segments = groups.get(folder_name) or [
            seg for seg in locked.segments if seg.folder_name == folder_name
        ]
        if not segments:
            continue
        timings = _synthesize_chapter(
            project,
            segments=segments,
            existing=timings,
            chapter_index=chapter_index,
            chapter_total=chapter_total,
            folder_name=folder_name,
            progress_callback=progress_callback,
        )
    if timings is None:
        raise AudioTimingError("Keine offenen Kapitel konnten vertont werden.")
    return timings


def synthesize_locked_script_audio(
    project: Project,
    *,
    progress_callback: TtsProgressCallback | None = None,
) -> SegmentTimingsDocument:
    """Erzeugt Audiodateien sequenziell: Intro (falls bestätigt), dann Kapitel.

    Pro Kapitel genau **ein** ElevenLabs-Call.
    """
    from otio_app.services.without_voiceover_enhanced.intro_script_bridge import (
        ensure_confirmed_intro_in_locked_script,
    )

    ensure_confirmed_intro_in_locked_script(project)
    locked = require_locked_script(project)
    folder_order = [
        entry.folder_name for entry in list_enabled_dramaturgy_folders(project)
    ]
    groups = group_segments_by_folder(locked, folder_order=folder_order)
    if not groups:
        raise AudioTimingError("Keine Segmente im gesperrten Skript.")

    timings: SegmentTimingsDocument | None = None
    chapter_total = len(groups)
    for chapter_index, (folder_name, segments) in enumerate(groups, start=1):
        timings = _synthesize_chapter(
            project,
            segments=segments,
            existing=timings,
            chapter_index=chapter_index,
            chapter_total=chapter_total,
            folder_name=folder_name,
            progress_callback=progress_callback,
        )
    assert timings is not None
    return timings


def synthesize_folder_script_audio(
    project: Project,
    folder_name: str,
    *,
    progress_callback: TtsProgressCallback | None = None,
) -> SegmentTimingsDocument:
    """Vertont ein Dramaturgie-Kapitel in einem ElevenLabs-Call."""
    from otio_app.services.without_voiceover_enhanced.intro_script_bridge import (
        ENHANCED_INTRO_FOLDER_NAME,
        ensure_confirmed_intro_in_locked_script,
        is_intro_folder_name,
    )

    if is_intro_folder_name(folder_name):
        ensure_confirmed_intro_in_locked_script(project)
        folder_name = ENHANCED_INTRO_FOLDER_NAME
    locked = require_locked_script(project)
    folder_segments = [
        seg for seg in locked.segments if seg.folder_name == folder_name
    ]
    if not folder_segments:
        raise AudioTimingError(
            f"Keine Segmente für Kapitel „{folder_name}“ im gesperrten Skript."
        )
    existing = load_segment_timings(project)
    return _synthesize_chapter(
        project,
        segments=folder_segments,
        existing=existing,
        chapter_index=1,
        chapter_total=1,
        folder_name=folder_name,
        progress_callback=progress_callback,
    )


def synthesize_intro_script_audio(
    project: Project,
    *,
    progress_callback: TtsProgressCallback | None = None,
) -> SegmentTimingsDocument:
    """Vertont das bestätigte Intro in einem ElevenLabs-Call."""
    from otio_app.services.without_voiceover_enhanced.intro_script_bridge import (
        ENHANCED_INTRO_FOLDER_NAME,
        confirmed_intro_text,
        ensure_confirmed_intro_in_locked_script,
    )

    if confirmed_intro_text(project) is None:
        raise AudioTimingError(
            "Kein bestätigter Intro-Hook vorhanden (intro_hook.confirmed.json)."
        )
    ensure_confirmed_intro_in_locked_script(project)
    return synthesize_folder_script_audio(
        project,
        ENHANCED_INTRO_FOLDER_NAME,
        progress_callback=progress_callback,
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
