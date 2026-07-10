"""Phase 8.2: reine Zeit-/Mapping-Logik für den Cut-Plan-Entwurf.

Voice-over-Audio ist die Timeline-Zeitquelle. Dieses Modul berechnet
ausschließlich Zeiten und baut `CutPlanItem`-Skelette ohne jede Asset-
Auswahl (`chosen_asset_id` bleibt immer leer). Keine ffprobe-Aufrufe, keine
Supplement-Logik, kein OTIO, keine EditPlanDocument-Importe — reine Python-
Zeit-Mathematik über bereits bestätigte Felder aus
`confirmed_voiceover_project_plan.json`. Plant nichts redaktionell neu,
erfindet keine Asset-IDs, ändert keine Texte."""

from __future__ import annotations

from otio_app.defaults import (
    AUDIO_SCOPE_FOLDER,
    AUDIO_SCOPE_INTRO,
    CUT_PLAN_ASSET_SELECTION_UNRESOLVED,
    CUT_PLAN_DURATION_STRATEGY_SINGLE_SHOT,
    CUT_PLAN_ERROR_MISSING_ALIGNMENT,
    CUT_PLAN_ERROR_MISSING_AUDIO,
    CUT_PLAN_FIX_BY_USER,
    READINESS_SEVERITY_BLOCKER,
)
from otio_app.models import Project
from otio_app.services.voiceover_generation.cut_plan_models import (
    CutPlanAudioItem,
    CutPlanItem,
    CutPlanPlannedSegmentAssetPlan,
    CutPlanSettings,
    CutPlanSourceRef,
    CutPlanValidationError,
)
from otio_app.services.voiceover_generation.models import (
    AlignmentItem,
    ConfirmedFolderPlanItem,
    ConfirmedIntroPlanItem,
    ConfirmedVoiceoverProjectPlan,
    IntroHookVisualBeat,
    SentenceItem,
)

__all__ = [
    "build_cut_plan_audio_items",
    "build_cut_plan_item_skeletons",
    "build_cut_plan_timeline_skeleton",
    "map_relative_alignment_to_absolute_timeline",
    "find_alignment_for_folder_sentence",
    "find_alignment_for_intro_visual_beat",
]


def find_alignment_for_intro_visual_beat(
    intro_plan_item: ConfirmedIntroPlanItem, visual_beat: IntroHookVisualBeat
) -> AlignmentItem | None:
    """`AlignmentItem.sentence_id` dient beim Intro-Alignment als hook_beat_id
    (siehe Phase 6/7)."""
    for alignment_item in intro_plan_item.alignment_items:
        if alignment_item.sentence_id == visual_beat.hook_beat_id:
            return alignment_item
    return None


def find_alignment_for_folder_sentence(
    folder_plan_item: ConfirmedFolderPlanItem, sentence_item: SentenceItem
) -> AlignmentItem | None:
    for alignment_item in folder_plan_item.alignment_items:
        if alignment_item.sentence_id == sentence_item.sentence_id:
            return alignment_item
    return None


def map_relative_alignment_to_absolute_timeline(
    audio_item: CutPlanAudioItem, alignment_item: AlignmentItem
) -> tuple[float, float]:
    """`alignment_item.audio_start_sec/audio_end_sec` sind relativ zur
    jeweiligen Audiodatei (0.0 = Start dieses einen Clips) — NICHT absolute
    Timeline-Zeit. Diese Funktion rechnet in absolute Timeline-Zeit um."""
    timeline_start_sec = audio_item.timeline_start_sec + alignment_item.audio_start_sec
    timeline_end_sec = audio_item.timeline_start_sec + alignment_item.audio_end_sec
    return timeline_start_sec, timeline_end_sec


def build_cut_plan_audio_items(
    project: Project, source_plan: ConfirmedVoiceoverProjectPlan, settings: CutPlanSettings
) -> list[CutPlanAudioItem]:
    """Ein CutPlanAudioItem pro Intro (falls bestätigt) und pro bestätigtem
    Folder, in dramaturgischer Reihenfolge (source_plan.folders ist bereits
    nach order_index sortiert und enthält nur bestätigte Ordner, Phase 7).

    Berechnung (§3 Nutzerentscheidung):
        cursor_sec = initial_audio_offset_sec
        intro_audio.timeline_start_sec = cursor_sec
        intro_audio.timeline_end_sec = cursor_sec + intro.audio_duration_sec
        cursor_sec = intro_audio.timeline_end_sec + pause_between_sections_sec
        (analog für jeden Folder)

    Ein Item wird nur erzeugt, wenn tatsächlich ein audio_path vorhanden ist
    — sonst gäbe es nichts, das auf A1 platziert werden könnte (fehlendes
    Audio wird stattdessen als CutPlanValidationError gemeldet, siehe
    build_cut_plan_timeline_skeleton)."""
    audio_items: list[CutPlanAudioItem] = []
    cursor_sec = settings.initial_audio_offset_sec

    intro = source_plan.intro
    if intro.hook_text.strip() and intro.audio_path:
        intro_audio = CutPlanAudioItem(
            scope=AUDIO_SCOPE_INTRO,
            folder_name="",
            audio_path=intro.audio_path,
            alignment_ref_path=intro.alignment_path,
            source_in_sec=0.0,
            timeline_start_sec=cursor_sec,
            timeline_end_sec=cursor_sec + intro.audio_duration_sec,
            duration_sec=intro.audio_duration_sec,
            track="A1",
        )
        audio_items.append(intro_audio)
        cursor_sec = intro_audio.timeline_end_sec + settings.pause_between_sections_sec

    for folder in sorted(source_plan.folders, key=lambda item: item.order_index):
        if not folder.audio_path:
            continue
        folder_audio = CutPlanAudioItem(
            scope=AUDIO_SCOPE_FOLDER,
            folder_name=folder.folder_name,
            audio_path=folder.audio_path,
            alignment_ref_path=folder.alignment_path,
            source_in_sec=0.0,
            timeline_start_sec=cursor_sec,
            timeline_end_sec=cursor_sec + folder.audio_duration_sec,
            duration_sec=folder.audio_duration_sec,
            track="A1",
        )
        audio_items.append(folder_audio)
        cursor_sec = folder_audio.timeline_end_sec + settings.pause_between_sections_sec

    return audio_items


def _find_audio_item(
    audio_items: list[CutPlanAudioItem], *, scope: str, folder_name: str
) -> CutPlanAudioItem | None:
    for audio_item in audio_items:
        if audio_item.scope != scope:
            continue
        if scope == AUDIO_SCOPE_INTRO:
            return audio_item
        if audio_item.folder_name == folder_name:
            return audio_item
    return None


def _intro_item_skeleton(
    beat: IntroHookVisualBeat, audio_item: CutPlanAudioItem | None, alignment_item: AlignmentItem | None
) -> CutPlanItem:
    if audio_item is not None and alignment_item is not None:
        timeline_start_sec, timeline_end_sec = map_relative_alignment_to_absolute_timeline(
            audio_item, alignment_item
        )
        audio_start_sec = alignment_item.audio_start_sec
        audio_end_sec = alignment_item.audio_end_sec
        duration_sec = alignment_item.duration_sec
        blockers: list[str] = []
    else:
        timeline_start_sec = 0.0
        timeline_end_sec = 0.0
        audio_start_sec = 0.0
        audio_end_sec = 0.0
        duration_sec = 0.0
        blockers = [CUT_PLAN_ERROR_MISSING_ALIGNMENT]

    return CutPlanItem(
        cut_item_id=f"cut_intro_{beat.hook_beat_id}",
        source_refs=[
            CutPlanSourceRef(
                source_scope=AUDIO_SCOPE_INTRO,
                folder_name="",
                source_hook_beat_id=beat.hook_beat_id,
                text=beat.text,
            )
        ],
        source_scope=AUDIO_SCOPE_INTRO,
        folder_name="",
        text=beat.text,
        visual_intent=beat.visual_intent,
        audio_start_sec=audio_start_sec,
        audio_end_sec=audio_end_sec,
        timeline_start_sec=timeline_start_sec,
        timeline_end_sec=timeline_end_sec,
        duration_sec=duration_sec,
        duration_strategy=CUT_PLAN_DURATION_STRATEGY_SINGLE_SHOT,
        planned_visual_segments=[],
        primary_asset_id=beat.primary_asset_id,
        backup_asset_ids=list(beat.backup_asset_ids),
        chosen_asset_id="",
        asset_selection_status=CUT_PLAN_ASSET_SELECTION_UNRESOLVED,
        asset_selection_reason="",
        fallback_reason="",
        needs_supplement_asset=beat.needs_supplement_asset,
        supplement_reason=beat.supplement_reason,
        supplement_request_id="",
        warnings=[],
        blockers=blockers,
    )


def _folder_item_skeleton(
    folder: ConfirmedFolderPlanItem,
    sentence_item: SentenceItem,
    audio_item: CutPlanAudioItem | None,
    alignment_item: AlignmentItem | None,
) -> CutPlanItem:
    if audio_item is not None and alignment_item is not None:
        timeline_start_sec, timeline_end_sec = map_relative_alignment_to_absolute_timeline(
            audio_item, alignment_item
        )
        audio_start_sec = alignment_item.audio_start_sec
        audio_end_sec = alignment_item.audio_end_sec
        duration_sec = alignment_item.duration_sec
        blockers: list[str] = []
    else:
        timeline_start_sec = 0.0
        timeline_end_sec = 0.0
        audio_start_sec = 0.0
        audio_end_sec = 0.0
        duration_sec = 0.0
        blockers = [CUT_PLAN_ERROR_MISSING_ALIGNMENT]

    return CutPlanItem(
        cut_item_id=f"cut_{folder.order_index:03d}_{sentence_item.sentence_id}",
        source_refs=[
            CutPlanSourceRef(
                source_scope=AUDIO_SCOPE_FOLDER,
                folder_name=folder.folder_name,
                source_sentence_id=sentence_item.sentence_id,
                text=sentence_item.text,
            )
        ],
        source_scope=AUDIO_SCOPE_FOLDER,
        folder_name=folder.folder_name,
        text=sentence_item.text,
        visual_intent=sentence_item.visual_intent,
        audio_start_sec=audio_start_sec,
        audio_end_sec=audio_end_sec,
        timeline_start_sec=timeline_start_sec,
        timeline_end_sec=timeline_end_sec,
        duration_sec=duration_sec,
        duration_strategy=CUT_PLAN_DURATION_STRATEGY_SINGLE_SHOT,
        planned_visual_segments=[],
        primary_asset_id=sentence_item.primary_asset_id,
        backup_asset_ids=list(sentence_item.backup_asset_ids),
        second_backup_asset_ids=list(sentence_item.second_backup_asset_ids),
        planned_segments=[
            CutPlanPlannedSegmentAssetPlan(
                segment_order=segment.segment_order,
                primary_asset_id=segment.primary_asset_id,
                backup_asset_ids=list(segment.backup_asset_ids),
            )
            for segment in sentence_item.planned_segments
        ],
        chosen_asset_id="",
        asset_selection_status=CUT_PLAN_ASSET_SELECTION_UNRESOLVED,
        asset_selection_reason="",
        fallback_reason="",
        needs_supplement_asset=sentence_item.needs_supplement_asset,
        supplement_reason=sentence_item.supplement_reason,
        supplement_request_id="",
        supplement_search_hint=sentence_item.visual_asset_plan.supplement_search_hint,
        warnings=[],
        blockers=blockers,
    )


def build_cut_plan_item_skeletons(
    project: Project,
    source_plan: ConfirmedVoiceoverProjectPlan,
    audio_items: list[CutPlanAudioItem],
    settings: CutPlanSettings,
) -> list[CutPlanItem]:
    """Ein CutPlanItem-Skelett pro Intro-visual_beat und pro Folder-
    sentence_item — noch OHNE Asset-Auswahl (chosen_asset_id bleibt "",
    asset_selection_status bleibt UNRESOLVED, planned_visual_segments bleibt
    leer). Redaktionelle Felder (Text, visual_intent, primary/backup
    Asset-IDs, needs_supplement_asset) werden 1:1 aus dem bestätigten
    Voice-over-Projektplan übernommen — nie verändert oder neu erfunden."""
    items: list[CutPlanItem] = []

    intro = source_plan.intro
    if intro.hook_text.strip():
        intro_audio_item = _find_audio_item(audio_items, scope=AUDIO_SCOPE_INTRO, folder_name="")
        for beat in intro.visual_beats:
            alignment_item = (
                find_alignment_for_intro_visual_beat(intro, beat) if intro_audio_item is not None else None
            )
            items.append(_intro_item_skeleton(beat, intro_audio_item, alignment_item))

    for folder in sorted(source_plan.folders, key=lambda item: item.order_index):
        folder_audio_item = _find_audio_item(audio_items, scope=AUDIO_SCOPE_FOLDER, folder_name=folder.folder_name)
        for sentence_item in folder.sentence_items:
            alignment_item = (
                find_alignment_for_folder_sentence(folder, sentence_item)
                if folder_audio_item is not None
                else None
            )
            items.append(_folder_item_skeleton(folder, sentence_item, folder_audio_item, alignment_item))

    return items


def build_cut_plan_timeline_skeleton(
    project: Project, source_plan: ConfirmedVoiceoverProjectPlan, settings: CutPlanSettings
) -> tuple[list[CutPlanAudioItem], list[CutPlanItem], list[CutPlanValidationError], list[CutPlanValidationError]]:
    """Orchestriert Audio-Platzierung + Item-Skelette und meldet nur die
    Fehler, die für den Zeit-Mathematik-Schritt selbst unmittelbar relevant
    sind (fehlendes Audio/Alignment für einen bestätigten Abschnitt) — KEINE
    vollständige Cut-Plan-Validierung (Asset-Existenz, Usage-Regeln,
    Split/Merge-Konsistenz etc. folgen erst in Phase 8.4)."""
    warnings: list[CutPlanValidationError] = []
    blockers: list[CutPlanValidationError] = []

    audio_items = build_cut_plan_audio_items(project, source_plan, settings)

    intro = source_plan.intro
    if intro.hook_text.strip() and not intro.audio_path:
        blockers.append(
            CutPlanValidationError(
                type=CUT_PLAN_ERROR_MISSING_AUDIO,
                severity=READINESS_SEVERITY_BLOCKER,
                scope="audio",
                folder_name="",
                message="Intro-Audio fehlt — es kann kein CutPlanAudioItem für das Intro platziert werden.",
                fix_hint="Unter Audio / ElevenLabs vertonen.",
                must_be_fixed_by=CUT_PLAN_FIX_BY_USER,
            )
        )
    elif intro.hook_text.strip() and intro.audio_path and not intro.alignment_items:
        blockers.append(
            CutPlanValidationError(
                type=CUT_PLAN_ERROR_MISSING_ALIGNMENT,
                severity=READINESS_SEVERITY_BLOCKER,
                scope="alignment",
                folder_name="",
                message="Intro-Alignment fehlt — visual_beats können nicht auf die Timeline gemappt werden.",
                fix_hint="Intro erneut vertonen (Audio / ElevenLabs).",
                must_be_fixed_by=CUT_PLAN_FIX_BY_USER,
            )
        )

    for folder in sorted(source_plan.folders, key=lambda item: item.order_index):
        if not folder.audio_path:
            blockers.append(
                CutPlanValidationError(
                    type=CUT_PLAN_ERROR_MISSING_AUDIO,
                    severity=READINESS_SEVERITY_BLOCKER,
                    scope="audio",
                    folder_name=folder.folder_name,
                    message=f"Audio für Ordner '{folder.folder_name}' fehlt — kein CutPlanAudioItem platziert.",
                    fix_hint="Unter Audio / ElevenLabs vertonen.",
                    must_be_fixed_by=CUT_PLAN_FIX_BY_USER,
                )
            )
        elif not folder.alignment_items:
            blockers.append(
                CutPlanValidationError(
                    type=CUT_PLAN_ERROR_MISSING_ALIGNMENT,
                    severity=READINESS_SEVERITY_BLOCKER,
                    scope="alignment",
                    folder_name=folder.folder_name,
                    message=f"Alignment für Ordner '{folder.folder_name}' fehlt — sentence_items können nicht "
                    "auf die Timeline gemappt werden.",
                    fix_hint="Ordner erneut vertonen (Audio / ElevenLabs).",
                    must_be_fixed_by=CUT_PLAN_FIX_BY_USER,
                )
            )

    items = build_cut_plan_item_skeletons(project, source_plan, audio_items, settings)

    for item in items:
        if item.blockers:
            for blocker_type in item.blockers:
                blockers.append(
                    CutPlanValidationError(
                        type=blocker_type,
                        severity=READINESS_SEVERITY_BLOCKER,
                        scope="sentence" if item.source_scope == AUDIO_SCOPE_FOLDER else "intro",
                        cut_item_id=item.cut_item_id,
                        folder_name=item.folder_name,
                        message=f"Kein Alignment für '{item.cut_item_id}' gefunden — Zeiten sind auf 0.0 gesetzt.",
                        must_be_fixed_by=CUT_PLAN_FIX_BY_USER,
                    )
                )

    return audio_items, items, warnings, blockers
