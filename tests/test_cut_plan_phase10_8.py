"""Phase 10.8: OTIO Export Readiness Check für promotete/gemappte Folder
(Service-Ebene).

Wichtig: Dieses Modul bleibt — wie alle anderen Module unter
otio_app/services/voiceover_generation/ — STRIKT von der bestehenden
"mit Voice-Over"-Produktionspipeline isoliert. Es ruft NIEMALS
otio_exporter/edit_plan_builder auf und schreibt NIEMALS eine .otio-Datei.
Es prüft rein strukturell und eigenständig, ob die grundlegenden
Voraussetzungen für einen späteren Export erfüllt wären."""

from __future__ import annotations

import inspect
import re
from pathlib import Path

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis, EditPlanDocument, VoiceFolderMappingEntry
from otio_app.defaults import (
    PRODUCTION_EDIT_PLAN_OTIO_EXPORT_READINESS_FOLDER_STATUS_NOT_READY,
    PRODUCTION_EDIT_PLAN_OTIO_EXPORT_READINESS_FOLDER_STATUS_READY,
    PRODUCTION_EDIT_PLAN_OTIO_EXPORT_READINESS_STATUS_BLOCKED,
    PRODUCTION_EDIT_PLAN_OTIO_EXPORT_READINESS_STATUS_NOT_READY,
    PRODUCTION_EDIT_PLAN_OTIO_EXPORT_READINESS_STATUS_READY,
)
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import (
    get_exports_dir,
    get_folder_edit_plan_path,
    get_folder_inventory_path,
    get_otio_export_readiness_report_path,
    get_supplement_dir,
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
from otio_app.services.voiceover_generation.models import (
    AlignmentItem,
    ConfirmedFolderPlanItem,
    ConfirmedIntroPlanItem,
    ConfirmedVoiceoverProjectPlan,
    IntroHookVisualBeat,
    SentenceItem,
)
from otio_app.services.voiceover_generation.production_edit_plan_otio_export_readiness import (
    build_otio_export_readiness_report,
    load_otio_export_readiness_report,
    save_otio_export_readiness_report,
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
    merge_voice_folder_mapping,
    save_voice_folder_mapping_merge_manifest,
)

FOLDER_A = "Grand Canyon"


def _make_project(tmp_path: Path) -> Project:
    project_root = tmp_path / "USA"
    (project_root / FOLDER_A).mkdir(parents=True, exist_ok=True)
    return Project(
        id="otio-export-readiness-project",
        name="OTIO Export Readiness Test",
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


def _promoted_and_mapped_project(tmp_path: Path) -> Project:
    """Baut die vollständige Kette bis inkl. Promote + Mapping Merge."""
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
    save_voice_folder_mapping_patch(project, patch)
    merge_manifest = merge_voice_folder_mapping(project, mark_entries_confirmed=True)
    save_voice_folder_mapping_merge_manifest(project, merge_manifest)
    # Simuliert den zusätzlichen, bewusst separaten Schritt im Tab
    # „② Zuordnung“: die GESAMTE Zuordnung (Dokument-Level) explizit
    # bestätigen — Phase 10.7 setzt dies absichtlich NICHT automatisch.
    mapping = load_voice_folder_mapping(project.voice_folder_mapping_path)
    save_voice_folder_mapping(project, list(mapping.entries), confirmed=True)
    return project


# --- 1-3: Blocker ohne Vorbedingungen ---


def test_blocked_without_promote_manifest(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    report = build_otio_export_readiness_report(project)
    assert report.status == PRODUCTION_EDIT_PLAN_OTIO_EXPORT_READINESS_STATUS_BLOCKED


def test_blocked_when_promote_manifest_has_no_promoted_folders(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    validate_production_edit_plan_staging(project)
    readiness = build_production_edit_plan_promote_readiness(project)
    save_production_edit_plan_promote_readiness(project, readiness)
    dry_run_trace = build_production_edit_plan_promote_dry_run_trace(project, readiness)
    save_production_edit_plan_promote_dry_run_trace(project, dry_run_trace)
    manifest = promote_production_edit_plans(project)
    # Manifest künstlich ohne Sections speichern, um "keine promoteten Folder" zu simulieren.
    empty_manifest = manifest.model_copy(update={"sections": []})
    save_production_edit_plan_promote_manifest(project, empty_manifest)

    report = build_otio_export_readiness_report(project)
    assert report.status == PRODUCTION_EDIT_PLAN_OTIO_EXPORT_READINESS_STATUS_BLOCKED


def test_blocked_when_mapping_not_confirmed(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    validate_production_edit_plan_staging(project)
    readiness = build_production_edit_plan_promote_readiness(project)
    save_production_edit_plan_promote_readiness(project, readiness)
    dry_run_trace = build_production_edit_plan_promote_dry_run_trace(project, readiness)
    save_production_edit_plan_promote_dry_run_trace(project, dry_run_trace)
    manifest = promote_production_edit_plans(project)
    save_production_edit_plan_promote_manifest(project, manifest)
    # Kein Mapping Merge -> voice_folder_mapping.json existiert nicht.

    report = build_otio_export_readiness_report(project)
    assert report.status == PRODUCTION_EDIT_PLAN_OTIO_EXPORT_READINESS_STATUS_BLOCKED
    assert report.mapping_confirmed is False


# --- 4-9: READY / NOT_READY Klassifikation ---


def test_ready_after_full_promote_and_merge(tmp_path: Path) -> None:
    project = _promoted_and_mapped_project(tmp_path)
    report = build_otio_export_readiness_report(project)
    assert report.status == PRODUCTION_EDIT_PLAN_OTIO_EXPORT_READINESS_STATUS_READY
    folder_result = next(f for f in report.folders if f.folder_name == FOLDER_A)
    assert folder_result.status == PRODUCTION_EDIT_PLAN_OTIO_EXPORT_READINESS_FOLDER_STATUS_READY


def test_not_ready_when_entry_not_confirmed_in_mapping(tmp_path: Path) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    validate_production_edit_plan_staging(project)
    readiness = build_production_edit_plan_promote_readiness(project)
    save_production_edit_plan_promote_readiness(project, readiness)
    dry_run_trace = build_production_edit_plan_promote_dry_run_trace(project, readiness)
    save_production_edit_plan_promote_dry_run_trace(project, dry_run_trace)
    manifest = promote_production_edit_plans(project)
    save_production_edit_plan_promote_manifest(project, manifest)

    target_path = get_folder_edit_plan_path(project.language_work_dir_path, FOLDER_A)
    promoted_doc = EditPlanDocument.model_validate_json(target_path.read_text(encoding="utf-8"))
    save_voice_folder_mapping(
        project,
        [VoiceFolderMappingEntry(voice_file=promoted_doc.voiceover.path, folder=FOLDER_A, confirmed=False)],
        confirmed=True,
    )

    report = build_otio_export_readiness_report(project)
    assert report.status == PRODUCTION_EDIT_PLAN_OTIO_EXPORT_READINESS_STATUS_NOT_READY
    folder_result = next(f for f in report.folders if f.folder_name == FOLDER_A)
    assert folder_result.status == PRODUCTION_EDIT_PLAN_OTIO_EXPORT_READINESS_FOLDER_STATUS_NOT_READY
    assert folder_result.in_confirmed_mapping is False


def test_not_ready_when_edit_plan_missing(tmp_path: Path) -> None:
    project = _promoted_and_mapped_project(tmp_path)
    get_folder_edit_plan_path(project.language_work_dir_path, FOLDER_A).unlink()

    report = build_otio_export_readiness_report(project)
    assert report.status == PRODUCTION_EDIT_PLAN_OTIO_EXPORT_READINESS_STATUS_NOT_READY
    folder_result = next(f for f in report.folders if f.folder_name == FOLDER_A)
    assert folder_result.edit_plan_exists is False
    assert any("existiert nicht" in w for w in folder_result.warnings)


def test_not_ready_when_edit_plan_not_confirmed(tmp_path: Path) -> None:
    project = _promoted_and_mapped_project(tmp_path)
    target_path = get_folder_edit_plan_path(project.language_work_dir_path, FOLDER_A)
    document = EditPlanDocument.model_validate_json(target_path.read_text(encoding="utf-8"))
    tampered = document.model_copy(update={"confirmed": False})
    target_path.write_text(tampered.model_dump_json(indent=2), encoding="utf-8")

    report = build_otio_export_readiness_report(project)
    folder_result = next(f for f in report.folders if f.folder_name == FOLDER_A)
    assert folder_result.status == PRODUCTION_EDIT_PLAN_OTIO_EXPORT_READINESS_FOLDER_STATUS_NOT_READY
    assert folder_result.edit_plan_confirmed is False


def test_not_ready_when_no_voiceover(tmp_path: Path) -> None:
    project = _promoted_and_mapped_project(tmp_path)
    target_path = get_folder_edit_plan_path(project.language_work_dir_path, FOLDER_A)
    document = EditPlanDocument.model_validate_json(target_path.read_text(encoding="utf-8"))
    tampered = document.model_copy(update={"voiceover": None})
    target_path.write_text(tampered.model_dump_json(indent=2), encoding="utf-8")

    report = build_otio_export_readiness_report(project)
    folder_result = next(f for f in report.folders if f.folder_name == FOLDER_A)
    assert folder_result.has_voiceover is False
    assert folder_result.status == PRODUCTION_EDIT_PLAN_OTIO_EXPORT_READINESS_FOLDER_STATUS_NOT_READY


def test_not_ready_when_no_timeline_items_or_shots(tmp_path: Path) -> None:
    project = _promoted_and_mapped_project(tmp_path)
    target_path = get_folder_edit_plan_path(project.language_work_dir_path, FOLDER_A)
    document = EditPlanDocument.model_validate_json(target_path.read_text(encoding="utf-8"))
    tampered = document.model_copy(update={"timeline_items": [], "shots": []})
    target_path.write_text(tampered.model_dump_json(indent=2), encoding="utf-8")

    report = build_otio_export_readiness_report(project)
    folder_result = next(f for f in report.folders if f.folder_name == FOLDER_A)
    assert folder_result.timeline_item_count == 0
    assert folder_result.shot_count == 0
    assert folder_result.status == PRODUCTION_EDIT_PLAN_OTIO_EXPORT_READINESS_FOLDER_STATUS_NOT_READY


# --- 10-12: Report-Inhalt ---


def test_report_contains_source_hashes(tmp_path: Path) -> None:
    project = _promoted_and_mapped_project(tmp_path)
    report = build_otio_export_readiness_report(project)
    assert report.source_promote_manifest_hash
    assert report.source_merge_manifest_hash


def test_report_totals_are_correct(tmp_path: Path) -> None:
    project = _promoted_and_mapped_project(tmp_path)
    report = build_otio_export_readiness_report(project)
    assert report.total_shots > 0
    assert report.total_timeline_items > 0
    assert report.checked_folders == ["Intro", FOLDER_A]


def test_unreadable_edit_plan_yields_warning_not_crash(tmp_path: Path) -> None:
    project = _promoted_and_mapped_project(tmp_path)
    target_path = get_folder_edit_plan_path(project.language_work_dir_path, FOLDER_A)
    target_path.write_text("NOT VALID JSON {{{", encoding="utf-8")

    report = build_otio_export_readiness_report(project)
    folder_result = next(f for f in report.folders if f.folder_name == FOLDER_A)
    assert any("nicht lesbar" in w for w in folder_result.warnings)
    assert folder_result.status == PRODUCTION_EDIT_PLAN_OTIO_EXPORT_READINESS_FOLDER_STATUS_NOT_READY


# --- 13-14: save/load ---


def test_save_and_load_report_roundtrip(tmp_path: Path) -> None:
    project = _promoted_and_mapped_project(tmp_path)
    report = build_otio_export_readiness_report(project)
    saved = save_otio_export_readiness_report(project, report)
    loaded = load_otio_export_readiness_report(project)
    assert loaded is not None
    assert loaded.status == saved.status
    assert get_otio_export_readiness_report_path(project.language_work_dir_path).is_file()


def test_load_report_returns_none_when_missing(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    assert load_otio_export_readiness_report(project) is None


# --- 15-19: keine Seiteneffekte / kein Aufruf der Produktionspipeline ---


def test_no_otio_file_written(tmp_path: Path) -> None:
    project = _promoted_and_mapped_project(tmp_path)
    build_otio_export_readiness_report(project)
    assert not get_exports_dir(project.language_work_dir_path).exists()


def test_no_files_written_under_supplement_dir(tmp_path: Path) -> None:
    project = _promoted_and_mapped_project(tmp_path)
    build_otio_export_readiness_report(project)
    assert not get_supplement_dir(project.language_work_dir_path).exists()


def test_no_original_media_modified(tmp_path: Path) -> None:
    project = _promoted_and_mapped_project(tmp_path)
    photo_path = project.project_root_path / FOLDER_A / "photo_a.jpg"
    original = photo_path.read_bytes()
    build_otio_export_readiness_report(project)
    assert photo_path.read_bytes() == original


def test_no_audio_files_overwritten(tmp_path: Path) -> None:
    project = _promoted_and_mapped_project(tmp_path)
    audio_path = project.language_work_dir_path / "voiceover_generation" / "audio" / "intro.mp3"
    original = audio_path.read_bytes()
    build_otio_export_readiness_report(project)
    assert audio_path.read_bytes() == original


def test_voice_folder_mapping_remains_byte_identical(tmp_path: Path) -> None:
    project = _promoted_and_mapped_project(tmp_path)
    mapping_before = project.voice_folder_mapping_path.read_text(encoding="utf-8")
    build_otio_export_readiness_report(project)
    assert project.voice_folder_mapping_path.read_text(encoding="utf-8") == mapping_before


def test_existing_production_edit_plan_remains_byte_identical(tmp_path: Path) -> None:
    project = _promoted_and_mapped_project(tmp_path)
    target_path = get_folder_edit_plan_path(project.language_work_dir_path, FOLDER_A)
    before = target_path.read_text(encoding="utf-8")
    build_otio_export_readiness_report(project)
    assert target_path.read_text(encoding="utf-8") == before


def test_report_reflects_current_mapping_state(tmp_path: Path) -> None:
    project = _promoted_and_mapped_project(tmp_path)
    mapping = load_voice_folder_mapping(project.voice_folder_mapping_path)
    assert mapping is not None and mapping.confirmed


# --- 20-24: Schutz bestehender Pipeline / keine verbotenen Referenzen ---


def test_module_does_not_reference_otio_exporter() -> None:
    """Wichtigster Guard dieser Phase: das Modul bleibt VOLLSTÄNDIG isoliert
    von der bestehenden Produktions-Export-Pipeline — kein Aufruf, kein
    Import von otio_exporter/merge_confirmed_edit_plans/build_otio_timeline/
    export_otio_timeline."""
    import otio_app.services.voiceover_generation.production_edit_plan_otio_export_readiness as readiness_module

    source = inspect.getsource(readiness_module)
    for forbidden in ("otio_exporter", "merge_confirmed_edit_plans", "build_otio_timeline", "export_otio_timeline"):
        assert not re.search(rf"\b{re.escape(forbidden)}\b", source), (
            f"production_edit_plan_otio_export_readiness.py referenziert verbotenes Symbol '{forbidden}'."
        )


def test_no_save_edit_plan_or_build_edit_plan_calls_referenced() -> None:
    import otio_app.services.voiceover_generation.production_edit_plan_otio_export_readiness as readiness_module

    source = inspect.getsource(readiness_module)
    assert not re.search(r"\bsave_edit_plan\b", source)
    assert not re.search(r"\bbuild_edit_plan\b", source)


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


def test_otio_export_readiness_modules_reference_no_forbidden_production_functions() -> None:
    import otio_app.services.voiceover_generation.production_edit_plan_otio_export_readiness as readiness_module
    import otio_app.services.voiceover_generation.production_edit_plan_otio_export_readiness_models as models_module

    for module in (readiness_module, models_module):
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
    assert hasattr(otio_exporter, "merge_confirmed_edit_plans")
