"""Tests für SQLite-Schema."""

from __future__ import annotations

import sqlite3
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
            "project_mode",
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
            "selected_asset_subdirs",
            "notes",
            "created_at",
            "updated_at",
        }
        assert expected.issubset(columns)
    finally:
        conn.close()


def test_fresh_schema_defaults_project_mode_to_with_voiceover(temp_db_path: Path) -> None:
    conn = get_connection(temp_db_path)
    try:
        column = next(
            col
            for col in conn.execute("PRAGMA table_info(projects)").fetchall()
            if col["name"] == "project_mode"
        )
        assert column["dflt_value"] == "'with_voiceover'"
    finally:
        conn.close()


def test_migration_adds_project_mode_column_with_default(temp_db_path: Path) -> None:
    """Simuliert eine Bestandsdatenbank ohne project_mode-Spalte (Alt-Projekt)."""
    legacy_conn = sqlite3.connect(temp_db_path)
    try:
        legacy_conn.execute(
            """
            CREATE TABLE projects (
                id                  TEXT PRIMARY KEY,
                name                TEXT NOT NULL,
                project_root        TEXT NOT NULL,
                work_dir            TEXT NOT NULL,
                voice_over_subdir   TEXT NOT NULL DEFAULT 'Voice over',
                language            TEXT NOT NULL DEFAULT 'de',
                frames_per_shot     INTEGER NOT NULL DEFAULT 3,
                fps                 REAL NOT NULL DEFAULT 25.0,
                width               INTEGER NOT NULL DEFAULT 3840,
                height              INTEGER NOT NULL DEFAULT 2160,
                aspect_ratio        TEXT NOT NULL DEFAULT '16:9',
                target_platform     TEXT NOT NULL DEFAULT 'YouTube',
                status              TEXT NOT NULL DEFAULT 'DRAFT',
                asset_subdir_names  TEXT NOT NULL DEFAULT '[]',
                selected_asset_subdirs TEXT NOT NULL DEFAULT '[]',
                notes               TEXT,
                created_at          TEXT NOT NULL,
                updated_at          TEXT NOT NULL
            )
            """
        )
        legacy_conn.execute(
            """
            INSERT INTO projects (
                id, name, project_root, work_dir, created_at, updated_at
            ) VALUES ('old-1', 'Altes Projekt', '/tmp/old', '/tmp/old/_otio', 't', 't')
            """
        )
        legacy_conn.commit()
    finally:
        legacy_conn.close()

    # get_connection() führt die Migration beim Öffnen automatisch aus.
    conn = get_connection(temp_db_path)
    try:
        row = conn.execute(
            "SELECT project_mode FROM projects WHERE id = 'old-1'"
        ).fetchone()
        assert row is not None
        assert row["project_mode"] == "with_voiceover"
    finally:
        conn.close()
