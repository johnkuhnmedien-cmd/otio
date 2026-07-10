"""Phase 8.2: Timeline-Mathematik + Audio-Platzierung + CutPlanItem-Skelette.

Noch KEINE Asset-Auswahl, Split-/Merge-Heuristik, Supplement Requests,
vollständige Validierung oder Confirm/Lock — siehe cut_plan_timeline_service.py
und cut_plan_builder.py."""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from otio_app.defaults import (
    CUT_PLAN_ASSET_SELECTION_UNRESOLVED,
    CUT_PLAN_ERROR_MISSING_ALIGNMENT,
    CUT_PLAN_ERROR_MISSING_AUDIO,
    CUT_PLAN_STATUS_DRAFT,
    CUT_PLAN_STATUS_NEEDS_REVIEW,
)
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import get_cut_plan_draft_path, get_edit_plan_dir, get_exports_dir
from otio_app.services.voiceover_generation.cut_plan_builder import (
    build_cut_plan_draft,
    load_cut_plan_draft,
    save_cut_plan_draft,
)
from otio_app.services.voiceover_generation.cut_plan_models import CutPlanSettings
from otio_app.services.voiceover_generation.cut_plan_timeline_service import (
    build_cut_plan_audio_items,
    build_cut_plan_item_skeletons,
    build_cut_plan_timeline_skeleton,
    find_alignment_for_folder_sentence,
    find_alignment_for_intro_visual_beat,
    map_relative_alignment_to_absolute_timeline,
)
from otio_app.services.voiceover_generation.final_plan_service import (
    save_confirmed_voiceover_project_plan,
)
from otio_app.services.voiceover_generation.models import (
    AlignmentItem,
    ConfirmedFolderPlanItem,
    ConfirmedIntroPlanItem,
    ConfirmedVoiceoverProjectPlan,
    IntroHookVisualBeat,
    SentenceItem,
)
from otio_app.ui.voiceover_generation.cut_plan_tab import render_cut_plan_page

FOLDER_A = "Grand Canyon"
FOLDER_B = "Antelope Canyon"


def _make_project(tmp_path: Path, folders: list[str] | None = None) -> Project:
    folders = folders or [FOLDER_A]
    project_root = tmp_path / "USA"
    for folder in folders:
        (project_root / folder).mkdir(parents=True)
    return Project(
        id="cut-plan-timeline-project",
        name="Cut Plan Timeline Test",
        project_root=str(project_root),
        work_dir=str(project_root / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=folders,
        selected_asset_subdirs=folders,
    )


def _intro(
    *, audio_path: str = "/fake/intro.mp3", audio_duration_sec: float = 20.0, with_alignment: bool = True
) -> ConfirmedIntroPlanItem:
    return ConfirmedIntroPlanItem(
        hook_text="Ein Ort voller Geheimnisse.",
        audio_path=audio_path,
        audio_duration_sec=audio_duration_sec,
        alignment_path="/fake/intro_align.json" if with_alignment else "",
        visual_beats=[
            IntroHookVisualBeat(
                hook_beat_id="hook_beat_001",
                text="Ein Ort voller Geheimnisse.",
                visual_intent="establishing",
                primary_asset_id="asset_intro_a",
                backup_asset_ids=["asset_intro_b"],
                needs_supplement_asset=False,
            )
        ],
        alignment_items=(
            [AlignmentItem(sentence_id="hook_beat_001", audio_start_sec=0.0, audio_end_sec=5.0, duration_sec=5.0)]
            if with_alignment
            else []
        ),
    )


def _folder(
    folder_name: str = FOLDER_A,
    order_index: int = 1,
    *,
    audio_path: str = "/fake/folder.mp3",
    audio_duration_sec: float = 40.0,
    with_alignment: bool = True,
) -> ConfirmedFolderPlanItem:
    return ConfirmedFolderPlanItem(
        folder_name=folder_name,
        order_index=order_index,
        dramaturgy_role="setup",
        audio_path=audio_path,
        audio_duration_sec=audio_duration_sec,
        alignment_path="/fake/folder_align.json" if with_alignment else "",
        sentence_items=[
            SentenceItem(
                sentence_id="sentence_001",
                text="Ein Testsatz.",
                visual_intent="wide_shot",
                primary_asset_id="asset_b",
                backup_asset_ids=["asset_c"],
                needs_supplement_asset=False,
            )
        ],
        alignment_items=(
            [AlignmentItem(sentence_id="sentence_001", audio_start_sec=2.0, audio_end_sec=7.0, duration_sec=5.0)]
            if with_alignment
            else []
        ),
    )


def _settings(project: Project, **overrides) -> CutPlanSettings:
    return CutPlanSettings(project_id=project.id, **overrides)


# --- map_relative_alignment_to_absolute_timeline ---


def test_map_relative_alignment_to_absolute_timeline() -> None:
    from otio_app.services.voiceover_generation.cut_plan_models import CutPlanAudioItem

    audio_item = CutPlanAudioItem(timeline_start_sec=21.25, timeline_end_sec=61.25)
    alignment_item = AlignmentItem(audio_start_sec=2.0, audio_end_sec=7.0)

    start, end = map_relative_alignment_to_absolute_timeline(audio_item, alignment_item)
    assert start == pytest.approx(23.25)
    assert end == pytest.approx(28.25)


# --- find_alignment_for_* ---


def test_find_alignment_for_intro_visual_beat_found_and_missing() -> None:
    intro = _intro()
    beat = intro.visual_beats[0]
    found = find_alignment_for_intro_visual_beat(intro, beat)
    assert found is not None
    assert found.audio_start_sec == 0.0

    other_beat = IntroHookVisualBeat(hook_beat_id="does_not_exist")
    assert find_alignment_for_intro_visual_beat(intro, other_beat) is None


def test_find_alignment_for_folder_sentence_found_and_missing() -> None:
    folder = _folder()
    sentence = folder.sentence_items[0]
    found = find_alignment_for_folder_sentence(folder, sentence)
    assert found is not None
    assert found.audio_start_sec == 2.0

    other_sentence = SentenceItem(sentence_id="does_not_exist")
    assert find_alignment_for_folder_sentence(folder, other_sentence) is None


# --- build_cut_plan_audio_items: Kernrechnung ---


def test_audio_items_use_initial_offset_only_once(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id, intro=_intro(audio_duration_sec=20.0), folders=[_folder(audio_duration_sec=40.0)]
    )
    settings = _settings(project, initial_audio_offset_sec=1.0, pause_between_sections_sec=0.25)

    audio_items = build_cut_plan_audio_items(project, plan, settings)
    assert len(audio_items) == 2

    intro_audio, folder_audio = audio_items
    assert intro_audio.scope == "intro"
    assert intro_audio.timeline_start_sec == pytest.approx(1.0)
    assert intro_audio.timeline_end_sec == pytest.approx(21.0)

    # Pause zwischen Intro und Folder — initial_audio_offset_sec wird NICHT erneut angewendet.
    assert folder_audio.scope == "folder"
    assert folder_audio.timeline_start_sec == pytest.approx(21.25)
    assert folder_audio.timeline_end_sec == pytest.approx(61.25)


def test_audio_items_apply_pause_between_multiple_folders(tmp_path: Path) -> None:
    project = _make_project(tmp_path, folders=[FOLDER_A, FOLDER_B])
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id,
        intro=ConfirmedIntroPlanItem(),  # kein bestätigtes Intro
        folders=[
            _folder(FOLDER_A, order_index=1, audio_duration_sec=10.0),
            _folder(FOLDER_B, order_index=2, audio_duration_sec=15.0),
        ],
    )
    settings = _settings(project, initial_audio_offset_sec=1.0, pause_between_sections_sec=0.25)

    audio_items = build_cut_plan_audio_items(project, plan, settings)
    assert len(audio_items) == 2
    first, second = audio_items
    assert first.folder_name == FOLDER_A
    assert first.timeline_start_sec == pytest.approx(1.0)
    assert first.timeline_end_sec == pytest.approx(11.0)

    assert second.folder_name == FOLDER_B
    assert second.timeline_start_sec == pytest.approx(11.25)
    assert second.timeline_end_sec == pytest.approx(26.25)


def test_audio_items_respect_folder_order_index_not_list_order(tmp_path: Path) -> None:
    project = _make_project(tmp_path, folders=[FOLDER_A, FOLDER_B])
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id,
        folders=[
            _folder(FOLDER_B, order_index=2, audio_duration_sec=15.0),
            _folder(FOLDER_A, order_index=1, audio_duration_sec=10.0),
        ],
    )
    settings = _settings(project)

    audio_items = build_cut_plan_audio_items(project, plan, settings)
    assert [item.folder_name for item in audio_items] == [FOLDER_A, FOLDER_B]


def test_no_audio_item_created_when_audio_path_missing(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id,
        intro=_intro(audio_path=""),
        folders=[_folder(audio_path="")],
    )
    settings = _settings(project)

    audio_items = build_cut_plan_audio_items(project, plan, settings)
    assert audio_items == []


def test_no_intro_audio_item_when_intro_not_confirmed(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id, intro=ConfirmedIntroPlanItem(), folders=[_folder()]
    )
    settings = _settings(project)

    audio_items = build_cut_plan_audio_items(project, plan, settings)
    assert len(audio_items) == 1
    assert audio_items[0].scope == "folder"


# --- build_cut_plan_item_skeletons ---


def test_item_skeletons_have_correct_absolute_timeline(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id, intro=_intro(audio_duration_sec=20.0), folders=[_folder(audio_duration_sec=40.0)]
    )
    settings = _settings(project, initial_audio_offset_sec=1.0, pause_between_sections_sec=0.25)

    audio_items = build_cut_plan_audio_items(project, plan, settings)
    items = build_cut_plan_item_skeletons(project, plan, audio_items, settings)
    assert len(items) == 2

    intro_item, folder_item = items
    assert intro_item.cut_item_id == "cut_intro_hook_beat_001"
    assert intro_item.timeline_start_sec == pytest.approx(1.0)
    assert intro_item.timeline_end_sec == pytest.approx(6.0)
    assert intro_item.audio_start_sec == pytest.approx(0.0)
    assert intro_item.audio_end_sec == pytest.approx(5.0)

    assert folder_item.cut_item_id == "cut_001_sentence_001"
    assert folder_item.timeline_start_sec == pytest.approx(23.25)
    assert folder_item.timeline_end_sec == pytest.approx(28.25)
    assert folder_item.audio_start_sec == pytest.approx(2.0)
    assert folder_item.audio_end_sec == pytest.approx(7.0)


def test_item_skeletons_copy_editorial_fields_unchanged(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    plan = ConfirmedVoiceoverProjectPlan(project_id=project.id, intro=_intro(), folders=[_folder()])
    settings = _settings(project)
    audio_items = build_cut_plan_audio_items(project, plan, settings)
    items = build_cut_plan_item_skeletons(project, plan, audio_items, settings)

    folder_item = next(item for item in items if item.source_scope == "folder")
    assert folder_item.text == "Ein Testsatz."
    assert folder_item.visual_intent == "wide_shot"
    assert folder_item.primary_asset_id == "asset_b"
    assert folder_item.backup_asset_ids == ["asset_c"]
    assert folder_item.needs_supplement_asset is False
    assert folder_item.folder_name == FOLDER_A


def test_item_skeletons_copy_second_backup_and_planned_segments(tmp_path: Path) -> None:
    """Phase 7 (Cut-Plan-Split-Fix): second_backup_asset_ids und
    planned_segments (Phase 4/7 auf SentenceItem) müssen 1:1 in das
    CutPlanItem übernommen werden."""
    from otio_app.services.voiceover_generation.models import SentenceSegmentAssetPlan

    project = _make_project(tmp_path)
    folder = ConfirmedFolderPlanItem(
        folder_name=FOLDER_A,
        order_index=1,
        audio_path="/fake/folder.mp3",
        audio_duration_sec=40.0,
        alignment_path="/fake/folder_align.json",
        sentence_items=[
            SentenceItem(
                sentence_id="sentence_001",
                text="Ein Testsatz.",
                primary_asset_id="asset_b",
                backup_asset_ids=["asset_c"],
                second_backup_asset_ids=["asset_d"],
                planned_segments=[
                    SentenceSegmentAssetPlan(
                        segment_order=1, primary_asset_id="asset_e", backup_asset_ids=["asset_f"]
                    ),
                ],
            )
        ],
        alignment_items=[
            AlignmentItem(sentence_id="sentence_001", audio_start_sec=0.0, audio_end_sec=5.0, duration_sec=5.0)
        ],
    )
    plan = ConfirmedVoiceoverProjectPlan(project_id=project.id, intro=_intro(), folders=[folder])
    settings = _settings(project)
    audio_items = build_cut_plan_audio_items(project, plan, settings)
    items = build_cut_plan_item_skeletons(project, plan, audio_items, settings)

    folder_item = next(item for item in items if item.source_scope == "folder")
    assert folder_item.second_backup_asset_ids == ["asset_d"]
    assert len(folder_item.planned_segments) == 1
    assert folder_item.planned_segments[0].segment_order == 1
    assert folder_item.planned_segments[0].primary_asset_id == "asset_e"
    assert folder_item.planned_segments[0].backup_asset_ids == ["asset_f"]


def test_item_skeletons_default_second_backup_and_planned_segments_to_empty(tmp_path: Path) -> None:
    """Rückwärtskompatibilität: ein SentenceItem ohne die Phase-4/7-Felder
    (Standardfall für alle vorher erzeugten Drafts) ergibt leere Listen."""
    project = _make_project(tmp_path)
    plan = ConfirmedVoiceoverProjectPlan(project_id=project.id, intro=_intro(), folders=[_folder()])
    settings = _settings(project)
    audio_items = build_cut_plan_audio_items(project, plan, settings)
    items = build_cut_plan_item_skeletons(project, plan, audio_items, settings)

    folder_item = next(item for item in items if item.source_scope == "folder")
    assert folder_item.second_backup_asset_ids == []
    assert folder_item.planned_segments == []


def test_item_skeletons_copy_supplement_search_hint_from_visual_asset_plan(tmp_path: Path) -> None:
    """Phase 9 (Asset-bewusste Cut-Plan-Vorbereitung): der bereits beim
    Skriptschreiben vorbereitete Suchvorschlag (SentenceItem.
    visual_asset_plan.supplement_search_hint) muss ins CutPlanItem
    übernommen werden."""
    from otio_app.services.voiceover_generation.models import VisualAssetPlanHint

    project = _make_project(tmp_path)
    folder = ConfirmedFolderPlanItem(
        folder_name=FOLDER_A,
        order_index=1,
        audio_path="/fake/folder.mp3",
        audio_duration_sec=40.0,
        alignment_path="/fake/folder_align.json",
        sentence_items=[
            SentenceItem(
                sentence_id="sentence_001",
                text="Ein Testsatz.",
                needs_supplement_asset=True,
                supplement_reason="Kein lokales Asset zeigt das Motiv.",
                visual_asset_plan=VisualAssetPlanHint(
                    supplement_search_hint="Havasu Falls waterfall woman mist"
                ),
            )
        ],
        alignment_items=[
            AlignmentItem(sentence_id="sentence_001", audio_start_sec=0.0, audio_end_sec=5.0, duration_sec=5.0)
        ],
    )
    plan = ConfirmedVoiceoverProjectPlan(project_id=project.id, intro=_intro(), folders=[folder])
    settings = _settings(project)
    audio_items = build_cut_plan_audio_items(project, plan, settings)
    items = build_cut_plan_item_skeletons(project, plan, audio_items, settings)

    folder_item = next(item for item in items if item.source_scope == "folder")
    assert folder_item.supplement_search_hint == "Havasu Falls waterfall woman mist"


def test_item_skeletons_default_supplement_search_hint_to_empty_for_intro(tmp_path: Path) -> None:
    """IntroHookVisualBeat hat kein visual_asset_plan (Scope-Entscheidung
    Phase 4/7/9) — Intro-Items bleiben deshalb bewusst bei einem leeren
    supplement_search_hint."""
    project = _make_project(tmp_path)
    plan = ConfirmedVoiceoverProjectPlan(project_id=project.id, intro=_intro(), folders=[_folder()])
    settings = _settings(project)
    audio_items = build_cut_plan_audio_items(project, plan, settings)
    items = build_cut_plan_item_skeletons(project, plan, audio_items, settings)

    intro_item = next(item for item in items if item.source_scope == "intro")
    assert intro_item.supplement_search_hint == ""


def test_item_skeletons_populate_source_refs(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    plan = ConfirmedVoiceoverProjectPlan(project_id=project.id, intro=_intro(), folders=[_folder()])
    settings = _settings(project)
    audio_items = build_cut_plan_audio_items(project, plan, settings)
    items = build_cut_plan_item_skeletons(project, plan, audio_items, settings)

    intro_item = next(item for item in items if item.source_scope == "intro")
    assert len(intro_item.source_refs) == 1
    assert intro_item.source_refs[0].source_hook_beat_id == "hook_beat_001"
    assert intro_item.source_refs[0].source_scope == "intro"

    folder_item = next(item for item in items if item.source_scope == "folder")
    assert len(folder_item.source_refs) == 1
    assert folder_item.source_refs[0].source_sentence_id == "sentence_001"
    assert folder_item.source_refs[0].folder_name == FOLDER_A


def test_item_skeletons_have_unresolved_asset_selection(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    plan = ConfirmedVoiceoverProjectPlan(project_id=project.id, intro=_intro(), folders=[_folder()])
    settings = _settings(project)
    audio_items = build_cut_plan_audio_items(project, plan, settings)
    items = build_cut_plan_item_skeletons(project, plan, audio_items, settings)

    for item in items:
        assert item.chosen_asset_id == ""
        assert item.asset_selection_status == CUT_PLAN_ASSET_SELECTION_UNRESOLVED
        assert item.planned_visual_segments == []
        assert item.asset_selection_reason == ""
        assert item.supplement_request_id == ""


# --- Fehlerbehandlung: fehlendes Audio / Alignment ---


def test_missing_intro_audio_produces_blocker_and_no_audio_item(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id, intro=_intro(audio_path=""), folders=[_folder()]
    )
    settings = _settings(project)

    audio_items, items, warnings, blockers = build_cut_plan_timeline_skeleton(project, plan, settings)
    assert all(item.scope != "intro" for item in audio_items)
    assert any(error.type == CUT_PLAN_ERROR_MISSING_AUDIO and error.scope == "audio" for error in blockers)

    intro_item = next(item for item in items if item.source_scope == "intro")
    assert intro_item.timeline_start_sec == 0.0
    assert intro_item.timeline_end_sec == 0.0
    assert CUT_PLAN_ERROR_MISSING_ALIGNMENT in intro_item.blockers


def test_missing_intro_alignment_produces_blocker(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id, intro=_intro(with_alignment=False), folders=[_folder()]
    )
    settings = _settings(project)

    audio_items, items, warnings, blockers = build_cut_plan_timeline_skeleton(project, plan, settings)
    assert any(
        error.type == CUT_PLAN_ERROR_MISSING_ALIGNMENT and error.scope == "alignment" for error in blockers
    )
    # Audio-Item existiert trotzdem (Audio ist vorhanden, nur Alignment fehlt).
    assert any(item.scope == "intro" for item in audio_items)


def test_missing_folder_audio_produces_blocker(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id, intro=_intro(), folders=[_folder(audio_path="")]
    )
    settings = _settings(project)

    audio_items, items, warnings, blockers = build_cut_plan_timeline_skeleton(project, plan, settings)
    assert any(
        error.type == CUT_PLAN_ERROR_MISSING_AUDIO and error.folder_name == FOLDER_A for error in blockers
    )


def test_missing_folder_alignment_produces_blocker(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id, intro=_intro(), folders=[_folder(with_alignment=False)]
    )
    settings = _settings(project)

    audio_items, items, warnings, blockers = build_cut_plan_timeline_skeleton(project, plan, settings)
    assert any(
        error.type == CUT_PLAN_ERROR_MISSING_ALIGNMENT and error.folder_name == FOLDER_A for error in blockers
    )


def test_partial_alignment_only_affects_specific_sentence(tmp_path: Path) -> None:
    """Ein Folder mit zwei Sätzen, nur einer hat ein Alignment-Item — nur
    dieser fehlende Satz bekommt einen Blocker, der andere bleibt sauber."""
    project = _make_project(tmp_path)
    folder = ConfirmedFolderPlanItem(
        folder_name=FOLDER_A,
        order_index=1,
        audio_path="/fake/folder.mp3",
        audio_duration_sec=40.0,
        alignment_path="/fake/folder_align.json",
        sentence_items=[
            SentenceItem(sentence_id="sentence_001", text="Satz eins.", primary_asset_id="asset_a"),
            SentenceItem(sentence_id="sentence_002", text="Satz zwei.", primary_asset_id="asset_b"),
        ],
        alignment_items=[
            AlignmentItem(sentence_id="sentence_001", audio_start_sec=0.0, audio_end_sec=3.0, duration_sec=3.0)
        ],
    )
    plan = ConfirmedVoiceoverProjectPlan(project_id=project.id, intro=_intro(), folders=[folder])
    settings = _settings(project)

    audio_items, items, warnings, blockers = build_cut_plan_timeline_skeleton(project, plan, settings)
    item_1 = next(item for item in items if "sentence_001" in item.cut_item_id)
    item_2 = next(item for item in items if "sentence_002" in item.cut_item_id)

    assert item_1.blockers == []
    assert item_1.timeline_start_sec > 0.0
    assert item_2.blockers == [CUT_PLAN_ERROR_MISSING_ALIGNMENT]
    assert item_2.timeline_start_sec == 0.0
    assert any(error.cut_item_id == item_2.cut_item_id for error in blockers)


# --- build_cut_plan_draft (Orchestrator) ---


def test_build_cut_plan_draft_raises_without_confirmed_plan(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    with pytest.raises(ValueError):
        build_cut_plan_draft(project)


def test_build_cut_plan_draft_status_draft_when_no_blockers(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id, project_title="Test Titel", intro=_intro(), folders=[_folder()]
    )
    save_confirmed_voiceover_project_plan(project, plan)

    draft = build_cut_plan_draft(project)
    assert draft.status == CUT_PLAN_STATUS_DRAFT
    assert draft.project_title == "Test Titel"
    assert len(draft.audio_items) == 2
    assert len(draft.items) == 2
    assert draft.blockers == []
    assert draft.asset_usage_summary == {}
    assert draft.supplement_requests == []
    assert draft.source_plan_hash != ""


def test_build_cut_plan_draft_status_needs_review_when_blockers(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id, intro=_intro(audio_path=""), folders=[_folder()]
    )
    save_confirmed_voiceover_project_plan(project, plan)

    draft = build_cut_plan_draft(project)
    assert draft.status == CUT_PLAN_STATUS_NEEDS_REVIEW
    assert len(draft.blockers) > 0


def test_build_cut_plan_draft_settings_snapshot_matches_settings(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    plan = ConfirmedVoiceoverProjectPlan(project_id=project.id, intro=_intro(), folders=[_folder()])
    save_confirmed_voiceover_project_plan(project, plan)

    draft = build_cut_plan_draft(project)
    assert draft.settings_snapshot["initial_audio_offset_sec"] == 1.0
    assert draft.settings_snapshot["pause_between_sections_sec"] == 0.25
    assert draft.settings_snapshot["video_head_trim_sec"] == 1.0


def test_save_and_load_cut_plan_draft_roundtrip(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    plan = ConfirmedVoiceoverProjectPlan(project_id=project.id, intro=_intro(), folders=[_folder()])
    save_confirmed_voiceover_project_plan(project, plan)

    draft = build_cut_plan_draft(project)
    save_cut_plan_draft(project, draft)

    loaded = load_cut_plan_draft(project)
    assert loaded is not None
    assert loaded.status == draft.status
    assert len(loaded.items) == len(draft.items)
    assert get_cut_plan_draft_path(project.work_dir_path).is_file()


def test_load_cut_plan_draft_returns_none_when_missing(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    assert load_cut_plan_draft(project) is None


# --- Schutz der bestehenden Pipeline ---


def test_build_cut_plan_draft_writes_nothing_under_edit_plan_or_exports(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    plan = ConfirmedVoiceoverProjectPlan(project_id=project.id, intro=_intro(), folders=[_folder()])
    save_confirmed_voiceover_project_plan(project, plan)

    draft = build_cut_plan_draft(project)
    save_cut_plan_draft(project, draft)

    assert not get_edit_plan_dir(project.work_dir_path).exists()
    assert not get_exports_dir(project.work_dir_path).exists()


def test_build_cut_plan_draft_does_not_touch_original_media(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    plan = ConfirmedVoiceoverProjectPlan(project_id=project.id, intro=_intro(), folders=[_folder()])
    save_confirmed_voiceover_project_plan(project, plan)

    original_media_dir = project.project_root_path / FOLDER_A
    build_cut_plan_draft(project)
    assert list(original_media_dir.iterdir()) == []


_FORBIDDEN_SYMBOLS = (
    "build_edit_plan",
    "save_edit_plan",
    "edit_plan_builder",
    "otio_exporter",
    "export_otio_timeline",
    "_set_draft",
    "merge_confirmed_edit_plans",
)


def test_cut_plan_timeline_and_builder_modules_never_reference_forbidden_symbols() -> None:
    import otio_app.services.voiceover_generation.cut_plan_builder as builder_module
    import otio_app.services.voiceover_generation.cut_plan_timeline_service as timeline_module

    for module in (builder_module, timeline_module):
        source = inspect.getsource(module)
        for forbidden in _FORBIDDEN_SYMBOLS:
            assert forbidden not in source, f"{module.__name__} referenziert verbotenes Symbol '{forbidden}'."


def test_cut_plan_timeline_service_does_not_call_ffprobe_or_supplement() -> None:
    import otio_app.services.voiceover_generation.cut_plan_timeline_service as timeline_module

    assert not hasattr(timeline_module, "probe_duration_seconds")
    assert not hasattr(timeline_module, "SupplementRequest")
    assert "import subprocess" not in inspect.getsource(timeline_module)
    assert "supplement_pipeline" not in inspect.getsource(timeline_module)


# --- UI: Draft-Erzeugung ---


def _patch_project_selector(project: Project, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("otio_app.ui.project_context.list_projects", lambda: [project])
    monkeypatch.setattr(
        "otio_app.ui.project_context.get_project_by_id",
        lambda project_id: project if project_id == project.id else None,
    )
    monkeypatch.setattr("streamlit.session_state", {"active_project_id": project.id}, raising=False)


def test_cut_plan_page_generates_draft_on_button_click(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _make_project(tmp_path)
    plan = ConfirmedVoiceoverProjectPlan(project_id=project.id, intro=_intro(), folders=[_folder()])
    save_confirmed_voiceover_project_plan(project, plan)
    _patch_project_selector(project, monkeypatch)

    monkeypatch.setattr("streamlit.button", lambda *a, **k: True)
    monkeypatch.setattr("streamlit.rerun", lambda: None)

    render_cut_plan_page()

    assert get_cut_plan_draft_path(project.work_dir_path).is_file()
    saved = json.loads(get_cut_plan_draft_path(project.work_dir_path).read_text(encoding="utf-8"))
    assert len(saved["items"]) == 2


def test_cut_plan_page_renders_existing_draft_without_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _make_project(tmp_path)
    plan = ConfirmedVoiceoverProjectPlan(project_id=project.id, intro=_intro(), folders=[_folder()])
    save_confirmed_voiceover_project_plan(project, plan)
    draft = build_cut_plan_draft(project)
    save_cut_plan_draft(project, draft)
    _patch_project_selector(project, monkeypatch)

    render_cut_plan_page()  # darf nicht werfen


def test_cut_plan_page_does_not_generate_draft_without_confirmed_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simuliert einen Klick auf einen (in echtem Streamlit) disabled Button:
    ein disabled Button liefert real nie True zurück — das wird hier über
    das disabled-Kwarg nachgebildet, statt st.button() blind True liefern
    zu lassen."""
    project = _make_project(tmp_path)
    _patch_project_selector(project, monkeypatch)
    monkeypatch.setattr(
        "streamlit.button", lambda *a, **k: not k.get("disabled", False)
    )

    render_cut_plan_page()  # kein bestätigter Plan -> Button ist disabled -> darf nichts erzeugen

    assert not get_cut_plan_draft_path(project.work_dir_path).exists()
