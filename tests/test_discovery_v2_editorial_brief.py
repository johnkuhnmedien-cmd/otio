"""Phase 9 Project Brief service tests."""

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
    get_active_project_brief,
    list_project_briefs,
    save_project_brief,
    start_narrative_run,
)
from otio_app.discovery_v2.domain.asset_analysis import (
    ANALYSIS_RUN_SCOPE_PREPARE_ONLY,
    AnalysisRun,
    AnalysisRunStatus,
)
from otio_app.discovery_v2.domain.editorial import (
    EDITORIAL_ERROR_ANALYSIS_RUN_ALREADY_ACTIVE,
    ProjectBriefStatus,
)
from otio_app.discovery_v2.persistence.asset_analysis_repository import (
    insert_analysis_run,
    new_analysis_run_id,
    open_analysis_registry,
)

from test_discovery_v2_analysis_prepare import _new_project, _now


@pytest.fixture(autouse=True)
def _reset_state() -> None:
    reset_analysis_job_launcher_for_tests()
    reset_editorial_job_launcher_for_tests()
    reset_fake_text_test_hook()
    yield
    reset_fake_text_test_hook()
    reset_editorial_job_launcher_for_tests()
    reset_analysis_job_launcher_for_tests()


def _project(tmp_path: Path, temp_db_path: Path):
    root = tmp_path / "Project"
    (root / "Media").mkdir(parents=True)
    return _new_project(root, temp_db_path, name="Phase 9 Brief")


def test_save_project_brief_versions_and_supersedes(tmp_path: Path, temp_db_path: Path) -> None:
    project = _project(tmp_path, temp_db_path)
    first = save_project_brief(
        project,
        language="de",
        topic="Erster Brief",
        target_audience="Audience",
        tone="klar",
        must_include=["A"],
    )
    second = save_project_brief(
        project,
        language="de",
        topic="Zweiter Brief",
        target_audience="Audience",
        tone="ruhig",
        must_exclude=["B"],
    )
    assert first.ok and second.ok
    active = get_active_project_brief(project)
    assert active is not None
    assert active.topic == "Zweiter Brief"
    assert active.brief_version == 2
    briefs = list_project_briefs(project)
    assert [brief.brief_version for brief in briefs] == [2, 1]
    assert briefs[0].status == ProjectBriefStatus.ACTIVE
    assert briefs[1].status == ProjectBriefStatus.SUPERSEDED


def test_start_narrative_requires_brief(tmp_path: Path, temp_db_path: Path) -> None:
    project = _project(tmp_path, temp_db_path)
    result = start_narrative_run(project, sync=True)
    assert result.started is False
    assert result.error_code == "project_brief_missing"


def test_active_analysis_run_blocks_editorial_start(tmp_path: Path, temp_db_path: Path) -> None:
    project = _project(tmp_path, temp_db_path)
    saved = save_project_brief(
        project,
        language="de",
        topic="Blocked",
        target_audience="Audience",
        tone="klar",
    )
    assert saved.ok
    conn = open_analysis_registry(project.project_root_path)
    try:
        insert_analysis_run(
            conn,
            AnalysisRun(
                run_id=new_analysis_run_id(),
                project_id=project.id,
                scope=ANALYSIS_RUN_SCOPE_PREPARE_ONLY,
                status=AnalysisRunStatus.QUEUED,
                created_at=_now(),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    result = start_narrative_run(project, sync=True)
    assert result.started is False
    assert result.error_code == EDITORIAL_ERROR_ANALYSIS_RUN_ALREADY_ACTIVE
