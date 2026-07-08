"""Phase 10.1: Reine Mapping-Funktionen für das Production-EditPlan-Staging.

Übersetzt Elemente eines bestätigten EditPlan-Bridge-Snapshots
(`EditPlanDocument` mit globalen Zeiten + `voiceover_audio`-TimelineItems,
`BridgeAudioPlanDocument`, `EditPlanBridgeTraceDocument`) in
produktionskompatible Bausteine (`TimelineItem` mit LOKALEN Zeiten,
`VoiceoverPlan`, `EditPlanDocument`-Skelette).

Alle Funktionen in dieser Datei sind REIN: kein Datei-I/O, kein
Netzwerkzugriff, keine LLM-Aufrufe, keine neue Asset-Auswahl, keine neue
Transform-Berechnung. Es werden ausschließlich die reinen Produktions-
Datenmodelle (`EditPlanDocument`, `TimelineItem`, `VoiceoverPlan`) als
Datenstrukturen importiert — KEINE der höherstufigen Builder-/Save-/Export-
Funktionen der bestehenden Produktions-EditPlan-Pipeline werden aufgerufen
oder verändert."""

from __future__ import annotations

from dataclasses import dataclass

from otio_app.analysis_models import EditPlanDocument, TimelineItem, VoiceoverPlan
from otio_app.defaults import (
    AUDIO_SCOPE_FOLDER,
    AUDIO_SCOPE_INTRO,
    EDIT_PLAN_BRIDGE_TIMELINE_ITEM_TYPE_VOICEOVER_AUDIO,
    PRODUCTION_EDIT_PLAN_CANDIDATE_STATUS_STAGING_DRAFT,
)
from otio_app.models import Project
from otio_app.project_layout import safe_folder_slug
from otio_app.services.voiceover_generation.cut_plan_edit_plan_models import (
    BridgeAudioPlanDocument,
    BridgeAudioPlanItem,
    EditPlanBridgeTraceDocument,
    EditPlanBridgeTraceEntry,
)

__all__ = [
    "SectionIdentity",
    "safe_staging_section_id_for_intro",
    "production_section_id_for_intro",
    "safe_staging_section_id_for_folder",
    "production_section_id_for_folder",
    "build_section_identity_from_bridge_trace_entry",
    "group_bridge_visual_items_by_section",
    "group_bridge_audio_plan_by_section",
    "compute_section_start_offset",
    "localize_timeline_item",
    "localize_bridge_audio_item",
    "map_bridge_audio_to_voiceover_plan",
    "map_bridge_visual_item_to_production_timeline_item",
    "build_production_edit_plan_document_skeleton",
]

_INTRO_STAGING_SECTION_ID = "000_intro"
_INTRO_PRODUCTION_SECTION_ID = "section_intro"
_INTRO_FOLDER_NAME = "Intro"


@dataclass(frozen=True)
class SectionIdentity:
    """Eindeutige Identität EINER Sektion (Intro oder ein Folder) für das
    Staging — trennt die Staging-interne Sortierhilfe (staging_section_id,
    mit Nummer) von der produktionskonformen Kennung (production_section_id,
    ohne Nummer, `section_{slug}`-Konvention)."""

    staging_section_id: str
    production_section_id: str
    folder_name: str
    is_intro: bool
    order_index: int


# --- Section Helpers (§Section Helpers) ---


def safe_staging_section_id_for_intro() -> str:
    return _INTRO_STAGING_SECTION_ID


def production_section_id_for_intro() -> str:
    return _INTRO_PRODUCTION_SECTION_ID


def safe_staging_section_id_for_folder(order_index: int, folder_name: str) -> str:
    return f"{order_index:03d}_{safe_folder_slug(folder_name)}"


def production_section_id_for_folder(folder_name: str) -> str:
    """Übernimmt die bestehende Produktionskonvention
    (`generic_outro_selector.section_id_for_folder`) 1:1, damit ein später
    promotetes Paket möglichst reibungslos zur bestehenden Produktionslogik
    passt — OHNE die Produktionsfunktion selbst zu importieren/aufzurufen."""
    return f"section_{safe_folder_slug(folder_name)}"


def build_section_identity_from_bridge_trace_entry(
    entry: EditPlanBridgeTraceEntry, *, order_index: int = 0
) -> SectionIdentity:
    """Baut die Sektions-Identität aus EINEM Bridge-Trace-Eintrag.

    `order_index` wird bewusst als optionaler Parameter mit Default 0
    entgegengenommen: ein einzelner Trace-Eintrag trägt keine eigene
    Ordnungsnummer (die kommt aus der Gesamt-Reihenfolge aller Sektionen,
    siehe group_bridge_visual_items_by_section/group_bridge_audio_plan_by_
    section) — der Aufrufer löst die korrekte Nummer auf und reicht sie hier
    durch, damit diese Funktion für sich genommen rein bleibt."""
    if entry.source_scope == AUDIO_SCOPE_INTRO:
        return SectionIdentity(
            staging_section_id=safe_staging_section_id_for_intro(),
            production_section_id=production_section_id_for_intro(),
            folder_name=_INTRO_FOLDER_NAME,
            is_intro=True,
            order_index=0,
        )
    return SectionIdentity(
        staging_section_id=safe_staging_section_id_for_folder(order_index, entry.folder_name),
        production_section_id=production_section_id_for_folder(entry.folder_name),
        folder_name=entry.folder_name,
        is_intro=False,
        order_index=order_index,
    )


# --- Grouping (§Grouping) ---


def group_bridge_visual_items_by_section(
    edit_plan_bridge: EditPlanDocument, bridge_trace: EditPlanBridgeTraceDocument
) -> dict[str, list[TimelineItem]]:
    """Gruppiert NUR Visual-TimelineItems (Track V1, kein `voiceover_audio`)
    nach staging_section_id.

    Wichtig: gruppiert NICHT über `TimelineItem.section_id` (das ist im
    Bridge-Draft bridge-intern der `cut_item_id`, keine verlässliche
    Sektionskennung) — sondern über den Bridge-Trace (`source_scope`,
    `folder_name`), der die zuverlässige Quelle für „welches TimelineItem
    gehört zu welcher Sektion“ ist.

    Die Reihenfolge der Folder-Sektionen wird aus der Reihenfolge abgeleitet,
    in der sie zum ersten Mal im Trace auftauchen — der Trace wird in
    derselben Reihenfolge gebaut wie der Cut Plan selbst durchlaufen wurde
    (Intro zuerst, dann Folder nach ihrem ursprünglichen order_index)."""
    trace_by_timeline_item_id = {
        entry.timeline_item_id: entry for entry in bridge_trace.entries if entry.visual_segment_id
    }

    folder_order: dict[str, int] = {}
    next_index = 1
    for entry in bridge_trace.entries:
        if not entry.visual_segment_id or entry.source_scope != AUDIO_SCOPE_FOLDER:
            continue
        if entry.folder_name not in folder_order:
            folder_order[entry.folder_name] = next_index
            next_index += 1

    grouped: dict[str, list[TimelineItem]] = {}
    for item in edit_plan_bridge.timeline_items:
        if item.track != "V1" or item.type == EDIT_PLAN_BRIDGE_TIMELINE_ITEM_TYPE_VOICEOVER_AUDIO:
            continue
        entry = trace_by_timeline_item_id.get(item.timeline_item_id)
        if entry is None:
            continue
        order_index = folder_order.get(entry.folder_name, 0)
        identity = build_section_identity_from_bridge_trace_entry(entry, order_index=order_index)
        grouped.setdefault(identity.staging_section_id, []).append(item)

    return grouped


def group_bridge_audio_plan_by_section(
    bridge_audio_plan: BridgeAudioPlanDocument,
) -> dict[str, BridgeAudioPlanItem]:
    """Gruppiert BridgeAudioPlanItems nach staging_section_id — höchstens
    EIN Audio-Item je Sektion (Produktionskonvention: ein VoiceoverPlan pro
    EditPlanDocument). Die Reihenfolge kommt aus
    `source_cut_plan_audio_index` (Position im ursprünglichen
    CutPlanDocument.audio_items), NICHT aus der Iterationsreihenfolge in
    dieser Datei."""
    sorted_items = sorted(bridge_audio_plan.items, key=lambda entry: entry.source_cut_plan_audio_index)

    grouped: dict[str, BridgeAudioPlanItem] = {}
    folder_counter = 0
    for item in sorted_items:
        if item.scope == AUDIO_SCOPE_INTRO:
            staging_section_id = safe_staging_section_id_for_intro()
        else:
            folder_counter += 1
            staging_section_id = safe_staging_section_id_for_folder(folder_counter, item.folder_name)
        grouped[staging_section_id] = item

    return grouped


# --- Local Time Mapping (§Local Time Mapping) ---


def compute_section_start_offset(
    visual_items: list[TimelineItem], audio_item: BridgeAudioPlanItem | None
) -> float:
    """Minimum aus allen Visual-`timeline_in_sec` UND (falls vorhanden) dem
    Audio-`timeline_in_sec` dieser Sektion — das ist der Nullpunkt für die
    Lokalisierung. Liefert 0.0, wenn weder Visual- noch Audio-Daten
    vorhanden sind (defensiv, sollte praktisch nicht vorkommen)."""
    candidates = [item.timeline_in_sec for item in visual_items]
    if audio_item is not None:
        candidates.append(audio_item.timeline_in_sec)
    if not candidates:
        return 0.0
    return min(candidates)


def localize_timeline_item(
    item: TimelineItem, section_start_offset: float, production_section_id: str, folder_name: str
) -> TimelineItem:
    """local_time = global_time - section_start_offset. Alle anderen Felder
    (asset_id, resolved_media_path, selection_reason, passage_text, beat_id,
    supplement_request_id, transform, ...) bleiben über model_copy
    unverändert erhalten — keine neue Asset-Auswahl, keine neue Transform-
    Berechnung."""
    return item.model_copy(
        update={
            "timeline_in_sec": item.timeline_in_sec - section_start_offset,
            "timeline_out_sec": item.timeline_out_sec - section_start_offset,
            "section_id": production_section_id,
            "folder_name": folder_name,
        }
    )


def localize_bridge_audio_item(audio_item: BridgeAudioPlanItem, section_start_offset: float) -> BridgeAudioPlanItem:
    """Analog zu localize_timeline_item, aber für ein BridgeAudioPlanItem —
    Dauer bleibt unverändert (Audio wird nie gekürzt), nur die
    Timeline-Position wird lokalisiert."""
    return audio_item.model_copy(
        update={
            "timeline_in_sec": audio_item.timeline_in_sec - section_start_offset,
            "timeline_out_sec": audio_item.timeline_out_sec - section_start_offset,
        }
    )


# --- Audio Mapping (§Audio Mapping) ---


def map_bridge_audio_to_voiceover_plan(audio_item: BridgeAudioPlanItem, section_start_offset: float) -> VoiceoverPlan:
    """BridgeAudioPlanItem -> VoiceoverPlan. Audio wird NIE gekürzt:
    source_in_sec/source_out_sec/duration_sec werden unverändert aus dem
    Bridge-Audio-Plan übernommen, nur die Timeline-Position wird
    lokalisiert."""
    timeline_start_sec = audio_item.timeline_in_sec - section_start_offset
    return VoiceoverPlan(
        path=audio_item.audio_path,
        timeline_start_sec=timeline_start_sec,
        source_in_sec=audio_item.source_in_sec,
        source_out_sec=audio_item.source_out_sec,
        duration_sec=audio_item.duration_sec,
        timeline_end_sec=timeline_start_sec + audio_item.duration_sec,
        duration_source="bridge_audio_plan",
        trim_policy="disabled",
    )


# --- Visual Mapping (§Visual Mapping) ---


def map_bridge_visual_item_to_production_timeline_item(
    item: TimelineItem,
    trace_entry: EditPlanBridgeTraceEntry | None,
    section_start_offset: float,
    production_section_id: str,
    folder_name: str,
) -> TimelineItem:
    """Übersetzt EIN Visual-Bridge-TimelineItem (Track V1) in ein
    produktionskompatibles, lokalisiertes TimelineItem.

    - `type="voiceover_audio"` oder Track != "V1" wird NIEMALS übernommen
      (wirft ValueError) — Audio läuft ausschließlich über VoiceoverPlan.
    - timeline_in/out_sec werden lokalisiert (§Local Time Mapping).
    - source_in_sec/source_out_sec bleiben UNVERÄNDERT (keine neue
      Asset-Auswahl, kein neues Trimmen).
    - selection_reason/passage_text/beat_id/supplement_request_id sind
      bereits auf dem Bridge-Item gesetzt (Phase 9.1) und bleiben über
      model_copy automatisch erhalten.
    - Falls ein trace_entry übergeben wird, werden dessen warnings additiv
      in item.warnings übernommen (Traceability), und es wird defensiv
      geprüft, dass trace_entry zum selben timeline_item_id gehört."""
    if item.type == EDIT_PLAN_BRIDGE_TIMELINE_ITEM_TYPE_VOICEOVER_AUDIO or item.track != "V1":
        raise ValueError(
            f"{item.timeline_item_id}: 'voiceover_audio'- oder Nicht-V1-Items dürfen nicht als "
            "Produktions-Visual-TimelineItem übernommen werden."
        )
    if trace_entry is not None and trace_entry.timeline_item_id != item.timeline_item_id:
        raise ValueError(
            f"trace_entry ({trace_entry.timeline_item_id}) gehört nicht zu item ({item.timeline_item_id})."
        )

    localized = localize_timeline_item(item, section_start_offset, production_section_id, folder_name)

    if trace_entry is not None and trace_entry.warnings:
        merged_warnings = list(dict.fromkeys(list(localized.warnings) + list(trace_entry.warnings)))
        localized = localized.model_copy(update={"warnings": merged_warnings})

    return localized


# --- Document Mapping (§Document Mapping) ---


def build_production_edit_plan_document_skeleton(
    project: Project,
    section_identity: SectionIdentity,
    visual_items: list[TimelineItem],
    voiceover_plan: VoiceoverPlan | None,
) -> EditPlanDocument:
    """Baut das EditPlanDocument-Skelett EINER Sektion. Phase 10.1: KEINE
    shots-Synthese (shots=[]) — folgt erst in Phase 10.2. `confirmed=False`
    und ein eigener candidate_status-Marker stellen sicher, dass ein
    gestagtes Dokument niemals mit einem echten Produktions-Draft verwechselt
    werden kann."""
    return EditPlanDocument(
        project_id=project.id,
        folder_name=section_identity.folder_name,
        confirmed=False,
        voiceover=voiceover_plan,
        shots=[],
        timeline_items=list(visual_items),
        allow_black_outro=True,
        candidate_status=PRODUCTION_EDIT_PLAN_CANDIDATE_STATUS_STAGING_DRAFT,
        validation_status="",
    )
