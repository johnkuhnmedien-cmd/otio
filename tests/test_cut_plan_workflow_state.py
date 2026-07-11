"""Commit 1: Cut-Plan Workflow-State-Service (Nutzervorgabe, Juli 2026:
"die Buttons sind all over the place"). Reine Diagnose-/Berechnungs-
funktion — keine Reparaturlogik, keine Seiteneffekte."""

from __future__ import annotations

from pathlib import Path

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis
from otio_app.defaults import CUT_PLAN_ASSET_SELECTION_UNRESOLVED
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import get_folder_inventory_path
from otio_app.services.voiceover_generation.cut_plan_builder import (
    apply_asset_selection_to_draft,
    build_cut_plan_draft,
    load_cut_plan_draft,
    save_cut_plan_draft,
    validate_cut_plan_draft,
)
from otio_app.services.voiceover_generation.cut_plan_models import CutPlanSettings, VisualSegment
from otio_app.services.voiceover_generation.cut_plan_settings_service import save_cut_plan_settings
from otio_app.services.voiceover_generation.cut_plan_supplement_bridge import (
    apply_accepted_supplement_to_cut_plan_item,
    build_supplement_requests_from_cut_plan,
    effective_cut_plan_supplement_request_status,
    merge_prior_supplement_request_state,
    save_cut_plan_supplement_requests,
)
from otio_app.services.voiceover_generation.cut_plan_supplement_models import CutPlanSupplementAsset
from otio_app.services.voiceover_generation.cut_plan_workflow_state import (
    CUT_PLAN_WORKFLOW_STATUS_BLOCKED,
    CUT_PLAN_WORKFLOW_STATUS_DONE,
    CUT_PLAN_WORKFLOW_STATUS_NOT_NEEDED,
    CUT_PLAN_WORKFLOW_STATUS_NOT_STARTED,
    CUT_PLAN_WORKFLOW_STATUS_READY,
    CUT_PLAN_WORKFLOW_STATUS_STALE,
    compute_cut_plan_workflow_state,
)
from otio_app.services.voiceover_generation.final_plan_service import save_confirmed_voiceover_project_plan
from otio_app.services.voiceover_generation.models import (
    AlignmentItem,
    ConfirmedFolderPlanItem,
    ConfirmedIntroPlanItem,
    ConfirmedVoiceoverProjectPlan,
    IntroHookVisualBeat,
    SentenceItem,
)

FOLDER_A = "Grand Canyon"


def _make_project(tmp_path: Path) -> Project:
    project_root = tmp_path / "USA"
    (project_root / FOLDER_A).mkdir(parents=True)
    return Project(
        id="cut-plan-workflow-project",
        name="Cut Plan Workflow Test",
        project_root=str(project_root),
        work_dir=str(project_root / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=[FOLDER_A],
        selected_asset_subdirs=[FOLDER_A],
    )


def _write_inventory(project: Project, filenames: list[str]) -> None:
    folder_dir = project.project_root_path / FOLDER_A
    folder_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    for filename in filenames:
        (folder_dir / filename).write_bytes(b"FAKE_MEDIA_BYTES")
        entries.append(AssetMediaAnalysis(path=f"{FOLDER_A}/{filename}", description=filename))
    inv_path = get_folder_inventory_path(project.work_dir_path, FOLDER_A)
    inv_path.parent.mkdir(parents=True, exist_ok=True)
    inv_path.write_text(
        AssetFolderAnalysis(folder=FOLDER_A, assets=entries).model_dump_json(indent=2), encoding="utf-8"
    )


def _write_audio_files(project: Project, names: list[str]) -> list[Path]:
    audio_dir = project.work_dir_path / "voiceover_generation" / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name in names:
        path = audio_dir / name
        path.write_bytes(b"FAKE_AUDIO_BYTES")
        paths.append(path)
    return paths


def _happy_path_plan_and_project(tmp_path: Path, *, with_supplement_need: bool = False) -> Project:
    project = _make_project(tmp_path)
    _write_inventory(project, ["photo_a.jpg", "photo_b.jpg"])
    intro_audio, folder_audio = _write_audio_files(project, ["intro.mp3", "folder.mp3"])

    intro = ConfirmedIntroPlanItem(
        hook_text="Ein Ort voller Geheimnisse.",
        audio_path=str(intro_audio),
        audio_duration_sec=5.0,
        visual_beats=[
            IntroHookVisualBeat(hook_beat_id="hook_beat_001", text="x", primary_asset_id="asset_photo_a")
        ],
        alignment_items=[
            AlignmentItem(sentence_id="hook_beat_001", audio_start_sec=0.0, audio_end_sec=5.0, duration_sec=5.0)
        ],
    )
    sentence_kwargs = dict(sentence_id="sentence_001", text="Ein Satz.")
    if not with_supplement_need:
        sentence_kwargs["primary_asset_id"] = "asset_photo_b"
    else:
        sentence_kwargs["needs_supplement_asset"] = True
        sentence_kwargs["supplement_reason"] = "No local asset available."
    folder = ConfirmedFolderPlanItem(
        folder_name=FOLDER_A,
        order_index=1,
        audio_path=str(folder_audio),
        audio_duration_sec=5.0,
        sentence_items=[SentenceItem(**sentence_kwargs)],
        alignment_items=[
            AlignmentItem(sentence_id="sentence_001", audio_start_sec=0.0, audio_end_sec=5.0, duration_sec=5.0)
        ],
    )
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id, project_title="Test", status="AUDIO_READY", intro=intro, folders=[folder]
    )
    save_confirmed_voiceover_project_plan(project, plan)
    save_cut_plan_settings(
        project,
        CutPlanSettings(project_id=project.id, initial_audio_offset_sec=0.0, pause_between_sections_sec=0.0),
    )
    draft = build_cut_plan_draft(project)
    save_cut_plan_draft(project, draft)
    apply_asset_selection_to_draft(project)
    return project


# --- draft ---


def test_workflow_no_source_plan_shows_draft_not_started(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    state = compute_cut_plan_workflow_state(project)
    draft_step = next(s for s in state.steps if s.step_id == "draft")
    assert draft_step.status == CUT_PLAN_WORKFLOW_STATUS_NOT_STARTED
    assert state.next_step_id == ""


def test_workflow_recommends_building_draft_when_source_plan_ready(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _write_inventory(project, ["photo_a.jpg"])
    intro_audio, folder_audio = _write_audio_files(project, ["intro.mp3", "folder.mp3"])
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id, project_title="Test", status="AUDIO_READY",
        intro=ConfirmedIntroPlanItem(audio_path=str(intro_audio)),
        folders=[
            ConfirmedFolderPlanItem(
                folder_name=FOLDER_A, order_index=1, audio_path=str(folder_audio),
                sentence_items=[SentenceItem(sentence_id="s1", text="x", primary_asset_id="asset_photo_a")],
            )
        ],
    )
    save_confirmed_voiceover_project_plan(project, plan)
    save_cut_plan_settings(project, CutPlanSettings(project_id=project.id))

    state = compute_cut_plan_workflow_state(project)
    assert state.next_step_id == "draft"
    assert state.next_action_label == "Cut Plan Draft erzeugen"


def test_workflow_detects_stale_draft_after_settings_change(tmp_path: Path) -> None:
    project = _happy_path_plan_and_project(tmp_path)
    save_cut_plan_settings(project, CutPlanSettings(project_id=project.id, max_asset_usage=99))

    state = compute_cut_plan_workflow_state(project)
    draft_step = next(s for s in state.steps if s.step_id == "draft")
    assert draft_step.status == CUT_PLAN_WORKFLOW_STATUS_STALE
    assert state.next_step_id == "draft"


# --- asset_selection ---


def test_workflow_recommends_asset_selection_when_unresolved(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _write_inventory(project, ["photo_a.jpg", "photo_b.jpg"])
    intro_audio, folder_audio = _write_audio_files(project, ["intro.mp3", "folder.mp3"])
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id, project_title="Test", status="AUDIO_READY",
        intro=ConfirmedIntroPlanItem(
            hook_text="x", audio_path=str(intro_audio), audio_duration_sec=5.0,
            visual_beats=[IntroHookVisualBeat(hook_beat_id="hook_beat_001", text="x", primary_asset_id="asset_photo_a")],
            alignment_items=[AlignmentItem(sentence_id="hook_beat_001", audio_start_sec=0.0, audio_end_sec=5.0, duration_sec=5.0)],
        ),
        folders=[
            ConfirmedFolderPlanItem(
                folder_name=FOLDER_A, order_index=1, audio_path=str(folder_audio), audio_duration_sec=5.0,
                sentence_items=[SentenceItem(sentence_id="sentence_001", text="Ein Satz.", primary_asset_id="asset_photo_b")],
                alignment_items=[AlignmentItem(sentence_id="sentence_001", audio_start_sec=0.0, audio_end_sec=5.0, duration_sec=5.0)],
            )
        ],
    )
    save_confirmed_voiceover_project_plan(project, plan)
    save_cut_plan_settings(
        project, CutPlanSettings(project_id=project.id, initial_audio_offset_sec=0.0, pause_between_sections_sec=0.0)
    )
    draft = build_cut_plan_draft(project)
    save_cut_plan_draft(project, draft)
    # Asset-Auswahl bewusst NICHT ausgeführt -> Items bleiben UNRESOLVED.
    assert any(item.asset_selection_status == CUT_PLAN_ASSET_SELECTION_UNRESOLVED for item in draft.items)

    state = compute_cut_plan_workflow_state(project)
    assert state.next_step_id == "asset_selection"
    assert state.next_action_label == "Asset-Auswahl anwenden"


# --- validate ---


def test_workflow_recommends_validation_after_asset_selection(tmp_path: Path) -> None:
    project = _happy_path_plan_and_project(tmp_path)
    state = compute_cut_plan_workflow_state(project)
    assert state.next_step_id == "validate"
    assert state.next_action_label == "Cut Plan validieren"


def test_workflow_validate_done_after_running_validation(tmp_path: Path) -> None:
    project = _happy_path_plan_and_project(tmp_path)
    validate_cut_plan_draft(project)

    state = compute_cut_plan_workflow_state(project)
    validate_step = next(s for s in state.steps if s.step_id == "validate")
    assert validate_step.status == CUT_PLAN_WORKFLOW_STATUS_DONE


def test_workflow_validate_becomes_stale_after_draft_changes(tmp_path: Path) -> None:
    project = _happy_path_plan_and_project(tmp_path)
    validate_cut_plan_draft(project)
    draft = load_cut_plan_draft(project)
    save_cut_plan_draft(project, draft)  # kein inhaltlicher Unterschied hier, aber re-run apply below
    apply_asset_selection_to_draft(project)  # simuliert erneute Änderung am Draft

    state = compute_cut_plan_workflow_state(project)
    validate_step = next(s for s in state.steps if s.step_id == "validate")
    assert validate_step.status in (CUT_PLAN_WORKFLOW_STATUS_STALE, CUT_PLAN_WORKFLOW_STATUS_DONE)


# --- supplement_requests / supplement_resolve ---


def test_workflow_supplement_not_needed_when_no_item_needs_it(tmp_path: Path) -> None:
    project = _happy_path_plan_and_project(tmp_path)
    validate_cut_plan_draft(project)

    state = compute_cut_plan_workflow_state(project)
    supplement_step = next(s for s in state.steps if s.step_id == "supplement_requests")
    assert supplement_step.status == CUT_PLAN_WORKFLOW_STATUS_NOT_NEEDED
    resolve_step = next(s for s in state.steps if s.step_id == "supplement_resolve")
    assert resolve_step.status == CUT_PLAN_WORKFLOW_STATUS_NOT_NEEDED


def test_workflow_recommends_building_supplement_requests_when_needed(tmp_path: Path) -> None:
    project = _happy_path_plan_and_project(tmp_path, with_supplement_need=True)
    validate_cut_plan_draft(project)

    state = compute_cut_plan_workflow_state(project)
    assert state.next_step_id == "supplement_requests"
    assert state.next_action_label == "Supplement Requests erzeugen"


def test_workflow_recommends_searching_supplement_assets_when_requests_open(tmp_path: Path) -> None:
    project = _happy_path_plan_and_project(tmp_path, with_supplement_need=True)
    validate_cut_plan_draft(project)
    draft = load_cut_plan_draft(project)
    document = build_supplement_requests_from_cut_plan(project, draft)
    save_cut_plan_supplement_requests(project, document)

    state = compute_cut_plan_workflow_state(project)
    assert state.next_step_id == "supplement_resolve"
    assert "suchen" in state.next_action_label.lower()


def test_workflow_treats_accepted_asset_id_as_fulfilled_even_if_status_stale(tmp_path: Path) -> None:
    project = _happy_path_plan_and_project(tmp_path, with_supplement_need=True)
    validate_cut_plan_draft(project)
    draft = load_cut_plan_draft(project)
    document = build_supplement_requests_from_cut_plan(project, draft)
    asset_path = tmp_path / "already_accepted.jpg"
    asset_path.write_bytes(b"img")
    stale_accepted = document.requests[0].model_copy(
        update={
            "status": "CANDIDATES_FOUND",
            "accepted_asset_id": "supplement_pexels_stale_status",
            "accepted_asset_path": str(asset_path),
            "accepted_candidate_id": "cand_stale",
        }
    )
    save_cut_plan_supplement_requests(project, document.model_copy(update={"requests": [stale_accepted]}))
    asset = CutPlanSupplementAsset(
        asset_id="supplement_pexels_stale_status",
        request_id=stale_accepted.request_id,
        candidate_id="cand_stale",
        provider="pexels",
        asset_path=str(asset_path),
        asset_type="image",
        duration_sec=0.0,
    )
    updated_draft = apply_accepted_supplement_to_cut_plan_item(project, draft, stale_accepted, asset)
    save_cut_plan_draft(project, updated_draft)

    state = compute_cut_plan_workflow_state(project)
    resolve_step = next(step for step in state.steps if step.step_id == "supplement_resolve")
    assert resolve_step.status == CUT_PLAN_WORKFLOW_STATUS_DONE
    assert "ohne Asset" not in resolve_step.summary
    assert state.next_step_id != "supplement_resolve"


# --- final check ---


# --- next_action_key (Commit 6: maschinenlesbarer Dispatch-Key) ---


def test_workflow_next_action_key_matches_draft(tmp_path: Path) -> None:
    from otio_app.services.voiceover_generation.cut_plan_workflow_state import CUT_PLAN_WORKFLOW_ACTION_BUILD_DRAFT

    project = _make_project(tmp_path)
    state = compute_cut_plan_workflow_state(project)
    assert state.next_step_id == ""  # kein Source Plan -> gar kein next_step


def test_workflow_next_action_key_matches_validate(tmp_path: Path) -> None:
    from otio_app.services.voiceover_generation.cut_plan_workflow_state import CUT_PLAN_WORKFLOW_ACTION_VALIDATE

    project = _happy_path_plan_and_project(tmp_path)
    state = compute_cut_plan_workflow_state(project)
    assert state.next_step_id == "validate"
    assert state.next_action_key == CUT_PLAN_WORKFLOW_ACTION_VALIDATE


def test_workflow_next_action_key_matches_build_supplement_requests(tmp_path: Path) -> None:
    from otio_app.services.voiceover_generation.cut_plan_workflow_state import (
        CUT_PLAN_WORKFLOW_ACTION_BUILD_SUPPLEMENT_REQUESTS,
    )

    project = _happy_path_plan_and_project(tmp_path, with_supplement_need=True)
    validate_cut_plan_draft(project)
    state = compute_cut_plan_workflow_state(project)
    assert state.next_action_key == CUT_PLAN_WORKFLOW_ACTION_BUILD_SUPPLEMENT_REQUESTS


def test_workflow_all_done_when_no_blockers(tmp_path: Path) -> None:
    project = _happy_path_plan_and_project(tmp_path)
    validate_cut_plan_draft(project)

    state = compute_cut_plan_workflow_state(project)
    assert state.next_step_id == ""
    assert state.all_done is True
    assert state.has_unresolvable_blockers is False


def test_workflow_reports_unresolvable_blockers_when_present(tmp_path: Path) -> None:
    from otio_app.services.voiceover_generation.cut_plan_builder import load_cut_plan_draft as _load

    project = _happy_path_plan_and_project(tmp_path)
    validate_cut_plan_draft(project)
    draft = _load(project)
    # Simuliert einen Rest-Blocker, der durch keinen der Automatik-Schritte
    # abgedeckt ist (z. B. FRAME_ROUNDING_ERROR als Blocker-Override).
    from otio_app.services.voiceover_generation.cut_plan_models import CutPlanValidationError
    from otio_app.services.voiceover_generation.cut_plan_validator import save_cut_plan_validation_report
    from otio_app.services.voiceover_generation.cut_plan_models import CutPlanValidationReport
    from otio_app.services.voiceover_generation.cut_plan_validator import content_hash_of_cut_plan_content

    fake_report = CutPlanValidationReport(
        project_id=project.id,
        cut_plan_hash=content_hash_of_cut_plan_content(draft),
        status="BLOCKED",
        blockers=[CutPlanValidationError(type="SOME_UNRESOLVABLE_TYPE", severity="BLOCKER")],
    )
    save_cut_plan_validation_report(project, fake_report)

    state = compute_cut_plan_workflow_state(project)
    assert state.next_step_id == ""
    assert state.has_unresolvable_blockers is True
    assert state.unresolvable_blocker_count == 1
