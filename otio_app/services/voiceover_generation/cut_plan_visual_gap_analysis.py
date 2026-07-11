"""Residual Visual Gap Analysis (Nutzervorgabe, Juli 2026: "die Zeiten
könnten ja rausgefunden werden").

Berechnet visuelle Lücken DIREKT aus dem Cut-Plan-Draft (Timeline-Zeiten,
VisualSegments, Visual-Window-Einstellung) — statt sie aus Validierungs-
Fehlermeldungen zu parsen, die nicht immer `gap_start_sec`/`gap_end_sec`
tragen (z. B. wenn item.blockers aus einer vorherigen, gröberen
Aggregation stammen). Reine, seiteneffektfreie Funktion — schreibt nichts,
löst keine Suche/Downloads aus.

Jede gefundene Lücke wird nach dem gleichen Muster klassifiziert, das der
Nutzer in der Diskussion "wieso 2. Supplement-Runde bzw. Validation Repair
nicht automatisch?" beschrieben hat:

- `FULL_ITEM_MISSING`: das verantwortliche Item hat noch KEIN einziges
  VisualSegment — das ist der bestehende Fall für normale Supplement
  Requests (build_supplement_requests_from_cut_plan), NICHT für Residual
  Gap Requests.
- `MINI_REPAIRABLE_GAP`: das Item hat bereits Segmente, UND die Lücke lässt
  sich durch Kürzen der direkt angrenzenden Segmente sicher schließen
  (siehe compute_black_gap_repair_plan) — das ist der bestehende Fall für
  Validation Repair.
- `RESIDUAL_ITEM_GAP`: das Item hat bereits Segmente (oft schon
  SUPPLEMENT_USED), aber die Lücke ist zu groß für eine sichere
  Nachbar-Kürzung — das ist der NEUE Fall, für den es bisher KEINEN
  automatischen Reparaturpfad gab (siehe Nutzerdiskussion).
- `UNATTRIBUTED_GAP`: keine Item-Zeitspanne (inkl. Visual-Window-
  Erweiterung) überlappt die Lücke — z. B. Videoanfang vor dem ersten
  Audio-Item oder eine Pause, die länger ist als die Visual-Window-
  Erweiterung erlaubt. Bleibt manueller Prüfung vorbehalten."""

from __future__ import annotations

from pydantic import BaseModel, Field

from otio_app.services.voiceover_generation.cut_plan_asset_selector import compute_visual_window_end_sec
from otio_app.services.voiceover_generation.cut_plan_models import CutPlanDocument, CutPlanItem, CutPlanSettings
from otio_app.services.voiceover_generation.cut_plan_validation_repair_apply import compute_black_gap_repair_plan

__all__ = [
    "GAP_KIND_FULL_ITEM_MISSING",
    "GAP_KIND_MINI_REPAIRABLE_GAP",
    "GAP_KIND_RESIDUAL_ITEM_GAP",
    "GAP_KIND_UNATTRIBUTED_GAP",
    "RECOMMENDED_ACTION_SUPPLEMENT_REQUESTS",
    "RECOMMENDED_ACTION_VALIDATION_REPAIR",
    "RECOMMENDED_ACTION_RESIDUAL_GAP_SUPPLEMENT",
    "RECOMMENDED_ACTION_MANUAL_REVIEW",
    "CutPlanVisualGap",
    "analyze_visual_gaps",
    "merge_intervals",
    "uncovered_subintervals",
]

_EPSILON = 0.01

GAP_KIND_FULL_ITEM_MISSING = "FULL_ITEM_MISSING"
GAP_KIND_MINI_REPAIRABLE_GAP = "MINI_REPAIRABLE_GAP"
GAP_KIND_RESIDUAL_ITEM_GAP = "RESIDUAL_ITEM_GAP"
GAP_KIND_UNATTRIBUTED_GAP = "UNATTRIBUTED_GAP"

RECOMMENDED_ACTION_SUPPLEMENT_REQUESTS = "supplement_requests"
RECOMMENDED_ACTION_VALIDATION_REPAIR = "validation_repair"
RECOMMENDED_ACTION_RESIDUAL_GAP_SUPPLEMENT = "residual_gap_supplement"
RECOMMENDED_ACTION_MANUAL_REVIEW = "manual_review"

_RECOMMENDED_ACTION_BY_GAP_KIND: dict[str, str] = {
    GAP_KIND_FULL_ITEM_MISSING: RECOMMENDED_ACTION_SUPPLEMENT_REQUESTS,
    GAP_KIND_MINI_REPAIRABLE_GAP: RECOMMENDED_ACTION_VALIDATION_REPAIR,
    GAP_KIND_RESIDUAL_ITEM_GAP: RECOMMENDED_ACTION_RESIDUAL_GAP_SUPPLEMENT,
    GAP_KIND_UNATTRIBUTED_GAP: RECOMMENDED_ACTION_MANUAL_REVIEW,
}


class CutPlanVisualGap(BaseModel):
    """EINE tatsächlich unbedeckte Zeitspanne, direkt aus Draft-Daten
    berechnet (siehe Modul-Docstring) — kein Parsing von Fehlermeldungen."""

    cut_item_id: str = ""
    folder_name: str = ""
    source_scope: str = ""
    text: str = ""
    visual_intent: str = ""

    gap_start_sec: float = 0.0
    gap_end_sec: float = 0.0
    gap_duration_sec: float = 0.0

    expected_start_sec: float = 0.0
    expected_end_sec: float = 0.0

    item_status: str = ""
    chosen_asset_id: str = ""
    planned_segments_count: int = 0

    gap_kind: str = GAP_KIND_UNATTRIBUTED_GAP
    recommended_action: str = RECOMMENDED_ACTION_MANUAL_REVIEW


def merge_intervals(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Analog zu cut_plan_validator._merge_intervals — lokal duplizierte,
    einfache Intervall-Zusammenführung, um dieses Modul unabhängig vom
    (privaten) Validator-Innenleben zu halten."""
    if not intervals:
        return []
    ordered = sorted(intervals)
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end + _EPSILON:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def uncovered_subintervals(
    merged_intervals: list[tuple[float, float]], span_start: float, span_end: float
) -> list[tuple[float, float]]:
    """Analog zu cut_plan_validator._uncovered_subintervals."""
    if span_end <= span_start + _EPSILON:
        return []
    cursor = span_start
    gaps: list[tuple[float, float]] = []
    for start, end in merged_intervals:
        if end <= cursor + _EPSILON:
            continue
        if start >= span_end - _EPSILON:
            break
        effective_start = max(start, cursor)
        if effective_start > cursor + _EPSILON:
            gaps.append((cursor, effective_start))
        cursor = max(cursor, end)
        if cursor >= span_end - _EPSILON:
            break
    if cursor < span_end - _EPSILON:
        gaps.append((cursor, span_end))
    return gaps


def _classify_gap_kind(item: CutPlanItem | None, cut_plan: CutPlanDocument, settings: CutPlanSettings,
                        gap_start_sec: float, gap_end_sec: float) -> str:
    if item is None:
        return GAP_KIND_UNATTRIBUTED_GAP
    if not item.planned_visual_segments:
        return GAP_KIND_FULL_ITEM_MISSING
    repair_plan = compute_black_gap_repair_plan(cut_plan, gap_start_sec, gap_end_sec, settings)
    if repair_plan is not None:
        return GAP_KIND_MINI_REPAIRABLE_GAP
    return GAP_KIND_RESIDUAL_ITEM_GAP


def _build_gap(
    item: CutPlanItem | None,
    gap_start_sec: float,
    gap_end_sec: float,
    *,
    cut_plan: CutPlanDocument,
    settings: CutPlanSettings,
    expected_start_sec: float = 0.0,
    expected_end_sec: float = 0.0,
) -> CutPlanVisualGap:
    gap_kind = _classify_gap_kind(item, cut_plan, settings, gap_start_sec, gap_end_sec)
    recommended_action = _RECOMMENDED_ACTION_BY_GAP_KIND.get(gap_kind, RECOMMENDED_ACTION_MANUAL_REVIEW)
    if item is None:
        return CutPlanVisualGap(
            gap_start_sec=gap_start_sec,
            gap_end_sec=gap_end_sec,
            gap_duration_sec=max(0.0, gap_end_sec - gap_start_sec),
            gap_kind=gap_kind,
            recommended_action=recommended_action,
        )
    return CutPlanVisualGap(
        cut_item_id=item.cut_item_id,
        folder_name=item.folder_name,
        source_scope=item.source_scope,
        text=item.text,
        visual_intent=item.visual_intent,
        gap_start_sec=gap_start_sec,
        gap_end_sec=gap_end_sec,
        gap_duration_sec=max(0.0, gap_end_sec - gap_start_sec),
        expected_start_sec=expected_start_sec,
        expected_end_sec=expected_end_sec,
        item_status=item.asset_selection_status,
        chosen_asset_id=item.chosen_asset_id,
        planned_segments_count=len(item.planned_visual_segments),
        gap_kind=gap_kind,
        recommended_action=recommended_action,
    )


def analyze_visual_gaps(cut_plan: CutPlanDocument, settings: CutPlanSettings) -> list[CutPlanVisualGap]:
    """Berechnet ALLE tatsächlich unbedeckten Zeitspannen zwischen 0.0 und
    dem Ende der Timeline und ordnet jede Spanne (ggf. in mehrere Teile
    aufgeteilt) dem jeweils verantwortlichen Item zu — anhand von dessen
    EIGENEM Visual-Window-Fenster [timeline_start_sec, compute_visual_
    window_end_sec(...)], nicht nur dessen reiner Audio-Zeitspanne. Das
    löst gezielt den Fall, den die bisherige Validierung nicht attribuieren
    konnte: eine Lücke INNERHALB der ins Visual Window verlängerten Pause
    nach einem bereits versorgten Item (siehe GAP_KIND_RESIDUAL_ITEM_GAP).

    Reine Funktion, kein I/O."""
    all_segment_intervals = [
        (segment.timeline_in_sec, segment.timeline_out_sec)
        for item in cut_plan.items
        for segment in item.planned_visual_segments
    ]
    coverage = merge_intervals(all_segment_intervals)

    timeline_end_candidates = [audio_item.timeline_end_sec for audio_item in cut_plan.audio_items]
    timeline_end_candidates += [item.timeline_end_sec for item in cut_plan.items]
    timeline_end_candidates += [end for _, end in all_segment_intervals]
    timeline_end = max(timeline_end_candidates, default=0.0)
    if timeline_end <= _EPSILON:
        return []

    raw_gaps = uncovered_subintervals(coverage, 0.0, timeline_end)
    if not raw_gaps:
        return []

    windows: list[tuple[CutPlanItem, float, float]] = []
    for index, item in enumerate(cut_plan.items):
        next_item = cut_plan.items[index + 1] if index + 1 < len(cut_plan.items) else None
        expected_end = compute_visual_window_end_sec(item, next_item, settings)
        if expected_end > item.timeline_start_sec + _EPSILON:
            windows.append((item, item.timeline_start_sec, expected_end))
    windows.sort(key=lambda entry: entry[1])

    results: list[CutPlanVisualGap] = []
    for gap_start, gap_end in raw_gaps:
        cursor = gap_start
        overlapping = [
            (max(gap_start, w_start), min(gap_end, w_end), item)
            for item, w_start, w_end in windows
            if w_end > gap_start + _EPSILON and w_start < gap_end - _EPSILON
        ]
        overlapping.sort(key=lambda entry: entry[0])
        for clip_start, clip_end, item in overlapping:
            if clip_end <= cursor + _EPSILON:
                continue
            effective_start = max(clip_start, cursor)
            if effective_start > cursor + _EPSILON:
                results.append(
                    _build_gap(None, cursor, effective_start, cut_plan=cut_plan, settings=settings)
                )
            sub_end = min(clip_end, gap_end)
            if sub_end > effective_start + _EPSILON:
                item_window = next((w for w in windows if w[0] is item), None)
                expected_start_sec, expected_end_sec = (item_window[1], item_window[2]) if item_window else (0.0, 0.0)
                results.append(
                    _build_gap(
                        item, effective_start, sub_end, cut_plan=cut_plan, settings=settings,
                        expected_start_sec=expected_start_sec, expected_end_sec=expected_end_sec,
                    )
                )
            cursor = max(cursor, sub_end)
        if cursor < gap_end - _EPSILON:
            results.append(_build_gap(None, cursor, gap_end, cut_plan=cut_plan, settings=settings))

    return results
