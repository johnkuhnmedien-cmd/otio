from __future__ import annotations

import sqlite3
from pathlib import Path

import opentimelineio as otio

from otio_app.discovery_v2.domain.asset_registry import REGISTRY_SCHEMA_VERSION
from otio_app.discovery_v2.domain.export import EXPORT_PROFILE_VERSION, OTIO_LIBRARY_VERSION, timeline_name_for
from otio_app.discovery_v2.export_paths import (
    assert_export_relative_path,
    otio_export_relative_path,
    resolve_export_relative_path,
)
from otio_app.discovery_v2.persistence import asset_registry_database as reg_db


def test_smoke_a_schema_19_to_20_is_idempotent(tmp_path: Path) -> None:
    db_dir = tmp_path / "_otio_v2" / "registry"
    db_dir.mkdir(parents=True)
    db_path = db_dir / "assets.sqlite3"
    raw = sqlite3.connect(str(db_path))
    raw.execute(
        "CREATE TABLE registry_schema (schema_version TEXT PRIMARY KEY, initialized_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    raw.execute("INSERT INTO registry_schema VALUES ('19', 'now', 'now')")
    raw.commit()
    raw.close()
    conn = reg_db.get_registry_connection(tmp_path)
    try:
        assert reg_db.read_schema_version(conn) == REGISTRY_SCHEMA_VERSION == "20"
        tables = {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {
            "export_project_state",
            "editorial_approvals",
            "editorial_approval_risks",
            "export_validation_reports",
            "export_validation_issues",
            "otio_export_runs",
            "otio_export_artifacts",
            "otio_reparse_reports",
        }.issubset(tables)
        assert not {"premiere_exports", "davinci_exports", "fcp_exports"}.intersection(tables)
    finally:
        conn.close()
    conn2 = reg_db.get_registry_connection(tmp_path)
    try:
        assert reg_db.read_schema_version(conn2) == "20"
    finally:
        conn2.close()


def test_smoke_b_export_paths_are_only_under_export(tmp_path: Path) -> None:
    rel = otio_export_relative_path("run-1")
    assert rel == "export/otio/run-1/timeline.otio"
    assert assert_export_relative_path(rel) == rel
    assert resolve_export_relative_path(tmp_path, rel).as_posix().endswith("_otio_v2/export/otio/run-1/timeline.otio")
    for bad in ("../x.otio", "_otio_v2/export/x.otio", "editing/x.json", "/abs/export/x"):
        try:
            assert_export_relative_path(bad)
        except ValueError:
            pass
        else:  # pragma: no cover
            raise AssertionError(f"bad path accepted: {bad}")


def test_smoke_c_phase13_constants_and_otio_version() -> None:
    assert EXPORT_PROFILE_VERSION == "discovery-otio-export-v1"
    assert OTIO_LIBRARY_VERSION == "0.18.1"
    assert otio.__version__ == "0.18.1"
    assert timeline_name_for("abcdef123456", 2) == "discovery_v2_abcdef12_2"
