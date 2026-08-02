"""Keyword-Flow: Onset-Validierung und ±1,5-s Bildschnitt-Auflösung."""

from __future__ import annotations

from typing import Any

from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
    KEYWORD_FLOW_ONSET_TOLERANCE_SEC,
)
from otio_app.services.without_voiceover_enhanced.models import (
    CutBoundary,
    SentenceTiming,
    UnifiedCutPlanDocument,
)
from otio_app.services.without_voiceover_enhanced.sentence_timing_prompt import (
    clean_words_for_keyword_flow_prompt,
)


class KeywordFlowTimingError(ValueError):
    pass


def _round3(value: float) -> float:
    return round(float(value), 3)


def validate_keyword_flow_mid_sentence_onsets(
    plan: UnifiedCutPlanDocument,
    *,
    sentence_rows_by_id: dict[str, dict[str, Any]],
) -> None:
    """Fail-closed: mid_sentence offset muss echtem bereinigtem Wort-Onset entsprechen."""
    for boundary in plan.boundaries:
        align = str(boundary.alignment or "").strip().lower()
        if align != "mid_sentence":
            continue
        sentence_id = str(boundary.sentence_id or "").strip()
        row = sentence_rows_by_id.get(sentence_id)
        if row is None:
            raise KeywordFlowTimingError(
                f"{boundary.cut_id}: Keyword Flow mid_sentence bezieht sich auf "
                f"unbekannten Satz {sentence_id}."
            )
        if boundary.offset_seconds is None:
            raise KeywordFlowTimingError(
                f"{boundary.cut_id}: Keyword Flow mid_sentence braucht "
                "offset_seconds vom echten Wort-Onset."
            )
        offset = _round3(float(boundary.offset_seconds))
        span = max(
            0.0,
            float(row.get("end_seconds") or 0.0) - float(row.get("start_seconds") or 0.0),
        )
        if offset < -1e-9 or offset > span + 1e-9:
            raise KeywordFlowTimingError(
                f"{boundary.cut_id}: Keyword-Onset {offset:.3f}s liegt außerhalb "
                f"des Satzes (span={span:.3f}s)."
            )
        words = clean_words_for_keyword_flow_prompt(
            list(row.get("words") or []),
            sentence_id=sentence_id,
        )
        allowed = {_round3(float(w["offset_seconds"])) for w in words}
        if offset not in allowed:
            raise KeywordFlowTimingError(
                f"{boundary.cut_id}: Offset {offset:.3f}s entspricht keinem "
                "echten bereinigten Wort-Onset."
            )


def choose_onset_within_tolerance(
    *,
    onset: float,
    candidates: list[float],
    tolerance_sec: float = KEYWORD_FLOW_ONSET_TOLERANCE_SEC,
) -> float | None:
    """Wählt gültigen Zeitpunkt: exakt → später → früher; bei Gleichstand später."""
    tol = max(0.0, float(tolerance_sec))
    onset_f = float(onset)
    valid = [
        float(c)
        for c in candidates
        if abs(float(c) - onset_f) <= tol + 1e-9
    ]
    if not valid:
        return None
    exact = [c for c in valid if abs(c - onset_f) <= 1e-9]
    if exact:
        return exact[0]
    later = sorted((c for c in valid if c > onset_f + 1e-9), key=lambda c: c - onset_f)
    earlier = sorted(
        (c for c in valid if c < onset_f - 1e-9),
        key=lambda c: onset_f - c,
    )
    if later and earlier:
        if abs(later[0] - onset_f) <= abs(earlier[0] - onset_f) + 1e-12:
            return later[0]
        return earlier[0]
    if later:
        return later[0]
    if earlier:
        return earlier[0]
    return valid[0]


def apply_keyword_flow_onset_tolerance(
    *,
    plan: UnifiedCutPlanDocument,
    raw_times: list[float],
    clamped_times: list[float],
    repairs: list[str],
    tolerance_sec: float = KEYWORD_FLOW_ONSET_TOLERANCE_SEC,
) -> list[float]:
    """Bildgrenzen nur innerhalb ±tolerance um Keyword-Onsets; sonst Fehler.

    Priorität bei mehreren Kandidaten: exakter Onset → später → früher.
    Audio wird nicht getrimmt.
    """
    if len(raw_times) != len(clamped_times) or len(raw_times) != len(plan.boundaries):
        return clamped_times
    tol = max(0.0, float(tolerance_sec))
    out = list(clamped_times)
    for index, boundary in enumerate(plan.boundaries):
        if str(boundary.alignment or "").strip().lower() != "mid_sentence":
            continue
        onset = float(raw_times[index])
        desired = float(clamped_times[index])
        delta = desired - onset
        if abs(delta) > tol + 1e-9:
            raise KeywordFlowTimingError(
                f"{boundary.cut_id}: notwendige Keyword-Verschiebung "
                f"{delta:+.3f}s überschreitet ±{tol:.1f}s "
                f"(onset={onset:.3f}s, desired={desired:.3f}s)."
            )
        # Im Fenster: Clamp-Ergebnis behalten (exact bleibt exact).
        pick = desired
        out[index] = pick
        if abs(delta) > 1e-6:
            repairs.append(
                f"{boundary.cut_id}: keyword_flow onset shift "
                f"{onset:.3f}s → {pick:.3f}s (Δ={delta:+.3f}s) "
                f"within ±{tol:.1f}s."
            )
    for index in range(1, len(out)):
        if out[index] + 1e-9 < out[index - 1]:
            boundary = plan.boundaries[index]
            onset = float(raw_times[index])
            fixed = out[index - 1]
            if abs(fixed - onset) > tol + 1e-9:
                raise KeywordFlowTimingError(
                    f"{boundary.cut_id}: Monotonie-Korrektur {fixed:.3f}s "
                    f"außerhalb ±{tol:.1f}s um Onset {onset:.3f}s."
                )
            repairs.append(
                f"{boundary.cut_id}: keyword_flow monotone clamp → {fixed:.3f}s."
            )
            out[index] = fixed
    return out


def sentence_rows_from_alignments(
    *,
    sentence_index: dict[str, SentenceTiming],
    words_by_segment: dict[str, list[dict[str, Any]]],
) -> dict[str, dict[str, Any]]:
    """Baut Sentence-Rows mit bereinigten words[] für Onset-Checks."""
    from otio_app.services.without_voiceover_enhanced.sentence_timing_prompt import (
        attach_words_to_sentence_row,
        slim_sentence_row,
    )

    rows: dict[str, dict[str, Any]] = {}
    for sentence in sentence_index.values():
        row = slim_sentence_row(sentence)
        seg_words = list(words_by_segment.get(sentence.segment_id) or [])
        if seg_words:
            row = attach_words_to_sentence_row(row, seg_words)
            row["words"] = clean_words_for_keyword_flow_prompt(
                list(row.get("words") or []),
                sentence_id=sentence.sentence_id,
            )
        else:
            row["words"] = []
        rows[sentence.sentence_id] = row
    return rows
