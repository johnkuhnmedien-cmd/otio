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
from otio_app.services.clean_media import (
    path_is_readable_file,
    resolve_effective_media_path,
    validate_clean_output,
)
from otio_app.services.media_utils import probe_duration_seconds
from otio_app.services.otio_export_settings import (
    OtioExportSettings,
    load_otio_export_settings,
    save_otio_export_settings,
)
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


@dataclass(frozen=True)
class TimelineSection:
    voice_file: str
    folder: str
    video_start_sec: float
    video_duration_sec: float
    voice_start_sec: float
    voice_play_duration_sec: float


def verify_shot_media_paths(
    project: Project,
    shots: list[EditPlanShot],
    *,
    strict: bool = False,
) -> list[str]:
    """Prüft Shot-Medien. strict=False: nur Pfade (schnell). strict=True: ffmpeg-Decode."""
    warnings: list[str] = []
    for index, shot in enumerate(shots, start=1):
        if not shot.asset_path:
            warnings.append(f"Shot {index:03d} ({shot.folder}): kein Asset zugeordnet")
            continue
        original = _resolve_media_path(shot.asset_path)
        resolved = resolve_effective_media_path(project, shot.folder, original)
        if not path_is_readable_file(resolved):
            warnings.append(
                f"Shot {index:03d} ({shot.folder}): Medien offline — "
                f"`{resolved}` nicht lesbar (Clean Media erneut ausführen?)"
            )
            continue
        if strict and resolved.suffix.lower() in {".mp4", ".mov", ".m4v"}:
            valid, validation_error = validate_clean_output(resolved)
            if not valid:
                warnings.append(
                    f"Shot {index:03d} ({shot.folder}): `{resolved.name}` — "
                    f"{validation_error or 'nicht Resolve-ready'}"
                )
    return warnings


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
    export_settings = load_otio_export_settings(project)
    settings = settings.model_copy(
        update={
            "audio_offset_sec": export_settings.audio_offset_sec,
            "section_outro_sec": export_settings.section_outro_sec,
        }
    )

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

    warnings.extend(verify_shot_media_paths(project, merged_shots))

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


def _media_target_url(path: Path) -> str:
    """Absoluter POSIX-Pfad für target_url.

    DaVinci Resolve importiert OTIO mit ``file://``-URLs oft nicht zuverlässig
    (sucht dann nur nach Dateinamen → „File not found in search directories“).
    """
    try:
        resolved = path.expanduser().resolve()
    except OSError:
        resolved = path.expanduser()
    return resolved.as_posix()


def _clip_name_for_media(media_path: Path, *, index: int) -> str:
    """Clip-Name in Resolve = Dateiname (nicht Motiv-Text)."""
    name = media_path.name.strip()
    if name:
        return name[:120]
    return f"Shot_{index:03d}"


def _time_range(duration_sec: float, rate: float, *, start_sec: float = 0.0) -> otio.opentime.TimeRange:
    """Sekunden → OTIO-Zeit. RationalTime(6, 25) wäre 6 Frames — nicht 6 Sekunden."""
    return otio.opentime.TimeRange(
        start_time=otio.opentime.RationalTime.from_seconds(start_sec, rate),
        duration=otio.opentime.RationalTime.from_seconds(duration_sec, rate),
    )


def _media_reference(path: str, rate: float) -> otio.schema.ExternalReference:
    """Absoluter Medienpfad für Resolve-kompatiblen OTIO-Import.

  ``available_range`` bewusst weggelassen: Resolve vergleicht sonst unsere
  00:00:00:00-Angabe mit eingebettetem Datei-Timecode (z. B. Resolve-ProRes-
  Exporte ab 00:00:15:01) und meldet „Mismatch between specified target timecodes“.
    """
    resolved = _resolve_media_path(path)
    return otio.schema.ExternalReference(target_url=_media_target_url(resolved))


def _compute_timeline_sections(
    shots: list[EditPlanShot],
    settings: EditPlanSettings,
) -> list[TimelineSection]:
    """Ordner-Abschnitte inkl. Ausklingen und Voice-Start je Abschnitt."""
    sections: list[TimelineSection] = []
    video_cursor = 0.0
    index = 0
    while index < len(shots):
        folder = shots[index].folder
        voice_file = shots[index].voice_file
        section_start = video_cursor
        section_duration = 0.0
        end_index = index
        while end_index < len(shots) and shots[end_index].folder == folder:
            section_duration += max(0.01, float(shots[end_index].duration_sec))
            end_index += 1
        if end_index < len(shots):
            section_duration += max(0.0, float(settings.section_outro_sec))

        offset = max(0.0, float(settings.audio_offset_sec))
        voice_start = section_start + offset
        voice_play = max(0.01, section_duration - offset)

        sections.append(
            TimelineSection(
                voice_file=voice_file,
                folder=folder,
                video_start_sec=section_start,
                video_duration_sec=section_duration,
                voice_start_sec=voice_start,
                voice_play_duration_sec=voice_play,
            )
        )
        video_cursor = section_start + section_duration
        index = end_index
    return sections


def _is_last_shot_in_folder(shots: list[EditPlanShot], index: int) -> bool:
    if index + 1 >= len(shots):
        return True
    return shots[index + 1].folder != shots[index].folder


def _append_video_item(
    track: otio.schema.Track,
    shot: EditPlanShot,
    *,
    project: Project,
    index: int,
    rate: float,
    duration_sec: float,
) -> None:
    duration = _time_range(max(0.01, duration_sec), rate)

    if shot.asset_path:
        original = _resolve_media_path(shot.asset_path)
        media_path = resolve_effective_media_path(project, shot.folder, original)
        clip_name = _clip_name_for_media(media_path, index=index)
        video_clip = otio.schema.Clip(
            name=clip_name,
            media_reference=_media_reference(str(media_path), rate),
        )
        video_clip.source_range = duration
        video_clip.metadata["folder"] = shot.folder
        video_clip.metadata["motif"] = shot.motif
        video_clip.metadata["passage_text"] = shot.passage_text
        video_clip.metadata["original_asset_path"] = shot.asset_path
        video_clip.metadata["resolved_media_path"] = str(media_path)
        track.append(video_clip)
        return

    label = shot.motif or f"Shot {index}"
    gap = otio.schema.Gap(name=f"Missing · {label[:100]}", source_range=duration)
    gap.metadata["folder"] = shot.folder
    gap.metadata["motif"] = shot.motif
    gap.metadata["passage_text"] = shot.passage_text
    track.append(gap)


def _append_aligned_voice_track(
    timeline: otio.schema.Timeline,
    section: TimelineSection,
    rate: float,
    *,
    track_index: int,
) -> None:
    """Eine Audiospur pro Voice-over — Originaldatei, ein Stück pro Ordner-Abschnitt."""
    track = otio.schema.Track(
        name=f"A{track_index} · {Path(section.voice_file).stem}"[:120],
        kind=otio.schema.TrackKind.Audio,
    )
    if section.voice_start_sec > 0:
        track.append(
            otio.schema.Gap(
                name="Voice Start",
                source_range=_time_range(section.voice_start_sec, rate),
            )
        )

    resolved = _resolve_media_path(section.voice_file)
    file_duration = probe_duration_seconds(resolved)
    if file_duration is None or file_duration <= 0:
        file_duration = section.voice_play_duration_sec
    play_duration = min(file_duration, section.voice_play_duration_sec)

    voice_clip = otio.schema.Clip(
        name=Path(section.voice_file).stem,
        media_reference=_media_reference(section.voice_file, rate),
    )
    voice_clip.source_range = _time_range(play_duration, rate, start_sec=0.0)
    voice_clip.metadata["voice_file"] = section.voice_file
    voice_clip.metadata["folder"] = section.folder
    voice_clip.metadata["otio_note"] = (
        "Ungeschnittene Originaldatei ab Sekunde 0 — Länge begrenzt auf Abschnitt, "
        "damit sich Voice-overs nicht überlappen."
    )
    track.append(voice_clip)
    timeline.tracks.append(track)


def build_otio_timeline(
    project: Project,
    merged: MergedEditPlanResult,
    *,
    export_settings: OtioExportSettings | None = None,
) -> otio.schema.Timeline:
    """Erzeugt eine OTIO-Timeline mit Video- und Voice-over-Spur."""
    rate = float(project.fps)
    settings = merged.settings
    if export_settings is not None:
        settings = settings.model_copy(
            update={
                "audio_offset_sec": export_settings.audio_offset_sec,
                "section_outro_sec": export_settings.section_outro_sec,
            }
        )

    sections = _compute_timeline_sections(merged.shots, settings)
    timeline = otio.schema.Timeline(name=project.name)
    timeline.metadata["project_id"] = project.id
    timeline.metadata["included_folders"] = list(merged.included_folders)
    timeline.metadata["audio_offset_sec"] = settings.audio_offset_sec
    timeline.metadata["section_outro_sec"] = settings.section_outro_sec
    timeline.global_start_time = otio.opentime.RationalTime.from_seconds(0, rate)

    video_track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    for index, shot in enumerate(merged.shots, start=1):
        duration_sec = float(shot.duration_sec)
        if _is_last_shot_in_folder(merged.shots, index - 1) and index < len(merged.shots):
            duration_sec += max(0.0, float(settings.section_outro_sec))
        _append_video_item(
            video_track,
            shot,
            project=project,
            index=index,
            rate=rate,
            duration_sec=duration_sec,
        )

    timeline.tracks.append(video_track)

    seen_voices: set[str] = set()
    audio_index = 1
    for section in sections:
        if section.voice_file in seen_voices:
            continue
        seen_voices.add(section.voice_file)
        _append_aligned_voice_track(
            timeline,
            section,
            rate,
            track_index=audio_index,
        )
        audio_index += 1

    return timeline


def export_otio_timeline(
    project: Project,
    merged: MergedEditPlanResult,
    *,
    output_path: Path | None = None,
    export_settings: OtioExportSettings | None = None,
) -> Path:
    """Schreibt die zusammengeführte Timeline als .otio-Datei."""
    if not merged.ready:
        raise ValueError("Keine Shots zum Export — zuerst Schnittpläne bestätigen.")

    media_issues = verify_shot_media_paths(project, merged.shots, strict=True)
    if media_issues:
        preview = "\n".join(f"• {line}" for line in media_issues[:12])
        extra = f"\n… und {len(media_issues) - 12} weitere" if len(media_issues) > 12 else ""
        raise ValueError(
            "Medien nicht exportierbar — Clean Media prüfen oder Schnittplan anpassen:\n"
            f"{preview}{extra}"
        )

    settings = export_settings or load_otio_export_settings(project)
    save_otio_export_settings(project, settings)

    timeline = build_otio_timeline(project, merged, export_settings=settings)
    path = output_path or get_otio_export_path(project.work_dir_path, project.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    otio.adapters.write_to_file(timeline, str(path))
    return path
