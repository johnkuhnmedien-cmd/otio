"""Phase 9 Editorial coverage tests."""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from otio_app.discovery_v2.adapters.analysis_job_launcher import (
    reset_analysis_job_launcher_for_tests,
)
from otio_app.discovery_v2.adapters.editorial_job_launcher import (
    reset_editorial_job_launcher_for_tests,
)
from otio_app.discovery_v2.adapters.text_fake import reset_fake_text_test_hook
from otio_app.discovery_v2.application.editorial_service import (
    get_editorial_view,
    save_project_brief,
    select_hook,
    start_coverage_run,
    start_narrative_run,
    start_script_run,
)
from otio_app.discovery_v2.application.model_analysis_service import start_model_analysis
from otio_app.discovery_v2.application.observation_review_service import (
    submit_observation_review,
)
from otio_app.discovery_v2.domain.asset_analysis import AnalysisRunStatus
from otio_app.discovery_v2.domain.editorial import (
    EDITORIAL_RUN_SCOPE_NARRATIVE,
    CoverageStatus,
    EditorialRun,
    EditorialRunStatus,
)
from otio_app.discovery_v2.persistence import editorial_repository as editorial_repo
from otio_app.discovery_v2.persistence import asset_analysis_repository as analysis_repo

from test_discovery_v2_analysis_prepare import _new_project, _now
from test_discovery_v2_editorial_script import _accepted_editorial_project, _brief_to_narrative
from test_discovery_v2_model_analysis_fake import _prepared_still_project


@pytest.fixture(autouse=True)
def _reset_state() -> None:
    reset_analysis_job_launcher_for_tests()
    reset_editorial_job_launcher_for_tests()
    reset_fake_text_test_hook()
    yield
    reset_fake_text_test_hook()
    reset_editorial_job_launcher_for_tests()
    reset_analysis_job_launcher_for_tests()


def _empty_project(tmp_path: Path, temp_db_path: Path):
    root = tmp_path / "Project"
    (root / "Media").mkdir(parents=True)
    return _new_project(root, temp_db_path, name="Phase 9 Coverage Empty")


def test_coverage_uses_local_candidates_max_five_and_requires_user_decision(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _accepted_editorial_project(tmp_path, temp_db_path)
    _brief_to_narrative(project)
    assert start_script_run(project, sync=True).started
    assert start_coverage_run(project, sync=True).started
    view = get_editorial_view(project)
    assert view.coverage_audit is not None
    assert view.coverage_audit.results
    for result in view.coverage_audit.results:
        assert result.coverage_status == CoverageStatus.PARTIALLY_COVERED
        assert len(result.candidate_asset_ids) <= 5
        assert result.accepted_observation_ids
        assert "Phase 10" in result.recommended_next_action


def test_coverage_not_covered_when_no_local_candidates_and_no_stock_search(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _empty_project(tmp_path, temp_db_path)
    assert save_project_brief(
        project,
        language="de",
        topic="Ohne Kandidaten",
        target_audience="Audience",
        tone="klar",
    ).ok
    assert start_narrative_run(project, sync=True).started
    view = get_editorial_view(project)
    assert len(view.hooks) == 3
    assert select_hook(project, hook_id=view.hooks[0].hook_id).ok
    assert start_script_run(project, sync=True).started
    assert start_coverage_run(project, sync=True).started
    view = get_editorial_view(project)
    assert view.coverage_audit is not None
    assert {r.coverage_status for r in view.coverage_audit.results} == {CoverageStatus.NOT_COVERED}
    assert all("Stock" in r.recommended_next_action or "Stock-Suche" in r.recommended_next_action for r in view.coverage_audit.results)


def test_active_editorial_run_blocks_model_analysis(tmp_path: Path, temp_db_path: Path) -> None:
    project = _accepted_editorial_project(tmp_path, temp_db_path)
    conn = editorial_repo.open_editorial_registry(project.project_root_path)
    try:
        editorial_repo.insert_editorial_run(
            conn,
            EditorialRun(
                run_id=editorial_repo.new_editorial_run_id(),
                project_id=project.id,
                scope=EDITORIAL_RUN_SCOPE_NARRATIVE,
                status=EditorialRunStatus.QUEUED,
                created_at=_now(),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    result = start_model_analysis(
        project,
        asset_ids=None,
        consent_acknowledged=True,
        sync=True,
    )
    assert result.started is False
    assert result.error_code == "editorial_run_already_active"


def test_unreviewed_observation_is_excluded_from_narrative_input(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _prepared_still_project(tmp_path, temp_db_path)
    result = start_model_analysis(project, asset_ids=None, consent_acknowledged=True, sync=True)
    assert result.started and result.run is not None
    assert result.run.status == AnalysisRunStatus.COMPLETED
    assert save_project_brief(
        project,
        language="de",
        topic="Unreviewed",
        target_audience="Audience",
        tone="klar",
    ).ok
    assert start_narrative_run(project, sync=True).started
    view = get_editorial_view(project)
    assert view.narrative_plan is not None
    assert view.narrative_plan.input_observation_ids == []
    assert view.editorial_ready_count == 0


def test_rejected_after_acceptance_is_excluded_from_coverage_inputs(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _accepted_editorial_project(tmp_path, temp_db_path)
    _brief_to_narrative(project)
    assert start_script_run(project, sync=True).started
    conn = analysis_repo.open_analysis_registry(project.project_root_path)
    try:
        obs = analysis_repo.list_visual_observations_for_project(conn, project_id=project.id)[0]
    finally:
        conn.close()
    rejected = submit_observation_review(
        project,
        observation_id=obs.observation_id,
        decision="rejected",
        reason_code="not_editorial",
    )
    assert rejected.ok
    assert start_coverage_run(project, sync=True).started
    view = get_editorial_view(project)
    assert view.coverage_audit is not None
    assert all(not result.accepted_observation_ids for result in view.coverage_audit.results)
    assert {result.coverage_status for result in view.coverage_audit.results} == {CoverageStatus.NOT_COVERED}
