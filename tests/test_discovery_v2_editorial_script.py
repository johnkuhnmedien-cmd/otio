"""Phase 9 Editorial script E2E smokes."""

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
    save_user_script_edit,
    select_hook,
    start_narrative_run,
    start_script_run,
    start_structure_run,
)
from otio_app.discovery_v2.application.model_analysis_service import start_model_analysis
from otio_app.discovery_v2.application.observation_review_service import (
    list_editorial_ready_observations,
    submit_observation_review,
)
from otio_app.discovery_v2.domain.asset_analysis import AnalysisRunStatus
from otio_app.discovery_v2.domain.editorial import ClaimStatus, ScriptDraftStatus
from otio_app.discovery_v2.persistence import asset_analysis_repository as analysis_repo
from otio_app.discovery_v2.persistence import editorial_repository as editorial_repo

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


def _accepted_editorial_project(tmp_path: Path, temp_db_path: Path):
    project = _prepared_still_project(tmp_path, temp_db_path)
    result = start_model_analysis(project, asset_ids=None, consent_acknowledged=True, sync=True)
    assert result.started and result.run is not None
    assert result.run.status == AnalysisRunStatus.COMPLETED
    conn = analysis_repo.open_analysis_registry(project.project_root_path)
    try:
        obs = analysis_repo.list_visual_observations_for_project(conn, project_id=project.id)[0]
    finally:
        conn.close()
    review = submit_observation_review(project, observation_id=obs.observation_id, decision="accepted")
    assert review.ok
    assert len(list_editorial_ready_observations(project)) == 1
    return project


def _brief_to_narrative(project):
    saved = save_project_brief(
        project,
        language="de",
        topic="Lokale Geschichte",
        target_audience="Audience",
        tone="klar",
    )
    assert saved.ok
    narrative = start_narrative_run(project, sync=True)
    assert narrative.started and narrative.run is not None
    view = get_editorial_view(project)
    assert view.narrative_plan is not None
    assert len(view.hooks) == 3
    selected = select_hook(project, hook_id=view.hooks[0].hook_id)
    assert selected.ok
    return get_editorial_view(project)


def test_fake_e2e_narrative_hooks_script_many_to_many_and_claims(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _accepted_editorial_project(tmp_path, temp_db_path)
    view = _brief_to_narrative(project)
    assert view.selected_hook_id == view.hooks[0].hook_id

    script_result = start_script_run(project, sync=True)
    assert script_result.started and script_result.run is not None
    view = get_editorial_view(project)
    assert view.script is not None
    assert view.script.status == ScriptDraftStatus.REVIEW_REQUESTED
    bundle = view.script_bundle or {}
    assert len(bundle["sentences"]) >= 3
    assert bundle["claims"]
    assert {claim["status"] for claim in bundle["claims"]}.issubset(
        {ClaimStatus.UNCERTAIN.value, ClaimStatus.USER_CONFIRMATION_REQUIRED.value}
    )
    beats = bundle["visual_beats"]
    assert any(len(beat["sentence_ids"]) > 1 for beat in beats)
    assert all(beat["function"] != "Media" for beat in beats)
    sentence_to_beats: dict[str, int] = {}
    for beat in beats:
        for sentence_id in beat["sentence_ids"]:
            sentence_to_beats[sentence_id] = sentence_to_beats.get(sentence_id, 0) + 1
    assert any(count > 1 for count in sentence_to_beats.values())


def test_user_script_edit_creates_new_version_and_structure_refresh(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _accepted_editorial_project(tmp_path, temp_db_path)
    _brief_to_narrative(project)
    assert start_script_run(project, sync=True).started
    view = get_editorial_view(project)
    assert view.script is not None
    edited = save_user_script_edit(project, full_text=view.script.full_text + " Neuer Satz.")
    assert edited.ok and edited.script is not None
    assert edited.script.script_version == 2
    assert edited.script.status == ScriptDraftStatus.USER_EDITED
    stale_view = get_editorial_view(project)
    assert stale_view.stale is True
    assert start_structure_run(project, sync=True).started
    refreshed = get_editorial_view(project)
    assert refreshed.script is not None
    assert refreshed.script.script_version == 2
    assert refreshed.script_bundle is not None
    assert refreshed.script_bundle["sentences"]


def test_explicit_narrative_start_reuses_cached_active_plan(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _accepted_editorial_project(tmp_path, temp_db_path)
    _brief_to_narrative(project)
    second = start_narrative_run(project, sync=True)
    assert second.started and second.run is not None
    conn = editorial_repo.open_editorial_registry(project.project_root_path)
    try:
        attempts = editorial_repo.list_editorial_attempts(conn, run_id=second.run.run_id)
    finally:
        conn.close()
    assert len(attempts) == 1
    assert attempts[0].status.value == "reused"
