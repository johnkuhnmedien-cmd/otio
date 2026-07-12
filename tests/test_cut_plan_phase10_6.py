"""Phase 10.6: Actual Production EditPlan Promote — Backup, Manifest,
Kollisionsschutz (Service-Ebene).

Ab dieser Phase ist Schreiben nach _otio/edit_plan/ erlaubt, aber
AUSSCHLIESSLICH innerhalb von production_edit_plan_promote_execute.py über
promote_production_edit_plans(). Kein OTIO-Export, kein Render, kein
Lock-Konzept, keine LLM-Planung, keine save_edit_plan()/build_edit_plan()-
Aufrufe, keine automatische Neuplanung, keine automatische Supplement-Suche,
keine Änderung an voice_folder_mapping.json."""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis, EditPlanDocument
from otio_app.defaults import (
    PRODUCTION_EDIT_PLAN_MAPPING_PATCH_ACTION_ALREADY_PRESENT,
    PRODUCTION_EDIT_PLAN_MAPPING_PATCH_ACTION_WOULD_ADD,
    PRODUCTION_EDIT_PLAN_PROMOTE_MANIFEST_STATUS_PROMOTED,
    PRODUCTION_EDIT_PLAN_PROMOTE_RESULT_ACTION_CREATED,
)
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import (
    get_edit_plan_dir,
    get_exports_dir,
    get_folder_edit_plan_path,
    get_folder_inventory_path,
    get_production_edit_plan_promote_manifest_path,
    get_production_edit_plan_voice_folder_mapping_patch_path,
    get_supplement_dir,
    get_voice_folder_mapping_path,
)
from otio_app.services.voice_folder_matcher import save_voice_folder_mapping
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
from otio_app.services.voiceover_generation.llm_trace_service import content_hash_of_model
from otio_app.services.voiceover_generation.models import (
    AlignmentItem,
    ConfirmedFolderPlanItem,
    ConfirmedIntroPlanItem,
    ConfirmedVoiceoverProjectPlan,
    IntroHookVisualBeat,
    SentenceItem,
)
from otio_app.services.voiceover_generation.production_edit_plan_promote_execute import (
    build_voice_folder_mapping_patch,
    can_promote_production_edit_plans,
    is_production_edit_plan_promote_manifest_stale,
    load_production_edit_plan_promote_manifest,
    load_voice_folder_mapping_patch,
    promote_production_edit_plans,
    save_production_edit_plan_promote_manifest,
    save_voice_folder_mapping_patch,
)
from otio_app.services.voiceover_generation.production_edit_plan_promote_readiness import (
    build_production_edit_plan_promote_dry_run_trace,
    build_production_edit_plan_promote_readiness,
    save_production_edit_plan_promote_dry_run_trace,
    save_production_edit_plan_promote_readiness,
)
from otio_app.services.voiceover_generation.production_edit_plan_staging_service import (
    build_and_save_production_edit_plan_staging,
    load_production_edit_plan_staging_package,
    load_staged_edit_plan,
    save_staged_edit_plan,
)
from otio_app.services.voiceover_generation.production_edit_plan_validation import (
    validate_production_edit_plan_staging,
)

FOLDER_A = "Grand Canyon"


def _make_project(tmp_path: Path) -> Project:
    project_root = tmp_path / "USA"
    (project_root / FOLDER_A).mkdir(parents=True, exist_ok=True)
    return Project(
        id="production-edit-plan-promote-execute-project",
        name="Production EditPlan Promote Execute Test",
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
    audio_dir = project.language_work_dir_path / "voiceover_generation" / "audio"
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


def _happy_project_with_dry_run(tmp_path: Path) -> Project:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    validate_production_edit_plan_staging(project)
    readiness = build_production_edit_plan_promote_readiness(project)
    save_production_edit_plan_promote_readiness(project, readiness)
    trace = build_production_edit_plan_promote_dry_run_trace(project, readiness)
    save_production_edit_plan_promote_dry_run_trace(project, trace)
    return project


def _folder_staging_section_id(project: Project) -> str:
    package = load_production_edit_plan_staging_package(project)
    return next(s.staging_section_id for s in package.sections if not s.is_intro)


# --- 1-8: can_promote_production_edit_plans ---


def test_can_promote_false_without_readiness(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    validate_production_edit_plan_staging(project)
    eligible, reasons = can_promote_production_edit_plans(project)
    assert eligible is False
    assert reasons


def test_can_promote_false_with_stale_readiness(tmp_path: Path) -> None:
    project = _happy_project_with_dry_run(tmp_path)
    document = load_staged_edit_plan(project, "000_intro")
    changed = document.model_copy(update={"plan_generation_notes": ["manually_edited"]})
    save_staged_edit_plan(project, "000_intro", changed)

    eligible, reasons = can_promote_production_edit_plans(project)
    assert eligible is False
    assert any("veraltet" in reason for reason in reasons)


def test_can_promote_false_without_validation_pass(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    # Kein validate_production_edit_plan_staging() Aufruf -> kein Report.
    readiness = build_production_edit_plan_promote_readiness(project)
    save_production_edit_plan_promote_readiness(project, readiness)
    trace = build_production_edit_plan_promote_dry_run_trace(project, readiness)
    save_production_edit_plan_promote_dry_run_trace(project, trace)

    eligible, reasons = can_promote_production_edit_plans(project)
    assert eligible is False


def test_can_promote_false_with_readiness_blocked(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    # Kein Validation Report -> Readiness selbst wird BLOCKED.
    readiness = build_production_edit_plan_promote_readiness(project)
    save_production_edit_plan_promote_readiness(project, readiness)
    trace = build_production_edit_plan_promote_dry_run_trace(project, readiness)
    save_production_edit_plan_promote_dry_run_trace(project, trace)
    assert readiness.status == "BLOCKED"

    eligible, reasons = can_promote_production_edit_plans(project)
    assert eligible is False


def test_can_promote_false_with_section_blocked(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    validate_production_edit_plan_staging(project)
    document = load_staged_edit_plan(project, "000_intro")
    tampered = document.model_copy(update={"voiceover": None})
    save_staged_edit_plan(project, "000_intro", tampered)
    package = load_production_edit_plan_staging_package(project)
    intro_pkg_section = next(s for s in package.sections if s.staging_section_id == "000_intro")
    intro_pkg_section.staged_edit_plan_hash = content_hash_of_model(tampered)
    from otio_app.services.voiceover_generation.production_edit_plan_staging_service import (
        save_production_edit_plan_staging_package,
    )

    save_production_edit_plan_staging_package(project, package)

    readiness = build_production_edit_plan_promote_readiness(project)
    save_production_edit_plan_promote_readiness(project, readiness)
    trace = build_production_edit_plan_promote_dry_run_trace(project, readiness)
    save_production_edit_plan_promote_dry_run_trace(project, trace)

    eligible, reasons = can_promote_production_edit_plans(project)
    assert eligible is False


def test_can_promote_false_with_would_overwrite_without_permission(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    existing_path = get_folder_edit_plan_path(project.language_work_dir_path, FOLDER_A)
    existing_path.parent.mkdir(parents=True, exist_ok=True)
    existing_path.write_text('{"project_id": "existing", "folder_name": "Grand Canyon", "confirmed": true}', encoding="utf-8")

    build_and_save_production_edit_plan_staging(project)
    validate_production_edit_plan_staging(project)
    readiness = build_production_edit_plan_promote_readiness(project)
    save_production_edit_plan_promote_readiness(project, readiness)
    trace = build_production_edit_plan_promote_dry_run_trace(project, readiness)
    save_production_edit_plan_promote_dry_run_trace(project, trace)

    eligible, reasons = can_promote_production_edit_plans(project)
    assert eligible is False
    assert any("allow_overwrite_section_ids" in reason for reason in reasons)


def test_can_promote_true_with_would_create_sections(tmp_path: Path) -> None:
    project = _happy_project_with_dry_run(tmp_path)
    eligible, reasons = can_promote_production_edit_plans(project)
    assert eligible is True
    assert reasons == []


def test_can_promote_true_with_would_overwrite_and_explicit_permission(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    existing_path = get_folder_edit_plan_path(project.language_work_dir_path, FOLDER_A)
    existing_path.parent.mkdir(parents=True, exist_ok=True)
    existing_path.write_text('{"project_id": "existing", "folder_name": "Grand Canyon", "confirmed": true}', encoding="utf-8")

    build_and_save_production_edit_plan_staging(project)
    validate_production_edit_plan_staging(project)
    readiness = build_production_edit_plan_promote_readiness(project)
    save_production_edit_plan_promote_readiness(project, readiness)
    trace = build_production_edit_plan_promote_dry_run_trace(project, readiness)
    save_production_edit_plan_promote_dry_run_trace(project, trace)

    folder_section_id = _folder_staging_section_id(project)
    eligible, reasons = can_promote_production_edit_plans(
        project, allow_overwrite_section_ids=[folder_section_id]
    )
    assert eligible is True
    assert reasons == []


# --- 9-13: Schreiben / Backup ---


def test_intro_is_written_to_edit_plan_dir(tmp_path: Path) -> None:
    project = _happy_project_with_dry_run(tmp_path)
    promote_production_edit_plans(project)
    intro_path = get_edit_plan_dir(project.language_work_dir_path) / "Intro.json"
    assert intro_path.is_file()


def test_would_create_writes_new_edit_plan_file(tmp_path: Path) -> None:
    project = _happy_project_with_dry_run(tmp_path)
    manifest = promote_production_edit_plans(project)
    target_path = get_folder_edit_plan_path(project.language_work_dir_path, FOLDER_A)
    assert target_path.is_file()
    assert manifest.created_count == 2  # Intro + folder


def test_would_overwrite_creates_backup_before_write(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    existing_path = get_folder_edit_plan_path(project.language_work_dir_path, FOLDER_A)
    existing_path.parent.mkdir(parents=True, exist_ok=True)
    existing_content = '{"project_id": "existing", "folder_name": "Grand Canyon", "confirmed": true}'
    existing_path.write_text(existing_content, encoding="utf-8")

    build_and_save_production_edit_plan_staging(project)
    validate_production_edit_plan_staging(project)
    readiness = build_production_edit_plan_promote_readiness(project)
    save_production_edit_plan_promote_readiness(project, readiness)
    trace = build_production_edit_plan_promote_dry_run_trace(project, readiness)
    save_production_edit_plan_promote_dry_run_trace(project, trace)
    folder_section_id = _folder_staging_section_id(project)

    manifest = promote_production_edit_plans(project, allow_overwrite_section_ids=[folder_section_id])
    assert manifest.overwritten_count == 1
    folder_result = next(s for s in manifest.sections if s.folder_name == FOLDER_A)
    assert folder_result.backup_path
    assert Path(folder_result.backup_path).is_file()


def test_would_overwrite_only_replaces_with_permission(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    existing_path = get_folder_edit_plan_path(project.language_work_dir_path, FOLDER_A)
    existing_path.parent.mkdir(parents=True, exist_ok=True)
    existing_content = '{"project_id": "existing", "folder_name": "Grand Canyon", "confirmed": true}'
    existing_path.write_text(existing_content, encoding="utf-8")

    build_and_save_production_edit_plan_staging(project)
    validate_production_edit_plan_staging(project)
    readiness = build_production_edit_plan_promote_readiness(project)
    save_production_edit_plan_promote_readiness(project, readiness)
    trace = build_production_edit_plan_promote_dry_run_trace(project, readiness)
    save_production_edit_plan_promote_dry_run_trace(project, trace)

    with pytest.raises(ValueError):
        promote_production_edit_plans(project)

    assert existing_path.read_text(encoding="utf-8") == existing_content


def test_backup_is_byte_identical_to_old_file(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    existing_path = get_folder_edit_plan_path(project.language_work_dir_path, FOLDER_A)
    existing_path.parent.mkdir(parents=True, exist_ok=True)
    existing_content = '{"project_id": "existing", "folder_name": "Grand Canyon", "confirmed": true}'
    existing_path.write_text(existing_content, encoding="utf-8")

    build_and_save_production_edit_plan_staging(project)
    validate_production_edit_plan_staging(project)
    readiness = build_production_edit_plan_promote_readiness(project)
    save_production_edit_plan_promote_readiness(project, readiness)
    trace = build_production_edit_plan_promote_dry_run_trace(project, readiness)
    save_production_edit_plan_promote_dry_run_trace(project, trace)
    folder_section_id = _folder_staging_section_id(project)

    manifest = promote_production_edit_plans(project, allow_overwrite_section_ids=[folder_section_id])
    folder_result = next(s for s in manifest.sections if s.folder_name == FOLDER_A)
    assert Path(folder_result.backup_path).read_text(encoding="utf-8") == existing_content


# --- 14-19: Promotetes Dokument ---


def test_promoted_document_confirmed_true(tmp_path: Path) -> None:
    project = _happy_project_with_dry_run(tmp_path)
    promote_production_edit_plans(project)
    target_path = get_folder_edit_plan_path(project.language_work_dir_path, FOLDER_A)
    document = EditPlanDocument.model_validate_json(target_path.read_text(encoding="utf-8"))
    assert document.confirmed is True


def test_promoted_document_contains_voiceover(tmp_path: Path) -> None:
    project = _happy_project_with_dry_run(tmp_path)
    promote_production_edit_plans(project)
    target_path = get_folder_edit_plan_path(project.language_work_dir_path, FOLDER_A)
    document = EditPlanDocument.model_validate_json(target_path.read_text(encoding="utf-8"))
    assert document.voiceover is not None


def test_promoted_document_contains_timeline_items(tmp_path: Path) -> None:
    project = _happy_project_with_dry_run(tmp_path)
    promote_production_edit_plans(project)
    target_path = get_folder_edit_plan_path(project.language_work_dir_path, FOLDER_A)
    document = EditPlanDocument.model_validate_json(target_path.read_text(encoding="utf-8"))
    assert document.timeline_items


def test_promoted_document_contains_shots(tmp_path: Path) -> None:
    project = _happy_project_with_dry_run(tmp_path)
    promote_production_edit_plans(project)
    target_path = get_folder_edit_plan_path(project.language_work_dir_path, FOLDER_A)
    document = EditPlanDocument.model_validate_json(target_path.read_text(encoding="utf-8"))
    assert document.shots


def test_promoted_document_has_no_voiceover_audio_item(tmp_path: Path) -> None:
    project = _happy_project_with_dry_run(tmp_path)
    promote_production_edit_plans(project)
    target_path = get_folder_edit_plan_path(project.language_work_dir_path, FOLDER_A)
    document = EditPlanDocument.model_validate_json(target_path.read_text(encoding="utf-8"))
    assert all(item.type != "voiceover_audio" for item in document.timeline_items)


def test_secret_leak_blocks_promote(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    validate_production_edit_plan_staging(project)
    document = load_staged_edit_plan(project, "000_intro")
    tampered = document.model_copy(update={"plan_generation_notes": ["leaked: sk-super-secret-value-for-test"]})
    save_staged_edit_plan(project, "000_intro", tampered)
    package = load_production_edit_plan_staging_package(project)
    intro_pkg_section = next(s for s in package.sections if s.staging_section_id == "000_intro")
    intro_pkg_section.staged_edit_plan_hash = content_hash_of_model(tampered)
    from otio_app.services.voiceover_generation.production_edit_plan_staging_service import (
        save_production_edit_plan_staging_package,
    )

    save_production_edit_plan_staging_package(project, package)

    readiness = build_production_edit_plan_promote_readiness(project)
    save_production_edit_plan_promote_readiness(project, readiness)
    trace = build_production_edit_plan_promote_dry_run_trace(project, readiness)
    save_production_edit_plan_promote_dry_run_trace(project, trace)

    with pytest.raises(ValueError):
        promote_production_edit_plans(project)
    assert not get_folder_edit_plan_path(project.language_work_dir_path, FOLDER_A).is_file()


# --- 20: Atomic Write ---


def test_atomic_write_leaves_no_temp_files_on_success(tmp_path: Path) -> None:
    project = _happy_project_with_dry_run(tmp_path)
    promote_production_edit_plans(project)
    edit_plan_dir = get_edit_plan_dir(project.language_work_dir_path)
    tmp_files = list(edit_plan_dir.glob("*.tmp"))
    assert tmp_files == []


# --- 21-26: Manifest ---


def test_promote_manifest_is_written(tmp_path: Path) -> None:
    project = _happy_project_with_dry_run(tmp_path)
    manifest = promote_production_edit_plans(project)
    save_production_edit_plan_promote_manifest(project, manifest)
    assert get_production_edit_plan_promote_manifest_path(project.language_work_dir_path).is_file()


def test_manifest_contains_source_readiness_hash(tmp_path: Path) -> None:
    project = _happy_project_with_dry_run(tmp_path)
    manifest = promote_production_edit_plans(project)
    assert manifest.source_readiness_hash


def test_manifest_contains_source_package_hash(tmp_path: Path) -> None:
    project = _happy_project_with_dry_run(tmp_path)
    manifest = promote_production_edit_plans(project)
    assert manifest.source_package_hash


def test_manifest_contains_source_validation_report_hash(tmp_path: Path) -> None:
    project = _happy_project_with_dry_run(tmp_path)
    manifest = promote_production_edit_plans(project)
    assert manifest.source_validation_report_hash


def test_manifest_counts_are_correct(tmp_path: Path) -> None:
    project = _happy_project_with_dry_run(tmp_path)
    manifest = promote_production_edit_plans(project)
    assert manifest.created_count == 2  # Intro + folder
    assert manifest.overwritten_count == 0
    assert manifest.skipped_intro_count == 0
    assert manifest.status == PRODUCTION_EDIT_PLAN_PROMOTE_MANIFEST_STATUS_PROMOTED
    intro_result = next(s for s in manifest.sections if s.is_intro)
    assert intro_result.promote_action == PRODUCTION_EDIT_PLAN_PROMOTE_RESULT_ACTION_CREATED
    folder_result = next(s for s in manifest.sections if not s.is_intro)
    assert folder_result.promote_action == PRODUCTION_EDIT_PLAN_PROMOTE_RESULT_ACTION_CREATED


def test_manifest_stale_detects_changed_readiness(tmp_path: Path) -> None:
    project = _happy_project_with_dry_run(tmp_path)
    manifest = promote_production_edit_plans(project)
    save_production_edit_plan_promote_manifest(project, manifest)
    assert is_production_edit_plan_promote_manifest_stale(project, manifest) is False

    readiness = build_production_edit_plan_promote_readiness(project)
    changed_readiness = readiness.model_copy(update={"warnings": ["manually_added"]})
    save_production_edit_plan_promote_readiness(project, changed_readiness)

    assert is_production_edit_plan_promote_manifest_stale(project, manifest) is True


# --- 27-32: Voice Folder Mapping Patch ---


def test_mapping_patch_is_written(tmp_path: Path) -> None:
    project = _happy_project_with_dry_run(tmp_path)
    manifest = promote_production_edit_plans(project)
    patch = build_voice_folder_mapping_patch(project, manifest)
    save_voice_folder_mapping_patch(project, patch)
    assert get_production_edit_plan_voice_folder_mapping_patch_path(project.language_work_dir_path).is_file()


def test_mapping_patch_contains_non_intro_folder(tmp_path: Path) -> None:
    project = _happy_project_with_dry_run(tmp_path)
    manifest = promote_production_edit_plans(project)
    patch = build_voice_folder_mapping_patch(project, manifest)
    assert any(entry.folder_name == FOLDER_A for entry in patch.entries)


def test_mapping_patch_includes_intro_as_folder(tmp_path: Path) -> None:
    project = _happy_project_with_dry_run(tmp_path)
    manifest = promote_production_edit_plans(project)
    patch = build_voice_folder_mapping_patch(project, manifest)
    assert any(entry.folder_name == "Intro" for entry in patch.entries)
    assert any(entry.folder_name == FOLDER_A for entry in patch.entries)
    assert len(patch.entries) == 2
    assert patch.entries[0].folder_name == "Intro"


def test_mapping_patch_action_would_add_when_not_in_mapping(tmp_path: Path) -> None:
    project = _happy_project_with_dry_run(tmp_path)
    manifest = promote_production_edit_plans(project)
    patch = build_voice_folder_mapping_patch(project, manifest)
    entry = next(e for e in patch.entries if e.folder_name == FOLDER_A)
    assert entry.action == PRODUCTION_EDIT_PLAN_MAPPING_PATCH_ACTION_WOULD_ADD


def test_mapping_patch_action_already_present_when_in_mapping(tmp_path: Path) -> None:
    from otio_app.analysis_models import VoiceFolderMappingEntry

    project = _happy_project_with_dry_run(tmp_path)
    manifest = promote_production_edit_plans(project)
    target_path = get_folder_edit_plan_path(project.language_work_dir_path, FOLDER_A)
    promoted_doc = EditPlanDocument.model_validate_json(target_path.read_text(encoding="utf-8"))
    save_voice_folder_mapping(
        project,
        [
            VoiceFolderMappingEntry(
                voice_file=promoted_doc.voiceover.path, folder=FOLDER_A, confirmed=True
            )
        ],
        confirmed=True,
    )

    patch = build_voice_folder_mapping_patch(project, manifest)
    entry = next(e for e in patch.entries if e.folder_name == FOLDER_A)
    assert entry.action == PRODUCTION_EDIT_PLAN_MAPPING_PATCH_ACTION_ALREADY_PRESENT


def test_voice_folder_mapping_file_remains_byte_identical(tmp_path: Path) -> None:
    project = _happy_project_with_dry_run(tmp_path)
    mapping_path = get_voice_folder_mapping_path(project.project_root_path)
    mapping_path.parent.mkdir(parents=True, exist_ok=True)
    existing_content = '{"project_id": "x", "confirmed": false, "entries": []}'
    mapping_path.write_text(existing_content, encoding="utf-8")

    manifest = promote_production_edit_plans(project)
    patch = build_voice_folder_mapping_patch(project, manifest)
    save_voice_folder_mapping_patch(project, patch)

    assert mapping_path.read_text(encoding="utf-8") == existing_content


def test_load_promote_manifest_returns_none_when_missing(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    assert load_production_edit_plan_promote_manifest(project) is None


def test_load_voice_folder_mapping_patch_returns_none_when_missing(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    assert load_voice_folder_mapping_patch(project) is None


# --- 40-45: keine Seiteneffekte ---


def test_no_files_written_under_exports_dir(tmp_path: Path) -> None:
    project = _happy_project_with_dry_run(tmp_path)
    promote_production_edit_plans(project)
    assert not get_exports_dir(project.language_work_dir_path).exists()


def test_no_files_written_under_supplement_dir(tmp_path: Path) -> None:
    project = _happy_project_with_dry_run(tmp_path)
    promote_production_edit_plans(project)
    assert not get_supplement_dir(project.language_work_dir_path).exists()


def test_no_original_media_modified(tmp_path: Path) -> None:
    project = _happy_project_with_dry_run(tmp_path)
    photo_path = project.project_root_path / FOLDER_A / "photo_a.jpg"
    original = photo_path.read_bytes()
    promote_production_edit_plans(project)
    assert photo_path.read_bytes() == original


def test_no_audio_files_overwritten(tmp_path: Path) -> None:
    project = _happy_project_with_dry_run(tmp_path)
    audio_path = project.language_work_dir_path / "voiceover_generation" / "audio" / "intro.mp3"
    original = audio_path.read_bytes()
    promote_production_edit_plans(project)
    assert audio_path.read_bytes() == original


def test_no_save_edit_plan_or_build_edit_plan_calls_referenced() -> None:
    import otio_app.services.voiceover_generation.production_edit_plan_promote_execute as promote_module

    source = inspect.getsource(promote_module)
    assert not re.search(r"\bsave_edit_plan\b", source)
    assert not re.search(r"\bbuild_edit_plan\b", source)


def test_no_otio_export_referenced() -> None:
    import otio_app.services.voiceover_generation.production_edit_plan_promote_execute as promote_module

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


def test_promote_execute_modules_reference_no_forbidden_production_functions() -> None:
    import otio_app.services.voiceover_generation.production_edit_plan_promote_execute as promote_module
    import otio_app.services.voiceover_generation.production_edit_plan_promote_execute_models as models_module

    for module in (promote_module, models_module):
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


def test_promote_writes_under_edit_plan_dir_including_intro(tmp_path: Path) -> None:
    """Nach Phase 10.6 ist Schreiben nach _otio/edit_plan/ erlaubt —
    inkl. Intro als Ordner „Intro“, ausschließlich über
    promote_production_edit_plans()."""
    project = _happy_project_with_dry_run(tmp_path)
    promote_production_edit_plans(project)
    edit_plan_dir = get_edit_plan_dir(project.language_work_dir_path)
    written_files = sorted(p.name for p in edit_plan_dir.glob("*.json"))
    assert written_files == ["Grand_Canyon.json", "Intro.json"]


