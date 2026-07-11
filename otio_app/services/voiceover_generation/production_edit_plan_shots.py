"""Phase 10.2: Shots-Synthese für das Production-EditPlan-Staging.

Baut EditPlanShot-Objekte aus bereits lokalisierten Produktions-TimelineItems
— ein Shot pro Visual-TimelineItem/VisualSegment. Es werden AUSSCHLIESSLICH
Felder gesetzt, die ehrlich aus dem Bridge-TimelineItem, dem zugehörigen
VoiceoverPlan oder dem Bridge-Trace ableitbar sind. Keine Fake-Inhalte, keine
neuen LLM-Beschreibungen, keine neue Asset-Auswahl, keine opening_title-/
outro-Shots.

Reine Funktionen — kein Datei-I/O, kein Netzwerkzugriff."""

from __future__ import annotations

from otio_app.analysis_models import EditPlanShot, TimelineItem, VoiceoverPlan
from otio_app.services.voiceover_generation.cut_plan_edit_plan_models import EditPlanBridgeTraceEntry

__all__ = [
    "build_edit_plan_shot_from_timeline_item",
    "synthesize_edit_plan_shots_for_section",
]


def build_edit_plan_shot_from_timeline_item(
    item: TimelineItem,
    voiceover_plan: VoiceoverPlan | None,
    trace_entry: EditPlanBridgeTraceEntry | None,
) -> EditPlanShot:
    """Baut EINEN EditPlanShot aus einem bereits lokalisierten Visual-
    TimelineItem.

    Feldherkunft (alle ehrlich aus vorhandenen Daten abgeleitet, nichts
    erfunden):
    - voice_file: aus voiceover_plan.path, falls vorhanden — sonst "" (die
      Sektion hat dann schlicht keine Voice-over-Zeitbasis; wird vom
      Aufrufer in fields_defaulted/section warnings dokumentiert).
    - voice_start_sec/voice_end_sec: die Zeitspanne des Shots RELATIV zum
      Start des VoiceoverPlans dieser Sektion — in dieser Pipeline deckt
      V1 die Voice-over-Audiospur lückenlos ab (Phase 8.5 Visual Coverage),
      d. h. diese Ableitung entspricht tatsächlich "wann dieser Ausschnitt
      des Textes gesprochen wird", genau wie es voice_start/end_sec in der
      Produktion bedeuten — keine Fabrikation.
    - duration_sec: timeline_out_sec - timeline_in_sec (bereits lokalisiert).
    - asset_path/asset_id/media_type: 1:1 aus dem TimelineItem.
    - motif/passage_text/beat_id/supplement_request_id: 1:1 aus dem
      TimelineItem, falls dort gesetzt (Phase 9.1 füllt passage_text/beat_id/
      supplement_request_id bereits; motif bleibt i. d. R. leer, da die
      Bridge kein Motiv-Feld befüllt — das ist eine ehrliche Leere, kein
      Default-Fake).
    - section_outro: immer False (Phase 10.2 erzeugt keine Outro-Shots)."""
    if voiceover_plan is not None:
        voice_start_sec = max(0.0, item.timeline_in_sec - voiceover_plan.timeline_start_sec)
        voice_end_sec = max(voice_start_sec, item.timeline_out_sec - voiceover_plan.timeline_start_sec)
        voice_file = voiceover_plan.path
    else:
        # Ohne VoiceoverPlan gibt es keine sinnvolle Voice-Zeitbasis — die
        # lokalisierten Timeline-Zeiten selbst sind der einzige ehrliche
        # Rückfall. voice_file bleibt bewusst leer (kein erfundener Pfad).
        voice_start_sec = item.timeline_in_sec
        voice_end_sec = item.timeline_out_sec
        voice_file = ""

    return EditPlanShot(
        voice_file=voice_file,
        folder=item.folder_name,
        voice_start_sec=voice_start_sec,
        voice_end_sec=voice_end_sec,
        duration_sec=item.timeline_out_sec - item.timeline_in_sec,
        asset_path=item.resolved_media_path or None,
        asset_source=item.media_source_type or "local",
        asset_id=item.asset_id,
        supplement_request_id=item.supplement_request_id,
        motif=item.motif,
        passage_text=item.passage_text,
        beat_id=item.beat_id,
        media_type=item.asset_type,
        section_outro=False,
    )


def synthesize_edit_plan_shots_for_section(
    timeline_items: list[TimelineItem],
    voiceover_plan: VoiceoverPlan | None,
    trace_entries: list[EditPlanBridgeTraceEntry | None],
) -> list[EditPlanShot]:
    """Ein EditPlanShot je Visual-TimelineItem — `trace_entries` muss
    dieselbe Länge/Reihenfolge wie `timeline_items` haben (positionale
    Zuordnung, fehlende Einträge als None). Erzeugt KEINE opening_title-/
    outro-Shots (die Bridge kennt diese Konzepte ohnehin nicht, siehe
    Architekturplan Phase 10 §6.8/§6.9)."""
    shots: list[EditPlanShot] = []
    for index, item in enumerate(timeline_items):
        trace_entry = trace_entries[index] if index < len(trace_entries) else None
        shots.append(build_edit_plan_shot_from_timeline_item(item, voiceover_plan, trace_entry))
    return shots
