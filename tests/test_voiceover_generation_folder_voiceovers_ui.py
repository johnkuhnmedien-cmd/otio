"""Phase 4: Folder-Voice-overs-Tab — UI-Guard, Sperre ohne Dramaturgie, Smoke-Tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

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
from otio_app.services.voiceover_generation.voiceover_author_service import (
    generate_folder_voiceover,
    load_folder_voiceovers_confirmed,
)
from otio_app.ui.voiceover_generation.folder_voiceovers_tab import render_folder_voiceovers_page

_AUTHOR_MODULE = "otio_app.services.voiceover_generation.voiceover_author_service"
_APPTEST_SCRIPT_PATH = (
    Path(__file__).parent / "_apptest_scripts" / "voiceover_gen_model_settings_repro.py"
)


def _make_project(tmp_path: Path, *, mode: ProjectMode) -> Project:
    project_root = tmp_path / "USA"
    (project_root / "Grand Canyon").mkdir(parents=True)
    return Project(
        id="fvo-ui-project",
        name="Folder Voiceover UI Test",
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
    monkeypatch.setattr(
        "streamlit.session_state", {"active_project_id": project.id}, raising=False
    )


def test_page_renders_without_exception_when_no_confirmed_dramaturgy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _make_project(tmp_path, mode=ProjectMode.WITHOUT_VOICEOVER)
    _patch_project_selector(project, monkeypatch)

    render_folder_voiceovers_page()  # darf nicht werfen


def test_page_locked_without_confirmed_dramaturgy_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _make_project(tmp_path, mode=ProjectMode.WITHOUT_VOICEOVER)
    _patch_project_selector(project, monkeypatch)

    render_folder_voiceovers_page()

    assert not (project.work_dir_path / "voiceover_generation" / "folder_voiceover_settings.json").exists()
    assert not get_edit_plan_dir(project.work_dir_path).exists()
    assert not get_exports_dir(project.work_dir_path).exists()


def test_page_guards_with_voiceover_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _make_project(tmp_path, mode=ProjectMode.WITH_VOICEOVER)
    _patch_project_selector(project, monkeypatch)

    render_folder_voiceovers_page()  # darf nicht werfen und darf nichts schreiben
    assert not (project.work_dir_path / "voiceover_generation").exists()


def test_page_renders_with_confirmed_dramaturgy_and_draft(
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
        provider="anthropic",
        model="claude-sonnet-5",
        raw_text='{"voiceover_text_full": "Text.", "sentence_items": []}',
    )
    with patch(f"{_AUTHOR_MODULE}.generate_plan_text_with_metadata", return_value=fake_response):
        generate_folder_voiceover(project, "Grand Canyon", provider="anthropic", model="claude-sonnet-5")

    _patch_project_selector(project, monkeypatch)
    render_folder_voiceovers_page()  # darf nicht werfen

    assert not get_edit_plan_dir(project.work_dir_path).exists()
    assert not get_exports_dir(project.work_dir_path).exists()


def _run_repro(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, project_id: str) -> AppTest:
    monkeypatch.setenv("REPRO_ROOT", str(tmp_path))
    monkeypatch.setenv("REPRO_PROJECT_ID", project_id)
    monkeypatch.setenv(
        "REPRO_RENDER_FUNCTION",
        "otio_app.ui.voiceover_generation.folder_voiceovers_tab:render_folder_voiceovers_page",
    )
    monkeypatch.setenv("REPRO_SETUP", "dramaturgy_and_voiceovers_confirmed")
    monkeypatch.delenv("REPRO_STYLE_PROFILE_LIBRARY_NAME", raising=False)
    at = AppTest.from_file(str(_APPTEST_SCRIPT_PATH))
    at.run()
    assert not at.exception, at.exception
    return at


def test_bulk_action_buttons_are_present_below_drafts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nutzerfeedback: 'Ich will unterhalb der Drafts alle gleichzeitig
    speichern, bestätigen, validieren etc. können.'"""
    at = _run_repro(tmp_path, monkeypatch, "fvo-bulk-ui-project")
    button_labels = {button.label for button in at.button}
    assert "Alle Texte speichern" in button_labels
    assert "Alle neu generieren" in button_labels
    assert "Alle validieren" in button_labels
    assert "Alle bestätigen" in button_labels
    assert "Alle Bestätigungen zurücknehmen" in button_labels

    subheaders = {subheader.value for subheader in at.subheader}
    assert "Alle Ordner gleichzeitig" in subheaders


def test_bulk_action_buttons_have_no_cancel_button(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nutzerfeedback: 'dann warten wir erstmal mit dem Abbrechen Button' —
    es darf (noch) keine Abbrechen-Schaltfläche für die Sammel-Aktionen geben."""
    at = _run_repro(tmp_path, monkeypatch, "fvo-bulk-ui-project")
    button_labels = {button.label for button in at.button}
    assert not any("abbrechen" in label.lower() for label in button_labels)


def test_bulk_confirm_all_button_click_confirms_all_folders_with_drafts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id = "fvo-bulk-confirm-project"
    at = _run_repro(tmp_path, monkeypatch, project_id)

    confirm_all_button = next(b for b in at.button if b.label == "Alle bestätigen")
    at = confirm_all_button.click().run()
    assert not at.exception, at.exception

    project = Project(
        id=project_id,
        name="Repro",
        project_root=str(tmp_path / "USA"),
        work_dir=str(tmp_path / "USA" / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )
    confirmed = load_folder_voiceovers_confirmed(project)
    assert "Grand Canyon" in {item.folder_name for item in confirmed.items}


def test_bulk_save_all_after_confirm_all_does_not_undo_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression (Nutzerfeedback Juli 2026): 'Wenn ich alle auf einmal
    bestätigen will kommt der Status Needs validation.' Reproduziert die
    exakte Reihenfolge: erst 'Alle bestätigen', DANACH 'Alle Texte
    speichern' (z. B. aus Gewohnheit) — der Text hat sich dabei nicht
    geändert, die Bestätigung darf NICHT zurückgesetzt werden."""
    project_id = "fvo-save-after-confirm-project"
    at = _run_repro(tmp_path, monkeypatch, project_id)

    confirm_all_button = next(b for b in at.button if b.label == "Alle bestätigen")
    at = confirm_all_button.click().run()
    assert not at.exception, at.exception

    save_all_button = next(b for b in at.button if b.label == "Alle Texte speichern")
    at = save_all_button.click().run()
    assert not at.exception, at.exception

    project = Project(
        id=project_id,
        name="Repro",
        project_root=str(tmp_path / "USA"),
        work_dir=str(tmp_path / "USA" / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )
    from otio_app.services.voiceover_generation.voiceover_author_service import (
        load_folder_voiceovers_draft,
    )

    draft_doc = load_folder_voiceovers_draft(project)
    draft = next(item for item in draft_doc.items if item.folder_name == "Grand Canyon")
    assert draft.status == "CONFIRMED"


def test_bulk_unconfirm_all_button_click_unconfirms_all_folders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id = "fvo-bulk-unconfirm-project"
    # Erster Lauf: Setup bestätigt "Grand Canyon" bereits (persistiert auf
    # Platte). Zweiter, separater Lauf mit REPRO_SETUP=none: das Setup-Skript
    # wiederholt das Bestätigen NICHT erneut, sodass der Klick unten nicht
    # durch einen automatischen Re-Setup-Rerun überschrieben wird.
    _run_repro(tmp_path, monkeypatch, project_id)

    monkeypatch.setenv("REPRO_SETUP", "none")
    at = AppTest.from_file(str(_APPTEST_SCRIPT_PATH))
    at.run()
    assert not at.exception, at.exception

    unconfirm_all_button = next(b for b in at.button if b.label == "Alle Bestätigungen zurücknehmen")
    assert not unconfirm_all_button.disabled
    at = unconfirm_all_button.click().run()
    assert not at.exception, at.exception

    project = Project(
        id=project_id,
        name="Repro",
        project_root=str(tmp_path / "USA"),
        work_dir=str(tmp_path / "USA" / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )
    confirmed = load_folder_voiceovers_confirmed(project)
    assert confirmed.items == []
