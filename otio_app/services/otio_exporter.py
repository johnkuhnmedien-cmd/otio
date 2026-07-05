"""Schnittpläne zusammenführen und als OTIO-Timeline exportieren."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlparse

import opentimelineio as otio

from otio_app.analysis_models import EditPlanDocument, EditPlanSettings, EditPlanShot
from otio_app.models import Project
from otio_app.project_layout import get_otio_export_path
from otio_app.services.edit_plan_builder import load_edit_plan
from otio_app.services.media_utils import probe_duration_seconds
from otio_app.services.voice_folder_matcher import load_voice_folder_mapping


@dataclass(frozen=True)
class MergedEditPlanResult:
    shots: list[EditPlanShot]
    settings: EditPlanSettings
    included_folders: list[str] = field(default_factory=list)
    skipped_folders: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return bool(self.shots)


def merge_confirmed_edit_plans(
    project: Project,
    *,
    folder_names: list[str] | None = None,
) -> MergedEditPlanResult:
    """Führt bestätigte pro-Ort-Schnittpläne in Voice-over-Reihenfolge zusammen."""
    mapping = load_voice_folder_mapping(project.voice_folder_mapping_path)
    if mapping is None or not mapping.confirmed:
        return MergedEditPlanResult(
            shots=[],
            settings=EditPlanSettings(),
            warnings=["Voice-over-Zuordnung fehlt oder ist nicht bestätigt."],
        )

    allowed_folders = set(folder_names) if folder_names is not None else None
    merged_shots: list[EditPlanShot] = []
    included: list[str] = []
    skipped: list[str] = []
    warnings: list[str] = []
    settings = EditPlanSettings()

    for entry in mapping.entries:
        if not entry.confirmed or not entry.folder:
            continue
        folder_name = entry.folder
        if allowed_folders is not None and folder_name not in allowed_folders:
            continue

        plan = load_edit_plan(project, folder_name)
        if plan is None or not plan.confirmed:
            if folder_name not in skipped:
                skipped.append(folder_name)
            continue

        if folder_name not in included:
            if not included:
                settings = plan.settings
            included.append(folder_name)

        voice_shots = [
            shot
            for shot in plan.shots
            if shot.voice_file == entry.voice_file and shot.folder == folder_name
        ]
        voice_shots.sort(key=lambda shot: (shot.voice_start_sec, shot.voice_end_sec))
        if not voice_shots:
            warnings.append(
                f"{folder_name}: keine Shots für `{Path(entry.voice_file).name}`."
            )
        merged_shots.extend(voice_shots)

    missing_assets = sum(1 for shot in merged_shots if not shot.asset_path)
    if missing_assets:
        warnings.append(f"{missing_assets} Shot(s) ohne lokales Asset — werden als Lücken exportiert.")

    if not merged_shots and not skipped:
        warnings.append("Keine bestätigten Schnittpläne zum Export gefunden.")

    return MergedEditPlanResult(
        shots=merged_shots,
        settings=settings,
        included_folders=included,
        skipped_folders=skipped,
        warnings=warnings,
    )


def _resolve_media_path(path: str) -> Path:
    if path.startswith("file:"):
        return Path(unquote(urlparse(path).path)).expanduser().resolve()
    return Path(path).expanduser().resolve()


def _time_range(duration_sec: float, rate: float, *, start_sec: float = 0.0) -> otio.opentime.TimeRange:
    """Sekunden → OTIO-Zeit. RationalTime(6, 25) wäre 6 Frames — nicht 6 Sekunden."""
    return otio.opentime.TimeRange(
        start_time=otio.opentime.RationalTime.from_seconds(start_sec, rate),
        duration=otio.opentime.RationalTime.from_seconds(duration_sec, rate),
    )


def _media_reference(path: str, rate: float) -> otio.schema.ExternalReference:
    """Absolute Medienpfad — Resolve verlinkt damit zuverlässiger als file://-URLs."""
    resolved = _resolve_media_path(path)
    available_duration = probe_duration_seconds(resolved)
    if available_duration is None or available_duration <= 0:
        available_duration = 3600.0
    available_range = _time_range(available_duration, rate)
    return otio.schema.ExternalReference(
        target_url=str(resolved),
        available_range=available_range,
    )


def _append_video_item(
    track: otio.schema.Track,
    shot: EditPlanShot,
    *,
    index: int,
    rate: float,
) -> None:
    duration_sec = max(0.01, float(shot.duration_sec))
    duration = _time_range(duration_sec, rate)
    label = shot.motif or f"Shot {index}"
    clip_name = f"{index:03d} · {shot.folder} · {label}"

    if shot.asset_path:
        video_clip = otio.schema.Clip(
            name=clip_name[:120],
            media_reference=_media_reference(shot.asset_path, rate),
        )
        video_clip.source_range = duration
        video_clip.metadata["folder"] = shot.folder
        video_clip.metadata["motif"] = shot.motif
        video_clip.metadata["passage_text"] = shot.passage_text
        track.append(video_clip)
        return

    gap = otio.schema.Gap(name=f"Missing · {clip_name[:100]}", source_range=duration)
    gap.metadata["folder"] = shot.folder
    gap.metadata["motif"] = shot.motif
    gap.metadata["passage_text"] = shot.passage_text
    track.append(gap)


def _voice_section_starts(
    shots: list[EditPlanShot],
    audio_offset_sec: float,
) -> list[tuple[str, float]]:
    """Pro Voice-over-Datei: Startzeit auf der Timeline (Video ab 0, Voice mit Offset)."""
    sections: list[tuple[str, float]] = []
    video_cursor = 0.0
    current_voice: str | None = None

    for shot in shots:
        if shot.voice_file != current_voice:
            start_sec = video_cursor
            if current_voice is None:
                start_sec = max(0.0, audio_offset_sec)
            sections.append((shot.voice_file, start_sec))
            current_voice = shot.voice_file
        video_cursor += max(0.01, float(shot.duration_sec))
    return sections


def _append_aligned_voice_track(
    timeline: otio.schema.Timeline,
    voice_file: str,
    start_sec: float,
    rate: float,
    *,
    track_index: int,
) -> None:
    """Eigene Audiospur pro Voice-over — ungeschnitten, per Gap auf Video-Abschnitt ausgerichtet."""
    track = otio.schema.Track(
        name=f"A{track_index} · {Path(voice_file).stem}"[:120],
        kind=otio.schema.TrackKind.Audio,
    )
    if start_sec > 0:
        track.append(
            otio.schema.Gap(
                name="Voice Start",
                source_range=_time_range(start_sec, rate),
            )
        )

    resolved = _resolve_media_path(voice_file)
    duration = probe_duration_seconds(resolved)
    if duration is None or duration <= 0:
        duration = 3600.0

    voice_clip = otio.schema.Clip(
        name=Path(voice_file).stem,
        media_reference=_media_reference(voice_file, rate),
    )
    voice_clip.source_range = _time_range(duration, rate, start_sec=0.0)
    voice_clip.metadata["voice_file"] = voice_file
    track.append(voice_clip)
    timeline.tracks.append(track)


def build_otio_timeline(
    project: Project,
    merged: MergedEditPlanResult,
) -> otio.schema.Timeline:
    """Erzeugt eine OTIO-Timeline mit Video- und Voice-over-Spur."""
    rate = float(project.fps)
    settings = merged.settings
    timeline = otio.schema.Timeline(name=project.name)
    timeline.metadata["project_id"] = project.id
    timeline.metadata["included_folders"] = list(merged.included_folders)
    timeline.global_start_time = otio.opentime.RationalTime.from_seconds(0, rate)

    video_track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)

    for index, shot in enumerate(merged.shots, start=1):
        _append_video_item(video_track, shot, index=index, rate=rate)

    timeline.tracks.append(video_track)

    voice_sections = _voice_section_starts(merged.shots, settings.audio_offset_sec)
    seen_voices: set[str] = set()
    audio_index = 1
    for voice_file, start_sec in voice_sections:
        if voice_file in seen_voices:
            continue
        seen_voices.add(voice_file)
        _append_aligned_voice_track(
            timeline,
            voice_file,
            start_sec,
            rate,
            track_index=audio_index,
        )
        audio_index += 1

    timeline.metadata["audio_offset_sec"] = settings.audio_offset_sec
    return timeline


def export_otio_timeline(
    project: Project,
    merged: MergedEditPlanResult,
    *,
    output_path: Path | None = None,
) -> Path:
    """Schreibt die zusammengeführte Timeline als .otio-Datei."""
    if not merged.ready:
        raise ValueError("Keine Shots zum Export — zuerst Schnittpläne bestätigen.")

    timeline = build_otio_timeline(project, merged)
    path = output_path or get_otio_export_path(project.work_dir_path, project.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    otio.adapters.write_to_file(timeline, str(path))
    return path
