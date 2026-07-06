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
from otio_app.services.edit_plan_rules import ExportRuleOptions, export_rule_options, load_edit_plan_rules
from otio_app.services.media_utils import is_image_media
from otio_app.services.otio_media_transform import (
    compute_fill_zoom_factor,
    ensure_export_media_for_export,
    ensure_zoomed_media_for_export,
    ffmpeg_scale_crop_filter,
    format_folder_display_name,
    media_needs_aspect_fill,
    resolve_media_dimensions,
)
from otio_app.services.clean_media import (
    path_is_readable_file,
    probe_media,
    resolve_effective_media_path,
    validate_clean_output,
)
from otio_app.services.media_utils import (
    is_image_media,
    probe_duration_seconds,
    probe_media_timing,
)
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


def _media_reference(
    path: str,
    fallback_rate: float,
    *,
    trim_leading_sec: float = 0.0,
) -> otio.schema.ExternalReference:
    """Medienreferenz mit available_range passend zum eingebetteten Datei-Timecode."""
    resolved = _resolve_media_path(path)
    timing = probe_media_timing(resolved, default_rate=fallback_rate)
    media_rate = timing.rate or fallback_rate
    start_sec = timing.start_sec + max(0.0, trim_leading_sec)
    duration_sec = timing.duration_sec
    if duration_sec is None or duration_sec <= 0:
        duration_sec = probe_duration_seconds(resolved)
    if duration_sec is not None and trim_leading_sec > 0:
        duration_sec = max(0.0, duration_sec - trim_leading_sec)
    if duration_sec is None or duration_sec <= 0:
        return otio.schema.ExternalReference(target_url=_media_target_url(resolved))
    return otio.schema.ExternalReference(
        target_url=_media_target_url(resolved),
        available_range=_time_range(duration_sec, media_rate, start_sec=start_sec),
    )


def _clip_source_range_for_media(
    media_path: Path,
    *,
    fallback_rate: float,
    requested_duration_sec: float,
    trim_leading_sec: float = 0.0,
    hold_last_frame: bool = False,
) -> tuple[otio.opentime.TimeRange, float, list[str]]:
    """source_range im selben TC-Raum wie die Datei; Dauer ggf. auf Datei gekappt."""
    notes: list[str] = []
    trim = max(0.0, trim_leading_sec)
    if is_image_media(media_path):
        return (
            _time_range(max(0.01, requested_duration_sec), fallback_rate),
            requested_duration_sec,
            notes,
        )

    timing = probe_media_timing(media_path, default_rate=fallback_rate)
    media_rate = timing.rate or fallback_rate
    start_sec = timing.start_sec + trim
    available_sec = timing.duration_sec
    if available_sec is None or available_sec <= 0:
        available_sec = probe_duration_seconds(media_path)
    if available_sec is not None and trim > 0:
        available_sec = max(0.0, available_sec - trim)
    if available_sec is None or available_sec <= 0:
        return (
            _time_range(max(0.01, requested_duration_sec), media_rate, start_sec=start_sec),
            requested_duration_sec,
            notes,
        )

    play_sec = requested_duration_sec if hold_last_frame else min(requested_duration_sec, available_sec)
    if trim > 0:
        notes.append(f"{media_path.name}: erste {trim:.1f}s übersprungen")
    if play_sec + 0.05 < requested_duration_sec and not hold_last_frame:
        notes.append(
            f"{media_path.name}: Shot {requested_duration_sec:.1f}s, Datei nur "
            f"{available_sec:.1f}s ab TC {start_sec:.2f}s"
        )
    elif hold_last_frame and play_sec > available_sec + 0.05:
        notes.append(
            f"{media_path.name}: letztes Frame {play_sec - available_sec:.1f}s gehalten "
            f"(Ordner-Ausklingen)"
        )
    return (
        _time_range(max(0.01, play_sec), media_rate, start_sec=start_sec),
        play_sec,
        notes,
    )


def _track_duration_sec(track: otio.schema.Track, *, start_index: int = 0) -> float:
    """Summiert source_range-Dauern ab start_index."""
    total = 0.0
    for item in track[start_index:]:
        if item.source_range is not None:
            total += item.source_range.duration.to_seconds()
    return total


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


def _extend_clip_hold_last_frame(
    clip: otio.schema.Clip,
    *,
    extra_sec: float,
    rate: float,
    folder: str,
) -> None:
    """Verlängert einen Clip über die Medienlänge — Resolve friert das letzte Frame ein."""
    if clip.source_range is None or extra_sec <= 0.05:
        return
    start_sec = clip.source_range.start_time.to_seconds()
    new_dur = clip.source_range.duration.to_seconds() + extra_sec
    clip.source_range = _time_range(new_dur, rate, start_sec=start_sec)
    clip.metadata["otio_note"] = (
        f"Letztes Frame {extra_sec:.1f}s gehalten (Ordner-Ausklingen · {folder})"
    )


def _append_video_item(
    track: otio.schema.Track,
    shot: EditPlanShot,
    *,
    project: Project,
    index: int,
    rate: float,
    duration_sec: float,
    export_rules: ExportRuleOptions,
    timing_notes: list[str] | None = None,
    hold_last_frame: bool = False,
) -> float:
    """Hängt Clip oder Gap an die Videospur; liefert die tatsächliche Dauer in Sekunden."""
    if shot.asset_path:
        original = _resolve_media_path(shot.asset_path)
        if (export_rules.auto_zoom_fill or export_rules.folder_title_enabled) and not is_image_media(
            original
        ):
            media_path = ensure_export_media_for_export(
                project,
                shot.folder,
                original,
                notes=timing_notes,
            )
        else:
            media_path = resolve_effective_media_path(project, shot.folder, original)
        clip_name = _clip_name_for_media(media_path, index=index)
        trim = export_rules.trim_leading_sec
        source_range, _, notes = _clip_source_range_for_media(
            media_path,
            fallback_rate=rate,
            requested_duration_sec=max(0.01, duration_sec),
            trim_leading_sec=trim,
            hold_last_frame=hold_last_frame,
        )
        if timing_notes is not None:
            timing_notes.extend(notes)
        video_clip = otio.schema.Clip(
            name=clip_name,
            media_reference=_media_reference(str(media_path), rate, trim_leading_sec=trim),
        )
        video_clip.source_range = source_range
        video_clip.metadata["folder"] = shot.folder
        video_clip.metadata["motif"] = shot.motif
        video_clip.metadata["passage_text"] = shot.passage_text
        video_clip.metadata["original_asset_path"] = shot.asset_path
        video_clip.metadata["resolved_media_path"] = str(media_path)

        if export_rules.folder_title_enabled and not is_image_media(original):
            video_clip.metadata["folder_title"] = format_folder_display_name(shot.folder)
            video_clip.metadata["folder_title_font"] = export_rules.folder_title_font
            video_clip.metadata["folder_title_duration_sec"] = export_rules.folder_title_duration_sec

        if export_rules.auto_zoom_fill and not is_image_media(original):
            src_w, src_h = resolve_media_dimensions(project, shot.folder, original)
            out_probe = probe_media(media_path) if media_path != original else None
            if src_w and src_h:
                zoom = compute_fill_zoom_factor(
                    src_w,
                    src_h,
                    project.width,
                    project.height,
                )
                if zoom is not None:
                    video_clip.metadata["asset_width"] = src_w
                    video_clip.metadata["asset_height"] = src_h
                    video_clip.metadata["zoom_factor"] = round(zoom, 4)
                    if out_probe and out_probe.width and out_probe.height:
                        video_clip.metadata["output_width"] = out_probe.width
                        video_clip.metadata["output_height"] = out_probe.height

        track.append(video_clip)
        return source_range.duration.to_seconds()

    duration = _time_range(max(0.01, duration_sec), rate)
    label = shot.motif or f"Shot {index}"
    gap = otio.schema.Gap(name=f"Missing · {label[:100]}", source_range=duration)
    gap.metadata["folder"] = shot.folder
    gap.metadata["motif"] = shot.motif
    gap.metadata["passage_text"] = shot.passage_text
    track.append(gap)
    return duration.duration.to_seconds()


def _append_aligned_voice_track(
    timeline: otio.schema.Timeline,
    section: TimelineSection,
    rate: float,
    *,
    track_index: int,
    export_rules: ExportRuleOptions,
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
    source_range, _, _notes = _clip_source_range_for_media(
        resolved,
        fallback_rate=rate,
        requested_duration_sec=min(
            probe_duration_seconds(resolved) or section.voice_play_duration_sec,
            section.voice_play_duration_sec,
        ),
        trim_leading_sec=export_rules.trim_leading_sec,
    )

    voice_clip = otio.schema.Clip(
        name=Path(section.voice_file).stem,
        media_reference=_media_reference(
            section.voice_file,
            rate,
            trim_leading_sec=export_rules.trim_leading_sec,
        ),
    )
    voice_clip.source_range = source_range
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
    export_rules = export_rule_options(load_edit_plan_rules(project))
    timeline = otio.schema.Timeline(name=project.name)
    timeline.metadata["project_id"] = project.id
    timeline.metadata["included_folders"] = list(merged.included_folders)
    timeline.metadata["audio_offset_sec"] = settings.audio_offset_sec
    timeline.metadata["section_outro_sec"] = settings.section_outro_sec
    if export_rules.trim_leading_sec > 0:
        timeline.metadata["trim_leading_sec"] = export_rules.trim_leading_sec
    if export_rules.auto_zoom_fill:
        timeline.metadata["auto_zoom_fill"] = True
    if export_rules.folder_title_enabled:
        timeline.metadata["folder_title_overlay"] = True
        timeline.metadata["folder_title_font"] = export_rules.folder_title_font
        timeline.metadata["folder_title_duration_sec"] = export_rules.folder_title_duration_sec
    timeline.global_start_time = otio.opentime.RationalTime.from_seconds(0, rate)

    video_track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    timing_notes: list[str] = []
    section_index = 0
    section_track_start = 0
    for index, shot in enumerate(merged.shots, start=1):
        duration_sec = float(shot.duration_sec)
        is_last_in_folder = _is_last_shot_in_folder(merged.shots, index - 1)
        if is_last_in_folder:
            duration_sec += max(0.0, float(settings.section_outro_sec))
        _append_video_item(
            video_track,
            shot,
            project=project,
            index=index,
            rate=rate,
            duration_sec=duration_sec,
            export_rules=export_rules,
            timing_notes=timing_notes,
            hold_last_frame=is_last_in_folder and settings.section_outro_sec > 0,
        )
        if is_last_in_folder:
            section = sections[section_index]
            actual_sec = _track_duration_sec(video_track, start_index=section_track_start)
            pad_sec = section.video_duration_sec - actual_sec
            if pad_sec > 0.05:
                last_item = video_track[-1]
                if isinstance(last_item, otio.schema.Clip):
                    _extend_clip_hold_last_frame(
                        last_item,
                        extra_sec=pad_sec,
                        rate=rate,
                        folder=section.folder,
                    )
                    timing_notes.append(
                        f"{section.folder}: letztes Asset um {pad_sec:.1f}s verlängert "
                        f"(Ziel {section.video_duration_sec:.1f}s)"
                    )
                else:
                    pad = otio.schema.Gap(
                        name=f"Ausklingen · {section.folder}"[:120],
                        source_range=_time_range(pad_sec, rate),
                    )
                    pad.metadata["folder"] = section.folder
                    video_track.append(pad)
                    timing_notes.append(
                        f"{section.folder}: Videospur um {pad_sec:.1f}s aufgefüllt "
                        f"(Ziel {section.video_duration_sec:.1f}s)"
                    )
            section_index += 1
            section_track_start = len(video_track)

    timeline.tracks.append(video_track)
    if timing_notes:
        timeline.metadata["media_timing_notes"] = list(timing_notes)
        zoom_notes = [
            note
            for note in timing_notes
            if "×" in note
            or "Letterboxing" in note
            or "Zoom" in note
            or "Auflösung" in note
            or "Titel" in note
        ]
        if zoom_notes:
            timeline.metadata["aspect_fill_notes"] = zoom_notes

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
            export_rules=export_rules,
        )
        audio_index += 1

    return timeline


@dataclass(frozen=True)
class OtioExportResult:
    path: Path
    aspect_fill_notes: list[str] = field(default_factory=list)


def export_otio_timeline(
    project: Project,
    merged: MergedEditPlanResult,
    *,
    output_path: Path | None = None,
    export_settings: OtioExportSettings | None = None,
) -> OtioExportResult:
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
    aspect_notes = list(timeline.metadata.get("aspect_fill_notes", []))
    return OtioExportResult(path=path, aspect_fill_notes=aspect_notes)
