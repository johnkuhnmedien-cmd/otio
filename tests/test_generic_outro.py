"""Tests für Generic-Outro-Auflösung."""

from __future__ import annotations

from pathlib import Path

from otio_app.models import Project
from otio_app.services.generic_outro import resolve_generic_outro_media


def _project(tmp_path: Path) -> Project:
    root = tmp_path / "USA"
    root.mkdir()
    return Project(
        id="generic-test",
        name="USA",
        project_root=str(root),
        work_dir=str(root / "_otio"),
        asset_subdir_names=["Florida Keys"],
        selected_asset_subdirs=["Florida Keys"],
    )


def test_resolve_generic_outro_from_project_folder(tmp_path: Path) -> None:
    project = _project(tmp_path)
    generic_dir = project.project_root_path / "Generic"
    generic_dir.mkdir()
    media = generic_dir / "generic.mp4"
    media.write_bytes(b"x")

    resolved = resolve_generic_outro_media(project)
    assert resolved == media.resolve()


def test_resolve_generic_outro_from_work_dir_fallback(tmp_path: Path) -> None:
    project = _project(tmp_path)
    media = project.work_dir_path / "generic_outro.mp4"
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(b"x")

    resolved = resolve_generic_outro_media(project)
    assert resolved == media.resolve()
