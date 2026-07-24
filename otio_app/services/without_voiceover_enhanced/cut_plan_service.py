"""LLM-Lauf 2/3 + Coverage/Stock-Orchestrierung für Enhanced Cut Plan MVP."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from pathlib import Path

from otio_app.models import Project
from otio_app.project_layout import safe_folder_slug
from otio_app.services.gemini_client import _extract_json
from otio_app.services.generic_outro_selector import asset_id_for_path
from otio_app.services.inventory_loader import load_folder_inventory
from otio_app.services.media_utils import probe_duration_seconds
from otio_app.services.plan_llm_client import (
    PlanImageAttachment,
    generate_plan_text_with_metadata,
)
from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
    format_shot_constraints_for_prompt,
    load_cut_plan_options,
)
from otio_app.services.voiceover_generation.dramaturgy_service import load_confirmed_dramaturgy
from otio_app.services.voiceover_generation.style_reference_service import (
    style_context_text_for_prompts,
)
from otio_app.services.without_voiceover_enhanced.audio_timing_service import (
    load_segment_timings,
    validate_timings_against_script,
)
from otio_app.services.without_voiceover_enhanced.io_utils import load_model, write_json
from otio_app.services.without_voiceover_enhanced.models import (
    AcceptedSupplementsDocument,
    CoverageGap,
    CoverageGapsDocument,
    EditorialAnchor,
    EnhancedScriptDocument,
    FinalCutPlanDocument,
    FinalShot,
    NarrationAnchor,
    NarrationTimelineDocument,
    PauseDirective,
    RoughCutPlanDocument,
    RoughShot,
    SegmentTimingsDocument,
    StockCandidate,
    StockSearchResultsDocument,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    accepted_supplements_path,
    coverage_gaps_path,
    final_cut_plan_path,
    narration_timeline_path,
    pause_directives_path,
    rough_cut_plan_path,
    stock_search_results_path,
)
from otio_app.services.without_voiceover_enhanced.pause_resolver import (
    build_narration_timeline,
)
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    require_locked_script,
)
from otio_app.services.without_voiceover_enhanced.script_prompts import (
    DEFAULT_CUT_RHYTHM_TARGETS,
    build_final_cut_prompt,
    build_rough_cut_prompt,
)
from otio_app.services.without_voiceover_enhanced.segment_alignment_service import (
    load_segment_alignments,
)
from otio_app.services.without_voiceover_enhanced.sentence_timing_prompt import (
    build_sentence_timings_json_for_segments,
    sentence_index_by_id,
)
from otio_app.services.without_voiceover_enhanced.local_media_service import (
    STATUS_LOCAL_MEDIA_MISSING,
    list_export_ready_supplements,
    refresh_supplement_validation,
)
from otio_app.services.without_voiceover_enhanced.stock.registry import (
    clear_rate_limit_circuit,
    search_all_providers,
    search_configured_providers,
)
from otio_app.services.without_voiceover_enhanced.stock_provider_config import (
    enabled_provider_names,
)


class CutPlanError(RuntimeError):
    pass


def select_middle_frame_path(frames_used: list[str] | None) -> Path | None:
    """Wählt das mittlere Analyse-Frame (bei 3 Frames: frame_002 / duration/2)."""
    existing = [Path(p) for p in (frames_used or []) if p and Path(p).is_file()]
    if not existing:
        return None
    return existing[len(existing) // 2]


def middle_frame_attachments_from_payload(
    assets: list[dict[str, Any]],
    *,
    max_images: int,
) -> list[PlanImageAttachment]:
    """Baut multimodale Attachments aus Payload-Einträgen mit middle_frame_path."""
    images: list[PlanImageAttachment] = []
    for item in assets:
        if len(images) >= max_images:
            break
        frame = str(item.get("middle_frame_path") or "").strip()
        if not frame:
            continue
        path = Path(frame)
        if not path.is_file():
            continue
        asset_id = str(item.get("local_asset_id") or item.get("asset_id") or path.stem)
        images.append(
            PlanImageAttachment(
                path=path,
                label=asset_id,
                mime_type="image/jpeg",
            )
        )
    return images


def _local_assets_payload(
    project: Project,
    *,
    folder_name: str | None = None,
    include_middle_frames: bool = False,
) -> list[dict[str, Any]]:
    """Schlanke Asset-Liste für LLM-Lauf 2/3 (kein voller Inventory-Dump).

    Bevorzugt vorhandene ``inventory/{folder}.slim.json``. Fallback: Slim aus
    kanonischem Inventar. Middle-Frames nur bei Vision aus dem Vollinventar.
    """
    from otio_app.project_layout import get_folder_inventory_path
    from otio_app.services.inventory_prompt_view import (
        slim_assets_for_cut_plan_prompt,
        slim_assets_from_slim_document,
        load_slim_folder_inventory_file,
        slim_inventory_path_for,
    )

    assets: list[dict[str, Any]] = []
    folders = (
        [folder_name]
        if folder_name
        else list(project.selected_asset_subdirs)
    )
    for folder in folders:
        if not folder:
            continue
        slim_path = slim_inventory_path_for(
            get_folder_inventory_path(project.work_dir_path, folder)
        )
        slim_doc = load_slim_folder_inventory_file(slim_path)
        if slim_doc is not None:
            slim_entries = slim_assets_from_slim_document(
                slim_doc, folder_name=folder
            )
        else:
            inventory = load_folder_inventory(project, folder)
            if inventory is None:
                continue
            slim_entries = slim_assets_for_cut_plan_prompt(
                inventory,
                folder_name=folder,
                probe_duration=False,
                existing_slim_path=None,
            )
        if not slim_entries:
            continue
        if not include_middle_frames:
            assets.extend(slim_entries)
            continue

        # Middle-Frames: Lookup über stabile asset_id im kanonischen Inventar.
        inventory = load_folder_inventory(project, folder)
        by_id: dict[str, Any] = {}
        for asset in getattr(inventory, "assets", []) or []:
            path = getattr(asset, "path", None) or getattr(asset, "source_path", None)
            if path is None:
                continue
            asset_id = getattr(asset, "asset_id", None) or asset_id_for_path(str(path))
            by_id[str(asset_id)] = asset
        for entry in slim_entries:
            asset = by_id.get(str(entry.get("local_asset_id") or ""))
            frames_used = list(getattr(asset, "frames_used", None) or []) if asset else []
            middle = select_middle_frame_path(frames_used)
            entry["middle_frame_path"] = str(middle) if middle is not None else None
            entry["has_middle_frame"] = middle is not None
            assets.append(entry)
    return assets


def _style_text(project: Project) -> str:
    return style_context_text_for_prompts(project, detailed=True)


def _dramaturgy_text(project: Project) -> str:
    plan = load_confirmed_dramaturgy(project)
    return plan.model_dump_json(indent=2) if plan else "(keine Dramaturgie)"


def _chapter_dramaturgy_text_for_folder(project: Project, folder_name: str) -> str:
    from otio_app.services.without_voiceover_enhanced.script_author_service import (
        _chapter_dramaturgy_text,
        list_enabled_dramaturgy_folders,
    )

    for entry in list_enabled_dramaturgy_folders(project):
        if entry.folder_name == folder_name:
            return _chapter_dramaturgy_text(entry)
    return f"folder_name: {folder_name}"


@dataclass
class FolderRoughCutResult:
    folder_name: str
    status: str  # PASS | FAIL
    rough: RoughCutPlanDocument | None = None
    coverage: CoverageGapsDocument | None = None
    error: str | None = None
    shot_count: int = 0
    pause_count: int = 0
    gap_count: int = 0


@dataclass
class FolderFinalCutResult:
    folder_name: str
    status: str  # PASS | FAIL
    final: FinalCutPlanDocument | None = None
    error: str | None = None
    shot_count: int = 0


@dataclass
class _ChapterCutContext:
    folder_name: str
    folder_slug: str
    previous_folder_name: str | None
    next_folder_name: str | None
    segment_ids: set[str]
    script_slice: EnhancedScriptDocument
    timings_slice: SegmentTimingsDocument


def list_cut_plan_chapter_names(
    project: Project,
    locked: EnhancedScriptDocument | None = None,
) -> list[str]:
    """Kapitel-Reihenfolge für LLM-Lauf 2/3 (wie Lauf 1: Dramaturgie-Ordner)."""
    from otio_app.services.without_voiceover_enhanced.script_author_service import (
        group_segments_by_folder,
        list_enabled_dramaturgy_folders,
        segments_for_folder,
    )

    if locked is None:
        locked = require_locked_script(project)
    entries = list_enabled_dramaturgy_folders(project)
    if entries:
        names = [
            entry.folder_name
            for entry in entries
            if segments_for_folder(locked, entry.folder_name)
        ]
    else:
        names = []
    if not names:
        grouped = group_segments_by_folder(locked)
        names = [name for name, segs in grouped if name and segs]
    # Intro mit Segmenten immer vorne einplanen (auch wenn Dramaturgie es ausließ).
    def _is_intro_name(name: str) -> bool:
        slug = (name or "").strip().lower()
        return slug in {"intro", "introduction"} or slug.startswith("intro_")

    intro_names = [
        name
        for name, segs in group_segments_by_folder(locked)
        if name and segs and _is_intro_name(name)
    ]
    for intro_name in reversed(intro_names):
        if intro_name in names:
            names = [intro_name] + [n for n in names if n != intro_name]
        else:
            names = [intro_name] + names
    if names:
        return names
    # Legacy: Skript ohne Ordner-Zuordnung → ein Gesamtlauf.
    return [""] if locked.segments else []


def _script_slice_for_folder(
    locked: EnhancedScriptDocument,
    folder_name: str,
) -> EnhancedScriptDocument:
    if not folder_name:
        return locked
    segments = [s for s in locked.segments if s.folder_name == folder_name]
    segment_ids = {s.segment_id for s in segments}
    intents = [
        intent
        for intent in locked.visual_intents
        if intent.folder_name == folder_name
        or any(
            intent.intent_id in (seg.visual_intent_ids or [])
            for seg in segments
        )
    ]
    intent_ids = {intent.intent_id for intent in intents}
    beats = [
        beat
        for beat in locked.visual_beats
        if any(sid in segment_ids for sid in beat.related_segment_ids)
        or any(iid in intent_ids for iid in beat.visual_intent_ids)
    ]
    needs = [
        need
        for need in locked.coverage_needs
        if not need.visual_intent_id or need.visual_intent_id in intent_ids
    ]
    hints = [
        hint
        for hint in locked.fact_check_hints
        if not hint.related_segment_id or hint.related_segment_id in segment_ids
    ]
    return locked.model_copy(
        update={
            "narration_full": " ".join(s.text for s in segments if s.text.strip()),
            "segments": segments,
            "visual_intents": intents,
            "visual_beats": beats,
            "coverage_needs": needs,
            "fact_check_hints": hints,
        }
    )


def _timings_slice(
    timings: SegmentTimingsDocument,
    segment_ids: set[str],
) -> SegmentTimingsDocument:
    return SegmentTimingsDocument(
        schema_version=timings.schema_version,
        script_version=timings.script_version,
        segments=[s for s in timings.segments if s.segment_id in segment_ids],
    )


def _timeline_slice(
    timeline: NarrationTimelineDocument,
    segment_ids: set[str],
) -> NarrationTimelineDocument:
    entries = [e for e in timeline.entries if e.segment_id in segment_ids]
    total = 0.0
    if entries:
        total = max(
            float(timeline.total_duration_seconds),
            max(e.end_seconds + e.pause_after_seconds for e in entries),
        )
    return NarrationTimelineDocument(
        schema_version=timeline.schema_version,
        script_version=timeline.script_version,
        total_duration_seconds=total,
        entries=entries,
    )


def _rough_slice_for_segments(
    rough: RoughCutPlanDocument,
    segment_ids: set[str],
) -> RoughCutPlanDocument:
    def _anchor_ok(anchor: EditorialAnchor) -> bool:
        if anchor.type == "pause":
            sid = (anchor.after_segment_id or anchor.segment_id or "").strip()
        else:
            sid = (anchor.segment_id or "").strip()
        return bool(sid) and sid in segment_ids

    shots = [
        shot
        for shot in rough.shots
        if _anchor_ok(shot.start_anchor) and _anchor_ok(shot.end_anchor)
    ]
    # Legacy bridge when editorial anchors are empty.
    if not shots and any(s.narration_start_anchor.segment_id for s in rough.shots):
        shots = [
            shot
            for shot in rough.shots
            if shot.narration_start_anchor.segment_id in segment_ids
            and shot.narration_end_anchor.segment_id in segment_ids
        ]
    pauses = [
        pause
        for pause in rough.pause_directives
        if pause.after_segment_id in segment_ids
    ]
    return RoughCutPlanDocument(
        schema_version=rough.schema_version,
        script_version=rough.script_version,
        pause_directives=pauses,
        shots=shots,
    )


def _with_folder_prefix(raw_id: str, folder_slug: str, kind: str, index: int) -> str:
    raw = (raw_id or "").strip()
    if not folder_slug:
        return raw or f"{kind}_{index:03d}"
    prefix = f"{folder_slug}_"
    if raw.startswith(prefix):
        return raw
    if raw:
        return f"{prefix}{raw}"
    return f"{prefix}{kind}_{index:03d}"


def _prefix_rough_ids(
    rough: RoughCutPlanDocument,
    coverage: CoverageGapsDocument,
    folder_slug: str,
) -> tuple[RoughCutPlanDocument, CoverageGapsDocument]:
    if not folder_slug:
        return rough, coverage
    id_map: dict[str, str] = {}
    for index, shot in enumerate(rough.shots, start=1):
        new_id = _with_folder_prefix(shot.shot_id, folder_slug, "shot", index)
        id_map[shot.shot_id] = new_id
        shot.shot_id = new_id
        if shot.coverage_gap_id:
            shot.coverage_gap_id = _with_folder_prefix(
                shot.coverage_gap_id, folder_slug, "gap", index
            )
    for index, gap in enumerate(coverage.gaps, start=1):
        new_id = _with_folder_prefix(gap.gap_id, folder_slug, "gap", index)
        id_map[gap.gap_id] = new_id
        gap.gap_id = new_id
        gap.related_shot_ids = [
            id_map.get(sid, _with_folder_prefix(sid, folder_slug, "shot", i + 1))
            for i, sid in enumerate(gap.related_shot_ids)
        ]
    for shot in rough.shots:
        if shot.coverage_gap_id and shot.coverage_gap_id in id_map:
            shot.coverage_gap_id = id_map[shot.coverage_gap_id]
    return rough, coverage


def _prefix_final_ids(
    final: FinalCutPlanDocument,
    folder_slug: str,
) -> FinalCutPlanDocument:
    if not folder_slug:
        return final
    for index, shot in enumerate(final.shots, start=1):
        shot.shot_id = _with_folder_prefix(shot.shot_id, folder_slug, "shot", index)
    return final


def _validate_rough_chapter_scope(
    rough: RoughCutPlanDocument,
    segment_ids: set[str],
    folder_name: str,
) -> None:
    if not folder_name:
        return
    for shot in rough.shots:
        for label, anchor in (
            ("start", shot.start_anchor),
            ("end", shot.end_anchor),
        ):
            if anchor.type == "pause":
                sid = (anchor.after_segment_id or anchor.segment_id or "").strip()
            else:
                sid = (anchor.segment_id or "").strip()
            if sid and sid not in segment_ids:
                raise CutPlanError(
                    f"Kapitel „{folder_name}“: Shot {shot.shot_id} {label}-Anker "
                    f"verweist auf fremdes Segment {sid}."
                )
    for pause in rough.pause_directives:
        if pause.after_segment_id and pause.after_segment_id not in segment_ids:
            raise CutPlanError(
                f"Kapitel „{folder_name}“: Pause nach fremdem Segment "
                f"{pause.after_segment_id}."
            )


def _approx_editorial_anchor_seconds(
    anchor: EditorialAnchor,
    *,
    segment_starts: dict[str, float],
    segment_durs: dict[str, float],
) -> float | None:
    """Grobe Sekundenposition aus Soft-Anker (nur Validierung, nicht Timeline)."""
    if anchor.type == "pause":
        sid = (anchor.after_segment_id or anchor.segment_id or "").strip()
        if not sid or sid not in segment_starts:
            return None
        return segment_starts[sid] + segment_durs.get(sid, 0.0)
    sid = (anchor.segment_id or "").strip()
    if not sid or sid not in segment_starts:
        return None
    frac = _POSITION_FRACTION.get(str(anchor.position or "start"), 0.0)
    return segment_starts[sid] + segment_durs.get(sid, 0.0) * frac


def _validate_rough_continuous_coverage(
    rough: RoughCutPlanDocument,
    *,
    timings: SegmentTimingsDocument,
    ordered_segment_ids: list[str],
    folder_name: str,
    gap_tolerance_sec: float = 0.05,
) -> list[str]:
    """Fail-closed: Rough-Shots müssen den Kapitel-Teppich ohne Löcher abdecken.

    Nutzt nur Soft-Anker × Segmentdauern — keine finalen Timeline-Sekunden.
    Vertauschte Start/Ende-Anker werden repariert (häufiger LLM-Fehler).
    Returns repair notes.
    """
    repairs: list[str] = []
    if not ordered_segment_ids or not rough.shots:
        return repairs
    dur_by_id = {
        str(seg.segment_id): max(0.0, float(seg.duration_seconds or 0.0))
        for seg in timings.segments
        if seg.segment_id in ordered_segment_ids
    }
    cursor = 0.0
    starts: dict[str, float] = {}
    for sid in ordered_segment_ids:
        starts[sid] = cursor
        cursor += dur_by_id.get(sid, 0.0)
    total = cursor
    if total <= 1e-9:
        return repairs

    spans: list[tuple[float, float, str]] = []
    for shot in rough.shots:
        start = _approx_editorial_anchor_seconds(
            shot.start_anchor, segment_starts=starts, segment_durs=dur_by_id
        )
        end = _approx_editorial_anchor_seconds(
            shot.end_anchor, segment_starts=starts, segment_durs=dur_by_id
        )
        if start is None or end is None:
            raise CutPlanError(
                f"Kapitel „{folder_name}“: Shot {shot.shot_id} hat unauflösbare "
                "Anker für die Abdeckungsprüfung."
            )
        if end + 1e-9 < start:
            # LLM vertauscht häufig Soft-Positionen (z.B. start=end, end=middle).
            old_start, old_end = start, end
            shot.start_anchor, shot.end_anchor = shot.end_anchor, shot.start_anchor
            shot.narration_start_anchor = _editorial_to_narration_anchor(
                shot.start_anchor
            )
            shot.narration_end_anchor = _editorial_to_narration_anchor(
                shot.end_anchor
            )
            start, end = end, start
            repairs.append(
                f"{shot.shot_id}: Start-/Ende-Anker vertauscht repariert "
                f"(waren ~{old_start:.2f}s → ~{old_end:.2f}s)."
            )
        spans.append((start, max(start, end), shot.shot_id))

    spans.sort(key=lambda item: (item[0], item[1], item[2]))
    tol = max(0.0, float(gap_tolerance_sec))
    if spans[0][0] > tol + 1e-9:
        raise CutPlanError(
            f"Kapitel „{folder_name}“: Rough-Cut lässt ~{spans[0][0]:.2f}s am "
            f"Kapitelanfang ungedeckt (vor {spans[0][2]}). "
            "Opening-Shot an Narrationsstart setzen oder coverage_gap."
        )
    if spans[-1][1] < total - tol - 1e-9:
        hole = total - spans[-1][1]
        raise CutPlanError(
            f"Kapitel „{folder_name}“: Rough-Cut lässt ~{hole:.2f}s am "
            f"Kapitelende ungedeckt (nach {spans[-1][2]}). "
            "Closing-Shot bis Narrationsende oder coverage_gap."
        )
    for prev, curr in zip(spans, spans[1:]):
        gap = curr[0] - prev[1]
        if gap > tol + 1e-9:
            raise CutPlanError(
                f"Kapitel „{folder_name}“: Rough-Cut hat visuelle Lücke "
                f"~{gap:.2f}s zwischen {prev[2]} und {curr[2]}. "
                "Shot dazwischen planen oder coverage_gap — kein Video-Hold."
            )
    return repairs


def _validate_final_chapter_scope(
    final: FinalCutPlanDocument,
    segment_ids: set[str],
    folder_name: str,
) -> None:
    if not folder_name:
        return
    for shot in final.shots:
        for sid in (
            shot.narration_start_anchor.segment_id,
            shot.narration_end_anchor.segment_id,
        ):
            if sid and sid not in segment_ids:
                raise CutPlanError(
                    f"Kapitel „{folder_name}“: Final-Shot {shot.shot_id} "
                    f"verweist auf fremdes Segment {sid}."
                )


def _build_chapter_contexts(
    project: Project,
    locked: EnhancedScriptDocument,
    timings: SegmentTimingsDocument | None = None,
) -> list[_ChapterCutContext]:
    from otio_app.services.without_voiceover_enhanced.script_author_service import (
        list_enabled_dramaturgy_folders,
        _previous_and_next_folder,
    )

    chapter_names = list_cut_plan_chapter_names(project, locked)
    entries = list_enabled_dramaturgy_folders(project)
    empty_timings = SegmentTimingsDocument(
        script_version=locked.script_version,
        segments=[],
    )
    contexts: list[_ChapterCutContext] = []
    for folder_name in chapter_names:
        script_slice = _script_slice_for_folder(locked, folder_name)
        segment_ids = {s.segment_id for s in script_slice.segments}
        if not segment_ids:
            continue
        previous_name: str | None = None
        next_name: str | None = None
        if folder_name and entries:
            previous_name, next_name = _previous_and_next_folder(entries, folder_name)
        elif folder_name:
            idx = chapter_names.index(folder_name)
            previous_name = chapter_names[idx - 1] if idx > 0 else None
            next_name = (
                chapter_names[idx + 1] if idx + 1 < len(chapter_names) else None
            )
        contexts.append(
            _ChapterCutContext(
                folder_name=folder_name,
                folder_slug=safe_folder_slug(folder_name) if folder_name else "",
                previous_folder_name=previous_name,
                next_folder_name=next_name,
                segment_ids=segment_ids,
                script_slice=script_slice,
                timings_slice=(
                    _timings_slice(timings, segment_ids)
                    if timings is not None
                    else empty_timings
                ),
            )
        )
    return contexts


_POSITION_FRACTION = {
    "start": 0.0,
    "early": 0.25,
    "middle": 0.5,
    "late": 0.75,
    "end": 1.0,
}


def _nullish(value: Any) -> bool:
    return value in (None, "", "null")


def _parse_editorial_anchor(raw: Any) -> EditorialAnchor:
    if not isinstance(raw, dict):
        return EditorialAnchor()
    anchor_type = str(raw.get("type") or "segment").strip().lower() or "segment"
    position = str(raw.get("position") or "start").strip().lower() or "start"
    if position not in _POSITION_FRACTION:
        position = "start"
    after = raw.get("after_segment_id")
    segment_id = str(raw.get("segment_id") or "")
    sentence_raw = raw.get("sentence_id")
    sentence_id = None if _nullish(sentence_raw) else str(sentence_raw)
    if anchor_type == "pause":
        after_id = str(after or segment_id or "")
        return EditorialAnchor(
            type="pause",
            segment_id=segment_id or after_id,
            after_segment_id=after_id or None,
            sentence_id=sentence_id,
            position=position if position in {"start", "middle", "end"} else "start",
        )
    if anchor_type == "sentence" or sentence_id:
        return EditorialAnchor(
            type="sentence",
            segment_id=segment_id,
            after_segment_id=None if _nullish(after) else str(after),
            sentence_id=sentence_id,
            position=position,
        )
    return EditorialAnchor(
        type="segment",
        segment_id=segment_id,
        after_segment_id=None if _nullish(after) else str(after),
        sentence_id=None,
        position=position,
    )


def _editorial_to_narration_anchor(anchor: EditorialAnchor) -> NarrationAnchor:
    """Bridge: editorial position → NarrationAnchor (fraction stored as offset).

    Final Cut / Resolver still use real seconds; LLM 2 must not emit seconds.
    Fractions (0–1) are a compact bridge for UI and later timing mapping.
    For sentence anchors the fraction is relative to the sentence span.
    """
    if anchor.type == "pause":
        segment_id = str(anchor.after_segment_id or anchor.segment_id or "")
        fraction = 1.0 if anchor.position in {"start", "middle", "end"} else 1.0
        return NarrationAnchor(
            segment_id=segment_id,
            offset_seconds=fraction,
            sentence_id=anchor.sentence_id,
        )
    fraction = _POSITION_FRACTION.get(anchor.position, 0.0)
    return NarrationAnchor(
        segment_id=anchor.segment_id,
        offset_seconds=float(fraction),
        sentence_id=anchor.sentence_id if anchor.type == "sentence" else None,
    )


def _legacy_anchor_to_editorial(raw: Any) -> EditorialAnchor:
    if not isinstance(raw, dict):
        return EditorialAnchor()
    segment_id = str(raw.get("segment_id") or "")
    offset = float(raw.get("offset_seconds") or 0.0)
    # Map rough offset buckets when legacy payloads still use seconds.
    if offset <= 0.05:
        position = "start"
    elif offset < 0.35:
        position = "early"
    elif offset < 0.65:
        position = "middle"
    elif offset < 0.9:
        position = "late"
    else:
        position = "end"
    return EditorialAnchor(type="segment", segment_id=segment_id, position=position)


def parse_rough_cut_response(raw: str | dict[str, Any], script_version: str) -> tuple[
    RoughCutPlanDocument, CoverageGapsDocument
]:
    payload = _extract_json(raw) if isinstance(raw, str) else raw
    if not isinstance(payload, dict):
        raise CutPlanError("Grober Cut Plan ist kein JSON-Objekt.")

    directives: list[PauseDirective] = []
    for item in payload.get("pause_directives") or []:
        if not isinstance(item, dict):
            continue
        after_segment = str(item.get("after_segment_id") or "")
        after_sentence_raw = item.get("after_sentence_id")
        after_sentence = (
            None if _nullish(after_sentence_raw) else str(after_sentence_raw)
        )
        if not after_segment and not after_sentence:
            continue
        directives.append(
            PauseDirective(
                after_segment_id=after_segment,
                after_sentence_id=after_sentence,
                pause_function=str(item.get("pause_function") or "breath"),
                duration_class=str(item.get("duration_class") or "medium"),
                visual_behavior=str(
                    item.get("visual_behavior") or "editorial_choice"
                ),
                editorial_reason=str(item.get("editorial_reason") or ""),
            )
        )

    for item in payload.get("shots") or []:
        if isinstance(item, dict) and (
            "start_frame" in item or "end_frame" in item or "timeline_start" in item
        ):
            raise CutPlanError("LLM-Ausgabe enthält finale Frames/Timelinezeiten.")

    shots: list[RoughShot] = []
    for index, item in enumerate(payload.get("shots") or [], start=1):
        if not isinstance(item, dict):
            continue
        uses_editorial = "start_anchor" in item or "end_anchor" in item
        if uses_editorial:
            if (
                "offset_seconds" in (item.get("start_anchor") or {})
                or "offset_seconds" in (item.get("end_anchor") or {})
            ):
                raise CutPlanError(
                    "LLM-Ausgabe enthält Sekunden in Editorial-Ankern — "
                    "nur position (start|early|middle|late|end) erlaubt."
                )
            start_anchor = _parse_editorial_anchor(item.get("start_anchor"))
            end_anchor = _parse_editorial_anchor(item.get("end_anchor"))
        else:
            start_anchor = _legacy_anchor_to_editorial(item.get("narration_start_anchor"))
            end_anchor = _legacy_anchor_to_editorial(item.get("narration_end_anchor"))

        local_asset = item.get("local_asset_id", item.get("asset_id"))
        local_asset_id = None if _nullish(local_asset) else str(local_asset)
        gap_ref = item.get("coverage_gap_id")
        coverage_gap_id = None if _nullish(gap_ref) else str(gap_ref)
        narrative_function = str(
            item.get("narrative_function")
            or item.get("editorial_function")
            or "orientation"
        )
        visual_intent = str(
            item.get("visual_intent") or item.get("visual_intent_id") or ""
        )
        asset_fit = str(item.get("asset_fit") or ("none" if local_asset_id is None else "acceptable"))
        asset_fit_reason = str(
            item.get("asset_fit_reason") or item.get("editorial_reason") or ""
        )
        narration_start = _editorial_to_narration_anchor(start_anchor)
        narration_end = _editorial_to_narration_anchor(end_anchor)
        if not uses_editorial:
            # Preserve legacy absolute offsets for older fixtures.
            legacy_start = item.get("narration_start_anchor") or {}
            legacy_end = item.get("narration_end_anchor") or {}
            if isinstance(legacy_start, dict):
                narration_start = NarrationAnchor(
                    segment_id=str(legacy_start.get("segment_id") or ""),
                    offset_seconds=float(legacy_start.get("offset_seconds") or 0.0),
                )
            if isinstance(legacy_end, dict):
                narration_end = NarrationAnchor(
                    segment_id=str(legacy_end.get("segment_id") or ""),
                    offset_seconds=float(legacy_end.get("offset_seconds") or 0.0),
                )

        alignment = str(item.get("start_cut_alignment") or "").strip().lower()
        if alignment not in {"mid_sentence", "sentence_boundary", "in_pause"}:
            alignment = ""
        shots.append(
            RoughShot(
                shot_id=str(item.get("shot_id") or f"shot_{index:03d}"),
                start_anchor=start_anchor,
                end_anchor=end_anchor,
                narrative_function=narrative_function,
                visual_intent=visual_intent,
                local_asset_id=local_asset_id,
                asset_fit=asset_fit,
                asset_fit_reason=asset_fit_reason,
                continuity_notes=str(item.get("continuity_notes") or ""),
                coverage_gap_id=coverage_gap_id,
                start_cut_alignment=alignment,
                narration_start_anchor=narration_start,
                narration_end_anchor=narration_end,
                visual_intent_id=str(item.get("visual_intent_id") or visual_intent),
                asset_id=local_asset_id,
                candidate_asset_ids=[
                    str(x) for x in (item.get("candidate_asset_ids") or []) if x
                ],
                editorial_function=narrative_function,
                editorial_reason=asset_fit_reason,
                visual_behavior=str(item.get("visual_behavior") or "hold"),
                may_overlap_pause=bool(item.get("may_overlap_pause", False)),
            )
        )

    gaps: list[CoverageGap] = []
    for i, item in enumerate(payload.get("coverage_gaps") or [], start=1):
        if not isinstance(item, dict):
            continue
        gap_id = str(item.get("coverage_gap_id") or item.get("gap_id") or f"gap_{i:03d}")
        shot_id = item.get("shot_id")
        related = [str(x) for x in (item.get("related_shot_ids") or []) if x]
        if shot_id and str(shot_id) not in related:
            related = [str(shot_id), *related]
        needed = str(item.get("needed_visual") or item.get("subject") or "")
        purpose = str(item.get("editorial_purpose") or item.get("reason") or "")
        concepts = [str(x) for x in (item.get("search_concepts") or []) if x]
        queries = [str(x) for x in (item.get("search_queries") or []) if x]
        if not queries:
            queries = list(concepts)
        gaps.append(
            CoverageGap(
                gap_id=gap_id,
                related_shot_ids=related,
                needed_visual=needed,
                editorial_purpose=purpose,
                preferred_media_type=str(item.get("preferred_media_type") or "video"),
                search_concepts=concepts or list(queries),
                must_include=[str(x) for x in (item.get("must_include") or []) if x],
                must_avoid=[str(x) for x in (item.get("must_avoid") or []) if x],
                fact_check_required=bool(item.get("fact_check_required", False)),
                covered_sentence_ids=[
                    str(x) for x in (item.get("covered_sentence_ids") or []) if x
                ],
                desired_motion=str(item.get("desired_motion") or ""),
                desired_framing=str(item.get("desired_framing") or ""),
                visual_intent_id=str(item.get("visual_intent_id") or ""),
                subject=needed or str(item.get("subject") or ""),
                location=str(item.get("location") or ""),
                action=str(item.get("action") or ""),
                editorial_function=str(item.get("editorial_function") or "orientation"),
                fallback_media_type=str(item.get("fallback_media_type") or "photo"),
                minimum_resolution=str(item.get("minimum_resolution") or "1920x1080"),
                priority=str(item.get("priority") or "high"),
                reason=purpose or str(item.get("reason") or ""),
                search_queries=queries,
            )
        )

    gaps_by_id = {gap.gap_id: gap for gap in gaps}
    covered_shots = {sid for gap in gaps for sid in gap.related_shot_ids}
    for shot in shots:
        if shot.local_asset_id is not None:
            continue
        if shot.coverage_gap_id and shot.coverage_gap_id in gaps_by_id:
            gap = gaps_by_id[shot.coverage_gap_id]
            if shot.shot_id not in gap.related_shot_ids:
                gap.related_shot_ids.append(shot.shot_id)
            continue
        if shot.shot_id in covered_shots:
            continue
        auto_id = shot.coverage_gap_id or f"gap_auto_{shot.shot_id}"
        gaps.append(
            CoverageGap(
                gap_id=auto_id,
                related_shot_ids=[shot.shot_id],
                needed_visual=shot.visual_intent or shot.narrative_function,
                editorial_purpose=shot.asset_fit_reason
                or "Kein lokales Asset für diesen Shot zugewiesen.",
                preferred_media_type="video",
                search_concepts=[
                    shot.visual_intent or shot.narrative_function or shot.shot_id
                ],
                subject=shot.visual_intent or shot.narrative_function,
                reason="Kein lokales Asset für diesen Shot zugewiesen.",
                search_queries=[
                    shot.visual_intent or shot.narrative_function or shot.shot_id
                ],
            )
        )
        shot.coverage_gap_id = auto_id

    rough = RoughCutPlanDocument(
        script_version=script_version,
        pause_directives=directives,
        shots=shots,
    )
    coverage = CoverageGapsDocument(script_version=script_version, gaps=gaps)
    return rough, coverage


def generate_rough_cut_for_folder(
    project: Project,
    folder_name: str,
    *,
    provider: str = "openai",
    model: str = "gpt-5.6-terra",
    llm_callable: Callable[..., Any] | None = None,
    context: _ChapterCutContext | None = None,
) -> FolderRoughCutResult:
    """Ein LLM-Lauf-2-Call für genau ein Kapitel."""
    from otio_app.services.voiceover_generation.model_settings_service import (
        resolve_llm_model_id,
    )

    display_name = folder_name or "(gesamtes Skript)"
    try:
        locked = require_locked_script(project)
        timings = load_segment_timings(project)
        if timings is None:
            raise CutPlanError("Segment-Timings fehlen.")
        if context is None:
            contexts = _build_chapter_contexts(project, locked, timings)
            context = next(
                (c for c in contexts if c.folder_name == folder_name),
                None,
            )
            if context is None:
                raise CutPlanError(f"Kein Kapitel-Kontext für „{display_name}“.")
        if not context.timings_slice.segments:
            raise CutPlanError(
                f"Kapitel „{display_name}“: keine Segment-Timings."
            )

        options = load_cut_plan_options(project)
        include_frames = bool(options.include_middle_frames)
        assets_folder = folder_name or None
        # Wenn der Kapitel-Ordner nicht in selected_asset_subdirs liegt,
        # trotzdem Assets dieses Namens versuchen; sonst alle (Legacy).
        if assets_folder and assets_folder not in project.selected_asset_subdirs:
            local_assets = _local_assets_payload(
                project,
                folder_name=assets_folder,
                include_middle_frames=include_frames,
            )
            if not local_assets:
                local_assets = _local_assets_payload(
                    project, include_middle_frames=include_frames
                )
        else:
            local_assets = _local_assets_payload(
                project,
                folder_name=assets_folder,
                include_middle_frames=include_frames,
            )

        dramaturgy_text = (
            _chapter_dramaturgy_text_for_folder(project, folder_name)
            if folder_name
            else _dramaturgy_text(project)
        )
        segment_ids = [seg.segment_id for seg in context.script_slice.segments]
        sentence_timings_json = build_sentence_timings_json_for_segments(
            project, segment_ids=segment_ids
        )
        prompt = build_rough_cut_prompt(
            locked_script_json=context.script_slice.model_dump_json(indent=2),
            segment_timings_json=context.timings_slice.model_dump_json(indent=2),
            local_assets_json=json.dumps(
                local_assets, ensure_ascii=False, indent=2
            ),
            style_profile_text=_style_text(project),
            dramaturgy_text=dramaturgy_text,
            folder_name=folder_name,
            folder_slug=context.folder_slug,
            previous_folder_name=context.previous_folder_name,
            next_folder_name=context.next_folder_name,
            include_middle_frames=include_frames,
            shot_constraints_text=format_shot_constraints_for_prompt(options),
            sentence_timings_json=sentence_timings_json,
            cut_rhythm_targets_text=DEFAULT_CUT_RHYTHM_TARGETS,
        )
        images = (
            middle_frame_attachments_from_payload(
                local_assets,
                max_images=int(options.max_middle_frames_per_chapter),
            )
            if include_frames
            else []
        )
        model_id = resolve_llm_model_id(provider, model)
        if llm_callable is not None:
            try:
                raw = llm_callable(prompt=prompt, model=model_id, images=images)
            except TypeError:
                # Ältere Test-Doubles ohne images-Parameter.
                raw = llm_callable(prompt=prompt, model=model_id)
            raw_text = (
                raw if isinstance(raw, str) else getattr(raw, "raw_text", str(raw))
            )
        else:
            raw_text = generate_plan_text_with_metadata(
                prompt=prompt,
                model=model_id,
                images=images or None,
            ).raw_text
        rough, coverage = parse_rough_cut_response(raw_text, locked.script_version)
        _validate_rough_chapter_scope(rough, context.segment_ids, folder_name)
        _validate_rough_continuous_coverage(
            rough,
            timings=context.timings_slice,
            ordered_segment_ids=[
                seg.segment_id for seg in context.script_slice.segments
            ],
            folder_name=display_name,
        )
        rough, coverage = _prefix_rough_ids(rough, coverage, context.folder_slug)
        if not rough.shots:
            raise CutPlanError("LLM-Antwort enthielt keine Shots.")
        return FolderRoughCutResult(
            folder_name=display_name,
            status="PASS",
            rough=rough,
            coverage=coverage,
            shot_count=len(rough.shots),
            pause_count=len(rough.pause_directives),
            gap_count=len(coverage.gaps),
        )
    except Exception as exc:  # noqa: BLE001
        return FolderRoughCutResult(
            folder_name=display_name,
            status="FAIL",
            error=str(exc),
        )


def generate_all_rough_cuts(
    project: Project,
    *,
    provider: str = "openai",
    model: str = "gpt-5.6-terra",
    llm_callable: Callable[..., Any] | None = None,
    progress_callback: Callable[[str, int, int], None] | None = None,
) -> list[FolderRoughCutResult]:
    """LLM-Lauf 2 sequenziell pro Kapitel (Dramaturgie-Reihenfolge)."""
    locked = require_locked_script(project)
    errors = validate_timings_against_script(project)
    if errors:
        raise CutPlanError("; ".join(errors))
    timings = load_segment_timings(project)
    assert timings is not None
    contexts = _build_chapter_contexts(project, locked, timings)
    if not contexts:
        raise CutPlanError("Keine Kapitel mit Segmenten für den Rough Cut.")

    results: list[FolderRoughCutResult] = []
    total = len(contexts)
    for index, context in enumerate(contexts, start=1):
        label = context.folder_name or "(gesamtes Skript)"
        if progress_callback is not None:
            progress_callback(label, index, total)
        results.append(
            generate_rough_cut_for_folder(
                project,
                context.folder_name,
                provider=provider,
                model=model,
                llm_callable=llm_callable,
                context=context,
            )
        )
    return results


def merge_and_persist_rough_cuts(
    project: Project,
    results: list[FolderRoughCutResult],
) -> tuple[RoughCutPlanDocument, CoverageGapsDocument]:
    """Merged Kapitel-Ergebnisse → globale Rough-/Timeline-/Gap-Artefakte."""
    locked = require_locked_script(project)
    timings = load_segment_timings(project)
    if timings is None:
        raise CutPlanError("Segment-Timings fehlen.")

    ok = [r for r in results if r.status == "PASS" and r.rough is not None]
    fail = [r for r in results if r.status != "PASS"]
    if not ok:
        details = "; ".join(f"{r.folder_name}: {r.error}" for r in fail) or "unbekannt"
        raise CutPlanError(f"LLM-Lauf 2 fehlgeschlagen für alle Kapitel. {details}")

    merged_pauses: list[PauseDirective] = []
    merged_shots: list[RoughShot] = []
    merged_gaps: list[CoverageGap] = []
    seen_pause_keys: set[str] = set()
    for result in ok:
        assert result.rough is not None
        assert result.coverage is not None
        for pause in result.rough.pause_directives:
            sentence_id = str(pause.after_sentence_id or "").strip()
            if sentence_id:
                key = f"sentence:{sentence_id}"
            else:
                key = f"segment:{pause.after_segment_id}"
            if key in seen_pause_keys:
                continue
            seen_pause_keys.add(key)
            merged_pauses.append(pause)
        merged_shots.extend(result.rough.shots)
        merged_gaps.extend(result.coverage.gaps)

    rough = RoughCutPlanDocument(
        script_version=locked.script_version,
        pause_directives=merged_pauses,
        shots=merged_shots,
    )
    coverage = CoverageGapsDocument(
        script_version=locked.script_version,
        gaps=merged_gaps,
    )
    timeline = build_narration_timeline(
        script_version=locked.script_version,
        segment_timings=timings.segments,
        pause_directives=rough.pause_directives,
        sentence_index=sentence_index_by_id(load_segment_alignments(project)),
    )
    write_json(
        pause_directives_path(project),
        {"directives": [d.model_dump(mode="json") for d in rough.pause_directives]},
    )
    write_json(narration_timeline_path(project), timeline)
    write_json(rough_cut_plan_path(project), rough)
    write_json(coverage_gaps_path(project), coverage)
    return rough, coverage


def generate_rough_cut_and_pauses(
    project: Project,
    *,
    provider: str = "openai",
    model: str = "gpt-5.6-terra",
    llm_callable: Callable[..., Any] | None = None,
    progress_callback: Callable[[str, int, int], None] | None = None,
) -> tuple[RoughCutPlanDocument, CoverageGapsDocument]:
    """Kompatibilitäts-Wrapper: Kapitel-Calls mergen → globale Artefakte schreiben."""
    results = generate_all_rough_cuts(
        project,
        provider=provider,
        model=model,
        llm_callable=llm_callable,
        progress_callback=progress_callback,
    )
    return merge_and_persist_rough_cuts(project, results)


_MAX_QUERIES_PER_GAP = 2
_STOCK_QUERY_PAUSE_SEC = 0.35


def _merge_provider_status(current: dict[str, str], incoming: dict[str, str]) -> None:
    """completed gewinnt; failed überschreibt nur, wenn noch kein completed."""
    from otio_app.services.without_voiceover_enhanced.stock_provider_config import (
        PROVIDER_STATUS_COMPLETED,
    )

    for key, value in incoming.items():
        prev = current.get(key)
        if prev == PROVIDER_STATUS_COMPLETED:
            continue
        if value == PROVIDER_STATUS_COMPLETED or prev is None:
            current[key] = value


def search_supplements_for_gaps(
    project: Project,
    *,
    providers=None,
    progress_callback: Callable[[float, str], None] | None = None,
) -> StockSearchResultsDocument:
    import time

    def _progress(fraction: float, message: str) -> None:
        if progress_callback is not None:
            progress_callback(min(1.0, max(0.0, fraction)), message)

    locked = require_locked_script(project)
    coverage = load_model(coverage_gaps_path(project), CoverageGapsDocument)
    if coverage is None or not coverage.gaps:
        raise CutPlanError("Keine Coverage Gaps vorhanden.")

    from otio_app.services.without_voiceover_enhanced.gap_search_concepts import (
        enrich_coverage_search_concepts,
        filter_keyword_concepts,
        heuristic_stock_concepts,
    )

    # E2E-2.1: Prosa in search_concepts vor der Stocksuche ersetzen.
    coverage = enrich_coverage_search_concepts(project, coverage)
    write_json(coverage_gaps_path(project), coverage)

    enabled = enabled_provider_names(project)
    if not enabled:
        # Preserve any previous search results; do not error.
        existing = load_model(stock_search_results_path(project), StockSearchResultsDocument)
        document = StockSearchResultsDocument(
            script_version=locked.script_version,
            provider_status={
                "pexels": "disabled",
                "pixabay": "disabled",
                "wikimedia": "disabled",
                "openverse": "disabled",
                "archive_org": "disabled",
            },
            candidates=list(existing.candidates) if existing is not None else [],
            message="Keine Stockanbieter aktiviert.",
        )
        write_json(stock_search_results_path(project), document)
        _progress(1.0, "Keine Stockanbieter aktiviert.")
        return document

    clear_rate_limit_circuit()
    all_candidates: list[StockCandidate] = []
    provider_status: dict[str, str] = {}
    gaps = list(coverage.gaps)
    # Vorab Query-Anzahl schätzen für stabile Progress-Bar.
    planned_queries = 0
    gap_queries: list[tuple[Any, list[str]]] = []
    for gap in gaps:
        raw_queries = filter_keyword_concepts(
            list(gap.search_concepts or []) + list(gap.search_queries or [])
        )
        if not raw_queries:
            raw_queries = heuristic_stock_concepts(
                needed_visual=gap.needed_visual or gap.subject or gap.gap_id,
                folder_name="",
            )
        queries = [q for q in raw_queries if str(q).strip()][:_MAX_QUERIES_PER_GAP]
        if not queries:
            queries = [gap.gap_id]
        gap_queries.append((gap, queries))
        planned_queries += len(queries)
    planned_queries = max(1, planned_queries)

    _progress(0.0, f"Stocksuche startet · {len(gaps)} Gaps · {planned_queries} Queries…")
    query_index = 0
    for gap_index, (gap, queries) in enumerate(gap_queries, start=1):
        for query in queries:
            if query_index > 0:
                time.sleep(_STOCK_QUERY_PAUSE_SEC)
            query_index += 1
            _progress(
                (query_index - 1) / planned_queries,
                f"Gap {gap_index}/{len(gaps)} · Query {query_index}/{planned_queries}: "
                f"{gap.gap_id} · „{str(query)[:60]}“",
            )
            if providers is not None:
                found, status = search_all_providers(
                    query,
                    media_type=gap.preferred_media_type,
                    providers=providers,
                    enabled_names=enabled,
                )
            else:
                found, status, _enabled = search_configured_providers(
                    project,
                    query,
                    media_type=gap.preferred_media_type,
                )
            _merge_provider_status(provider_status, status)
            for candidate in found:
                candidate.gap_id = gap.gap_id
                all_candidates.append(candidate)
            _progress(
                query_index / planned_queries,
                f"Gap {gap_index}/{len(gaps)} · Query {query_index}/{planned_queries} fertig · "
                f"+{len(found)} Treffer",
            )

    from otio_app.services.without_voiceover_enhanced.supplement_resolve_service import (
        dedupe_stock_candidates,
    )

    unique_candidates = dedupe_stock_candidates(all_candidates)

    failed = [
        name
        for name, status in provider_status.items()
        if status == "failed"
    ]
    message = ""
    if not unique_candidates and failed:
        message = (
            "Keine Treffer — Anbieter fehlgeschlagen: "
            + ", ".join(failed)
            + ". Oft Rate-Limit (429) oder falscher Endpoint."
        )
    elif failed:
        message = "Teilweise fehlgeschlagen: " + ", ".join(failed)
    if len(unique_candidates) < len(all_candidates):
        dropped = len(all_candidates) - len(unique_candidates)
        note = f"{dropped} Doppelte Treffer entfernt."
        message = f"{message} {note}".strip() if message else note

    document = StockSearchResultsDocument(
        script_version=locked.script_version,
        provider_status=provider_status,
        candidates=unique_candidates,
        message=message,
    )
    write_json(stock_search_results_path(project), document)
    _progress(
        1.0,
        f"Stocksuche fertig · {len(unique_candidates)} Kandidaten",
    )
    return document


def accept_supplement_candidates(
    project: Project,
    candidate_ids: list[str],
) -> AcceptedSupplementsDocument:
    locked = require_locked_script(project)
    results = load_model(stock_search_results_path(project), StockSearchResultsDocument)
    if results is None:
        raise CutPlanError("Keine Stockergebnisse vorhanden.")
    from otio_app.services.without_voiceover_enhanced.gap_status_service import (
        compute_cut_plan_run_id_from_path,
    )
    from otio_app.services.without_voiceover_enhanced.paths import (
        unified_cut_plan_path,
    )

    coverage = load_model(coverage_gaps_path(project), CoverageGapsDocument)
    run_id = str(getattr(coverage, "cut_plan_run_id", "") or "").strip()
    if not run_id:
        run_id = compute_cut_plan_run_id_from_path(unified_cut_plan_path(project))
    selected: list[StockCandidate] = []
    for candidate in results.candidates:
        if candidate.candidate_id in candidate_ids:
            if candidate.license in (None, "", "unknown"):
                # Keep unknown license metadata as null/unknown — do not invent;
                # still allow manual accept but flag in attribution note.
                candidate.license = candidate.license or None
            candidate.selected = True
            if run_id:
                candidate.cut_plan_run_id = run_id
            if candidate.local_media_path:
                candidate = refresh_supplement_validation(candidate)
            else:
                candidate.media_validation_status = STATUS_LOCAL_MEDIA_MISSING
                candidate.media_validation_error = (
                    f"Supplement {candidate.candidate_id} besitzt keine validierte "
                    "lokale Mediendatei. Ordne zuerst eine lokale Originaldatei zu."
                )
            selected.append(candidate)
    document = AcceptedSupplementsDocument(
        script_version=locked.script_version,
        supplements=selected,
    )
    write_json(accepted_supplements_path(project), document)
    # Persist selection flags on search results too.
    for candidate in results.candidates:
        candidate.selected = candidate.candidate_id in candidate_ids
    write_json(stock_search_results_path(project), results)
    return document


def parse_final_cut_response(raw: str | dict[str, Any], script_version: str) -> FinalCutPlanDocument:
    payload = _extract_json(raw) if isinstance(raw, str) else raw
    if not isinstance(payload, dict):
        raise CutPlanError("Finaler Cut Plan ist kein JSON-Objekt.")
    shots: list[FinalShot] = []
    for index, item in enumerate(payload.get("shots") or [], start=1):
        if not isinstance(item, dict):
            continue
        start = item.get("narration_start_anchor") or {}
        end = item.get("narration_end_anchor") or {}
        asset_id = str(item.get("asset_id") or "").strip()
        if not asset_id:
            raise CutPlanError(f"Shot {item.get('shot_id')} ohne asset_id.")
        start_sentence = start.get("sentence_id")
        end_sentence = end.get("sentence_id")
        alignment = str(item.get("start_cut_alignment") or "").strip().lower()
        if alignment not in {"mid_sentence", "sentence_boundary", "in_pause"}:
            alignment = ""
        shots.append(
            FinalShot(
                shot_id=str(item.get("shot_id") or f"shot_{index:03d}"),
                narration_start_anchor=NarrationAnchor(
                    segment_id=str(start.get("segment_id") or ""),
                    offset_seconds=float(start.get("offset_seconds") or 0.0),
                    sentence_id=(
                        None if _nullish(start_sentence) else str(start_sentence)
                    ),
                ),
                narration_end_anchor=NarrationAnchor(
                    segment_id=str(end.get("segment_id") or ""),
                    offset_seconds=float(end.get("offset_seconds") or 0.0),
                    sentence_id=(
                        None if _nullish(end_sentence) else str(end_sentence)
                    ),
                ),
                asset_id=asset_id,
                editorial_function=str(item.get("editorial_function") or "narration_support"),
                editorial_reason=str(item.get("editorial_reason") or ""),
                transition_behavior=str(item.get("transition_behavior") or "straight_cut"),
                source_range_intent=str(
                    item.get("source_range_intent") or "representative_middle_section"
                ),
                may_overlap_pause=bool(item.get("may_overlap_pause", False)),
                start_cut_alignment=alignment,
            )
        )
    if not shots:
        raise CutPlanError("Finaler Plan enthält keine Shots.")

    def _optional_float(key: str) -> float | None:
        if key not in payload or payload.get(key) is None:
            return None
        try:
            return float(payload.get(key))
        except (TypeError, ValueError):
            return None

    return FinalCutPlanDocument(
        script_version=script_version,
        shots=shots,
        voiceover_preroll_sec=_optional_float("voiceover_preroll_sec"),
        voiceover_postroll_sec=_optional_float("voiceover_postroll_sec"),
    )


def generate_final_cut_for_folder(
    project: Project,
    folder_name: str,
    *,
    provider: str = "openai",
    model: str = "gpt-5.6-terra",
    llm_callable: Callable[..., Any] | None = None,
    context: _ChapterCutContext | None = None,
    rough: RoughCutPlanDocument | None = None,
    timeline: NarrationTimelineDocument | None = None,
) -> FolderFinalCutResult:
    """Ein LLM-Lauf-3-Call für genau ein Kapitel."""
    from otio_app.services.voiceover_generation.model_settings_service import (
        resolve_llm_model_id,
    )

    display_name = folder_name or "(gesamtes Skript)"
    try:
        locked = require_locked_script(project)
        timings = load_segment_timings(project)
        if rough is None:
            rough = load_model(rough_cut_plan_path(project), RoughCutPlanDocument)
        if timeline is None:
            timeline = load_model(
                narration_timeline_path(project), NarrationTimelineDocument
            )
        if rough is None or timeline is None:
            raise CutPlanError("Grober Cut Plan / Narrationstimeline fehlt.")
        if context is None:
            contexts = _build_chapter_contexts(project, locked, timings)
            context = next(
                (c for c in contexts if c.folder_name == folder_name),
                None,
            )
            if context is None:
                raise CutPlanError(f"Kein Kapitel-Kontext für „{display_name}“.")

        script_slice = context.script_slice
        rough_slice = _rough_slice_for_segments(rough, context.segment_ids)
        timeline_slice = _timeline_slice(timeline, context.segment_ids)
        if not rough_slice.shots:
            raise CutPlanError(
                f"Kapitel „{display_name}“: kein Rough-Cut für dieses Kapitel."
            )

        options = load_cut_plan_options(project)
        assets_folder = folder_name or None
        if assets_folder and assets_folder not in project.selected_asset_subdirs:
            local_assets = _local_assets_payload(project, folder_name=assets_folder)
            if not local_assets:
                local_assets = _local_assets_payload(project)
        else:
            local_assets = _local_assets_payload(project, folder_name=assets_folder)

        export_ready = list_export_ready_supplements(project)
        supplement_rows = []
        for supplement in export_ready:
            row = supplement.model_dump(mode="json")
            row["description"] = (
                (supplement.title or "").strip()
                or (supplement.attribution or "").strip()
                or supplement.candidate_id
            )
            if supplement.duration_seconds is None and supplement.local_media_path:
                try:
                    row["duration_seconds"] = probe_duration_seconds(
                        Path(supplement.local_media_path)
                    )
                except Exception:  # noqa: BLE001
                    pass
            supplement_rows.append(row)
        accepted_json = json.dumps(
            {
                "schema_version": "enhanced-accepted-supplements-v1",
                "script_version": locked.script_version,
                "supplements": supplement_rows,
            },
            ensure_ascii=False,
            indent=2,
        )
        segment_ids = [seg.segment_id for seg in script_slice.segments]
        sentence_alignment_json = build_sentence_timings_json_for_segments(
            project, segment_ids=segment_ids
        )
        prompt = build_final_cut_prompt(
            locked_script_json=script_slice.model_dump_json(indent=2),
            narration_timeline_json=timeline_slice.model_dump_json(indent=2),
            pause_directives_json=json.dumps(
                [d.model_dump(mode="json") for d in rough_slice.pause_directives],
                ensure_ascii=False,
                indent=2,
            ),
            rough_cut_json=rough_slice.model_dump_json(indent=2),
            local_assets_json=json.dumps(
                local_assets, ensure_ascii=False, indent=2
            ),
            accepted_supplements_json=accepted_json,
            style_profile_text=_style_text(project),
            folder_name=folder_name,
            folder_slug=context.folder_slug,
            previous_folder_name=context.previous_folder_name,
            next_folder_name=context.next_folder_name,
            shot_constraints_text=format_shot_constraints_for_prompt(options),
            sentence_alignment_json=sentence_alignment_json,
            cut_rhythm_targets_text=DEFAULT_CUT_RHYTHM_TARGETS,
        )
        model_id = resolve_llm_model_id(provider, model)
        if llm_callable is not None:
            raw = llm_callable(prompt=prompt, model=model_id)
            raw_text = (
                raw if isinstance(raw, str) else getattr(raw, "raw_text", str(raw))
            )
        else:
            raw_text = generate_plan_text_with_metadata(
                prompt=prompt, model=model_id
            ).raw_text
        final = parse_final_cut_response(raw_text, locked.script_version)
        _validate_final_chapter_scope(final, context.segment_ids, folder_name)
        final = _prefix_final_ids(final, context.folder_slug)
        return FolderFinalCutResult(
            folder_name=display_name,
            status="PASS",
            final=final,
            shot_count=len(final.shots),
        )
    except Exception as exc:  # noqa: BLE001
        return FolderFinalCutResult(
            folder_name=display_name,
            status="FAIL",
            error=str(exc),
        )


def generate_all_final_cuts(
    project: Project,
    *,
    provider: str = "openai",
    model: str = "gpt-5.6-terra",
    llm_callable: Callable[..., Any] | None = None,
    progress_callback: Callable[[str, int, int], None] | None = None,
) -> list[FolderFinalCutResult]:
    """LLM-Lauf 3 sequenziell pro Kapitel."""
    locked = require_locked_script(project)
    timings = load_segment_timings(project)
    rough = load_model(rough_cut_plan_path(project), RoughCutPlanDocument)
    timeline = load_model(narration_timeline_path(project), NarrationTimelineDocument)
    if rough is None or timeline is None:
        raise CutPlanError("Grober Cut Plan / Narrationstimeline fehlt.")
    contexts = _build_chapter_contexts(project, locked, timings)
    if not contexts:
        raise CutPlanError("Keine Kapitel mit Segmenten für den Final Cut.")

    results: list[FolderFinalCutResult] = []
    total = len(contexts)
    for index, context in enumerate(contexts, start=1):
        label = context.folder_name or "(gesamtes Skript)"
        if progress_callback is not None:
            progress_callback(label, index, total)
        results.append(
            generate_final_cut_for_folder(
                project,
                context.folder_name,
                provider=provider,
                model=model,
                llm_callable=llm_callable,
                context=context,
                rough=rough,
                timeline=timeline,
            )
        )
    return results


def merge_and_persist_final_cuts(
    project: Project,
    results: list[FolderFinalCutResult],
) -> FinalCutPlanDocument:
    locked = require_locked_script(project)
    ok = [r for r in results if r.status == "PASS" and r.final is not None]
    fail = [r for r in results if r.status != "PASS"]
    if not ok:
        details = "; ".join(f"{r.folder_name}: {r.error}" for r in fail) or "unbekannt"
        raise CutPlanError(f"LLM-Lauf 3 fehlgeschlagen für alle Kapitel. {details}")
    shots: list[FinalShot] = []
    preroll: float | None = None
    postroll: float | None = None
    for result in ok:
        assert result.final is not None
        shots.extend(result.final.shots)
        if preroll is None and result.final.voiceover_preroll_sec is not None:
            preroll = result.final.voiceover_preroll_sec
        if result.final.voiceover_postroll_sec is not None:
            postroll = result.final.voiceover_postroll_sec
    final = FinalCutPlanDocument(
        script_version=locked.script_version,
        shots=shots,
        voiceover_preroll_sec=preroll,
        voiceover_postroll_sec=postroll,
    )
    write_json(final_cut_plan_path(project), final)
    return final


def generate_final_cut_plan(
    project: Project,
    *,
    provider: str = "openai",
    model: str = "gpt-5.6-terra",
    llm_callable: Callable[..., Any] | None = None,
    progress_callback: Callable[[str, int, int], None] | None = None,
) -> FinalCutPlanDocument:
    """Kompatibilitäts-Wrapper: Kapitel-Calls mergen → final_cut_plan.json."""
    results = generate_all_final_cuts(
        project,
        provider=provider,
        model=model,
        llm_callable=llm_callable,
        progress_callback=progress_callback,
    )
    return merge_and_persist_final_cuts(project, results)


# --- Unified Cut Plan (Phase 7) -------------------------------------------------

@dataclass
class FolderUnifiedCutResult:
    folder_name: str
    status: str  # PASS | FAIL
    plan: "UnifiedCutPlanDocument | None" = None
    error: str | None = None
    slot_count: int = 0
    pause_count: int = 0
    gap_count: int = 0


def _used_in_ledger_text(plans: list[Any]) -> str:
    """Filmweite Asset-Nutzung bisheriger Kapitel für den Prompt."""
    from collections import Counter

    counts: Counter[str] = Counter()
    for plan in plans:
        if plan is None:
            continue
        for slot in getattr(plan, "slots", []) or []:
            asset_id = getattr(slot, "local_asset_id", None)
            if asset_id:
                counts[str(asset_id)] += 1
    if not counts:
        return ""
    lines = ["asset_id\tuses"]
    for asset_id, count in sorted(counts.items()):
        lines.append(f"{asset_id}\t{count}")
    return "\n".join(lines)


def generate_unified_cut_for_folder(
    project: Project,
    folder_name: str,
    *,
    provider: str = "openai",
    model: str = "gpt-5.6-terra",
    llm_callable: Callable[..., Any] | None = None,
    context: _ChapterCutContext | None = None,
    used_in_ledger_text: str = "",
) -> FolderUnifiedCutResult:
    """Ein Unified-LLM-Call für genau ein Kapitel."""
    from otio_app.services.voiceover_generation.model_settings_service import (
        resolve_llm_model_id,
    )
    from otio_app.services.without_voiceover_enhanced.models import (
        UnifiedCutPlanDocument,
    )
    from otio_app.services.without_voiceover_enhanced.script_prompts import (
        build_unified_cut_prompt,
    )
    from otio_app.services.without_voiceover_enhanced.unified_cut_plan import (
        parse_unified_cut_response,
        unified_to_rough,
    )

    display_name = folder_name or "(gesamtes Skript)"
    try:
        locked = require_locked_script(project)
        timings = load_segment_timings(project)
        if timings is None:
            raise CutPlanError("Segment-Timings fehlen.")
        if context is None:
            contexts = _build_chapter_contexts(project, locked, timings)
            context = next(
                (c for c in contexts if c.folder_name == folder_name),
                None,
            )
            if context is None:
                raise CutPlanError(f"Kein Kapitel-Kontext für „{display_name}“.")
        if not context.timings_slice.segments:
            raise CutPlanError(
                f"Kapitel „{display_name}“: keine Segment-Timings."
            )

        options = load_cut_plan_options(project)
        include_frames = bool(options.include_middle_frames)
        from otio_app.services.without_voiceover_enhanced.intro_script_bridge import (
            is_intro_folder_name,
        )

        is_intro = is_intro_folder_name(folder_name)
        assets_folder = folder_name or None
        bundled_inventory: dict[str, Any] | None = None
        if is_intro:
            from otio_app.services.without_voiceover_enhanced.intro_cut_service import (
                build_bundled_inventory_for_intro,
                intro_bundled_inventory_path,
            )

            bundled_inventory = build_bundled_inventory_for_intro(
                project, include_middle_frames=include_frames
            )
            write_json(intro_bundled_inventory_path(project), bundled_inventory)
            local_assets = list(bundled_inventory.get("all_assets") or [])
            if not local_assets:
                raise CutPlanError(
                    "Intro: gebündeltes Inventar ist leer — "
                    "Slim-Inventare der Kapitel aufbauen."
                )
        elif assets_folder and assets_folder not in project.selected_asset_subdirs:
            local_assets = _local_assets_payload(
                project,
                folder_name=assets_folder,
                include_middle_frames=include_frames,
            )
            if not local_assets:
                local_assets = _local_assets_payload(
                    project, include_middle_frames=include_frames
                )
        else:
            local_assets = _local_assets_payload(
                project,
                folder_name=assets_folder,
                include_middle_frames=include_frames,
            )

        dramaturgy_text = (
            _chapter_dramaturgy_text_for_folder(project, folder_name)
            if folder_name
            else _dramaturgy_text(project)
        )
        segment_ids = [seg.segment_id for seg in context.script_slice.segments]
        sentence_timings_json = build_sentence_timings_json_for_segments(
            project, segment_ids=segment_ids
        )
        if is_intro:
            from otio_app.services.without_voiceover_enhanced.script_prompts import (
                build_intro_unified_cut_prompt,
            )

            intro_duration = sum(
                float(seg.duration_seconds or 0.0)
                for seg in context.timings_slice.segments
            )
            prompt = build_intro_unified_cut_prompt(
                locked_script_json=context.script_slice.model_dump_json(indent=2),
                segment_timings_json=context.timings_slice.model_dump_json(indent=2),
                bundled_inventory_json=json.dumps(
                    bundled_inventory or {}, ensure_ascii=False, indent=2
                ),
                style_profile_text=_style_text(project),
                dramaturgy_text=dramaturgy_text,
                folder_name=folder_name,
                folder_slug=context.folder_slug,
                sentence_timings_json=sentence_timings_json,
                intro_audio_duration_seconds=intro_duration,
            )
        else:
            prompt = build_unified_cut_prompt(
                locked_script_json=context.script_slice.model_dump_json(indent=2),
                segment_timings_json=context.timings_slice.model_dump_json(indent=2),
                local_assets_json=json.dumps(
                    local_assets, ensure_ascii=False, indent=2
                ),
                style_profile_text=_style_text(project),
                dramaturgy_text=dramaturgy_text,
                folder_name=folder_name,
                folder_slug=context.folder_slug,
                previous_folder_name=context.previous_folder_name,
                next_folder_name=context.next_folder_name,
                include_middle_frames=include_frames,
                shot_constraints_text=format_shot_constraints_for_prompt(options),
                sentence_timings_json=sentence_timings_json,
                cut_rhythm_targets_text=DEFAULT_CUT_RHYTHM_TARGETS,
                used_in_ledger_text=used_in_ledger_text,
            )
        images = (
            middle_frame_attachments_from_payload(
                local_assets,
                max_images=int(options.max_middle_frames_per_chapter),
            )
            if include_frames
            else []
        )
        model_id = resolve_llm_model_id(provider, model)
        if llm_callable is not None:
            try:
                raw = llm_callable(prompt=prompt, model=model_id, images=images)
            except TypeError:
                raw = llm_callable(prompt=prompt, model=model_id)
            raw_text = (
                raw if isinstance(raw, str) else getattr(raw, "raw_text", str(raw))
            )
        else:
            raw_text = generate_plan_text_with_metadata(
                prompt=prompt,
                model=model_id,
                images=images or None,
            ).raw_text

        plan = parse_unified_cut_response(
            raw_text,
            locked.script_version,
            folder_slug=context.folder_slug,
        )
        if not plan.slots:
            raise CutPlanError("LLM-Antwort enthielt keine Slots.")
        if is_intro:
            from otio_app.services.without_voiceover_enhanced.intro_cut_service import (
                enforce_intro_strong_only,
            )

            plan = enforce_intro_strong_only(plan)
        _rough, coverage = unified_to_rough(plan)
        return FolderUnifiedCutResult(
            folder_name=display_name,
            status="PASS",
            plan=plan,
            slot_count=len(plan.slots),
            pause_count=len(plan.pause_directives),
            gap_count=len(coverage.gaps),
        )
    except Exception as exc:  # noqa: BLE001
        return FolderUnifiedCutResult(
            folder_name=display_name,
            status="FAIL",
            error=str(exc),
        )


def generate_all_unified_cuts(
    project: Project,
    *,
    provider: str = "openai",
    model: str = "gpt-5.6-terra",
    llm_callable: Callable[..., Any] | None = None,
    progress_callback: Callable[[str, int, int], None] | None = None,
) -> list[FolderUnifiedCutResult]:
    """Unified-LLM sequenziell pro Kapitel inkl. used_in-Ledger."""
    locked = require_locked_script(project)
    errors = validate_timings_against_script(project)
    if errors:
        raise CutPlanError("; ".join(errors))
    timings = load_segment_timings(project)
    assert timings is not None
    from otio_app.services.without_voiceover_enhanced.intro_script_bridge import (
        is_intro_folder_name,
    )

    contexts = _build_chapter_contexts(project, locked, timings)
    # Intro hat eigene Buttons — Kapitel-Lauf überspringt Intro.
    contexts = [c for c in contexts if not is_intro_folder_name(c.folder_name)]
    if not contexts:
        raise CutPlanError(
            "Keine Kapitel mit Segmenten für den Unified Cut "
            "(Intro separat über Intro-Buttons)."
        )

    results: list[FolderUnifiedCutResult] = []
    prior_plans: list[Any] = []
    total = len(contexts)
    for index, context in enumerate(contexts, start=1):
        label = context.folder_name or "(gesamtes Skript)"
        if progress_callback is not None:
            progress_callback(label, index, total)
        ledger = _used_in_ledger_text(prior_plans)
        result = generate_unified_cut_for_folder(
            project,
            context.folder_name,
            provider=provider,
            model=model,
            llm_callable=llm_callable,
            context=context,
            used_in_ledger_text=ledger,
        )
        results.append(result)
        if result.status == "PASS" and result.plan is not None:
            prior_plans.append(result.plan)
    return results


def merge_and_persist_unified_cuts(
    project: Project,
    results: list[FolderUnifiedCutResult],
) -> Any:
    """Merged Kapitel-Unified-Pläne → ``unified_cut_plan.json`` (+ Rough/Gaps-Schatten)."""
    from otio_app.services.without_voiceover_enhanced.models import (
        CutBoundary,
        CutSlot,
        UnifiedCutPlanDocument,
    )
    from otio_app.services.without_voiceover_enhanced.paths import (
        unified_cut_plan_path,
    )
    from otio_app.services.without_voiceover_enhanced.unified_cut_plan import (
        unified_to_rough,
    )

    locked = require_locked_script(project)
    ok = [r for r in results if r.status == "PASS" and r.plan is not None]
    fail = [r for r in results if r.status != "PASS"]
    if not ok:
        details = "; ".join(f"{r.folder_name}: {r.error}" for r in fail) or "unbekannt"
        raise CutPlanError(f"Unified Cut fehlgeschlagen für alle Kapitel. {details}")

    boundaries: list[CutBoundary] = []
    slots: list[CutSlot] = []
    pauses: list[PauseDirective] = []
    seen_pause: set[str] = set()
    preroll: float | None = None
    postroll: float | None = None

    for result in ok:
        plan = result.plan
        assert plan is not None
        if plan.voiceover_preroll_sec is not None and preroll is None:
            preroll = plan.voiceover_preroll_sec
        if plan.voiceover_postroll_sec is not None and postroll is None:
            postroll = plan.voiceover_postroll_sec
        for pause in plan.pause_directives:
            # E2E-4: chapter_transition wird durch Kapitelhülle abgedeckt.
            if str(pause.pause_function or "").strip().lower() == "chapter_transition":
                continue
            sentence_id = str(pause.after_sentence_id or "").strip()
            key = (
                f"sentence:{sentence_id}"
                if sentence_id
                else f"segment:{pause.after_segment_id}"
            )
            if key in seen_pause:
                continue
            seen_pause.add(key)
            pauses.append(pause)
        # E2E-4: keine bridge_*-Slots. Kapitel N+1 teilt die letzte Grenze von N
        # (VO-Ende ≈ VO-Start bei ignorierter chapter_transition-Pause).
        if not boundaries:
            boundaries.extend(plan.boundaries)
            slots.extend(plan.slots)
            continue
        if not plan.boundaries or not plan.slots:
            continue
        next_first = plan.boundaries[0]
        next_slots = list(plan.slots)
        first_slot = next_slots[0].model_copy(
            update={
                "start_sentence_id": str(next_first.sentence_id or "").strip() or None
            }
        )
        next_slots[0] = first_slot
        boundaries.extend(plan.boundaries[1:])
        slots.extend(next_slots)

    merged = UnifiedCutPlanDocument(
        script_version=locked.script_version,
        pause_directives=pauses,
        boundaries=boundaries,
        slots=slots,
        voiceover_preroll_sec=preroll,
        voiceover_postroll_sec=postroll,
    )
    rough, coverage = unified_to_rough(merged)
    from otio_app.services.without_voiceover_enhanced.gap_search_concepts import (
        enrich_coverage_search_concepts,
    )

    coverage = enrich_coverage_search_concepts(project, coverage, plan=merged)

    # Vorhandenes Intro-Fragment nach Kapitel-Merge wieder vorne einhängen.
    from otio_app.services.without_voiceover_enhanced.intro_cut_service import (
        intro_unified_cut_plan_path,
        merge_intro_and_body_plans,
        split_intro_from_unified,
    )

    existing = load_model(unified_cut_plan_path(project), UnifiedCutPlanDocument)
    intro_plan = load_model(intro_unified_cut_plan_path(project), UnifiedCutPlanDocument)
    if intro_plan is None and existing is not None:
        intro_plan, _ = split_intro_from_unified(existing)
    if intro_plan is not None:
        merged = merge_intro_and_body_plans(
            intro=intro_plan,
            body=merged,
            script_version=locked.script_version,
        )
        rough, coverage = unified_to_rough(merged)
        coverage = enrich_coverage_search_concepts(project, coverage, plan=merged)
        pauses = list(merged.pause_directives)

    write_json(unified_cut_plan_path(project), merged)
    write_json(rough_cut_plan_path(project), rough)
    write_json(coverage_gaps_path(project), coverage)
    write_json(
        pause_directives_path(project),
        {"directives": [d.model_dump(mode="json") for d in pauses]},
    )
    return merged


def mini_repair_unified_plan(
    project: Project,
    plan: Any,
    report: Any,
    *,
    provider: str = "openai",
    model: str = "gpt-5.6-terra",
    llm_callable: Callable[..., Any] | None = None,
) -> Any:
    """Optionaler Mini-Repair: nur betroffene Slots ± Nachbarn, gleiches Format.

    Default-Pfad ruft dies nur bei ``enable_unified_mini_repair`` und Schwellwert.
    """
    from otio_app.services.voiceover_generation.model_settings_service import (
        resolve_llm_model_id,
    )
    from otio_app.services.without_voiceover_enhanced.models import (
        GapMergeReport,
        UnifiedCutPlanDocument,
    )
    from otio_app.services.without_voiceover_enhanced.unified_cut_plan import (
        parse_unified_cut_response,
    )

    if not isinstance(plan, UnifiedCutPlanDocument):
        raise CutPlanError("mini_repair erwartet UnifiedCutPlanDocument.")
    if not isinstance(report, GapMergeReport):
        raise CutPlanError("mini_repair erwartet GapMergeReport.")

    affected_ids: set[str] = set(report.review_shot_ids or [])
    for slot_result in report.slots or []:
        if slot_result.status in {"open_none", "failed"}:
            affected_ids.add(slot_result.shot_id)
    if not affected_ids:
        return plan

    index_by_id = {slot.slot_id: i for i, slot in enumerate(plan.slots)}
    windows: set[int] = set()
    for shot_id in affected_ids:
        index = index_by_id.get(shot_id)
        if index is None:
            continue
        for neighbor in (index - 1, index, index + 1):
            if 0 <= neighbor < len(plan.slots):
                windows.add(neighbor)
    if not windows:
        return plan

    ordered_idx = sorted(windows)
    lo, hi = ordered_idx[0], ordered_idx[-1]
    # Grenzen für [lo..hi] Slots = boundaries[lo .. hi+1]
    patch_boundaries = plan.boundaries[lo : hi + 2]
    patch_slots = plan.slots[lo : hi + 1]
    patch_doc = {
        "pause_directives": [],
        "boundaries": [b.model_dump(mode="json") for b in patch_boundaries],
        "slots": [s.model_dump(mode="json") for s in patch_slots],
    }
    prompt = (
        "You are repairing a UNIFIED cut-plan fragment. Return STRICT JSON only "
        "with the same schema (boundaries + slots). Keep len(slots)==len(boundaries)-1. "
        "Only improve asset_fit / local_asset_id / gap fields for weak/none slots; "
        "do not invent sentence_ids. Keep cut_ids/slot_ids stable when possible.\n\n"
        f"FRAGMENT:\n{json.dumps(patch_doc, ensure_ascii=False, indent=2)}"
    )
    model_id = resolve_llm_model_id(provider, model)
    if llm_callable is not None:
        try:
            raw = llm_callable(prompt=prompt, model=model_id, images=[])
        except TypeError:
            raw = llm_callable(prompt=prompt, model=model_id)
        raw_text = raw if isinstance(raw, str) else getattr(raw, "raw_text", str(raw))
    else:
        raw_text = generate_plan_text_with_metadata(
            prompt=prompt, model=model_id, images=None
        ).raw_text

    repaired_fragment = parse_unified_cut_response(raw_text, plan.script_version)
    if len(repaired_fragment.slots) != len(patch_slots):
        raise CutPlanError(
            "Mini-Repair: Slot-Anzahl der Patch-Antwort stimmt nicht."
        )
    if len(repaired_fragment.boundaries) != len(patch_boundaries):
        raise CutPlanError(
            "Mini-Repair: Boundary-Anzahl der Patch-Antwort stimmt nicht."
        )

    new_boundaries = list(plan.boundaries)
    new_slots = list(plan.slots)
    new_boundaries[lo : hi + 2] = list(repaired_fragment.boundaries)
    new_slots[lo : hi + 1] = list(repaired_fragment.slots)
    return UnifiedCutPlanDocument(
        script_version=plan.script_version,
        pause_directives=list(plan.pause_directives),
        boundaries=new_boundaries,
        slots=new_slots,
        voiceover_preroll_sec=plan.voiceover_preroll_sec,
        voiceover_postroll_sec=plan.voiceover_postroll_sec,
    )


def generate_unified_cut_plan(
    project: Project,
    *,
    provider: str = "openai",
    model: str = "gpt-5.6-terra",
    llm_callable: Callable[..., Any] | None = None,
    progress_callback: Callable[[str, int, int], None] | None = None,
) -> Any:
    """Nur LLM: Unified Cut Plan erzeugen und als ``unified_cut_plan.json`` speichern.

    Kein Python-Timing, kein Gap-Merge — bewusst entkoppelt vom Resolver.
    """
    results = generate_all_unified_cuts(
        project,
        provider=provider,
        model=model,
        llm_callable=llm_callable,
        progress_callback=progress_callback,
    )
    return merge_and_persist_unified_cuts(project, results)


def resolve_unified_cut_plan_timeline(
    project: Project,
    *,
    plan: Any | None = None,
    run_gap_merge: bool = True,
    provider: str = "openai",
    model: str = "gpt-5.6-terra",
    llm_callable: Callable[..., Any] | None = None,
) -> tuple[Any, Any, Any | None]:
    """Python-Timing (+ optional Gap-Merge / Mini-Repair) aus gespeichertem Plan.

    Erwartet einen Unified Cut Plan (Argument oder Disk). Kein neuer LLM-Lauf,
    außer optionalem Mini-Repair wenn explizit aktiviert.
    """
    from otio_app.services.without_voiceover_enhanced.cut_rhythm_validator import (
        should_run_unified_mini_repair,
    )
    from otio_app.services.without_voiceover_enhanced.gap_merge_service import (
        merge_export_ready_gaps_into_timeline,
    )
    from otio_app.services.without_voiceover_enhanced.models import (
        ResolvedTimelineDocument,
        UnifiedCutPlanDocument,
    )
    from otio_app.services.without_voiceover_enhanced.paths import (
        resolved_timeline_path,
        unified_cut_plan_path,
    )
    from otio_app.services.without_voiceover_enhanced.unified_timeline_service import (
        UnifiedTimelineError,
        resolve_unified_timeline,
    )

    if plan is None:
        plan = load_model(unified_cut_plan_path(project), UnifiedCutPlanDocument)
    if plan is None:
        raise CutPlanError(
            "Unified Cut Plan fehlt — zuerst „Unified Cut Plan erzeugen (LLM)“."
        )

    # Timing persistiert die Timeline auch bei Envelope-Fehlern; Merge muss
    # trotzdem laufen (sonst bleiben Funnel-Accepted als Placeholder).
    timing_error: UnifiedTimelineError | None = None
    try:
        resolved = resolve_unified_timeline(
            project, plan, allow_open_gaps=True, persist=True
        )
    except UnifiedTimelineError as exc:
        timing_error = exc
        resolved = load_model(resolved_timeline_path(project), ResolvedTimelineDocument)
        if resolved is None:
            raise

    merge_report = None
    if run_gap_merge and resolved is not None:
        try:
            resolved, merge_report = merge_export_ready_gaps_into_timeline(
                project,
                timeline=resolved,
                require_closed_none=False,
                persist=True,
            )
        except Exception:  # noqa: BLE001 — Merge optional wenn noch keine Supplements
            merge_report = None

    options = load_cut_plan_options(project)
    if (
        timing_error is None
        and merge_report is not None
        and should_run_unified_mini_repair(
            merge_report,
            total_slots=len(plan.slots),
            enabled=bool(options.enable_unified_mini_repair),
            threshold=float(options.unified_mini_repair_threshold),
        )
    ):
        repaired = mini_repair_unified_plan(
            project,
            plan,
            merge_report,
            provider=provider,
            model=model,
            llm_callable=llm_callable,
        )
        write_json(unified_cut_plan_path(project), repaired)
        plan = repaired
        try:
            resolved = resolve_unified_timeline(
                project, plan, allow_open_gaps=True, persist=True
            )
        except UnifiedTimelineError as exc:
            timing_error = exc
            resolved = load_model(
                resolved_timeline_path(project), ResolvedTimelineDocument
            )
        if resolved is not None:
            resolved, merge_report = merge_export_ready_gaps_into_timeline(
                project,
                timeline=resolved,
                require_closed_none=False,
                persist=True,
            )

    # Timing-Fehler bleiben in resolved.errors; Merge ist trotzdem gelaufen.
    # Kein Re-Raise — sonst sieht die UI den Merge-Erfolg nicht.
    return plan, resolved, merge_report


def generate_unified_cut_plan_and_timeline(
    project: Project,
    *,
    provider: str = "openai",
    model: str = "gpt-5.6-terra",
    llm_callable: Callable[..., Any] | None = None,
    progress_callback: Callable[[str, int, int], None] | None = None,
    run_gap_merge: bool = True,
) -> tuple[Any, Any, Any | None]:
    """Kombi-Orchestrierung: Unified LLM → Timing → optional Gap-Merge.

    Für Tests/Automation. Die UI nutzt die entkoppelten Schritte
    ``generate_unified_cut_plan`` und ``resolve_unified_cut_plan_timeline``.
    """
    plan = generate_unified_cut_plan(
        project,
        provider=provider,
        model=model,
        llm_callable=llm_callable,
        progress_callback=progress_callback,
    )
    return resolve_unified_cut_plan_timeline(
        project,
        plan=plan,
        run_gap_merge=run_gap_merge,
        provider=provider,
        model=model,
        llm_callable=llm_callable,
    )
