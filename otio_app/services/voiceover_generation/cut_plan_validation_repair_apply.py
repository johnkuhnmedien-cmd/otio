"""Validation Repair (Nutzervorgabe, Juli 2026): Phase 4/5 — tatsächliche
Reparatur-Ausführung für BLACK_GAP-Requests.

Kernproblem (Nutzervorgabe): eine kurze visuelle Lücke (z. B. 0.6s) darf
NICHT einfach durch ein neues, exakt lückenfüllendes VisualSegment
geschlossen werden — das neue Segment wäre selbst wieder ein
SHOT_TOO_SHORT-Kandidat (< shot_min_sec) und würde im schlimmsten Fall
nur für einen Wimpernschlag aufblitzen. Stattdessen wird ein Reparatur-
FENSTER berechnet, das mindestens shot_min_sec lang ist — reicht die
reine Lücke dafür nicht aus, werden die BEIDEN direkt angrenzenden
VisualSegments (das davor UND das danach, siehe Nutzervorgabe) jeweils
so weit gekürzt, wie sie es ohne selbst unter shot_min_sec zu fallen
erlauben. Reicht auch das nicht aus (beide Nachbarn haben zusammen zu
wenig 'Kürzungs-Spielraum'), wird KEIN Reparatur-Plan erzeugt — der
Aufrufer soll dann auf die normale Supplement-Pipeline zurückfallen
(volles Ersatz-Asset für das betroffene Item), statt eine unsichere
Reparatur zu erzwingen.

Trimmt AUSSCHLIESSLICH die visuelle Platzierung (VisualSegment.
timeline_in_sec/timeline_out_sec/duration_sec/source_in_sec/
source_out_sec) — NIEMALS CutPlanItem.timeline_start_sec/
timeline_end_sec oder Audio-Zeiten. Reine Funktionen, kein I/O, keine
Downloads, keine LLM-Aufrufe (siehe cut_plan_validation_repair.py für
Request-Erkennung/-Aufbau, spätere Phasen für Foto-first-Suche)."""

from __future__ import annotations

from dataclasses import dataclass

from otio_app.services.voiceover_generation.cut_plan_models import (
    CutPlanDocument,
    CutPlanItem,
    CutPlanSettings,
    VisualSegment,
)
from otio_app.services.voiceover_generation.cut_plan_supplement_models import CutPlanSupplementAsset
from otio_app.services.voiceover_generation.cut_plan_visual_coverage import all_segments_sorted

__all__ = [
    "REASON_BLACK_GAP_REPAIR_SUPPLEMENT",
    "REASON_BLACK_GAP_REPAIR_TRIM",
    "BlackGapRepairPlan",
    "compute_black_gap_repair_plan",
    "apply_black_gap_repair",
]

_EPSILON = 0.01

REASON_BLACK_GAP_REPAIR_SUPPLEMENT = "black_gap_repair_supplement"
REASON_BLACK_GAP_REPAIR_TRIM = "black_gap_repair_trim"


def _combine_reason(original_reason: str, extension_marker: str) -> str:
    parts = [part for part in original_reason.split("+") if part]
    if extension_marker not in parts:
        parts.append(extension_marker)
    return "+".join(parts)


@dataclass(frozen=True)
class BlackGapRepairPlan:
    """Ergebnis von compute_black_gap_repair_plan — beschreibt EIN
    Zeitfenster [window_start_sec, window_end_sec], das mindestens
    shot_min_sec lang ist und die ursprüngliche Lücke vollständig
    abdeckt. take_from_prev_sec/take_from_next_sec > 0 bedeutet: das
    jeweils angrenzende VisualSegment muss um diesen Betrag gekürzt
    werden, damit KEIN Overlap mit dem neuen Reparatur-Segment entsteht."""

    window_start_sec: float
    window_end_sec: float
    take_from_prev_sec: float = 0.0
    take_from_next_sec: float = 0.0
    prev_item_id: str = ""
    prev_segment_id: str = ""
    next_item_id: str = ""
    next_segment_id: str = ""

    @property
    def window_duration_sec(self) -> float:
        return self.window_end_sec - self.window_start_sec


def compute_black_gap_repair_plan(
    cut_plan: CutPlanDocument, gap_start_sec: float, gap_end_sec: float, settings: CutPlanSettings
) -> BlackGapRepairPlan | None:
    """Berechnet das Reparatur-Fenster für eine BLACK_GAP-Lücke
    [gap_start_sec, gap_end_sec]. Gibt None zurück, wenn:

    - die Lücke selbst schon größer als shot_max_sec ist (das ist kein
      Fall für DIESE Mini-Reparatur-Strategie mehr — ein komplett
      fehlendes Item mit mehreren Sekunden Lücke braucht ein normales,
      volles Ersatz-Asset, siehe cut_plan_supplement_bridge.py), oder
    - die direkt angrenzenden VisualSegments zusammen nicht genug
      'Kürzungs-Spielraum' haben, um das Fenster auf mindestens
      shot_min_sec zu erweitern, ohne selbst unter shot_min_sec zu
      fallen."""
    gap_duration = gap_end_sec - gap_start_sec
    if gap_duration <= _EPSILON:
        return None
    if gap_duration > settings.shot_max_sec + _EPSILON:
        return None

    needed_extra = max(0.0, settings.shot_min_sec - gap_duration)
    if needed_extra <= _EPSILON:
        return BlackGapRepairPlan(window_start_sec=gap_start_sec, window_end_sec=gap_end_sec)

    prev_pair: tuple[VisualSegment, CutPlanItem] | None = None
    next_pair: tuple[VisualSegment, CutPlanItem] | None = None
    for segment, item in all_segments_sorted(cut_plan):
        if segment.timeline_out_sec <= gap_start_sec + _EPSILON:
            if prev_pair is None or segment.timeline_out_sec > prev_pair[0].timeline_out_sec:
                prev_pair = (segment, item)
        if segment.timeline_in_sec >= gap_end_sec - _EPSILON:
            if next_pair is None or segment.timeline_in_sec < next_pair[0].timeline_in_sec:
                next_pair = (segment, item)

    prev_room = max(0.0, prev_pair[0].duration_sec - settings.shot_min_sec) if prev_pair else 0.0
    next_room = max(0.0, next_pair[0].duration_sec - settings.shot_min_sec) if next_pair else 0.0
    if prev_room + next_room + _EPSILON < needed_extra:
        return None  # nicht sicher reparierbar -> Aufrufer soll auf vollen Ersatz zurückfallen

    take_prev = min(prev_room, needed_extra / 2)
    take_next = min(next_room, needed_extra / 2)
    remaining = needed_extra - take_prev - take_next
    if remaining > _EPSILON:
        extra_from_prev = min(prev_room - take_prev, remaining)
        take_prev += extra_from_prev
        remaining -= extra_from_prev
    if remaining > _EPSILON:
        extra_from_next = min(next_room - take_next, remaining)
        take_next += extra_from_next
        remaining -= extra_from_next

    return BlackGapRepairPlan(
        window_start_sec=gap_start_sec - take_prev,
        window_end_sec=gap_end_sec + take_next,
        take_from_prev_sec=take_prev,
        take_from_next_sec=take_next,
        prev_item_id=prev_pair[1].cut_item_id if prev_pair else "",
        prev_segment_id=prev_pair[0].segment_id if prev_pair else "",
        next_item_id=next_pair[1].cut_item_id if next_pair else "",
        next_segment_id=next_pair[0].segment_id if next_pair else "",
    )


def _trim_segment_end(segment: VisualSegment, shrink_amount: float) -> VisualSegment:
    """Kürzt segment.timeline_out_sec um shrink_amount (timeline_in_sec
    bleibt fest) — identische Logik zu resolve_timeline_overlaps in
    cut_plan_visual_coverage.py, hier dupliziert, da dort privat."""
    new_timeline_out_sec = segment.timeline_out_sec - shrink_amount
    new_duration = segment.duration_sec - shrink_amount
    if segment.asset_type == "image":
        new_source_out_sec = segment.source_in_sec + new_duration
    else:
        new_source_out_sec = segment.source_out_sec - shrink_amount
    return segment.model_copy(
        update={
            "timeline_out_sec": new_timeline_out_sec,
            "duration_sec": new_duration,
            "source_out_sec": new_source_out_sec,
            "reason": _combine_reason(segment.reason, REASON_BLACK_GAP_REPAIR_TRIM),
        }
    )


def _trim_segment_start(segment: VisualSegment, shrink_amount: float) -> VisualSegment:
    """Kürzt segment.timeline_in_sec um shrink_amount nach hinten
    (timeline_out_sec bleibt fest). Für Bilder bleibt source_in_sec bei
    0.0 (Pflicht laut cut_plan_validator.validate_visual_segments), für
    Video wird der Kopf um denselben Betrag versetzt, damit weiterhin
    derselbe Endpunkt des Quellmaterials gezeigt wird."""
    new_timeline_in_sec = segment.timeline_in_sec + shrink_amount
    new_duration = segment.duration_sec - shrink_amount
    if segment.asset_type == "image":
        new_source_in_sec = 0.0
        new_source_out_sec = new_source_in_sec + new_duration
    else:
        new_source_in_sec = segment.source_in_sec + shrink_amount
        new_source_out_sec = segment.source_out_sec
    return segment.model_copy(
        update={
            "timeline_in_sec": new_timeline_in_sec,
            "duration_sec": new_duration,
            "source_in_sec": new_source_in_sec,
            "source_out_sec": new_source_out_sec,
            "reason": _combine_reason(segment.reason, REASON_BLACK_GAP_REPAIR_TRIM),
        }
    )


def _build_repair_segment(
    *,
    cut_item_id: str,
    repair_plan: BlackGapRepairPlan,
    accepted_asset: CutPlanSupplementAsset,
    settings: CutPlanSettings,
) -> VisualSegment:
    """Baut das neue, lückenfüllende VisualSegment. Für Video gilt
    dieselbe video_head_trim_sec-Regel wie beim regulären Supplement-
    Accept (apply_accepted_supplement_to_cut_plan_item) — wirft
    ValueError, wenn das Asset dafür zu kurz ist, damit der Aufrufer den
    nächsten Kandidaten versuchen kann, statt eine zu kurze Quelle
    stillschweigend zu akzeptieren."""
    window_duration = repair_plan.window_duration_sec
    if accepted_asset.asset_type == "video":
        source_in_sec = settings.video_head_trim_sec
        usable_duration_sec = max(0.0, accepted_asset.duration_sec - settings.video_head_trim_sec)
        if window_duration > usable_duration_sec + _EPSILON:
            raise ValueError(
                f"Black-Gap-Repair-Kandidat zu kurz: benötigt {window_duration:.2f}s, verfügbar "
                f"{usable_duration_sec:.2f}s nach video_head_trim_sec ({settings.video_head_trim_sec:.2f}s)."
            )
        source_out_sec = source_in_sec + window_duration
    else:
        source_in_sec = 0.0
        source_out_sec = window_duration

    return VisualSegment(
        segment_id=f"{cut_item_id}_black_gap_repair",
        timeline_in_sec=repair_plan.window_start_sec,
        timeline_out_sec=repair_plan.window_end_sec,
        duration_sec=window_duration,
        asset_id=accepted_asset.asset_id,
        asset_path=accepted_asset.asset_path,
        asset_type=accepted_asset.asset_type,
        source_in_sec=source_in_sec,
        source_out_sec=source_out_sec,
        track="V1",
        reason=REASON_BLACK_GAP_REPAIR_SUPPLEMENT,
    )


def apply_black_gap_repair(
    cut_plan: CutPlanDocument,
    settings: CutPlanSettings,
    *,
    cut_item_id: str,
    repair_plan: BlackGapRepairPlan,
    accepted_asset: CutPlanSupplementAsset,
) -> CutPlanDocument:
    """Wendet einen zuvor berechneten BlackGapRepairPlan an: fügt das neue
    Reparatur-Segment in das Ziel-Item ein und kürzt die betroffenen
    Nachbar-Segmente (falls take_from_prev_sec/take_from_next_sec > 0).
    Reine Funktion, speichert nichts. Wirft ValueError, wenn
    accepted_asset für das Fenster zu kurz ist (siehe
    _build_repair_segment) — der Aufrufer sollte dann den nächsten
    Kandidaten versuchen, analog zum bestehenden Supplement-Accept-Pfad."""
    repair_segment = _build_repair_segment(
        cut_item_id=cut_item_id, repair_plan=repair_plan, accepted_asset=accepted_asset, settings=settings
    )

    updated_items: list[CutPlanItem] = []
    for item in cut_plan.items:
        updated_segments = list(item.planned_visual_segments)

        if repair_plan.take_from_prev_sec > _EPSILON and item.cut_item_id == repair_plan.prev_item_id:
            updated_segments = [
                _trim_segment_end(segment, repair_plan.take_from_prev_sec)
                if segment.segment_id == repair_plan.prev_segment_id
                else segment
                for segment in updated_segments
            ]
        if repair_plan.take_from_next_sec > _EPSILON and item.cut_item_id == repair_plan.next_item_id:
            updated_segments = [
                _trim_segment_start(segment, repair_plan.take_from_next_sec)
                if segment.segment_id == repair_plan.next_segment_id
                else segment
                for segment in updated_segments
            ]

        if item.cut_item_id == cut_item_id:
            updated_segments = sorted(updated_segments + [repair_segment], key=lambda seg: seg.timeline_in_sec)

        if updated_segments != item.planned_visual_segments:
            updated_items.append(item.model_copy(update={"planned_visual_segments": updated_segments}))
        else:
            updated_items.append(item)

    return cut_plan.model_copy(update={"items": updated_items})
