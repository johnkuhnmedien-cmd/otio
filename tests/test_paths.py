"""Tests für Pfadnormalisierung und -validierung."""

from __future__ import annotations

from pathlib import Path

import pytest

from otio_app.paths import (
    PathValidationError,
    normalize_path,
    resolve_work_dir,
    validate_project_layout,
    validate_readonly_dir,
)
from otio_app.project_layout import default_work_dir, discover_asset_subdirs


def test_normalize_path_strips_quotes(temp_project_layout: dict[str, Path]) -> None:
    project_root = temp_project_layout["project_root"]
    quoted = f"'{project_root}'"
    result = normalize_path(quoted)
    assert result == project_root.resolve()


def test_normalize_path_resolves(temp_project_layout: dict[str, Path]) -> None:
    project_root = temp_project_layout["project_root"]
    result = normalize_path(str(project_root))
    assert result == project_root.resolve()
    assert result.is_dir()


def test_validate_readonly_dir_missing() -> None:
    with pytest.raises(PathValidationError, match="existiert nicht"):
        validate_readonly_dir(Path("/nonexistent/dir"))


def test_resolve_work_dir_default(temp_project_layout: dict[str, Path]) -> None:
    project_root = temp_project_layout["project_root"]
    work_dir = resolve_work_dir(project_root, None)
    assert work_dir == default_work_dir(project_root)


def test_validate_project_layout_rejects_project_root_as_work_dir(
    temp_project_layout: dict[str, Path],
) -> None:
    project_root = temp_project_layout["project_root"]
    with pytest.raises(PathValidationError, match="Projektordner"):
        validate_project_layout(project_root, project_root, "Voice over")


def test_discover_asset_subdirs(temp_project_layout: dict[str, Path]) -> None:
    project_root = temp_project_layout["project_root"]
    work_dir = default_work_dir(project_root)
    subdirs = discover_asset_subdirs(project_root, work_dir, "Voice over")
    names = {path.name for path in subdirs}
    assert names == {"Grand Canyon", "Yellowstone"}
