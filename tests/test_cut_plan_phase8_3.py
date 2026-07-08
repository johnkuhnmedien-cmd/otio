"""Phase 8.3: Asset-Auswahl, Fallback, Dauer-/Split-/Merge-Strategie.

Noch KEINE Supplement-Suche/-Beschaffung, keine vollständige Cut-Plan-
Validierung (Phase 8.4), kein Confirm/Lock, kein EditPlanDocument, kein
OTIO-Export, kein LLM-Konfliktlöser."""

from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import patch

import pytest

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis
from otio_app.defaults import (
    CUT_PLAN_ASSET_SELECTION_BACKUP_USED,
    CUT_PLAN_ASSET_SELECTION_BLOCKED,
    CUT_PLAN_ASSET_SELECTION_PRIMARY_USED,
    CUT_PLAN_ASSET_SELECTION_SUPPLEMENT_REQUIRED,
    CUT_PLAN_DURATION_STRATEGY_MERGED,
    CUT_PLAN_DURATION_STRATEGY_SINGLE_SHOT,
    CUT_PLAN_DURATION_STRATEGY_SPLIT,
    CUT_PLAN_ERROR_AMBIGUOUS_ASSET_ID,
    CUT_PLAN_ERROR_ASSET_FILE_MISSING,
    CUT_PLAN_ERROR_ASSET_TOO_SHORT,
    CUT_PLAN_ERROR_INVALID_ASSET_ID,
    CUT_PLAN_ERROR_SHOT_TOO_SHORT,
    CUT_PLAN_ERROR_SUPPLEMENT_REASON_MISSING,
    CUT_PLAN_ERROR_SUPPLEMENT_REQUIRED,
    CUT_PLAN_STATUS_DRAFT,
    CUT_PLAN_STATUS_NEEDS_REVIEW,
)
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import (
    get_edit_plan_dir,
    get_exports_dir,
    get_folder_inventory_path,
    get_supplement_dir,
)
from otio_app.services.voiceover_generation.cut_plan_asset_selector import (
    CutPlanAssetLookup,
    UsageTracker,
    apply_asset_selection_to_cut_plan,
    build_visual_segments_for_item,
    choose_asset_for_cut_item,
    determine_duration_strategy,
    load_asset_lookup_for_cut_plan,
    resolve_asset_candidate,
)
from otio_app.services.voiceover_generation.cut_plan_builder import (
    apply_asset_selection_to_draft,
    build_cut_plan_draft,
    load_cut_plan_draft,
    save_cut_plan_draft,
)
from otio_app.services.voiceover_generation.cut_plan_models import CutPlanSettings
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

_ASSET_SELECTOR_MODULE = "otio_app.services.voiceover_generation.cut_plan_asset_selector"


def _make_project(tmp_path: Path, folders: list[str] | None = None) -> Project:
    folders = folders or [FOLDER_A]
    project_root = tmp_path / "USA"
    for folder in folders:
        (project_root / folder).mkdir(parents=True)
    return Project(
        id="cut-plan-asset-selection-project",
        name="Cut Plan Asset Selection Test",
        project_root=str(project_root),
        work_dir=str(project_root / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=folders,
        selected_asset_subdirs=folders,
    )


def _write_inventory(
    project: Project, folder_name: str, assets: list[tuple[str, str]]
) -> None:
    """assets: Liste von (filename, description) — Bilddateien erkennt man an
    der Endung .jpg/.png, alles andere gilt als Video."""
    folder_dir = project.project_root_path / folder_name
    folder_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    for filename, description in assets:
        (folder_dir / filename).write_bytes(b"FAKE_MEDIA_BYTES")
        entries.append(AssetMediaAnalysis(path=f"{folder_name}/{filename}", description=description))
    inv_path = get_folder_inventory_path(project.work_dir_path, folder_name)
    inv_path.parent.mkdir(parents=True, exist_ok=True)
    inv_path.write_text(
        AssetFolderAnalysis(folder=folder_name, assets=entries).model_dump_json(indent=2), encoding="utf-8"
    )


def _intro(
    *,
    primary_asset_id: str = "asset_intro_a",
    backup_asset_ids: list[str] | None = None,
    needs_supplement_asset: bool = False,
    supplement_reason: str = "",
    audio_duration_sec: float = 20.0,
) -> ConfirmedIntroPlanItem:
    return ConfirmedIntroPlanItem(
        hook_text="Ein Ort voller Geheimnisse.",
        audio_path="/fake/intro.mp3",
        audio_duration_sec=audio_duration_sec,
        visual_beats=[
            IntroHookVisualBeat(
                hook_beat_id="hook_beat_001",
                text="Ein Ort voller Geheimnisse.",
                primary_asset_id=primary_asset_id,
                backup_asset_ids=backup_asset_ids or [],
                needs_supplement_asset=needs_supplement_asset,
                supplement_reason=supplement_reason,
            )
        ],
        alignment_items=[
            AlignmentItem(sentence_id="hook_beat_001", audio_start_sec=0.0, audio_end_sec=5.0, duration_sec=5.0)
        ],
    )


def _folder_with_sentence(
    folder_name: str = FOLDER_A,
    order_index: int = 1,
    *,
    sentence_id: str = "sentence_001",
    text: str = "Ein Testsatz.",
    primary_asset_id: str = "asset_b",
    backup_asset_ids: list[str] | None = None,
    needs_supplement_asset: bool = False,
    supplement_reason: str = "",
    audio_start_sec: float = 0.0,
    duration_sec: float = 5.0,
) -> ConfirmedFolderPlanItem:
    return ConfirmedFolderPlanItem(
        folder_name=folder_name,
        order_index=order_index,
        audio_path="/fake/folder.mp3",
        audio_duration_sec=audio_start_sec + duration_sec + 5.0,
        sentence_items=[
            SentenceItem(
                sentence_id=sentence_id,
                text=text,
                primary_asset_id=primary_asset_id,
                backup_asset_ids=backup_asset_ids or [],
                needs_supplement_asset=needs_supplement_asset,
                supplement_reason=supplement_reason,
            )
        ],
        alignment_items=[
            AlignmentItem(
                sentence_id=sentence_id,
                audio_start_sec=audio_start_sec,
                audio_end_sec=audio_start_sec + duration_sec,
                duration_sec=duration_sec,
            )
        ],
    )


def _settings(project: Project, **overrides) -> CutPlanSettings:
    return CutPlanSettings(project_id=project.id, **overrides)


def _build_and_save_draft(project: Project, plan: ConfirmedVoiceoverProjectPlan) -> None:
    save_confirmed_voiceover_project_plan(project, plan)
    draft = build_cut_plan_draft(project)
    save_cut_plan_draft(project, draft)


def _patch_project_selector(project: Project, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("otio_app.ui.project_context.list_projects", lambda: [project])
    monkeypatch.setattr(
        "otio_app.ui.project_context.get_project_by_id",
        lambda project_id: project if project_id == project.id else None,
    )
    monkeypatch.setattr("streamlit.session_state", {"active_project_id": project.id}, raising=False)


# --- 1-3: primary/backup Auswahl ---


def test_primary_asset_id_is_chosen_when_usable(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _write_inventory(project, FOLDER_A, [("photo_a.jpg", "Primary"), ("photo_b.jpg", "Backup")])
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id,
        intro=ConfirmedIntroPlanItem(),
        folders=[
            _folder_with_sentence(
                primary_asset_id="asset_photo_a", backup_asset_ids=["asset_photo_b"], duration_sec=5.0
            )
        ],
    )
    _build_and_save_draft(project, plan)
    updated = apply_asset_selection_to_draft(project)

    item = updated.items[0]
    assert item.chosen_asset_id == "asset_photo_a"
    assert item.asset_selection_status == CUT_PLAN_ASSET_SELECTION_PRIMARY_USED
    assert len(item.planned_visual_segments) == 1


def test_backup_asset_id_is_chosen_when_primary_missing(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _write_inventory(project, FOLDER_A, [("photo_b.jpg", "Backup")])
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id,
        intro=ConfirmedIntroPlanItem(),
        folders=[
            _folder_with_sentence(
                primary_asset_id="asset_does_not_exist", backup_asset_ids=["asset_photo_b"], duration_sec=5.0
            )
        ],
    )
    _build_and_save_draft(project, plan)
    updated = apply_asset_selection_to_draft(project)

    item = updated.items[0]
    assert item.chosen_asset_id == "asset_photo_b"
    assert item.asset_selection_status == CUT_PLAN_ASSET_SELECTION_BACKUP_USED


def test_fallback_reason_is_set_when_backup_is_used(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _write_inventory(project, FOLDER_A, [("photo_b.jpg", "Backup")])
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id,
        intro=ConfirmedIntroPlanItem(),
        folders=[
            _folder_with_sentence(
                primary_asset_id="asset_does_not_exist", backup_asset_ids=["asset_photo_b"], duration_sec=5.0
            )
        ],
    )
    _build_and_save_draft(project, plan)
    updated = apply_asset_selection_to_draft(project)

    item = updated.items[0]
    assert item.fallback_reason != ""
    assert "asset_does_not_exist" in item.fallback_reason
    assert item.asset_selection_reason != ""


# --- 4-7: Fehlerarten ---


def test_invalid_primary_asset_id_produces_invalid_asset_id_warning(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _write_inventory(project, FOLDER_A, [])
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id,
        intro=ConfirmedIntroPlanItem(),
        folders=[_folder_with_sentence(primary_asset_id="asset_does_not_exist", duration_sec=5.0)],
    )
    _build_and_save_draft(project, plan)
    updated = apply_asset_selection_to_draft(project)

    item = updated.items[0]
    assert CUT_PLAN_ERROR_INVALID_ASSET_ID in item.warnings
    assert item.asset_selection_status == CUT_PLAN_ASSET_SELECTION_SUPPLEMENT_REQUIRED


def test_missing_asset_file_produces_asset_file_missing(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    # Inventory referenziert eine Datei, die NICHT auf der Platte liegt.
    (project.project_root_path / FOLDER_A).mkdir(parents=True, exist_ok=True)
    inv_path = get_folder_inventory_path(project.work_dir_path, FOLDER_A)
    inv_path.parent.mkdir(parents=True, exist_ok=True)
    inv_path.write_text(
        AssetFolderAnalysis(
            folder=FOLDER_A,
            assets=[AssetMediaAnalysis(path=f"{FOLDER_A}/missing.jpg", description="fehlt", asset_id="asset_missing")],
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id,
        intro=ConfirmedIntroPlanItem(),
        folders=[_folder_with_sentence(primary_asset_id="asset_missing", duration_sec=5.0)],
    )
    _build_and_save_draft(project, plan)
    updated = apply_asset_selection_to_draft(project)

    item = updated.items[0]
    assert CUT_PLAN_ERROR_ASSET_FILE_MISSING in item.warnings


def test_video_too_short_after_head_trim_produces_asset_too_short(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _write_inventory(project, FOLDER_A, [("clip_short.mp4", "kurz")])
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id,
        intro=ConfirmedIntroPlanItem(),
        folders=[_folder_with_sentence(primary_asset_id="asset_clip_short", duration_sec=5.0)],
    )
    _build_and_save_draft(project, plan)

    # Video ist laut ffprobe 1.2s lang, video_head_trim_sec=1.0 -> usable=0.2s < 5s benötigt.
    with patch(f"{_ASSET_SELECTOR_MODULE}.probe_duration_seconds", return_value=1.2):
        updated = apply_asset_selection_to_draft(project)

    item = updated.items[0]
    assert CUT_PLAN_ERROR_ASSET_TOO_SHORT in item.warnings


def test_image_asset_may_be_held_arbitrarily_long(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _write_inventory(project, FOLDER_A, [("photo_long.jpg", "Bild")])
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id,
        intro=ConfirmedIntroPlanItem(),
        folders=[_folder_with_sentence(primary_asset_id="asset_photo_long", duration_sec=30.0)],
    )
    _build_and_save_draft(project, plan)
    updated = apply_asset_selection_to_draft(project)

    item = updated.items[0]
    assert item.asset_selection_status == CUT_PLAN_ASSET_SELECTION_PRIMARY_USED
    assert CUT_PLAN_ERROR_ASSET_TOO_SHORT not in item.warnings


# --- 8-9: Supplement ---


def test_needs_supplement_asset_produces_supplement_required_and_no_provider_call(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _write_inventory(project, FOLDER_A, [])
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id,
        intro=ConfirmedIntroPlanItem(),
        folders=[
            _folder_with_sentence(
                primary_asset_id="", needs_supplement_asset=True, supplement_reason="Kein Motiv gefunden.",
                duration_sec=5.0,
            )
        ],
    )
    _build_and_save_draft(project, plan)

    with patch(f"{_ASSET_SELECTOR_MODULE}.load_folder_inventory") as mock_load_inventory:
        mock_load_inventory.side_effect = lambda project, folder_name: AssetFolderAnalysis(folder=folder_name)
        updated = apply_asset_selection_to_draft(project)

    item = updated.items[0]
    assert item.asset_selection_status == CUT_PLAN_ASSET_SELECTION_SUPPLEMENT_REQUIRED
    assert item.chosen_asset_id == ""
    assert item.planned_visual_segments == []
    assert CUT_PLAN_ERROR_SUPPLEMENT_REQUIRED in item.blockers
    assert not list(get_supplement_dir(project.work_dir_path).glob("**/*"))


def test_missing_supplement_reason_produces_warning(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _write_inventory(project, FOLDER_A, [])
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id,
        intro=ConfirmedIntroPlanItem(),
        folders=[
            _folder_with_sentence(
                primary_asset_id="", needs_supplement_asset=True, supplement_reason="", duration_sec=5.0
            )
        ],
    )
    _build_and_save_draft(project, plan)
    updated = apply_asset_selection_to_draft(project)

    item = updated.items[0]
    assert CUT_PLAN_ERROR_SUPPLEMENT_REASON_MISSING in item.warnings


# --- 10-13: Usage-Regeln ---


def test_max_asset_usage_applied_globally_intro_counts(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _write_inventory(project, FOLDER_A, [("photo_a.jpg", "shared")])
    # Intro nutzt asset_photo_a, Ordner will es 2 weitere Male (max_asset_usage=2 insgesamt).
    intro = _intro(primary_asset_id="asset_photo_a")
    folder = ConfirmedFolderPlanItem(
        folder_name=FOLDER_A,
        order_index=1,
        audio_path="/fake/folder.mp3",
        audio_duration_sec=30.0,
        sentence_items=[
            SentenceItem(sentence_id="sentence_001", text="Satz eins.", primary_asset_id="asset_photo_a"),
            SentenceItem(sentence_id="sentence_002", text="Satz zwei.", primary_asset_id="asset_photo_a"),
        ],
        alignment_items=[
            AlignmentItem(sentence_id="sentence_001", audio_start_sec=0.0, audio_end_sec=5.0, duration_sec=5.0),
            AlignmentItem(sentence_id="sentence_002", audio_start_sec=10.0, audio_end_sec=15.0, duration_sec=5.0),
        ],
    )
    plan = ConfirmedVoiceoverProjectPlan(project_id=project.id, intro=intro, folders=[folder])
    _build_and_save_draft(project, plan)
    updated = apply_asset_selection_to_draft(project)

    intro_item = next(item for item in updated.items if item.source_scope == "intro")
    folder_items = [item for item in updated.items if item.source_scope == "folder"]
    assert intro_item.chosen_asset_id == "asset_photo_a"
    # Erster Folder-Versuch: direkt nach Intro -> Consecutive-Reuse-Verletzung.
    # Zweiter Versuch (weiter entfernt) sollte scheitern, sobald max_asset_usage (2) erreicht ist.
    assert updated.asset_usage_summary.get("asset_photo_a", 0) <= 2
    assert any(item.asset_selection_status == CUT_PLAN_ASSET_SELECTION_BLOCKED for item in folder_items)


def test_min_asset_reuse_distance_shots_is_applied(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _write_inventory(project, FOLDER_A, [("photo_a.jpg", "a"), ("photo_b.jpg", "b")])
    folder = ConfirmedFolderPlanItem(
        folder_name=FOLDER_A,
        order_index=1,
        audio_path="/fake/folder.mp3",
        audio_duration_sec=30.0,
        sentence_items=[
            SentenceItem(sentence_id="sentence_001", text="Satz eins.", primary_asset_id="asset_photo_a"),
            SentenceItem(
                sentence_id="sentence_002", text="Satz zwei.", primary_asset_id="asset_photo_a",
                backup_asset_ids=["asset_photo_b"],
            ),
        ],
        alignment_items=[
            AlignmentItem(sentence_id="sentence_001", audio_start_sec=0.0, audio_end_sec=5.0, duration_sec=5.0),
            AlignmentItem(sentence_id="sentence_002", audio_start_sec=10.0, audio_end_sec=15.0, duration_sec=5.0),
        ],
    )
    plan = ConfirmedVoiceoverProjectPlan(project_id=project.id, intro=ConfirmedIntroPlanItem(), folders=[folder])
    save_confirmed_voiceover_project_plan(project, plan)

    from otio_app.services.voiceover_generation.cut_plan_settings_service import save_cut_plan_settings

    save_cut_plan_settings(project, _settings(project, min_asset_reuse_distance_shots=5, max_asset_usage=10))
    draft = build_cut_plan_draft(project)
    save_cut_plan_draft(project, draft)

    updated = apply_asset_selection_to_draft(project)
    second_item = next(item for item in updated.items if "sentence_002" in item.cut_item_id)
    # asset_photo_a wurde in Segment 1 genutzt -> Distanz zu Segment 2 ist zu kurz -> Backup verwendet.
    assert second_item.chosen_asset_id == "asset_photo_b"
    assert second_item.asset_selection_status == CUT_PLAN_ASSET_SELECTION_BACKUP_USED


def test_direct_reuse_avoided_when_backup_available(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _write_inventory(project, FOLDER_A, [("photo_a.jpg", "a"), ("photo_b.jpg", "b")])
    folder = ConfirmedFolderPlanItem(
        folder_name=FOLDER_A,
        order_index=1,
        audio_path="/fake/folder.mp3",
        audio_duration_sec=30.0,
        sentence_items=[
            SentenceItem(sentence_id="sentence_001", text="Satz eins.", primary_asset_id="asset_photo_a"),
            SentenceItem(
                sentence_id="sentence_002", text="Satz zwei.", primary_asset_id="asset_photo_a",
                backup_asset_ids=["asset_photo_b"],
            ),
        ],
        alignment_items=[
            AlignmentItem(sentence_id="sentence_001", audio_start_sec=0.0, audio_end_sec=5.0, duration_sec=5.0),
            AlignmentItem(sentence_id="sentence_002", audio_start_sec=10.0, audio_end_sec=15.0, duration_sec=5.0),
        ],
    )
    plan = ConfirmedVoiceoverProjectPlan(project_id=project.id, intro=ConfirmedIntroPlanItem(), folders=[folder])
    _build_and_save_draft(project, plan)
    updated = apply_asset_selection_to_draft(project)

    first_item = next(item for item in updated.items if "sentence_001" in item.cut_item_id)
    second_item = next(item for item in updated.items if "sentence_002" in item.cut_item_id)
    assert first_item.chosen_asset_id == "asset_photo_a"
    assert second_item.chosen_asset_id == "asset_photo_b"  # nicht direkt dasselbe Asset wie Segment davor


def test_split_continuation_may_reuse_same_asset_id(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _write_inventory(project, FOLDER_A, [("photo_only.jpg", "einzig")])
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id,
        intro=ConfirmedIntroPlanItem(),
        folders=[_folder_with_sentence(primary_asset_id="asset_photo_only", duration_sec=14.0)],
    )
    _build_and_save_draft(project, plan)
    updated = apply_asset_selection_to_draft(project)

    item = updated.items[0]
    assert item.duration_strategy == CUT_PLAN_DURATION_STRATEGY_SPLIT
    assert len(item.planned_visual_segments) == 2
    assert all(segment.asset_id == "asset_photo_only" for segment in item.planned_visual_segments)
    assert item.planned_visual_segments[1].reason == "split_long_sentence_continuation"
    assert item.asset_selection_status != CUT_PLAN_ASSET_SELECTION_BLOCKED


# --- 14-16: Merge / kurze Sätze ---


def test_short_duration_merges_with_previous_item(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _write_inventory(project, FOLDER_A, [("photo_a.jpg", "a")])
    folder = ConfirmedFolderPlanItem(
        folder_name=FOLDER_A,
        order_index=1,
        audio_path="/fake/folder.mp3",
        audio_duration_sec=30.0,
        sentence_items=[
            SentenceItem(sentence_id="sentence_001", text="Satz eins.", primary_asset_id="asset_photo_a"),
            SentenceItem(sentence_id="sentence_002", text="Kurz.", primary_asset_id="asset_photo_a"),
        ],
        alignment_items=[
            AlignmentItem(sentence_id="sentence_001", audio_start_sec=0.0, audio_end_sec=5.0, duration_sec=5.0),
            AlignmentItem(sentence_id="sentence_002", audio_start_sec=5.0, audio_end_sec=6.5, duration_sec=1.5),
        ],
    )
    plan = ConfirmedVoiceoverProjectPlan(project_id=project.id, intro=ConfirmedIntroPlanItem(), folders=[folder])
    _build_and_save_draft(project, plan)
    updated = apply_asset_selection_to_draft(project)

    first_item = next(item for item in updated.items if "sentence_001" in item.cut_item_id)
    second_item = next(item for item in updated.items if "sentence_002" in item.cut_item_id)
    assert second_item.duration_strategy == CUT_PLAN_DURATION_STRATEGY_MERGED
    assert second_item.chosen_asset_id == first_item.chosen_asset_id
    assert len(second_item.planned_visual_segments) == 1
    assert second_item.planned_visual_segments[0].reason == "merged_short_sentence"
    assert CUT_PLAN_ERROR_SHOT_TOO_SHORT not in second_item.blockers


def test_short_duration_without_merge_produces_shot_too_short_warning(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _write_inventory(project, FOLDER_A, [("photo_a.jpg", "a")])
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id,
        intro=ConfirmedIntroPlanItem(),
        folders=[_folder_with_sentence(primary_asset_id="asset_photo_a", duration_sec=1.5)],
    )
    _build_and_save_draft(project, plan)
    updated = apply_asset_selection_to_draft(project)

    item = updated.items[0]
    assert item.duration_strategy == CUT_PLAN_DURATION_STRATEGY_SINGLE_SHOT
    assert CUT_PLAN_ERROR_SHOT_TOO_SHORT in item.warnings
    assert CUT_PLAN_ERROR_SHOT_TOO_SHORT not in item.blockers


def test_duration_below_one_second_produces_shot_too_short_blocker(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _write_inventory(project, FOLDER_A, [("photo_a.jpg", "a")])
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id,
        intro=ConfirmedIntroPlanItem(),
        folders=[_folder_with_sentence(primary_asset_id="asset_photo_a", duration_sec=0.5)],
    )
    _build_and_save_draft(project, plan)
    updated = apply_asset_selection_to_draft(project)

    item = updated.items[0]
    assert CUT_PLAN_ERROR_SHOT_TOO_SHORT in item.blockers
    assert updated.status == CUT_PLAN_STATUS_NEEDS_REVIEW


# --- 17-20: Fall B/C ---


def test_duration_between_min_and_max_produces_single_shot(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _write_inventory(project, FOLDER_A, [("photo_a.jpg", "a")])
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id,
        intro=ConfirmedIntroPlanItem(),
        folders=[_folder_with_sentence(primary_asset_id="asset_photo_a", duration_sec=5.0)],
    )
    _build_and_save_draft(project, plan)
    updated = apply_asset_selection_to_draft(project)

    item = updated.items[0]
    assert item.duration_strategy == CUT_PLAN_DURATION_STRATEGY_SINGLE_SHOT
    assert len(item.planned_visual_segments) == 1


def test_duration_above_max_produces_split_with_multiple_segments(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _write_inventory(project, FOLDER_A, [("photo_a.jpg", "a")])
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id,
        intro=ConfirmedIntroPlanItem(),
        folders=[_folder_with_sentence(primary_asset_id="asset_photo_a", duration_sec=14.0)],
    )
    _build_and_save_draft(project, plan)
    updated = apply_asset_selection_to_draft(project)

    item = updated.items[0]
    assert item.duration_strategy == CUT_PLAN_DURATION_STRATEGY_SPLIT
    assert len(item.planned_visual_segments) > 1


def test_14_seconds_produces_approximately_two_seven_second_segments(tmp_path: Path) -> None:
    from otio_app.services.voiceover_generation.cut_plan_settings_service import save_cut_plan_settings

    project = _make_project(tmp_path)
    _write_inventory(project, FOLDER_A, [("photo_a.jpg", "a")])
    # initial_audio_offset_sec=0.0: kein Phase-8.5-Coverage-Vorlauf, der die
    # reine Split-Dauer-Berechnung hier verzerren würde.
    save_cut_plan_settings(project, _settings(project, initial_audio_offset_sec=0.0))
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id,
        intro=ConfirmedIntroPlanItem(),
        folders=[_folder_with_sentence(primary_asset_id="asset_photo_a", duration_sec=14.0)],
    )
    _build_and_save_draft(project, plan)
    updated = apply_asset_selection_to_draft(project)

    segments = updated.items[0].planned_visual_segments
    assert len(segments) == 2
    for segment in segments:
        assert segment.duration_sec == pytest.approx(7.0, abs=0.05)


def test_20_seconds_produces_approximately_three_667_second_segments(tmp_path: Path) -> None:
    from otio_app.services.voiceover_generation.cut_plan_settings_service import save_cut_plan_settings

    project = _make_project(tmp_path)
    _write_inventory(project, FOLDER_A, [("photo_a.jpg", "a")])
    save_cut_plan_settings(project, _settings(project, initial_audio_offset_sec=0.0))
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id,
        intro=ConfirmedIntroPlanItem(),
        folders=[_folder_with_sentence(primary_asset_id="asset_photo_a", duration_sec=20.0)],
    )
    _build_and_save_draft(project, plan)
    updated = apply_asset_selection_to_draft(project)

    segments = updated.items[0].planned_visual_segments
    assert len(segments) == 3
    for segment in segments:
        assert segment.duration_sec == pytest.approx(20.0 / 3, abs=0.05)


# --- 21-23: source_in/out ---


def test_visual_segment_source_in_respects_video_head_trim(tmp_path: Path) -> None:
    from otio_app.services.voiceover_generation.cut_plan_settings_service import save_cut_plan_settings

    project = _make_project(tmp_path)
    _write_inventory(project, FOLDER_A, [("clip_a.mp4", "video")])
    save_cut_plan_settings(project, _settings(project, initial_audio_offset_sec=0.0))
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id,
        intro=ConfirmedIntroPlanItem(),
        folders=[_folder_with_sentence(primary_asset_id="asset_clip_a", duration_sec=5.0)],
    )
    _build_and_save_draft(project, plan)

    with patch(f"{_ASSET_SELECTOR_MODULE}.probe_duration_seconds", return_value=30.0):
        updated = apply_asset_selection_to_draft(project)

    segment = updated.items[0].planned_visual_segments[0]
    assert segment.asset_type == "video"
    assert segment.source_in_sec == pytest.approx(1.0)  # Default video_head_trim_sec
    assert segment.source_out_sec == pytest.approx(1.0 + 5.0)


def test_visual_segment_source_in_is_zero_for_images(tmp_path: Path) -> None:
    from otio_app.services.voiceover_generation.cut_plan_settings_service import save_cut_plan_settings

    project = _make_project(tmp_path)
    _write_inventory(project, FOLDER_A, [("photo_a.jpg", "a")])
    save_cut_plan_settings(project, _settings(project, initial_audio_offset_sec=0.0))
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id,
        intro=ConfirmedIntroPlanItem(),
        folders=[_folder_with_sentence(primary_asset_id="asset_photo_a", duration_sec=5.0)],
    )
    _build_and_save_draft(project, plan)
    updated = apply_asset_selection_to_draft(project)

    segment = updated.items[0].planned_visual_segments[0]
    assert segment.asset_type == "image"
    assert segment.source_in_sec == 0.0
    assert segment.source_out_sec == pytest.approx(5.0)


def test_source_out_never_exceeds_video_duration(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _write_inventory(project, FOLDER_A, [("clip_a.mp4", "video"), ("photo_fallback.jpg", "backup")])
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id,
        intro=ConfirmedIntroPlanItem(),
        folders=[
            _folder_with_sentence(
                primary_asset_id="asset_clip_a", backup_asset_ids=["asset_photo_fallback"], duration_sec=5.0
            )
        ],
    )
    _build_and_save_draft(project, plan)

    # Video ist nur 3s lang (nach head trim 2s nutzbar) -> reicht nicht für 5s -> Fallback auf Bild.
    with patch(f"{_ASSET_SELECTOR_MODULE}.probe_duration_seconds", return_value=3.0):
        updated = apply_asset_selection_to_draft(project)

    item = updated.items[0]
    assert item.chosen_asset_id == "asset_photo_fallback"
    assert CUT_PLAN_ERROR_ASSET_TOO_SHORT in item.warnings


# --- 24-25: Intro-Lookup / Mehrdeutigkeit ---


def test_intro_asset_lookup_searches_across_multiple_used_folders(tmp_path: Path) -> None:
    project = _make_project(tmp_path, folders=[FOLDER_A, FOLDER_B])
    _write_inventory(project, FOLDER_A, [("photo_a.jpg", "a")])
    _write_inventory(project, FOLDER_B, [("photo_b.jpg", "b")])
    intro = _intro(primary_asset_id="asset_photo_b")
    intro = intro.model_copy(update={"used_folders": [FOLDER_A, FOLDER_B]})
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id,
        intro=intro,
        folders=[_folder_with_sentence(FOLDER_A, primary_asset_id="asset_photo_a", duration_sec=5.0)],
    )
    _build_and_save_draft(project, plan)
    updated = apply_asset_selection_to_draft(project)

    intro_item = next(item for item in updated.items if item.source_scope == "intro")
    assert intro_item.chosen_asset_id == "asset_photo_b"
    assert intro_item.asset_selection_status == CUT_PLAN_ASSET_SELECTION_PRIMARY_USED


def test_ambiguous_asset_id_produces_warning(tmp_path: Path) -> None:
    project = _make_project(tmp_path, folders=[FOLDER_A, FOLDER_B])
    # Dieselbe Datei-ID (Dateiname-basiert) existiert zufällig in zwei Ordnern.
    _write_inventory(project, FOLDER_A, [("shared_name.jpg", "in A")])
    _write_inventory(project, FOLDER_B, [("shared_name.jpg", "in B")])
    intro = _intro(primary_asset_id="asset_shared_name")
    intro = intro.model_copy(update={"used_folders": [FOLDER_A, FOLDER_B]})
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id,
        intro=intro,
        folders=[_folder_with_sentence(FOLDER_A, primary_asset_id="asset_shared_name", duration_sec=5.0)],
    )
    _build_and_save_draft(project, plan)
    updated = apply_asset_selection_to_draft(project)

    intro_item = next(item for item in updated.items if item.source_scope == "intro")
    assert CUT_PLAN_ERROR_AMBIGUOUS_ASSET_ID in intro_item.warnings


def test_resolve_asset_candidate_prefers_source_folder_name() -> None:
    from otio_app.services.voiceover_generation.cut_plan_asset_selector import CutPlanAssetCandidate

    lookup = CutPlanAssetLookup()
    candidate_a = CutPlanAssetCandidate(
        asset_id="asset_shared", asset_path="/a.jpg", folder_name=FOLDER_A, asset_type="image",
        is_image=True, exists=True, usable_duration_sec=float("inf"),
    )
    candidate_b = CutPlanAssetCandidate(
        asset_id="asset_shared", asset_path="/b.jpg", folder_name=FOLDER_B, asset_type="image",
        is_image=True, exists=True, usable_duration_sec=float("inf"),
    )
    lookup.add(candidate_a)
    lookup.add(candidate_b)

    assert lookup.is_ambiguous("asset_shared") is True
    resolved = resolve_asset_candidate("asset_shared", lookup, preferred_folder_name=FOLDER_B)
    assert resolved is candidate_b


# --- 26-29: Summary / Persistenz / Status ---


def test_asset_usage_summary_is_written(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _write_inventory(project, FOLDER_A, [("photo_a.jpg", "a")])
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id,
        intro=ConfirmedIntroPlanItem(),
        folders=[_folder_with_sentence(primary_asset_id="asset_photo_a", duration_sec=5.0)],
    )
    _build_and_save_draft(project, plan)
    updated = apply_asset_selection_to_draft(project)

    assert updated.asset_usage_summary.get("asset_photo_a") == 1


def test_apply_asset_selection_to_draft_persists_to_disk(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _write_inventory(project, FOLDER_A, [("photo_a.jpg", "a")])
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id,
        intro=ConfirmedIntroPlanItem(),
        folders=[_folder_with_sentence(primary_asset_id="asset_photo_a", duration_sec=5.0)],
    )
    _build_and_save_draft(project, plan)
    apply_asset_selection_to_draft(project)

    reloaded = load_cut_plan_draft(project)
    assert reloaded is not None
    assert reloaded.items[0].chosen_asset_id == "asset_photo_a"


def test_draft_status_stays_draft_without_blockers(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _write_inventory(project, FOLDER_A, [("photo_a.jpg", "a")])
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id,
        intro=ConfirmedIntroPlanItem(),
        folders=[_folder_with_sentence(primary_asset_id="asset_photo_a", duration_sec=5.0)],
    )
    _build_and_save_draft(project, plan)
    updated = apply_asset_selection_to_draft(project)
    assert updated.status == CUT_PLAN_STATUS_DRAFT


def test_draft_status_becomes_needs_review_with_blockers(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _write_inventory(project, FOLDER_A, [])
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id,
        intro=ConfirmedIntroPlanItem(),
        folders=[_folder_with_sentence(primary_asset_id="", needs_supplement_asset=True, duration_sec=5.0)],
    )
    _build_and_save_draft(project, plan)
    updated = apply_asset_selection_to_draft(project)
    assert updated.status == CUT_PLAN_STATUS_NEEDS_REVIEW


# --- 30-31: UI ---


def test_ui_shows_asset_selection_button_when_draft_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _make_project(tmp_path)
    _write_inventory(project, FOLDER_A, [("photo_a.jpg", "a")])
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id,
        intro=ConfirmedIntroPlanItem(),
        folders=[_folder_with_sentence(primary_asset_id="asset_photo_a", duration_sec=5.0)],
    )
    _build_and_save_draft(project, plan)
    _patch_project_selector(project, monkeypatch)

    button_labels: list[str] = []
    monkeypatch.setattr(
        "streamlit.button", lambda label, *a, **k: (button_labels.append(label), False)[1]
    )

    render_cut_plan_page()

    assert any("Asset-Auswahl anwenden" in label for label in button_labels)


def test_ui_shows_chosen_asset_after_selection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _make_project(tmp_path)
    _write_inventory(project, FOLDER_A, [("photo_a.jpg", "a")])
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id,
        intro=ConfirmedIntroPlanItem(),
        folders=[_folder_with_sentence(primary_asset_id="asset_photo_a", duration_sec=5.0)],
    )
    _build_and_save_draft(project, plan)
    apply_asset_selection_to_draft(project)
    _patch_project_selector(project, monkeypatch)

    render_cut_plan_page()  # darf nicht werfen; chosen_asset_id ist jetzt gesetzt


# --- 32-35: Schutz bestehender Pipeline ---


def test_no_files_written_under_supplement_dir(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _write_inventory(project, FOLDER_A, [])
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id,
        intro=ConfirmedIntroPlanItem(),
        folders=[_folder_with_sentence(primary_asset_id="", needs_supplement_asset=True, duration_sec=5.0)],
    )
    _build_and_save_draft(project, plan)
    apply_asset_selection_to_draft(project)

    assert not get_supplement_dir(project.work_dir_path).exists()


def test_no_edit_plan_documents_created(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _write_inventory(project, FOLDER_A, [("photo_a.jpg", "a")])
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id,
        intro=ConfirmedIntroPlanItem(),
        folders=[_folder_with_sentence(primary_asset_id="asset_photo_a", duration_sec=5.0)],
    )
    _build_and_save_draft(project, plan)
    apply_asset_selection_to_draft(project)

    assert not get_edit_plan_dir(project.work_dir_path).exists()


def test_no_otio_export_triggered(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _write_inventory(project, FOLDER_A, [("photo_a.jpg", "a")])
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id,
        intro=ConfirmedIntroPlanItem(),
        folders=[_folder_with_sentence(primary_asset_id="asset_photo_a", duration_sec=5.0)],
    )
    _build_and_save_draft(project, plan)
    apply_asset_selection_to_draft(project)

    assert not get_exports_dir(project.work_dir_path).exists()


def test_original_media_not_modified(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _write_inventory(project, FOLDER_A, [("photo_a.jpg", "a")])
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id,
        intro=ConfirmedIntroPlanItem(),
        folders=[_folder_with_sentence(primary_asset_id="asset_photo_a", duration_sec=5.0)],
    )
    _build_and_save_draft(project, plan)
    original_content = (project.project_root_path / FOLDER_A / "photo_a.jpg").read_bytes()

    apply_asset_selection_to_draft(project)

    assert (project.project_root_path / FOLDER_A / "photo_a.jpg").read_bytes() == original_content


# --- 36-37: Struktureller Schutz / Regression ---

_FORBIDDEN_SYMBOLS = (
    "build_edit_plan",
    "save_edit_plan",
    "edit_plan_builder",
    "otio_exporter",
    "export_otio_timeline",
    "_set_draft",
    "merge_confirmed_edit_plans",
)


def test_cut_plan_modules_never_reference_forbidden_production_symbols() -> None:
    import re

    import otio_app.services.voiceover_generation.cut_plan_asset_selector as asset_selector_module
    import otio_app.services.voiceover_generation.cut_plan_builder as builder_module
    import otio_app.services.voiceover_generation.cut_plan_timeline_service as timeline_module
    import otio_app.ui.voiceover_generation.cut_plan_tab as tab_module

    for module in (asset_selector_module, builder_module, timeline_module, tab_module):
        source = inspect.getsource(module)
        for forbidden in _FORBIDDEN_SYMBOLS:
            # Wort-Grenzen statt Substring-Suche: vermeidet False Positives
            # durch legitime Phase-9.1-Bridge-Namen wie
            # build_edit_plan_draft_from_confirmed_cut_plan.
            assert not re.search(rf"\b{re.escape(forbidden)}\b", source), (
                f"{module.__name__} referenziert verbotenes Symbol '{forbidden}'."
            )


def test_cut_plan_asset_selector_does_not_call_supplement_search() -> None:
    import otio_app.services.voiceover_generation.cut_plan_asset_selector as asset_selector_module

    source = inspect.getsource(asset_selector_module)
    assert "search_supplement_candidates" not in source
    assert "acquire_supplement_candidate" not in source
    assert "supplement_pipeline" not in source


def test_with_voiceover_workflow_unaffected() -> None:
    from otio_app.services import edit_plan_builder, otio_exporter

    assert hasattr(edit_plan_builder, "build_edit_plan")
    assert hasattr(edit_plan_builder, "save_edit_plan")
    assert hasattr(otio_exporter, "build_otio_timeline")


# --- Zusätzliche gezielte Unit-Tests für Kernfunktionen ---


def test_determine_duration_strategy_split_and_single_shot(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    settings = _settings(project)
    from otio_app.services.voiceover_generation.cut_plan_models import CutPlanItem

    short_item = CutPlanItem(cut_item_id="a", duration_sec=5.0)
    long_item = CutPlanItem(cut_item_id="b", duration_sec=14.0)
    assert determine_duration_strategy(short_item, settings) == CUT_PLAN_DURATION_STRATEGY_SINGLE_SHOT
    assert determine_duration_strategy(long_item, settings) == CUT_PLAN_DURATION_STRATEGY_SPLIT


def test_usage_tracker_register_and_distance() -> None:
    tracker = UsageTracker()
    tracker.register("asset_a")  # Segment 0 -> visual_segment_index wird 1
    tracker.register("asset_b")  # Segment 1 -> visual_segment_index wird 2
    tracker.register("asset_a")  # Segment 2 -> visual_segment_index wird 3
    assert tracker.count_by_asset_id["asset_a"] == 2
    # Direkt nach einer Registrierung ist die "Distanz zur letzten Nutzung"
    # aus Sicht der NÄCHSTEN potenziellen Platzierung 1 (= unmittelbar danach).
    assert tracker.distance_since_last_use("asset_a") == 1
    assert tracker.distance_since_last_use("asset_b") == 2
    assert tracker.distance_since_last_use("asset_unknown") is None


def test_usage_tracker_continuation_does_not_increase_count() -> None:
    tracker = UsageTracker()
    tracker.register("asset_a", count_as_usage=True)
    tracker.register("asset_a", count_as_usage=False)
    assert tracker.count_by_asset_id["asset_a"] == 1


def test_load_asset_lookup_does_not_modify_inventory_files(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _write_inventory(project, FOLDER_A, [("photo_a.jpg", "a")])
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id, intro=ConfirmedIntroPlanItem(), folders=[_folder_with_sentence()]
    )
    save_confirmed_voiceover_project_plan(project, plan)
    draft = build_cut_plan_draft(project)

    inv_path = get_folder_inventory_path(project.work_dir_path, FOLDER_A)
    original_content = inv_path.read_text(encoding="utf-8")

    load_asset_lookup_for_cut_plan(project, plan, draft)

    assert inv_path.read_text(encoding="utf-8") == original_content


def test_choose_asset_for_cut_item_skips_selection_when_item_already_blocked(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    settings = _settings(project)
    lookup = CutPlanAssetLookup()
    tracker = UsageTracker()
    from otio_app.services.voiceover_generation.cut_plan_models import CutPlanItem

    blocked_item = CutPlanItem(cut_item_id="a", duration_sec=5.0, blockers=["MISSING_ALIGNMENT"])
    result = choose_asset_for_cut_item(project, blocked_item, lookup, tracker, settings)
    assert result.asset_selection_status == CUT_PLAN_ASSET_SELECTION_BLOCKED
    assert result.chosen_asset_id == ""
    assert result.planned_visual_segments == []


def test_build_visual_segments_for_item_single_candidate(tmp_path: Path) -> None:
    from otio_app.services.voiceover_generation.cut_plan_asset_selector import CutPlanAssetCandidate
    from otio_app.services.voiceover_generation.cut_plan_models import CutPlanItem

    project = _make_project(tmp_path)
    settings = _settings(project)
    item = CutPlanItem(
        cut_item_id="a", duration_sec=5.0, timeline_start_sec=10.0, timeline_end_sec=15.0,
        primary_asset_id="asset_a",
    )
    candidate = CutPlanAssetCandidate(
        asset_id="asset_a", asset_path="/x.jpg", folder_name=FOLDER_A, asset_type="image", is_image=True,
        exists=True, usable_duration_sec=float("inf"),
    )
    segments = build_visual_segments_for_item(project, item, [candidate], settings)
    assert len(segments) == 1
    assert segments[0].timeline_in_sec == 10.0
    assert segments[0].timeline_out_sec == 15.0
    assert segments[0].reason == "primary_asset"


def test_apply_asset_selection_to_cut_plan_raises_without_source_plan(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _write_inventory(project, FOLDER_A, [("photo_a.jpg", "a")])
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id, intro=ConfirmedIntroPlanItem(), folders=[_folder_with_sentence()]
    )
    save_confirmed_voiceover_project_plan(project, plan)
    draft = build_cut_plan_draft(project)

    import os

    confirmed_plan_path = project.work_dir_path / "voiceover_generation" / "confirmed_voiceover_project_plan.json"
    os.remove(confirmed_plan_path)

    with pytest.raises(ValueError):
        apply_asset_selection_to_cut_plan(project, draft)


def test_apply_asset_selection_to_draft_raises_without_existing_draft(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    with pytest.raises(ValueError):
        apply_asset_selection_to_draft(project)
