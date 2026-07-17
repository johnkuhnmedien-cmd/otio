"""Dauerhafte 7C1-Regression: Schema v6→v7 und gegenseitige Run-Sperren."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from otio_app.discovery_v2.domain.media_intake import (
    INTAKE_RUN_SCOPE_COPY_ONLY,
    INTAKE_RUN_SCOPE_REMUX_ONLY,
    INTAKE_RUN_SCOPE_VIDEO_TRANSCODE_ONLY,
    IntakeRunRecord,
    IntakeRunStatus,
)
from otio_app.discovery_v2.persistence import asset_registry_database as reg_db
from otio_app.discovery_v2.persistence import copy_intake_repository as copy_repo


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _build_v6_registry(project_root: Path) -> Path:
    """Erzeugt eine Schema-v6-Registry ohne scope-Spalte inkl. historischer Daten."""
    reg_dir = project_root / "_otio_v2" / "registry"
    reg_dir.mkdir(parents=True)
    db_path = reg_dir / "assets.sqlite3"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE registry_schema (
            schema_version TEXT PRIMARY KEY,
            initialized_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE assets (
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
        CREATE TABLE selection_imports (
            import_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            selection_id TEXT NOT NULL,
            scan_id TEXT NOT NULL,
            source_selection_relative_path TEXT NOT NULL,
            imported_at TEXT NOT NULL,
            status TEXT NOT NULL,
            selected_asset_count INTEGER NOT NULL
        );
        CREATE TABLE validation_runs (
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
            error_summary TEXT
        );
        CREATE TABLE intake_plans (
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
            duplicate_warning_count INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE intake_runs (
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
            worker_version TEXT NOT NULL
        );
        CREATE TABLE intake_run_assets (
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
            UNIQUE (run_id, asset_id)
        );
        CREATE TABLE working_media (
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
                project_id, asset_id, source_sha256, action, processing_profile_version
            )
        );
        """
    )
    now = _now().isoformat()
    pid, aid = "proj-scope", "asset-scope"
    plan_id, run_id = "plan-scope", "run-copy-hist"
    import_id, sel_id, scan_id, val_id = "imp", "sel", "scan", "val"
    sha = "d" * 64
    conn.execute(
        "INSERT INTO registry_schema VALUES (?,?,?)", ("6", now, now)
    )
    conn.execute(
        "INSERT INTO assets VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (aid, pid, "F/a.mp4", "F", "a.mp4", ".mp4", "video", 10, 1, now, now),
    )
    conn.execute(
        "INSERT INTO selection_imports VALUES (?,?,?,?,?,?,?,?)",
        (import_id, pid, sel_id, scan_id, "inventory/selection_latest.json", now, "imported", 1),
    )
    conn.execute(
        """
        INSERT INTO validation_runs
        (run_id, project_id, import_id, selection_id, scan_id, status, created_at,
         total_assets, processed_assets, successful_assets, failed_assets)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (val_id, pid, import_id, sel_id, scan_id, "completed", now, 1, 1, 1, 0),
    )
    conn.execute(
        """
        INSERT INTO intake_plans
        (plan_id, project_id, import_id, selection_id, scan_id, validation_run_id,
         planner_version, status, created_at, total_assets, copy_count)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (plan_id, pid, import_id, sel_id, scan_id, val_id, "2", "ready", now, 1, 1),
    )
    conn.execute(
        """
        INSERT INTO intake_runs
        (run_id, project_id, plan_id, import_id, selection_id, scan_id,
         validation_run_id, status, created_at, total_assets, succeeded_assets,
         worker_version)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (run_id, pid, plan_id, import_id, sel_id, scan_id, val_id, "completed", now, 1, 1, "1"),
    )
    conn.execute(
        """
        INSERT INTO intake_run_assets
        (run_asset_id, run_id, plan_id, asset_id, source_relative_path, source_group,
         media_kind, planned_action, status, source_sha256, output_sha256,
         working_relative_path)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "ra-scope",
            run_id,
            plan_id,
            aid,
            "F/a.mp4",
            "F",
            "video",
            "copy",
            "succeeded",
            sha,
            sha,
            f"media/working/{aid}/{sha}/copy-v1/{aid}.mp4",
        ),
    )
    conn.execute(
        """
        INSERT INTO working_media
        (working_media_id, project_id, asset_id, plan_id, intake_run_id,
         source_relative_path, working_relative_path, source_sha256, output_sha256,
         media_kind, extension, action, processing_profile_version, status,
         created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "wm-scope",
            pid,
            aid,
            plan_id,
            run_id,
            "F/a.mp4",
            f"media/working/{aid}/{sha}/copy-v1/{aid}.mp4",
            sha,
            sha,
            "video",
            ".mp4",
            "copy",
            "copy-v1",
            "completed",
            now,
            now,
        ),
    )
    conn.commit()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(intake_runs)")}
    assert "scope" not in cols
    conn.close()
    return db_path


def test_schema_v6_to_v9_preserves_copy_history(tmp_path: Path) -> None:
    root = tmp_path / "Project"
    root.mkdir()
    _build_v6_registry(root)

    conn = reg_db.get_registry_connection(root)
    assert reg_db.read_schema_version(conn) == "20"
    cols = {r[1] for r in conn.execute("PRAGMA table_info(intake_runs)")}
    assert "scope" in cols
    assert "transcoded_assets" in cols
    assert "copied_assets" in cols
    assert "remuxed_assets" in cols
    assert "reused_assets" in cols
    val_cols = {r[1] for r in conn.execute("PRAGMA table_info(asset_validations)")}
    assert "audio_channel_count" in val_cols
    assert "rotation_degrees" in val_cols
    run = conn.execute("SELECT * FROM intake_runs WHERE run_id='run-copy-hist'").fetchone()
    assert run is not None
    assert run["scope"] == "copy_only"
    assert int(run["transcoded_assets"]) == 0
    assert int(run["remuxed_assets"]) == 0
    assert conn.execute("SELECT COUNT(*) FROM working_media").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM intake_run_assets").fetchone()[0] == 1
    conn.close()

    # Idempotent erneut öffnen
    conn2 = reg_db.get_registry_connection(root)
    assert reg_db.read_schema_version(conn2) == "20"
    assert conn2.execute("SELECT COUNT(*) FROM intake_runs").fetchone()[0] == 1
    assert conn2.execute(
        "SELECT scope FROM intake_runs WHERE run_id='run-copy-hist'"
    ).fetchone()[0] == "copy_only"
    cols2 = {r[1] for r in conn2.execute("PRAGMA table_info(intake_runs)")}
    assert "transcoded_assets" in cols2
    conn2.close()


def _insert_active(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    scope: str,
    status: IntakeRunStatus = IntakeRunStatus.RUNNING,
) -> str:
    run_id = str(uuid4())
    # Minimal FK parents may already exist from v6 seed; create if needed.
    copy_repo.insert_intake_run(
        conn,
        IntakeRunRecord(
            run_id=run_id,
            project_id=project_id,
            plan_id="plan-scope",
            import_id="imp",
            selection_id="sel",
            scan_id="scan",
            validation_run_id="val",
            status=status,
            created_at=_now(),
            total_assets=1,
            worker_version="1",
            scope=scope,
        ),
    )
    conn.commit()
    return run_id


def test_active_copy_blocks_remux(tmp_path: Path) -> None:
    root = tmp_path / "Project"
    root.mkdir()
    _build_v6_registry(root)
    conn = reg_db.get_registry_connection(root)
    _insert_active(conn, project_id="proj-scope", scope=INTAKE_RUN_SCOPE_COPY_ONLY)
    active = copy_repo.find_active_intake_run(conn, project_id="proj-scope")
    assert active is not None
    assert active.scope == INTAKE_RUN_SCOPE_COPY_ONLY
    conn.close()


def test_active_remux_blocks_copy_lookup(tmp_path: Path) -> None:
    root = tmp_path / "Project"
    root.mkdir()
    _build_v6_registry(root)
    conn = reg_db.get_registry_connection(root)
    # Terminal historical already present; add active remux
    _insert_active(conn, project_id="proj-scope", scope=INTAKE_RUN_SCOPE_REMUX_ONLY)
    active = copy_repo.find_active_intake_run(conn, project_id="proj-scope")
    assert active is not None
    assert active.scope == INTAKE_RUN_SCOPE_REMUX_ONLY
    conn.close()


def test_terminal_runs_do_not_block(tmp_path: Path) -> None:
    root = tmp_path / "Project"
    root.mkdir()
    _build_v6_registry(root)
    conn = reg_db.get_registry_connection(root)
    # Historical completed copy already inserted; add completed remux
    _insert_active(
        conn,
        project_id="proj-scope",
        scope=INTAKE_RUN_SCOPE_REMUX_ONLY,
        status=IntakeRunStatus.COMPLETED,
    )
    active = copy_repo.find_active_intake_run(conn, project_id="proj-scope")
    assert active is None
    conn.close()


def test_active_copy_and_remux_mutual_exclusion_via_find_active(tmp_path: Path) -> None:
    """Ein aktiver Run (egal welcher Scope) ist über find_active sichtbar."""
    root = tmp_path / "Project"
    root.mkdir()
    _build_v6_registry(root)
    conn = reg_db.get_registry_connection(root)
    copy_id = _insert_active(
        conn, project_id="proj-scope", scope=INTAKE_RUN_SCOPE_COPY_ONLY
    )
    assert copy_repo.find_active_intake_run(conn, project_id="proj-scope").run_id == copy_id
    # Complete copy, start remux
    run = copy_repo.get_intake_run(conn, run_id=copy_id)
    assert run is not None
    copy_repo.update_intake_run(
        conn,
        run.model_copy(
            update={
                "status": IntakeRunStatus.COMPLETED,
                "completed_at": _now(),
            }
        ),
    )
    conn.commit()
    remux_id = _insert_active(
        conn, project_id="proj-scope", scope=INTAKE_RUN_SCOPE_REMUX_ONLY
    )
    active = copy_repo.find_active_intake_run(conn, project_id="proj-scope")
    assert active is not None and active.run_id == remux_id
    conn.close()


def test_active_copy_blocks_video_transcode_lookup(tmp_path: Path) -> None:
    root = tmp_path / "Project"
    root.mkdir()
    _build_v6_registry(root)
    conn = reg_db.get_registry_connection(root)
    _insert_active(conn, project_id="proj-scope", scope=INTAKE_RUN_SCOPE_COPY_ONLY)
    active = copy_repo.find_active_intake_run(conn, project_id="proj-scope")
    assert active is not None
    assert active.scope == INTAKE_RUN_SCOPE_COPY_ONLY
    conn.close()


def test_active_remux_blocks_video_transcode_lookup(tmp_path: Path) -> None:
    root = tmp_path / "Project"
    root.mkdir()
    _build_v6_registry(root)
    conn = reg_db.get_registry_connection(root)
    _insert_active(conn, project_id="proj-scope", scope=INTAKE_RUN_SCOPE_REMUX_ONLY)
    active = copy_repo.find_active_intake_run(conn, project_id="proj-scope")
    assert active is not None
    assert active.scope == INTAKE_RUN_SCOPE_REMUX_ONLY
    conn.close()


def test_active_video_transcode_blocks_other_scopes(tmp_path: Path) -> None:
    root = tmp_path / "Project"
    root.mkdir()
    _build_v6_registry(root)
    conn = reg_db.get_registry_connection(root)
    vt_id = _insert_active(
        conn,
        project_id="proj-scope",
        scope=INTAKE_RUN_SCOPE_VIDEO_TRANSCODE_ONLY,
    )
    active = copy_repo.find_active_intake_run(conn, project_id="proj-scope")
    assert active is not None
    assert active.run_id == vt_id
    assert active.scope == INTAKE_RUN_SCOPE_VIDEO_TRANSCODE_ONLY
    # Zweiter aktiver Transcode wäre ebenfalls über find_active blockiert.
    conn.close()


def test_terminal_video_transcode_does_not_block(tmp_path: Path) -> None:
    root = tmp_path / "Project"
    root.mkdir()
    _build_v6_registry(root)
    conn = reg_db.get_registry_connection(root)
    _insert_active(
        conn,
        project_id="proj-scope",
        scope=INTAKE_RUN_SCOPE_VIDEO_TRANSCODE_ONLY,
        status=IntakeRunStatus.COMPLETED,
    )
    assert copy_repo.find_active_intake_run(conn, project_id="proj-scope") is None
    conn.close()


def test_active_image_convert_blocks_other_scopes(tmp_path: Path) -> None:
    from otio_app.discovery_v2.domain.media_intake import (
        INTAKE_RUN_SCOPE_IMAGE_CONVERT_ONLY,
    )

    root = tmp_path / "Project"
    root.mkdir()
    _build_v6_registry(root)
    conn = reg_db.get_registry_connection(root)
    img_id = _insert_active(
        conn,
        project_id="proj-scope",
        scope=INTAKE_RUN_SCOPE_IMAGE_CONVERT_ONLY,
    )
    active = copy_repo.find_active_intake_run(conn, project_id="proj-scope")
    assert active is not None
    assert active.run_id == img_id
    assert active.scope == INTAKE_RUN_SCOPE_IMAGE_CONVERT_ONLY
    conn.close()
