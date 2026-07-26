"""Soft-Quoten für Schnittrhythmus (alignment + Shotlängen + Coverage/Usage)."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from otio_app.services.without_voiceover_enhanced.cut_plan_options import CutPlanOptions
from otio_app.services.without_voiceover_enhanced.intro_script_bridge import (
    is_intro_folder_name,
)
from otio_app.services.without_voiceover_enhanced.models import (
    FinalCutPlanDocument,
    GapMergeReport,
    ResolvedShot,
    ResolvedTimelineDocument,
    UnifiedCutPlanDocument,
)

# Zielband aus DEFAULT_CUT_RHYTHM_TARGETS; Toleranz für Soft-Hinweise.
_TARGET_MID = 0.65
_TARGET_BOUNDARY = 0.25
_TARGET_PAUSE = 0.10
_TOLERANCE = 0.20  # absolute Abweichung vom Zielanteil

_SHOT_LEN_LO = 10.0
_SHOT_LEN_HI = 17.0

# Phase 6: optionaler Mini-Repair wenn Merge-Schwellwert reißt.
DEFAULT_MINI_REPAIR_THRESHOLD = 0.20


@dataclass
class CutRhythmAssessment:
    notes: list[str] = field(default_factory=list)
    alignment_counts: dict[str, int] = field(default_factory=dict)
    shot_lengths: list[float] = field(default_factory=list)
    usage_violations: list[str] = field(default_factory=list)
    reuse_violations: list[str] = field(default_factory=list)
    coverage_notes: list[str] = field(default_factory=list)

    def all_notes(self) -> list[str]:
        return (
            list(self.notes)
            + list(self.coverage_notes)
            + list(self.usage_violations)
            + list(self.reuse_violations)
        )


def assess_cut_rhythm(
    final: FinalCutPlanDocument,
    resolved_shots: list[ResolvedShot],
) -> list[str]:
    """Liefert Repair-/Hinweistexte (keine Hard-Errors)."""
    notes: list[str] = []
    shots = list(final.shots or [])
    total = len(shots)
    if total == 0:
        return notes

    missing = sum(1 for shot in shots if not str(shot.start_cut_alignment or "").strip())
    if missing:
        notes.append(
            f"Cut-Rhythmus: {missing}/{total} Shots ohne start_cut_alignment."
        )

    counts: Counter[str] = Counter()
    for shot in shots:
        key = str(shot.start_cut_alignment or "").strip().lower()
        if key in {"mid_sentence", "sentence_boundary", "in_pause"}:
            counts[key] += 1
    notes.extend(_alignment_mix_notes(counts))

    lengths = [
        max(0.0, shot.timeline_end_seconds - shot.timeline_start_seconds)
        for shot in resolved_shots
    ]
    notes.extend(_shot_length_notes(lengths))
    return notes


def _alignment_mix_notes(counts: Counter[str]) -> list[str]:
    notes: list[str] = []
    classified = sum(counts.values())
    if classified < 4:
        return notes
    mid = counts.get("mid_sentence", 0) / classified
    boundary = counts.get("sentence_boundary", 0) / classified
    pause = counts.get("in_pause", 0) / classified
    if abs(mid - _TARGET_MID) > _TOLERANCE:
        notes.append(
            f"Cut-Rhythmus: mid_sentence={mid:.0%} "
            f"(Ziel ~{_TARGET_MID:.0%}±{_TOLERANCE:.0%})."
        )
    if abs(boundary - _TARGET_BOUNDARY) > _TOLERANCE:
        notes.append(
            f"Cut-Rhythmus: sentence_boundary={boundary:.0%} "
            f"(Ziel ~{_TARGET_BOUNDARY:.0%}±{_TOLERANCE:.0%})."
        )
    if abs(pause - _TARGET_PAUSE) > _TOLERANCE:
        notes.append(
            f"Cut-Rhythmus: in_pause={pause:.0%} "
            f"(Ziel ~{_TARGET_PAUSE:.0%}±{_TOLERANCE:.0%})."
        )
    return notes


def _shot_length_notes(lengths: list[float]) -> list[str]:
    if not lengths:
        return []
    outside = sum(1 for length in lengths if length < _SHOT_LEN_LO or length > _SHOT_LEN_HI)
    if not outside:
        return []
    median = sorted(lengths)[len(lengths) // 2]
    return [
        f"Cut-Rhythmus: {outside}/{len(lengths)} Shots außerhalb "
        f"{_SHOT_LEN_LO:.0f}–{_SHOT_LEN_HI:.0f}s "
        f"(Median {median:.1f}s, Zielband ~13.5s)."
    ]


def _is_intro_folder(folder: str | None) -> bool:
    name = (folder or "").strip().lower()
    return name in {"intro", "introduction"} or name.startswith("intro_")


def assess_unified_cut_quality(
    *,
    plan: UnifiedCutPlanDocument,
    resolved: ResolvedTimelineDocument,
    options: CutPlanOptions,
) -> CutRhythmAssessment:
    """Erweiterte Soft-QS für Unified: Alignment, Länge, Coverage, Usage/Reuse."""
    assessment = CutRhythmAssessment()

    counts: Counter[str] = Counter()
    missing_align = 0
    for boundary in plan.boundaries[:-1]:  # Startgrenze je Slot
        key = str(boundary.alignment or "").strip().lower()
        if key in {"mid_sentence", "sentence_boundary", "in_pause"}:
            counts[key] += 1
        else:
            missing_align += 1
    assessment.alignment_counts = dict(counts)
    if missing_align:
        assessment.notes.append(
            f"Cut-Rhythmus: {missing_align}/{max(1, len(plan.slots))} "
            "Slot-Startgrenzen ohne alignment."
        )
    assessment.notes.extend(_alignment_mix_notes(counts))

    editorial = [
        shot
        for shot in resolved.shots
        if not str(shot.editorial_function or "").startswith("technical_chapter_")
    ]
    lengths = [
        max(0.0, float(s.timeline_end_seconds) - float(s.timeline_start_seconds))
        for s in editorial
        if not s.open_gap
    ]
    assessment.shot_lengths = lengths
    assessment.notes.extend(_shot_length_notes(lengths))

    # Settings-Band soft markieren (Intro ausnehmen; gilt Rhythmus + Keyword-Sync).
    lo = float(options.shot_min_sec)
    hi = float(options.shot_max_sec)
    body_lengths = [
        max(0.0, float(s.timeline_end_seconds) - float(s.timeline_start_seconds))
        for s in editorial
        if not s.open_gap
        and not is_intro_folder_name(s.folder_name)
        and not is_intro_folder_name(s.chapter_id)
        and not is_intro_folder_name(s.shot_id)
    ]
    if body_lengths and hi >= lo:
        outside_settings = sum(
            1
            for length in body_lengths
            if length + 1e-9 < lo or length > hi + 1e-9
        )
        if outside_settings:
            assessment.notes.append(
                f"Cut-Rhythmus: {outside_settings}/{len(body_lengths)} Shots "
                f"außerhalb Settings shot_min/max ({lo:.1f}–{hi:.1f}s)."
            )

    # Visuelle Coverage (nicht-open Shots) innerhalb Kapitel / global.
    frame = 1.0 / max(1.0, float(resolved.fps or 25.0))
    ordered = sorted(editorial, key=lambda s: (s.timeline_start_seconds, s.shot_id))
    filled = [s for s in ordered if not s.open_gap]
    for prev, curr in zip(filled, filled[1:]):
        gap = float(curr.timeline_start_seconds) - float(prev.timeline_end_seconds)
        if gap > frame + 1e-9:
            assessment.coverage_notes.append(
                f"Coverage: Lücke {gap:.2f}s zwischen {prev.shot_id} → {curr.shot_id}."
            )
        elif gap < -(frame + 1e-9):
            assessment.coverage_notes.append(
                f"Coverage: Überlappung {abs(gap):.2f}s zwischen "
                f"{prev.shot_id} → {curr.shot_id}."
            )

    open_gaps = [s for s in ordered if s.open_gap]
    if open_gaps:
        assessment.coverage_notes.append(
            f"Coverage: {len(open_gaps)} offene Gap-Platzhalter "
            f"(Funnel/Merge ausstehend)."
        )

    # Usage / Reuse (soft; Resolver kann zusätzlich hart failen).
    with_asset = [s for s in filled if s.asset_id]
    usage = Counter(s.asset_id for s in with_asset)
    for asset_id, count in sorted(usage.items()):
        folder = next(
            (s.folder_name for s in with_asset if s.asset_id == asset_id),
            "",
        )
        if _is_intro_folder(folder):
            continue
        if count > int(options.max_asset_usage):
            assessment.usage_violations.append(
                f"Usage: Asset {asset_id} {count}× "
                f"(max {options.max_asset_usage})."
            )

    reuse_distance = int(options.min_asset_reuse_distance_shots)
    if reuse_distance > 0:
        last_index: dict[str, int] = {}
        for index, shot in enumerate(with_asset):
            if _is_intro_folder(shot.folder_name):
                continue
            prev_index = last_index.get(shot.asset_id)
            if prev_index is not None:
                gap_shots = index - prev_index - 1
                if gap_shots < reuse_distance:
                    assessment.reuse_violations.append(
                        f"Reuse: {shot.shot_id} Asset {shot.asset_id} nach "
                        f"{gap_shots} Shots (min {reuse_distance})."
                    )
            last_index[shot.asset_id] = index

    return assessment


def merge_report_repair_ratio(
    report: GapMergeReport,
    *,
    total_slots: int,
) -> float:
    """(offene none + Review-Flags) / Slot-Anzahl."""
    if total_slots <= 0:
        return 0.0
    open_none = len(set(report.open_none_gap_ids or []))
    reviews = len(set(report.review_shot_ids or []))
    return (open_none + reviews) / float(total_slots)


def should_run_unified_mini_repair(
    report: GapMergeReport,
    *,
    total_slots: int,
    enabled: bool = False,
    threshold: float = DEFAULT_MINI_REPAIR_THRESHOLD,
) -> bool:
    """Optionaler Repair-Call — Default aus; nur bei Schwellwertüberschreitung."""
    if not enabled:
        return False
    if total_slots <= 0:
        return False
    return merge_report_repair_ratio(report, total_slots=total_slots) > float(threshold)
