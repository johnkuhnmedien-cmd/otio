"""Phase 10.3: Production EditPlan Staging — vollständige Revalidierung.

Rein prüfend: keine automatische Reparatur, keine Produktions-Promotion, kein
OTIO-Export, kein Render, keine save_edit_plan()/build_edit_plan()-Aufrufe,
keine UI, kein Schreiben nach _otio/edit_plan/."""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis, TimelineItem
from otio_app.defaults import (
    PRODUCTION_EDIT_PLAN_ERROR_MAPPING_TRACE_ITEM_MISSING,
    PRODUCTION_EDIT_PLAN_ERROR_MAPPING_TRACE_ROUNDTRIP_FAILED,
    PRODUCTION_EDIT_PLAN_ERROR_PRODUCTION_STAGING_MISSING,
    PRODUCTION_EDIT_PLAN_ERROR_PRODUCTION_STAGING_STALE,
    PRODUCTION_EDIT_PLAN_ERROR_PRODUCTION_STAGING_TRACE_MISSING,
    PRODUCTION_EDIT_PLAN_ERROR_PRODUCTION_TIMELINE_VALIDATION_FAILED,
    PRODUCTION_EDIT_PLAN_ERROR_SECRET_LEAK_DETECTED,
    PRODUCTION_EDIT_PLAN_ERROR_SHOT_COUNT_MISMATCH,
    PRODUCTION_EDIT_PLAN_ERROR_SHOT_DURATION_INVALID,
    PRODUCTION_EDIT_PLAN_ERROR_SHOT_TIMING_OUTSIDE_VOICEOVER,
    PRODUCTION_EDIT_PLAN_ERROR_STAGED_EDIT_PLAN_CONFIRMED_TRUE,
    PRODUCTION_EDIT_PLAN_ERROR_STAGED_EDIT_PLAN_EMPTY_SHOTS,
    PRODUCTION_EDIT_PLAN_ERROR_STAGED_EDIT_PLAN_EMPTY_TIMELINE,
    PRODUCTION_EDIT_PLAN_ERROR_STAGED_EDIT_PLAN_HASH_MISMATCH,
    PRODUCTION_EDIT_PLAN_ERROR_STAGED_EDIT_PLAN_MISSING,
    PRODUCTION_EDIT_PLAN_ERROR_STAGED_EDIT_PLAN_STATUS_INVALID,
    PRODUCTION_EDIT_PLAN_ERROR_TIMELINE_ITEM_ASSET_MISSING,
    PRODUCTION_EDIT_PLAN_ERROR_TIMELINE_ITEM_TYPE_INVALID,
    PRODUCTION_EDIT_PLAN_ERROR_VOICEOVER_AUDIO_ITEM_LEAKED,
    PRODUCTION_EDIT_PLAN_ERROR_VOICEOVER_AUDIO_PATH_MISSING,
    PRODUCTION_EDIT_PLAN_ERROR_VOICEOVER_DURATION_INVALID,
    PRODUCTION_EDIT_PLAN_ERROR_VOICEOVER_DURATION_SOURCE_INVALID,
    PRODUCTION_EDIT_PLAN_ERROR_VOICEOVER_PLAN_MISSING,
    PRODUCTION_EDIT_PLAN_ERROR_VOICEOVER_TIMING_INVALID,
    PRODUCTION_EDIT_PLAN_ERROR_VOICEOVER_TRIM_POLICY_INVALID,
    PRODUCTION_EDIT_PLAN_VALIDATION_STATUS_BLOCKED,
    PRODUCTION_EDIT_PLAN_VALIDATION_STATUS_PASS,
    PRODUCTION_EDIT_PLAN_VALIDATION_STATUS_WARNING,
)
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import (
    get_edit_plan_dir,
    get_exports_dir,
    get_folder_edit_plan_path,
    get_production_edit_plan_mapping_trace_path,
    get_production_edit_plan_package_path,
    get_production_edit_plan_validation_report_path,
    get_staged_edit_plan_path,
    get_supplement_dir,
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
from otio_app.services.voiceover_generation.models import (
    AlignmentItem,
    ConfirmedFolderPlanItem,
    ConfirmedIntroPlanItem,
    ConfirmedVoiceoverProjectPlan,
    IntroHookVisualBeat,
    SentenceItem,
)
from otio_app.services.voiceover_generation.production_edit_plan_models import (
    ProductionEditPlanValidationError,
    ProductionEditPlanValidationReport,
)
from otio_app.services.voiceover_generation.production_edit_plan_staging_service import (
    build_and_save_production_edit_plan_staging,
    load_production_edit_plan_staging_package,
    load_staged_edit_plan,
    save_production_edit_plan_staging_package,
    save_staged_edit_plan,
)
from otio_app.services.voiceover_generation.production_edit_plan_trace import (
    load_production_edit_plan_mapping_trace,
    save_production_edit_plan_mapping_trace,
)
from otio_app.services.voiceover_generation.production_edit_plan_validation import (
    build_production_validation_error_from_existing_error,
    classify_production_edit_plan_validation_status,
    is_production_edit_plan_validation_report_stale,
    load_production_edit_plan_validation_report,
    normalize_existing_validator_error,
    save_production_edit_plan_validation_report,
    validate_production_edit_plan_staging,
    validate_staged_section,
)

FOLDER_A = "Grand Canyon"


def _make_project(tmp_path: Path) -> Project:
    project_root = tmp_path / "USA"
    (project_root / FOLDER_A).mkdir(parents=True)
    return Project(
        id="production-edit-plan-validation-project",
        name="Production EditPlan Validation Test",
        project_root=str(project_root),
        work_dir=str(project_root / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=[FOLDER_A],
        selected_asset_subdirs=[FOLDER_A],
    )


def _write_inventory(project: Project, filenames: list[str]) -> None:
    from otio_app.project_layout import get_folder_inventory_path

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
    return project


# --- 1-4: Vorab-Checks auf Paket-/Trace-/Datei-Ebene ---


def test_blocked_without_package(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    report = validate_production_edit_plan_staging(project)
    assert report.status == PRODUCTION_EDIT_PLAN_VALIDATION_STATUS_BLOCKED
    assert any(b.type == PRODUCTION_EDIT_PLAN_ERROR_PRODUCTION_STAGING_MISSING for b in report.blockers)


def test_blocked_with_stale_package(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id, project_title="Geändert", status="AUDIO_READY",
        intro=ConfirmedIntroPlanItem(), folders=[],
    )
    save_confirmed_voiceover_project_plan(project, plan)
    report = validate_production_edit_plan_staging(project)
    assert report.status == PRODUCTION_EDIT_PLAN_VALIDATION_STATUS_BLOCKED
    assert any(b.type == PRODUCTION_EDIT_PLAN_ERROR_PRODUCTION_STAGING_STALE for b in report.blockers)


def test_blocked_without_mapping_trace(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    get_production_edit_plan_mapping_trace_path(project.work_dir_path).unlink()
    report = validate_production_edit_plan_staging(project)
    assert report.status == PRODUCTION_EDIT_PLAN_VALIDATION_STATUS_BLOCKED
    assert any(b.type == PRODUCTION_EDIT_PLAN_ERROR_PRODUCTION_STAGING_TRACE_MISSING for b in report.blockers)


def test_blocked_with_missing_staged_edit_plan_file(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    get_staged_edit_plan_path(project.work_dir_path, "000_intro").unlink()
    report = validate_production_edit_plan_staging(project)
    assert report.status == PRODUCTION_EDIT_PLAN_VALIDATION_STATUS_BLOCKED
    assert any(b.type == PRODUCTION_EDIT_PLAN_ERROR_STAGED_EDIT_PLAN_MISSING for b in report.blockers)


def test_blocked_with_staged_edit_plan_hash_mismatch(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    document = load_staged_edit_plan(project, "000_intro")
    tampered = document.model_copy(update={"plan_generation_notes": ["tampered"]})
    save_staged_edit_plan(project, "000_intro", tampered)
    report = validate_production_edit_plan_staging(project)
    assert report.status == PRODUCTION_EDIT_PLAN_VALIDATION_STATUS_BLOCKED
    assert any(b.type == PRODUCTION_EDIT_PLAN_ERROR_STAGED_EDIT_PLAN_HASH_MISMATCH for b in report.blockers)


# --- 6-7: Dokument-Struktur ---


def test_blocked_when_staged_edit_plan_confirmed_true(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    document = load_staged_edit_plan(project, "000_intro")
    tampered = document.model_copy(update={"confirmed": True})
    save_staged_edit_plan(project, "000_intro", tampered)
    package = load_production_edit_plan_staging_package(project)
    section = next(s for s in package.sections if s.staging_section_id == "000_intro")
    warnings, blockers = validate_staged_section(project, section, tampered)
    assert any(b.type == PRODUCTION_EDIT_PLAN_ERROR_STAGED_EDIT_PLAN_CONFIRMED_TRUE for b in blockers)


def test_blocked_with_wrong_candidate_status(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    document = load_staged_edit_plan(project, "000_intro")
    tampered = document.model_copy(update={"candidate_status": "SOMETHING_ELSE"})
    package = load_production_edit_plan_staging_package(project)
    section = next(s for s in package.sections if s.staging_section_id == "000_intro")
    _warnings, blockers = validate_staged_section(project, section, tampered)
    assert any(b.type == PRODUCTION_EDIT_PLAN_ERROR_STAGED_EDIT_PLAN_STATUS_INVALID for b in blockers)


# --- 8-14: VoiceoverPlan ---


def test_blocked_with_missing_voiceover_plan(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    document = load_staged_edit_plan(project, "000_intro")
    tampered = document.model_copy(update={"voiceover": None})
    package = load_production_edit_plan_staging_package(project)
    section = next(s for s in package.sections if s.staging_section_id == "000_intro")
    _warnings, blockers = validate_staged_section(project, section, tampered)
    assert any(b.type == PRODUCTION_EDIT_PLAN_ERROR_VOICEOVER_PLAN_MISSING for b in blockers)


def test_blocked_with_missing_voiceover_audio_file(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    document = load_staged_edit_plan(project, "000_intro")
    tampered_voiceover = document.voiceover.model_copy(update={"path": "/does/not/exist.mp3"})
    tampered = document.model_copy(update={"voiceover": tampered_voiceover})
    package = load_production_edit_plan_staging_package(project)
    section = next(s for s in package.sections if s.staging_section_id == "000_intro")
    _warnings, blockers = validate_staged_section(project, section, tampered)
    assert any(b.type == PRODUCTION_EDIT_PLAN_ERROR_VOICEOVER_AUDIO_PATH_MISSING for b in blockers)


def test_blocked_with_invalid_voiceover_duration(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    document = load_staged_edit_plan(project, "000_intro")
    tampered_voiceover = document.voiceover.model_copy(update={"duration_sec": 0.0})
    tampered = document.model_copy(update={"voiceover": tampered_voiceover})
    package = load_production_edit_plan_staging_package(project)
    section = next(s for s in package.sections if s.staging_section_id == "000_intro")
    _warnings, blockers = validate_staged_section(project, section, tampered)
    assert any(b.type == PRODUCTION_EDIT_PLAN_ERROR_VOICEOVER_DURATION_INVALID for b in blockers)


def test_blocked_with_invalid_voiceover_timing(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    document = load_staged_edit_plan(project, "000_intro")
    tampered_voiceover = document.voiceover.model_copy(
        update={"timeline_end_sec": document.voiceover.timeline_start_sec}
    )
    tampered = document.model_copy(update={"voiceover": tampered_voiceover})
    package = load_production_edit_plan_staging_package(project)
    section = next(s for s in package.sections if s.staging_section_id == "000_intro")
    _warnings, blockers = validate_staged_section(project, section, tampered)
    assert any(b.type == PRODUCTION_EDIT_PLAN_ERROR_VOICEOVER_TIMING_INVALID for b in blockers)


def test_blocked_with_wrong_duration_source(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    document = load_staged_edit_plan(project, "000_intro")
    tampered_voiceover = document.voiceover.model_copy(update={"duration_source": "ffprobe"})
    tampered = document.model_copy(update={"voiceover": tampered_voiceover})
    package = load_production_edit_plan_staging_package(project)
    section = next(s for s in package.sections if s.staging_section_id == "000_intro")
    _warnings, blockers = validate_staged_section(project, section, tampered)
    assert any(b.type == PRODUCTION_EDIT_PLAN_ERROR_VOICEOVER_DURATION_SOURCE_INVALID for b in blockers)


def test_blocked_with_wrong_trim_policy(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    document = load_staged_edit_plan(project, "000_intro")
    tampered_voiceover = document.voiceover.model_copy(update={"trim_policy": "auto_trim"})
    tampered = document.model_copy(update={"voiceover": tampered_voiceover})
    package = load_production_edit_plan_staging_package(project)
    section = next(s for s in package.sections if s.staging_section_id == "000_intro")
    _warnings, blockers = validate_staged_section(project, section, tampered)
    assert any(b.type == PRODUCTION_EDIT_PLAN_ERROR_VOICEOVER_TRIM_POLICY_INVALID for b in blockers)


def test_blocked_when_voiceover_audio_leaks_as_timeline_item(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    document = load_staged_edit_plan(project, "000_intro")
    leaked_item = TimelineItem(
        timeline_item_id="leaked_audio",
        type="voiceover_audio",
        section_id="section_intro",
        folder_name="Intro",
        timeline_in_sec=0.0,
        timeline_out_sec=5.0,
        duration_sec=5.0,
        source_out_sec=5.0,
        track="A1",
    )
    tampered = document.model_copy(update={"timeline_items": list(document.timeline_items) + [leaked_item]})
    package = load_production_edit_plan_staging_package(project)
    section = next(s for s in package.sections if s.staging_section_id == "000_intro")
    _warnings, blockers = validate_staged_section(project, section, tampered)
    assert any(b.type == PRODUCTION_EDIT_PLAN_ERROR_VOICEOVER_AUDIO_ITEM_LEAKED for b in blockers)


# --- 15-20: TimelineItems ---


def test_blocked_with_empty_timeline(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    document = load_staged_edit_plan(project, "000_intro")
    tampered = document.model_copy(update={"timeline_items": []})
    package = load_production_edit_plan_staging_package(project)
    section = next(s for s in package.sections if s.staging_section_id == "000_intro")
    _warnings, blockers = validate_staged_section(project, section, tampered)
    assert any(b.type == PRODUCTION_EDIT_PLAN_ERROR_STAGED_EDIT_PLAN_EMPTY_TIMELINE for b in blockers)


def test_blocked_with_empty_shots(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    document = load_staged_edit_plan(project, "000_intro")
    tampered = document.model_copy(update={"shots": []})
    package = load_production_edit_plan_staging_package(project)
    section = next(s for s in package.sections if s.staging_section_id == "000_intro")
    _warnings, blockers = validate_staged_section(project, section, tampered)
    assert any(b.type == PRODUCTION_EDIT_PLAN_ERROR_STAGED_EDIT_PLAN_EMPTY_SHOTS for b in blockers)
    assert any(b.type == PRODUCTION_EDIT_PLAN_ERROR_SHOT_COUNT_MISMATCH for b in blockers)


def test_blocked_with_zero_duration_timeline_item(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    document = load_staged_edit_plan(project, "000_intro")
    item = document.timeline_items[0]
    broken_item = item.model_copy(update={"timeline_out_sec": item.timeline_in_sec})
    tampered = document.model_copy(update={"timeline_items": [broken_item]})
    package = load_production_edit_plan_staging_package(project)
    section = next(s for s in package.sections if s.staging_section_id == "000_intro")
    _warnings, blockers = validate_staged_section(project, section, tampered)
    assert any(
        b.type == "TIMELINE_ITEM_DURATION_INVALID" and b.timeline_item_id == item.timeline_item_id for b in blockers
    )


def test_blocked_with_source_out_not_greater_than_source_in(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    document = load_staged_edit_plan(project, "000_intro")
    item = document.timeline_items[0]
    broken_item = item.model_copy(update={"source_out_sec": item.source_in_sec})
    tampered = document.model_copy(update={"timeline_items": [broken_item]})
    package = load_production_edit_plan_staging_package(project)
    section = next(s for s in package.sections if s.staging_section_id == "000_intro")
    _warnings, blockers = validate_staged_section(project, section, tampered)
    assert any(b.type == "TIMELINE_ITEM_SOURCE_RANGE_INVALID" for b in blockers)


def test_blocked_with_missing_visual_asset(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    document = load_staged_edit_plan(project, "000_intro")
    item = document.timeline_items[0]
    broken_item = item.model_copy(update={"resolved_media_path": ""})
    tampered = document.model_copy(update={"timeline_items": [broken_item]})
    package = load_production_edit_plan_staging_package(project)
    section = next(s for s in package.sections if s.staging_section_id == "000_intro")
    _warnings, blockers = validate_staged_section(project, section, tampered)
    assert any(b.type == PRODUCTION_EDIT_PLAN_ERROR_TIMELINE_ITEM_ASSET_MISSING for b in blockers)


def test_blocked_with_invalid_timeline_item_type(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    document = load_staged_edit_plan(project, "000_intro")
    item = document.timeline_items[0]
    broken_item = item.model_copy(update={"type": "opening_title"})
    tampered = document.model_copy(update={"timeline_items": [broken_item]})
    package = load_production_edit_plan_staging_package(project)
    section = next(s for s in package.sections if s.staging_section_id == "000_intro")
    _warnings, blockers = validate_staged_section(project, section, tampered)
    assert any(b.type == PRODUCTION_EDIT_PLAN_ERROR_TIMELINE_ITEM_TYPE_INVALID for b in blockers)


# --- 21: Bestehender validate_timeline_items-Fehler wird übersetzt ---


def test_existing_validate_timeline_items_error_is_translated(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    document = load_staged_edit_plan(project, "000_intro")
    item = document.timeline_items[0]
    # Bewusst rights_status auf einen manuellen Freigabe-Status setzen — löst
    # in validate_timeline_items einen harten String-Error aus.
    broken_item = item.model_copy(update={"rights_status": "NEEDS_REVIEW"})
    tampered = document.model_copy(update={"timeline_items": [broken_item]})
    package = load_production_edit_plan_staging_package(project)
    section = next(s for s in package.sections if s.staging_section_id == "000_intro")
    _warnings, blockers = validate_staged_section(project, section, tampered)
    assert any(
        b.type == PRODUCTION_EDIT_PLAN_ERROR_PRODUCTION_TIMELINE_VALIDATION_FAILED
        and b.timeline_item_id == item.timeline_item_id
        for b in blockers
    )


# --- 22-24: Shots ---


def test_blocked_with_shot_count_mismatch(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    document = load_staged_edit_plan(project, "000_intro")
    tampered = document.model_copy(update={"shots": list(document.shots) + [document.shots[0]]})
    package = load_production_edit_plan_staging_package(project)
    section = next(s for s in package.sections if s.staging_section_id == "000_intro")
    _warnings, blockers = validate_staged_section(project, section, tampered)
    assert any(b.type == PRODUCTION_EDIT_PLAN_ERROR_SHOT_COUNT_MISMATCH for b in blockers)


def test_blocked_with_negative_shot_duration(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    document = load_staged_edit_plan(project, "000_intro")
    broken_shot = document.shots[0].model_copy(update={"duration_sec": 0.0})
    tampered = document.model_copy(update={"shots": [broken_shot]})
    package = load_production_edit_plan_staging_package(project)
    section = next(s for s in package.sections if s.staging_section_id == "000_intro")
    _warnings, blockers = validate_staged_section(project, section, tampered)
    assert any(b.type == PRODUCTION_EDIT_PLAN_ERROR_SHOT_DURATION_INVALID for b in blockers)


def test_blocked_with_shot_timing_outside_voiceover(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    document = load_staged_edit_plan(project, "000_intro")
    broken_shot = document.shots[0].model_copy(update={"voice_end_sec": document.voiceover.duration_sec + 50.0})
    tampered = document.model_copy(update={"shots": [broken_shot]})
    package = load_production_edit_plan_staging_package(project)
    section = next(s for s in package.sections if s.staging_section_id == "000_intro")
    _warnings, blockers = validate_staged_section(project, section, tampered)
    assert any(b.type == PRODUCTION_EDIT_PLAN_ERROR_SHOT_TIMING_OUTSIDE_VOICEOVER for b in blockers)


# --- 25-28: Mapping Trace ---


def test_blocked_with_missing_visual_mapping_trace(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    trace = load_production_edit_plan_mapping_trace(project)
    filtered = trace.model_copy(
        update={"entries": [e for e in trace.entries if not e.source_bridge_timeline_item_id]}
    )
    save_production_edit_plan_mapping_trace(project, filtered)
    report = validate_production_edit_plan_staging(project)
    assert any(b.type == PRODUCTION_EDIT_PLAN_ERROR_MAPPING_TRACE_ITEM_MISSING for b in report.blockers)


def test_blocked_with_missing_audio_mapping_trace(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    trace = load_production_edit_plan_mapping_trace(project)
    filtered = trace.model_copy(
        update={"entries": [e for e in trace.entries if e.mapping_reason != "bridge_audio_plan_to_voiceover_plan"]}
    )
    save_production_edit_plan_mapping_trace(project, filtered)
    report = validate_production_edit_plan_staging(project)
    assert any(b.type == PRODUCTION_EDIT_PLAN_ERROR_MAPPING_TRACE_ITEM_MISSING for b in report.blockers)


def test_blocked_with_trace_section_not_in_package(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    trace = load_production_edit_plan_mapping_trace(project)
    entry = trace.entries[0]
    tampered_entry = entry.model_copy(update={"resulting_staging_section_id": "999_ghost"})
    other_entries = [e for e in trace.entries if e.trace_id != entry.trace_id]
    tampered_trace = trace.model_copy(update={"entries": other_entries + [tampered_entry]})
    save_production_edit_plan_mapping_trace(project, tampered_trace)
    report = validate_production_edit_plan_staging(project)
    assert any(b.type == PRODUCTION_EDIT_PLAN_ERROR_MAPPING_TRACE_ITEM_MISSING for b in report.blockers)


def test_blocked_with_trace_roundtrip_failure(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    trace = load_production_edit_plan_mapping_trace(project)
    entry = next(e for e in trace.entries if e.source_bridge_timeline_item_id)
    tampered_entry = entry.model_copy(update={"original_timeline_in_sec": entry.original_timeline_in_sec + 999.0})
    other_entries = [e for e in trace.entries if e.trace_id != entry.trace_id]
    tampered_trace = trace.model_copy(update={"entries": other_entries + [tampered_entry]})
    save_production_edit_plan_mapping_trace(project, tampered_trace)
    report = validate_production_edit_plan_staging(project)
    assert any(b.type == PRODUCTION_EDIT_PLAN_ERROR_MAPPING_TRACE_ROUNDTRIP_FAILED for b in report.blockers)


# --- 29: Secret Leak ---


def test_secret_leak_detected_when_api_key_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-super-secret-value-for-test")
    project = _happy_project(tmp_path)
    document = load_staged_edit_plan(project, "000_intro")
    tampered = document.model_copy(update={"plan_generation_notes": ["leaked: sk-super-secret-value-for-test"]})
    package = load_production_edit_plan_staging_package(project)
    section = next(s for s in package.sections if s.staging_section_id == "000_intro")
    _warnings, blockers = validate_staged_section(project, section, tampered)
    assert any(b.type == PRODUCTION_EDIT_PLAN_ERROR_SECRET_LEAK_DETECTED for b in blockers)


# --- 30-33: Report ---


def test_report_pass_in_happy_path(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    report = validate_production_edit_plan_staging(project)
    assert report.status == PRODUCTION_EDIT_PLAN_VALIDATION_STATUS_PASS
    assert report.warnings == []
    assert report.blockers == []


def test_report_warning_when_only_warnings() -> None:
    warning = ProductionEditPlanValidationError(type="SOME_WARNING", severity="WARNING")
    assert classify_production_edit_plan_validation_status([warning], []) == PRODUCTION_EDIT_PLAN_VALIDATION_STATUS_WARNING


def test_report_dedupes_duplicate_errors() -> None:
    error = ProductionEditPlanValidationError(type="X", severity="BLOCKER", message="same")
    from otio_app.services.voiceover_generation.production_edit_plan_validation import _dedupe_errors

    deduped = _dedupe_errors([error, error.model_copy()])
    assert len(deduped) == 1


def test_report_package_hash_matches_package(tmp_path: Path) -> None:
    from otio_app.services.voiceover_generation.llm_trace_service import content_hash_of_model

    project = _happy_project(tmp_path)
    report = validate_production_edit_plan_staging(project)
    package = load_production_edit_plan_staging_package(project)
    assert report.package_hash == content_hash_of_model(package)


# --- 34-36: Staleness + Save/Load ---


def test_report_stale_detects_changed_package(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    report = validate_production_edit_plan_staging(project)
    assert is_production_edit_plan_validation_report_stale(project, report) is False

    package = load_production_edit_plan_staging_package(project)
    changed_package = package.model_copy(update={"warnings": ["manually_added"]})
    save_production_edit_plan_staging_package(project, changed_package)

    assert is_production_edit_plan_validation_report_stale(project, report) is True


def test_report_stale_detects_changed_staged_edit_plan(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    report = validate_production_edit_plan_staging(project)
    assert is_production_edit_plan_validation_report_stale(project, report) is False

    document = load_staged_edit_plan(project, "000_intro")
    changed = document.model_copy(update={"plan_generation_notes": ["manually_edited"]})
    save_staged_edit_plan(project, "000_intro", changed)

    assert is_production_edit_plan_validation_report_stale(project, report) is True


def test_save_and_load_validation_report_roundtrip(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    report = ProductionEditPlanValidationReport(project_id=project.id, status=PRODUCTION_EDIT_PLAN_VALIDATION_STATUS_PASS)
    saved = save_production_edit_plan_validation_report(project, report)
    loaded = load_production_edit_plan_validation_report(project)
    assert loaded is not None
    assert loaded.status == saved.status
    assert get_production_edit_plan_validation_report_path(project.work_dir_path).is_file()


def test_load_validation_report_returns_none_when_missing(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    assert load_production_edit_plan_validation_report(project) is None


# --- 37: Duration-Cache ---


def test_duration_cache_probes_each_video_path_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _happy_project(tmp_path)
    document = load_staged_edit_plan(project, "000_intro")
    base_item = document.timeline_items[0]
    video_path = str(tmp_path / "shared_video.mp4")
    Path(video_path).write_bytes(b"FAKE_VIDEO_BYTES")

    item_a = base_item.model_copy(
        update={"timeline_item_id": "va", "type": "video_shot", "resolved_media_path": video_path}
    )
    item_b = base_item.model_copy(
        update={"timeline_item_id": "vb", "type": "video_shot", "resolved_media_path": video_path}
    )
    tampered = document.model_copy(update={"timeline_items": [item_a, item_b], "shots": document.shots})

    call_count = {"n": 0}

    def _fake_probe(path: Path):
        call_count["n"] += 1
        return 999.0

    monkeypatch.setattr(
        "otio_app.services.voiceover_generation.production_edit_plan_validation.probe_duration_seconds", _fake_probe
    )

    package = load_production_edit_plan_staging_package(project)
    section = next(s for s in package.sections if s.staging_section_id == "000_intro")
    duration_cache: dict[str, float | None] = {}
    validate_staged_section(project, section, tampered, duration_cache=duration_cache)

    assert call_count["n"] == 1


# --- 38-46: Schutz bestehender Pipeline ---


def test_package_json_not_modified_by_validation(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    package_path = get_production_edit_plan_package_path(project.work_dir_path)
    before = package_path.read_text(encoding="utf-8")
    validate_production_edit_plan_staging(project)
    after = package_path.read_text(encoding="utf-8")
    assert before == after


def test_no_files_written_under_edit_plan_dir(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    validate_production_edit_plan_staging(project)
    assert not get_edit_plan_dir(project.work_dir_path).exists()


def test_no_files_written_under_exports_dir(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    validate_production_edit_plan_staging(project)
    assert not get_exports_dir(project.work_dir_path).exists()


def test_no_files_written_under_supplement_dir(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    validate_production_edit_plan_staging(project)
    assert not get_supplement_dir(project.work_dir_path).exists()


def test_existing_production_edit_plan_remains_byte_identical(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    existing_path = get_folder_edit_plan_path(project.work_dir_path, FOLDER_A)
    existing_path.parent.mkdir(parents=True, exist_ok=True)
    existing_content = '{"project_id": "existing", "folder_name": "Grand Canyon", "confirmed": true}'
    existing_path.write_text(existing_content, encoding="utf-8")

    build_and_save_production_edit_plan_staging(project)
    validate_production_edit_plan_staging(project)

    assert existing_path.read_text(encoding="utf-8") == existing_content


def test_no_original_media_modified(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    photo_path = project.project_root_path / FOLDER_A / "photo_a.jpg"
    original = photo_path.read_bytes()
    validate_production_edit_plan_staging(project)
    assert photo_path.read_bytes() == original


def test_no_audio_files_overwritten(tmp_path: Path) -> None:
    project = _happy_project(tmp_path)
    audio_path = project.work_dir_path / "voiceover_generation" / "audio" / "intro.mp3"
    original = audio_path.read_bytes()
    validate_production_edit_plan_staging(project)
    assert audio_path.read_bytes() == original


def test_no_save_edit_plan_or_build_edit_plan_calls_referenced() -> None:
    import otio_app.services.voiceover_generation.production_edit_plan_validation as validation_module

    source = inspect.getsource(validation_module)
    assert not re.search(r"\bsave_edit_plan\b", source)
    assert not re.search(r"\bbuild_edit_plan\b", source)


def test_no_otio_export_referenced() -> None:
    import otio_app.services.voiceover_generation.production_edit_plan_validation as validation_module

    source = inspect.getsource(validation_module)
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


def test_production_validation_module_references_no_forbidden_production_functions() -> None:
    import otio_app.services.voiceover_generation.production_edit_plan_validation as validation_module

    source = inspect.getsource(validation_module)
    for symbol in _FORBIDDEN_SYMBOLS:
        assert not re.search(rf"\b{re.escape(symbol)}\b", source), (
            f"production_edit_plan_validation referenziert verbotenes Symbol '{symbol}'."
        )


def test_with_voiceover_workflow_unaffected() -> None:
    from otio_app.services import edit_plan_builder, otio_exporter

    assert hasattr(edit_plan_builder, "build_edit_plan")
    assert hasattr(edit_plan_builder, "save_edit_plan")
    assert hasattr(otio_exporter, "build_otio_timeline")


# --- Zusätzlich: Unit-Tests für Konvertierungs-Helfer ---


def test_normalize_existing_validator_error_extracts_timeline_item_id() -> None:
    error = normalize_existing_validator_error(
        "edit_v1: duration_sec 1.0s < 3.0s", error_type="X", severity="BLOCKER", scope="timeline"
    )
    assert error.timeline_item_id == "edit_v1"
    assert error.message == "edit_v1: duration_sec 1.0s < 3.0s"


def test_build_production_validation_error_from_existing_error_uses_message() -> None:
    from otio_app.services.edit_plan_validator import PlanValidationError

    plan_error = PlanValidationError(type="SHOT_TOO_SHORT", timeline_item_id="edit_v1", duration_sec=1.0, min_sec=3.0)
    error = build_production_validation_error_from_existing_error(
        plan_error, error_type="SHOT_DURATION_INVALID", severity="BLOCKER", scope="shot"
    )
    assert error.timeline_item_id == "edit_v1"
    assert error.message
