"""Phase 10.7: Voice Folder Mapping Merge — explizit bestätigte, selektive
Übernahme des Vorbereitungs-Patches (Phase 10.6) in die echte
`voice_folder_mapping.json` (Service-Ebene).

Dies ist die EINZIGE Stelle im gesamten "Projekt ohne Voice-Over"-Workflow,
die `voice_folder_mapping.json` tatsächlich verändern darf. Kein
OTIO-Export, kein Render, kein Lock-Konzept, keine
save_edit_plan()/build_edit_plan()-Aufrufe, kein Schreiben nach
_otio/edit_plan/, _otio/exports/ oder _otio/supplement/, keine
Originalmedien/Audio-Dateien werden verändert."""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis, EditPlanDocument, VoiceFolderMappingEntry
from otio_app.defaults import (
    VOICE_FOLDER_MAPPING_MERGE_ACTION_SKIPPED_ALREADY_PRESENT,
    VOICE_FOLDER_MAPPING_MERGE_ACTION_SKIPPED_BY_USER,
    VOICE_FOLDER_MAPPING_MERGE_ACTION_UPDATED,
    VOICE_FOLDER_MAPPING_MERGE_MANIFEST_STATUS_MERGED,
    VOICE_FOLDER_MAPPING_MERGE_RESOLUTION_APPLY,
    VOICE_FOLDER_MAPPING_MERGE_RESOLUTION_SKIP,
)
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import (
    get_edit_plan_dir,
    get_exports_dir,
    get_folder_edit_plan_path,
    get_folder_inventory_path,
    get_production_edit_plan_promote_manifest_path,
    get_supplement_dir,
    get_voice_folder_mapping_merge_manifest_path,
)
from otio_app.services.voice_folder_matcher import load_voice_folder_mapping, save_voice_folder_mapping
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
)
from otio_app.services.voiceover_generation.production_edit_plan_validation import (
    validate_production_edit_plan_staging,
)
from otio_app.services.voiceover_generation.production_edit_plan_voice_folder_mapping_merge import (
    can_merge_voice_folder_mapping,
    is_voice_folder_mapping_merge_manifest_stale,
    load_voice_folder_mapping_merge_manifest,
    merge_voice_folder_mapping,
    save_voice_folder_mapping_merge_manifest,
)

FOLDER_A = "Grand Canyon"


def _make_project(tmp_path: Path) -> Project:
    project_root = tmp_path / "USA"
    (project_root / FOLDER_A).mkdir(parents=True, exist_ok=True)
    return Project(
        id="voice-folder-mapping-merge-project",
        name="Voice Folder Mapping Merge Test",
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


def _promoted_project_with_patch(tmp_path: Path):
    """Baut die vollständige Kette bis inkl. Promote + Mapping Patch. Gibt
    (project, manifest, patch) zurück."""
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    validate_production_edit_plan_staging(project)
    readiness = build_production_edit_plan_promote_readiness(project)
    save_production_edit_plan_promote_readiness(project, readiness)
    dry_run_trace = build_production_edit_plan_promote_dry_run_trace(project, readiness)
    save_production_edit_plan_promote_dry_run_trace(project, dry_run_trace)

    manifest = promote_production_edit_plans(project)
    manifest = save_production_edit_plan_promote_manifest(project, manifest)
    patch = build_voice_folder_mapping_patch(project, manifest)
    patch = save_voice_folder_mapping_patch(project, patch)
    return project, manifest, patch


# --- 1-7: can_merge_voice_folder_mapping ---


def test_can_merge_false_without_patch(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    eligible, reasons = can_merge_voice_folder_mapping(project)
    assert eligible is False
    assert reasons


def test_can_merge_false_without_promote_manifest(tmp_path: Path) -> None:
    project, manifest, patch = _promoted_project_with_patch(tmp_path)
    get_production_edit_plan_promote_manifest_path(project.language_work_dir_path).unlink()
    eligible, reasons = can_merge_voice_folder_mapping(project)
    assert eligible is False


def test_can_merge_false_with_mismatched_promote_run_id(tmp_path: Path) -> None:
    project, manifest, patch = _promoted_project_with_patch(tmp_path)
    tampered_patch = patch.model_copy(update={"promote_run_id": "some_other_run"})
    save_voice_folder_mapping_patch(project, tampered_patch)
    eligible, reasons = can_merge_voice_folder_mapping(project)
    assert eligible is False
    assert any("anderen Promote-Lauf" in reason for reason in reasons)


def test_can_merge_false_with_unresolved_conflict(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    save_voice_folder_mapping(
        project,
        [VoiceFolderMappingEntry(voice_file="/old/voice.mp3", folder=FOLDER_A, confirmed=True)],
        confirmed=True,
    )
    build_and_save_production_edit_plan_staging(project)
    validate_production_edit_plan_staging(project)
    readiness = build_production_edit_plan_promote_readiness(project)
    save_production_edit_plan_promote_readiness(project, readiness)
    dry_run_trace = build_production_edit_plan_promote_dry_run_trace(project, readiness)
    save_production_edit_plan_promote_dry_run_trace(project, dry_run_trace)
    manifest = promote_production_edit_plans(project)
    manifest = save_production_edit_plan_promote_manifest(project, manifest)
    patch = build_voice_folder_mapping_patch(project, manifest)
    save_voice_folder_mapping_patch(project, patch)

    entry = next(e for e in patch.entries if e.folder_name == FOLDER_A)
    assert entry.action == "NEEDS_REVIEW"

    eligible, reasons = can_merge_voice_folder_mapping(project)
    assert eligible is False
    assert any(FOLDER_A in reason for reason in reasons)


def test_can_merge_true_with_conflict_resolved_apply(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    save_voice_folder_mapping(
        project,
        [VoiceFolderMappingEntry(voice_file="/old/voice.mp3", folder=FOLDER_A, confirmed=True)],
        confirmed=True,
    )
    build_and_save_production_edit_plan_staging(project)
    validate_production_edit_plan_staging(project)
    readiness = build_production_edit_plan_promote_readiness(project)
    save_production_edit_plan_promote_readiness(project, readiness)
    dry_run_trace = build_production_edit_plan_promote_dry_run_trace(project, readiness)
    save_production_edit_plan_promote_dry_run_trace(project, dry_run_trace)
    manifest = promote_production_edit_plans(project)
    manifest = save_production_edit_plan_promote_manifest(project, manifest)
    patch = build_voice_folder_mapping_patch(project, manifest)
    save_voice_folder_mapping_patch(project, patch)

    eligible, reasons = can_merge_voice_folder_mapping(
        project, folder_resolutions={FOLDER_A: VOICE_FOLDER_MAPPING_MERGE_RESOLUTION_APPLY}
    )
    assert eligible is True
    assert reasons == []


def test_can_merge_true_with_conflict_resolved_skip(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    save_voice_folder_mapping(
        project,
        [VoiceFolderMappingEntry(voice_file="/old/voice.mp3", folder=FOLDER_A, confirmed=True)],
        confirmed=True,
    )
    build_and_save_production_edit_plan_staging(project)
    validate_production_edit_plan_staging(project)
    readiness = build_production_edit_plan_promote_readiness(project)
    save_production_edit_plan_promote_readiness(project, readiness)
    dry_run_trace = build_production_edit_plan_promote_dry_run_trace(project, readiness)
    save_production_edit_plan_promote_dry_run_trace(project, dry_run_trace)
    manifest = promote_production_edit_plans(project)
    manifest = save_production_edit_plan_promote_manifest(project, manifest)
    patch = build_voice_folder_mapping_patch(project, manifest)
    save_voice_folder_mapping_patch(project, patch)

    eligible, reasons = can_merge_voice_folder_mapping(
        project, folder_resolutions={FOLDER_A: VOICE_FOLDER_MAPPING_MERGE_RESOLUTION_SKIP}
    )
    assert eligible is True


def test_can_merge_true_without_any_conflicts(tmp_path: Path) -> None:
    project, manifest, patch = _promoted_project_with_patch(tmp_path)
    assert not any(e.action == "NEEDS_REVIEW" for e in patch.entries)
    eligible, reasons = can_merge_voice_folder_mapping(project)
    assert eligible is True
    assert reasons == []


# --- 8-19: merge_voice_folder_mapping Verhalten ---


def test_merge_adds_new_entry_for_would_add_folder(tmp_path: Path) -> None:
    project, manifest, patch = _promoted_project_with_patch(tmp_path)
    merge_manifest = merge_voice_folder_mapping(project)
    mapping = load_voice_folder_mapping(project.voice_folder_mapping_path)
    assert any(entry.folder == FOLDER_A for entry in mapping.entries)
    assert any(entry.folder == "Intro" for entry in mapping.entries)
    assert merge_manifest.added_count == 2


def test_merge_marks_entries_confirmed_only_when_requested(tmp_path: Path) -> None:
    project, manifest, patch = _promoted_project_with_patch(tmp_path)
    merge_voice_folder_mapping(project, mark_entries_confirmed=False)
    mapping = load_voice_folder_mapping(project.voice_folder_mapping_path)
    entry = next(e for e in mapping.entries if e.folder == FOLDER_A)
    assert entry.confirmed is False


def test_merge_marks_entries_confirmed_when_requested(tmp_path: Path) -> None:
    project, manifest, patch = _promoted_project_with_patch(tmp_path)
    merge_voice_folder_mapping(project, mark_entries_confirmed=True)
    mapping = load_voice_folder_mapping(project.voice_folder_mapping_path)
    entry = next(e for e in mapping.entries if e.folder == FOLDER_A)
    assert entry.confirmed is True


def test_merge_defaults_new_document_confirmed_to_false(tmp_path: Path) -> None:
    project, manifest, patch = _promoted_project_with_patch(tmp_path)
    merge_voice_folder_mapping(project)
    mapping = load_voice_folder_mapping(project.voice_folder_mapping_path)
    assert mapping.confirmed is False


def test_merge_preserves_existing_document_confirmed_true(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    save_voice_folder_mapping(project, [], confirmed=True)
    build_and_save_production_edit_plan_staging(project)
    validate_production_edit_plan_staging(project)
    readiness = build_production_edit_plan_promote_readiness(project)
    save_production_edit_plan_promote_readiness(project, readiness)
    dry_run_trace = build_production_edit_plan_promote_dry_run_trace(project, readiness)
    save_production_edit_plan_promote_dry_run_trace(project, dry_run_trace)
    manifest = promote_production_edit_plans(project)
    manifest = save_production_edit_plan_promote_manifest(project, manifest)
    patch = build_voice_folder_mapping_patch(project, manifest)
    save_voice_folder_mapping_patch(project, patch)

    merge_voice_folder_mapping(project)
    mapping = load_voice_folder_mapping(project.voice_folder_mapping_path)
    assert mapping.confirmed is True


def test_merge_creates_backup_when_mapping_already_existed(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    save_voice_folder_mapping(project, [], confirmed=False)
    build_and_save_production_edit_plan_staging(project)
    validate_production_edit_plan_staging(project)
    readiness = build_production_edit_plan_promote_readiness(project)
    save_production_edit_plan_promote_readiness(project, readiness)
    dry_run_trace = build_production_edit_plan_promote_dry_run_trace(project, readiness)
    save_production_edit_plan_promote_dry_run_trace(project, dry_run_trace)
    manifest = promote_production_edit_plans(project)
    manifest = save_production_edit_plan_promote_manifest(project, manifest)
    patch = build_voice_folder_mapping_patch(project, manifest)
    save_voice_folder_mapping_patch(project, patch)

    merge_manifest = merge_voice_folder_mapping(project)
    assert merge_manifest.backup_path
    assert Path(merge_manifest.backup_path).is_file()


def test_merge_no_backup_when_mapping_did_not_exist(tmp_path: Path) -> None:
    project, manifest, patch = _promoted_project_with_patch(tmp_path)
    assert not project.voice_folder_mapping_path.is_file()
    merge_manifest = merge_voice_folder_mapping(project)
    assert merge_manifest.backup_path == ""


def test_merge_backup_is_byte_identical_to_old_file(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    save_voice_folder_mapping(project, [], confirmed=False)
    existing_content = project.voice_folder_mapping_path.read_text(encoding="utf-8")
    build_and_save_production_edit_plan_staging(project)
    validate_production_edit_plan_staging(project)
    readiness = build_production_edit_plan_promote_readiness(project)
    save_production_edit_plan_promote_readiness(project, readiness)
    dry_run_trace = build_production_edit_plan_promote_dry_run_trace(project, readiness)
    save_production_edit_plan_promote_dry_run_trace(project, dry_run_trace)
    manifest = promote_production_edit_plans(project)
    manifest = save_production_edit_plan_promote_manifest(project, manifest)
    patch = build_voice_folder_mapping_patch(project, manifest)
    save_voice_folder_mapping_patch(project, patch)

    merge_manifest = merge_voice_folder_mapping(project)
    assert Path(merge_manifest.backup_path).read_text(encoding="utf-8") == existing_content


def test_would_add_with_skip_resolution_does_not_add_entry(tmp_path: Path) -> None:
    project, manifest, patch = _promoted_project_with_patch(tmp_path)
    merge_manifest = merge_voice_folder_mapping(
        project, folder_resolutions={FOLDER_A: VOICE_FOLDER_MAPPING_MERGE_RESOLUTION_SKIP}
    )
    mapping = load_voice_folder_mapping(project.voice_folder_mapping_path)
    assert not any(entry.folder == FOLDER_A for entry in mapping.entries)
    result = next(e for e in merge_manifest.entries if e.folder_name == FOLDER_A)
    assert result.action == VOICE_FOLDER_MAPPING_MERGE_ACTION_SKIPPED_BY_USER


def test_already_present_entries_are_not_modified(tmp_path: Path) -> None:
    project, manifest, patch = _promoted_project_with_patch(tmp_path)
    target_path = get_folder_edit_plan_path(project.language_work_dir_path, FOLDER_A)
    promoted_doc = EditPlanDocument.model_validate_json(target_path.read_text(encoding="utf-8"))
    save_voice_folder_mapping(
        project,
        [VoiceFolderMappingEntry(voice_file=promoted_doc.voiceover.path, folder=FOLDER_A, confirmed=True)],
        confirmed=True,
    )
    fresh_patch = build_voice_folder_mapping_patch(project, manifest)
    save_voice_folder_mapping_patch(project, fresh_patch)
    entry = next(e for e in fresh_patch.entries if e.folder_name == FOLDER_A)
    assert entry.action == "ALREADY_PRESENT"

    merge_manifest = merge_voice_folder_mapping(project)
    result = next(e for e in merge_manifest.entries if e.folder_name == FOLDER_A)
    assert result.action == VOICE_FOLDER_MAPPING_MERGE_ACTION_SKIPPED_ALREADY_PRESENT
    assert result.applied is False


def test_needs_review_apply_replaces_existing_entry(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    save_voice_folder_mapping(
        project,
        [VoiceFolderMappingEntry(voice_file="/old/voice.mp3", folder=FOLDER_A, confirmed=True)],
        confirmed=True,
    )
    build_and_save_production_edit_plan_staging(project)
    validate_production_edit_plan_staging(project)
    readiness = build_production_edit_plan_promote_readiness(project)
    save_production_edit_plan_promote_readiness(project, readiness)
    dry_run_trace = build_production_edit_plan_promote_dry_run_trace(project, readiness)
    save_production_edit_plan_promote_dry_run_trace(project, dry_run_trace)
    manifest = promote_production_edit_plans(project)
    manifest = save_production_edit_plan_promote_manifest(project, manifest)
    patch = build_voice_folder_mapping_patch(project, manifest)
    save_voice_folder_mapping_patch(project, patch)

    merge_manifest = merge_voice_folder_mapping(
        project, folder_resolutions={FOLDER_A: VOICE_FOLDER_MAPPING_MERGE_RESOLUTION_APPLY}
    )
    mapping = load_voice_folder_mapping(project.voice_folder_mapping_path)
    entries = [e for e in mapping.entries if e.folder == FOLDER_A]
    assert len(entries) == 1
    assert entries[0].voice_file != "/old/voice.mp3"
    result = next(e for e in merge_manifest.entries if e.folder_name == FOLDER_A)
    assert result.action == VOICE_FOLDER_MAPPING_MERGE_ACTION_UPDATED
    assert result.previous_voice_file == "/old/voice.mp3"


def test_needs_review_skip_keeps_existing_entry(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    save_voice_folder_mapping(
        project,
        [VoiceFolderMappingEntry(voice_file="/old/voice.mp3", folder=FOLDER_A, confirmed=True)],
        confirmed=True,
    )
    build_and_save_production_edit_plan_staging(project)
    validate_production_edit_plan_staging(project)
    readiness = build_production_edit_plan_promote_readiness(project)
    save_production_edit_plan_promote_readiness(project, readiness)
    dry_run_trace = build_production_edit_plan_promote_dry_run_trace(project, readiness)
    save_production_edit_plan_promote_dry_run_trace(project, dry_run_trace)
    manifest = promote_production_edit_plans(project)
    manifest = save_production_edit_plan_promote_manifest(project, manifest)
    patch = build_voice_folder_mapping_patch(project, manifest)
    save_voice_folder_mapping_patch(project, patch)

    merge_voice_folder_mapping(project, folder_resolutions={FOLDER_A: VOICE_FOLDER_MAPPING_MERGE_RESOLUTION_SKIP})
    mapping = load_voice_folder_mapping(project.voice_folder_mapping_path)
    entries = [e for e in mapping.entries if e.folder == FOLDER_A]
    assert len(entries) == 1
    assert entries[0].voice_file == "/old/voice.mp3"


def test_manifest_counts_are_correct(tmp_path: Path) -> None:
    project, manifest, patch = _promoted_project_with_patch(tmp_path)
    merge_manifest = merge_voice_folder_mapping(project)
    assert merge_manifest.added_count == 2
    assert merge_manifest.updated_count == 0
    assert merge_manifest.skipped_count == 0
    assert merge_manifest.status == VOICE_FOLDER_MAPPING_MERGE_MANIFEST_STATUS_MERGED


def test_manifest_contains_source_hashes(tmp_path: Path) -> None:
    project, manifest, patch = _promoted_project_with_patch(tmp_path)
    merge_manifest = merge_voice_folder_mapping(project)
    assert merge_manifest.source_patch_hash == content_hash_of_model(patch)
    assert merge_manifest.source_promote_manifest_hash == content_hash_of_model(manifest)


def test_unrelated_existing_entries_remain_untouched(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    save_voice_folder_mapping(
        project,
        [VoiceFolderMappingEntry(voice_file="/other/voice.mp3", folder="Other Folder", confirmed=True)],
        confirmed=True,
    )
    build_and_save_production_edit_plan_staging(project)
    validate_production_edit_plan_staging(project)
    readiness = build_production_edit_plan_promote_readiness(project)
    save_production_edit_plan_promote_readiness(project, readiness)
    dry_run_trace = build_production_edit_plan_promote_dry_run_trace(project, readiness)
    save_production_edit_plan_promote_dry_run_trace(project, dry_run_trace)
    manifest = promote_production_edit_plans(project)
    manifest = save_production_edit_plan_promote_manifest(project, manifest)
    patch = build_voice_folder_mapping_patch(project, manifest)
    save_voice_folder_mapping_patch(project, patch)

    merge_voice_folder_mapping(project)
    mapping = load_voice_folder_mapping(project.voice_folder_mapping_path)
    other_entry = next(e for e in mapping.entries if e.folder == "Other Folder")
    assert other_entry.voice_file == "/other/voice.mp3"
    assert other_entry.confirmed is True


def test_intro_appears_as_first_mapping_entry(tmp_path: Path) -> None:
    project, manifest, patch = _promoted_project_with_patch(tmp_path)
    merge_voice_folder_mapping(project)
    mapping = load_voice_folder_mapping(project.voice_folder_mapping_path)
    intro_entries = [entry for entry in mapping.entries if entry.folder == "Intro"]
    assert len(intro_entries) == 1
    assert mapping.entries[0].folder == "Intro"


# --- 20-24: Manifest speichern / laden / stale ---


def test_save_and_load_merge_manifest_roundtrip(tmp_path: Path) -> None:
    project, manifest, patch = _promoted_project_with_patch(tmp_path)
    merge_manifest = merge_voice_folder_mapping(project)
    saved = save_voice_folder_mapping_merge_manifest(project, merge_manifest)
    loaded = load_voice_folder_mapping_merge_manifest(project)
    assert loaded is not None
    assert loaded.merge_run_id == saved.merge_run_id
    assert get_voice_folder_mapping_merge_manifest_path(project.language_work_dir_path).is_file()


def test_load_merge_manifest_returns_none_when_missing(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    assert load_voice_folder_mapping_merge_manifest(project) is None


def test_merge_manifest_stale_detects_changed_patch(tmp_path: Path) -> None:
    project, manifest, patch = _promoted_project_with_patch(tmp_path)
    merge_manifest = merge_voice_folder_mapping(project)
    save_voice_folder_mapping_merge_manifest(project, merge_manifest)
    assert is_voice_folder_mapping_merge_manifest_stale(project, merge_manifest) is False

    changed_patch = patch.model_copy(update={"warnings": ["manually_added"]})
    save_voice_folder_mapping_patch(project, changed_patch)

    assert is_voice_folder_mapping_merge_manifest_stale(project, merge_manifest) is True


def test_merge_manifest_stale_detects_external_mapping_change(tmp_path: Path) -> None:
    project, manifest, patch = _promoted_project_with_patch(tmp_path)
    merge_manifest = merge_voice_folder_mapping(project)
    save_voice_folder_mapping_merge_manifest(project, merge_manifest)
    assert is_voice_folder_mapping_merge_manifest_stale(project, merge_manifest) is False

    current_mapping = load_voice_folder_mapping(project.voice_folder_mapping_path)
    save_voice_folder_mapping(project, list(current_mapping.entries), confirmed=True)

    assert is_voice_folder_mapping_merge_manifest_stale(project, merge_manifest) is True


# --- 25: Atomic Write ---


def test_atomic_write_leaves_no_temp_files(tmp_path: Path) -> None:
    project, manifest, patch = _promoted_project_with_patch(tmp_path)
    merge_voice_folder_mapping(project)
    tmp_files = list(project.project_root_path.glob("*.tmp"))
    assert tmp_files == []


# --- 26-28: keine Seiteneffekte außerhalb ---


def test_no_files_written_under_edit_plan_dir(tmp_path: Path) -> None:
    project, manifest, patch = _promoted_project_with_patch(tmp_path)
    before = list(get_edit_plan_dir(project.language_work_dir_path).glob("*.json"))
    merge_voice_folder_mapping(project)
    after = list(get_edit_plan_dir(project.language_work_dir_path).glob("*.json"))
    assert before == after


def test_no_files_written_under_exports_dir(tmp_path: Path) -> None:
    project, manifest, patch = _promoted_project_with_patch(tmp_path)
    merge_voice_folder_mapping(project)
    assert not get_exports_dir(project.language_work_dir_path).exists()


def test_no_files_written_under_supplement_dir(tmp_path: Path) -> None:
    project, manifest, patch = _promoted_project_with_patch(tmp_path)
    merge_voice_folder_mapping(project)
    assert not get_supplement_dir(project.language_work_dir_path).exists()


def test_no_original_media_modified(tmp_path: Path) -> None:
    project, manifest, patch = _promoted_project_with_patch(tmp_path)
    photo_path = project.project_root_path / FOLDER_A / "photo_a.jpg"
    original = photo_path.read_bytes()
    merge_voice_folder_mapping(project)
    assert photo_path.read_bytes() == original


def test_no_audio_files_overwritten(tmp_path: Path) -> None:
    project, manifest, patch = _promoted_project_with_patch(tmp_path)
    audio_path = project.language_work_dir_path / "voiceover_generation" / "audio" / "intro.mp3"
    original = audio_path.read_bytes()
    merge_voice_folder_mapping(project)
    assert audio_path.read_bytes() == original


def test_merge_blocked_writes_nothing(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    save_voice_folder_mapping(
        project,
        [VoiceFolderMappingEntry(voice_file="/old/voice.mp3", folder=FOLDER_A, confirmed=True)],
        confirmed=True,
    )
    existing_content = project.voice_folder_mapping_path.read_text(encoding="utf-8")
    build_and_save_production_edit_plan_staging(project)
    validate_production_edit_plan_staging(project)
    readiness = build_production_edit_plan_promote_readiness(project)
    save_production_edit_plan_promote_readiness(project, readiness)
    dry_run_trace = build_production_edit_plan_promote_dry_run_trace(project, readiness)
    save_production_edit_plan_promote_dry_run_trace(project, dry_run_trace)
    manifest = promote_production_edit_plans(project)
    manifest = save_production_edit_plan_promote_manifest(project, manifest)
    patch = build_voice_folder_mapping_patch(project, manifest)
    save_voice_folder_mapping_patch(project, patch)

    with pytest.raises(ValueError):
        merge_voice_folder_mapping(project)  # kein resolution für NEEDS_REVIEW

    assert project.voice_folder_mapping_path.read_text(encoding="utf-8") == existing_content


# --- 29-33: Schutz bestehender Pipeline ---


def test_no_save_edit_plan_or_build_edit_plan_calls_referenced() -> None:
    import otio_app.services.voiceover_generation.production_edit_plan_voice_folder_mapping_merge as merge_module

    source = inspect.getsource(merge_module)
    assert not re.search(r"\bsave_edit_plan\b", source)
    assert not re.search(r"\bbuild_edit_plan\b", source)


def test_no_otio_export_referenced() -> None:
    import otio_app.services.voiceover_generation.production_edit_plan_voice_folder_mapping_merge as merge_module

    source = inspect.getsource(merge_module)
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


def test_merge_modules_reference_no_forbidden_production_functions() -> None:
    import otio_app.services.voiceover_generation.production_edit_plan_voice_folder_mapping_merge as merge_module
    import otio_app.services.voiceover_generation.production_edit_plan_voice_folder_mapping_merge_models as models_module

    for module in (merge_module, models_module):
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
