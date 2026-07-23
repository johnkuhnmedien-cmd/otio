"""Python-Finalisierung: Anchors → Sekunden, Validierung, Reparaturprotokoll."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from otio_app.models import Project
from otio_app.services.inventory_loader import load_folder_inventory
from otio_app.services.media_utils import (
    is_image_media,
    probe_duration_seconds,
    probe_media_timing,
)
from otio_app.services.without_voiceover_enhanced.asset_identity import (
    canonicalize_inventory_asset_id,
    is_legacy_ambiguous_asset_id,
)
from otio_app.services.without_voiceover_enhanced.audio_timing_service import (
    load_segment_timings,
)
from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
    load_cut_plan_options,
    resolve_timing_seconds,
)
from otio_app.services.without_voiceover_enhanced.cut_rhythm_validator import (
    assess_cut_rhythm,
)
from otio_app.services.without_voiceover_enhanced.io_utils import load_model, write_json
from otio_app.services.without_voiceover_enhanced.local_media_service import (
    STATUS_EXPORT_READY,
    is_http_url,
    list_export_ready_supplements,
    refresh_supplement_validation,
)
from otio_app.services.without_voiceover_enhanced.media_hold import (
    MediaHoldError,
    ensure_still_hold_video,
    ensure_video_padded_hold,
)
from otio_app.services.without_voiceover_enhanced.models import (
    AcceptedSupplementsDocument,
    FinalCutPlanDocument,
    NarrationAnchor,
    NarrationTimelineDocument,
    NarrationTimelineEntry,
    ResolvedAudioSegment,
    ResolvedChapterEnvelope,
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
from otio_app.services.without_voiceover_enhanced.pause_resolver import (
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


@dataclass
class AssetCatalog:
    """Eindeutige Asset-Einträge; Kollisionen und Legacy-Aliase separat."""

    by_id: dict[str, dict] = field(default_factory=dict)
    collisions: list[str] = field(default_factory=list)
    legacy_to_ids: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))


def _resolve_local_path(project: Project, raw: str | Path) -> Path:
    path = Path(str(raw)).expanduser()
    if path.is_file():
        return path.resolve()
    candidate = (Path(project.project_root).expanduser() / path).resolve()
    if candidate.is_file():
        return candidate
    return path


def _probe_entry(
    project: Project,
    *,
    path: Path,
    folder: str,
    asset_id: str,
    usable_in: float | None,
    media_type_hint: str,
    fps: float,
) -> dict:
    timing = probe_media_timing(path, default_rate=fps)
    duration = timing.duration_sec
    if duration is None:
        duration = probe_duration_seconds(path)
    kind = "image" if is_image_media(path) or media_type_hint in {"photo", "image"} else "video"
    return {
        "path": str(path),
        "duration_seconds": float(duration) if duration else None,
        "usable_in_s": float(usable_in) if usable_in is not None else None,
        "folder": folder,
        "media_type": media_type_hint or kind,
        "media_kind": kind,
        "available_start_seconds": float(timing.start_sec or 0.0),
        "media_rate": float(timing.rate or fps),
        "canonical_id": asset_id,
    }


def build_asset_catalog(project: Project, *, fps: float = 25.0) -> AssetCatalog:
    """Baut eindeutigen Katalog; doppelte explizite IDs → collisions."""
    result = AssetCatalog()
    explicit_paths: dict[str, list[str]] = defaultdict(list)

    def _register(entry_id: str, entry: dict, *, raw_id: str) -> None:
        path = str(entry["path"])
        if entry_id in result.by_id and result.by_id[entry_id]["path"] != path:
            explicit_paths[entry_id].append(path)
            explicit_paths[entry_id].append(result.by_id[entry_id]["path"])
            return
        if entry_id in result.by_id:
            return
        result.by_id[entry_id] = entry
        if is_legacy_ambiguous_asset_id(raw_id):
            if entry_id not in result.legacy_to_ids[raw_id]:
                result.legacy_to_ids[raw_id].append(entry_id)
        # Auch Stem-Legacy aus Dateiname indexieren (für alte Cut-Pläne).
        stem_legacy = f"asset_{Path(path).stem}"
        stem_legacy = (
            "asset_"
            + "".join(ch if ch.isalnum() else "_" for ch in Path(path).stem).strip("_").lower()
        )
        if stem_legacy and entry_id not in result.legacy_to_ids[stem_legacy]:
            result.legacy_to_ids[stem_legacy].append(entry_id)

    for folder in project.selected_asset_subdirs:
        inventory = load_folder_inventory(project, folder)
        if inventory is None:
            continue
        for asset in getattr(inventory, "assets", []) or []:
            raw_path = getattr(asset, "path", None) or getattr(asset, "source_path", None)
            if raw_path is None:
                continue
            path = _resolve_local_path(project, raw_path)
            if not path.is_file():
                continue
            if is_http_url(str(path)):
                continue
            existing = str(getattr(asset, "asset_id", "") or "").strip()
            canonical = canonicalize_inventory_asset_id(
                project,
                path=path,
                folder_name=folder,
                existing_id=existing,
            )
            if existing and not is_legacy_ambiguous_asset_id(existing):
                # Explizite ID: Kollision prüfen (gleiche ID, anderer Pfad).
                if existing in result.by_id and result.by_id[existing]["path"] != str(path):
                    explicit_paths[existing].extend(
                        [result.by_id[existing]["path"], str(path)]
                    )
                    continue
            duration = getattr(asset, "duration_sec", None)
            if duration is None:
                duration = getattr(asset, "duration_seconds", None)
            usable_in = getattr(asset, "usable_in_s", None)
            media_type = getattr(asset, "media_type", None) or (
                "photo" if is_image_media(path) else "video"
            )
            entry = _probe_entry(
                project,
                path=path,
                folder=folder,
                asset_id=canonical,
                usable_in=float(usable_in) if usable_in is not None else None,
                media_type_hint=str(media_type or "video").lower(),
                fps=fps,
            )
            if duration is not None and entry["duration_seconds"] is None:
                entry["duration_seconds"] = float(duration)
            register_id = (
                existing
                if existing and not is_legacy_ambiguous_asset_id(existing)
                else canonical
            )
            entry["canonical_id"] = register_id
            _register(register_id, entry, raw_id=existing or canonical)
            if register_id != canonical:
                _register(canonical, entry, raw_id=existing or canonical)

    accepted = load_model(accepted_supplements_path(project), AcceptedSupplementsDocument)
    if accepted is not None:
        for supplement in accepted.supplements:
            refreshed = refresh_supplement_validation(supplement)
            if refreshed.media_validation_status != STATUS_EXPORT_READY:
                continue
            local_path = str(refreshed.local_media_path or "").strip()
            if not local_path or is_http_url(local_path):
                continue
            path = _resolve_local_path(project, local_path)
            if not path.is_file():
                continue
            media_type = (refreshed.media_type or "photo").lower()
            entry = _probe_entry(
                project,
                path=path,
                folder="",
                asset_id=supplement.candidate_id,
                usable_in=None,
                media_type_hint=media_type,
                fps=fps,
            )
            entry["supplement"] = True
            entry["export_ready"] = True
            if refreshed.duration_seconds is not None:
                entry["duration_seconds"] = refreshed.duration_seconds
            _register(supplement.candidate_id, entry, raw_id=supplement.candidate_id)

    for supplement in list_export_ready_supplements(project):
        local_path = str(supplement.local_media_path or "").strip()
        if not local_path or is_http_url(local_path):
            continue
        path = _resolve_local_path(project, local_path)
        if not path.is_file():
            continue
        if supplement.candidate_id in result.by_id:
            continue
        entry = _probe_entry(
            project,
            path=path,
            folder="",
            asset_id=supplement.candidate_id,
            usable_in=None,
            media_type_hint=(supplement.media_type or "photo").lower(),
            fps=fps,
        )
        entry["supplement"] = True
        entry["export_ready"] = True
        if supplement.duration_seconds is not None:
            entry["duration_seconds"] = supplement.duration_seconds
        _register(supplement.candidate_id, entry, raw_id=supplement.candidate_id)

    for asset_id, paths in sorted(explicit_paths.items()):
        unique_paths = sorted(set(paths))
        if len(unique_paths) < 2:
            continue
        listed = "; ".join(unique_paths)
        result.collisions.append(
            f"Asset-ID '{asset_id}' zeigt auf mehrere lokale Pfade: {listed}. "
            "Inventar sowie Lauf 2 und Lauf 3 neu erzeugen."
        )
        # Mehrdeutige ID aus Katalog entfernen — kein stilles first/last.
        result.by_id.pop(asset_id, None)
    return result


def _asset_catalog(project: Project) -> dict[str, dict]:
    """Kompatibilitäts-Wrapper (eindeutige IDs)."""
    return build_asset_catalog(project).by_id


def lookup_catalog_entry(
    catalog: AssetCatalog,
    asset_id: str,
) -> tuple[dict | None, str | None]:
    """Gibt (entry, error) zurück — nie stilles first/last bei Mehrdeutigkeit."""
    key = (asset_id or "").strip()
    if not key:
        return None, "Leere Asset-ID."
    if key in catalog.by_id:
        return catalog.by_id[key], None
    aliases = catalog.legacy_to_ids.get(key) or []
    if len(aliases) == 1:
        return catalog.by_id[aliases[0]], None
    if len(aliases) > 1:
        paths = [catalog.by_id[a]["path"] for a in aliases if a in catalog.by_id]
        return None, (
            f"Mehrdeutige Legacy-Asset-ID '{key}' trifft "
            f"{len(paths)} Dateien: {'; '.join(paths)}. "
            "Inventar sowie Lauf 2 und Lauf 3 neu erzeugen "
            "(eindeutige Ordner-Scoped-IDs erforderlich)."
        )
    return None, f"Unbekannte Asset-ID: {key}"


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


def _frame_duration(fps: float) -> float:
    rate = float(fps) if float(fps) > 0 else 25.0
    return 1.0 / rate


def _chapters_from_locked(locked) -> list[tuple[str, list[str]]]:
    """Kapitel in Skriptreihenfolge: aufeinanderfolgende Segmente mit gleichem Ordner.

    Segmente ohne folder_name bilden ein gemeinsames Default-Kapitel — nicht
    je Segment ein Pseudo-Kapitel — damit kapitelübergreifende Ankerprüfung
    echte Ordnergrenzen meint.
    """
    chapters: list[tuple[str, list[str]]] = []
    for segment in sorted(locked.segments, key=lambda s: int(s.sequence_index)):
        folder = (segment.folder_name or "").strip()
        chapter_id = folder or "_default"
        if not chapters or chapters[-1][0] != chapter_id:
            chapters.append((chapter_id, [segment.segment_id]))
        else:
            chapters[-1][1].append(segment.segment_id)
    return chapters


def build_shot_continuity_table(
    shots: list[ResolvedShot],
    *,
    fps: float,
) -> list[dict]:
    """Diagnosetabelle für visuelle Gaps/Overlaps zwischen benachbarten Shots."""
    ordered = sorted(shots, key=lambda s: (s.timeline_start_seconds, s.shot_id))
    frame = _frame_duration(fps)
    rows: list[dict] = []
    for index, shot in enumerate(ordered):
        next_start = (
            ordered[index + 1].timeline_start_seconds
            if index + 1 < len(ordered)
            else None
        )
        gap = (
            None
            if next_start is None
            else round(next_start - shot.timeline_end_seconds, 6)
        )
        status = "ok"
        if gap is not None:
            if gap > frame + 1e-9:
                status = "gap_error"
            elif gap < -(frame + 1e-9):
                status = "overlap_error"
            elif abs(gap) > 1e-9:
                status = "frame_snap_candidate"
        rows.append(
            {
                "chapter_id": shot.chapter_id or shot.folder_name,
                "shot_id": shot.shot_id,
                "timeline_start": shot.timeline_start_seconds,
                "timeline_end": shot.timeline_end_seconds,
                "next_shot_start": next_start,
                "gap_or_overlap_seconds": gap,
                "asset_id": shot.asset_id,
                "resolved_media_path": shot.resolved_media_path,
                "source_start": shot.source_start_seconds,
                "source_end": shot.source_end_seconds,
                "repair_or_error": status,
            }
        )
    return rows


def _apply_visual_continuity_rules(
    ordered: list[ResolvedShot],
    *,
    project: Project,
    fps: float,
    repairs: list[str],
    errors: list[str],
) -> None:
    """Ein-Frame-Snap reparieren; größere Gaps/Overlaps blockieren."""
    if len(ordered) < 2:
        return
    frame = _frame_duration(fps)
    for prev, curr in zip(ordered, ordered[1:]):
        delta = curr.timeline_start_seconds - prev.timeline_end_seconds
        chapter = curr.chapter_id or prev.chapter_id or prev.folder_name or "?"
        if abs(delta) <= 1e-9:
            continue
        if 0.0 < delta <= frame + 1e-9:
            prev.timeline_end_seconds = round(curr.timeline_start_seconds, 6)
            repairs.append(
                f"Ein-Frame-Gap {delta:.6f}s zwischen {prev.shot_id} und "
                f"{curr.shot_id} (Kapitel {chapter}) — Snap auf Anschluss."
            )
            try:
                _reapply_hold_for_timeline_span(
                    project,
                    prev,
                    fps=fps,
                    repairs=repairs,
                    label="Ein-Frame-Gap",
                )
            except TimelineResolveError as exc:
                errors.append(str(exc))
            continue
        if -frame - 1e-9 <= delta < 0.0:
            curr.timeline_start_seconds = round(prev.timeline_end_seconds, 6)
            repairs.append(
                f"Ein-Frame-Overlap {delta:.6f}s zwischen {prev.shot_id} und "
                f"{curr.shot_id} (Kapitel {chapter}) — Snap auf Anschluss."
            )
            continue
        if delta > frame + 1e-9:
            errors.append(
                f"Visuelle Lücke in Kapitel {chapter}: "
                f"{prev.timeline_end_seconds:.3f}s–{curr.timeline_start_seconds:.3f}s "
                f"({delta:.3f}s) zwischen {prev.shot_id} ({prev.asset_id}) und "
                f"{curr.shot_id} ({curr.asset_id})."
            )
            continue
        if delta < -(frame + 1e-9):
            # may_overlap_pause darf keine Video-Clip-Überlappung erlauben.
            errors.append(
                f"Visuelle Überlappung in Kapitel {chapter}: "
                f"{curr.timeline_start_seconds:.3f}s–{prev.timeline_end_seconds:.3f}s "
                f"({abs(delta):.3f}s) zwischen {prev.shot_id} ({prev.asset_id}) und "
                f"{curr.shot_id} ({curr.asset_id})."
            )


def _chapter_id_slug(chapter_id: str) -> str:
    return (
        chapter_id.replace(" ", "_")
        .replace("í", "i")
        .replace("á", "a")
        .replace("ó", "o")
        .replace("é", "e")
        .replace("ú", "u")
        .replace("ñ", "n")
    )


def _segment_to_chapter_map(locked) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for chapter_id, segment_ids in _chapters_from_locked(locked):
        for segment_id in segment_ids:
            mapping[segment_id] = chapter_id
    return mapping


def _make_chapter_edge_hold(
    project: Project,
    *,
    template: ResolvedShot,
    shot_id: str,
    timeline_start: float,
    timeline_end: float,
    chapter_id: str,
    editorial_function: str,
    fps: float,
    repairs: list[str],
    label: str,
) -> ResolvedShot:
    """Technischer Kapitel-Hold — eigene Hold-Datei, getrennt vom redaktionellen Shot."""
    need = max(0.0, float(timeline_end) - float(timeline_start))
    if need <= 1e-9:
        raise TimelineResolveError(
            f"{shot_id}: {label} hat keine positive Dauer."
        )
    path = Path(template.resolved_media_path or "")
    if not path.is_file():
        raise TimelineResolveError(
            f"{shot_id}: {label} unmöglich — Medienpfad fehlt "
            f"({template.resolved_media_path})."
        )
    try:
        if is_image_media(path):
            hold_path = ensure_still_hold_video(
                project, path, duration_seconds=need, fps=fps
            )
        else:
            hold_path = ensure_video_padded_hold(
                project, path, target_duration_seconds=need, fps=fps
            )
    except MediaHoldError as exc:
        raise TimelineResolveError(
            f"{shot_id}: {label} fehlgeschlagen: {exc}"
        ) from exc

    repairs.append(
        f"{shot_id}: {label}-Hold-Video {need:.2f}s ({hold_path.name})."
    )
    # Eigene Asset-ID, damit Portable-Manifest redaktionelle Originalclips
    # nicht mit technischen Holds überschreibt.
    return ResolvedShot(
        shot_id=shot_id,
        asset_id=f"techhold__{shot_id}",
        timeline_start_seconds=round(timeline_start, 6),
        timeline_end_seconds=round(timeline_end, 6),
        source_start_seconds=0.0,
        source_end_seconds=round(need, 6),
        editorial_function=editorial_function,
        may_overlap_pause=False,
        resolved_media_path=str(hold_path),
        resolved_media_kind="video",
        resolved_media_duration_seconds=round(need, 6),
        resolved_available_start_seconds=0.0,
        folder_name=template.folder_name or chapter_id,
        chapter_id=chapter_id,
        hold_mode="freeze_video",
    )


def _apply_chapter_envelopes(
    project: Project,
    *,
    locked,
    final: FinalCutPlanDocument,
    ordered: list[ResolvedShot],
    audio_segments: list[ResolvedAudioSegment],
    preroll: float,
    postroll: float,
    fps: float,
    repairs: list[str],
    errors: list[str],
) -> list[ResolvedChapterEnvelope]:
    """Kapitelhüllen: Rohabdeckung prüfen, dann Vor-/Nachlauf über technische Holds."""
    chapters = _chapters_from_locked(locked)
    if not chapters:
        return []

    shot_start_segment = {
        shot.shot_id: next(
            (
                plan.narration_start_anchor.segment_id
                for plan in final.shots
                if plan.shot_id == shot.shot_id
            ),
            "",
        )
        for shot in ordered
    }
    raw_shot_times = {
        shot.shot_id: (shot.timeline_start_seconds, shot.timeline_end_seconds)
        for shot in ordered
    }

    narration_expected = bool(locked.segments)
    cursor = 0.0
    envelopes: list[ResolvedChapterEnvelope] = []
    frame = _frame_duration(fps)
    hold_shots: list[ResolvedShot] = []

    for chapter_id, segment_ids in chapters:
        seg_set = set(segment_ids)
        ch_audios = [a for a in audio_segments if a.segment_id in seg_set]
        ch_shots = [
            shot
            for shot in ordered
            if shot_start_segment.get(shot.shot_id, "") in seg_set
        ]

        if narration_expected and not ch_audios:
            errors.append(
                f"Kapitel {chapter_id}: Narration erwartet, aber keine "
                "Audio-Segmente vorhanden."
            )
            continue
        if not ch_shots:
            errors.append(
                f"Kapitel {chapter_id}: kein visueller Shot für die Kapitelhülle."
            )
            continue

        for audio in ch_audios:
            audio.chapter_id = chapter_id
        for shot in ch_shots:
            shot.chapter_id = chapter_id
            if not shot.folder_name:
                shot.folder_name = chapter_id

        if ch_audios:
            raw_audio_start = min(a.timeline_start_seconds for a in ch_audios)
            raw_audio_end = max(a.timeline_end_seconds for a in ch_audios)
        else:
            raw_audio_start = min(s.timeline_start_seconds for s in ch_shots)
            raw_audio_end = max(s.timeline_end_seconds for s in ch_shots)

        raw_first_shot_start = min(raw_shot_times[s.shot_id][0] for s in ch_shots)
        raw_last_shot_end = max(raw_shot_times[s.shot_id][1] for s in ch_shots)

        # Rohabdeckung vor jeder Hüllen-Verlängerung / jedem Hold.
        if raw_first_shot_start > raw_audio_start + frame + 1e-9:
            errors.append(
                f"Führende visuelle Lücke in Kapitel {chapter_id}: "
                f"erster Shot startet bei {raw_first_shot_start:.3f}s, "
                f"Audio bei {raw_audio_start:.3f}s "
                f"(>{frame:.4f}s Frame-Toleranz)."
            )
        elif raw_first_shot_start > raw_audio_start + 1e-9:
            first_raw = min(
                ch_shots,
                key=lambda s: (raw_shot_times[s.shot_id][0], s.shot_id),
            )
            _old_start, old_end = raw_shot_times[first_raw.shot_id]
            raw_shot_times[first_raw.shot_id] = (raw_audio_start, old_end)
            repairs.append(
                f"Ein-Frame-Randabweichung führend in Kapitel {chapter_id}: "
                f"{first_raw.shot_id} Start {raw_first_shot_start:.6f}s → "
                f"{raw_audio_start:.6f}s."
            )
            raw_first_shot_start = raw_audio_start

        if raw_last_shot_end < raw_audio_end - frame - 1e-9:
            errors.append(
                f"Abschließende visuelle Lücke während der Narration in Kapitel "
                f"{chapter_id}: letzter Shot endet bei {raw_last_shot_end:.3f}s, "
                f"Audio bei {raw_audio_end:.3f}s "
                f"(>{frame:.4f}s Frame-Toleranz)."
            )
        elif raw_last_shot_end < raw_audio_end - 1e-9:
            last_raw = max(
                ch_shots,
                key=lambda s: (raw_shot_times[s.shot_id][1], s.shot_id),
            )
            old_start, _old_end = raw_shot_times[last_raw.shot_id]
            raw_shot_times[last_raw.shot_id] = (old_start, raw_audio_end)
            repairs.append(
                f"Ein-Frame-Randabweichung abschließend in Kapitel {chapter_id}: "
                f"{last_raw.shot_id} Ende {raw_last_shot_end:.6f}s → "
                f"{raw_audio_end:.6f}s."
            )
            raw_last_shot_end = raw_audio_end

        raw_span = max(0.0, raw_audio_end - raw_audio_start)

        chapter_video_start = _seconds_to_frame(cursor, fps)
        chapter_audio_start = _seconds_to_frame(chapter_video_start + preroll, fps)
        chapter_audio_end = _seconds_to_frame(chapter_audio_start + raw_span, fps)
        chapter_video_end = _seconds_to_frame(chapter_audio_end + postroll, fps)

        for audio in ch_audios:
            delta_start = audio.timeline_start_seconds - raw_audio_start
            delta_end = audio.timeline_end_seconds - raw_audio_start
            audio.timeline_start_seconds = round(chapter_audio_start + delta_start, 6)
            audio.timeline_end_seconds = round(chapter_audio_start + delta_end, 6)

        for shot in ch_shots:
            old_start, old_end = raw_shot_times[shot.shot_id]
            shot.timeline_start_seconds = round(
                chapter_audio_start + (old_start - raw_audio_start), 6
            )
            shot.timeline_end_seconds = round(
                chapter_audio_start + (old_end - raw_audio_start), 6
            )
            if shot.timeline_end_seconds < shot.timeline_start_seconds:
                shot.timeline_end_seconds = shot.timeline_start_seconds

        first = min(ch_shots, key=lambda s: (s.timeline_start_seconds, s.shot_id))
        last = max(ch_shots, key=lambda s: (s.timeline_end_seconds, s.shot_id))
        slug = _chapter_id_slug(chapter_id)
        preroll_hold_shot_id = ""
        postroll_hold_shot_id = ""

        if preroll > 1e-9:
            hold_start = round(chapter_video_start, 6)
            # Nicht in den ersten redaktionellen Shot hineinragen.
            hold_end = round(
                min(chapter_audio_start, first.timeline_start_seconds), 6
            )
            if hold_end > hold_start + 1e-9:
                try:
                    hold = _make_chapter_edge_hold(
                        project,
                        template=first,
                        shot_id=f"{slug}__chapter_preroll_hold",
                        timeline_start=hold_start,
                        timeline_end=hold_end,
                        chapter_id=chapter_id,
                        editorial_function="technical_chapter_preroll_hold",
                        fps=fps,
                        repairs=repairs,
                        label=f"Kapitel-{chapter_id}-Vorlauf-Hold",
                    )
                    hold_shots.append(hold)
                    preroll_hold_shot_id = hold.shot_id
                except TimelineResolveError as exc:
                    errors.append(str(exc))

        if postroll > 1e-9:
            # Nachlauf erst nach Audioende; nicht über redaktionelles Shot-Ende.
            hold_start = round(
                max(chapter_audio_end, last.timeline_end_seconds), 6
            )
            hold_end = round(chapter_video_end, 6)
            if hold_end > hold_start + 1e-9:
                try:
                    hold = _make_chapter_edge_hold(
                        project,
                        template=last,
                        shot_id=f"{slug}__chapter_postroll_hold",
                        timeline_start=hold_start,
                        timeline_end=hold_end,
                        chapter_id=chapter_id,
                        editorial_function="technical_chapter_postroll_hold",
                        fps=fps,
                        repairs=repairs,
                        label=f"Kapitel-{chapter_id}-Nachlauf-Hold",
                    )
                    hold_shots.append(hold)
                    postroll_hold_shot_id = hold.shot_id
                except TimelineResolveError as exc:
                    errors.append(str(exc))

        envelope_first_id = preroll_hold_shot_id or first.shot_id
        envelope_last_id = postroll_hold_shot_id or last.shot_id
        envelopes.append(
            ResolvedChapterEnvelope(
                chapter_id=chapter_id,
                folder_name=chapter_id,
                chapter_video_start=round(chapter_video_start, 6),
                chapter_audio_start=round(chapter_audio_start, 6),
                chapter_audio_end=round(chapter_audio_end, 6),
                chapter_video_end=round(chapter_video_end, 6),
                preroll_seconds=round(preroll, 6),
                postroll_seconds=round(postroll, 6),
                first_shot_id=envelope_first_id,
                last_shot_id=envelope_last_id,
                preroll_hold_shot_id=preroll_hold_shot_id,
                postroll_hold_shot_id=postroll_hold_shot_id,
                segment_ids=list(segment_ids),
            )
        )
        cursor = chapter_video_end

    ordered.extend(hold_shots)

    if envelopes:
        repairs.append(
            f"Kapitelhüllen: {len(envelopes)} Kapitel mit Vorlauf {preroll:.2f}s "
            f"und Nachlauf {postroll:.2f}s pro Kapitel "
            f"(Nachlauf/Vorlauf als technische Holds, nicht als shot_max-Stretch)."
        )
    return envelopes


def _count_chapter_continuity(
    envelopes: list[ResolvedChapterEnvelope],
    ordered: list[ResolvedShot],
    *,
    fps: float,
) -> None:
    frame = _frame_duration(fps)
    by_chapter: dict[str, list[ResolvedShot]] = defaultdict(list)
    for shot in ordered:
        key = shot.chapter_id or shot.folder_name or ""
        by_chapter[key].append(shot)
    for envelope in envelopes:
        shots = sorted(
            by_chapter.get(envelope.chapter_id, []),
            key=lambda s: (s.timeline_start_seconds, s.shot_id),
        )
        gaps = 0
        overlaps = 0
        for prev, curr in zip(shots, shots[1:]):
            delta = curr.timeline_start_seconds - prev.timeline_end_seconds
            if delta > frame + 1e-9:
                gaps += 1
            elif delta < -(frame + 1e-9):
                overlaps += 1
        envelope.visual_gap_count = gaps
        envelope.visual_overlap_count = overlaps


def _reapply_hold_for_timeline_span(
    project: Project,
    shot: ResolvedShot,
    *,
    fps: float,
    repairs: list[str],
    label: str,
) -> None:
    """Passt Source an Timeline-Span an; bei Overflow Hold-Video, nie Source>Datei."""
    need = max(0.0, shot.timeline_end_seconds - shot.timeline_start_seconds)
    source_span = max(0.0, shot.source_end_seconds - shot.source_start_seconds)
    if need <= source_span + 1e-6:
        return
    path = Path(shot.resolved_media_path or "")
    if not path.is_file():
        raise TimelineResolveError(
            f"{shot.shot_id}: {label}-Hold unmöglich — Medienpfad fehlt "
            f"({shot.resolved_media_path})."
        )
    available_start = float(shot.resolved_available_start_seconds or 0.0)
    media_dur = shot.resolved_media_duration_seconds
    if media_dur is not None:
        available_end = available_start + float(media_dur)
        # Zuerst: Source nach vorne schieben, wenn Datei lang genug.
        if float(media_dur) + 1e-6 >= need:
            shot.source_start_seconds = round(available_start, 6)
            shot.source_end_seconds = round(available_start + need, 6)
            if shot.source_end_seconds <= available_end + 1e-6:
                repairs.append(
                    f"{shot.shot_id}: {label} — Source auf Dateianfang geschoben."
                )
                return
    # Hold-Video erzeugen (Still → Loop-Video, Video → tpad clone).
    try:
        if is_image_media(path):
            hold = ensure_still_hold_video(
                project, path, duration_seconds=need, fps=fps
            )
        else:
            hold = ensure_video_padded_hold(
                project, path, target_duration_seconds=need, fps=fps
            )
    except MediaHoldError as exc:
        raise TimelineResolveError(f"{shot.shot_id}: {label}-Hold fehlgeschlagen: {exc}") from exc
    shot.resolved_media_path = str(hold)
    shot.resolved_media_kind = "video"
    shot.resolved_available_start_seconds = 0.0
    shot.resolved_media_duration_seconds = need
    shot.source_start_seconds = 0.0
    shot.source_end_seconds = round(need, 6)
    shot.hold_mode = "freeze_video"
    repairs.append(f"{shot.shot_id}: {label}-Hold-Video {need:.2f}s ({hold.name}).")


def detect_one_to_one_sentence_asset(final: FinalCutPlanDocument, segment_count: int) -> bool:
    """True wenn Shotanzahl == Segmentanzahl und jeder Shot genau ein Segment spannt."""
    if len(final.shots) != segment_count or segment_count == 0:
        return False
    return all(
        shot.narration_start_anchor.segment_id == shot.narration_end_anchor.segment_id
        and shot.narration_start_anchor.offset_seconds == 0.0
        for shot in final.shots
    )


def _resolve_shot_media(
    project: Project,
    *,
    shot_id: str,
    asset_id: str,
    entry: dict,
    timeline_start: float,
    timeline_end: float,
    fps: float,
    head_trim: float,
    short_tolerance: float,
    editorial_function: str,
    may_overlap_pause: bool,
    repairs: list[str],
) -> ResolvedShot:
    """Berechnet Source-Ranges inkl. Embedded-TC und Hold-Medien."""
    duration = max(0.0, timeline_end - timeline_start)
    media_path = Path(str(entry["path"]))
    available_start = float(entry.get("available_start_seconds") or 0.0)
    media_duration = entry.get("duration_seconds")
    media_kind = str(entry.get("media_kind") or "").lower()
    if not media_kind:
        media_kind = "image" if is_image_media(media_path) else "video"
    hold_mode = ""
    resolved_path = media_path

    if media_kind == "image" or (media_duration is None or float(media_duration or 0) <= 0):
        # Stills: Hold-Video über die volle Timeline-Dauer (Resolve-sicher).
        try:
            hold_path = ensure_still_hold_video(
                project,
                media_path,
                duration_seconds=max(duration, TECH_MIN_SHOT_SECONDS),
                fps=fps,
            )
        except MediaHoldError as exc:
            raise TimelineResolveError(f"{shot_id}: {exc}") from exc
        resolved_path = hold_path
        hold_mode = "freeze_video"
        available_start = 0.0
        media_duration = max(duration, TECH_MIN_SHOT_SECONDS)
        media_kind = "video"
        source_start = 0.0
        source_end = duration
        repairs.append(
            f"{shot_id}: Still → Hold-Video {duration:.2f}s ({hold_path.name})."
        )
    else:
        media_duration_f = float(media_duration)
        usable_in = entry.get("usable_in_s")
        trim = head_trim
        if usable_in is not None:
            trim = max(trim, max(0.0, float(usable_in)))
        if trim >= media_duration_f:
            raise TimelineResolveError(
                f"{shot_id}: Asset {asset_id}: Head-Trim/usable_in ({trim}s) "
                f">= Mediendauer ({media_duration_f}s) · Pfad {media_path}."
            )
        usable = media_duration_f - trim
        need = duration
        if need > usable + 1e-6:
            shortfall = need - usable
            if shortfall <= short_tolerance + 1e-6:
                need = usable
                timeline_end = _seconds_to_frame(timeline_start + need, fps)
                duration = need
                repairs.append(
                    f"{shot_id}: nutzbare Dauer knapp ({shortfall:.2f}s) — "
                    "Toleranz, Shot gekürzt."
                )
            else:
                # Anderen gültigen Source-Start wählen hilft nicht, wenn need > usable.
                # Hold-Video mit tpad (letztes Frame klonen).
                try:
                    hold_path = ensure_video_padded_hold(
                        project,
                        media_path,
                        target_duration_seconds=need,
                        fps=fps,
                    )
                except MediaHoldError as exc:
                    raise TimelineResolveError(
                        f"{shot_id}: Asset {asset_id} zu kurz "
                        f"({media_duration_f}s < {need}s; Toleranz "
                        f"{short_tolerance:.1f}s) und Hold fehlgeschlagen: {exc} "
                        f"· Pfad {media_path}."
                    ) from exc
                resolved_path = hold_path
                hold_mode = "freeze_video"
                available_start = 0.0
                media_duration_f = need
                source_start = 0.0
                source_end = need
                repairs.append(
                    f"{shot_id}: Video-Hold {need:.2f}s via tpad ({hold_path.name})."
                )
                return ResolvedShot(
                    shot_id=shot_id,
                    asset_id=asset_id,
                    timeline_start_seconds=timeline_start,
                    timeline_end_seconds=timeline_end,
                    source_start_seconds=round(source_start, 6),
                    source_end_seconds=round(source_end, 6),
                    editorial_function=editorial_function,
                    may_overlap_pause=may_overlap_pause,
                    resolved_media_path=str(resolved_path),
                    resolved_media_kind=media_kind,
                    resolved_media_duration_seconds=round(media_duration_f, 6),
                    resolved_available_start_seconds=round(available_start, 6),
                    folder_name=str(entry.get("folder") or ""),
                    hold_mode=hold_mode,
                )

        # Mitte der nutzbaren Zone; Source im Embedded-TC-Raum.
        content_start = trim + max(0.0, (usable - need) / 2.0)
        source_start = available_start + content_start
        source_end = source_start + need
        available_end = available_start + media_duration_f
        if source_end > available_end + 1e-6:
            # Nach links schieben, sofern möglich.
            shift = source_end - available_end
            source_start = max(available_start + trim, source_start - shift)
            source_end = source_start + need
        if source_start < available_start - 1e-6 or source_end > available_end + 1e-6:
            raise TimelineResolveError(
                f"{shot_id}: Source-Range außerhalb der verfügbaren Range für "
                f"{asset_id} (source {source_start:.3f}–{source_end:.3f}, "
                f"available {available_start:.3f}–{available_end:.3f}) · "
                f"Pfad {media_path}."
            )

    return ResolvedShot(
        shot_id=shot_id,
        asset_id=asset_id,
        timeline_start_seconds=timeline_start,
        timeline_end_seconds=timeline_end,
        source_start_seconds=round(source_start, 6),
        source_end_seconds=round(source_end, 6),
        editorial_function=editorial_function,
        may_overlap_pause=may_overlap_pause,
        resolved_media_path=str(resolved_path),
        resolved_media_kind=media_kind,
        resolved_media_duration_seconds=(
            round(float(media_duration), 6) if media_duration is not None else None
        ),
        resolved_available_start_seconds=round(available_start, 6),
        folder_name=str(entry.get("folder") or ""),
        hold_mode=hold_mode,
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
    fps = float(project.fps)
    catalog = build_asset_catalog(project, fps=fps)
    errors.extend(catalog.collisions)
    options = load_cut_plan_options(project)
    editorial_min = max(TECH_MIN_SHOT_SECONDS, float(options.shot_min_sec))
    editorial_max = min(
        TECH_MAX_SHOT_SECONDS,
        max(editorial_min, float(options.shot_max_sec)),
    )
    head_trim = max(0.0, float(options.video_head_trim_sec))
    short_tolerance = max(0.0, float(options.short_asset_tolerance_sec))
    known_segments = {s.segment_id for s in locked.segments}
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
    audio_segments = _build_resolved_audio_segments(
        timeline=timeline,
        timing_map=timing_map,
    )
    segment_to_chapter = _segment_to_chapter_map(locked)

    resolved_shots: list[ResolvedShot] = []
    for shot in final.shots:
        if shot.narration_start_anchor.segment_id not in known_segments:
            errors.append(f"Unbekannte Segment-ID: {shot.narration_start_anchor.segment_id}")
            continue
        if shot.narration_end_anchor.segment_id not in known_segments:
            errors.append(f"Unbekannte Segment-ID: {shot.narration_end_anchor.segment_id}")
            continue
        start_seg = shot.narration_start_anchor.segment_id
        end_seg = shot.narration_end_anchor.segment_id
        start_chapter = segment_to_chapter.get(start_seg, "")
        end_chapter = segment_to_chapter.get(end_seg, "")
        if start_chapter and end_chapter and start_chapter != end_chapter:
            errors.append(
                f"{shot.shot_id}: Start- und Endanker in unterschiedlichen Kapiteln "
                f"(Startanker {start_seg} → Kapitel {start_chapter}, "
                f"Endanker {end_seg} → Kapitel {end_chapter})."
            )
            continue
        entry, lookup_error = lookup_catalog_entry(catalog, shot.asset_id)
        if entry is None:
            accepted = load_model(
                accepted_supplements_path(project), AcceptedSupplementsDocument
            )
            if accepted is not None and any(
                s.candidate_id == shot.asset_id for s in accepted.supplements
            ):
                errors.append(
                    f"{shot.shot_id}: Supplement {shot.asset_id} ist nicht "
                    "export_ready (lokale Mediendatei fehlt oder ist ungültig)."
                )
            else:
                errors.append(f"{shot.shot_id}: {lookup_error}")
            continue
        media_path = Path(str(entry.get("path") or ""))
        if is_http_url(str(media_path)):
            errors.append(
                f"{shot.shot_id}: Asset {shot.asset_id} besitzt eine Web-URL "
                f"statt lokaler Datei ({media_path})."
            )
            continue
        if not media_path.is_file():
            errors.append(
                f"{shot.shot_id}: lokale Datei fehlt für {shot.asset_id}: {media_path}"
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
            errors.append(f"{shot.shot_id}: {exc}")
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

        try:
            resolved_shot = _resolve_shot_media(
                project,
                shot_id=shot.shot_id,
                asset_id=str(entry.get("canonical_id") or shot.asset_id),
                entry=entry,
                timeline_start=start,
                timeline_end=end,
                fps=fps,
                head_trim=head_trim,
                short_tolerance=short_tolerance,
                editorial_function=shot.editorial_function,
                may_overlap_pause=shot.may_overlap_pause,
                repairs=repairs,
            )
        except TimelineResolveError as exc:
            errors.append(str(exc))
            continue
        resolved_shots.append(resolved_shot)

    ordered = sorted(resolved_shots, key=lambda s: (s.timeline_start_seconds, s.shot_id))

    # Vor-/Nachlauf pro Kapitel (Hülle), nicht einmal global.
    chapter_envelopes = _apply_chapter_envelopes(
        project,
        locked=locked,
        final=final,
        ordered=ordered,
        audio_segments=audio_segments,
        preroll=preroll,
        postroll=postroll,
        fps=fps,
        repairs=repairs,
        errors=errors,
    )
    ordered = sorted(ordered, key=lambda s: (s.timeline_start_seconds, s.shot_id))
    _apply_visual_continuity_rules(
        ordered,
        project=project,
        fps=fps,
        repairs=repairs,
        errors=errors,
    )
    ordered = sorted(ordered, key=lambda s: (s.timeline_start_seconds, s.shot_id))
    _count_chapter_continuity(chapter_envelopes, ordered, fps=fps)

    def _is_technical_hold(shot: ResolvedShot) -> bool:
        return str(shot.editorial_function or "").startswith("technical_chapter_")

    # Max asset usage (Intro und technische Kapitel-Holds zählen nicht).
    usage_counts = Counter(
        shot.asset_id for shot in ordered if not _is_technical_hold(shot)
    )
    for asset_id, count in sorted(usage_counts.items()):
        folder = str((catalog.by_id.get(asset_id) or {}).get("folder") or "")
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
            if _is_technical_hold(shot):
                continue
            folder = str(
                shot.folder_name
                or (catalog.by_id.get(shot.asset_id) or {}).get("folder")
                or ""
            )
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

    chapter_count = max(1, len(chapter_envelopes))
    total = timeline.total_duration_seconds + (preroll + postroll) * chapter_count
    if ordered:
        total = max(total, ordered[-1].timeline_end_seconds)
    if audio_segments:
        total = max(
            total,
            max(a.timeline_end_seconds + a.pause_after_seconds for a in audio_segments),
        )
    if chapter_envelopes:
        total = max(total, chapter_envelopes[-1].chapter_video_end)

    document = ResolvedTimelineDocument(
        script_version=locked.script_version,
        fps=fps,
        total_duration_seconds=round(total, 6),
        audio_segments=audio_segments,
        shots=ordered,
        chapters=chapter_envelopes,
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
