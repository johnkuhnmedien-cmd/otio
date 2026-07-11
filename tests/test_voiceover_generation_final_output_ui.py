"""Phase 7: Final-Output-Tab — UI-Guard, keine EditPlanDocuments/OTIO-Export, Anzeige."""

from __future__ import annotations

from pathlib import Path

import pytest

from otio_app.models import Project, ProjectMode
from otio_app.project_layout import get_edit_plan_dir, get_exports_dir
from otio_app.services.voiceover_generation.dramaturgy_service import save_confirmed_dramaturgy
from otio_app.services.voiceover_generation.final_plan_service import (
    build_confirmed_voiceover_project_plan,
    save_confirmed_voiceover_project_plan,
)
from otio_app.services.voiceover_generation.models import DramaturgyFolderEntry, DramaturgyPlan
from otio_app.ui.voiceover_generation.final_output_tab import render_final_output_page


def _make_project(tmp_path: Path, *, mode: ProjectMode) -> Project:
    project_root = tmp_path / "USA"
    (project_root / "Grand Canyon").mkdir(parents=True)
    return Project(
        id="final-output-ui-project",
        name="Final Output UI Test",
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

    render_final_output_page()  # darf nicht werfen


def test_page_locked_without_confirmed_dramaturgy_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _make_project(tmp_path, mode=ProjectMode.WITHOUT_VOICEOVER)
    _patch_project_selector(project, monkeypatch)

    render_final_output_page()

    assert not (project.work_dir_path / "voiceover_generation" / "confirmed_voiceover_project_plan.json").exists()
    assert not get_edit_plan_dir(project.work_dir_path).exists()
    assert not get_exports_dir(project.work_dir_path).exists()


def test_page_guards_with_voiceover_project_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _make_project(tmp_path, mode=ProjectMode.WITH_VOICEOVER)
    _patch_project_selector(project, monkeypatch)

    render_final_output_page()  # darf nicht werfen und darf nichts schreiben

    assert not (project.work_dir_path / "voiceover_generation").exists()
    assert not get_edit_plan_dir(project.work_dir_path).exists()
    assert not get_exports_dir(project.work_dir_path).exists()


def test_page_renders_with_confirmed_dramaturgy_but_no_plan_yet(
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
    render_final_output_page()  # darf nicht werfen; noch kein finaler Plan vorhanden

    assert not get_edit_plan_dir(project.work_dir_path).exists()
    assert not get_exports_dir(project.work_dir_path).exists()


def test_page_renders_existing_plan_without_writing_edit_plan_or_otio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _make_project(tmp_path, mode=ProjectMode.WITHOUT_VOICEOVER)
    dramaturgy = DramaturgyPlan(
        project_id=project.id,
        recommended_folder_order=[
            DramaturgyFolderEntry(folder_name="Grand Canyon", order_index=1, enabled=True)
        ],
    )
    save_confirmed_dramaturgy(project, dramaturgy)
    plan = build_confirmed_voiceover_project_plan(project)
    save_confirmed_voiceover_project_plan(project, plan)

    _patch_project_selector(project, monkeypatch)
    render_final_output_page()  # darf nicht werfen

    assert not get_edit_plan_dir(project.work_dir_path).exists()
    assert not get_exports_dir(project.work_dir_path).exists()
    original_media = project.project_root_path / "Grand Canyon"
    assert original_media.exists()
    assert list(original_media.iterdir()) == []


def test_audio_ready_with_warnings_is_shown_and_not_ready_for_cut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from otio_app.services.voiceover_generation.models import (
        ConfirmedFolderPlanItem,
        ConfirmedVoiceoverProjectPlan,
    )
    from otio_app.defaults import PLAN_STATUS_AUDIO_READY, PLAN_STATUS_READY_FOR_CUT

    project = _make_project(tmp_path, mode=ProjectMode.WITHOUT_VOICEOVER)
    dramaturgy = DramaturgyPlan(
        project_id=project.id,
        recommended_folder_order=[
            DramaturgyFolderEntry(folder_name="Grand Canyon", order_index=1, enabled=True)
        ],
    )
    save_confirmed_dramaturgy(project, dramaturgy)

    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id,
        status=PLAN_STATUS_AUDIO_READY,
        folders=[
            ConfirmedFolderPlanItem(
                folder_name="Grand Canyon",
                order_index=1,
                audio_status="AUDIO_READY_WITH_WARNINGS",
                readiness_status="WARNING",
                voiceover_text_full="Ein Testsatz.",
                warnings=["Audio-Dauer konnte nicht ermittelt werden (ffprobe)."],
            )
        ],
    )
    save_confirmed_voiceover_project_plan(project, plan)
    assert plan.status != PLAN_STATUS_READY_FOR_CUT

    _patch_project_selector(project, monkeypatch)
    render_final_output_page()  # darf nicht werfen; AUDIO_READY_WITH_WARNINGS wird angezeigt
