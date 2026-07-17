"""Phase 10 coverage gap materialization and escalation tests."""

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
from otio_app.discovery_v2.adapters.supplementation_job_launcher import (
    reset_supplementation_job_launcher_for_tests,
)
from otio_app.discovery_v2.adapters.text_fake import reset_fake_text_test_hook
from otio_app.discovery_v2.application.coverage_gap_service import (
    accept_gap_unresolved,
    escalate_gap,
    materialize_gaps_from_current_coverage,
)
from otio_app.discovery_v2.application.editorial_service import (
    get_editorial_view,
    start_coverage_run,
    start_script_run,
)
from otio_app.discovery_v2.domain.supplementation import (
    CoverageGapStatus,
    CoverageLevel,
    CoverageRiskFlag,
    EscalationStep,
)

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


def _coverage_project(tmp_path: Path, temp_db_path: Path):
    project = _accepted_editorial_project(tmp_path, temp_db_path)
    _brief_to_narrative(project)
    assert start_script_run(project, sync=True).started
    assert start_coverage_run(project, sync=True).started
    assert get_editorial_view(project).coverage_audit is not None
    return project


def test_materialize_gaps_from_current_coverage_and_escalate_in_locked_order(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _coverage_project(tmp_path, temp_db_path)
    result = materialize_gaps_from_current_coverage(project)
    assert result.ok
    assert result.gaps
    assert {gap.coverage_level for gap in result.gaps} == {CoverageLevel.PARTIALLY_COVERED}
    gap = result.gaps[0]
    assert gap.status == CoverageGapStatus.OPEN
    assert gap.current_escalation_step == EscalationStep.LOCAL_DEEPER_REVIEW

    step2 = escalate_gap(project, gap_id=gap.gap_id)
    assert step2.ok and step2.gap is not None
    assert step2.gap.current_escalation_step == EscalationStep.PHOTO
    step3 = escalate_gap(project, gap_id=gap.gap_id)
    assert step3.ok and step3.gap is not None
    assert step3.gap.current_escalation_step == EscalationStep.BETTER_SEARCH


def test_accepted_unresolved_keeps_risk_flags_separate_from_coverage_level(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _coverage_project(tmp_path, temp_db_path)
    gap = materialize_gaps_from_current_coverage(project).gaps[0]
    risk_gap = gap.model_copy(update={"risk_flags": [CoverageRiskFlag.TOO_GENERIC]})
    from otio_app.discovery_v2.persistence import supplementation_repository as repo

    conn = repo.open_supplementation_registry(project.project_root_path)
    try:
        repo.update_coverage_gap(conn, risk_gap)
        conn.commit()
    finally:
        conn.close()
    accepted = accept_gap_unresolved(
        project,
        gap_id=gap.gap_id,
        confirmed_risks=["too_generic"],
    )
    assert accepted.ok and accepted.gap is not None
    assert accepted.gap.status == CoverageGapStatus.ACCEPTED_UNRESOLVED
    assert accepted.gap.coverage_level == CoverageLevel.PARTIALLY_COVERED
    assert [risk.value for risk in accepted.gap.accepted_unresolved_risks] == ["too_generic"]
