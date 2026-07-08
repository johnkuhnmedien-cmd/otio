"""Phase 3: Dramaturgy-Tab — UI-Guard und Rendering-Smoke-Tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from otio_app.models import Project, ProjectMode
from otio_app.project_layout import get_edit_plan_dir, get_exports_dir
from otio_app.ui.voiceover_generation.dramaturgy_tab import render_dramaturgy_page


def _make_project(tmp_path: Path, *, mode: ProjectMode) -> Project:
    project_root = tmp_path / "USA"
    (project_root / "Grand Canyon").mkdir(parents=True)
    return Project(
        id="dram-ui-project",
        name="Dramaturgy UI Test",
        project_root=str(project_root),
        work_dir=str(project_root / "_otio"),
        project_mode=mode,
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )


@pytest.fixture
def without_voiceover_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Project:
    project = _make_project(tmp_path, mode=ProjectMode.WITHOUT_VOICEOVER)
    monkeypatch.setattr("otio_app.ui.project_context.list_projects", lambda: [project])
    monkeypatch.setattr(
        "otio_app.ui.project_context.get_project_by_id",
        lambda project_id: project if project_id == project.id else None,
    )
    monkeypatch.setattr(
        "streamlit.session_state", {"active_project_id": project.id}, raising=False
    )
    return project


def test_dramaturgy_page_renders_without_exception(without_voiceover_project: Project) -> None:
    render_dramaturgy_page()  # darf nicht werfen, auch ohne Brief/Profile/Inventory


def test_dramaturgy_page_writes_no_edit_plan_documents(
    without_voiceover_project: Project,
) -> None:
    render_dramaturgy_page()
    assert not get_edit_plan_dir(without_voiceover_project.work_dir_path).exists()
    assert not get_exports_dir(without_voiceover_project.work_dir_path).exists()


def test_dramaturgy_page_guards_with_voiceover_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _make_project(tmp_path, mode=ProjectMode.WITH_VOICEOVER)
    monkeypatch.setattr("otio_app.ui.project_context.list_projects", lambda: [project])
    monkeypatch.setattr(
        "otio_app.ui.project_context.get_project_by_id",
        lambda project_id: project if project_id == project.id else None,
    )
    monkeypatch.setattr(
        "streamlit.session_state", {"active_project_id": project.id}, raising=False
    )

    render_dramaturgy_page()  # darf nicht werfen und darf nichts schreiben
    assert not (project.work_dir_path / "voiceover_generation").exists()
