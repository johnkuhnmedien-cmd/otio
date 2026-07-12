"""Phase 1: Platzhalterseiten für "Projekt ohne Voice-Over".

Sichert zu:
- Die neuen Platzhalterseiten lassen sich rendern, ohne Exceptions zu werfen.
- Sie erzeugen KEINE EditPlanDocuments und fassen keine Produktionspfade an.
- Ihr Quellcode referenziert keine der verbotenen Produktions-Funktionen
  (save_edit_plan, build_edit_plan, _set_draft, export_otio_timeline,
  merge_confirmed_edit_plans) — ein struktureller Schutz gegen künftige,
  versehentliche Kopplung an den Produktionspfad.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from pathlib import Path

import pytest

import otio_app.ui.voiceover_generation as voiceover_generation_pkg
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import get_edit_plan_dir, get_exports_dir, get_folder_edit_plan_path
from otio_app.ui.voiceover_generation.audio_tab import render_audio_page
from otio_app.ui.voiceover_generation.dramaturgy_tab import render_dramaturgy_page
from otio_app.ui.voiceover_generation.final_output_tab import render_final_output_page
from otio_app.ui.voiceover_generation.folder_voiceovers_tab import render_folder_voiceovers_page
from otio_app.ui.voiceover_generation.intro_tab import render_intro_page
from otio_app.ui.voiceover_generation.project_brief_tab import render_project_brief_page
from otio_app.ui.voiceover_generation.style_references_tab import render_style_references_page

FORBIDDEN_SYMBOLS = (
    "save_edit_plan",
    "build_edit_plan",
    "_set_draft",
    "export_otio_timeline",
    "merge_confirmed_edit_plans",
    "persist_accepted_edit_plan",
)

ALL_PLACEHOLDER_RENDER_FNS = (
    render_project_brief_page,
    render_style_references_page,
    render_dramaturgy_page,
    render_folder_voiceovers_page,
    render_intro_page,
    render_audio_page,
    render_final_output_page,
)


def _make_without_voiceover_project(tmp_path: Path) -> Project:
    project_root = tmp_path / "USA"
    (project_root / "Grand Canyon").mkdir(parents=True)
    work_dir = project_root / "_otio"
    return Project(
        id="p1",
        name="Ohne VO Projekt",
        project_root=str(project_root),
        work_dir=str(work_dir),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )


@pytest.fixture
def without_voiceover_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Project:
    project = _make_without_voiceover_project(tmp_path)
    monkeypatch.setattr(
        "otio_app.ui.project_context.list_projects", lambda: [project]
    )
    monkeypatch.setattr(
        "otio_app.ui.project_context.get_project_by_id",
        lambda project_id: project if project_id == project.id else None,
    )
    monkeypatch.setattr("streamlit.session_state", {"active_project_id": project.id}, raising=False)
    return project


@pytest.mark.parametrize("render_fn", ALL_PLACEHOLDER_RENDER_FNS)
def test_placeholder_page_renders_without_exception(
    render_fn, without_voiceover_project: Project
) -> None:
    render_fn()  # darf nicht werfen


@pytest.mark.parametrize("render_fn", ALL_PLACEHOLDER_RENDER_FNS)
def test_placeholder_page_writes_no_edit_plan_document(
    render_fn, without_voiceover_project: Project
) -> None:
    render_fn()

    assert not get_edit_plan_dir(without_voiceover_project.work_dir_path).exists()
    assert not get_exports_dir(without_voiceover_project.language_work_dir_path).exists()
    assert not get_folder_edit_plan_path(without_voiceover_project.language_work_dir_path, "Grand Canyon"
    ).exists()


def test_placeholder_pages_never_reference_production_edit_plan_functions() -> None:
    """Statischer Schutz: Kein Modul dieser Pipeline darf Produktions-Symbole
    aus edit_plan_builder.py / otio_exporter.py / edit_plan.py referenzieren."""
    import re

    package_path = Path(voiceover_generation_pkg.__file__).parent
    for module_info in pkgutil.iter_modules([str(package_path)]):
        module = importlib.import_module(
            f"otio_app.ui.voiceover_generation.{module_info.name}"
        )
        source = inspect.getsource(module)
        for forbidden in FORBIDDEN_SYMBOLS:
            # Wort-Grenzen statt Substring-Suche: vermeidet False Positives
            # durch legitime, isolierte Bridge-Funktionsnamen (Phase 9.1), die
            # z. B. 'build_edit_plan' nur als Präfix enthalten.
            assert not re.search(rf"\b{re.escape(forbidden)}\b", source), (
                f"{module.__name__} referenziert verbotenes Produktions-Symbol "
                f"'{forbidden}' — der neue Workflow muss vom Produktionspfad "
                "getrennt bleiben."
            )


def test_placeholder_pages_do_not_import_production_modules() -> None:
    """Kein Import von edit_plan_builder oder otio_exporter in dieser Pipeline."""
    package_path = Path(voiceover_generation_pkg.__file__).parent
    forbidden_modules = ("edit_plan_builder", "otio_exporter")
    for module_info in pkgutil.iter_modules([str(package_path)]):
        module = importlib.import_module(
            f"otio_app.ui.voiceover_generation.{module_info.name}"
        )
        source = inspect.getsource(module)
        for forbidden_module in forbidden_modules:
            assert forbidden_module not in source, (
                f"{module.__name__} importiert/referenziert '{forbidden_module}' — "
                "verboten für die Diagnose-/Generierungs-Pipeline."
            )


def test_placeholder_page_warns_for_with_voiceover_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Schutz gegen Fehlbedienung: Diese Seiten sollen nur für
    "Projekt ohne Voice-Over" sinnvoll benutzt werden."""
    project_root = tmp_path / "USA"
    project_root.mkdir()
    project = Project(
        id="p2",
        name="Mit VO Projekt",
        project_root=str(project_root),
        work_dir=str(project_root / "_otio"),
        project_mode=ProjectMode.WITH_VOICEOVER,
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )
    monkeypatch.setattr("otio_app.ui.project_context.list_projects", lambda: [project])
    monkeypatch.setattr(
        "otio_app.ui.project_context.get_project_by_id",
        lambda project_id: project if project_id == project.id else None,
    )
    monkeypatch.setattr("streamlit.session_state", {"active_project_id": project.id}, raising=False)

    # Darf nicht werfen — zeigt stattdessen eine Warnung und tut sonst nichts.
    render_project_brief_page()
