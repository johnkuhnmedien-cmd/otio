"""Phase 8.5: Visual Coverage Fix.

Verlängert bereits gewählte VisualSegments, damit weder der initiale
Audio-Vorlauf (initial_audio_offset_sec) noch die Pausen zwischen Sektionen
(pause_between_sections_sec) zu Schwarzbild führen. Erzeugt KEINE neue
redaktionelle Asset-Entscheidung, KEINE neuen CutPlanItems, KEINE Supplement-
Suche, kein Transcoding. Audio-Zeiten (CutPlanAudioItem) bleiben unverändert
— nur bereits vorhandene VisualSegments werden erweitert."""

from __future__ import annotations

from pathlib import Path

from otio_app.services.media_utils import probe_duration_seconds
from otio_app.services.voiceover_generation.cut_plan_models import (
    CutPlanDocument,
    CutPlanItem,
    CutPlanSettings,
    VisualSegment,
)

__all__ = [
    "apply_visual_coverage_extensions",
    "extend_first_visual_to_timeline_zero",
    "extend_section_end_visuals_over_pauses",
    "find_first_visual_segment",
    "find_last_visual_segment_before_time",
]

_EPSILON = 0.01
_TOLERANCE = 0.05  # gleiche Toleranz wie cut_plan_validator._TIME_TOLERANCE

REASON_INITIAL_PREROLL_EXTENSION = "initial_preroll_extension"
REASON_SECTION_PAUSE_HOLD = "section_pause_hold"


def _combine_reason(original_reason: str, extension_marker: str) -> str:
    """Ein Segment kann sowohl den initialen Vorlauf ALS AUCH eine
    anschließende Sektions-Pause abdecken (z. B. das allererste Intro-
    Segment). reason wird deshalb um den Erweiterungs-Marker ERGÄNZT statt
    überschrieben, damit beide Erweiterungen in der UI/Validierung sichtbar
    bleiben (siehe cut_plan_validator._reason_has_marker)."""
    parts = [part for part in original_reason.split("+") if part]
    if extension_marker not in parts:
        parts.append(extension_marker)
    return "+".join(parts)


def find_first_visual_segment(cut_plan: CutPlanDocument) -> tuple[CutPlanItem, VisualSegment] | None:
    """Das VisualSegment mit dem kleinsten timeline_in_sec über den gesamten
    Cut Plan (Intro + alle Folder)."""
    all_pairs = [
        (item, segment) for item in cut_plan.items for segment in item.planned_visual_segments
    ]
    if not all_pairs:
        return None
    return min(all_pairs, key=lambda pair: pair[1].timeline_in_sec)


def find_last_visual_segment_before_time(
    cut_plan: CutPlanDocument, timeline_time_sec: float
) -> tuple[CutPlanItem, VisualSegment] | None:
    """Das VisualSegment mit dem größten timeline_out_sec, das bei oder vor
    timeline_time_sec endet."""
    candidates = [
        (item, segment)
        for item in cut_plan.items
        for segment in item.planned_visual_segments
        if segment.timeline_out_sec <= timeline_time_sec + _EPSILON
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda pair: pair[1].timeline_out_sec)


def _video_can_extend_to(asset_path: str, new_source_out_sec: float) -> bool:
    """True, wenn die Videodatei lang genug ist, ODER die Dauer nicht
    ermittelt werden kann (dann optimistisch verlängern — die eigentliche
    Prüfung übernimmt ohnehin validate_visual_segments in Phase 8.4)."""
    path = Path(asset_path)
    if not path.is_file():
        return False
    real_duration = probe_duration_seconds(path)
    if real_duration is None:
        return True
    return new_source_out_sec <= real_duration + _TOLERANCE


def _replace_segment(
    cut_plan: CutPlanDocument, cut_item_id: str, segment_id: str, new_segment: VisualSegment
) -> CutPlanDocument:
    updated_items: list[CutPlanItem] = []
    for item in cut_plan.items:
        if item.cut_item_id != cut_item_id:
            updated_items.append(item)
            continue
        updated_segments = [
            new_segment if segment.segment_id == segment_id else segment
            for segment in item.planned_visual_segments
        ]
        updated_items.append(item.model_copy(update={"planned_visual_segments": updated_segments}))
    return cut_plan.model_copy(update={"items": updated_items})


def extend_first_visual_to_timeline_zero(cut_plan: CutPlanDocument) -> CutPlanDocument:
    """§3: Wenn das erste AudioItem nicht bei 0.0 beginnt (initial_audio_offset_sec
    > 0), wird das allererste VisualSegment nach vorne bis 0.0 verlängert —
    Audio-Zeiten bleiben unverändert. Bricht leise ab (kein Blocker hier),
    wenn keine Erweiterung möglich ist — das verbleibende Loch wird von
    validate_no_black_gap_during_voiceover (Phase 8.4) erkannt."""
    if not cut_plan.audio_items:
        return cut_plan

    first_audio = min(cut_plan.audio_items, key=lambda audio_item: audio_item.timeline_start_sec)
    if first_audio.timeline_start_sec <= _EPSILON:
        return cut_plan  # kein Vorlauf vorhanden -> nichts zu tun

    found = find_first_visual_segment(cut_plan)
    if found is None:
        return cut_plan  # keine VisualSegments vorhanden -> Validator meldet die Lücke
    item, segment = found

    if segment.timeline_in_sec <= _EPSILON:
        return cut_plan  # bereits abgedeckt

    additional = segment.timeline_in_sec - 0.0
    new_duration = segment.duration_sec + additional

    if segment.asset_type == "image":
        new_source_in_sec = 0.0
        new_source_out_sec = new_duration
    else:
        new_source_in_sec = segment.source_in_sec  # video_head_trim_sec bleibt unverändert
        new_source_out_sec = segment.source_out_sec + additional
        if not _video_can_extend_to(segment.asset_path, new_source_out_sec):
            return cut_plan  # Video zu kurz -> keine stille Erweiterung, Loch bleibt sichtbar

    updated_segment = segment.model_copy(
        update={
            "timeline_in_sec": 0.0,
            "duration_sec": new_duration,
            "source_in_sec": new_source_in_sec,
            "source_out_sec": new_source_out_sec,
            "reason": _combine_reason(segment.reason, REASON_INITIAL_PREROLL_EXTENSION),
        }
    )
    return _replace_segment(cut_plan, item.cut_item_id, segment.segment_id, updated_segment)


def extend_section_end_visuals_over_pauses(
    cut_plan: CutPlanDocument, settings: CutPlanSettings
) -> CutPlanDocument:
    """§4: Für jede Pause zwischen zwei AudioItems wird das letzte VisualSegment
    der vorherigen Sektion bis zum Start der nächsten Sektion verlängert.
    Das nächste Visual bleibt unverändert (kein Vorziehen). Nur exakt
    konfigurierte pause_between_sections_sec (± Toleranz) wird überbrückt —
    echte, unerwartete Lücken werden NICHT blind gestreckt und bleiben für
    validate_no_black_gap_during_voiceover sichtbar."""
    audio_items = sorted(cut_plan.audio_items, key=lambda audio_item: audio_item.timeline_start_sec)
    updated_cut_plan = cut_plan

    for i in range(len(audio_items) - 1):
        current_audio = audio_items[i]
        next_audio = audio_items[i + 1]
        pause_duration = next_audio.timeline_start_sec - current_audio.timeline_end_sec
        if pause_duration <= _EPSILON:
            continue
        if abs(pause_duration - settings.pause_between_sections_sec) > _TOLERANCE:
            continue  # keine blinde Streckung über unerwartet große/kleine Lücken hinweg

        found = find_last_visual_segment_before_time(updated_cut_plan, current_audio.timeline_end_sec)
        if found is None:
            continue
        item, segment = found

        if segment.timeline_out_sec >= next_audio.timeline_start_sec - _EPSILON:
            continue  # bereits abgedeckt

        additional = next_audio.timeline_start_sec - segment.timeline_out_sec
        new_duration = segment.duration_sec + additional
        new_source_out_sec = segment.source_out_sec + additional

        if segment.asset_type != "image":
            if not _video_can_extend_to(segment.asset_path, new_source_out_sec):
                continue  # Video zu kurz -> Loch bleibt sichtbar, kein stilles Schwarzbild

        updated_segment = segment.model_copy(
            update={
                "timeline_out_sec": next_audio.timeline_start_sec,
                "duration_sec": new_duration,
                "source_out_sec": new_source_out_sec,
                "reason": _combine_reason(segment.reason, REASON_SECTION_PAUSE_HOLD),
            }
        )
        updated_cut_plan = _replace_segment(updated_cut_plan, item.cut_item_id, segment.segment_id, updated_segment)

    return updated_cut_plan


def apply_visual_coverage_extensions(cut_plan: CutPlanDocument, settings: CutPlanSettings) -> CutPlanDocument:
    """Orchestriert beide Coverage-Erweiterungen. Läuft nach der Asset-
    Auswahl, bevor validiert wird (siehe apply_asset_selection_to_cut_plan)."""
    updated = extend_first_visual_to_timeline_zero(cut_plan)
    updated = extend_section_end_visuals_over_pauses(updated, settings)
    return updated
