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

_INDEX_ROOT_LANGUAGE = "idx_projects_root_language"
_INDEX_ROOT_LANGUAGE_MODE = "idx_projects_root_language_mode"


def _index_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='projects'"
    ).fetchall()
    return {row[0] for row in rows if row[0]}


def _ensure_root_language_mode_unique_index(conn: sqlite3.Connection) -> None:
    """Unique Key: project_root + language + project_mode (idempotent).

    Ersetzt den älteren Index ohne ``project_mode``, damit am selben Ordner
    und in derselben Sprache unterschiedliche Pipeline-Modi parallel existieren
    können (Classic / Without-VO / Discovery V2).
    """
    names = _index_names(conn)
    if _INDEX_ROOT_LANGUAGE_MODE in names:
        if _INDEX_ROOT_LANGUAGE in names:
            conn.execute(f"DROP INDEX IF EXISTS {_INDEX_ROOT_LANGUAGE}")
        return

    # Ohne project_mode-Spalte (sehr alte DB vor Spalten-Migration) nicht umstellen.
    columns = {row[1] for row in conn.execute("PRAGMA table_info(projects)").fetchall()}
    if "project_mode" not in columns:
        if _INDEX_ROOT_LANGUAGE not in names:
            duplicates = conn.execute(
                """
                SELECT project_root, lower(language) AS lang, COUNT(*) AS cnt
                FROM projects
                GROUP BY project_root, lower(language)
                HAVING cnt > 1
                """
            ).fetchall()
            if not duplicates:
                conn.execute(
                    f"""
                    CREATE UNIQUE INDEX IF NOT EXISTS {_INDEX_ROOT_LANGUAGE}
                    ON projects(project_root, lower(language))
                    """
                )
        return

    mode_duplicates = conn.execute(
        """
        SELECT project_root, lower(language) AS lang, project_mode, COUNT(*) AS cnt
        FROM projects
        GROUP BY project_root, lower(language), project_mode
        HAVING cnt > 1
        """
    ).fetchall()
    if mode_duplicates:
        # Unsichere Datenlage — alten Index belassen, keinen Datenverlust riskieren.
        if _INDEX_ROOT_LANGUAGE not in names:
            legacy_duplicates = conn.execute(
                """
                SELECT project_root, lower(language) AS lang, COUNT(*) AS cnt
                FROM projects
                GROUP BY project_root, lower(language)
                HAVING cnt > 1
                """
            ).fetchall()
            if not legacy_duplicates:
                conn.execute(
                    f"""
                    CREATE UNIQUE INDEX IF NOT EXISTS {_INDEX_ROOT_LANGUAGE}
                    ON projects(project_root, lower(language))
                    """
                )
        return

    if _INDEX_ROOT_LANGUAGE in names:
        conn.execute(f"DROP INDEX IF EXISTS {_INDEX_ROOT_LANGUAGE}")

    conn.execute(
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS {_INDEX_ROOT_LANGUAGE_MODE}
        ON projects(project_root, lower(language), project_mode)
        """
    )


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """Aktualisiert ältere Datenbankschemas auf das neue Projektmodell."""
    rows = conn.execute("PRAGMA table_info(projects)").fetchall()
    if not rows:
        conn.executescript(SCHEMA)
    else:
        column_names = {row[1] for row in rows}
        if "project_root" not in column_names:
            conn.execute("DROP TABLE projects")
            conn.executescript(SCHEMA)
        else:
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

    _ensure_root_language_mode_unique_index(conn)


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
