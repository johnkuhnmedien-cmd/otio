"""Phase 5: Intro-Tab — UI-Guard, Sperre ohne vollständig bestätigte Folder Voice-overs."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import get_edit_plan_dir, get_exports_dir, get_folder_inventory_path
from otio_app.services.plan_llm_client import PlanLlmResponse
from otio_app.services.voiceover_generation.dramaturgy_service import save_confirmed_dramaturgy
from otio_app.services.voiceover_generation.folder_voiceover_settings_service import (
    build_default_folder_voiceover_settings,
    save_folder_voiceover_settings,
)
from otio_app.services.voiceover_generation.models import DramaturgyFolderEntry, DramaturgyPlan
from otio_app.services.voiceover_generation.voiceover_author_service import generate_folder_voiceover
from otio_app.services.voiceover_generation.voiceover_review_service import confirm_folder_voiceover
from otio_app.ui.voiceover_generation.intro_tab import render_intro_page

_AUTHOR_MODULE = "otio_app.services.voiceover_generation.voiceover_author_service"


def _make_project(tmp_path: Path, *, mode: ProjectMode) -> Project:
    project_root = tmp_path / "USA"
    (project_root / "Grand Canyon").mkdir(parents=True)
    return Project(
        id="intro-ui-project",
        name="Intro UI Test",
        project_root=str(project_root),
        work_dir=str(project_root / "_otio"),
        project_mode=mode,
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )


def _patch_project_selector(project: Project, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("otio_app.ui.project_context.list_projects", lambda: [project])
    monkeypatch.setattr(
        "otio_app.ui.project_context.get_project_by_id",
        lambda project_id: project if project_id == project.id else None,
    )
    monkeypatch.setattr("streamlit.session_state", {"active_project_id": project.id}, raising=False)


def test_page_renders_without_exception_when_no_confirmed_dramaturgy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _make_project(tmp_path, mode=ProjectMode.WITHOUT_VOICEOVER)
    _patch_project_selector(project, monkeypatch)

    render_intro_page()  # darf nicht werfen


def test_page_locked_without_confirmed_dramaturgy_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _make_project(tmp_path, mode=ProjectMode.WITHOUT_VOICEOVER)
    _patch_project_selector(project, monkeypatch)

    render_intro_page()

    assert not (project.work_dir_path / "voiceover_generation" / "intro_hook_candidates.json").exists()
    assert not get_edit_plan_dir(project.work_dir_path).exists()
    assert not get_exports_dir(project.work_dir_path).exists()


def test_page_locked_with_confirmed_dramaturgy_but_no_confirmed_voiceovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _make_project(tmp_path, mode=ProjectMode.WITHOUT_VOICEOVER)
    plan = DramaturgyPlan(
        project_id=project.id,
        recommended_folder_order=[
            DramaturgyFolderEntry(folder_name="Grand Canyon", order_index=1, enabled=True)
        ],
    )
    save_confirmed_dramaturgy(project, plan)

    _patch_project_selector(project, monkeypatch)
    render_intro_page()  # darf nicht werfen — zeigt Warnung + Sperre

    assert not (project.work_dir_path / "voiceover_generation" / "intro_hook_candidates.json").exists()


def test_page_guards_with_voiceover_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _make_project(tmp_path, mode=ProjectMode.WITH_VOICEOVER)
    _patch_project_selector(project, monkeypatch)

    render_intro_page()  # darf nicht werfen und darf nichts schreiben
    assert not (project.work_dir_path / "voiceover_generation").exists()


def test_page_renders_when_all_active_folders_confirmed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _make_project(tmp_path, mode=ProjectMode.WITHOUT_VOICEOVER)
    inv_path = get_folder_inventory_path(project.work_dir_path, "Grand Canyon")
    inv_path.parent.mkdir(parents=True, exist_ok=True)
    analysis = AssetFolderAnalysis(
        folder="Grand Canyon",
        assets=[AssetMediaAnalysis(path="Grand Canyon/clip1.mp4", description="Weite Aufnahme.")],
    )
    inv_path.write_text(analysis.model_dump_json(indent=2), encoding="utf-8")

    plan = DramaturgyPlan(
        project_id=project.id,
        recommended_folder_order=[
            DramaturgyFolderEntry(folder_name="Grand Canyon", order_index=1, enabled=True)
        ],
    )
    save_confirmed_dramaturgy(project, plan)
    save_folder_voiceover_settings(project, build_default_folder_voiceover_settings(project))

    fake_response = PlanLlmResponse(
        provider="anthropic", model="claude-sonnet-5",
        raw_text=json.dumps({"voiceover_text_full": "Text.", "sentence_items": []}),
    )
    with patch(f"{_AUTHOR_MODULE}.generate_plan_text_with_metadata", return_value=fake_response):
        generate_folder_voiceover(project, "Grand Canyon", provider="anthropic", model="claude-sonnet-5")
    confirm_folder_voiceover(project, "Grand Canyon")

    _patch_project_selector(project, monkeypatch)
    render_intro_page()  # darf nicht werfen — Voraussetzungen erfüllt

    assert not get_edit_plan_dir(project.work_dir_path).exists()
    assert not get_exports_dir(project.work_dir_path).exists()
