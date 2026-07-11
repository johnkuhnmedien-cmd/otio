"""Phase 6: Audio-Tab — UI-Guard, API-Key-/Voice-ID-Sperren, Kostenbestätigung."""

from __future__ import annotations

from pathlib import Path

import pytest

from otio_app.models import Project, ProjectMode
from otio_app.project_layout import get_edit_plan_dir, get_exports_dir
from otio_app.services.voiceover_generation.dramaturgy_service import save_confirmed_dramaturgy
from otio_app.services.voiceover_generation.elevenlabs_settings_service import (
    save_elevenlabs_settings,
)
from otio_app.services.voiceover_generation.models import (
    DramaturgyFolderEntry,
    DramaturgyPlan,
    ElevenLabsSettings,
)
from otio_app.ui.voiceover_generation.audio_tab import render_audio_page


def _make_project(tmp_path: Path, *, mode: ProjectMode) -> Project:
    project_root = tmp_path / "USA"
    (project_root / "Grand Canyon").mkdir(parents=True)
    return Project(
        id="audio-ui-project",
        name="Audio UI Test",
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

    render_audio_page()  # darf nicht werfen


def test_page_locked_without_confirmed_dramaturgy_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _make_project(tmp_path, mode=ProjectMode.WITHOUT_VOICEOVER)
    _patch_project_selector(project, monkeypatch)

    render_audio_page()

    assert not (project.work_dir_path / "voiceover_generation" / "voiceover_audio_manifest.json").exists()
    assert not get_edit_plan_dir(project.work_dir_path).exists()
    assert not get_exports_dir(project.work_dir_path).exists()


def test_page_guards_with_voiceover_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _make_project(tmp_path, mode=ProjectMode.WITH_VOICEOVER)
    _patch_project_selector(project, monkeypatch)

    render_audio_page()  # darf nicht werfen und darf nichts schreiben
    assert not (project.work_dir_path / "voiceover_generation").exists()


def test_page_renders_with_confirmed_dramaturgy_no_api_key(
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
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)

    _patch_project_selector(project, monkeypatch)
    render_audio_page()  # darf nicht werfen; TTS-Buttons müssen deaktiviert bleiben

    assert not get_edit_plan_dir(project.work_dir_path).exists()
    assert not get_exports_dir(project.work_dir_path).exists()


def test_page_renders_with_api_key_but_no_voice_id(
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
    save_elevenlabs_settings(project, ElevenLabsSettings(project_id=project.id, voice_id=""))
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_test")

    _patch_project_selector(project, monkeypatch)
    render_audio_page()  # darf nicht werfen; TTS-Buttons müssen wegen fehlender Voice-ID deaktiviert bleiben
