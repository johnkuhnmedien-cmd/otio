"""Phase 10 stock candidate decision tests."""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from otio_app.discovery_v2.adapters.analysis_job_launcher import reset_analysis_job_launcher_for_tests
from otio_app.discovery_v2.adapters.editorial_job_launcher import reset_editorial_job_launcher_for_tests
from otio_app.discovery_v2.adapters.supplementation_job_launcher import reset_supplementation_job_launcher_for_tests
from otio_app.discovery_v2.adapters.text_fake import reset_fake_text_test_hook
from otio_app.discovery_v2.application.coverage_gap_service import materialize_gaps_from_current_coverage
from otio_app.discovery_v2.application.editorial_service import start_coverage_run, start_script_run
from otio_app.discovery_v2.application.supplementation_service import (
    record_candidate_decision,
    start_search_run,
)
from otio_app.discovery_v2.domain.supplementation import StockCandidateUserStatus
from otio_app.discovery_v2.persistence import supplementation_repository as repo

from test_discovery_v2_editorial_script import _accepted_editorial_project, _brief_to_narrative


@pytest.fixture(autouse=True)
def _reset_state() -> None:
    reset_analysis_job_launcher_for_tests()
    reset_editorial_job_launcher_for_tests()
    reset_supplementation_job_launcher_for_tests()
    reset_fake_text_test_hook()
    yield
    reset_fake_text_test_hook()
    reset_supplementation_job_launcher_for_tests()
    reset_editorial_job_launcher_for_tests()
    reset_analysis_job_launcher_for_tests()


def _candidate_project(tmp_path: Path, temp_db_path: Path):
    project = _accepted_editorial_project(tmp_path, temp_db_path)
    _brief_to_narrative(project)
    assert start_script_run(project, sync=True).started
    assert start_coverage_run(project, sync=True).started
    gaps = materialize_gaps_from_current_coverage(project).gaps
    assert start_search_run(project, gap_ids=[gaps[0].gap_id], sync=True).started
    conn = repo.open_supplementation_registry(project.project_root_path)
    try:
        candidate = repo.list_stock_candidates_for_gap(conn, gap_id=gaps[0].gap_id)[0]
    finally:
        conn.close()
    return project, candidate


def test_candidate_decisions_are_append_only_and_update_current_status(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project, candidate = _candidate_project(tmp_path, temp_db_path)
    first = record_candidate_decision(
        project,
        candidate_id=candidate.candidate_id,
        decision="needs_review",
        reason="unsicher",
    )
    second = record_candidate_decision(
        project,
        candidate_id=candidate.candidate_id,
        decision="accepted_for_import",
        reason="passt",
    )
    assert first.ok and second.ok
    conn = repo.open_supplementation_registry(project.project_root_path)
    try:
        decisions = repo.list_candidate_decisions(conn, candidate_id=candidate.candidate_id)
        updated = repo.get_stock_candidate(conn, candidate_id=candidate.candidate_id)
    finally:
        conn.close()
    assert [decision.revision for decision in decisions] == [1, 2]
    assert updated is not None
    assert updated.user_status == StockCandidateUserStatus.ACCEPTED_FOR_IMPORT
