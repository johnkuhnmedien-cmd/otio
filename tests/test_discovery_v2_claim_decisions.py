"""Phase 10 claim decision tests."""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from otio_app.discovery_v2.adapters.analysis_job_launcher import reset_analysis_job_launcher_for_tests
from otio_app.discovery_v2.adapters.editorial_job_launcher import reset_editorial_job_launcher_for_tests
from otio_app.discovery_v2.adapters.supplementation_job_launcher import reset_supplementation_job_launcher_for_tests
from otio_app.discovery_v2.adapters.text_fake import reset_fake_text_test_hook
from otio_app.discovery_v2.application.editorial_service import get_editorial_view, start_script_run
from otio_app.discovery_v2.application.supplementation_service import record_claim_decision
from otio_app.discovery_v2.domain.supplementation import ClaimDecisionValue
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


def test_claim_decisions_are_append_only_and_bound_to_claim_hash(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _accepted_editorial_project(tmp_path, temp_db_path)
    _brief_to_narrative(project)
    assert start_script_run(project, sync=True).started
    view = get_editorial_view(project)
    assert view.script is not None and view.script_bundle is not None
    claim = view.script_bundle["claims"][0]
    first = record_claim_decision(
        project,
        script_id=view.script.script_id,
        claim_id=claim["claim_id"],
        claim_text=claim["statement"],
        decision="confirmed",
    )
    second = record_claim_decision(
        project,
        script_id=view.script.script_id,
        claim_id=claim["claim_id"],
        claim_text=claim["statement"],
        decision="accepted_as_uncertain",
    )
    assert first.revision == 1
    assert second.revision == 2
    conn = repo.open_supplementation_registry(project.project_root_path)
    try:
        latest = repo.latest_claim_decisions_for_script(
            conn,
            project_id=project.id,
            script_id=view.script.script_id,
        )
    finally:
        conn.close()
    assert latest[claim["claim_id"]].decision == ClaimDecisionValue.ACCEPTED_AS_UNCERTAIN
    assert latest[claim["claim_id"]].claim_content_sha256 == first.claim_content_sha256
