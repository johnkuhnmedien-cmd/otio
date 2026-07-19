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
from otio_app.discovery_v2.application import narration_timing_service
from otio_app.discovery_v2.application.narration_job_recovery import (
    reconcile_orphaned_narration_run,
)
from otio_app.discovery_v2.application.narration_timing_service import (
    start_narration_timing_run,
)
from otio_app.discovery_v2.application.pause_direction_service import start_pause_direction_run
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


def _voice_run(project, run_id: str) -> VoiceGenerationRun:
    conn = narration_repo.open_narration_registry(project.project_root_path)
    try:
        run = narration_repo.get_voice_run(conn, run_id=run_id)
    finally:
        conn.close()
    assert run is not None
    return run


def _latest_voice_scope_run(project) -> VoiceGenerationRun:
    conn = narration_repo.open_narration_registry(project.project_root_path)
    try:
        runs = [
            run
            for run in narration_repo.list_voice_runs(conn, project_id=project.id)
            if run.scope == NARRATION_RUN_SCOPE_VOICE
        ]
    finally:
        conn.close()
    assert runs
    return runs[0]


def _audio_files(project) -> set[Path]:
    root = project.project_root_path / "_otio_v2" / "narration" / "audio"
    return set(root.rglob("*.wav")) if root.exists() else set()


def _timeline_files(project) -> set[Path]:
    root = project.project_root_path / "_otio_v2" / "narration" / "timelines"
    return set(root.rglob("*.json")) if root.exists() else set()


def _pause_ready_project(tmp_path: Path, temp_db_path: Path):
    project = _locked_project(tmp_path, temp_db_path)
    voice = start_voice_generation_run(project, sync=True)
    assert voice.started and voice.run is not None
    pause = start_pause_direction_run(project, sync=True)
    assert pause.started and pause.run is not None
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


def test_crash_window_abort_before_first_wav_publication(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _locked_project(tmp_path, temp_db_path)

    def abort_before_publish(*_args, **_kwargs):
        raise RuntimeError("abort-before-first-wav")

    monkeypatch.setattr(narration_repo, "publish_voice_wav", abort_before_publish)
    with pytest.raises(RuntimeError, match="abort-before-first-wav"):
        start_voice_generation_run(project, sync=True)
    run = _latest_voice_scope_run(project)
    assert _voice_run(project, run.run_id).status == NarrationRunStatus.RUNNING
    assert _audio_files(project) == set()
    assert not narration_temp_dir(project.project_root_path, run.run_id).exists()

    calls_before_recovery = fake_voice_call_count()
    reconcile_orphaned_narration_run(project)
    recovered = _voice_run(project, run.run_id)
    assert recovered.status == NarrationRunStatus.FAILED
    assert fake_voice_call_count() == calls_before_recovery

    monkeypatch.undo()
    restart = start_voice_generation_run(project, sync=True)
    assert restart.started and restart.run is not None
    assert restart.run.status == NarrationRunStatus.COMPLETED
    assert restart.run.segments_reused == 0
    assert len(_audio_files(project)) == restart.run.sentence_count


def test_crash_window_abort_after_one_of_multiple_published_wavs(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _locked_project(tmp_path, temp_db_path)
    real_publish = narration_repo.publish_voice_wav
    calls = {"publish": 0}

    def abort_on_second_publish(*args, **kwargs):
        calls["publish"] += 1
        if calls["publish"] == 2:
            raise RuntimeError("abort-after-one-wav")
        return real_publish(*args, **kwargs)

    monkeypatch.setattr(narration_repo, "publish_voice_wav", abort_on_second_publish)
    with pytest.raises(RuntimeError, match="abort-after-one-wav"):
        start_voice_generation_run(project, sync=True)
    run = _latest_voice_scope_run(project)
    published_before = _audio_files(project)
    assert len(published_before) == 1
    assert _voice_run(project, run.run_id).status == NarrationRunStatus.RUNNING
    assert not narration_temp_dir(project.project_root_path, run.run_id).exists()

    calls_before_recovery = fake_voice_call_count()
    reconcile_orphaned_narration_run(project)
    assert _voice_run(project, run.run_id).status == NarrationRunStatus.FAILED
    assert fake_voice_call_count() == calls_before_recovery

    monkeypatch.undo()
    restart = start_voice_generation_run(project, sync=True)
    assert restart.started and restart.run is not None
    assert restart.run.status == NarrationRunStatus.COMPLETED
    assert restart.run.segments_reused == 1
    published_after = _audio_files(project)
    assert published_before.issubset(published_after)
    assert len(published_after) == restart.run.sentence_count


def test_crash_window_abort_after_all_wavs_before_voice_completion_state(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _locked_project(tmp_path, temp_db_path)
    real_save_report = narration_repo.save_run_report

    def abort_after_report(*args, **kwargs):
        real_save_report(*args, **kwargs)
        raise RuntimeError("abort-after-all-wavs")

    monkeypatch.setattr(narration_repo, "save_run_report", abort_after_report)
    with pytest.raises(RuntimeError, match="abort-after-all-wavs"):
        start_voice_generation_run(project, sync=True)
    run = _latest_voice_scope_run(project)
    published_before = _audio_files(project)
    assert len(published_before) == run.sentence_count
    assert _voice_run(project, run.run_id).status == NarrationRunStatus.RUNNING
    conn = narration_repo.open_narration_registry(project.project_root_path)
    try:
        state = narration_repo.get_project_state(conn, project_id=project.id)
        assert state is not None
        # L4 binds Narration current_voice_run_id atomically at Voice start.
        assert state.current_voice_run_id == run.run_id
    finally:
        conn.close()
    assert not narration_temp_dir(project.project_root_path, run.run_id).exists()

    calls_before_recovery = fake_voice_call_count()
    reconcile_orphaned_narration_run(project)
    assert _voice_run(project, run.run_id).status == NarrationRunStatus.FAILED
    assert fake_voice_call_count() == calls_before_recovery

    monkeypatch.undo()
    restart = start_voice_generation_run(project, sync=True)
    assert restart.started and restart.run is not None
    assert restart.run.status == NarrationRunStatus.COMPLETED
    assert restart.run.segments_reused == restart.run.sentence_count
    assert _audio_files(project) == published_before


def test_crash_window_abort_after_timeline_json_before_current_state_update(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _pause_ready_project(tmp_path, temp_db_path)
    real_insert_timeline = narration_timing_service.repo.insert_timeline

    def abort_after_timeline_insert(*args, **kwargs):
        real_insert_timeline(*args, **kwargs)
        raise RuntimeError("abort-after-timeline-json")

    monkeypatch.setattr(
        narration_timing_service.repo,
        "insert_timeline",
        abort_after_timeline_insert,
    )
    calls_before_timing = fake_voice_call_count()
    failed = start_narration_timing_run(project, sync=True)
    assert not failed.started
    assert failed.run is not None
    assert failed.run.status == NarrationRunStatus.FAILED
    timeline_files_before = _timeline_files(project)
    assert len(timeline_files_before) == 1
    conn = narration_repo.open_narration_registry(project.project_root_path)
    try:
        state = narration_repo.get_project_state(conn, project_id=project.id)
        assert state is not None
        assert state.current_timeline_id is None
        assert narration_repo.list_timelines(conn, project_id=project.id) == []
    finally:
        conn.close()

    reconcile_orphaned_narration_run(project)
    assert fake_voice_call_count() == calls_before_timing

    monkeypatch.undo()
    restart = start_narration_timing_run(project, sync=True)
    assert restart.started and restart.run is not None
    assert restart.run.status == NarrationRunStatus.COMPLETED
    assert fake_voice_call_count() == calls_before_timing
    assert _timeline_files(project) == timeline_files_before
