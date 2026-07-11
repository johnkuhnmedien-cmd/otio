"""Phase 10.7: UI für Voice Folder Mapping Merge.

Nutzt dasselbe isolierte AppTest-Repro-Skript wie Phase 10.4/10.5/10.6
(tests/_apptest_scripts/production_edit_plan_staging_repro.py), da der neue
Mapping-Merge-Unterbereich Teil derselben
`_render_production_edit_plan_staging`-Funktion ist.

Kein OTIO-Export, kein Render, kein Lock-Konzept, keine
save_edit_plan()/build_edit_plan()-Aufrufe."""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis, VoiceFolderMappingEntry
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import get_exports_dir, get_folder_inventory_path, get_supplement_dir
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

FOLDER_A = "Grand Canyon"
PROJECT_ID = "repro-project"
SCRIPT_PATH = Path(__file__).parent / "_apptest_scripts" / "production_edit_plan_staging_repro.py"


def _make_project(tmp_path: Path) -> Project:
    project_root = tmp_path / "USA"
    (project_root / FOLDER_A).mkdir(parents=True, exist_ok=True)
    return Project(
        id=PROJECT_ID,
        name="Repro",
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


def _promoted_project_with_patch(tmp_path: Path) -> Project:
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
    return project


def _run_repro(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AppTest:
    monkeypatch.setenv("REPRO_ROOT", str(tmp_path))
    monkeypatch.setenv("REPRO_PROJECT_ID", PROJECT_ID)
    monkeypatch.setenv("REPRO_FOLDER", FOLDER_A)
    at = AppTest.from_file(str(SCRIPT_PATH))
    at.run()
    assert not at.exception, at.exception
    return at


def _all_text(at: AppTest, *element_types: str) -> list[str]:
    texts: list[str] = []
    for element_type in element_types:
        for element in getattr(at, element_type):
            texts.append(element.value)
    return texts


def test_ui_shows_mapping_merge_section(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _promoted_project_with_patch(tmp_path)
    at = _run_repro(tmp_path, monkeypatch)
    assert any("Voice Folder Mapping übernehmen" in text for text in _all_text(at, "subheader"))


def test_ui_shows_write_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _promoted_project_with_patch(tmp_path)
    at = _run_repro(tmp_path, monkeypatch)
    combined = " ".join(_all_text(at, "warning"))
    assert "voice_folder_mapping.json" in combined


def test_ui_shows_conflict_resolution_when_needs_review(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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

    at = _run_repro(tmp_path, monkeypatch)
    radio_labels = [radio.label for radio in at.radio]
    assert any(FOLDER_A in label for label in radio_labels)

    merge_button = next(b for b in at.button if b.label == "Voice Folder Mapping aktualisieren")
    assert merge_button.disabled is True


def test_ui_merge_button_executes_and_writes_mapping(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _promoted_project_with_patch(tmp_path)
    at = _run_repro(tmp_path, monkeypatch)
    merge_button = next(b for b in at.button if b.label == "Voice Folder Mapping aktualisieren")
    assert merge_button.disabled is False
    at = merge_button.click().run()
    assert not at.exception, at.exception
    mapping = load_voice_folder_mapping(project.voice_folder_mapping_path)
    assert mapping is not None
    assert any(entry.folder == FOLDER_A for entry in mapping.entries)


def test_ui_shows_manifest_after_merge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _promoted_project_with_patch(tmp_path)
    at = _run_repro(tmp_path, monkeypatch)
    merge_button = next(b for b in at.button if b.label == "Voice Folder Mapping aktualisieren")
    at = merge_button.click().run()
    assert not at.exception, at.exception
    combined = " ".join(_all_text(at, "markdown"))
    assert "Voice Folder Mapping Merge Manifest" in combined


def test_ui_resolving_conflict_enables_merge_button(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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

    at = _run_repro(tmp_path, monkeypatch)
    radio_key = f"voice_folder_mapping_merge_resolution_{PROJECT_ID}_{FOLDER_A}"
    at = at.radio(key=radio_key).set_value("Neuen Voice-over übernehmen (APPLY)").run()
    assert not at.exception, at.exception
    merge_button = next(b for b in at.button if b.label == "Voice Folder Mapping aktualisieren")
    assert merge_button.disabled is False

    at = merge_button.click().run()
    assert not at.exception, at.exception
    mapping = load_voice_folder_mapping(project.voice_folder_mapping_path)
    entry = next(e for e in mapping.entries if e.folder == FOLDER_A)
    assert entry.voice_file != "/old/voice.mp3"


def test_no_files_written_under_exports_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _promoted_project_with_patch(tmp_path)
    at = _run_repro(tmp_path, monkeypatch)
    merge_button = next(b for b in at.button if b.label == "Voice Folder Mapping aktualisieren")
    merge_button.click().run()
    assert not get_exports_dir(project.work_dir_path).exists()


def test_no_files_written_under_supplement_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _promoted_project_with_patch(tmp_path)
    at = _run_repro(tmp_path, monkeypatch)
    merge_button = next(b for b in at.button if b.label == "Voice Folder Mapping aktualisieren")
    merge_button.click().run()
    assert not get_supplement_dir(project.work_dir_path).exists()


def test_no_original_media_modified(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _promoted_project_with_patch(tmp_path)
    photo_path = project.project_root_path / FOLDER_A / "photo_a.jpg"
    original = photo_path.read_bytes()
    at = _run_repro(tmp_path, monkeypatch)
    merge_button = next(b for b in at.button if b.label == "Voice Folder Mapping aktualisieren")
    merge_button.click().run()
    assert photo_path.read_bytes() == original


def test_ui_has_no_real_otio_export_button(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Phase 10.8 fügt einen rein lesenden, vollständig isolierten „OTIO
    Export Readiness prüfen“-Button hinzu (kein Export, kein Aufruf der
    Produktions-Export-Pipeline) — hier wird geprüft, dass kein Button mit
    tatsächlicher Export-Semantik existiert."""
    _promoted_project_with_patch(tmp_path)
    at = _run_repro(tmp_path, monkeypatch)
    labels = [button.label for button in at.button]
    assert not any("exportieren" in label.lower() for label in labels)


def test_ui_has_no_lock_button(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _promoted_project_with_patch(tmp_path)
    at = _run_repro(tmp_path, monkeypatch)
    labels = [button.label for button in at.button]
    assert not any("lock" in label.lower() for label in labels)
