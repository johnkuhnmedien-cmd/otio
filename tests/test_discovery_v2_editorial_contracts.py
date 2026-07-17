"""Phase 9 Editorial contracts: domain, paths, and schema 15."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent))

from otio_app.discovery_v2.domain.asset_registry import REGISTRY_SCHEMA_VERSION
from otio_app.discovery_v2.domain.editorial import (
    RESPONSE_SCHEMA_SCRIPT,
    CoverageIntentResult,
    ProjectBrief,
    ProjectBriefStatus,
    ScriptDraft,
    ScriptDraftStatus,
    ScriptSourceKind,
    compute_observation_set_fingerprint,
)
from otio_app.discovery_v2.editorial_paths import (
    EditorialPathError,
    editorial_brief_json_relative_path,
    normalize_editorial_relative_path,
)
from otio_app.discovery_v2.persistence import asset_registry_database as reg_db
from otio_app.discovery_v2.persistence.editorial_repository import editorial_table_names

from test_discovery_v2_analysis_prepare import _new_project, _now


def test_schema_14_to_15_preserves_assets_and_adds_editorial_tables(
    tmp_path: Path, temp_db_path: Path
) -> None:
    root = tmp_path / "Project"
    (root / "Media").mkdir(parents=True)
    project = _new_project(root, temp_db_path, name="Phase 9 Schema")
    conn = reg_db.get_registry_connection(root)
    try:
        assert reg_db.read_schema_version(conn) == "20"
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
        conn.execute("UPDATE registry_schema SET schema_version = '14'")
        conn.commit()
    finally:
        conn.close()

    conn2 = reg_db.get_registry_connection(root)
    try:
        assert reg_db.read_schema_version(conn2) == REGISTRY_SCHEMA_VERSION == "20"
        assert conn2.execute(
            "SELECT COUNT(*) FROM assets WHERE asset_id = 'asset-keep'"
        ).fetchone()[0] == 1
        assert {
            "editorial_project_state",
            "editorial_runs",
            "editorial_attempts",
            "project_briefs",
            "narrative_plans",
            "hook_variants",
            "script_drafts",
            "script_sentences",
            "script_claims",
            "visual_beats",
            "visual_beat_sentences",
            "visual_intents",
            "coverage_audits",
            "coverage_intent_results",
        } == editorial_table_names(conn2)
    finally:
        conn2.close()


def test_editorial_domain_forbids_extra_and_locked_status() -> None:
    now = datetime.now(timezone.utc)
    brief = ProjectBrief(
        project_brief_id="brief-1",
        project_id="project-1",
        language="de",
        topic="Topic",
        target_audience="Audience",
        tone="klar",
        brief_version=1,
        content_sha256="a" * 64,
        status=ProjectBriefStatus.ACTIVE,
        created_at=now,
    )
    with pytest.raises(ValidationError):
        ProjectBrief.model_validate({**brief.model_dump(mode="json"), "extra": "no"})
    with pytest.raises(ValidationError):
        ScriptDraft.model_validate(
            {
                "schema_version": RESPONSE_SCHEMA_SCRIPT,
                "script_id": "script-1",
                "script_version": 1,
                "project_id": "project-1",
                "language": "de",
                "full_text": "Text.",
                "sentence_order": [],
                "narrative_plan_id": "plan-1",
                "selected_hook_id": None,
                "project_brief_id": "brief-1",
                "brief_version": 1,
                "prompt_version": "p",
                "gateway_version": "g",
                "model_identifier": "m",
                "provider": "fake",
                "source_kind": ScriptSourceKind.LLM.value,
                "supersedes_script_id": None,
                "content_sha256": "b" * 64,
                "status": "locked",
                "created_at": now.isoformat(),
            }
        )


def test_editorial_paths_are_isolated_under_otio_v2_editorial() -> None:
    assert editorial_brief_json_relative_path("brief-1") == "editorial/briefs/brief-1.json"
    assert normalize_editorial_relative_path("editorial/runs/run-1.json")
    for bad in ("/tmp/file", "../editorial/x.json", "_otio_v2/editorial/x.json", "analysis/x.json"):
        with pytest.raises(EditorialPathError):
            normalize_editorial_relative_path(bad)


def test_observation_fingerprint_is_stable_and_content_sensitive() -> None:
    class Obs:
        def __init__(self, observation_id: str, asset_id: str, sha: str, frame: str) -> None:
            self.observation_id = observation_id
            self.asset_id = asset_id
            self.observation_sha256 = sha
            self.frame_set_fingerprint = frame

    a = Obs("obs-a", "asset-a", "sha-a", "frame-a")
    b = Obs("obs-b", "asset-b", "sha-b", "frame-b")
    assert compute_observation_set_fingerprint([a, b]) == compute_observation_set_fingerprint([b, a])
    assert compute_observation_set_fingerprint([a, b]) != compute_observation_set_fingerprint([a])


def test_coverage_candidate_limit_is_enforced() -> None:
    with pytest.raises(ValidationError):
        CoverageIntentResult(
            visual_intent_id="intent-1",
            coverage_status="partially_covered",
            candidate_asset_ids=[str(i) for i in range(6)],
            accepted_observation_ids=[],
            rationale="too many",
            confidence=0.5,
            missing_properties=[],
            recommended_next_action="user decision",
        )
