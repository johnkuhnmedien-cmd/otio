"""Schnittpläne zusammenführen und als OTIO-Timeline exportieren."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlparse

import opentimelineio as otio

from otio_app.analysis_models import (
    EditPlanDocument,
    EditPlanSettings,
    EditPlanShot,
    TimelineItem,
    VoiceoverPlan,
)
from otio_app.models import Project
from otio_app.project_layout import get_otio_export_path
from otio_app.services.edit_plan_builder import load_edit_plan
from otio_app.services.clean_media_settings import load_clean_media_settings
from otio_app.services.edit_plan_rules import ExportRuleOptions, export_rule_options, load_edit_plan_rules
from otio_app.services.inventory_hash import inventory_hash_is_stale
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
from otio_app.services.edit_plan_validator import (
    ValidationStatus,
    plan_validation_error_to_message,
    validate_opening_titles,
    validate_timeline_items,
)
from otio_app.services.plan_validation_reports import global_validation_blocked
from otio_app.services.timeline_plan_builder import (
    assign_global_timeline_positions,
    build_voiceover_plan,
    shots_from_timeline_items,
)
from otio_app.services.opening_title_renderer import ensure_opening_titles_rendered
from otio_app.services.voice_folder_matcher import load_voice_folder_mapping


@dataclass(frozen=True)
class MergedEditPlanResult:
    timeline_items: list[TimelineItem]
    shots: list[EditPlanShot]
    settings: EditPlanSettings
    voiceovers: list[VoiceoverPlan] = field(default_factory=list)
    included_folders: list[str] = field(default_factory=list)
    skipped_folders: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    validation_status: str = ValidationStatus.OK.value

    @property
    def ready(self) -> bool:
        return bool(self.timeline_items) and self.validation_status == ValidationStatus.OK.value


@dataclass(frozen=True)
class TimelineSection:
    voice_file: str
    folder: str
    video_start_sec: float
    video_duration_sec: float
    voiceover: VoiceoverPlan


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


def verify_timeline_media_paths(
    project: Project,
    items: list[TimelineItem],
    *,
    strict: bool = False,
) -> list[str]:
    """Prüft Medien der Timeline-Items."""
    warnings: list[str] = []
    for item in items:
        if item.type == "opening_title":
            media_path = item.rendered_media_path or item.resolved_media_path
            if not media_path:
                warnings.append(f"{item.timeline_item_id} ({item.folder_name}): kein gerenderter Titel")
                continue
            resolved = Path(media_path).expanduser().resolve()
            if not path_is_readable_file(resolved):
                warnings.append(
                    f"{item.timeline_item_id} ({item.folder_name}): Titel offline — `{resolved}`"
                )
            continue
        if not item.resolved_media_path:
            warnings.append(f"{item.timeline_item_id} ({item.folder_name}): kein Medium")
            continue
        original = _resolve_media_path(item.resolved_media_path)
        resolved = resolve_effective_media_path(project, item.folder_name, original)
        if not path_is_readable_file(resolved):
            warnings.append(
                f"{item.timeline_item_id} ({item.folder_name}): offline — `{resolved}`"
            )
            continue
        if strict and resolved.suffix.lower() in {".mp4", ".mov", ".m4v"}:
            valid, validation_error = validate_clean_output(resolved)
            if not valid:
                warnings.append(
                    f"{item.timeline_item_id}: `{resolved.name}` — "
                    f"{validation_error or 'nicht Resolve-ready'}"
                )
    return warnings


def _plan_section_items(plan: EditPlanDocument, folder_name: str, voice_file: str) -> list[TimelineItem]:
    if plan.timeline_items:
        return [
            item
            for item in plan.timeline_items
            if item.folder_name == folder_name and item.voice_file == voice_file
        ]
    return []


def merge_confirmed_edit_plans(
    project: Project,
    *,
    folder_names: list[str] | None = None,
) -> MergedEditPlanResult:
    """Führt bestätigte Schnittpläne in Voice-over-Reihenfolge zusammen."""
    mapping = load_voice_folder_mapping(project.voice_folder_mapping_path)
    if mapping is None or not mapping.confirmed:
        return MergedEditPlanResult(
            timeline_items=[],
            shots=[],
            settings=EditPlanSettings(),
            warnings=["Voice-over-Zuordnung fehlt oder ist nicht bestätigt."],
            validation_status=ValidationStatus.BLOCKED.value,
        )

    allowed_folders = set(folder_names) if folder_names is not None else None
    merged_items: list[TimelineItem] = []
    merged_voiceovers: list[VoiceoverPlan] = []
    included: list[str] = []
    skipped: list[str] = []
    warnings: list[str] = []
    settings = EditPlanSettings()
    global_cursor = 0.0
    rules_doc = load_edit_plan_rules(project)
    export_rules = export_rule_options(rules_doc)

    worst_status = ValidationStatus.OK
    all_validation_errors: list[str] = []

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

        if plan.candidate_status == "BLOCKED":
            all_validation_errors.append(
                f"{folder_name}: Schnittplan BLOCKED — bitte unter „Vorschlag“ neu generieren."
            )
            worst_status = ValidationStatus.BLOCKED
            if folder_name not in skipped:
                skipped.append(folder_name)
            continue

        if plan.inventory_hash_at_plan_time and inventory_hash_is_stale(
            project,
            folder_name,
            plan.inventory_hash_at_plan_time,
        ):
            all_validation_errors.append(
                f"{folder_name}: Inventory geändert — bitte Schnittplan neu vorschlagen "
                f"(inventory_hash stale)."
            )
            worst_status = ValidationStatus.BLOCKED

        if folder_name not in included:
            included.append(folder_name)
            settings = plan.settings

        section_items = _plan_section_items(plan, folder_name, entry.voice_file)
        if not section_items:
            warnings.append(
                f"{folder_name}: kein timeline_items im Schnittplan — bitte neu vorschlagen."
            )
            continue

        section_voiceover = (
            plan.voiceover
            if plan.voiceover is not None
            else build_voiceover_plan(entry.voice_file, plan.settings)
        )
        merged_voiceovers.append(section_voiceover)

        # Audio-Start und Ordner-Ausklingen werden fest beim Bestätigen dieses
        # Ordners eingebaut (plan.settings / plan.voiceover). Ein späterer
        # globaler Export-Override (frühere otio_export_settings.json-Werte)
        # führte hier zu Geisterfehlern: die Validierung prüfte gegen einen
        # Wert, mit dem die Timeline-Items nie gebaut wurden — z. B.
        # "section_outro_sec (8.5s) nicht vollständig geplant (4.0s)", obwohl
        # der Schnittplan selbst vollkommen konsistent war.
        validation = validate_timeline_items(
            section_items,
            settings=plan.settings,
            allow_black_outro=plan.allow_black_outro,
            fps=float(project.fps),
            voiceover=section_voiceover,
            opening_title_required=export_rules.folder_title_enabled,
            rules_doc=rules_doc,
            work_dir_path=project.work_dir_path,
        )
        all_validation_errors.extend(f"{folder_name}: {err}" for err in validation.errors)
        warnings.extend(validation.warnings)
        if validation.status == ValidationStatus.BLOCKED:
            worst_status = ValidationStatus.BLOCKED
        elif (
            validation.status == ValidationStatus.AWAITING_APPROVAL
            and worst_status != ValidationStatus.BLOCKED
        ):
            worst_status = ValidationStatus.AWAITING_APPROVAL

        positioned = assign_global_timeline_positions(
            section_items,
            section_start_sec=global_cursor,
        )
        merged_items.extend(positioned)
        global_cursor = max(
            (item.timeline_out_sec for item in positioned),
            default=global_cursor,
        )

    shots = shots_from_timeline_items(merged_items)
    warnings.extend(verify_timeline_media_paths(project, merged_items))

    if merged_items:
        global_validation = global_validation_blocked(
            merged_items,
            settings=settings,
            rules_doc=rules_doc,
        )
        if not global_validation.ok:
            worst_status = ValidationStatus.BLOCKED
            for error in global_validation.errors:
                all_validation_errors.append(
                    f"Global: {plan_validation_error_to_message(error)}"
                )

    if all_validation_errors:
        for line in all_validation_errors:
            warnings.append(f"Validierung: {line}")

    if not merged_items and not skipped:
        warnings.append("Keine bestätigten Schnittpläne zum Export gefunden.")

    return MergedEditPlanResult(
        timeline_items=merged_items,
        shots=shots,
        settings=settings,
        voiceovers=merged_voiceovers,
        included_folders=included,
        skipped_folders=skipped,
        warnings=warnings,
        validation_status=worst_status.value,
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
    items: list[TimelineItem],
    settings: EditPlanSettings,
    voiceovers: list[VoiceoverPlan],
) -> list[TimelineSection]:
    """Ordner-Abschnitte inkl. Voice-over-Plan je Abschnitt."""
    sections: list[TimelineSection] = []
    if not items:
        return sections

    voiceover_by_file = {vo.path: vo for vo in voiceovers}
    index = 0
    while index < len(items):
        folder = items[index].folder_name
        voice_file = items[index].voice_file
        section_start = items[index].timeline_in_sec
        section_duration = 0.0
        end_index = index
        while end_index < len(items) and items[end_index].folder_name == folder:
            item = items[end_index]
            duration = max(0.01, float(item.duration_sec))
            section_duration += duration
            end_index += 1

        voiceover = voiceover_by_file.get(voice_file)
        if voiceover is None:
            voiceover = build_voiceover_plan(voice_file, settings)

        sections.append(
            TimelineSection(
                voice_file=voice_file,
                folder=folder,
                video_start_sec=section_start,
                video_duration_sec=section_duration,
                voiceover=voiceover,
            )
        )
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
    auto_zoom_fill: bool,
    timing_notes: list[str] | None = None,
    hold_last_frame: bool = False,
) -> float:
    """Hängt Clip oder Gap an die Videospur; liefert die tatsächliche Dauer in Sekunden."""
    if shot.asset_path:
        original = _resolve_media_path(shot.asset_path)
        if auto_zoom_fill and not is_image_media(
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
        if shot.section_outro:
            video_clip.metadata["section_outro"] = True

        if export_rules.folder_title_enabled and not is_image_media(original):
            video_clip.metadata["folder_title"] = format_folder_display_name(shot.folder)
            video_clip.metadata["folder_title_font"] = export_rules.folder_title_font
            video_clip.metadata["folder_title_duration_sec"] = export_rules.folder_title_duration_sec

        if auto_zoom_fill and not is_image_media(original):
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
) -> None:
    """Eine Audiospur pro Voice-over — volle WAV-Dauer, ohne Head-Trim.

    Nutzt section.voiceover.timeline_start_sec (beim Bestätigen dieses Ordners
    fest eingebaut), NICHT einen separaten globalen Export-Wert — sonst
    können Video- und Audiospur auseinanderlaufen, sobald sich die globale
    Audio-Start-Einstellung nach dem Bestätigen geändert hat.
    """
    track = otio.schema.Track(
        name=f"A{track_index} · {Path(section.voice_file).stem}"[:120],
        kind=otio.schema.TrackKind.Audio,
    )
    gap_sec = max(0.0, section.video_start_sec + section.voiceover.timeline_start_sec)
    if gap_sec > 0.001:
        track.append(
            otio.schema.Gap(
                name="Voice Start",
                source_range=_time_range(gap_sec, rate),
            )
        )

    voiceover = section.voiceover
    resolved = _resolve_media_path(section.voice_file)
    play_sec = max(0.01, voiceover.duration_sec)

    voice_clip = otio.schema.Clip(
        name=Path(section.voice_file).stem,
        media_reference=_media_reference(
            section.voice_file,
            rate,
            trim_leading_sec=0.0,
        ),
    )
    media_rate = rate
    timing = probe_media_timing(resolved, default_rate=rate)
    if timing.rate:
        media_rate = timing.rate
    # Konsistent mit available_range (siehe _append_timeline_item_clip):
    # falls die WAV-Datei einen eingebetteten Timecode ungleich Null hat,
    # muss source_range diesen Offset ebenfalls berücksichtigen.
    source_in = timing.start_sec + voiceover.source_in_sec
    voice_clip.source_range = _time_range(play_sec, media_rate, start_sec=source_in)
    voice_clip.metadata["voice_file"] = section.voice_file
    voice_clip.metadata["folder"] = section.folder
    voice_clip.metadata["otio_note"] = (
        "Ungeschnittene Originaldatei ab Sekunde 0 — volle ffprobe-Dauer, kein Head-Trim."
    )
    voice_clip.metadata["voiceover_timeline_start_sec"] = round(
        section.video_start_sec + voiceover.timeline_start_sec, 4
    )
    voice_clip.metadata["voiceover_timeline_end_sec"] = round(
        section.video_start_sec + voiceover.timeline_end_sec, 4
    )
    track.append(voice_clip)
    timeline.tracks.append(track)


def _append_timeline_item_clip(
    track: otio.schema.Track,
    item: TimelineItem,
    *,
    project: Project,
    index: int,
    rate: float,
    export_rules: ExportRuleOptions,
    auto_zoom_fill: bool,
    timing_notes: list[str] | None = None,
) -> None:
    """Schreibt ein Timeline-Item 1:1 — ohne Daueränderung oder Asset-Auswahl."""
    media_path = _resolve_media_path(item.resolved_media_path)
    original = (
        _resolve_media_path(item.original_asset_path)
        if item.original_asset_path
        else media_path
    )
    if auto_zoom_fill and not is_image_media(
        original
    ):
        effective = ensure_export_media_for_export(
            project,
            item.folder_name,
            original,
            notes=timing_notes,
        )
    else:
        effective = resolve_effective_media_path(project, item.folder_name, original)

    duration_sec = item.final_duration_sec or item.duration_sec
    media_rate = rate
    timing = probe_media_timing(effective, default_rate=rate)
    if timing.rate:
        media_rate = timing.rate

    # source_range muss im selben Zeit-Koordinatensystem wie available_range
    # liegen (siehe _media_reference unten, trim_leading_sec=0.0 -> deren
    # start_time = eingebetteter Timecode der Datei). item.source_in_sec/
    # source_out_sec wurden beim Schnittplan-Bau IMMER relativ zu einem bei
    # Null beginnenden Timecode berechnet (nur video_head_trim_sec
    # berücksichtigt) — für Dateien mit einem von Null abweichenden
    # eingebetteten SMPTE-Timecode (z. B. Kamera-Footage) muss dieser Offset
    # hier nachträglich ergänzt werden. Sonst fällt source_range außerhalb
    # von available_range, und Resolve meldet beim Import/Reconnect einen
    # Timecode-Mismatch ("No overlap between specified target timecodes and
    # located file timecodes"), obwohl die richtige Datei referenziert wird.
    embedded_offset = timing.start_sec
    source_in = embedded_offset + item.source_in_sec
    source_out = embedded_offset + (item.source_out_sec or (item.source_in_sec + duration_sec))

    # source_range darf available_range (die TATSÄCHLICHE Dateidauer inkl.
    # eingebettetem Timecode) nicht überschreiten — sonst meldet Resolve
    # ebenfalls einen Timecode-/Media-Offline-Mismatch, selbst wenn
    # embedded_offset korrekt berücksichtigt ist. Das passiert, wenn die
    # geplante Shot-Dauer länger ist als die tatsächlich verfügbare
    # Restlänge der Quelldatei (z. B. sehr kurze Clips). Der ältere,
    # Shot-basierte Export-Pfad (_clip_source_range_for_media) hat diese
    # Begrenzung bereits — hier fehlte sie im moderneren TimelineItem-Pfad.
    if timing.duration_sec is not None:
        available_end = embedded_offset + timing.duration_sec
        if source_out > available_end + 0.01:
            if timing_notes is not None:
                timing_notes.append(
                    f"{effective.name}: Shot {source_out - source_in:.1f}s angefordert, "
                    f"Datei nur bis {max(0.0, available_end - source_in):.1f}s verfügbar — Ende gekürzt."
                )
            source_out = available_end
        if source_in > available_end:
            source_in = max(embedded_offset, available_end - 0.01)

    play_sec = max(0.01, source_out - source_in)

    video_clip = otio.schema.Clip(
        name=_clip_name_for_media(effective, index=index),
        media_reference=_media_reference(
            str(effective),
            rate,
            trim_leading_sec=0.0,
        ),
    )
    video_clip.source_range = _time_range(play_sec, media_rate, start_sec=source_in)
    video_clip.metadata["timeline_item_id"] = item.timeline_item_id
    video_clip.metadata["type"] = item.type
    video_clip.metadata["folder"] = item.folder_name
    video_clip.metadata["motif"] = item.motif
    video_clip.metadata["passage_text"] = item.passage_text
    video_clip.metadata["resolved_media_path"] = str(effective)
    video_clip.metadata["asset_role"] = item.asset_role
    video_clip.metadata["selection_reason"] = item.selection_reason
    if item.type == "generic_outro_visual":
        video_clip.metadata["section_outro"] = True
    if item.warnings:
        video_clip.metadata["warnings"] = list(item.warnings)
    if timing_notes is not None and item.warnings:
        timing_notes.extend(f"{effective.name}: {w}" for w in item.warnings)

    if export_rules.folder_title_enabled and item.type != "generic_outro_visual" and not is_image_media(
        original
    ):
        pass  # Opening Title liegt als eigenes V2-Element im Schnittplan — nicht in V1-Metadata.

    if auto_zoom_fill and not is_image_media(original):
        src_w, src_h = resolve_media_dimensions(project, item.folder_name, original)
        if src_w and src_h:
            zoom = compute_fill_zoom_factor(src_w, src_h, project.width, project.height)
            if zoom is not None:
                video_clip.metadata["zoom_factor"] = round(zoom, 4)

    track.append(video_clip)


def _append_opening_title_clip(
    track: otio.schema.Track,
    item: TimelineItem,
    *,
    rate: float,
) -> None:
    """Platziert gerenderten Opening-Title-Clip auf V2 — nur rendered_media_path aus Plan."""
    media_path = Path(item.rendered_media_path or item.resolved_media_path)
    if media_path.suffix.lower() in {".jpg", ".jpeg"}:
        raise ValueError(f"JPG darf nicht als Titel-Overlay verwendet werden: {media_path}")

    duration_sec = item.final_duration_sec or item.duration_sec
    media_rate = rate
    timing = probe_media_timing(media_path, default_rate=rate)
    if timing.rate:
        media_rate = timing.rate

    style = item.title_style
    display_text = style.text if style is not None else item.text
    title_clip = otio.schema.Clip(
        name=f"Title · {display_text[:80]}",
        media_reference=_media_reference(str(media_path), rate, trim_leading_sec=0.0),
    )
    title_clip.source_range = _time_range(max(0.01, duration_sec), media_rate, start_sec=0.0)
    title_clip.metadata["timeline_item_id"] = item.timeline_item_id
    title_clip.metadata["type"] = "opening_title"
    title_clip.metadata["track"] = item.track or "V2"
    title_clip.metadata["timeline_in_sec"] = round(item.timeline_in_sec, 4)
    title_clip.metadata["timeline_out_sec"] = round(item.timeline_out_sec, 4)
    title_clip.metadata["duration_sec"] = round(duration_sec, 4)
    title_clip.metadata["rendered_media_path"] = str(media_path)
    title_clip.metadata["folder"] = item.folder_name
    if style is not None:
        title_clip.metadata["text"] = style.text
        title_clip.metadata["render_hash"] = style.render_hash
        title_clip.metadata["requested_font_family"] = style.requested_font_family
        title_clip.metadata["resolved_font_family"] = style.resolved_font_family
        title_clip.metadata["font_fallback_used"] = style.font_fallback_used
        title_clip.metadata["font_size_px"] = style.font_size_px
        title_clip.metadata["shadow_enabled"] = style.shadow_enabled
        title_clip.metadata["shadow_opacity"] = style.shadow_opacity
        title_clip.metadata["shadow_offset_x"] = style.shadow_offset_x
        title_clip.metadata["shadow_offset_y"] = style.shadow_offset_y
        title_clip.metadata["position"] = style.position
        title_clip.metadata["fade_in_sec"] = style.fade_in_sec
        title_clip.metadata["fade_out_sec"] = style.fade_out_sec
        title_clip.metadata["render_manifest_path"] = style.render_manifest_path
    else:
        title_clip.metadata["text"] = item.text
        title_clip.metadata["font_size_px"] = item.font_size_px or item.font_size
    track.append(title_clip)


def _build_v2_title_track(
    title_items: list[TimelineItem],
    *,
    rate: float,
) -> otio.schema.Track | None:
    """Baut V2 mit Gaps und Opening-Title-Clips an geplanten Positionen."""
    if not title_items:
        return None
    track = otio.schema.Track(name="V2", kind=otio.schema.TrackKind.Video)
    cursor = 0.0
    for item in sorted(title_items, key=lambda entry: entry.timeline_in_sec):
        gap_sec = item.timeline_in_sec - cursor
        if gap_sec > 0.001:
            track.append(
                otio.schema.Gap(
                    name="Title Gap",
                    source_range=_time_range(gap_sec, rate),
                )
            )
            cursor += gap_sec
        _append_opening_title_clip(track, item, rate=rate)
        cursor += item.duration_sec
    return track


def build_otio_timeline(
    project: Project,
    merged: MergedEditPlanResult,
    *,
    export_settings: OtioExportSettings | None = None,
) -> otio.schema.Timeline:
    """Erzeugt OTIO nur aus expliziten Timeline-Items — ohne Regeländerungen.

    `export_settings` wird nur noch für die informative `timeline.metadata`
    verwendet — die tatsächliche Audio-/Video-Platzierung nutzt IMMER die pro
    Ordner beim Bestätigen fest eingebauten Werte (siehe
    `_append_aligned_voice_track`). Ein globaler Override hätte sonst zu
    Video-/Audio-Drift geführt, sobald sich die globale Timing-Einstellung
    nach dem Bestätigen eines Schnittplans geändert hat.
    """
    rate = float(project.fps)
    settings = merged.settings
    if export_settings is not None:
        settings = settings.model_copy(
            update={
                "audio_offset_sec": export_settings.audio_offset_sec,
                "section_outro_sec": export_settings.section_outro_sec,
            }
        )

    items = merged.timeline_items
    sections = _compute_timeline_sections(items, settings, merged.voiceovers)
    export_rules = export_rule_options(load_edit_plan_rules(project))
    auto_zoom_fill = load_clean_media_settings(project).auto_zoom_fill
    timeline = otio.schema.Timeline(name=project.name)
    timeline.metadata["project_id"] = project.id
    timeline.metadata["included_folders"] = list(merged.included_folders)
    timeline.metadata["audio_offset_sec"] = settings.audio_offset_sec
    timeline.metadata["section_outro_sec"] = settings.section_outro_sec
    if export_rules.trim_leading_sec > 0:
        timeline.metadata["trim_leading_sec"] = export_rules.trim_leading_sec
    if auto_zoom_fill:
        timeline.metadata["auto_zoom_fill"] = True
    timeline.global_start_time = otio.opentime.RationalTime.from_seconds(0, rate)

    v1_items = [item for item in items if item.type != "opening_title"]
    title_items = [item for item in items if item.type == "opening_title"]

    video_track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    timing_notes: list[str] = []
    for index, item in enumerate(v1_items, start=1):
        _append_timeline_item_clip(
            video_track,
            item,
            project=project,
            index=index,
            rate=rate,
            export_rules=export_rules,
            auto_zoom_fill=auto_zoom_fill,
            timing_notes=timing_notes,
        )

    timeline.tracks.append(video_track)

    title_track = _build_v2_title_track(title_items, rate=rate)
    if title_track is not None:
        timeline.tracks.append(title_track)
        timeline.metadata["opening_title_count"] = len(title_items)

    if timing_notes:
        timeline.metadata["media_timing_notes"] = list(timing_notes)

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


@dataclass(frozen=True)
class OtioReadbackReport:
    voiceover_timeline_start_sec: float
    voiceover_source_in_sec: float
    voiceover_duration_sec: float
    voiceover_timeline_end_sec: float
    expected_voiceover_timeline_end_sec: float
    audio_offset_sec: float
    generic_outro_timeline_start_sec: float | None
    visual_coverage_until_sec: float
    ok: bool
    opening_title_on_v2: bool = False
    opening_title_timeline_start_sec: float | None = None
    errors: list[str] = field(default_factory=list)


def _visual_coverage_until_sec(items: list[TimelineItem]) -> float:
    visual_types = {"video_shot", "image_shot", "generic_narration_visual", "generic_outro_visual"}
    return max(
        (item.timeline_out_sec for item in items if item.type in visual_types),
        default=0.0,
    )


def _first_outro_start_sec(items: list[TimelineItem]) -> float | None:
    outros = [item.timeline_in_sec for item in items if item.type == "generic_outro_visual"]
    return min(outros) if outros else None


def validate_otio_readback(
    timeline: otio.schema.Timeline,
    *,
    sections: list[TimelineSection],
    items: list[TimelineItem],
    audio_offset_sec: float,
) -> list[OtioReadbackReport]:
    """Prüft geschriebene OTIO gegen Voice-over-Regeln."""
    reports: list[OtioReadbackReport] = []
    audio_tracks = [
        track
        for track in timeline.tracks
        if track.kind == otio.schema.TrackKind.Audio
    ]

    for section_index, section in enumerate(sections):
        voiceover = section.voiceover
        section_items = [item for item in items if item.folder_name == section.folder]
        expected_end = section.video_start_sec + voiceover.timeline_end_sec
        expected_start = section.video_start_sec + voiceover.timeline_start_sec
        visual_coverage = _visual_coverage_until_sec(section_items)
        outro_start = _first_outro_start_sec(section_items)

        errors: list[str] = []
        voice_source_in = 0.0
        voice_duration = 0.0
        voice_timeline_start = expected_start
        voice_timeline_end = expected_start

        if section_index < len(audio_tracks):
            track = audio_tracks[section_index]
            cursor = 0.0
            for child in track:
                if isinstance(child, otio.schema.Gap):
                    if child.source_range is not None:
                        cursor += child.source_range.duration.to_seconds()
                elif isinstance(child, otio.schema.Clip):
                    voice_timeline_start = cursor
                    if child.source_range is not None:
                        voice_source_in = child.source_range.start_time.to_seconds()
                        voice_duration = child.source_range.duration.to_seconds()
                        voice_timeline_end = cursor + voice_duration
                    cursor += voice_duration

        if abs(voice_source_in) > 0.001:
            errors.append(f"voiceover_source_in_sec={voice_source_in:.3f}, erwartet 0.0")
        if abs(voice_timeline_start - expected_start) > 0.1:
            errors.append(
                f"voiceover_timeline_start_sec={voice_timeline_start:.2f}, "
                f"erwartet {expected_start:.2f}"
            )
        if voice_timeline_end + 0.1 < expected_end:
            errors.append(
                f"voiceover_timeline_end_sec={voice_timeline_end:.2f} < "
                f"expected {expected_end:.2f}"
            )
        if outro_start is not None and outro_start + 0.05 < expected_end:
            errors.append(
                f"generic_outro_timeline_start_sec={outro_start:.2f} < "
                f"voiceover_timeline_end_sec={expected_end:.2f}"
            )

        opening_title_on_v2 = False
        opening_title_start: float | None = None
        v2_tracks = [
            track
            for track in timeline.tracks
            if track.kind == otio.schema.TrackKind.Video and track.name == "V2"
        ]
        section_title = next(
            (item for item in section_items if item.type == "opening_title"),
            None,
        )
        if section_title is not None:
            if not v2_tracks:
                errors.append("V2 fehlt — Opening Title nicht auf separater Spur.")
            else:
                cursor_v2 = section.video_start_sec
                for child in v2_tracks[0]:
                    if isinstance(child, otio.schema.Gap):
                        if child.source_range is not None:
                            cursor_v2 += child.source_range.duration.to_seconds()
                    elif isinstance(child, otio.schema.Clip):
                        if child.metadata.get("type") == "opening_title":
                            if abs(cursor_v2 - (section.video_start_sec + section_title.timeline_in_sec)) < 0.15:
                                opening_title_on_v2 = True
                                opening_title_start = cursor_v2
                        if child.source_range is not None:
                            cursor_v2 += child.source_range.duration.to_seconds()
                if not opening_title_on_v2:
                    errors.append("Opening Title nicht auf V2 gefunden.")

        reports.append(
            OtioReadbackReport(
                voiceover_timeline_start_sec=round(voice_timeline_start, 4),
                voiceover_source_in_sec=round(voice_source_in, 4),
                voiceover_duration_sec=round(voice_duration, 4),
                voiceover_timeline_end_sec=round(voice_timeline_end, 4),
                expected_voiceover_timeline_end_sec=round(expected_end, 4),
                audio_offset_sec=audio_offset_sec,
                generic_outro_timeline_start_sec=round(outro_start, 4) if outro_start else None,
                visual_coverage_until_sec=round(visual_coverage, 4),
                opening_title_on_v2=opening_title_on_v2,
                opening_title_timeline_start_sec=(
                    round(opening_title_start, 4) if opening_title_start is not None else None
                ),
                ok=not errors,
                errors=errors,
            )
        )
    return reports


@dataclass(frozen=True)
class OtioExportResult:
    path: Path
    aspect_fill_notes: list[str] = field(default_factory=list)
    readback_reports: list[OtioReadbackReport] = field(default_factory=list)


def export_otio_timeline(
    project: Project,
    merged: MergedEditPlanResult,
    *,
    output_path: Path | None = None,
    export_settings: OtioExportSettings | None = None,
) -> OtioExportResult:
    """Schreibt die zusammengeführte Timeline als .otio-Datei."""
    if not merged.ready:
        raise ValueError(
            "Export blockiert — Schnittplan validieren oder neu vorschlagen. "
            + "; ".join(merged.warnings[:5])
        )

    settings = export_settings or load_otio_export_settings(project)
    save_otio_export_settings(project, settings)

    rendered_items, _render_notes = ensure_opening_titles_rendered(project, merged.timeline_items)
    merged = MergedEditPlanResult(
        timeline_items=rendered_items,
        shots=merged.shots,
        settings=merged.settings,
        voiceovers=merged.voiceovers,
        included_folders=merged.included_folders,
        skipped_folders=merged.skipped_folders,
        warnings=list(merged.warnings) + _render_notes,
        validation_status=merged.validation_status,
    )

    export_rules = export_rule_options(load_edit_plan_rules(project))
    post_render_validation = validate_opening_titles(
        merged.timeline_items,
        opening_title_required=export_rules.folder_title_enabled,
        require_rendered_media=True,
    )
    if post_render_validation.errors:
        details = "; ".join(post_render_validation.errors[:5])
        raise ValueError(f"Opening-Title-Validierung nach Render fehlgeschlagen: {details}")

    media_issues = verify_timeline_media_paths(project, merged.timeline_items, strict=True)
    if media_issues:
        preview = "\n".join(f"• {line}" for line in media_issues[:12])
        extra = f"\n… und {len(media_issues) - 12} weitere" if len(media_issues) > 12 else ""
        raise ValueError(
            "Medien nicht exportierbar — Clean Media prüfen oder Schnittplan anpassen:\n"
            f"{preview}{extra}"
        )

    timeline = build_otio_timeline(project, merged, export_settings=settings)
    path = output_path or get_otio_export_path(project.work_dir_path, project.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    otio.adapters.write_to_file(timeline, str(path))
    aspect_notes = list(timeline.metadata.get("aspect_fill_notes", []))

    readback_timeline = otio.adapters.read_from_file(str(path))
    sections = _compute_timeline_sections(
        merged.timeline_items,
        merged.settings,
        merged.voiceovers,
    )
    readback_reports = validate_otio_readback(
        readback_timeline,
        sections=sections,
        items=merged.timeline_items,
        audio_offset_sec=settings.audio_offset_sec,
    )
    timeline.metadata["otio_readback"] = [
        {
            "voiceover_timeline_start_sec": report.voiceover_timeline_start_sec,
            "voiceover_source_in_sec": report.voiceover_source_in_sec,
            "voiceover_duration_sec": report.voiceover_duration_sec,
            "voiceover_timeline_end_sec": report.voiceover_timeline_end_sec,
            "expected_voiceover_timeline_end_sec": report.expected_voiceover_timeline_end_sec,
            "audio_offset_sec": report.audio_offset_sec,
            "generic_outro_timeline_start_sec": report.generic_outro_timeline_start_sec,
            "visual_coverage_until_sec": report.visual_coverage_until_sec,
            "opening_title_on_v2": report.opening_title_on_v2,
            "opening_title_timeline_start_sec": report.opening_title_timeline_start_sec,
            "ok": report.ok,
            "errors": report.errors,
        }
        for report in readback_reports
    ]
    otio.adapters.write_to_file(timeline, str(path))

    failed = [report for report in readback_reports if not report.ok]
    if failed:
        details = "; ".join(
            f"{report.errors[0]}" for report in failed if report.errors
        )
        raise ValueError(f"OTIO Readback fehlgeschlagen: {details}")

    return OtioExportResult(
        path=path,
        aspect_fill_notes=aspect_notes,
        readback_reports=readback_reports,
    )
