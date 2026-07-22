"""Python-Finalisierung: Anchors → Sekunden, Validierung, Reparaturprotokoll."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from otio_app.models import Project
from otio_app.services.generic_outro_selector import asset_id_for_path
from otio_app.services.inventory_loader import load_folder_inventory
from otio_app.services.media_utils import is_image_media, probe_duration_seconds
from otio_app.services.without_voiceover_enhanced.audio_timing_service import (
    load_segment_timings,
)
from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
    load_cut_plan_options,
    resolve_timing_seconds,
)
from otio_app.services.without_voiceover_enhanced.io_utils import load_model, write_json
from otio_app.services.without_voiceover_enhanced.local_media_service import (
    STATUS_EXPORT_READY,
    is_http_url,
    list_export_ready_supplements,
    refresh_supplement_validation,
)
from otio_app.services.without_voiceover_enhanced.cut_rhythm_validator import (
    assess_cut_rhythm,
)
from otio_app.services.without_voiceover_enhanced.models import (
    AcceptedSupplementsDocument,
    FinalCutPlanDocument,
    NarrationAnchor,
    NarrationTimelineDocument,
    NarrationTimelineEntry,
    ResolvedAudioSegment,
    ResolvedShot,
    ResolvedTimelineDocument,
    SentenceTiming,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    accepted_supplements_path,
    final_cut_plan_path,
    narration_timeline_path,
    repair_log_path,
    resolved_timeline_path,
)
from otio_app.services.without_voiceover_enhanced.pause_config import (
    voiceover_pauses_enabled,
)
from otio_app.services.without_voiceover_enhanced.pause_resolver import (
    build_narration_timeline,
    source_seconds_to_timeline,
)
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    require_locked_script,
)
from otio_app.services.without_voiceover_enhanced.segment_alignment_service import (
    load_segment_alignments,
)
from otio_app.services.without_voiceover_enhanced.sentence_timing_prompt import (
    sentence_index_by_id,
)

# Technisches Minimum (Frame-Sicherheit); redaktionelle min/max kommen aus Settings.
TECH_MIN_SHOT_SECONDS = 0.4
TECH_MAX_SHOT_SECONDS = 120.0


class TimelineResolveError(RuntimeError):
    pass


def _asset_catalog(project: Project) -> dict[str, dict]:
    catalog: dict[str, dict] = {}
    for folder in project.selected_asset_subdirs:
        inventory = load_folder_inventory(project, folder)
        if inventory is None:
            continue
        for asset in getattr(inventory, "assets", []) or []:
            path = getattr(asset, "path", None) or getattr(asset, "source_path", None)
            if path is None:
                continue
            asset_id = getattr(asset, "asset_id", None) or asset_id_for_path(str(path))
            duration = getattr(asset, "duration_sec", None)
            if duration is None:
                duration = getattr(asset, "duration_seconds", None)
            if duration is None:
                duration = probe_duration_seconds(Path(path))
            usable_in = getattr(asset, "usable_in_s", None)
            media_type = getattr(asset, "media_type", None) or (
                "photo" if is_image_media(Path(path)) else "video"
            )
            catalog[str(asset_id)] = {
                "path": str(path),
                "duration_seconds": float(duration) if duration else None,
                "usable_in_s": float(usable_in) if usable_in is not None else None,
                "folder": folder,
                "media_type": str(media_type or "video").lower(),
            }
    # Only export_ready supplements are technically available assets.
    # Selected-but-missing remain visible in UI/search, but not in the catalog.
    accepted = load_model(accepted_supplements_path(project), AcceptedSupplementsDocument)
    if accepted is not None:
        for supplement in accepted.supplements:
            refreshed = refresh_supplement_validation(supplement)
            if refreshed.media_validation_status != STATUS_EXPORT_READY:
                continue
            local_path = str(refreshed.local_media_path or "").strip()
            if not local_path or is_http_url(local_path):
                continue
            media_type = (refreshed.media_type or "photo").lower()
            catalog[supplement.candidate_id] = {
                "path": local_path,
                "duration_seconds": refreshed.duration_seconds,
                "supplement": True,
                "export_ready": True,
                "folder": "",
                "media_type": media_type,
            }
    for supplement in list_export_ready_supplements(project):
        local_path = str(supplement.local_media_path or "").strip()
        if local_path and not is_http_url(local_path):
            catalog.setdefault(
                supplement.candidate_id,
                {
                    "path": local_path,
                    "duration_seconds": supplement.duration_seconds,
                    "supplement": True,
                    "export_ready": True,
                    "folder": "",
                    "media_type": (supplement.media_type or "photo").lower(),
                },
            )
    return catalog


def _is_intro_folder(folder: str | None) -> bool:
    name = (folder or "").strip().lower()
    return name in {"intro", "introduction"} or name.startswith("intro_")


def _entry_audio_duration(entry: NarrationTimelineEntry) -> float:
    if entry.audio_duration_seconds is not None:
        return max(0.0, float(entry.audio_duration_seconds))
    span = max(0.0, float(entry.end_seconds) - float(entry.start_seconds))
    for pause in entry.intra_pauses:
        span = max(0.0, span - float(pause.pause_seconds))
    return span


def _anchor_to_seconds(
    timeline: NarrationTimelineDocument,
    anchor: NarrationAnchor,
    *,
    sentence_index: dict[str, SentenceTiming],
) -> float:
    entry_map = {entry.segment_id: entry for entry in timeline.entries}
    entry = entry_map.get(anchor.segment_id)
    if entry is None:
        raise TimelineResolveError(f"Unbekannte Segment-ID: {anchor.segment_id}")

    sentence_id = str(anchor.sentence_id or "").strip()
    if sentence_id:
        sentence = sentence_index.get(sentence_id)
        if sentence is None:
            raise TimelineResolveError(f"Unbekannte Sentence-ID: {sentence_id}")
        if sentence.segment_id != anchor.segment_id:
            raise TimelineResolveError(
                f"Sentence {sentence_id} gehört zu {sentence.segment_id}, "
                f"nicht zu {anchor.segment_id}."
            )
        span = max(0.0, float(sentence.end_seconds) - float(sentence.start_seconds))
        offset = max(0.0, min(float(anchor.offset_seconds), span))
        source = float(sentence.start_seconds) + offset
        return source_seconds_to_timeline(entry, source)

    audio_dur = _entry_audio_duration(entry)
    offset = max(0.0, min(float(anchor.offset_seconds), audio_dur))
    return source_seconds_to_timeline(entry, offset)


def _build_resolved_audio_segments(
    *,
    timeline: NarrationTimelineDocument,
    timing_map: dict,
) -> list[ResolvedAudioSegment]:
    """Segment-MP3s; Intra-Pausen → Silence-Mid-Split + Gap (kein Time-Stretch)."""
    audio_segments: list[ResolvedAudioSegment] = []
    for entry in timeline.entries:
        timing = timing_map.get(entry.segment_id)
        if timing is None:
            continue
        audio_path = timing.audio_path
        audio_dur = _entry_audio_duration(entry)
        intra = sorted(entry.intra_pauses, key=lambda p: p.source_split_seconds)
        if not intra:
            audio_segments.append(
                ResolvedAudioSegment(
                    segment_id=entry.segment_id,
                    audio_path=audio_path,
                    timeline_start_seconds=entry.start_seconds,
                    timeline_end_seconds=entry.end_seconds,
                    pause_after_seconds=entry.pause_after_seconds,
                    source_start_seconds=0.0,
                    source_end_seconds=round(audio_dur, 6),
                )
            )
            continue

        source_cursor = 0.0
        timeline_cursor = float(entry.start_seconds)
        for pause in intra:
            split = max(source_cursor, min(float(pause.source_split_seconds), audio_dur))
            piece_dur = max(0.0, split - source_cursor)
            audio_segments.append(
                ResolvedAudioSegment(
                    segment_id=entry.segment_id,
                    audio_path=audio_path,
                    timeline_start_seconds=round(timeline_cursor, 6),
                    timeline_end_seconds=round(timeline_cursor + piece_dur, 6),
                    pause_after_seconds=round(float(pause.pause_seconds), 6),
                    source_start_seconds=round(source_cursor, 6),
                    source_end_seconds=round(split, 6),
                    split_label=f"after:{pause.after_sentence_id}",
                )
            )
            timeline_cursor += piece_dur + float(pause.pause_seconds)
            source_cursor = split

        remainder = max(0.0, audio_dur - source_cursor)
        audio_segments.append(
            ResolvedAudioSegment(
                segment_id=entry.segment_id,
                audio_path=audio_path,
                timeline_start_seconds=round(timeline_cursor, 6),
                timeline_end_seconds=round(timeline_cursor + remainder, 6),
                pause_after_seconds=entry.pause_after_seconds,
                source_start_seconds=round(source_cursor, 6),
                source_end_seconds=round(audio_dur, 6),
                split_label="tail",
            )
        )
    return audio_segments


def _seconds_to_frame(seconds: float, fps: float) -> float:
    """Frame-Rundung: snappe auf Framegrenze (deterministisch)."""
    frame = round(seconds * fps)
    return round(frame / fps, 6)


def detect_one_to_one_sentence_asset(final: FinalCutPlanDocument, segment_count: int) -> bool:
    """True wenn Shotanzahl == Segmentanzahl und jeder Shot genau ein Segment spannt."""
    if len(final.shots) != segment_count or segment_count == 0:
        return False
    return all(
        shot.narration_start_anchor.segment_id == shot.narration_end_anchor.segment_id
        and shot.narration_start_anchor.offset_seconds == 0.0
        for shot in final.shots
    )


def resolve_final_timeline(project: Project) -> ResolvedTimelineDocument:
    locked = require_locked_script(project)
    final = load_model(final_cut_plan_path(project), FinalCutPlanDocument)
    timeline = load_model(narration_timeline_path(project), NarrationTimelineDocument)
    timings = load_segment_timings(project)
    if final is None:
        raise TimelineResolveError("Finaler Cut Plan fehlt.")
    if timeline is None:
        raise TimelineResolveError("Narrationstimeline fehlt.")
    if timings is None:
        raise TimelineResolveError("Segment-Timings fehlen.")

    errors: list[str] = []
    repairs: list[str] = []
    catalog = _asset_catalog(project)
    options = load_cut_plan_options(project)
    editorial_min = max(TECH_MIN_SHOT_SECONDS, float(options.shot_min_sec))
    editorial_max = min(
        TECH_MAX_SHOT_SECONDS,
        max(editorial_min, float(options.shot_max_sec)),
    )
    head_trim = max(0.0, float(options.video_head_trim_sec))
    short_tolerance = max(0.0, float(options.short_asset_tolerance_sec))
    known_segments = {s.segment_id for s in locked.segments}
    intro_segment_ids = {
        s.segment_id
        for s in locked.segments
        if _is_intro_folder(s.folder_name)
    }
    fps = float(project.fps)
    preroll = resolve_timing_seconds(
        mode=options.voiceover_preroll_mode,
        setting_max=options.voiceover_preroll_sec,
        llm_value=final.voiceover_preroll_sec,
    )
    postroll = resolve_timing_seconds(
        mode=options.voiceover_postroll_mode,
        setting_max=options.voiceover_postroll_sec,
        llm_value=final.voiceover_postroll_sec,
    )

    # One-sentence-one-asset is allowed when editorial; no hard reject.
    # Kept as an optional note for debugging / transparency only.
    if detect_one_to_one_sentence_asset(final, len(locked.segments)):
        repairs.append(
            "Hinweis: Shotstruktur ist durchgängig 1 Segment → 1 Shot "
            "(erlaubt, aber oft weniger abwechslungsreich)."
        )

    timing_map = {item.segment_id: item for item in timings.segments}
    sentence_index = sentence_index_by_id(load_segment_alignments(project))
    # Pausen temporär aus: durchgehende VO-Timeline aus Segment-Timings,
    # unabhängig von gespeicherten pause_directives / intra_pauses.
    if not voiceover_pauses_enabled():
        timeline = build_narration_timeline(
            script_version=locked.script_version,
            segment_timings=timings.segments,
            pause_directives=[],
            sentence_index=sentence_index,
        )
        repairs.append(
            "Voice-over-Pausen deaktiviert — Narration durchgehend ohne Gaps."
        )
    audio_segments = _build_resolved_audio_segments(
        timeline=timeline,
        timing_map=timing_map,
    )

    resolved_shots: list[ResolvedShot] = []
    for shot in final.shots:
        if shot.narration_start_anchor.segment_id not in known_segments:
            errors.append(f"Unbekannte Segment-ID: {shot.narration_start_anchor.segment_id}")
            continue
        if shot.narration_end_anchor.segment_id not in known_segments:
            errors.append(f"Unbekannte Segment-ID: {shot.narration_end_anchor.segment_id}")
            continue
        if shot.asset_id not in catalog:
            accepted = load_model(
                accepted_supplements_path(project), AcceptedSupplementsDocument
            )
            if accepted is not None and any(
                s.candidate_id == shot.asset_id for s in accepted.supplements
            ):
                errors.append(
                    f"Supplement {shot.asset_id} ist nicht export_ready "
                    "(lokale Mediendatei fehlt oder ist ungültig)."
                )
            else:
                errors.append(f"Unbekannte Asset-ID: {shot.asset_id}")
            continue
        if is_http_url(str(catalog[shot.asset_id].get("path") or "")):
            errors.append(
                f"Asset {shot.asset_id} besitzt eine Web-URL statt lokaler Datei."
            )
            continue

        try:
            start = _anchor_to_seconds(
                timeline,
                shot.narration_start_anchor,
                sentence_index=sentence_index,
            )
            end = _anchor_to_seconds(
                timeline,
                shot.narration_end_anchor,
                sentence_index=sentence_index,
            )
        except TimelineResolveError as exc:
            errors.append(str(exc))
            continue
        start = _seconds_to_frame(start, fps)
        end = _seconds_to_frame(end, fps)
        if end <= start:
            end = _seconds_to_frame(start + editorial_min, fps)
            repairs.append(
                f"{shot.shot_id}: Ende vor/gleich Start — auf Mindestlänge "
                f"{editorial_min}s gesetzt."
            )
        duration = end - start
        if duration < editorial_min:
            end = _seconds_to_frame(start + editorial_min, fps)
            repairs.append(
                f"{shot.shot_id}: unter shot_min ({editorial_min}s) — verlängert."
            )
            duration = end - start
        if duration > editorial_max:
            end = _seconds_to_frame(start + editorial_max, fps)
            repairs.append(
                f"{shot.shot_id}: über shot_max ({editorial_max}s) — gekürzt."
            )
            duration = end - start

        entry = catalog[shot.asset_id]
        media_duration = entry.get("duration_seconds")
        media_type = str(entry.get("media_type") or "").lower()
        is_video = media_type in {"video"} or (
            media_type not in {"photo", "image"}
            and media_duration is not None
            and float(media_duration) > 0
            and not is_image_media(Path(str(entry.get("path") or "")))
        )

        if media_duration is not None and media_duration < duration:
            shortfall = float(duration) - float(media_duration)
            if shortfall <= short_tolerance + 1e-6:
                repairs.append(
                    f"{shot.shot_id}: Asset {shot.asset_id} {shortfall:.2f}s zu kurz "
                    f"— Toleranz {short_tolerance:.1f}s, Shot auf Mediendauer gekürzt."
                )
                end = _seconds_to_frame(start + float(media_duration), fps)
                duration = end - start
            else:
                errors.append(
                    f"Asset {shot.asset_id} ist kürzer als gewünschter Shot "
                    f"({media_duration}s < {duration}s; Toleranz {short_tolerance:.1f}s)."
                )
                continue

        if media_duration is None or media_duration <= 0:
            # Stills / unknown duration: hold for shot length.
            source_start = 0.0
            source_end = duration
        else:
            usable_in = entry.get("usable_in_s")
            trim = 0.0
            if is_video:
                trim = head_trim
                if usable_in is not None:
                    trim = max(trim, max(0.0, float(usable_in)))
            if trim >= float(media_duration):
                errors.append(
                    f"Asset {shot.asset_id}: Head-Trim/usable_in ({trim}s) "
                    f">= Mediendauer ({media_duration}s)."
                )
                continue
            usable = float(media_duration) - trim
            if duration > usable + 1e-6:
                shortfall = duration - usable
                if shortfall <= short_tolerance + 1e-6:
                    repairs.append(
                        f"{shot.shot_id}: nutzbare Dauer knapp "
                        f"({shortfall:.2f}s) — Toleranz, Shot gekürzt."
                    )
                    end = _seconds_to_frame(start + usable, fps)
                    duration = end - start
                else:
                    errors.append(
                        f"Source-Range für {shot.asset_id} würde nutzbare "
                        f"Mediendauer überschreiten "
                        f"(shot {duration}s > usable {usable}s nach Head-Trim/"
                        f"usable_in_s)."
                    )
                    continue
            source_start = trim + max(0.0, (usable - duration) / 2.0)
            source_end = source_start + duration
            if source_end > float(media_duration) + 1e-6:
                errors.append(
                    f"Source-Range außerhalb der Mediendauer für {shot.asset_id}."
                )
                continue

        resolved_shots.append(
            ResolvedShot(
                shot_id=shot.shot_id,
                asset_id=shot.asset_id,
                timeline_start_seconds=start,
                timeline_end_seconds=end,
                source_start_seconds=round(source_start, 6),
                source_end_seconds=round(source_end, 6),
                editorial_function=shot.editorial_function,
                may_overlap_pause=shot.may_overlap_pause,
            )
        )

    # Overlap / gap checks (deterministic, non-silent).
    ordered = sorted(resolved_shots, key=lambda s: (s.timeline_start_seconds, s.shot_id))

    # Vorlauf: non-Intro-VO später; erster non-Intro-Shot beginnt früher (Bild vor Ton).
    if preroll > 0 and ordered:
        shot_start_segments = {
            shot.shot_id: next(
                (
                    s.narration_start_anchor.segment_id
                    for s in final.shots
                    if s.shot_id == shot.shot_id
                ),
                "",
            )
            for shot in ordered
        }
        for audio in audio_segments:
            if audio.segment_id in intro_segment_ids:
                continue
            audio.timeline_start_seconds = round(
                audio.timeline_start_seconds + preroll, 6
            )
            audio.timeline_end_seconds = round(
                audio.timeline_end_seconds + preroll, 6
            )
        first_main_index: int | None = None
        for index, shot in enumerate(ordered):
            seg_id = shot_start_segments.get(shot.shot_id, "")
            if seg_id in intro_segment_ids:
                continue
            shot.timeline_start_seconds = round(
                shot.timeline_start_seconds + preroll, 6
            )
            shot.timeline_end_seconds = round(
                shot.timeline_end_seconds + preroll, 6
            )
            if first_main_index is None:
                first_main_index = index
        if first_main_index is not None:
            first = ordered[first_main_index]
            first.timeline_start_seconds = round(
                max(0.0, first.timeline_start_seconds - preroll), 6
            )
            hold_duration = first.timeline_end_seconds - first.timeline_start_seconds
            media_duration = catalog.get(first.asset_id, {}).get("duration_seconds")
            if media_duration is None or float(media_duration or 0) <= 0:
                first.source_end_seconds = round(
                    first.source_start_seconds + hold_duration, 6
                )
        repairs.append(
            f"Voice-over-Vorlauf {preroll:.2f}s angewendet (Intro unverschoben)."
        )

    # Nachlauf: letzten Shot verlängern (Hold).
    if postroll > 0 and ordered:
        last = ordered[-1]
        last.timeline_end_seconds = round(last.timeline_end_seconds + postroll, 6)
        hold_duration = last.timeline_end_seconds - last.timeline_start_seconds
        media_duration = catalog.get(last.asset_id, {}).get("duration_seconds")
        if media_duration is None or float(media_duration or 0) <= 0:
            last.source_end_seconds = round(
                last.source_start_seconds + hold_duration, 6
            )
        else:
            # Video: so weit wie möglich in der Source mitgehen, Rest als Hold-Ende.
            max_end = float(media_duration)
            desired_end = last.source_start_seconds + hold_duration
            last.source_end_seconds = round(min(max_end, desired_end), 6)
        repairs.append(f"Voice-over-Nachlauf {postroll:.2f}s am letzten Shot.")
    for prev, curr in zip(ordered, ordered[1:]):
        if curr.timeline_start_seconds < prev.timeline_end_seconds - 1e-6:
            if not (prev.may_overlap_pause or curr.may_overlap_pause):
                errors.append(
                    f"Shotüberlappung: {prev.shot_id} und {curr.shot_id}"
                )
            else:
                repairs.append(
                    f"Überlappung {prev.shot_id}/{curr.shot_id} wegen may_overlap_pause belassen."
                )
        gap = curr.timeline_start_seconds - prev.timeline_end_seconds
        if gap > 0.05:
            # Keine schwarzen Lücken auf der Video-Spur: vorherigen Shot halten.
            prev.timeline_end_seconds = round(curr.timeline_start_seconds, 6)
            hold_duration = (
                prev.timeline_end_seconds - prev.timeline_start_seconds
            )
            media_duration = catalog.get(prev.asset_id, {}).get("duration_seconds")
            if media_duration is None or float(media_duration or 0) <= 0:
                prev.source_end_seconds = round(
                    prev.source_start_seconds + hold_duration, 6
                )
            else:
                max_end = float(media_duration)
                desired_end = prev.source_start_seconds + hold_duration
                prev.source_end_seconds = round(min(max_end, desired_end), 6)
            repairs.append(
                f"Visuelle Lücke {gap:.3f}s zwischen {prev.shot_id} und "
                f"{curr.shot_id} — vorherigen Shot gehalten (kein Video-Gap)."
            )

    # Max asset usage (Intro zählt nicht).
    usage_counts = Counter(shot.asset_id for shot in ordered)
    for asset_id, count in sorted(usage_counts.items()):
        folder = str((catalog.get(asset_id) or {}).get("folder") or "")
        if _is_intro_folder(folder):
            continue
        if count > int(options.max_asset_usage):
            errors.append(
                f"Asset {asset_id} wird {count}× genutzt "
                f"(max_asset_usage={options.max_asset_usage}; Intro zählt nicht)."
            )

    # Wiederverwendungsabstand (soft: nur Repair/Hinweis).
    reuse_distance = int(options.min_asset_reuse_distance_shots)
    if reuse_distance > 0:
        last_index: dict[str, int] = {}
        for index, shot in enumerate(ordered):
            folder = str((catalog.get(shot.asset_id) or {}).get("folder") or "")
            if _is_intro_folder(folder):
                continue
            prev_index = last_index.get(shot.asset_id)
            if prev_index is not None:
                gap_shots = index - prev_index - 1
                if gap_shots < reuse_distance:
                    repairs.append(
                        f"{shot.shot_id}: Asset {shot.asset_id} erneut nach "
                        f"{gap_shots} Shots (min Abstand {reuse_distance})."
                    )
            last_index[shot.asset_id] = index

    repairs.extend(assess_cut_rhythm(final, ordered))

    total = timeline.total_duration_seconds + preroll + postroll
    if ordered:
        total = max(total, ordered[-1].timeline_end_seconds)
    if audio_segments:
        total = max(
            total,
            max(a.timeline_end_seconds + a.pause_after_seconds for a in audio_segments),
        )

    document = ResolvedTimelineDocument(
        script_version=locked.script_version,
        fps=fps,
        total_duration_seconds=round(total, 6),
        audio_segments=audio_segments,
        shots=ordered,
        voiceover_preroll_sec=round(preroll, 6),
        voiceover_postroll_sec=round(postroll, 6),
        repairs=repairs,
        errors=errors,
    )
    write_json(resolved_timeline_path(project), document)
    write_json(repair_log_path(project), {"repairs": repairs, "errors": errors})
    if errors:
        raise TimelineResolveError("; ".join(errors))
    return document
