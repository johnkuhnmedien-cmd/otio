"""Phase 10.5: Production EditPlan Promote Readiness / Dry Run (Service-Ebene).

Rein prüfend: kein Schreiben nach _otio/edit_plan/, kein tatsächlicher
Promote, kein Lock, kein OTIO-Export, kein Render, keine
save_edit_plan()/build_edit_plan()-Aufrufe, keine Produktions-Dateien werden
überschrieben, keine Änderung an voice_folder_mapping.json."""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis, TimelineItem
from otio_app.defaults import (
    PRODUCTION_EDIT_PLAN_ERROR_EXISTING_PRODUCTION_PLAN_UNREADABLE,
    PRODUCTION_EDIT_PLAN_ERROR_PRODUCTION_STAGING_MISSING,
    PRODUCTION_EDIT_PLAN_ERROR_PRODUCTION_STAGING_STALE,
    PRODUCTION_EDIT_PLAN_ERROR_STAGED_EDIT_PLAN_EMPTY_SHOTS,
    PRODUCTION_EDIT_PLAN_ERROR_STAGED_EDIT_PLAN_EMPTY_TIMELINE,
    PRODUCTION_EDIT_PLAN_ERROR_STAGED_EDIT_PLAN_HASH_MISMATCH,
    PRODUCTION_EDIT_PLAN_ERROR_STAGED_EDIT_PLAN_MISSING,
    PRODUCTION_EDIT_PLAN_ERROR_VALIDATION_REPORT_MISSING,
    PRODUCTION_EDIT_PLAN_ERROR_VALIDATION_REPORT_NOT_PASS,
    PRODUCTION_EDIT_PLAN_ERROR_VALIDATION_REPORT_STALE,
    PRODUCTION_EDIT_PLAN_ERROR_VOICEOVER_AUDIO_ITEM_LEAKED,
    PRODUCTION_EDIT_PLAN_ERROR_VOICEOVER_PLAN_MISSING,
    PRODUCTION_EDIT_PLAN_PROMOTE_ACTION_BLOCKED,
    PRODUCTION_EDIT_PLAN_PROMOTE_ACTION_WOULD_CREATE,
    PRODUCTION_EDIT_PLAN_PROMOTE_ACTION_WOULD_OVERWRITE,
    PRODUCTION_EDIT_PLAN_PROMOTE_READINESS_STATUS_BLOCKED,
    PRODUCTION_EDIT_PLAN_PROMOTE_READINESS_STATUS_NEEDS_REVIEW,
    PRODUCTION_EDIT_PLAN_PROMOTE_READINESS_STATUS_READY,
)
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import (
    get_edit_plan_dir,
    get_exports_dir,
    get_folder_edit_plan_path,
    get_folder_inventory_path,
    get_production_edit_plan_promote_dry_run_trace_path,
    get_production_edit_plan_promote_readiness_path,
    get_staged_edit_plan_path,
    get_supplement_dir,
    get_voice_folder_mapping_path,
)
from otio_app.services.voiceover_generation.cut_plan_builder import (
    apply_asset_selection_to_draft,
    build_cut_plan_draft,
    save_cut_plan_draft,
    validate_cut_plan_draft,
)
from otio_app.services.voiceover_generation.cut_plan_confirm_service import confirm_cut_plan, load_confirmed_cut_plan
from otio_app.services.voiceover_generation.cut_plan_edit_plan_bridge import (
    build_bridge_audio_plan_from_confirmed_cut_plan,
    build_edit_plan_draft_from_confirmed_cut_plan,
    save_bridge_audio_plan,
    save_edit_plan_bridge_draft,
    validate_edit_plan_bridge,
)
from otio_app.services.voiceover_generation.cut_plan_edit_plan_confirm_service import confirm_edit_plan_bridge
from otio_app.services.voiceover_generation.cut_plan_edit_plan_trace import (
    build_edit_plan_bridge_trace,
    save_edit_plan_bridge_trace,
)
from otio_app.services.voiceover_generation.cut_plan_models import CutPlanSettings
from otio_app.services.voiceover_generation.cut_plan_settings_service import save_cut_plan_settings
from otio_app.services.voiceover_generation.final_plan_service import save_confirmed_voiceover_project_plan
from otio_app.services.voiceover_generation.llm_trace_service import content_hash, content_hash_of_model
from otio_app.services.voiceover_generation.models import (
    AlignmentItem,
    ConfirmedFolderPlanItem,
    ConfirmedIntroPlanItem,
    ConfirmedVoiceoverProjectPlan,
    IntroHookVisualBeat,
    SentenceItem,
)
from otio_app.services.voiceover_generation.production_edit_plan_models import ProductionEditPlanValidationError
from otio_app.services.voiceover_generation.production_edit_plan_promote_readiness import (
    build_production_edit_plan_promote_dry_run_trace,
    build_production_edit_plan_promote_readiness,
    is_production_edit_plan_promote_readiness_stale,
    load_production_edit_plan_promote_dry_run_trace,
    load_production_edit_plan_promote_readiness,
    save_production_edit_plan_promote_dry_run_trace,
    save_production_edit_plan_promote_readiness,
)
from otio_app.services.voiceover_generation.production_edit_plan_staging_service import (
    build_and_save_production_edit_plan_staging,
    load_production_edit_plan_staging_package,
    load_staged_edit_plan,
    save_production_edit_plan_staging_package,
    save_staged_edit_plan,
)
from otio_app.services.voiceover_generation.production_edit_plan_validation import (
    save_production_edit_plan_validation_report,
    validate_production_edit_plan_staging,
)

FOLDER_A = "Grand Canyon"


def _make_project(tmp_path: Path) -> Project:
    project_root = tmp_path / "USA"
    (project_root / FOLDER_A).mkdir(parents=True, exist_ok=True)
    return Project(
        id="production-edit-plan-promote-project",
        name="Production EditPlan Promote Test",
        project_root=str(project_root),
        work_dir=str(project_root / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=[FOLDER_A],
        selected_asset_subdirs=[FOLDER_A],
    )


def _write_inventory(project: Project, filenames: list[str]) -> None:
    entries = []
    for filename in filenames:
        (project.project_root_path / FOLDER_A / filename).write_bytes(b"FAKE_MEDIA_BYTES")
        entries.append(AssetMediaAnalysis(path=f"{FOLDER_A}/{filename}", description=filename))
    inv_path = get_folder_inventory_path(project.work_dir_path, FOLDER_A)
    inv_path.parent.mkdir(parents=True, exist_ok=True)
    inv_path.write_text(
        AssetFolderAnalysis(folder=FOLDER_A, assets=entries).model_dump_json(indent=2), encoding="utf-8"
    )


def _write_audio(project: Project, name: str) -> Path:
    audio_dir = project.work_dir_path / "voiceover_generation" / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    path = audio_dir / name
    path.write_bytes(b"FAKE_AUDIO_BYTES")
    return path


def _build_confirmed_bridge_project(tmp_path: Path) -> Project:
    project = _make_project(tmp_path)
    _write_inventory(project, ["photo_a.jpg", "photo_b.jpg"])
    intro_audio = _write_audio(project, "intro.mp3")
    folder_audio = _write_audio(project, "folder.mp3")

    intro = ConfirmedIntroPlanItem(
        hook_text="Ein Ort voller Geheimnisse.", audio_path=str(intro_audio), audio_duration_sec=5.0,
        visual_beats=[IntroHookVisualBeat(hook_beat_id="hook_beat_001", text="x", primary_asset_id="asset_photo_a")],
        alignment_items=[
            AlignmentItem(sentence_id="hook_beat_001", audio_start_sec=0.0, audio_end_sec=5.0, duration_sec=5.0)
        ],
    )
    folder = ConfirmedFolderPlanItem(
        folder_name=FOLDER_A, order_index=1, audio_path=str(folder_audio), audio_duration_sec=5.0,
        sentence_items=[SentenceItem(sentence_id="sentence_001", text="Ein Satz.", primary_asset_id="asset_photo_b")],
        alignment_items=[
            AlignmentItem(sentence_id="sentence_001", audio_start_sec=0.0, audio_end_sec=5.0, duration_sec=5.0)
        ],
    )
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id, project_title="Test", status="AUDIO_READY", intro=intro, folders=[folder]
    )
    save_confirmed_voiceover_project_plan(project, plan)
    save_cut_plan_settings(project, CutPlanSettings(project_id=project.id))
    draft = build_cut_plan_draft(project)
    save_cut_plan_draft(project, draft)
    apply_asset_selection_to_draft(project)
    validate_cut_plan_draft(project)
    confirm_cut_plan(project)

    edit_plan = build_edit_plan_draft_from_confirmed_cut_plan(project)
    edit_plan = save_edit_plan_bridge_draft(project, edit_plan)
    audio_plan = build_bridge_audio_plan_from_confirmed_cut_plan(project)
    save_bridge_audio_plan(project, audio_plan)
    confirmed_cut_plan = load_confirmed_cut_plan(project)
    trace = build_edit_plan_bridge_trace(project, confirmed_cut_plan, edit_plan)
    save_edit_plan_bridge_trace(project, trace)
    validate_edit_plan_bridge(project, edit_plan)
    confirm_edit_plan_bridge(project)
    return project


def _happy_project(tmp_path: Path) -> Project:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    validate_production_edit_plan_staging(project)
    return project


# --- 1-6: Globale Vorab-Blocker ---


def test_blocked_without_package(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    readiness = build_production_edit_plan_promote_readiness(project)
    assert readiness.status == PRODUCTION_EDIT_PLAN_PROMOTE_READINESS_STATUS_BLOCKED
    assert any(PRODUCTION_EDIT_PLAN_ERROR_PRODUCTION_STAGING_MISSING in b for b in readiness.blockers)


def test_blocked_without_validation_report(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    readiness = build_production_edit_plan_promote_readiness(project)
    assert readiness.status == PRODUCTION_EDIT_PLAN_PROMOTE_READINESS_STATUS_BLOCKED
    assert any(PRODUCTION_EDIT_PLAN_ERROR_VALIDATION_REPORT_MISSING in b for b in readiness.blockers)


def test_blocked_with_validation_report_warning(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    report = validate_production_edit_plan_staging(project)
    warned = report.model_copy(update={"status": "WARNING", "warnings": [ProductionEditPlanValidationError(type="X")]})
    save_production_edit_plan_validation_report(project, warned)
    readiness = build_production_edit_plan_promote_readiness(project)
    assert readiness.status == PRODUCTION_EDIT_PLAN_PROMOTE_READINESS_STATUS_BLOCKED
    assert any(PRODUCTION_EDIT_PLAN_ERROR_VALIDATION_REPORT_NOT_PASS in b for b in readiness.blockers)


def test_blocked_with_validation_report_blocked(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    report = validate_production_edit_plan_staging(project)
    blocked = report.model_copy(update={"status": "BLOCKED", "blockers": [ProductionEditPlanValidationError(type="X")]})
    save_production_edit_plan_validation_report(project, blocked)
    readiness = build_production_edit_plan_promote_readiness(project)
    assert readiness.status == PRODUCTION_EDIT_PLAN_PROMOTE_READINESS_STATUS_BLOCKED
    assert any(PRODUCTION_EDIT_PLAN_ERROR_VALIDATION_REPORT_NOT_PASS in b for b in readiness.blockers)


def test_blocked_with_stale_package(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id, project_title="Geändert", status="AUDIO_READY",
        intro=ConfirmedIntroPlanItem(), folders=[],
    )
    save_confirmed_voiceover_project_plan(project, plan)
    readiness = build_production_edit_plan_promote_readiness(project)
    assert readiness.status == PRODUCTION_EDIT_PLAN_PROMOTE_READINESS_STATUS_BLOCKED
    assert any(PRODUCTION_EDIT_PLAN_ERROR_PRODUCTION_STAGING_STALE in b for b in readiness.blockers)


def test_blocked_with_stale_validation_report(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    package = load_production_edit_plan_staging_package(project)
    changed_package = package.model_copy(update={"warnings": ["manually_added"]})
    save_production_edit_plan_staging_package(project, changed_package)
    readiness = build_production_edit_plan_promote_readiness(project)
    assert readiness.status == PRODUCTION_EDIT_PLAN_PROMOTE_READINESS_STATUS_BLOCKED
    assert any(PRODUCTION_EDIT_PLAN_ERROR_VALIDATION_REPORT_STALE in b for b in readiness.blockers)


# --- 7-8: Staged Dateien ---


def test_detects_missing_staged_edit_plan_file(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    get_staged_edit_plan_path(project.work_dir_path, "000_intro").unlink()
    readiness = build_production_edit_plan_promote_readiness(project)
    intro_section = next(s for s in readiness.sections if s.staging_section_id == "000_intro")
    assert intro_section.promote_action == PRODUCTION_EDIT_PLAN_PROMOTE_ACTION_BLOCKED
    assert any(PRODUCTION_EDIT_PLAN_ERROR_STAGED_EDIT_PLAN_MISSING in b for b in intro_section.blockers)


def test_detects_staged_edit_plan_hash_mismatch(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    document = load_staged_edit_plan(project, "000_intro")
    tampered = document.model_copy(update={"plan_generation_notes": ["tampered"]})
    save_staged_edit_plan(project, "000_intro", tampered)
    readiness = build_production_edit_plan_promote_readiness(project)
    intro_section = next(s for s in readiness.sections if s.staging_section_id == "000_intro")
    assert intro_section.promote_action == PRODUCTION_EDIT_PLAN_PROMOTE_ACTION_BLOCKED
    assert any(PRODUCTION_EDIT_PLAN_ERROR_STAGED_EDIT_PLAN_HASH_MISMATCH in b for b in intro_section.blockers)


# --- 9-13: Technische Section-Blocker ---


def test_detects_section_without_voiceover(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    document = load_staged_edit_plan(project, "000_intro")
    tampered = document.model_copy(update={"voiceover": None})
    save_staged_edit_plan(project, "000_intro", tampered)
    package = load_production_edit_plan_staging_package(project)
    intro_pkg_section = next(s for s in package.sections if s.staging_section_id == "000_intro")
    intro_pkg_section.staged_edit_plan_hash = content_hash_of_model(tampered)
    save_production_edit_plan_staging_package(project, package)

    readiness = build_production_edit_plan_promote_readiness(project)
    intro_section = next(s for s in readiness.sections if s.staging_section_id == "000_intro")
    assert any(PRODUCTION_EDIT_PLAN_ERROR_VOICEOVER_PLAN_MISSING in b for b in intro_section.blockers)


def test_detects_section_without_timeline_items(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    document = load_staged_edit_plan(project, "000_intro")
    tampered = document.model_copy(update={"timeline_items": []})
    save_staged_edit_plan(project, "000_intro", tampered)
    package = load_production_edit_plan_staging_package(project)
    intro_pkg_section = next(s for s in package.sections if s.staging_section_id == "000_intro")

    intro_pkg_section.staged_edit_plan_hash = content_hash_of_model(tampered)
    save_production_edit_plan_staging_package(project, package)

    readiness = build_production_edit_plan_promote_readiness(project)
    intro_section = next(s for s in readiness.sections if s.staging_section_id == "000_intro")
    assert any(PRODUCTION_EDIT_PLAN_ERROR_STAGED_EDIT_PLAN_EMPTY_TIMELINE in b for b in intro_section.blockers)


def test_detects_section_without_shots(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    document = load_staged_edit_plan(project, "000_intro")
    tampered = document.model_copy(update={"shots": []})
    save_staged_edit_plan(project, "000_intro", tampered)
    package = load_production_edit_plan_staging_package(project)
    intro_pkg_section = next(s for s in package.sections if s.staging_section_id == "000_intro")

    intro_pkg_section.staged_edit_plan_hash = content_hash_of_model(tampered)
    save_production_edit_plan_staging_package(project, package)

    readiness = build_production_edit_plan_promote_readiness(project)
    intro_section = next(s for s in readiness.sections if s.staging_section_id == "000_intro")
    assert any(PRODUCTION_EDIT_PLAN_ERROR_STAGED_EDIT_PLAN_EMPTY_SHOTS in b for b in intro_section.blockers)


def test_detects_voiceover_audio_leak(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    document = load_staged_edit_plan(project, "000_intro")
    leaked_item = TimelineItem(
        timeline_item_id="leaked_audio", type="voiceover_audio", section_id="section_intro",
        folder_name="Intro", timeline_in_sec=0.0, timeline_out_sec=5.0, duration_sec=5.0,
        source_out_sec=5.0, track="A1",
    )
    tampered = document.model_copy(update={"timeline_items": list(document.timeline_items) + [leaked_item]})
    save_staged_edit_plan(project, "000_intro", tampered)
    package = load_production_edit_plan_staging_package(project)
    intro_pkg_section = next(s for s in package.sections if s.staging_section_id == "000_intro")

    intro_pkg_section.staged_edit_plan_hash = content_hash_of_model(tampered)
    save_production_edit_plan_staging_package(project, package)

    readiness = build_production_edit_plan_promote_readiness(project)
    intro_section = next(s for s in readiness.sections if s.staging_section_id == "000_intro")
    assert any(PRODUCTION_EDIT_PLAN_ERROR_VOICEOVER_AUDIO_ITEM_LEAKED in b for b in intro_section.blockers)


def test_detects_secret_leak(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-super-secret-value-for-test")
    project = _happy_project(tmp_path)
    document = load_staged_edit_plan(project, "000_intro")
    tampered = document.model_copy(update={"plan_generation_notes": ["leaked: sk-super-secret-value-for-test"]})
    save_staged_edit_plan(project, "000_intro", tampered)
    package = load_production_edit_plan_staging_package(project)
    intro_pkg_section = next(s for s in package.sections if s.staging_section_id == "000_intro")

    intro_pkg_section.staged_edit_plan_hash = content_hash_of_model(tampered)
    save_production_edit_plan_staging_package(project, package)

    readiness = build_production_edit_plan_promote_readiness(project)
    intro_section = next(s for s in readiness.sections if s.staging_section_id == "000_intro")
    assert any("SECRET_LEAK_DETECTED" in b for b in intro_section.blockers)


# --- 14-19: promote_action Klassifikation + Readiness-Status ---


def test_intro_section_is_would_create(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    readiness = build_production_edit_plan_promote_readiness(project)
    intro_section = next(s for s in readiness.sections if s.staging_section_id == "000_intro")
    assert intro_section.promote_action == PRODUCTION_EDIT_PLAN_PROMOTE_ACTION_WOULD_CREATE
    assert intro_section.is_intro is True
    assert intro_section.target_edit_plan_path.endswith("Intro.json")
    assert intro_section.folder_name == "Intro"


def test_folder_without_existing_plan_is_would_create(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    readiness = build_production_edit_plan_promote_readiness(project)
    folder_section = next(s for s in readiness.sections if s.folder_name == FOLDER_A)
    assert folder_section.promote_action == PRODUCTION_EDIT_PLAN_PROMOTE_ACTION_WOULD_CREATE
    assert folder_section.target_exists is False


def test_folder_with_existing_plan_is_would_overwrite(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    existing_path = get_folder_edit_plan_path(project.work_dir_path, FOLDER_A)
    existing_path.parent.mkdir(parents=True, exist_ok=True)
    existing_path.write_text('{"project_id": "existing", "folder_name": "Grand Canyon", "confirmed": true}', encoding="utf-8")

    readiness = build_production_edit_plan_promote_readiness(project)
    folder_section = next(s for s in readiness.sections if s.folder_name == FOLDER_A)
    assert folder_section.promote_action == PRODUCTION_EDIT_PLAN_PROMOTE_ACTION_WOULD_OVERWRITE
    assert folder_section.target_exists is True


def test_would_overwrite_leads_to_needs_review_status(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    existing_path = get_folder_edit_plan_path(project.work_dir_path, FOLDER_A)
    existing_path.parent.mkdir(parents=True, exist_ok=True)
    existing_path.write_text('{"project_id": "existing", "folder_name": "Grand Canyon", "confirmed": true}', encoding="utf-8")

    readiness = build_production_edit_plan_promote_readiness(project)
    assert readiness.status == PRODUCTION_EDIT_PLAN_PROMOTE_READINESS_STATUS_NEEDS_REVIEW


def test_all_new_leads_to_ready_status(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    readiness = build_production_edit_plan_promote_readiness(project)
    assert readiness.status == PRODUCTION_EDIT_PLAN_PROMOTE_READINESS_STATUS_READY
    assert readiness.warnings == []
    assert readiness.blockers == []


def test_technical_blocker_leads_to_blocked_status(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    document = load_staged_edit_plan(project, "000_intro")
    tampered = document.model_copy(update={"voiceover": None})
    save_staged_edit_plan(project, "000_intro", tampered)
    package = load_production_edit_plan_staging_package(project)
    intro_pkg_section = next(s for s in package.sections if s.staging_section_id == "000_intro")

    intro_pkg_section.staged_edit_plan_hash = content_hash_of_model(tampered)
    save_production_edit_plan_staging_package(project, package)

    readiness = build_production_edit_plan_promote_readiness(project)
    assert readiness.status == PRODUCTION_EDIT_PLAN_PROMOTE_READINESS_STATUS_BLOCKED


# --- 20-23: Kollisionsprüfung Details ---


def test_existing_production_plan_is_only_read(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    existing_path = get_folder_edit_plan_path(project.work_dir_path, FOLDER_A)
    existing_path.parent.mkdir(parents=True, exist_ok=True)
    existing_content = '{"project_id": "existing", "folder_name": "Grand Canyon", "confirmed": true}'
    existing_path.write_text(existing_content, encoding="utf-8")

    build_production_edit_plan_promote_readiness(project)

    assert existing_path.read_text(encoding="utf-8") == existing_content


def test_existing_production_plan_hash_is_captured(tmp_path: Path) -> None:

    project = _happy_project(tmp_path)
    existing_path = get_folder_edit_plan_path(project.work_dir_path, FOLDER_A)
    existing_path.parent.mkdir(parents=True, exist_ok=True)
    existing_content = '{"project_id": "existing", "folder_name": "Grand Canyon", "confirmed": true}'
    existing_path.write_text(existing_content, encoding="utf-8")

    readiness = build_production_edit_plan_promote_readiness(project)
    folder_section = next(s for s in readiness.sections if s.folder_name == FOLDER_A)
    assert folder_section.existing_file_hash == content_hash(existing_content)


def test_existing_production_plan_metadata_is_captured(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    existing_path = get_folder_edit_plan_path(project.work_dir_path, FOLDER_A)
    existing_path.parent.mkdir(parents=True, exist_ok=True)
    existing_path.write_text(
        '{"project_id": "existing", "folder_name": "Grand Canyon", "confirmed": true, '
        '"candidate_status": "SOME_STATUS", "shots": [], "timeline_items": []}',
        encoding="utf-8",
    )

    readiness = build_production_edit_plan_promote_readiness(project)
    folder_section = next(s for s in readiness.sections if s.folder_name == FOLDER_A)
    assert folder_section.existing_confirmed is True
    assert folder_section.existing_candidate_status == "SOME_STATUS"
    assert folder_section.existing_shot_count == 0
    assert folder_section.existing_timeline_item_count == 0


def test_unreadable_existing_production_plan_yields_warning_not_crash(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    existing_path = get_folder_edit_plan_path(project.work_dir_path, FOLDER_A)
    existing_path.parent.mkdir(parents=True, exist_ok=True)
    existing_path.write_text("NOT VALID JSON {{{", encoding="utf-8")

    readiness = build_production_edit_plan_promote_readiness(project)
    folder_section = next(s for s in readiness.sections if s.folder_name == FOLDER_A)
    assert any(PRODUCTION_EDIT_PLAN_ERROR_EXISTING_PRODUCTION_PLAN_UNREADABLE in w for w in folder_section.warnings)
    assert folder_section.existing_confirmed is None


# --- 24-26: Speichern / Trace ---


def test_readiness_file_is_written_under_staging_dir(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    readiness = build_production_edit_plan_promote_readiness(project)
    save_production_edit_plan_promote_readiness(project, readiness)
    assert get_production_edit_plan_promote_readiness_path(project.work_dir_path).is_file()


def test_dry_run_trace_file_is_written_under_staging_dir(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    readiness = build_production_edit_plan_promote_readiness(project)
    trace = build_production_edit_plan_promote_dry_run_trace(project, readiness)
    save_production_edit_plan_promote_dry_run_trace(project, trace)
    assert get_production_edit_plan_promote_dry_run_trace_path(project.work_dir_path).is_file()


def test_dry_run_trace_would_write_and_would_overwrite_are_correct(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    existing_path = get_folder_edit_plan_path(project.work_dir_path, FOLDER_A)
    existing_path.parent.mkdir(parents=True, exist_ok=True)
    existing_path.write_text('{"project_id": "existing", "folder_name": "Grand Canyon", "confirmed": true}', encoding="utf-8")

    readiness = build_production_edit_plan_promote_readiness(project)
    trace = build_production_edit_plan_promote_dry_run_trace(project, readiness)

    intro_entry = next(e for e in trace.entries if e.staging_section_id == "000_intro")
    assert intro_entry.would_write is True
    assert intro_entry.would_overwrite is False

    folder_entry = next(e for e in trace.entries if e.folder_name == FOLDER_A)
    assert folder_entry.would_write is True
    assert folder_entry.would_overwrite is True


# --- 27-28: Staleness ---


def test_readiness_stale_detects_changed_package(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    readiness = build_production_edit_plan_promote_readiness(project)
    save_production_edit_plan_promote_readiness(project, readiness)
    assert is_production_edit_plan_promote_readiness_stale(project, readiness) is False

    package = load_production_edit_plan_staging_package(project)
    changed_package = package.model_copy(update={"warnings": ["manually_added"]})
    save_production_edit_plan_staging_package(project, changed_package)

    assert is_production_edit_plan_promote_readiness_stale(project, readiness) is True


def test_readiness_stale_detects_changed_validation_report(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    readiness = build_production_edit_plan_promote_readiness(project)
    save_production_edit_plan_promote_readiness(project, readiness)
    assert is_production_edit_plan_promote_readiness_stale(project, readiness) is False

    report = validate_production_edit_plan_staging(project)
    changed_report = report.model_copy(update={"warnings": [ProductionEditPlanValidationError(type="X")]})
    save_production_edit_plan_validation_report(project, changed_report)

    assert is_production_edit_plan_promote_readiness_stale(project, readiness) is True


def test_load_readiness_returns_none_when_missing(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    assert load_production_edit_plan_promote_readiness(project) is None


def test_load_dry_run_trace_returns_none_when_missing(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    assert load_production_edit_plan_promote_dry_run_trace(project) is None


# --- 36-42: Schutz bestehender Pipeline / keine Seiteneffekte ---


def test_no_files_written_under_edit_plan_dir(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    readiness = build_production_edit_plan_promote_readiness(project)
    save_production_edit_plan_promote_readiness(project, readiness)
    trace = build_production_edit_plan_promote_dry_run_trace(project, readiness)
    save_production_edit_plan_promote_dry_run_trace(project, trace)
    assert not get_edit_plan_dir(project.work_dir_path).exists()


def test_no_files_written_under_exports_dir(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    build_production_edit_plan_promote_readiness(project)
    assert not get_exports_dir(project.work_dir_path).exists()


def test_no_files_written_under_supplement_dir(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    build_production_edit_plan_promote_readiness(project)
    assert not get_supplement_dir(project.work_dir_path).exists()


def test_existing_production_edit_plan_remains_byte_identical(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    existing_path = get_folder_edit_plan_path(project.work_dir_path, FOLDER_A)
    existing_path.parent.mkdir(parents=True, exist_ok=True)
    existing_content = '{"project_id": "existing", "folder_name": "Grand Canyon", "confirmed": true}'
    existing_path.write_text(existing_content, encoding="utf-8")

    readiness = build_production_edit_plan_promote_readiness(project)
    save_production_edit_plan_promote_readiness(project, readiness)
    trace = build_production_edit_plan_promote_dry_run_trace(project, readiness)
    save_production_edit_plan_promote_dry_run_trace(project, trace)

    assert existing_path.read_text(encoding="utf-8") == existing_content


def test_no_original_media_modified(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    photo_path = project.project_root_path / FOLDER_A / "photo_a.jpg"
    original = photo_path.read_bytes()
    build_production_edit_plan_promote_readiness(project)
    assert photo_path.read_bytes() == original


def test_no_audio_files_overwritten(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    audio_path = project.work_dir_path / "voiceover_generation" / "audio" / "intro.mp3"
    original = audio_path.read_bytes()
    build_production_edit_plan_promote_readiness(project)
    assert audio_path.read_bytes() == original


def test_no_voice_folder_mapping_change(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    mapping_path = get_voice_folder_mapping_path(project.project_root_path)
    mapping_path.parent.mkdir(parents=True, exist_ok=True)
    existing_content = '{"mappings": []}'
    mapping_path.write_text(existing_content, encoding="utf-8")

    readiness = build_production_edit_plan_promote_readiness(project)
    save_production_edit_plan_promote_readiness(project, readiness)

    assert mapping_path.read_text(encoding="utf-8") == existing_content


def test_no_save_edit_plan_or_build_edit_plan_calls_referenced() -> None:
    import otio_app.services.voiceover_generation.production_edit_plan_promote_readiness as promote_module

    source = inspect.getsource(promote_module)
    assert not re.search(r"\bsave_edit_plan\b", source)
    assert not re.search(r"\bbuild_edit_plan\b", source)


def test_no_otio_export_referenced() -> None:
    import otio_app.services.voiceover_generation.production_edit_plan_promote_readiness as promote_module

    source = inspect.getsource(promote_module)
    assert not re.search(r"\botio_exporter\b", source)
    assert not re.search(r"\bexport_otio_timeline\b", source)


_FORBIDDEN_SYMBOLS = (
    "build_edit_plan",
    "save_edit_plan",
    "edit_plan_builder",
    "otio_exporter",
    "export_otio_timeline",
    "mark_edit_plans_stale_for_folder",
    "replan_folder_after_supplement",
    "extend_folder_inventory",
    "_set_draft",
    "merge_confirmed_edit_plans",
)


def test_promote_readiness_modules_reference_no_forbidden_production_functions() -> None:
    import otio_app.services.voiceover_generation.production_edit_plan_promote_models as models_module
    import otio_app.services.voiceover_generation.production_edit_plan_promote_readiness as promote_module

    for module in (models_module, promote_module):
        source = inspect.getsource(module)
        for symbol in _FORBIDDEN_SYMBOLS:
            assert not re.search(rf"\b{re.escape(symbol)}\b", source), (
                f"{module.__name__} referenziert verbotenes Symbol '{symbol}'."
            )


def test_with_voiceover_workflow_unaffected() -> None:
    from otio_app.services import edit_plan_builder, otio_exporter

    assert hasattr(edit_plan_builder, "build_edit_plan")
    assert hasattr(edit_plan_builder, "save_edit_plan")
    assert hasattr(otio_exporter, "build_otio_timeline")
