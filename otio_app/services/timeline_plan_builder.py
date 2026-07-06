"""Timeline-Items für Schnittplan und OTIO-Export."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from otio_app.analysis_models import (
    EditPlanSettings,
    EditPlanShot,
    TimelineItem,
    TimelineItemTransform,
    VoiceoverPlan,
)
from otio_app.services.duration_rules import MAX_DURATION_SEC, MIN_DURATION_SEC, split_total_duration
from otio_app.services.opening_title_renderer import DEFAULT_OPENING_TITLE_FONT
from otio_app.services.generic_outro_selector import (
    GenericAssetCandidate,
    asset_id_for_path,
    section_id_for_folder,
    select_generic_outro_assets,
)
from otio_app.services.media_utils import is_image_media, probe_duration_seconds

VISUAL_VIDEO_TYPES = frozenset(
    {"video_shot", "generic_narration_visual", "generic_outro_visual", "image_with_background"}
)
NARRATION_VISUAL_TYPES = frozenset({"video_shot", "image_shot", "generic_narration_visual"})


def _new_item_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:8]}"


def build_voiceover_plan(
    voice_file: str,
    settings: EditPlanSettings,
) -> VoiceoverPlan:
    """Erzeugt den Voice-over-Block mit ffprobe-Dauer und ohne Head-Trim."""
    wav_path = Path(voice_file)
    duration = probe_duration_seconds(wav_path) or 0.0
    offset = max(0.0, float(settings.audio_offset_sec))
    return VoiceoverPlan(
        path=voice_file,
        timeline_start_sec=offset,
        source_in_sec=0.0,
        source_out_sec=round(duration, 4),
        duration_sec=round(duration, 4),
        timeline_end_sec=round(offset + duration, 4),
        duration_source="ffprobe",
        trim_policy=settings.voiceover_trim_policy or "disabled",
    )


def _shot_to_timeline_item(
    shot: EditPlanShot,
    *,
    section_id: str,
    item_index: int,
    trim_leading_sec: float,
) -> TimelineItem:
    duration = round(float(shot.duration_sec), 4)
    asset_path = shot.asset_path or ""
    source_in = trim_leading_sec if asset_path and not is_image_media(Path(asset_path)) else 0.0
    source_out = source_in + duration
    item_type = "image_shot" if asset_path and is_image_media(Path(asset_path)) else "video_shot"
    confidence = 0.0
    if shot.confidence:
        try:
            confidence = float(shot.confidence)
        except ValueError:
            confidence = 0.5 if shot.confidence == "high" else 0.3

    return TimelineItem(
        timeline_item_id=f"item_{item_index:03d}",
        type=item_type,
        section_id=section_id,
        folder_name=shot.folder,
        voice_file=shot.voice_file,
        asset_id=asset_id_for_path(asset_path) if asset_path else "",
        shot_id=f"shot_{item_index:03d}",
        resolved_media_path=asset_path,
        original_asset_path=asset_path or None,
        asset_role="narration",
        duration_sec=duration,
        final_duration_sec=duration,
        source_in_sec=source_in,
        source_out_sec=source_out,
        voice_start_sec=shot.voice_start_sec,
        voice_end_sec=shot.voice_end_sec,
        selection_reason="Motiv aus Schnittplan-Vorschlag",
        confidence=confidence,
        transform=TimelineItemTransform(scaling_mode="fill"),
        media_source_type=shot.asset_source,
        motif=shot.motif,
        passage_text=shot.passage_text,
    )


def build_narration_filler_items(
    *,
    folder_name: str,
    voice_file: str,
    section_cursor_sec: float,
    target_end_sec: float,
    folder_assets: list[dict[str, str]],
    used_paths: set[str],
    last_asset_path: str | None,
    item_index_start: int,
    trim_leading_sec: float,
) -> tuple[list[TimelineItem], list[str]]:
    """Fügt generic_narration_visual-Elemente ein, bis target_end_sec erreicht ist."""
    errors: list[str] = []
    gap = target_end_sec - section_cursor_sec
    if gap <= 0.05:
        return [], errors

    section_id = section_id_for_folder(folder_name)
    remaining = gap
    items: list[TimelineItem] = []
    cursor = section_cursor_sec
    index = item_index_start

    while remaining > 0.05:
        chunk = min(remaining, MAX_DURATION_SEC)
        if chunk < MIN_DURATION_SEC and remaining > MIN_DURATION_SEC:
            chunk = MIN_DURATION_SEC
        elif chunk < MIN_DURATION_SEC:
            chunk = remaining

        candidates = select_generic_outro_assets(
            folder_assets,
            used_paths=used_paths,
            last_asset_path=last_asset_path,
            count=1,
        )
        if not candidates:
            errors.append(
                f"{folder_name}: kein generic_narration_visual verfügbar — "
                f"visuelle Abdeckung endet bei {section_cursor_sec:.1f}s, "
                f"Voice-over bis {target_end_sec:.1f}s."
            )
            break

        candidate: GenericAssetCandidate = candidates[0]
        source_in = trim_leading_sec
        media_duration = probe_duration_seconds(Path(candidate.path))
        play_duration = chunk
        source_out = source_in + play_duration
        if media_duration is not None:
            source_out = min(source_in + play_duration, media_duration)
            play_duration = source_out - source_in
            if play_duration < chunk - 0.05:
                candidate.warnings.append(
                    f"Medienlänge {media_duration:.1f}s — Filler gekürzt auf {play_duration:.1f}s"
                )

        timeline_in = cursor
        timeline_out = cursor + play_duration
        items.append(
            TimelineItem(
                timeline_item_id=_new_item_id("filler"),
                type="generic_narration_visual",
                section_id=section_id,
                folder_name=folder_name,
                voice_file=voice_file,
                asset_id=candidate.asset_id,
                shot_id=f"filler_{index:03d}",
                resolved_media_path=candidate.path,
                original_asset_path=candidate.path,
                asset_role="generic_narration_visual",
                timeline_in_sec=round(timeline_in, 4),
                timeline_out_sec=round(timeline_out, 4),
                duration_sec=round(play_duration, 4),
                final_duration_sec=round(play_duration, 4),
                source_in_sec=source_in,
                source_out_sec=round(source_out, 4),
                voice_start_sec=timeline_in,
                voice_end_sec=target_end_sec,
                selection_reason=candidate.selection_reason,
                confidence=candidate.score,
                transform=TimelineItemTransform(scaling_mode="fill"),
                warnings=list(candidate.warnings),
                media_source_type="local",
                motif="Visuelle Abdeckung bis Voice-over-Ende",
            )
        )
        used_paths.add(candidate.path)
        last_path = candidate.path
        cursor = timeline_out
        remaining = target_end_sec - cursor
        index += 1

    return items, errors


def build_outro_timeline_items(
    *,
    folder_name: str,
    voice_file: str,
    voice_end_sec: float,
    section_cursor_sec: float,
    outro_total_sec: float,
    folder_assets: list[dict[str, str]],
    used_paths: set[str],
    last_asset_path: str | None,
    item_index_start: int,
    trim_leading_sec: float,
) -> tuple[list[TimelineItem], list[str]]:
    """Erzeugt 1..n Outro-Elemente (je max. 8 s) mit explizit gewähltem Ordner-Asset."""
    errors: list[str] = []
    if outro_total_sec <= 0.05:
        return [], errors

    section_id = section_id_for_folder(folder_name)
    durations = split_total_duration(outro_total_sec)
    candidates = select_generic_outro_assets(
        folder_assets,
        used_paths=used_paths,
        last_asset_path=last_asset_path,
        count=len(durations),
    )
    if len(candidates) < len(durations):
        errors.append(
            f"{folder_name}: nicht genug geeignete Outro-Assets "
            f"({len(candidates)}/{len(durations)} benötigt)."
        )
        return [], errors

    items: list[TimelineItem] = []
    cursor = section_cursor_sec
    for offset, duration in enumerate(durations):
        candidate: GenericAssetCandidate = candidates[offset % len(candidates)]
        source_in = trim_leading_sec
        media_duration = probe_duration_seconds(Path(candidate.path))
        if media_duration is not None:
            source_out = min(source_in + duration, media_duration)
            if source_out - source_in < duration - 0.05:
                candidate.warnings.append(
                    f"Medienlänge {media_duration:.1f}s — Outro gekürzt auf "
                    f"{source_out - source_in:.1f}s"
                )
        else:
            source_out = source_in + duration

        timeline_in = cursor
        timeline_out = cursor + duration
        items.append(
            TimelineItem(
                timeline_item_id=_new_item_id("outro"),
                type="generic_outro_visual",
                section_id=section_id,
                folder_name=folder_name,
                voice_file=voice_file,
                asset_id=candidate.asset_id,
                shot_id=f"outro_{item_index_start + offset:03d}",
                resolved_media_path=candidate.path,
                original_asset_path=candidate.path,
                asset_role="generic_section_outro",
                timeline_in_sec=round(timeline_in, 4),
                timeline_out_sec=round(timeline_out, 4),
                duration_sec=duration,
                final_duration_sec=duration,
                source_in_sec=source_in,
                source_out_sec=round(source_out, 4),
                voice_start_sec=voice_end_sec,
                voice_end_sec=voice_end_sec,
                selection_reason=candidate.selection_reason,
                confidence=candidate.score,
                transform=TimelineItemTransform(scaling_mode="fill"),
                warnings=list(candidate.warnings),
                media_source_type="local",
                motif="Ausklingen",
            )
        )
        cursor = timeline_out
    return items, errors


def build_timeline_items_for_folder(
    narration_shots: list[EditPlanShot],
    *,
    folder_name: str,
    voice_file: str,
    settings: EditPlanSettings,
    folder_assets: list[dict[str, str]],
    trim_leading_sec: float = 0.0,
    item_index_start: int = 1,
    opening_title_enabled: bool = False,
    opening_title_font: str = DEFAULT_OPENING_TITLE_FONT,
    opening_title_duration_sec: float = 5.0,
    opening_title_font_size: float | None = None,
    work_dir: Path | None = None,
    project: Project | None = None,
) -> tuple[list[TimelineItem], VoiceoverPlan, list[str]]:
    """Baut alle Timeline-Items einer Sektion (Titel + Narration + Filler + Outro)."""
    errors: list[str] = []
    section_id = section_id_for_folder(folder_name)
    items: list[TimelineItem] = []
    cursor = 0.0
    used_paths: set[str] = set()
    last_path: str | None = None

    if opening_title_enabled and work_dir is not None and project is not None:
        from otio_app.services.opening_title_renderer import build_opening_title_item

        title_item = build_opening_title_item(
            folder_name=folder_name,
            voice_file=voice_file,
            section_id=section_id,
            work_dir=work_dir,
            project=project,
            requested_font_family=opening_title_font,
            duration_sec=opening_title_duration_sec,
            font_size_px=opening_title_font_size,
        )
        items.append(title_item)
        if title_item.warnings:
            errors.extend(f"{folder_name}: {w}" for w in title_item.warnings)

    voiceover = build_voiceover_plan(voice_file, settings)

    narration = [shot for shot in narration_shots if not shot.section_outro]
    for index, shot in enumerate(narration, start=item_index_start):
        item = _shot_to_timeline_item(
            shot,
            section_id=section_id,
            item_index=index,
            trim_leading_sec=trim_leading_sec,
        )
        item.timeline_in_sec = round(cursor, 4)
        item.timeline_out_sec = round(cursor + item.duration_sec, 4)
        cursor = item.timeline_out_sec
        if shot.asset_path:
            used_paths.add(shot.asset_path)
            last_path = shot.asset_path
        items.append(item)

    visual_narration_end = cursor
    if visual_narration_end + 0.05 < voiceover.timeline_end_sec:
        filler_items, filler_errors = build_narration_filler_items(
            folder_name=folder_name,
            voice_file=voice_file,
            section_cursor_sec=visual_narration_end,
            target_end_sec=voiceover.timeline_end_sec,
            folder_assets=folder_assets,
            used_paths=used_paths,
            last_asset_path=last_path,
            item_index_start=item_index_start + len(narration),
            trim_leading_sec=trim_leading_sec,
        )
        errors.extend(filler_errors)
        items.extend(filler_items)
        if filler_items:
            cursor = filler_items[-1].timeline_out_sec
            last_path = filler_items[-1].resolved_media_path or last_path

    outro_start = voiceover.timeline_end_sec
    outro_items, outro_errors = build_outro_timeline_items(
        folder_name=folder_name,
        voice_file=voice_file,
        voice_end_sec=voiceover.timeline_end_sec,
        section_cursor_sec=outro_start,
        outro_total_sec=settings.section_outro_sec,
        folder_assets=folder_assets,
        used_paths=used_paths,
        last_asset_path=last_path,
        item_index_start=item_index_start + len(narration) + len(
            [i for i in items if i.type == "generic_narration_visual"]
        ),
        trim_leading_sec=trim_leading_sec,
    )
    errors.extend(outro_errors)
    items.extend(outro_items)
    return items, voiceover, errors


def assign_global_timeline_positions(
    items: list[TimelineItem],
    *,
    section_start_sec: float,
) -> list[TimelineItem]:
    """Verschiebt lokale timeline_in/out um section_start_sec — ohne Neu-Packen."""
    if not items:
        return []
    local_base = items[0].timeline_in_sec
    shift = section_start_sec - local_base
    positioned: list[TimelineItem] = []
    for item in items:
        timeline_in = item.timeline_in_sec + shift
        duration = item.duration_sec
        positioned.append(
            item.model_copy(
                update={
                    "timeline_in_sec": round(timeline_in, 4),
                    "timeline_out_sec": round(timeline_in + duration, 4),
                }
            )
        )
    return positioned


def shots_from_timeline_items(items: list[TimelineItem]) -> list[EditPlanShot]:
    """Legacy-Kompatibilität: Shots-Liste für UI aus Timeline-Items."""
    shots: list[EditPlanShot] = []
    for item in items:
        if item.type == "generic_outro_visual":
            shots.append(
                EditPlanShot(
                    voice_file=item.voice_file,
                    folder=item.folder_name,
                    voice_start_sec=item.voice_start_sec,
                    voice_end_sec=item.voice_end_sec,
                    duration_sec=item.duration_sec,
                    asset_path=item.resolved_media_path or None,
                    asset_source=item.media_source_type,
                    motif=item.motif,
                    passage_text=item.passage_text,
                    section_outro=True,
                )
            )
        elif item.type in {"video_shot", "image_shot", "generic_narration_visual"}:
            shots.append(
                EditPlanShot(
                    voice_file=item.voice_file,
                    folder=item.folder_name,
                    voice_start_sec=item.voice_start_sec,
                    voice_end_sec=item.voice_end_sec,
                    duration_sec=item.duration_sec,
                    asset_path=item.resolved_media_path or None,
                    asset_source=item.media_source_type,
                    motif=item.motif,
                    passage_text=item.passage_text,
                    confidence=str(item.confidence) if item.confidence else None,
                )
            )
    return shots
