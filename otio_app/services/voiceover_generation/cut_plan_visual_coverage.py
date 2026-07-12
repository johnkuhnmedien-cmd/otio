"""Phase 8.5: Visual Coverage Fix.

Verlängert bereits gewählte VisualSegments, damit weder der initiale
Audio-Vorlauf (initial_audio_offset_sec) noch die Pausen zwischen Sektionen
(pause_between_sections_sec) zu Schwarzbild führen. Erzeugt KEINE neue
redaktionelle Asset-Entscheidung, KEINE neuen CutPlanItems, KEINE Supplement-
Suche, kein Transcoding. Audio-Zeiten (CutPlanAudioItem) bleiben unverändert
— nur bereits vorhandene VisualSegments werden erweitert."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from otio_app.services.media_utils import probe_duration_seconds
from otio_app.services.voiceover_generation.cut_plan_models import (
    CutPlanAudioItem,
    CutPlanDocument,
    CutPlanItem,
    CutPlanSettings,
    VisualSegment,
)

__all__ = [
    "apply_visual_coverage_extensions",
    "extend_first_visual_to_timeline_zero",
    "extend_section_end_visuals_over_pauses",
    "close_small_visual_gaps",
    "resolve_timeline_overlaps",
    "find_first_visual_segment",
    "find_last_visual_segment_before_time",
    "find_section_pause_responsible_item",
    "diagnose_section_pause_hold_failure",
    "all_segments_sorted",
    "apply_segment_replacements",
]

_EPSILON = 0.01
_TOLERANCE = 0.05  # gleiche Toleranz wie cut_plan_validator._TIME_TOLERANCE

REASON_INITIAL_PREROLL_EXTENSION = "initial_preroll_extension"
REASON_SECTION_PAUSE_HOLD = "section_pause_hold"
# Phase H (Bugfix aus Phase C, Nutzervorgabe Juli 2026): close_small_visual_
# gaps verlängert ein Segment über eine kleine Sprechpause hinweg — ohne
# diesen Marker würde cut_plan_validator.validate_visual_segments ein knapp
# unter shot_max_sec liegendes Segment, das durch diese Verlängerung
# knapp darüber rutscht, fälschlich als harten SHOT_TOO_LONG-Blocker
# melden (legitime Coverage-Erweiterung, kein Struktur-Fehler — analog zu
# REASON_INITIAL_PREROLL_EXTENSION/REASON_SECTION_PAUSE_HOLD).
REASON_SMALL_GAP_HOLD = "small_gap_hold"

# Phase C (Nutzervorgabe): close_small_visual_gaps schließt NUR kleine,
# durch natürliche Sprechpausen zwischen Sätzen entstandene Lücken
# innerhalb durchgehend aktiven Voice-overs — bewusst weit unterhalb
# typischer BLACK_GAP-Fälle aus fehlenden Supplement-Assets (die reichen
# über mehrere Sekunden bis Minuten, siehe Cut-Plan-Diagnose), damit ein
# echtes Beschaffungsproblem NICHT stillschweigend durch ein eingefrorenes
# Standbild überdeckt wird, sondern weiterhin als BLACK_GAP_DURING_VOICEOVER
# sichtbar bleibt.
#
# Nutzervorgabe (Juli 2026): der Schwellenwert ist jetzt projektspezifisch
# über CutPlanSettings.black_gap_auto_hold_max_sec einstellbar (siehe
# close_small_visual_gaps) — diese Konstante bleibt nur als Fallback für
# Aufrufer ohne Settings (siehe Default-Parameter unten).
_MAX_AUTO_FILLED_GAP_SEC = 1.0


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
    # Nutzervorgabe (Juli 2026): bei einem exakten timeline_out_sec-
    # Gleichstand (z. B. weil ein neu eingefügter Closing-Shot-Slot vor der
    # Überlappungsauflösung testweise bis zum selben Audio-Ende reicht wie
    # das vorherige Segment) gewinnt das Segment mit dem SPÄTEREN
    # timeline_in_sec — chronologisch tatsächlich das letzte/aktuellste
    # Segment vor diesem Zeitpunkt, nicht zufällig das erste in Listenreihenfolge.
    return max(candidates, key=lambda pair: (pair[1].timeline_out_sec, pair[1].timeline_in_sec))


def find_section_pause_responsible_item(
    cut_plan: CutPlanDocument,
    gap_start_sec: float,
    *,
    preceding_audio: CutPlanAudioItem | None = None,
) -> CutPlanItem | None:
    """Ordnet eine Sektionspausen-Lücke dem Closing Shot (oder dem letzten
    Visual) der vorherigen Sektion zu — damit Orphan-BLACK_GAPs eine
    cut_item_id bekommen und manuell reparierbar werden."""
    if preceding_audio is not None and preceding_audio.folder_name:
        section_items = [
            item for item in cut_plan.items if item.folder_name == preceding_audio.folder_name
        ]
    elif preceding_audio is not None:
        section_items = [item for item in cut_plan.items if not item.folder_name]
    else:
        section_items = list(cut_plan.items)

    closing = next((item for item in section_items if item.is_closing_shot), None)
    if closing is not None:
        return closing

    found = find_last_visual_segment_before_time(cut_plan, gap_start_sec)
    if found is not None:
        item, _segment = found
        if preceding_audio is None:
            return item
        if preceding_audio.folder_name:
            if item.folder_name == preceding_audio.folder_name:
                return item
        elif not item.folder_name:
            return item

    if not section_items:
        return None
    return max(section_items, key=lambda item: item.timeline_end_sec)


@dataclass(frozen=True)
class SectionPauseHoldDiagnosis:
    """Menschenlesbare Diagnose, warum der Hold über eine Sektionspause
    fehlgeschlagen ist — für Blocker-Meldung und Manual-Repair-UI."""

    responsible_cut_item_id: str
    responsible_is_closing_shot: bool
    hold_candidate_asset_id: str
    hold_candidate_asset_type: str
    segment_timeline_out_sec: float
    gap_start_sec: float
    gap_end_sec: float
    needed_extend_sec: float
    usable_extend_sec: float | None
    failure_reason: str
    summary: str


def diagnose_section_pause_hold_failure(
    cut_plan: CutPlanDocument,
    gap_start_sec: float,
    gap_end_sec: float,
    *,
    responsible_item: CutPlanItem | None = None,
    preceding_audio: CutPlanAudioItem | None = None,
) -> SectionPauseHoldDiagnosis:
    """Ermittelt Asset, nutzbare Restlänge und Hold-Fehlgrund für eine
    Sektionspausen-Lücke — reine Diagnose, keine Seiteneffekte."""
    item = responsible_item or find_section_pause_responsible_item(
        cut_plan, gap_start_sec, preceding_audio=preceding_audio
    )
    found = find_last_visual_segment_before_time(cut_plan, gap_start_sec)
    if item is None and found is not None:
        item = found[0]

    if item is None:
        return SectionPauseHoldDiagnosis(
            responsible_cut_item_id="",
            responsible_is_closing_shot=False,
            hold_candidate_asset_id="",
            hold_candidate_asset_type="",
            segment_timeline_out_sec=gap_start_sec,
            gap_start_sec=gap_start_sec,
            gap_end_sec=gap_end_sec,
            needed_extend_sec=max(0.0, gap_end_sec - gap_start_sec),
            usable_extend_sec=None,
            failure_reason="NO_RESPONSIBLE_ITEM",
            summary="Keine Cut-Plan-Item-Zuordnung für diese Sektionspause möglich.",
        )

    if found is None:
        return SectionPauseHoldDiagnosis(
            responsible_cut_item_id=item.cut_item_id,
            responsible_is_closing_shot=bool(item.is_closing_shot),
            hold_candidate_asset_id=item.chosen_asset_id,
            hold_candidate_asset_type="",
            segment_timeline_out_sec=item.timeline_end_sec,
            gap_start_sec=gap_start_sec,
            gap_end_sec=gap_end_sec,
            needed_extend_sec=max(0.0, gap_end_sec - gap_start_sec),
            usable_extend_sec=None,
            failure_reason="NO_VISUAL_BEFORE_PAUSE",
            summary=(
                f"Item '{item.cut_item_id}' ist zuständig, hat aber kein VisualSegment "
                f"vor der Pause {gap_start_sec:.2f}s–{gap_end_sec:.2f}s."
            ),
        )

    _hold_item, segment = found
    needed = max(0.0, gap_end_sec - segment.timeline_out_sec)
    usable: float | None
    failure_reason: str
    if segment.asset_type == "image":
        usable = None
        failure_reason = "HOLD_NOT_APPLIED"
        summary = (
            f"Closing/Hold-Kandidat '{segment.asset_id}' (Bild) könnte die Pause halten, "
            f"wurde aber nicht bis {gap_end_sec:.2f}s verlängert."
        )
    else:
        path = Path(segment.asset_path) if segment.asset_path else None
        real_duration = probe_duration_seconds(path) if path is not None and path.is_file() else None
        if real_duration is None:
            usable = None
            failure_reason = "VIDEO_DURATION_UNKNOWN"
            summary = (
                f"Asset '{segment.asset_id}' (Video): Dauer unbekannt — Hold bis "
                f"{gap_end_sec:.2f}s konnte nicht verifiziert werden."
            )
        else:
            usable = max(0.0, real_duration - segment.source_out_sec)
            if usable + _TOLERANCE < needed:
                failure_reason = "VIDEO_TOO_SHORT"
                summary = (
                    f"Asset '{segment.asset_id}' (Video): nur noch {usable:.2f}s Reserve, "
                    f"Pause braucht {needed:.2f}s Extra "
                    f"(Segment endet {segment.timeline_out_sec:.2f}s, Lücke bis {gap_end_sec:.2f}s). "
                    "Bitte längeres Video oder Bild manuell zuweisen."
                )
            else:
                failure_reason = "HOLD_NOT_APPLIED"
                summary = (
                    f"Asset '{segment.asset_id}' hätte {usable:.2f}s Reserve für {needed:.2f}s Pause, "
                    "Hold wurde aber nicht angewendet — Asset-Auswahl/Coverage erneut ausführen "
                    "oder manuell ersetzen."
                )

    return SectionPauseHoldDiagnosis(
        responsible_cut_item_id=item.cut_item_id,
        responsible_is_closing_shot=bool(item.is_closing_shot),
        hold_candidate_asset_id=segment.asset_id,
        hold_candidate_asset_type=segment.asset_type,
        segment_timeline_out_sec=segment.timeline_out_sec,
        gap_start_sec=gap_start_sec,
        gap_end_sec=gap_end_sec,
        needed_extend_sec=needed,
        usable_extend_sec=usable,
        failure_reason=failure_reason,
        summary=summary,
    )


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

        target_out = next_audio.timeline_start_sec
        additional = target_out - segment.timeline_out_sec
        new_source_out_sec = segment.source_out_sec + additional

        if segment.asset_type != "image":
            # Teil-Hold: so weit strecken wie das Video hergibt, statt bei
            # knapper Reserve gar nichts zu verlängern (Restlücke kann dann
            # unter section_pause_hold_tolerance_sec fallen).
            path = Path(segment.asset_path) if segment.asset_path else None
            real_duration = (
                probe_duration_seconds(path) if path is not None and path.is_file() else None
            )
            if real_duration is None:
                if not _video_can_extend_to(segment.asset_path, new_source_out_sec):
                    continue
            elif segment.source_out_sec >= real_duration - _EPSILON:
                continue  # keine Reserve mehr
            elif new_source_out_sec > real_duration + _TOLERANCE:
                usable_additional = max(0.0, real_duration - segment.source_out_sec)
                if usable_additional <= _EPSILON:
                    continue
                additional = usable_additional
                target_out = segment.timeline_out_sec + additional
                new_source_out_sec = segment.source_out_sec + additional

        updated_segment = segment.model_copy(
            update={
                "timeline_out_sec": target_out,
                "duration_sec": segment.duration_sec + additional,
                "source_out_sec": new_source_out_sec,
                "reason": _combine_reason(segment.reason, REASON_SECTION_PAUSE_HOLD),
            }
        )
        updated_cut_plan = _replace_segment(updated_cut_plan, item.cut_item_id, segment.segment_id, updated_segment)

    return updated_cut_plan


def all_segments_sorted(cut_plan: CutPlanDocument) -> list[tuple[VisualSegment, CutPlanItem]]:
    """Alle VisualSegments über den gesamten Cut Plan, sortiert nach
    timeline_in_sec — öffentlich (Validation Repair, cut_plan_validation_
    repair_apply.py, braucht dieselbe Nachbar-Ermittlung wie close_small_
    visual_gaps/resolve_timeline_overlaps)."""
    return sorted(
        ((segment, item) for item in cut_plan.items for segment in item.planned_visual_segments),
        key=lambda pair: pair[0].timeline_in_sec,
    )


def apply_segment_replacements(
    cut_plan: CutPlanDocument, replacements: dict[tuple[str, str], VisualSegment]
) -> CutPlanDocument:
    if not replacements:
        return cut_plan
    updated_items: list[CutPlanItem] = []
    for item in cut_plan.items:
        if not item.planned_visual_segments:
            updated_items.append(item)
            continue
        updated_segments = [
            replacements.get((item.cut_item_id, segment.segment_id), segment)
            for segment in item.planned_visual_segments
        ]
        updated_items.append(item.model_copy(update={"planned_visual_segments": updated_segments}))
    return cut_plan.model_copy(update={"items": updated_items})


def close_small_visual_gaps(
    cut_plan: CutPlanDocument, max_auto_filled_gap_sec: float = _MAX_AUTO_FILLED_GAP_SEC
) -> CutPlanDocument:
    """Phase C (Nutzervorgabe): schließt KLEINE Lücken (<= max_auto_filled_
    gap_sec) zwischen zwei zeitlich aufeinanderfolgenden VisualSegments —
    typischerweise natürliche Sprechpausen zwischen zwei Sätzen INNERHALB
    durchgehend aktiven Voice-overs, bei denen die Alignment-Zeiten des
    vorherigen Satzes knapp vor dem Start des nächsten enden. Verlängert
    IMMER das VORHERGEHENDE Segment (letztes Bild/Video hält kurz), NIE das
    nächste — vermeidet Kaskaden-Effekte, analog zu
    extend_section_end_visuals_over_pauses. Größere Lücken (fehlendes
    Supplement-Asset, blockiertes Item) werden bewusst NICHT angefasst und
    bleiben für validate_no_black_gap_during_voiceover sichtbar.

    Nutzervorgabe (Juli 2026): max_auto_filled_gap_sec ist standardmäßig
    weiterhin 1.0s (_MAX_AUTO_FILLED_GAP_SEC), aber apply_visual_coverage_
    extensions übergibt hier settings.black_gap_auto_hold_max_sec — pro
    Projekt einstellbar, damit sich mehr BLACK_GAP_DURING_VOICEOVER-Fälle
    bereits hier lösen, statt einen Supplement Request auszulösen."""
    all_segments = all_segments_sorted(cut_plan)
    replacements: dict[tuple[str, str], VisualSegment] = {}

    for index in range(len(all_segments) - 1):
        current_segment, current_item = all_segments[index]
        next_segment, _next_item = all_segments[index + 1]
        current_key = (current_item.cut_item_id, current_segment.segment_id)
        current_effective = replacements.get(current_key, current_segment)

        gap = next_segment.timeline_in_sec - current_effective.timeline_out_sec
        if gap <= _EPSILON or gap > max_auto_filled_gap_sec:
            continue

        new_duration = current_effective.duration_sec + gap
        if current_effective.asset_type == "image":
            new_source_out_sec = current_effective.source_in_sec + new_duration
        else:
            new_source_out_sec = current_effective.source_out_sec + gap
            if not _video_can_extend_to(current_effective.asset_path, new_source_out_sec):
                continue  # Video zu kurz -> Lücke bleibt sichtbar, kein stilles Schwarzbild

        replacements[current_key] = current_effective.model_copy(
            update={
                "timeline_out_sec": next_segment.timeline_in_sec,
                "duration_sec": new_duration,
                "source_out_sec": new_source_out_sec,
                "reason": _combine_reason(current_effective.reason, REASON_SMALL_GAP_HOLD),
            }
        )

    return apply_segment_replacements(cut_plan, replacements)


def resolve_timeline_overlaps(cut_plan: CutPlanDocument) -> CutPlanDocument:
    """Phase C (Nutzervorgabe): schneidet ein überlappendes VisualSegment-
    Paar so zurecht, dass sie sich nicht mehr überlappen. Verkürzt IMMER das
    FRÜHERE Segment (timeline_out_sec wird auf den Start des nächsten
    zurückgesetzt) — nie das spätere, das würde dessen eigene Startzeit
    verschieben und einen Kaskaden-Effekt auf alle nachfolgenden Segmente
    auslösen. Betrifft NUR die VISUELLE Platzierung auf V1, NICHT
    CutPlanItem.timeline_start_sec/timeline_end_sec (die Audio-Zeit bleibt
    exakt so, wie sie die Sprachausrichtung vorgibt) — ein paar Frames
    Versatz zwischen Schnitt und Wortgrenze sind unauffällig, ein doppelt
    belegter V1-Zeitraum ist es nicht.

    Überlappungen dieser Größenordnung entstehen typischerweise durch
    kleine Ungenauigkeiten der Sprachausrichtung (Whisper) an
    Satzgrenzen — keine Cut-Plan-Logikfehler."""
    all_segments = all_segments_sorted(cut_plan)
    replacements: dict[tuple[str, str], VisualSegment] = {}

    for index in range(len(all_segments) - 1):
        current_segment, current_item = all_segments[index]
        next_segment, _next_item = all_segments[index + 1]
        current_key = (current_item.cut_item_id, current_segment.segment_id)
        current_effective = replacements.get(current_key, current_segment)

        if next_segment.timeline_in_sec >= current_effective.timeline_out_sec - _EPSILON:
            continue  # kein Overlap

        new_timeline_out_sec = next_segment.timeline_in_sec
        shrink_amount = current_effective.timeline_out_sec - new_timeline_out_sec
        new_duration = current_effective.duration_sec - shrink_amount
        if new_duration <= _EPSILON:
            continue  # Kürzen würde das Segment auf (nahezu) 0 reduzieren -> nicht antasten

        if current_effective.asset_type == "image":
            new_source_out_sec = current_effective.source_in_sec + new_duration
        else:
            new_source_out_sec = current_effective.source_out_sec - shrink_amount

        replacements[current_key] = current_effective.model_copy(
            update={
                "timeline_out_sec": new_timeline_out_sec,
                "duration_sec": new_duration,
                "source_out_sec": new_source_out_sec,
            }
        )

    return apply_segment_replacements(cut_plan, replacements)


def apply_visual_coverage_extensions(cut_plan: CutPlanDocument, settings: CutPlanSettings) -> CutPlanDocument:
    """Orchestriert alle Coverage-Erweiterungen/-Normalisierungen. Läuft
    nach der Asset-Auswahl, bevor validiert wird (siehe
    apply_asset_selection_to_cut_plan). Reihenfolge: zuerst die beiden
    festen Coverage-Fälle (initialer Vorlauf, Sektions-Pausen), danach
    Phase C — kleine Lücken schließen, zuletzt verbleibende Überlappungen
    normalisieren (das Schließen einer kleinen Lücke kann denselben
    Zeitpunkt berühren, an dem sonst eine Überlappung entstehen könnte;
    resolve_timeline_overlaps läuft deshalb bewusst zuletzt)."""
    updated = extend_first_visual_to_timeline_zero(cut_plan)
    updated = extend_section_end_visuals_over_pauses(updated, settings)
    updated = close_small_visual_gaps(updated, settings.black_gap_auto_hold_max_sec)
    updated = resolve_timeline_overlaps(updated)
    return updated
