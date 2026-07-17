from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from otio_app.discovery_v2.domain.asset_registry import REGISTRY_SCHEMA_VERSION
from otio_app.discovery_v2.domain.visual_edit import (
    ASSET_REUSE_MAX,
    MAX_SHOTS_PER_MINUTE_BLOCKING,
    MAX_SHOTS_PER_MINUTE_WARNING,
    PHOTO_SHOT_MAX_SECONDS,
    PHOTO_SHOT_MIN_SECONDS,
    PROMPT_VERSION_EDITORIAL_REPAIR_PROPOSAL,
    PROMPT_VERSION_HUMANITY_REVIEW,
    PROMPT_VERSION_VISUAL_EDIT_PLAN,
    RESPONSE_SCHEMA_EDITORIAL_REPAIR_PROPOSAL,
    RESPONSE_SCHEMA_HUMANITY_REVIEW,
    RESPONSE_SCHEMA_VISUAL_EDIT_PLAN,
    TEXT_REQUEST_KIND_EDITORIAL_REPAIR_PROPOSAL,
    TEXT_REQUEST_KIND_HUMANITY_REVIEW,
    TEXT_REQUEST_KIND_VISUAL_EDIT_PLAN,
    VIDEO_SHOT_MAX_SECONDS,
    VIDEO_SHOT_MIN_SECONDS,
)
from otio_app.discovery_v2.editing_paths import (
    EditingPathError,
    assert_editing_relative_path,
    visual_edit_plan_json_relative_path,
)
from otio_app.discovery_v2.persistence import asset_registry_database as reg_db


def test_visual_edit_contract_constants_are_phase12_values() -> None:
    assert MAX_SHOTS_PER_MINUTE_WARNING == 12.0
    assert MAX_SHOTS_PER_MINUTE_BLOCKING == 20.0
    assert VIDEO_SHOT_MIN_SECONDS == 0.80
    assert VIDEO_SHOT_MAX_SECONDS == 12.0
    assert PHOTO_SHOT_MIN_SECONDS == 1.20
    assert PHOTO_SHOT_MAX_SECONDS == 6.0
    assert ASSET_REUSE_MAX == 3
    assert TEXT_REQUEST_KIND_VISUAL_EDIT_PLAN == "visual_edit_plan"
    assert TEXT_REQUEST_KIND_HUMANITY_REVIEW == "humanity_review"
    assert TEXT_REQUEST_KIND_EDITORIAL_REPAIR_PROPOSAL == "editorial_repair_proposal"
    assert PROMPT_VERSION_VISUAL_EDIT_PLAN == "visual-edit-plan-v1"
    assert PROMPT_VERSION_HUMANITY_REVIEW == "humanity-review-v1"
    assert PROMPT_VERSION_EDITORIAL_REPAIR_PROPOSAL == "editorial-repair-proposal-v1"
    assert RESPONSE_SCHEMA_VISUAL_EDIT_PLAN == "visual-edit-plan-response-v1"
    assert RESPONSE_SCHEMA_HUMANITY_REVIEW == "humanity-review-response-v1"
    assert RESPONSE_SCHEMA_EDITORIAL_REPAIR_PROPOSAL == "editorial-repair-proposal-response-v1"


def test_schema_18_to_19_adds_visual_edit_tables_idempotently(tmp_path: Path) -> None:
    db_dir = reg_db.ensure_registry_dir(tmp_path)
    db_path = db_dir / "assets.sqlite3"
    raw = sqlite3.connect(str(db_path))
    raw.execute(
        "CREATE TABLE registry_schema (schema_version TEXT PRIMARY KEY, initialized_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    raw.execute("INSERT INTO registry_schema VALUES ('18', 'now', 'now')")
    raw.commit()
    raw.close()
    conn = reg_db.get_registry_connection(tmp_path)
    try:
        assert reg_db.read_schema_version(conn) == REGISTRY_SCHEMA_VERSION == "20"
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        assert {
            "visual_edit_project_state",
            "visual_edit_runs",
            "visual_edit_plans",
            "editorial_shots",
            "shot_media_assignments",
            "humanity_reviews",
            "feasibility_reports",
            "repair_proposals",
        }.issubset(tables)
        assert "otio_exports" not in tables
    finally:
        conn.close()
    conn2 = reg_db.get_registry_connection(tmp_path)
    try:
        assert reg_db.read_schema_version(conn2) == "20"
    finally:
        conn2.close()


def test_smoke_h_editing_paths_reject_classic_absolute_and_escape() -> None:
    assert assert_editing_relative_path("editing/plans/plan.json")
    assert visual_edit_plan_json_relative_path("plan-1") == "editing/plans/plan-1.json"
    for value in ["/tmp/x", "_otio/editing/x", "editing/../x", "_otio_v2/editing/x"]:
        with pytest.raises(EditingPathError):
            assert_editing_relative_path(value)
