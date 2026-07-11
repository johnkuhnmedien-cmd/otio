"""Phase 10.9: UI für das Gesamt-Übersichts-Dashboard.

Nutzt dasselbe isolierte AppTest-Repro-Skript wie Phase 10.4-10.8
(tests/_apptest_scripts/production_edit_plan_staging_repro.py), da das neue
Dashboard Teil derselben gerenderten Seite ist.

Rein lesend — kein neues Artefakt, kein Seiteneffekt."""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import get_exports_dir, get_folder_inventory_path, get_supplement_dir
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


def test_ui_shows_pipeline_overview_section(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _make_project(tmp_path)
    at = _run_repro(tmp_path, monkeypatch)
    assert any("Pipeline" in text and "Übersicht" in text for text in _all_text(at, "subheader"))


def test_ui_shows_not_started_when_nothing_built(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _make_project(tmp_path)
    at = _run_repro(tmp_path, monkeypatch)
    combined = " ".join(_all_text(at, "markdown"))
    assert "Noch nicht gestartet" in combined


def test_ui_shows_in_progress_after_staging_build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    at = _run_repro(tmp_path, monkeypatch)
    combined = " ".join(_all_text(at, "markdown"))
    assert "In Bearbeitung" in combined


def test_ui_shows_stage_metrics(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    validate_production_edit_plan_staging(project)
    at = _run_repro(tmp_path, monkeypatch)
    metric_labels = [metric.label for metric in at.metric]
    assert "Staging" in metric_labels
    assert "Validation" in metric_labels
    assert "Promote Readiness" in metric_labels
    assert "Promote" in metric_labels
    assert "Voice Folder Mapping Merge" in metric_labels
    assert "OTIO Export Readiness" in metric_labels


def test_ui_does_not_write_new_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    staging_dir = project.work_dir_path / "voiceover_generation" / "cut_plan" / "production_edit_plan_staging"
    before = sorted(p.relative_to(staging_dir) for p in staging_dir.rglob("*") if p.is_file())
    _run_repro(tmp_path, monkeypatch)
    after = sorted(p.relative_to(staging_dir) for p in staging_dir.rglob("*") if p.is_file())
    assert before == after


def test_no_files_written_under_exports_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    _run_repro(tmp_path, monkeypatch)
    assert not get_exports_dir(project.work_dir_path).exists()


def test_no_files_written_under_supplement_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    _run_repro(tmp_path, monkeypatch)
    assert not get_supplement_dir(project.work_dir_path).exists()


def test_build_button_still_works_with_overview_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Regressionsschutz: das neue Dashboard darf den bestehenden Build-
    Button-Erfolgsfluss nicht stören. Prüft den persistenten, disk-basierten
    Erfolgsnachweis (das tatsächlich geschriebene Package) statt der
    transienten st.success()-Meldung direkt vor st.rerun(), deren
    Sichtbarkeit in AppTest implementierungsabhängig ist."""
    from otio_app.services.voiceover_generation.production_edit_plan_staging_service import (
        load_production_edit_plan_staging_package,
    )

    project = _build_confirmed_bridge_project(tmp_path)
    at = _run_repro(tmp_path, monkeypatch)
    build_button = next(b for b in at.button if "Production EditPlan Staging erzeugen" in b.label)
    at = build_button.click().run()
    assert not at.exception, at.exception
    assert load_production_edit_plan_staging_package(project) is not None
