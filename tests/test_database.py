"""Tests für SQLite-Schema."""

from __future__ import annotations

from pathlib import Path

from otio_app.database import get_connection


def test_schema_creates_projects_table(temp_db_path: Path) -> None:
    conn = get_connection(temp_db_path)
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='projects'"
        ).fetchone()
        assert row is not None

        columns = {
            col["name"]
            for col in conn.execute("PRAGMA table_info(projects)").fetchall()
        }
        expected = {
            "id",
            "name",
            "project_root",
            "work_dir",
            "voice_over_subdir",
            "language",
            "frames_per_shot",
            "fps",
            "width",
            "height",
            "aspect_ratio",
            "target_platform",
            "status",
            "asset_subdir_names",
            "notes",
            "created_at",
            "updated_at",
        }
        assert expected.issubset(columns)
    finally:
        conn.close()
