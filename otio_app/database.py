"""SQLite-Datenbankzugriff."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from otio_app.config import ensure_data_dir, get_db_path

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    project_root        TEXT NOT NULL,
    work_dir            TEXT NOT NULL,
    project_mode        TEXT NOT NULL DEFAULT 'with_voiceover',
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
);
"""


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """Aktualisiert ältere Datenbankschemas auf das neue Projektmodell."""
    rows = conn.execute("PRAGMA table_info(projects)").fetchall()
    if not rows:
        conn.executescript(SCHEMA)
        return

    column_names = {row[1] for row in rows}
    if "project_root" not in column_names:
        conn.execute("DROP TABLE projects")
        conn.executescript(SCHEMA)
        return

    if "asset_subdir_names" not in column_names:
        conn.execute(
            "ALTER TABLE projects ADD COLUMN asset_subdir_names TEXT NOT NULL DEFAULT '[]'"
        )
        column_names.add("asset_subdir_names")

    if "selected_asset_subdirs" not in column_names:
        conn.execute(
            "ALTER TABLE projects ADD COLUMN selected_asset_subdirs TEXT NOT NULL DEFAULT '[]'"
        )
        conn.execute(
            """
            UPDATE projects
            SET selected_asset_subdirs = asset_subdir_names
            WHERE selected_asset_subdirs = '[]' AND asset_subdir_names != '[]'
            """
        )

    if "project_mode" not in column_names:
        # Bestandsprojekte sind ausnahmslos der bisherige Workflow — der neue
        # Diagnose-/Generierungsworkflow existierte zum Zeitpunkt ihrer Anlage nicht.
        conn.execute(
            "ALTER TABLE projects ADD COLUMN project_mode TEXT NOT NULL DEFAULT 'with_voiceover'"
        )


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """Öffnet eine SQLite-Verbindung und stellt das Schema sicher."""
    path = db_path or get_db_path()
    if db_path is None:
        ensure_data_dir()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    _migrate_schema(conn)
    conn.commit()
    return conn
