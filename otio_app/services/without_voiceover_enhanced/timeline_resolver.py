"""Python-Finalisierung: Anchors → Sekunden, Validierung, Reparaturprotokoll."""

from __future__ import annotations

from pathlib import Path

from otio_app.models import Project
from otio_app.services.generic_outro_selector import asset_id_for_path
from otio_app.services.inventory_loader import load_folder_inventory
from otio_app.services.media_utils import probe_duration_seconds
from otio_app.services.without_voiceover_enhanced.audio_timing_service import (
    load_segment_timings,
)
from otio_app.services.without_voiceover_enhanced.io_utils import load_model, write_json
from otio_app.services.without_voiceover_enhanced.local_media_service import (
    STATUS_EXPORT_READY,
    is_http_url,
    list_export_ready_supplements,
    refresh_supplement_validation,
)
from otio_app.services.without_voiceover_enhanced.models import (
    AcceptedSupplementsDocument,
    FinalCutPlanDocument,
    NarrationTimelineDocument,
    ResolvedAudioSegment,
    ResolvedShot,
    ResolvedTimelineDocument,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    accepted_supplements_path,
    final_cut_plan_path,
    narration_timeline_path,
    repair_log_path,
    resolved_timeline_path,
)
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    require_locked_script,
)

MIN_SHOT_SECONDS = 0.4
MAX_SHOT_SECONDS = 120.0


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
            catalog[str(asset_id)] = {
                "path": str(path),
                "duration_seconds": float(duration) if duration else None,
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
            catalog[supplement.candidate_id] = {
                "path": local_path,
                "duration_seconds": refreshed.duration_seconds,
                "supplement": True,
                "export_ready": True,
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
                },
            )
    return catalog


def _anchor_to_seconds(
    timeline: NarrationTimelineDocument,
    segment_id: str,
    offset_seconds: float,
) -> float:
    entry_map = {entry.segment_id: entry for entry in timeline.entries}
    entry = entry_map.get(segment_id)
    if entry is None:
        raise TimelineResolveError(f"Unbekannte Segment-ID: {segment_id}")
    span = entry.end_seconds - entry.start_seconds
    offset = max(0.0, min(float(offset_seconds), span))
    return round(entry.start_seconds + offset, 6)


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
    known_segments = {s.segment_id for s in locked.segments}
    fps = float(project.fps)

    if detect_one_to_one_sentence_asset(final, len(locked.segments)):
        errors.append(
            "Finaler Plan enthält keine freie Shotstruktur "
            "(Eins-zu-eins Satz/Segment → Asset erkannt)."
        )

    timing_map = {item.segment_id: item for item in timings.segments}
    audio_segments = [
        ResolvedAudioSegment(
            segment_id=entry.segment_id,
            audio_path=timing_map[entry.segment_id].audio_path,
            timeline_start_seconds=entry.start_seconds,
            timeline_end_seconds=entry.end_seconds,
            pause_after_seconds=entry.pause_after_seconds,
        )
        for entry in timeline.entries
        if entry.segment_id in timing_map
    ]

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

        start = _anchor_to_seconds(
            timeline,
            shot.narration_start_anchor.segment_id,
            shot.narration_start_anchor.offset_seconds,
        )
        end = _anchor_to_seconds(
            timeline,
            shot.narration_end_anchor.segment_id,
            shot.narration_end_anchor.offset_seconds,
        )
        start = _seconds_to_frame(start, fps)
        end = _seconds_to_frame(end, fps)
        if end <= start:
            end = _seconds_to_frame(start + MIN_SHOT_SECONDS, fps)
            repairs.append(
                f"{shot.shot_id}: Ende vor/gleich Start — auf Mindestlänge {MIN_SHOT_SECONDS}s gesetzt."
            )
        duration = end - start
        if duration < MIN_SHOT_SECONDS:
            end = _seconds_to_frame(start + MIN_SHOT_SECONDS, fps)
            repairs.append(f"{shot.shot_id}: unter Mindestlänge — verlängert.")
            duration = end - start
        if duration > MAX_SHOT_SECONDS:
            end = _seconds_to_frame(start + MAX_SHOT_SECONDS, fps)
            repairs.append(f"{shot.shot_id}: über Maximallänge — gekürzt.")
            duration = end - start

        media_duration = catalog[shot.asset_id].get("duration_seconds")
        if media_duration is not None and media_duration < duration:
            errors.append(
                f"Asset {shot.asset_id} ist kürzer als gewünschter Shot "
                f"({media_duration}s < {duration}s)."
            )
            continue

        if media_duration is None or media_duration <= 0:
            # Stills / unknown duration: hold for shot length.
            source_start = 0.0
            source_end = duration
        else:
            # representative middle section
            usable = media_duration
            if duration > usable:
                errors.append(
                    f"Source-Range für {shot.asset_id} würde Mediendauer überschreiten."
                )
                continue
            source_start = max(0.0, (usable - duration) / 2.0)
            source_end = source_start + duration
            if source_end > usable + 1e-6:
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
            # Unintended visual gap (audio pauses are separate).
            repairs.append(
                f"Visuelle Lücke {gap:.3f}s zwischen {prev.shot_id} und {curr.shot_id} erkannt."
            )

    total = timeline.total_duration_seconds
    if ordered:
        total = max(total, ordered[-1].timeline_end_seconds)

    document = ResolvedTimelineDocument(
        script_version=locked.script_version,
        fps=fps,
        total_duration_seconds=round(total, 6),
        audio_segments=audio_segments,
        shots=ordered,
        repairs=repairs,
        errors=errors,
    )
    write_json(resolved_timeline_path(project), document)
    write_json(repair_log_path(project), {"repairs": repairs, "errors": errors})
    if errors:
        raise TimelineResolveError("; ".join(errors))
    return document
