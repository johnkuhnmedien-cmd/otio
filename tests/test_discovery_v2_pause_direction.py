from __future__ import annotations

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from otio_app.discovery_v2.adapters.analysis_job_launcher import reset_analysis_job_launcher_for_tests
from otio_app.discovery_v2.adapters.editorial_job_launcher import reset_editorial_job_launcher_for_tests
from otio_app.discovery_v2.adapters.narration_job_launcher import reset_narration_job_launcher_for_tests
from otio_app.discovery_v2.adapters.supplementation_job_launcher import reset_supplementation_job_launcher_for_tests
from otio_app.discovery_v2.adapters.text_fake import (
    reset_fake_text_test_hook,
    set_fake_text_test_hook,
)
from otio_app.discovery_v2.adapters.voice_fake import reset_fake_voice_call_count
from otio_app.discovery_v2.application.pause_direction_service import start_pause_direction_run
from otio_app.discovery_v2.application.script_lock_service import create_script_lock, preview_script_lock
from otio_app.discovery_v2.application.voice_generation_service import start_voice_generation_run
from otio_app.discovery_v2.domain.narration import (
    NARRATION_ERROR_INVALID_PAUSE_REFERENCE,
    NARRATION_ERROR_PAUSE_RETRY_EXHAUSTED,
    NarrationRunStatus,
    PauseFunction,
)
from otio_app.discovery_v2.persistence import narration_repository as narration_repo

from test_discovery_v2_script_lock import (
    _decide_all_claims,
    _resolve_all_gaps_locally,
    _script_coverage_project,
)


@pytest.fixture(autouse=True)
def _reset_state() -> None:
    reset_analysis_job_launcher_for_tests()
    reset_editorial_job_launcher_for_tests()
    reset_supplementation_job_launcher_for_tests()
    reset_narration_job_launcher_for_tests()
    reset_fake_text_test_hook()
    reset_fake_voice_call_count()
    yield
    reset_fake_text_test_hook()
    reset_narration_job_launcher_for_tests()
    reset_supplementation_job_launcher_for_tests()
    reset_editorial_job_launcher_for_tests()
    reset_analysis_job_launcher_for_tests()


def _voiced_project(tmp_path: Path, temp_db_path: Path):
    project = _script_coverage_project(tmp_path, temp_db_path)
    _resolve_all_gaps_locally(project)
    _decide_all_claims(project)
    preview = preview_script_lock(project)
    assert create_script_lock(
        project,
        user_confirmed=True,
        confirmed_fingerprint=preview.lock_fingerprint,
    ).ok
    voice = start_voice_generation_run(project, sync=True)
    assert voice.started and voice.run is not None
    return project


def test_pause_direction_requires_completed_voice_run(tmp_path: Path, temp_db_path: Path) -> None:
    project = _script_coverage_project(tmp_path, temp_db_path)
    result = start_pause_direction_run(project, sync=True)
    assert not result.started


def test_pause_direction_persists_valid_fake_plan(tmp_path: Path, temp_db_path: Path) -> None:
    project = _voiced_project(tmp_path, temp_db_path)
    result = start_pause_direction_run(project, sync=True)
    assert result.started and result.run is not None
    assert result.run.status == NarrationRunStatus.COMPLETED
    conn = narration_repo.open_narration_registry(project.project_root_path)
    try:
        state = narration_repo.get_project_state(conn, project_id=project.id)
        assert state is not None and state.current_pause_plan_id
        directions = narration_repo.list_pause_directions(
            conn,
            pause_plan_id=state.current_pause_plan_id,
        )
    finally:
        conn.close()
    assert directions
    assert any(direction.function == PauseFunction.COLD_OPEN for direction in directions)


def test_smoke_e_pause_invalid_ref_retries_and_no_plan(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _voiced_project(tmp_path, temp_db_path)

    def bad_ref(request):
        if request.request_kind != "pause_direction":
            return None
        plan_id = "bad-plan"
        return {
            "pause_plan": {
                "pause_plan_id": plan_id,
                "project_id": request.project_id,
                "script_lock_id": request.selected_hook_id,
                "voice_run_id": request.run_id,
                "prompt_version": request.prompt_version,
                "model_identifier": request.model_identifier,
                "gateway_version": request.gateway_version,
                "response_schema_version": request.response_schema_version,
                "provider": request.provider,
                "input_fingerprint": request.input_fingerprint,
                "global_notes": [],
                "status": "completed",
                "created_at": "2026-01-01T00:00:00+00:00",
            },
            "directions": [
                {
                    "direction_id": "bad-direction",
                    "pause_plan_id": plan_id,
                    "position_kind": "after_sentence",
                    "sentence_id": "missing-sentence",
                    "segment_id": None,
                    "anchor_ordinal": 0,
                    "function": "emphasis",
                    "min_duration_intent_s": 0.15,
                    "preferred_duration_intent_s": 0.25,
                    "max_duration_intent_s": 2.5,
                    "hardness": "soft",
                    "rationale": "bad ref",
                    "uncertainty": "low",
                }
            ],
        }

    set_fake_text_test_hook(bad_ref)
    result = start_pause_direction_run(project, sync=True)
    assert result.started
    conn = narration_repo.open_narration_registry(project.project_root_path)
    try:
        final = narration_repo.get_voice_run(conn, run_id=result.run.run_id)
        assert final is not None
        assert final.status == NarrationRunStatus.FAILED
        assert final.error_code in {
            NARRATION_ERROR_PAUSE_RETRY_EXHAUSTED,
            NARRATION_ERROR_INVALID_PAUSE_REFERENCE,
        }
        state = narration_repo.get_project_state(conn, project_id=project.id)
        assert state is not None
        assert state.current_pause_plan_id is None
    finally:
        conn.close()
