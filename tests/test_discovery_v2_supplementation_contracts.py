"""Phase 10 supplementation contracts: schema 16, paths, and domain limits."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent))

from otio_app.discovery_v2.domain.asset_registry import REGISTRY_SCHEMA_VERSION
from otio_app.discovery_v2.domain.supplementation import (
    MAX_STOCK_CANDIDATES_PER_ATTEMPT,
    CoverageGap,
    CoverageGapStatus,
    CoverageLevel,
    StockSearchRequest,
)
from otio_app.discovery_v2.editorial_paths import EditorialPathError
from otio_app.discovery_v2.persistence import asset_registry_database as reg_db
from otio_app.discovery_v2.persistence.supplementation_repository import (
    supplementation_table_names,
)
from otio_app.discovery_v2.supplementation_paths import (
    supplementation_preview_relative_path,
    supplementation_script_lock_json_relative_path,
)

from test_discovery_v2_analysis_prepare import _new_project, _now


def test_schema_15_to_16_preserves_assets_and_adds_supplementation_tables(
    tmp_path: Path, temp_db_path: Path
) -> None:
    root = tmp_path / "Project"
    (root / "Media").mkdir(parents=True)
    project = _new_project(root, temp_db_path, name="Phase 10 Schema")
    conn = reg_db.get_registry_connection(root)
    try:
        assert reg_db.read_schema_version(conn) == "18"
        conn.execute(
            """
            INSERT INTO assets (
                asset_id, project_id, source_relative_path, source_group, file_name,
                extension, media_kind, size_bytes, mtime_ns, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "asset-keep",
                project.id,
                "Media/still.jpg",
                "Media",
                "still.jpg",
                ".jpg",
                "image",
                1,
                1,
                _now().isoformat(),
                _now().isoformat(),
            ),
        )
        conn.execute("UPDATE registry_schema SET schema_version = '15'")
        conn.commit()
    finally:
        conn.close()

    conn2 = reg_db.get_registry_connection(root)
    try:
        assert reg_db.read_schema_version(conn2) == REGISTRY_SCHEMA_VERSION == "18"
        assert conn2.execute(
            "SELECT COUNT(*) FROM assets WHERE asset_id = 'asset-keep'"
        ).fetchone()[0] == 1
        assert supplementation_table_names(conn2) == {
            "coverage_gaps",
            "coverage_gap_events",
            "supplementation_runs",
            "supplementation_attempts",
            "supplementation_requests",
            "stock_search_attempts",
            "stock_candidates",
            "stock_candidate_decisions",
            "claim_decisions",
            "graphic_plans",
            "script_locks",
            "script_lock_risks",
        }
        columns = {row[1] for row in conn2.execute("PRAGMA table_info(editorial_project_state)")}
        assert "current_script_lock_id" in columns
    finally:
        conn2.close()


def test_supplementation_domain_forbids_extra_and_limits_candidates() -> None:
    now = datetime.now(timezone.utc)
    gap = CoverageGap(
        gap_id="gap-1",
        project_id="project-1",
        script_id="script-1",
        script_version=1,
        coverage_audit_id="coverage-1",
        visual_intent_id="intent-1",
        coverage_level=CoverageLevel.NOT_COVERED,
        status=CoverageGapStatus.OPEN,
        gap_version=1,
        created_at=now,
        updated_at=now,
    )
    with pytest.raises(ValidationError):
        CoverageGap.model_validate({**gap.model_dump(mode="json"), "extra": "no"})
    with pytest.raises(ValidationError):
        StockSearchRequest(
            project_id="project-1",
            request_id="request-1",
            gap_id="gap-1",
            query_text="query",
            search_strategy="fake",
            max_results=MAX_STOCK_CANDIDATES_PER_ATTEMPT + 1,
        )


def test_supplementation_paths_keep_previews_opaque_under_editorial() -> None:
    assert supplementation_script_lock_json_relative_path("lock-1") == "editorial/script_locks/lock-1.json"
    assert supplementation_preview_relative_path("attempt-1", "preview-1").startswith(
        "editorial/supplementation/previews/"
    )
    with pytest.raises(EditorialPathError):
        supplementation_preview_relative_path("../attempt", "preview")
