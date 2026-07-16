"""SQLite-Schema für die isolierte Discovery-V2-Asset-Registry."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from otio_app.discovery_v2.domain.asset_registry import REGISTRY_SCHEMA_VERSION
from otio_app.discovery_v2.paths import (
    assert_path_is_under_discovery_v2,
    get_discovery_v2_root,
)


class RegistryDatabaseError(ValueError):
    """Fehler beim Öffnen/Initialisieren der Registry-DB."""


def registry_dir(project_root: Path) -> Path:
    return get_discovery_v2_root(project_root) / "registry"


def registry_sqlite_path(project_root: Path) -> Path:
    return registry_dir(project_root) / "assets.sqlite3"


def registry_sqlite_relative_path() -> str:
    return "registry/assets.sqlite3"


def ensure_registry_dir(project_root: Path) -> Path:
    path = registry_dir(project_root)
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RegistryDatabaseError(
            f"Registry-Verzeichnis nicht beschreibbar: {path} ({exc})"
        ) from exc
    assert_path_is_under_discovery_v2(path, project_root)
    return path


def get_registry_connection(project_root: Path) -> sqlite3.Connection:
    """Öffnet die V2-Registry-DB und stellt Schema + PRAGMAs sicher."""
    ensure_registry_dir(project_root)
    db_path = registry_sqlite_path(project_root)
    assert_path_is_under_discovery_v2(db_path, project_root)
    try:
        conn = sqlite3.connect(str(db_path), timeout=5.0)
    except sqlite3.Error as exc:
        raise RegistryDatabaseError(
            f"Registry-SQLite nicht öffnenbar: {db_path} ({exc})"
        ) from exc
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        _ensure_schema(conn)
        conn.commit()
    except Exception:
        conn.close()
        raise
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS registry_schema (
            schema_version TEXT PRIMARY KEY,
            initialized_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS assets (
            asset_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            source_relative_path TEXT NOT NULL,
            source_group TEXT NOT NULL,
            file_name TEXT NOT NULL,
            extension TEXT NOT NULL,
            media_kind TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            mtime_ns INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (project_id, source_relative_path)
        );

        CREATE TABLE IF NOT EXISTS selection_imports (
            import_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            selection_id TEXT NOT NULL,
            scan_id TEXT NOT NULL,
            source_selection_relative_path TEXT NOT NULL,
            imported_at TEXT NOT NULL,
            status TEXT NOT NULL,
            selected_asset_count INTEGER NOT NULL,
            UNIQUE (project_id, selection_id)
        );

        CREATE TABLE IF NOT EXISTS selection_import_assets (
            import_id TEXT NOT NULL,
            asset_id TEXT NOT NULL,
            source_relative_path TEXT NOT NULL,
            PRIMARY KEY (import_id, asset_id),
            FOREIGN KEY (import_id) REFERENCES selection_imports(import_id),
            FOREIGN KEY (asset_id) REFERENCES assets(asset_id)
        );
        """
    )
    now = datetime.now(timezone.utc).isoformat()
    row = conn.execute(
        "SELECT schema_version FROM registry_schema LIMIT 1"
    ).fetchone()
    if row is None:
        conn.execute(
            """
            INSERT INTO registry_schema (schema_version, initialized_at, updated_at)
            VALUES (?, ?, ?)
            """,
            (REGISTRY_SCHEMA_VERSION, now, now),
        )
    else:
        if row["schema_version"] != REGISTRY_SCHEMA_VERSION:
            raise RegistryDatabaseError(
                f"Inkompatibles Registry-Schema: "
                f"{row['schema_version']} (erwartet {REGISTRY_SCHEMA_VERSION})"
            )
        conn.execute(
            "UPDATE registry_schema SET updated_at = ? WHERE schema_version = ?",
            (now, REGISTRY_SCHEMA_VERSION),
        )


def foreign_keys_enabled(conn: sqlite3.Connection) -> bool:
    value = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    return bool(value)


def read_schema_version(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT schema_version FROM registry_schema LIMIT 1"
    ).fetchone()
    return None if row is None else str(row["schema_version"])
