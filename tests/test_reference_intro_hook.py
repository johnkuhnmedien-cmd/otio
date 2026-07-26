"""DE-Referenz-Hook für andere Sprachen (nur Lesen / UI-Info)."""

from __future__ import annotations

from pathlib import Path

from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import (
    get_intro_hook_confirmed_path,
    get_language_work_dir,
)
from otio_app.services.voiceover_generation.intro_hook_service import (
    load_reference_intro_hook_text,
)
from otio_app.services.voiceover_generation.models import ConfirmedIntroHook


def _project(tmp_path: Path, *, language: str) -> Project:
    root = tmp_path / "USA"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    return Project(
        name="USA",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        language=language,
        asset_subdir_names=["Yellowstone"],
        selected_asset_subdirs=["Yellowstone"],
    )


def _write_de_confirmed(work: Path, text: str) -> None:
    de_dir = get_language_work_dir(work, "de")
    path = get_intro_hook_confirmed_path(de_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    hook = ConfirmedIntroHook(
        project_id="proj-de",
        language="de",
        hook_id="hook_001",
        hook_text=text,
        word_count=5,
        hook_type="cinematic_promise",
    )
    path.write_text(hook.model_dump_json(indent=2), encoding="utf-8")


def test_load_reference_intro_hook_text_for_fr(tmp_path: Path) -> None:
    project = _project(tmp_path, language="fr")
    _write_de_confirmed(Path(project.work_dir), "Amerika, wild und still.")
    assert (
        load_reference_intro_hook_text(project) == "Amerika, wild und still."
    )


def test_load_reference_skips_when_current_is_de(tmp_path: Path) -> None:
    project = _project(tmp_path, language="de")
    _write_de_confirmed(Path(project.work_dir), "Nur DE")
    assert load_reference_intro_hook_text(project) is None


def test_load_reference_missing_file(tmp_path: Path) -> None:
    project = _project(tmp_path, language="en")
    assert load_reference_intro_hook_text(project) is None
