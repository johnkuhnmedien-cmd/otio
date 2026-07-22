"""Soft-Quoten für Schnittrhythmus (start_cut_alignment + Shotlängen)."""

from __future__ import annotations

from collections import Counter

from otio_app.services.without_voiceover_enhanced.models import (
    FinalCutPlanDocument,
    ResolvedShot,
)

# Zielband aus DEFAULT_CUT_RHYTHM_TARGETS; Toleranz für Soft-Hinweise.
_TARGET_MID = 0.65
_TARGET_BOUNDARY = 0.25
_TARGET_PAUSE = 0.10
_TOLERANCE = 0.20  # absolute Abweichung vom Zielanteil

_SHOT_LEN_LO = 10.0
_SHOT_LEN_HI = 17.0


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
    classified = sum(counts.values())
    if classified >= 4:
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

    lengths = [
        max(0.0, shot.timeline_end_seconds - shot.timeline_start_seconds)
        for shot in resolved_shots
    ]
    if lengths:
        outside = sum(1 for length in lengths if length < _SHOT_LEN_LO or length > _SHOT_LEN_HI)
        if outside:
            median = sorted(lengths)[len(lengths) // 2]
            notes.append(
                f"Cut-Rhythmus: {outside}/{len(lengths)} Shots außerhalb "
                f"{_SHOT_LEN_LO:.0f}–{_SHOT_LEN_HI:.0f}s "
                f"(Median {median:.1f}s, Zielband ~13.5s)."
            )
    return notes
