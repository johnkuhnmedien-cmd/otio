"""Phase 10.9: Gesamt-Übersichts-Dashboard für den Production-EditPlan-
Workflow (Service-Ebene).

Rein lesende Aggregation — kein neues Artefakt, kein Seiteneffekt, kein
Aufruf einer Build-/Save-/Export-Funktion."""

from __future__ import annotations

import inspect
import re
from pathlib import Path

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis
from otio_app.defaults import (
    PRODUCTION_EDIT_PLAN_OTIO_EXPORT_READINESS_STATUS_READY,
    PRODUCTION_EDIT_PLAN_PIPELINE_OVERALL_STATUS_BLOCKED,
    PRODUCTION_EDIT_PLAN_PIPELINE_OVERALL_STATUS_COMPLETE,
    PRODUCTION_EDIT_PLAN_PIPELINE_OVERALL_STATUS_IN_PROGRESS,
    PRODUCTION_EDIT_PLAN_PIPELINE_OVERALL_STATUS_NOT_STARTED,
    PRODUCTION_EDIT_PLAN_PIPELINE_STAGE_STATUS_NOT_STARTED,
    PRODUCTION_EDIT_PLAN_PROMOTE_MANIFEST_STATUS_PROMOTED,
)
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import get_folder_inventory_path
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
from otio_app.services.voiceover_generation.models import (
    AlignmentItem,
    ConfirmedFolderPlanItem,
    ConfirmedIntroPlanItem,
    ConfirmedVoiceoverProjectPlan,
    IntroHookVisualBeat,
    SentenceItem,
)
from otio_app.services.voiceover_generation.production_edit_plan_models import ProductionEditPlanValidationError
from otio_app.services.voiceover_generation.production_edit_plan_otio_export_readiness import (
    build_otio_export_readiness_report,
    save_otio_export_readiness_report,
)
from otio_app.services.voiceover_generation.production_edit_plan_pipeline_overview import (
    build_production_edit_plan_pipeline_overview,
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
    load_production_edit_plan_staging_package,
    load_staged_edit_plan,
    save_production_edit_plan_staging_package,
    save_staged_edit_plan,
)
from otio_app.services.voiceover_generation.production_edit_plan_validation import (
    save_production_edit_plan_validation_report,
    validate_production_edit_plan_staging,
)
from otio_app.services.voiceover_generation.production_edit_plan_voice_folder_mapping_merge import (
    merge_voice_folder_mapping,
    save_voice_folder_mapping_merge_manifest,
)

FOLDER_A = "Grand Canyon"


def _make_project(tmp_path: Path) -> Project:
    project_root = tmp_path / "USA"
    (project_root / FOLDER_A).mkdir(parents=True, exist_ok=True)
    return Project(
        id="pipeline-overview-project",
        name="Pipeline Overview Test",
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


def _stage(overview, stage_id: str):
    return next(s for s in overview.stages if s.stage_id == stage_id)


def _full_happy_chain(tmp_path: Path) -> Project:
    """Führt die komplette Kette bis inkl. Merge + OTIO-Readiness-Check aus.

    Bestätigt die (leere) voice_folder_mapping.json VOR dem Merge — Phase
    10.7 preserviert ein bereits bestätigtes Dokument-Level-Flag beim Merge
    (bewusst konservatives Design), sodass der Merge-Manifest-Hash danach
    NICHT durch eine nachträgliche externe Änderung veraltet wird."""
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
    merge_manifest = merge_voice_folder_mapping(project, mark_entries_confirmed=True)
    save_voice_folder_mapping_merge_manifest(project, merge_manifest)
    report = build_otio_export_readiness_report(project)
    save_otio_export_readiness_report(project, report)
    return project


# --- 1-7: Stage-Erkennung ---


def test_all_stages_not_started_initially(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    overview = build_production_edit_plan_pipeline_overview(project)
    assert len(overview.stages) == 6
    assert all(not stage.exists for stage in overview.stages)
    assert all(stage.status == PRODUCTION_EDIT_PLAN_PIPELINE_STAGE_STATUS_NOT_STARTED for stage in overview.stages)
    assert overview.overall_status == PRODUCTION_EDIT_PLAN_PIPELINE_OVERALL_STATUS_NOT_STARTED


def test_staging_stage_appears_after_build(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    overview = build_production_edit_plan_pipeline_overview(project)
    staging = _stage(overview, "staging")
    assert staging.exists is True
    assert staging.status == "STAGED"


def test_validation_stage_appears_after_validate(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    validate_production_edit_plan_staging(project)
    overview = build_production_edit_plan_pipeline_overview(project)
    validation = _stage(overview, "validation")
    assert validation.exists is True
    assert validation.status == "PASS"


def test_promote_readiness_stage_appears_after_dry_run(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    validate_production_edit_plan_staging(project)
    readiness = build_production_edit_plan_promote_readiness(project)
    save_production_edit_plan_promote_readiness(project, readiness)
    dry_run_trace = build_production_edit_plan_promote_dry_run_trace(project, readiness)
    save_production_edit_plan_promote_dry_run_trace(project, dry_run_trace)

    overview = build_production_edit_plan_pipeline_overview(project)
    stage = _stage(overview, "promote_readiness")
    assert stage.exists is True
    assert stage.status == "READY"


def test_promote_stage_appears_after_promote(tmp_path: Path) -> None:
    project = _full_happy_chain(tmp_path)
    overview = build_production_edit_plan_pipeline_overview(project)
    stage = _stage(overview, "promote")
    assert stage.exists is True
    assert stage.status == PRODUCTION_EDIT_PLAN_PROMOTE_MANIFEST_STATUS_PROMOTED
    assert "erstellt" in stage.detail


def test_merge_stage_appears_after_merge(tmp_path: Path) -> None:
    project = _full_happy_chain(tmp_path)
    overview = build_production_edit_plan_pipeline_overview(project)
    stage = _stage(overview, "voice_folder_mapping_merge")
    assert stage.exists is True
    assert stage.status == "MERGED"


def test_otio_readiness_stage_appears_after_check(tmp_path: Path) -> None:
    project = _full_happy_chain(tmp_path)
    overview = build_production_edit_plan_pipeline_overview(project)
    stage = _stage(overview, "otio_export_readiness")
    assert stage.exists is True
    assert stage.status == PRODUCTION_EDIT_PLAN_OTIO_EXPORT_READINESS_STATUS_READY


# --- 8-10: Overall Status ---


def test_overall_status_blocked_when_any_stage_blocked(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    report = validate_production_edit_plan_staging(project)
    blocked = report.model_copy(update={"status": "BLOCKED", "blockers": [ProductionEditPlanValidationError(type="X")]})
    save_production_edit_plan_validation_report(project, blocked)

    overview = build_production_edit_plan_pipeline_overview(project)
    assert overview.overall_status == PRODUCTION_EDIT_PLAN_PIPELINE_OVERALL_STATUS_BLOCKED


def test_overall_status_complete_after_full_happy_chain(tmp_path: Path) -> None:
    project = _full_happy_chain(tmp_path)
    overview = build_production_edit_plan_pipeline_overview(project)
    assert overview.overall_status == PRODUCTION_EDIT_PLAN_PIPELINE_OVERALL_STATUS_COMPLETE
    assert all(not stage.is_stale for stage in overview.stages if stage.exists)


def test_overall_status_in_progress_when_partially_done(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    overview = build_production_edit_plan_pipeline_overview(project)
    assert overview.overall_status == PRODUCTION_EDIT_PLAN_PIPELINE_OVERALL_STATUS_IN_PROGRESS


# --- 11-16: Staleness ---


def test_staging_stage_is_stale_when_bridge_changes(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id, project_title="Geändert", status="AUDIO_READY",
        intro=ConfirmedIntroPlanItem(), folders=[],
    )
    save_confirmed_voiceover_project_plan(project, plan)

    overview = build_production_edit_plan_pipeline_overview(project)
    staging = _stage(overview, "staging")
    assert staging.is_stale is True


def test_validation_stage_is_stale_when_package_changes(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    validate_production_edit_plan_staging(project)
    package = load_production_edit_plan_staging_package(project)
    changed = package.model_copy(update={"warnings": ["manually_added"]})
    save_production_edit_plan_staging_package(project, changed)

    overview = build_production_edit_plan_pipeline_overview(project)
    validation = _stage(overview, "validation")
    assert validation.is_stale is True


def test_promote_readiness_stage_is_stale_when_staged_document_changes(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    validate_production_edit_plan_staging(project)
    readiness = build_production_edit_plan_promote_readiness(project)
    save_production_edit_plan_promote_readiness(project, readiness)
    dry_run_trace = build_production_edit_plan_promote_dry_run_trace(project, readiness)
    save_production_edit_plan_promote_dry_run_trace(project, dry_run_trace)

    document = load_staged_edit_plan(project, "000_intro")
    changed = document.model_copy(update={"plan_generation_notes": ["manually_edited"]})
    save_staged_edit_plan(project, "000_intro", changed)

    overview = build_production_edit_plan_pipeline_overview(project)
    stage = _stage(overview, "promote_readiness")
    assert stage.is_stale is True


def test_promote_stage_is_stale_when_readiness_changes(tmp_path: Path) -> None:
    project = _full_happy_chain(tmp_path)
    readiness = build_production_edit_plan_promote_readiness(project)
    changed_readiness = readiness.model_copy(update={"warnings": ["manually_added"]})
    save_production_edit_plan_promote_readiness(project, changed_readiness)

    overview = build_production_edit_plan_pipeline_overview(project)
    stage = _stage(overview, "promote")
    assert stage.is_stale is True


def test_merge_stage_is_stale_when_patch_changes(tmp_path: Path) -> None:
    project = _full_happy_chain(tmp_path)
    from otio_app.services.voiceover_generation.production_edit_plan_promote_execute import (
        load_voice_folder_mapping_patch,
        save_voice_folder_mapping_patch,
    )

    patch = load_voice_folder_mapping_patch(project)
    changed_patch = patch.model_copy(update={"warnings": ["manually_added"]})
    save_voice_folder_mapping_patch(project, changed_patch)

    overview = build_production_edit_plan_pipeline_overview(project)
    stage = _stage(overview, "voice_folder_mapping_merge")
    assert stage.is_stale is True


def test_otio_readiness_stage_is_stale_when_promote_manifest_changes(tmp_path: Path) -> None:
    project = _full_happy_chain(tmp_path)
    manifest = promote_production_edit_plans(project)  # frischer Lauf -> anderer generated_at/hash
    save_production_edit_plan_promote_manifest(project, manifest)

    overview = build_production_edit_plan_pipeline_overview(project)
    stage = _stage(overview, "otio_export_readiness")
    assert stage.is_stale is True


# --- 17: Detail-Strings ---


def test_stage_details_contain_meaningful_counts(tmp_path: Path) -> None:
    project = _full_happy_chain(tmp_path)
    overview = build_production_edit_plan_pipeline_overview(project)
    staging = _stage(overview, "staging")
    assert "Sektion" in staging.detail
    promote = _stage(overview, "promote")
    assert "erstellt" in promote.detail
    merge = _stage(overview, "voice_folder_mapping_merge")
    assert "hinzugefügt" in merge.detail


# --- 18: Keine Seiteneffekte ---


def test_build_overview_writes_no_new_files(tmp_path: Path) -> None:
    project = _full_happy_chain(tmp_path)
    staging_dir = project.language_work_dir_path / "voiceover_generation" / "cut_plan" / "production_edit_plan_staging"
    before = sorted(p.relative_to(staging_dir) for p in staging_dir.rglob("*") if p.is_file())
    build_production_edit_plan_pipeline_overview(project)
    after = sorted(p.relative_to(staging_dir) for p in staging_dir.rglob("*") if p.is_file())
    assert before == after


# --- 19-20: Schutz bestehender Pipeline ---


_FORBIDDEN_SYMBOLS = (
    "build_edit_plan",
    "save_edit_plan",
    "edit_plan_builder",
    "otio_exporter",
    "build_otio_timeline",
    "export_otio_timeline",
    "merge_confirmed_edit_plans",
    "mark_edit_plans_stale_for_folder",
    "replan_folder_after_supplement",
    "extend_folder_inventory",
    "_set_draft",
)


def test_pipeline_overview_modules_reference_no_forbidden_production_functions() -> None:
    import otio_app.services.voiceover_generation.production_edit_plan_pipeline_overview as overview_module
    import otio_app.services.voiceover_generation.production_edit_plan_pipeline_overview_models as models_module

    for module in (overview_module, models_module):
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
