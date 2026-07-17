from __future__ import annotations

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from otio_app.discovery_v2.adapters.analysis_job_launcher import reset_analysis_job_launcher_for_tests
from otio_app.discovery_v2.adapters.editorial_job_launcher import reset_editorial_job_launcher_for_tests
from otio_app.discovery_v2.adapters.narration_job_launcher import reset_narration_job_launcher_for_tests
from otio_app.discovery_v2.adapters.supplementation_job_launcher import reset_supplementation_job_launcher_for_tests
from otio_app.discovery_v2.adapters.text_fake import reset_fake_text_test_hook
from otio_app.discovery_v2.adapters.voice_fake import (
    fake_voice_call_count,
    reset_fake_voice_call_count,
)
from otio_app.discovery_v2.application.narration_job_recovery import (
    reconcile_orphaned_narration_run,
)
from otio_app.discovery_v2.application.script_lock_service import create_script_lock, preview_script_lock
from otio_app.discovery_v2.application.voice_generation_service import (
    ensure_default_voice_profile,
    start_voice_generation_run,
)
from otio_app.discovery_v2.domain.narration import (
    NARRATION_ERROR_WORKER_INTERRUPTED,
    NARRATION_RUN_SCOPE_VOICE,
    NarrationRunStatus,
    VoiceGenerationRun,
)
from otio_app.discovery_v2.narration_paths import narration_temp_dir
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


def _locked_project(tmp_path: Path, temp_db_path: Path):
    project = _script_coverage_project(tmp_path, temp_db_path)
    _resolve_all_gaps_locally(project)
    _decide_all_claims(project)
    preview = preview_script_lock(project)
    assert create_script_lock(
        project,
        user_confirmed=True,
        confirmed_fingerprint=preview.lock_fingerprint,
    ).ok
    return project


def test_recovery_marks_orphaned_narration_run_failed_and_cleans_temp(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _locked_project(tmp_path, temp_db_path)
    profile = ensure_default_voice_profile(project)
    conn = narration_repo.open_narration_registry(project.project_root_path)
    try:
        run = VoiceGenerationRun(
            run_id=narration_repo.new_voice_run_id(),
            project_id=project.id,
            script_lock_id="lock-1",
            script_id="script-1",
            voice_profile_id=profile.voice_profile_id,
            input_fingerprint="fp",
            scope=NARRATION_RUN_SCOPE_VOICE,
            status=NarrationRunStatus.QUEUED,
            sentence_count=1,
            created_at=profile.created_at,
        )
        narration_repo.insert_voice_run(conn, run)
        conn.commit()
    finally:
        conn.close()
    temp = narration_temp_dir(project.project_root_path, run.run_id)
    temp.mkdir(parents=True)
    (temp / "own.tmp").write_text("temp")
    reconcile_orphaned_narration_run(project)
    conn = narration_repo.open_narration_registry(project.project_root_path)
    try:
        final = narration_repo.get_voice_run(conn, run_id=run.run_id)
    finally:
        conn.close()
    assert final is not None
    assert final.status == NarrationRunStatus.FAILED
    assert final.error_code == NARRATION_ERROR_WORKER_INTERRUPTED
    assert not temp.exists()


def test_smoke_g_restart_reuses_published_segments_without_duplicates(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _locked_project(tmp_path, temp_db_path)
    first = start_voice_generation_run(project, sync=True)
    assert first.started and first.run is not None
    calls = fake_voice_call_count()
    conn = narration_repo.open_narration_registry(project.project_root_path)
    try:
        first_segments = narration_repo.list_voice_segments_for_run(conn, run_id=first.run.run_id)
    finally:
        conn.close()
    second = start_voice_generation_run(project, sync=True)
    assert second.started and second.run is not None
    assert fake_voice_call_count() == calls
    conn = narration_repo.open_narration_registry(project.project_root_path)
    try:
        second_segments = narration_repo.list_voice_segments_for_run(conn, run_id=second.run.run_id)
        all_lock_segments = narration_repo.list_voice_segments_for_lock(
            conn,
            script_lock_id=first.run.script_lock_id,
        )
    finally:
        conn.close()
    assert [s.segment_id for s in first_segments] == [s.segment_id for s in second_segments]
    assert len({s.segment_id for s in all_lock_segments}) == len(all_lock_segments)
