"""Compat: YouTube-Metadaten-Pfade auch in älteren project_layout-Kopien."""

from __future__ import annotations

from pathlib import Path

import otio_app.project_layout as layout
from otio_app.youtube_layout_compat import install_youtube_metadata_paths


def test_install_adds_missing_youtube_metadata_paths(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delattr(layout, "get_project_youtube_metadata_path")
    monkeypatch.delattr(layout, "get_project_youtube_metadata_text_path")
    assert getattr(layout, "get_project_youtube_metadata_path", None) is None

    install_youtube_metadata_paths()

    from otio_app.project_layout import get_project_youtube_metadata_path
    from otio_app.project_layout import get_project_youtube_metadata_text_path

    assert get_project_youtube_metadata_path(tmp_path, "Voice over", "it") == (
        tmp_path / "Voice over" / "IT" / "youtube_metadata.json"
    )
    assert get_project_youtube_metadata_text_path(tmp_path, "Voice over", "it") == (
        tmp_path / "youtube_metadata_IT.txt"
    )


def test_app_installs_youtube_layout_helpers_before_ui_import() -> None:
    src = Path("app.py").read_text(encoding="utf-8")
    install_at = src.find("install_youtube_metadata_paths()")
    ui_at = src.find("from otio_app.ui.language_sibling_ui import")
    assert install_at != -1
    assert ui_at != -1
    assert install_at < ui_at
