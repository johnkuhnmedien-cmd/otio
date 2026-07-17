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
_LEGACY_SCHEMA_VERSIONS = frozenset(
    {
        "1",
        "2",
        "3",
        "4",
        "5",
        "6",
        "7",
        "8",
        "9",
        "10",
        "11",
        "12",
        "13",
        "14",
        "15",
    }
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
            audio_channel_count INTEGER,
            embedded_timecode TEXT,
            pixel_format TEXT,
            bit_depth INTEGER,
            rotation_degrees REAL,
            image_format TEXT,
            image_mode TEXT,
            image_frame_count INTEGER,
            has_alpha INTEGER,
            has_icc_profile INTEGER,
            exif_orientation INTEGER,
            image_bit_depth INTEGER,
            image_is_bigtiff INTEGER,
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


def _ensure_validation_profile_columns(conn: sqlite3.Connection) -> None:
    """Idempotent: Profilfelder an bestehende asset_validations anfügen."""
    rows = conn.execute("PRAGMA table_info(asset_validations)").fetchall()
    columns = {str(row[1]) for row in rows}
    if "pixel_format" not in columns:
        conn.execute("ALTER TABLE asset_validations ADD COLUMN pixel_format TEXT")
    if "bit_depth" not in columns:
        conn.execute("ALTER TABLE asset_validations ADD COLUMN bit_depth INTEGER")
    if "audio_channel_count" not in columns:
        conn.execute(
            "ALTER TABLE asset_validations ADD COLUMN audio_channel_count INTEGER"
        )
    if "rotation_degrees" not in columns:
        conn.execute(
            "ALTER TABLE asset_validations ADD COLUMN rotation_degrees REAL"
        )
    _ensure_validation_image_columns(conn)


def _ensure_validation_image_columns(conn: sqlite3.Connection) -> None:
    """Idempotent: Bildfelder an bestehende asset_validations anfügen (Schema 10)."""
    rows = conn.execute("PRAGMA table_info(asset_validations)").fetchall()
    columns = {str(row[1]) for row in rows}
    for name, decl in (
        ("image_format", "TEXT"),
        ("image_mode", "TEXT"),
        ("image_frame_count", "INTEGER"),
        ("has_alpha", "INTEGER"),
        ("has_icc_profile", "INTEGER"),
        ("exif_orientation", "INTEGER"),
        ("image_bit_depth", "INTEGER"),
        ("image_is_bigtiff", "INTEGER"),
    ):
        if name not in columns:
            conn.execute(f"ALTER TABLE asset_validations ADD COLUMN {name} {decl}")


def _ensure_intake_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS intake_plans (
            plan_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            import_id TEXT NOT NULL,
            selection_id TEXT NOT NULL,
            scan_id TEXT NOT NULL,
            validation_run_id TEXT NOT NULL,
            planner_version TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            total_assets INTEGER NOT NULL DEFAULT 0,
            copy_count INTEGER NOT NULL DEFAULT 0,
            remux_count INTEGER NOT NULL DEFAULT 0,
            transcode_count INTEGER NOT NULL DEFAULT 0,
            blocked_count INTEGER NOT NULL DEFAULT 0,
            duplicate_warning_count INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (import_id) REFERENCES selection_imports(import_id),
            FOREIGN KEY (validation_run_id) REFERENCES validation_runs(run_id)
        );

        CREATE TABLE IF NOT EXISTS intake_plan_assets (
            plan_id TEXT NOT NULL,
            asset_id TEXT NOT NULL,
            validation_id TEXT NOT NULL,
            source_sha256 TEXT,
            source_relative_path TEXT NOT NULL,
            source_group TEXT NOT NULL,
            media_kind TEXT NOT NULL,
            planned_action TEXT NOT NULL,
            status TEXT NOT NULL,
            reason_code TEXT NOT NULL,
            reason_detail TEXT NOT NULL,
            proposed_target_extension TEXT,
            processing_profile_version TEXT NOT NULL,
            duplicate_group_id TEXT,
            PRIMARY KEY (plan_id, asset_id),
            FOREIGN KEY (plan_id) REFERENCES intake_plans(plan_id),
            FOREIGN KEY (asset_id) REFERENCES assets(asset_id),
            FOREIGN KEY (validation_id) REFERENCES asset_validations(validation_id)
        );

        CREATE INDEX IF NOT EXISTS idx_intake_plans_project_created
            ON intake_plans (project_id, created_at DESC);

        CREATE INDEX IF NOT EXISTS idx_intake_plan_assets_plan
            ON intake_plan_assets (plan_id);
        """
    )


def _ensure_copy_intake_tables(conn: sqlite3.Connection) -> None:
    """Phase 7B: Intake-Runs und Working-Media (nur Copy)."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS intake_runs (
            run_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            plan_id TEXT NOT NULL,
            import_id TEXT NOT NULL,
            selection_id TEXT NOT NULL,
            scan_id TEXT NOT NULL,
            validation_run_id TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            total_assets INTEGER NOT NULL DEFAULT 0,
            processed_assets INTEGER NOT NULL DEFAULT 0,
            succeeded_assets INTEGER NOT NULL DEFAULT 0,
            failed_assets INTEGER NOT NULL DEFAULT 0,
            skipped_assets INTEGER NOT NULL DEFAULT 0,
            error_summary TEXT,
            worker_version TEXT NOT NULL,
            scope TEXT NOT NULL DEFAULT 'copy_only',
            copied_assets INTEGER NOT NULL DEFAULT 0,
            remuxed_assets INTEGER NOT NULL DEFAULT 0,
            transcoded_assets INTEGER NOT NULL DEFAULT 0,
            converted_assets INTEGER NOT NULL DEFAULT 0,
            reused_assets INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (plan_id) REFERENCES intake_plans(plan_id),
            FOREIGN KEY (import_id) REFERENCES selection_imports(import_id),
            FOREIGN KEY (validation_run_id) REFERENCES validation_runs(run_id)
        );

        CREATE TABLE IF NOT EXISTS intake_run_assets (
            run_asset_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            plan_id TEXT NOT NULL,
            asset_id TEXT NOT NULL,
            source_relative_path TEXT NOT NULL,
            source_group TEXT NOT NULL,
            media_kind TEXT NOT NULL,
            planned_action TEXT NOT NULL,
            status TEXT NOT NULL,
            source_sha256 TEXT,
            output_sha256 TEXT,
            working_relative_path TEXT,
            error_code TEXT,
            error_message TEXT,
            processed_at TEXT,
            UNIQUE (run_id, asset_id),
            FOREIGN KEY (run_id) REFERENCES intake_runs(run_id),
            FOREIGN KEY (plan_id) REFERENCES intake_plans(plan_id),
            FOREIGN KEY (asset_id) REFERENCES assets(asset_id)
        );

        CREATE TABLE IF NOT EXISTS working_media (
            working_media_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            asset_id TEXT NOT NULL,
            plan_id TEXT NOT NULL,
            intake_run_id TEXT NOT NULL,
            source_relative_path TEXT NOT NULL,
            working_relative_path TEXT NOT NULL,
            source_sha256 TEXT NOT NULL,
            output_sha256 TEXT NOT NULL,
            media_kind TEXT NOT NULL,
            extension TEXT NOT NULL,
            action TEXT NOT NULL DEFAULT 'copy',
            processing_profile_version TEXT NOT NULL DEFAULT 'copy-v1',
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (
                project_id,
                asset_id,
                source_sha256,
                action,
                processing_profile_version
            ),
            FOREIGN KEY (asset_id) REFERENCES assets(asset_id),
            FOREIGN KEY (plan_id) REFERENCES intake_plans(plan_id),
            FOREIGN KEY (intake_run_id) REFERENCES intake_runs(run_id)
        );

        CREATE INDEX IF NOT EXISTS idx_intake_runs_project_status
            ON intake_runs (project_id, status);

        CREATE INDEX IF NOT EXISTS idx_intake_run_assets_run
            ON intake_run_assets (run_id);

        CREATE INDEX IF NOT EXISTS idx_working_media_project
            ON working_media (project_id);
        """
    )
    _migrate_working_media_schema(conn)
    _migrate_intake_runs_scope(conn)
    _migrate_intake_runs_action_counters(conn)


def _migrate_intake_runs_scope(conn: sqlite3.Connection) -> None:
    """Idempotent: scope-Spalte für Copy-/Remux-Trennung."""
    row = conn.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type='table' AND name='intake_runs'
        """
    ).fetchone()
    if row is None:
        return
    cols = {
        str(r[1]) for r in conn.execute("PRAGMA table_info(intake_runs)").fetchall()
    }
    if "scope" not in cols:
        conn.execute(
            "ALTER TABLE intake_runs ADD COLUMN scope TEXT NOT NULL DEFAULT 'copy_only'"
        )


def _migrate_intake_runs_action_counters(conn: sqlite3.Connection) -> None:
    """Idempotent: getrennte Copy-/Remux-/Transcode-/Reuse-Zähler."""
    row = conn.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type='table' AND name='intake_runs'
        """
    ).fetchone()
    if row is None:
        return
    cols = {
        str(r[1]) for r in conn.execute("PRAGMA table_info(intake_runs)").fetchall()
    }
    for column in (
        "copied_assets",
        "remuxed_assets",
        "transcoded_assets",
        "converted_assets",
        "reused_assets",
    ):
        if column not in cols:
            conn.execute(
                f"ALTER TABLE intake_runs ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0"
            )


def _migrate_working_media_schema(conn: sqlite3.Connection) -> None:
    """Idempotent: Unique-Key und Profilspalten für historische Hash-Versionen."""
    row = conn.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type='table' AND name='working_media'
        """
    ).fetchone()
    if row is None:
        return

    cols = {
        str(r[1])
        for r in conn.execute("PRAGMA table_info(working_media)").fetchall()
    }
    indexes = {
        str(r[1])
        for r in conn.execute("PRAGMA index_list(working_media)").fetchall()
    }
    # sqlite autoindex names vary; prüfe Unique-Index-Spalten.
    has_versioned_unique = False
    for idx_name in indexes:
        idx_cols = [
            str(r[2])
            for r in conn.execute(f"PRAGMA index_info('{idx_name}')").fetchall()
        ]
        if idx_cols == [
            "project_id",
            "asset_id",
            "source_sha256",
            "action",
            "processing_profile_version",
        ]:
            has_versioned_unique = True
            break

    needs_rebuild = (
        "action" not in cols
        or "processing_profile_version" not in cols
        or not has_versioned_unique
    )
    if not needs_rebuild:
        return

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS working_media__v6 (
            working_media_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            asset_id TEXT NOT NULL,
            plan_id TEXT NOT NULL,
            intake_run_id TEXT NOT NULL,
            source_relative_path TEXT NOT NULL,
            working_relative_path TEXT NOT NULL,
            source_sha256 TEXT NOT NULL,
            output_sha256 TEXT NOT NULL,
            media_kind TEXT NOT NULL,
            extension TEXT NOT NULL,
            action TEXT NOT NULL DEFAULT 'copy',
            processing_profile_version TEXT NOT NULL DEFAULT 'copy-v1',
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (
                project_id,
                asset_id,
                source_sha256,
                action,
                processing_profile_version
            ),
            FOREIGN KEY (asset_id) REFERENCES assets(asset_id),
            FOREIGN KEY (plan_id) REFERENCES intake_plans(plan_id),
            FOREIGN KEY (intake_run_id) REFERENCES intake_runs(run_id)
        );
        """
    )
    # Spalten robust lesen — fehlende Profilfelder mit Defaults füllen.
    select_action = "action" if "action" in cols else "'copy'"
    select_profile = (
        "processing_profile_version"
        if "processing_profile_version" in cols
        else "'copy-v1'"
    )
    # Legacy status ready → completed
    conn.execute(
        f"""
        INSERT OR IGNORE INTO working_media__v6 (
            working_media_id, project_id, asset_id, plan_id, intake_run_id,
            source_relative_path, working_relative_path, source_sha256,
            output_sha256, media_kind, extension, action,
            processing_profile_version, status, created_at, updated_at
        )
        SELECT
            working_media_id, project_id, asset_id, plan_id, intake_run_id,
            source_relative_path, working_relative_path, source_sha256,
            output_sha256, media_kind, extension,
            {select_action},
            {select_profile},
            CASE WHEN status = 'ready' THEN 'completed' ELSE status END,
            created_at, updated_at
        FROM working_media
        """
    )
    conn.execute("DROP TABLE working_media")
    conn.execute("ALTER TABLE working_media__v6 RENAME TO working_media")
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_working_media_project
            ON working_media (project_id)
        """
    )


def _ensure_analysis_tables(conn: sqlite3.Connection) -> None:
    """Phase 8A/8B: Analysis-Runs, Identities, Technical Shots, Frames."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS analysis_runs (
            run_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            scope TEXT NOT NULL,
            analysis_profile_version TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            total_assets INTEGER NOT NULL DEFAULT 0,
            prepared_assets INTEGER NOT NULL DEFAULT 0,
            reused_assets INTEGER NOT NULL DEFAULT 0,
            not_applicable_assets INTEGER NOT NULL DEFAULT 0,
            failed_assets INTEGER NOT NULL DEFAULT 0,
            interrupted_assets INTEGER NOT NULL DEFAULT 0,
            error_summary TEXT
        );

        CREATE TABLE IF NOT EXISTS analysis_run_assets (
            run_id TEXT NOT NULL,
            asset_id TEXT NOT NULL,
            working_media_id TEXT NOT NULL,
            validation_id TEXT NOT NULL,
            source_sha256 TEXT NOT NULL,
            output_sha256 TEXT NOT NULL,
            processing_profile_version TEXT NOT NULL,
            analysis_profile_version TEXT NOT NULL,
            media_kind TEXT NOT NULL,
            status TEXT NOT NULL,
            error_code TEXT,
            error_message TEXT,
            created_at TEXT,
            completed_at TEXT,
            analysis_identity_id TEXT,
            UNIQUE (run_id, asset_id, working_media_id),
            FOREIGN KEY (run_id) REFERENCES analysis_runs(run_id),
            FOREIGN KEY (asset_id) REFERENCES assets(asset_id)
        );

        CREATE TABLE IF NOT EXISTS analysis_identities (
            analysis_identity_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            asset_id TEXT NOT NULL,
            working_media_id TEXT NOT NULL,
            output_sha256 TEXT NOT NULL,
            processing_profile_version TEXT NOT NULL,
            analysis_profile_version TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE (
                project_id,
                asset_id,
                working_media_id,
                output_sha256,
                processing_profile_version,
                analysis_profile_version
            )
        );

        CREATE TABLE IF NOT EXISTS technical_shots (
            shot_id TEXT PRIMARY KEY,
            analysis_identity_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            asset_id TEXT NOT NULL,
            working_media_id TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            start_seconds REAL NOT NULL,
            end_seconds REAL NOT NULL,
            duration_seconds REAL NOT NULL,
            detection_profile_version TEXT NOT NULL,
            created_at TEXT NOT NULL,
            CHECK (ordinal >= 0),
            CHECK (start_seconds >= 0),
            CHECK (end_seconds > start_seconds),
            CHECK (duration_seconds > 0),
            UNIQUE (
                analysis_identity_id,
                detection_profile_version,
                ordinal
            ),
            FOREIGN KEY (analysis_identity_id)
                REFERENCES analysis_identities(analysis_identity_id)
        );

        CREATE TABLE IF NOT EXISTS representative_frames (
            frame_id TEXT PRIMARY KEY,
            analysis_identity_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            asset_id TEXT NOT NULL,
            working_media_id TEXT NOT NULL,
            shot_id TEXT,
            ordinal INTEGER NOT NULL,
            timestamp_seconds REAL,
            relative_path TEXT NOT NULL,
            frame_sha256 TEXT NOT NULL,
            pixel_sha256 TEXT NOT NULL,
            file_size_bytes INTEGER NOT NULL,
            width INTEGER NOT NULL,
            height INTEGER NOT NULL,
            sampling_profile_version TEXT NOT NULL,
            brightness_mean REAL NOT NULL,
            black_fraction REAL NOT NULL,
            sharpness_score REAL NOT NULL,
            is_black INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            CHECK (ordinal >= 0),
            CHECK (file_size_bytes >= 0),
            CHECK (width > 0),
            CHECK (height > 0),
            UNIQUE (
                analysis_identity_id,
                sampling_profile_version,
                ordinal
            ),
            FOREIGN KEY (analysis_identity_id)
                REFERENCES analysis_identities(analysis_identity_id),
            FOREIGN KEY (shot_id) REFERENCES technical_shots(shot_id)
        );

        CREATE INDEX IF NOT EXISTS idx_analysis_runs_project_status
            ON analysis_runs (project_id, status);

        CREATE INDEX IF NOT EXISTS idx_analysis_run_assets_run
            ON analysis_run_assets (run_id);

        CREATE INDEX IF NOT EXISTS idx_analysis_identities_project
            ON analysis_identities (project_id, asset_id);

        CREATE INDEX IF NOT EXISTS idx_technical_shots_identity
            ON technical_shots (analysis_identity_id, detection_profile_version);

        CREATE INDEX IF NOT EXISTS idx_representative_frames_identity
            ON representative_frames (
                analysis_identity_id, sampling_profile_version
            );
        """
    )
    _ensure_analysis_run_columns(conn)


def _ensure_model_analysis_tables(conn: sqlite3.Connection) -> None:
    """Phase 8C: fake vision consent, attempts, and observations."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS analysis_consent_events (
            consent_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            frame_count INTEGER NOT NULL,
            total_bytes INTEGER NOT NULL,
            acknowledged INTEGER NOT NULL,
            provider TEXT NOT NULL,
            model_identifier TEXT NOT NULL,
            gateway_version TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            response_schema_version TEXT NOT NULL,
            CHECK (frame_count >= 0),
            CHECK (total_bytes >= 0),
            CHECK (acknowledged IN (0, 1)),
            FOREIGN KEY (run_id) REFERENCES analysis_runs(run_id)
        );

        CREATE TABLE IF NOT EXISTS model_analysis_attempts (
            attempt_id TEXT PRIMARY KEY,
            analysis_identity_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            asset_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            model_identifier TEXT NOT NULL,
            gateway_version TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            response_schema_version TEXT NOT NULL,
            status TEXT NOT NULL,
            attempt_number INTEGER NOT NULL,
            error_code TEXT,
            error_message TEXT,
            frame_count INTEGER NOT NULL,
            frame_hash_fingerprint TEXT NOT NULL,
            created_at TEXT NOT NULL,
            completed_at TEXT,
            CHECK (attempt_number >= 1),
            CHECK (frame_count >= 0),
            FOREIGN KEY (analysis_identity_id)
                REFERENCES analysis_identities(analysis_identity_id),
            FOREIGN KEY (run_id) REFERENCES analysis_runs(run_id),
            FOREIGN KEY (asset_id) REFERENCES assets(asset_id)
        );

        CREATE TABLE IF NOT EXISTS visual_observations (
            observation_id TEXT PRIMARY KEY,
            analysis_identity_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            asset_id TEXT NOT NULL,
            attempt_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            model_identifier TEXT NOT NULL,
            gateway_version TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            response_schema_version TEXT NOT NULL,
            frame_hash_fingerprint TEXT NOT NULL,
            relative_json_path TEXT NOT NULL,
            observation_json TEXT,
            created_at TEXT NOT NULL,
            UNIQUE (
                analysis_identity_id,
                provider,
                model_identifier,
                prompt_version,
                response_schema_version,
                gateway_version,
                frame_hash_fingerprint
            ),
            FOREIGN KEY (analysis_identity_id)
                REFERENCES analysis_identities(analysis_identity_id),
            FOREIGN KEY (attempt_id) REFERENCES model_analysis_attempts(attempt_id),
            FOREIGN KEY (asset_id) REFERENCES assets(asset_id)
        );

        CREATE INDEX IF NOT EXISTS idx_analysis_consent_events_project_run
            ON analysis_consent_events (project_id, run_id);

        CREATE INDEX IF NOT EXISTS idx_model_analysis_attempts_cache
            ON model_analysis_attempts (
                analysis_identity_id,
                provider,
                model_identifier,
                prompt_version,
                response_schema_version,
                gateway_version,
                frame_hash_fingerprint,
                status
            );

        CREATE INDEX IF NOT EXISTS idx_model_analysis_attempts_run
            ON model_analysis_attempts (run_id);

        CREATE INDEX IF NOT EXISTS idx_visual_observations_project
            ON visual_observations (project_id, created_at DESC);
        """
    )


def _ensure_observation_review_tables(conn: sqlite3.Connection) -> None:
    """Phase 8D: immutable editorial reviews for visual observations."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS visual_observation_reviews (
            review_id TEXT PRIMARY KEY,
            observation_id TEXT NOT NULL,
            analysis_identity_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            asset_id TEXT NOT NULL,
            working_media_id TEXT NOT NULL,
            observation_sha256 TEXT NOT NULL,
            frame_set_fingerprint TEXT NOT NULL,
            review_revision INTEGER NOT NULL,
            decision TEXT NOT NULL,
            reason_code TEXT,
            review_note TEXT,
            created_at TEXT NOT NULL,
            supersedes_review_id TEXT,
            CHECK (review_revision >= 1),
            CHECK (decision IN ('accepted', 'reanalyze_requested', 'rejected')),
            UNIQUE (observation_id, review_revision),
            FOREIGN KEY (observation_id) REFERENCES visual_observations(observation_id),
            FOREIGN KEY (analysis_identity_id)
                REFERENCES analysis_identities(analysis_identity_id),
            FOREIGN KEY (asset_id) REFERENCES assets(asset_id)
        );

        CREATE INDEX IF NOT EXISTS idx_visual_observation_reviews_project
            ON visual_observation_reviews (project_id, created_at DESC);

        CREATE INDEX IF NOT EXISTS idx_visual_observation_reviews_observation_current
            ON visual_observation_reviews (observation_id, review_revision DESC);
        """
    )


def _ensure_editorial_tables(conn: sqlite3.Connection) -> None:
    """Phase 9: editorial brief, text attempts, script structure, and coverage."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS editorial_project_state (
            project_id TEXT PRIMARY KEY,
            active_brief_id TEXT,
            active_narrative_plan_id TEXT,
            selected_hook_id TEXT,
            active_script_id TEXT,
            active_coverage_audit_id TEXT,
            current_script_lock_id TEXT,
            observation_fingerprint TEXT,
            status TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS editorial_runs (
            run_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            scope TEXT NOT NULL,
            status TEXT NOT NULL,
            brief_id TEXT,
            brief_version INTEGER,
            narrative_plan_id TEXT,
            script_id TEXT,
            error_code TEXT,
            error_message TEXT,
            relative_report_path TEXT,
            created_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            schema_version TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS editorial_attempts (
            attempt_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            request_kind TEXT NOT NULL,
            provider TEXT NOT NULL,
            model_identifier TEXT NOT NULL,
            gateway_version TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            response_schema_version TEXT NOT NULL,
            input_fingerprint TEXT NOT NULL,
            status TEXT NOT NULL,
            relative_json_path TEXT,
            error_code TEXT,
            error_message TEXT,
            created_at TEXT NOT NULL,
            completed_at TEXT,
            FOREIGN KEY (run_id) REFERENCES editorial_runs(run_id)
        );

        CREATE TABLE IF NOT EXISTS project_briefs (
            project_brief_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            language TEXT NOT NULL,
            topic TEXT NOT NULL,
            target_audience TEXT NOT NULL,
            desired_duration_seconds INTEGER,
            tone TEXT NOT NULL,
            geographic_frame TEXT,
            brief_version INTEGER NOT NULL,
            content_sha256 TEXT NOT NULL,
            status TEXT NOT NULL,
            relative_json_path TEXT NOT NULL,
            created_at TEXT NOT NULL,
            supersedes_brief_id TEXT,
            UNIQUE (project_id, brief_version)
        );

        CREATE TABLE IF NOT EXISTS narrative_plans (
            narrative_plan_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            project_brief_id TEXT NOT NULL,
            brief_version INTEGER NOT NULL,
            status TEXT NOT NULL,
            input_observation_fingerprint TEXT NOT NULL,
            provider TEXT NOT NULL,
            model_identifier TEXT NOT NULL,
            gateway_version TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            response_schema_version TEXT NOT NULL,
            relative_json_path TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (project_brief_id) REFERENCES project_briefs(project_brief_id)
        );

        CREATE TABLE IF NOT EXISTS hook_variants (
            hook_id TEXT PRIMARY KEY,
            narrative_plan_id TEXT NOT NULL,
            hook_text TEXT NOT NULL,
            hook_type TEXT NOT NULL,
            intended_effect TEXT NOT NULL,
            user_status TEXT NOT NULL,
            relative_json_path TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (narrative_plan_id) REFERENCES narrative_plans(narrative_plan_id)
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_hook_variants_one_selected
            ON hook_variants (narrative_plan_id)
            WHERE user_status = 'selected';

        CREATE TABLE IF NOT EXISTS script_drafts (
            script_id TEXT PRIMARY KEY,
            script_version INTEGER NOT NULL,
            project_id TEXT NOT NULL,
            language TEXT NOT NULL,
            narrative_plan_id TEXT NOT NULL,
            selected_hook_id TEXT,
            project_brief_id TEXT NOT NULL,
            brief_version INTEGER NOT NULL,
            status TEXT NOT NULL,
            source_kind TEXT NOT NULL,
            supersedes_script_id TEXT,
            content_sha256 TEXT NOT NULL,
            provider TEXT NOT NULL,
            model_identifier TEXT NOT NULL,
            gateway_version TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            response_schema_version TEXT NOT NULL,
            relative_json_path TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE (project_id, script_version),
            FOREIGN KEY (narrative_plan_id) REFERENCES narrative_plans(narrative_plan_id),
            FOREIGN KEY (selected_hook_id) REFERENCES hook_variants(hook_id),
            FOREIGN KEY (project_brief_id) REFERENCES project_briefs(project_brief_id)
        );

        CREATE TABLE IF NOT EXISTS script_sentences (
            sentence_id TEXT PRIMARY KEY,
            script_id TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            text TEXT NOT NULL,
            narrative_function TEXT NOT NULL,
            claim_ids_json TEXT NOT NULL,
            visual_beat_ids_json TEXT NOT NULL,
            UNIQUE (script_id, ordinal),
            FOREIGN KEY (script_id) REFERENCES script_drafts(script_id)
        );

        CREATE TABLE IF NOT EXISTS script_claims (
            claim_id TEXT PRIMARY KEY,
            script_id TEXT NOT NULL,
            statement TEXT NOT NULL,
            claim_type TEXT NOT NULL,
            confidence REAL NOT NULL,
            evidence_refs_json TEXT NOT NULL,
            user_note TEXT,
            status TEXT NOT NULL,
            FOREIGN KEY (script_id) REFERENCES script_drafts(script_id)
        );

        CREATE TABLE IF NOT EXISTS visual_beats (
            visual_beat_id TEXT PRIMARY KEY,
            script_id TEXT NOT NULL,
            function TEXT NOT NULL,
            description TEXT NOT NULL,
            rhythm_function TEXT NOT NULL,
            continuity_requirements_json TEXT NOT NULL,
            intended_duration_hint_seconds REAL,
            FOREIGN KEY (script_id) REFERENCES script_drafts(script_id)
        );

        CREATE TABLE IF NOT EXISTS visual_beat_sentences (
            visual_beat_id TEXT NOT NULL,
            sentence_id TEXT NOT NULL,
            PRIMARY KEY (visual_beat_id, sentence_id),
            FOREIGN KEY (visual_beat_id) REFERENCES visual_beats(visual_beat_id),
            FOREIGN KEY (sentence_id) REFERENCES script_sentences(sentence_id)
        );

        CREATE TABLE IF NOT EXISTS visual_intents (
            visual_intent_id TEXT PRIMARY KEY,
            visual_beat_id TEXT NOT NULL,
            desired_motif TEXT NOT NULL,
            action TEXT NOT NULL,
            setting TEXT NOT NULL,
            geographic_requirements TEXT,
            authenticity_requirements_json TEXT NOT NULL,
            allowed_media_kinds_json TEXT NOT NULL,
            priority INTEGER NOT NULL,
            FOREIGN KEY (visual_beat_id) REFERENCES visual_beats(visual_beat_id)
        );

        CREATE TABLE IF NOT EXISTS coverage_audits (
            coverage_audit_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            script_id TEXT NOT NULL,
            script_version INTEGER NOT NULL,
            brief_version INTEGER NOT NULL,
            narrative_plan_id TEXT NOT NULL,
            input_observation_fingerprint TEXT NOT NULL,
            status TEXT NOT NULL,
            provider TEXT NOT NULL,
            model_identifier TEXT NOT NULL,
            gateway_version TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            response_schema_version TEXT NOT NULL,
            relative_json_path TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (script_id) REFERENCES script_drafts(script_id),
            FOREIGN KEY (narrative_plan_id) REFERENCES narrative_plans(narrative_plan_id)
        );

        CREATE TABLE IF NOT EXISTS coverage_intent_results (
            coverage_audit_id TEXT NOT NULL,
            visual_intent_id TEXT NOT NULL,
            coverage_status TEXT NOT NULL,
            candidate_asset_ids_json TEXT NOT NULL,
            accepted_observation_ids_json TEXT NOT NULL,
            rationale TEXT NOT NULL,
            confidence REAL NOT NULL,
            missing_properties_json TEXT NOT NULL,
            recommended_next_action TEXT NOT NULL,
            PRIMARY KEY (coverage_audit_id, visual_intent_id),
            FOREIGN KEY (coverage_audit_id) REFERENCES coverage_audits(coverage_audit_id),
            FOREIGN KEY (visual_intent_id) REFERENCES visual_intents(visual_intent_id)
        );

        CREATE INDEX IF NOT EXISTS idx_editorial_runs_project_status
            ON editorial_runs (project_id, status);
        CREATE INDEX IF NOT EXISTS idx_editorial_attempts_cache
            ON editorial_attempts (
                project_id, request_kind, provider, model_identifier,
                gateway_version, prompt_version, response_schema_version,
                input_fingerprint, status
            );
        CREATE INDEX IF NOT EXISTS idx_project_briefs_project_status
            ON project_briefs (project_id, status);
        CREATE INDEX IF NOT EXISTS idx_narrative_plans_project_status
            ON narrative_plans (project_id, status);
        CREATE INDEX IF NOT EXISTS idx_script_drafts_project_status
            ON script_drafts (project_id, status);
        CREATE INDEX IF NOT EXISTS idx_coverage_audits_project_status
            ON coverage_audits (project_id, status);
        """
    )
    columns = {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(editorial_project_state)").fetchall()
    }
    if "current_script_lock_id" not in columns:
        conn.execute(
            "ALTER TABLE editorial_project_state ADD COLUMN current_script_lock_id TEXT"
        )


def _ensure_supplementation_tables(conn: sqlite3.Connection) -> None:
    """Phase 10: coverage gaps, fake stock supplementation, and script locks."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS coverage_gaps (
            gap_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            script_id TEXT NOT NULL,
            script_version INTEGER NOT NULL,
            coverage_audit_id TEXT NOT NULL,
            visual_intent_id TEXT NOT NULL,
            coverage_level TEXT NOT NULL,
            risk_flags_json TEXT NOT NULL,
            missing_properties_json TEXT NOT NULL,
            current_escalation_step TEXT NOT NULL,
            prior_attempt_summaries_json TEXT NOT NULL,
            user_decision TEXT,
            outcome TEXT,
            status TEXT NOT NULL,
            gap_version INTEGER NOT NULL,
            accepted_unresolved_risks_json TEXT NOT NULL,
            resolved_asset_id TEXT,
            relative_json_path TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (project_id, visual_intent_id, gap_version)
        );

        CREATE TABLE IF NOT EXISTS coverage_gap_events (
            event_id TEXT PRIMARY KEY,
            gap_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            from_step TEXT,
            to_step TEXT,
            message TEXT,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (gap_id) REFERENCES coverage_gaps(gap_id)
        );

        CREATE TABLE IF NOT EXISTS supplementation_runs (
            run_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            scope TEXT NOT NULL,
            status TEXT NOT NULL,
            selected_gap_ids_json TEXT NOT NULL,
            error_code TEXT,
            error_message TEXT,
            relative_report_path TEXT,
            created_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            schema_version TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS supplementation_attempts (
            attempt_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            scope TEXT NOT NULL,
            gap_id TEXT,
            request_id TEXT,
            cache_key TEXT,
            status TEXT NOT NULL,
            relative_json_path TEXT,
            error_code TEXT,
            error_message TEXT,
            created_at TEXT NOT NULL,
            completed_at TEXT,
            FOREIGN KEY (run_id) REFERENCES supplementation_runs(run_id)
        );

        CREATE TABLE IF NOT EXISTS supplementation_requests (
            request_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            gap_id TEXT NOT NULL,
            script_id TEXT NOT NULL,
            visual_intent_id TEXT NOT NULL,
            motif TEXT NOT NULL,
            action TEXT NOT NULL,
            setting TEXT NOT NULL,
            geographic_requirements TEXT,
            authenticity_requirements_json TEXT NOT NULL,
            allowed_media_kinds_json TEXT NOT NULL,
            query_text TEXT NOT NULL,
            search_version INTEGER NOT NULL,
            status TEXT NOT NULL,
            relative_json_path TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (gap_id) REFERENCES coverage_gaps(gap_id)
        );

        CREATE TABLE IF NOT EXISTS stock_search_attempts (
            attempt_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            request_id TEXT NOT NULL,
            gap_id TEXT NOT NULL,
            query_text TEXT NOT NULL,
            search_strategy TEXT NOT NULL,
            provider TEXT NOT NULL,
            adapter_version TEXT NOT NULL,
            attempt_number INTEGER NOT NULL,
            result_count INTEGER NOT NULL,
            status TEXT NOT NULL,
            error_code TEXT,
            error_message TEXT,
            relative_json_path TEXT,
            created_at TEXT NOT NULL,
            UNIQUE (request_id, attempt_number),
            FOREIGN KEY (request_id) REFERENCES supplementation_requests(request_id),
            FOREIGN KEY (gap_id) REFERENCES coverage_gaps(gap_id)
        );

        CREATE TABLE IF NOT EXISTS stock_candidates (
            candidate_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            request_id TEXT NOT NULL,
            gap_id TEXT NOT NULL,
            attempt_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            provider_candidate_id TEXT NOT NULL,
            preview_ref TEXT,
            description TEXT NOT NULL,
            media_kind TEXT NOT NULL,
            visible_metadata_json TEXT NOT NULL,
            geographic_hint TEXT,
            license_status TEXT NOT NULL,
            duplicate_status TEXT NOT NULL,
            user_status TEXT NOT NULL,
            metadata_fingerprint TEXT,
            preview_sha256 TEXT,
            relative_json_path TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE (attempt_id, provider, provider_candidate_id),
            FOREIGN KEY (attempt_id) REFERENCES stock_search_attempts(attempt_id),
            FOREIGN KEY (request_id) REFERENCES supplementation_requests(request_id),
            FOREIGN KEY (gap_id) REFERENCES coverage_gaps(gap_id)
        );

        CREATE TABLE IF NOT EXISTS stock_candidate_decisions (
            decision_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            gap_id TEXT NOT NULL,
            candidate_id TEXT NOT NULL,
            revision INTEGER NOT NULL,
            decision TEXT NOT NULL,
            reason TEXT NOT NULL,
            user_note TEXT,
            created_at TEXT NOT NULL,
            UNIQUE (candidate_id, revision),
            FOREIGN KEY (candidate_id) REFERENCES stock_candidates(candidate_id),
            FOREIGN KEY (gap_id) REFERENCES coverage_gaps(gap_id)
        );

        CREATE TABLE IF NOT EXISTS claim_decisions (
            decision_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            script_id TEXT NOT NULL,
            claim_id TEXT NOT NULL,
            claim_content_sha256 TEXT NOT NULL,
            revision INTEGER NOT NULL,
            decision TEXT NOT NULL,
            reason TEXT,
            user_note TEXT,
            relative_json_path TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE (script_id, claim_id, revision)
        );

        CREATE TABLE IF NOT EXISTS graphic_plans (
            graphic_plan_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            visual_intent_id TEXT NOT NULL,
            gap_id TEXT NOT NULL,
            description TEXT NOT NULL,
            required_data_json TEXT NOT NULL,
            geographic_scope TEXT,
            user_status TEXT NOT NULL,
            relative_json_path TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (gap_id) REFERENCES coverage_gaps(gap_id)
        );

        CREATE TABLE IF NOT EXISTS script_locks (
            lock_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            script_id TEXT NOT NULL,
            script_version INTEGER NOT NULL,
            project_brief_id TEXT NOT NULL,
            narrative_plan_id TEXT NOT NULL,
            selected_hook_id TEXT NOT NULL,
            coverage_audit_id TEXT NOT NULL,
            observation_set_fingerprint TEXT NOT NULL,
            script_hash TEXT NOT NULL,
            structure_fingerprint TEXT NOT NULL,
            coverage_fingerprint TEXT NOT NULL,
            accepted_open_risks_json TEXT NOT NULL,
            claim_decision_snapshot_json TEXT NOT NULL,
            user_confirmed INTEGER NOT NULL,
            user_confirmed_at TEXT,
            confirmation_fingerprint TEXT NOT NULL,
            lock_fingerprint TEXT NOT NULL,
            lock_version INTEGER NOT NULL,
            status TEXT NOT NULL,
            relative_json_path TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS script_lock_risks (
            lock_id TEXT NOT NULL,
            risk_key TEXT NOT NULL,
            confirmed_at TEXT NOT NULL,
            confirmation_fingerprint TEXT NOT NULL,
            PRIMARY KEY (lock_id, risk_key),
            FOREIGN KEY (lock_id) REFERENCES script_locks(lock_id)
        );

        CREATE INDEX IF NOT EXISTS idx_coverage_gaps_project_status
            ON coverage_gaps (project_id, status);
        CREATE INDEX IF NOT EXISTS idx_coverage_gap_events_gap
            ON coverage_gap_events (gap_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_supplementation_runs_project_status
            ON supplementation_runs (project_id, status);
        CREATE INDEX IF NOT EXISTS idx_supplementation_attempts_run
            ON supplementation_attempts (run_id);
        CREATE INDEX IF NOT EXISTS idx_supplementation_requests_gap
            ON supplementation_requests (gap_id, search_version);
        CREATE INDEX IF NOT EXISTS idx_stock_search_attempts_gap
            ON stock_search_attempts (gap_id, attempt_number);
        CREATE INDEX IF NOT EXISTS idx_stock_candidates_gap
            ON stock_candidates (gap_id, user_status);
        CREATE INDEX IF NOT EXISTS idx_claim_decisions_script_claim
            ON claim_decisions (script_id, claim_id, revision DESC);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_script_locks_one_current
            ON script_locks (project_id)
            WHERE status = 'locked';
        """
    )


def _ensure_analysis_run_columns(conn: sqlite3.Connection) -> None:
    """Idempotente Spalten-Nachrüstung für Schema 11 → 12."""
    run_cols = {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(analysis_runs)").fetchall()
    }
    if "reused_assets" not in run_cols:
        conn.execute(
            "ALTER TABLE analysis_runs "
            "ADD COLUMN reused_assets INTEGER NOT NULL DEFAULT 0"
        )

    asset_cols = {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(analysis_run_assets)").fetchall()
    }
    if "analysis_identity_id" not in asset_cols:
        conn.execute(
            "ALTER TABLE analysis_run_assets "
            "ADD COLUMN analysis_identity_id TEXT"
        )


def _apply_current_schema_objects(conn: sqlite3.Connection) -> None:
    _ensure_validation_tables(conn)
    _ensure_validation_profile_columns(conn)
    _ensure_validation_image_columns(conn)
    _ensure_intake_tables(conn)
    _ensure_copy_intake_tables(conn)
    _ensure_analysis_tables(conn)
    _ensure_model_analysis_tables(conn)
    _ensure_observation_review_tables(conn)
    _ensure_editorial_tables(conn)
    _ensure_supplementation_tables(conn)


def _ensure_schema(conn: sqlite3.Connection) -> None:
    _ensure_base_tables(conn)
    now = datetime.now(timezone.utc).isoformat()
    row = conn.execute(
        "SELECT schema_version FROM registry_schema LIMIT 1"
    ).fetchone()

    if row is None:
        _apply_current_schema_objects(conn)
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
        _apply_current_schema_objects(conn)
        conn.execute(
            "UPDATE registry_schema SET updated_at = ? WHERE schema_version = ?",
            (now, REGISTRY_SCHEMA_VERSION),
        )
        return

    if current in _LEGACY_SCHEMA_VERSIONS:
        # Idempotente Migration: bestehende Assets/Imports/Validations bleiben.
        _apply_current_schema_objects(conn)
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
