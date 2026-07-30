"""Commit 6: Workflow-Dashboard vollständig verdrahten — der primäre
Dashboard-Button ruft für JEDEN Schritt dieselbe Funktion auf wie der
jeweilige Detail-Bereich weiter unten im Tab (siehe
_run_cut_plan_workflow_action in cut_plan_tab.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import get_folder_inventory_path
from otio_app.services.voiceover_generation.cut_plan_builder import (
    apply_asset_selection_to_draft,
    build_cut_plan_draft,
    load_cut_plan_draft,
    save_cut_plan_draft,
)
from otio_app.services.voiceover_generation.cut_plan_models import CutPlanSettings
from otio_app.services.voiceover_generation.cut_plan_settings_service import save_cut_plan_settings
from otio_app.services.voiceover_generation.cut_plan_validator import load_cut_plan_validation_report
from otio_app.services.voiceover_generation.final_plan_service import save_confirmed_voiceover_project_plan
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


def _make_project(tmp_path: Path) -> Project:
    project_root = tmp_path / "USA"
    (project_root / FOLDER_A).mkdir(parents=True)
    return Project(
        id="cut-plan-workflow-dashboard-project",
        name="Workflow Dashboard UI Test",
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
    audio_dir = project.language_work_dir_path / "voiceover_generation" / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name in names:
        path = audio_dir / name
        path.write_bytes(b"FAKE_AUDIO_BYTES")
        paths.append(path)
    return paths


def _happy_path_plan_and_project(tmp_path: Path) -> Project:
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
    folder = ConfirmedFolderPlanItem(
        folder_name=FOLDER_A,
        order_index=1,
        audio_path=str(folder_audio),
        audio_duration_sec=5.0,
        sentence_items=[SentenceItem(sentence_id="sentence_001", text="Ein Satz.", primary_asset_id="asset_photo_b")],
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


def _patch_project_selector(project: Project, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("otio_app.ui.project_context.list_projects", lambda: [project])
    monkeypatch.setattr(
        "otio_app.ui.project_context.get_project_by_id",
        lambda project_id: project if project_id == project.id else None,
    )
    monkeypatch.setattr("streamlit.session_state", {"active_project_id": project.id}, raising=False)


def test_dashboard_button_triggers_validation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Der primäre Dashboard-Button für den 'validate'-Schritt löst
    tatsächlich validate_cut_plan_draft aus (End-to-End über den UI-Layer,
    nicht nur den Workflow-State selbst)."""
    project = _happy_path_plan_and_project(tmp_path)
    assert load_cut_plan_validation_report(project) is None

    dashboard_key = f"cut_plan_workflow_next_action_{project.id}_validate"

    def _fake_button(label, *args, **kwargs):
        return kwargs.get("key") == dashboard_key

    successes: list[str] = []
    _patch_project_selector(project, monkeypatch)
    monkeypatch.setattr("streamlit.button", _fake_button)
    monkeypatch.setattr("streamlit.rerun", lambda: None)
    monkeypatch.setattr("streamlit.success", lambda msg, **k: successes.append(msg))

    render_cut_plan_page()

    assert load_cut_plan_validation_report(project) is not None
    assert any("Validierung" in msg for msg in successes)


def test_dashboard_shows_all_done_when_pipeline_complete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from otio_app.services.voiceover_generation.cut_plan_builder import validate_cut_plan_draft

    project = _happy_path_plan_and_project(tmp_path)
    validate_cut_plan_draft(project)

    _patch_project_selector(project, monkeypatch)
    monkeypatch.setattr("streamlit.button", lambda *a, **k: False)
    monkeypatch.setattr("streamlit.rerun", lambda: None)
    successes: list[str] = []
    monkeypatch.setattr("streamlit.success", lambda msg, **k: successes.append(msg))

    render_cut_plan_page()

    assert any("vollständig durchlaufen" in msg for msg in successes)
