"""Phase 3: Dramaturgy-Tab — UI-Guard und Rendering-Smoke-Tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from otio_app.models import Project, ProjectMode
from otio_app.project_layout import get_edit_plan_dir, get_exports_dir
from otio_app.services.media_inventory_cache import media_cache_path, save_cached_media
from otio_app.ui.voiceover_generation.dramaturgy_tab import render_dramaturgy_page

_APPTEST_SCRIPT_PATH = (
    Path(__file__).parent / "_apptest_scripts" / "voiceover_gen_model_settings_repro.py"
)


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


def test_dramaturgy_page_has_both_plan_buttons(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zwei Planungsmodi: Geographie vs. abwechslungsreiche Dramaturgie."""
    project_id = "dram-two-buttons"
    project_root = tmp_path / "USA"
    (project_root / "Grand Canyon").mkdir(parents=True)

    monkeypatch.setenv("REPRO_ROOT", str(tmp_path))
    monkeypatch.setenv("REPRO_PROJECT_ID", project_id)
    monkeypatch.setenv(
        "REPRO_RENDER_FUNCTION",
        "otio_app.ui.voiceover_generation.dramaturgy_tab:render_dramaturgy_page",
    )
    monkeypatch.setenv("REPRO_SETUP", "none")
    monkeypatch.delenv("REPRO_STYLE_PROFILE_LIBRARY_NAME", raising=False)

    at = AppTest.from_file(str(_APPTEST_SCRIPT_PATH))
    at.run()
    assert not at.exception, at.exception

    button_labels = {button.label for button in at.button}
    assert "Dramaturgie nach Geographie planen" in button_labels
    assert "Abwechslungsreiche Dramaturgie planen" in button_labels
    assert "Dramaturgie ohne Thinking" not in button_labels

    captions = " ".join(caption.value for caption in at.caption)
    assert "70" in captions and "000" in captions
    assert "Geographie" in captions or "Reiseverlauf" in captions
    assert "Abwechslung" in captions


def test_dramaturgy_page_writes_no_edit_plan_documents(
    without_voiceover_project: Project,
) -> None:
    render_dramaturgy_page()
    assert not get_edit_plan_dir(without_voiceover_project.work_dir_path).exists()
    assert not get_exports_dir(without_voiceover_project.language_work_dir_path).exists()


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
    assert not (project.language_work_dir_path / "voiceover_generation").exists()


def test_inventory_metric_counts_folder_with_only_cached_data_no_flat_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression (Nutzerfeedback Juli 2026): 'im Dramaturgie wird angezeigt
    dass für keinen Ordner ein Inventory erkannt wurde', obwohl alle Assets
    analysiert waren. Ursache: die flache Inventar-JSON wird gelöscht, sobald
    der Ordner nicht vollständig 'grün' ist — obwohl im Analyse-Cache bereits
    erfolgreich analysierte Daten liegen, die die Dramaturgie-Planung selbst
    ohnehin verwenden würde. Die 'Mit Inventory'-Kennzahl muss diesen Ordner
    trotzdem zählen."""
    project_id = "dram-inventory-repro"
    project_root = tmp_path / "USA"
    folder_dir = project_root / "Grand Canyon"
    folder_dir.mkdir(parents=True)
    media_path = folder_dir / "clip.mp4"
    media_path.write_bytes(b"mp4")

    project = Project(
        id=project_id,
        name="Repro",
        project_root=str(project_root),
        work_dir=str(project_root / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )

    # Nur Analyse-Cache vorhanden — KEINE flache Inventar-JSON unter
    # _otio/inventory/Grand_Canyon.json (genau der gemeldete Fall).
    from otio_app.analysis_models import AssetMediaAnalysis
    from otio_app.project_layout import get_folder_inventory_path

    save_cached_media(
        media_cache_path(project, "Grand Canyon", media_path),
        AssetMediaAnalysis(path=str(media_path), description="Steile Felswand aus Cache"),
    )
    assert not get_folder_inventory_path(project.work_dir_path, "Grand Canyon").is_file()

    monkeypatch.setenv("REPRO_ROOT", str(tmp_path))
    monkeypatch.setenv("REPRO_PROJECT_ID", project_id)
    monkeypatch.setenv(
        "REPRO_RENDER_FUNCTION",
        "otio_app.ui.voiceover_generation.dramaturgy_tab:render_dramaturgy_page",
    )
    monkeypatch.setenv("REPRO_SETUP", "none")
    monkeypatch.delenv("REPRO_STYLE_PROFILE_LIBRARY_NAME", raising=False)

    at = AppTest.from_file(str(_APPTEST_SCRIPT_PATH))
    at.run()
    assert not at.exception, at.exception

    inventory_metric = next(m for m in at.metric if m.label == "Mit Inventory")
    assert inventory_metric.value == "1"
    assert not any(
        "für keinen Ordner liegt ein inventory vor" in error.value.lower() for error in at.error
    )
