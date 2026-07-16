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

# Lesbare Schema-Versionen, die idempotent auf CURRENT migriert werden.
_LEGACY_SCHEMA_VERSIONS = frozenset({"1"})


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


def _ensure_base_tables(conn: sqlite3.Connection) -> None:
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


def _ensure_validation_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS validation_runs (
            run_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            import_id TEXT NOT NULL,
            selection_id TEXT NOT NULL,
            scan_id TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            total_assets INTEGER NOT NULL DEFAULT 0,
            processed_assets INTEGER NOT NULL DEFAULT 0,
            successful_assets INTEGER NOT NULL DEFAULT 0,
            failed_assets INTEGER NOT NULL DEFAULT 0,
            error_summary TEXT,
            FOREIGN KEY (import_id) REFERENCES selection_imports(import_id)
        );

        CREATE TABLE IF NOT EXISTS asset_validations (
            validation_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            asset_id TEXT NOT NULL,
            source_relative_path TEXT NOT NULL,
            status TEXT NOT NULL,
            checked_size_bytes INTEGER,
            checked_mtime_ns INTEGER,
            sha256 TEXT,
            media_kind TEXT,
            container_format TEXT,
            video_codec TEXT,
            audio_codec TEXT,
            width INTEGER,
            height INTEGER,
            duration_seconds REAL,
            frame_rate_numerator INTEGER,
            frame_rate_denominator INTEGER,
            audio_stream_count INTEGER,
            embedded_timecode TEXT,
            error_code TEXT,
            error_message TEXT,
            validated_at TEXT NOT NULL,
            duplicate_group_id TEXT,
            duplicate_hint TEXT,
            FOREIGN KEY (run_id) REFERENCES validation_runs(run_id),
            FOREIGN KEY (asset_id) REFERENCES assets(asset_id)
        );

        CREATE TABLE IF NOT EXISTS duplicate_groups (
            duplicate_group_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            member_count INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            hint TEXT NOT NULL DEFAULT 'potential_content_duplicate',
            UNIQUE (run_id, sha256),
            FOREIGN KEY (run_id) REFERENCES validation_runs(run_id)
        );

        CREATE INDEX IF NOT EXISTS idx_validation_runs_project_status
            ON validation_runs (project_id, status);

        CREATE INDEX IF NOT EXISTS idx_asset_validations_run
            ON asset_validations (run_id);

        CREATE INDEX IF NOT EXISTS idx_asset_validations_sha256
            ON asset_validations (run_id, sha256);
        """
    )


def _ensure_schema(conn: sqlite3.Connection) -> None:
    _ensure_base_tables(conn)
    now = datetime.now(timezone.utc).isoformat()
    row = conn.execute(
        "SELECT schema_version FROM registry_schema LIMIT 1"
    ).fetchone()

    if row is None:
        _ensure_validation_tables(conn)
        conn.execute(
            """
            INSERT INTO registry_schema (schema_version, initialized_at, updated_at)
            VALUES (?, ?, ?)
            """,
            (REGISTRY_SCHEMA_VERSION, now, now),
        )
        return

    current = str(row["schema_version"])
    if current == REGISTRY_SCHEMA_VERSION:
        _ensure_validation_tables(conn)
        conn.execute(
            "UPDATE registry_schema SET updated_at = ? WHERE schema_version = ?",
            (now, REGISTRY_SCHEMA_VERSION),
        )
        return

    if current in _LEGACY_SCHEMA_VERSIONS:
        # Idempotente Migration: bestehende Assets/Imports bleiben erhalten.
        _ensure_validation_tables(conn)
        conn.execute(
            """
            UPDATE registry_schema
            SET schema_version = ?, updated_at = ?
            WHERE schema_version = ?
            """,
            (REGISTRY_SCHEMA_VERSION, now, current),
        )
        return

    raise RegistryDatabaseError(
        f"Inkompatibles Registry-Schema: "
        f"{current} (erwartet {REGISTRY_SCHEMA_VERSION})"
    )


def foreign_keys_enabled(conn: sqlite3.Connection) -> bool:
    value = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    return bool(value)


def read_schema_version(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT schema_version FROM registry_schema LIMIT 1"
    ).fetchone()
    return None if row is None else str(row["schema_version"])
