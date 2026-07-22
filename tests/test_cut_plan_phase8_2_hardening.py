"""Vorab-Hardening vor Phase 8.3: Staleness-Erkennung für cut_plan.draft.json.

source_plan_hash wird bereits seit Phase 8.2 im Draft gespeichert, aber bis
jetzt nirgends gegen den aktuellen Quellplan verglichen. Kein Auto-Update,
kein Auto-Overwrite — nur eine Warnung."""

from __future__ import annotations

from pathlib import Path

import pytest

from otio_app.models import Project, ProjectMode
from otio_app.services.voiceover_generation.cut_plan_builder import (
    build_cut_plan_draft,
    is_cut_plan_draft_stale,
    save_cut_plan_draft,
)
from otio_app.services.voiceover_generation.final_plan_service import (
    save_confirmed_voiceover_project_plan,
)
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
        id="cut-plan-stale-project",
        name="Cut Plan Stale Test",
        project_root=str(project_root),
        work_dir=str(project_root / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=[FOLDER_A],
        selected_asset_subdirs=[FOLDER_A],
    )


def _plan(project: Project, *, title: str = "Test") -> ConfirmedVoiceoverProjectPlan:
    intro = ConfirmedIntroPlanItem(
        hook_text="Ein Ort voller Geheimnisse.",
        audio_path="/fake/intro.mp3",
        audio_duration_sec=20.0,
        visual_beats=[IntroHookVisualBeat(hook_beat_id="hook_beat_001", text="x", primary_asset_id="asset_a")],
        alignment_items=[
            AlignmentItem(sentence_id="hook_beat_001", audio_start_sec=0.0, audio_end_sec=5.0, duration_sec=5.0)
        ],
    )
    folder = ConfirmedFolderPlanItem(
        folder_name=FOLDER_A,
        order_index=1,
        audio_path="/fake/folder.mp3",
        audio_duration_sec=40.0,
        sentence_items=[SentenceItem(sentence_id="sentence_001", text="Ein Satz.", primary_asset_id="asset_b")],
        alignment_items=[
            AlignmentItem(sentence_id="sentence_001", audio_start_sec=2.0, audio_end_sec=7.0, duration_sec=5.0)
        ],
    )
    return ConfirmedVoiceoverProjectPlan(project_id=project.id, project_title=title, intro=intro, folders=[folder])


def test_draft_is_not_stale_right_after_creation(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    save_confirmed_voiceover_project_plan(project, _plan(project))

    draft = build_cut_plan_draft(project)
    save_cut_plan_draft(project, draft)

    assert is_cut_plan_draft_stale(project, draft) is False


def test_draft_becomes_stale_after_source_plan_changes(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    save_confirmed_voiceover_project_plan(project, _plan(project))

    draft = build_cut_plan_draft(project)
    save_cut_plan_draft(project, draft)
    assert is_cut_plan_draft_stale(project, draft) is False

    save_confirmed_voiceover_project_plan(project, _plan(project, title="Neuer Titel"))
    assert is_cut_plan_draft_stale(project, draft) is True


def _patch_project_selector(project: Project, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("otio_app.ui.project_context.list_projects", lambda: [project])
    monkeypatch.setattr(
        "otio_app.ui.project_context.get_project_by_id",
        lambda project_id: project if project_id == project.id else None,
    )
    monkeypatch.setattr("streamlit.session_state", {"active_project_id": project.id}, raising=False)


def test_ui_shows_staleness_warning_for_stale_draft(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _make_project(tmp_path)
    save_confirmed_voiceover_project_plan(project, _plan(project))
    draft = build_cut_plan_draft(project)
    save_cut_plan_draft(project, draft)
    save_confirmed_voiceover_project_plan(project, _plan(project, title="Geänderter Titel"))

    _patch_project_selector(project, monkeypatch)
    warnings: list[str] = []
    monkeypatch.setattr("streamlit.warning", lambda message, *a, **k: warnings.append(message))

    render_cut_plan_page()

    assert any("veraltet" in message for message in warnings)


def test_ui_shows_no_staleness_warning_for_fresh_draft(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _make_project(tmp_path)
    save_confirmed_voiceover_project_plan(project, _plan(project))
    draft = build_cut_plan_draft(project)
    save_cut_plan_draft(project, draft)

    _patch_project_selector(project, monkeypatch)
    warnings: list[str] = []
    monkeypatch.setattr("streamlit.warning", lambda message, *a, **k: warnings.append(message))

    render_cut_plan_page()

    assert not any("veraltet" in message for message in warnings)
