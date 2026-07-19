from __future__ import annotations

from pathlib import Path
import sys
import wave

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from otio_app.discovery_v2.adapters.analysis_job_launcher import reset_analysis_job_launcher_for_tests
from otio_app.discovery_v2.adapters.editorial_job_launcher import reset_editorial_job_launcher_for_tests
from otio_app.discovery_v2.adapters.narration_job_launcher import reset_narration_job_launcher_for_tests
from otio_app.discovery_v2.adapters.supplementation_job_launcher import reset_supplementation_job_launcher_for_tests
from otio_app.discovery_v2.adapters.text_fake import reset_fake_text_test_hook
from otio_app.discovery_v2.adapters.voice_fake import reset_fake_voice_call_count
from otio_app.discovery_v2.application.narration_timing_service import (
    start_narration_timing_run,
)
from otio_app.discovery_v2.application.pause_direction_service import start_pause_direction_run
from otio_app.discovery_v2.application.script_lock_service import create_script_lock, preview_script_lock
from otio_app.discovery_v2.application.voice_generation_service import start_voice_generation_run
from otio_app.discovery_v2.domain.narration import (
    NarrationRunStatus,
    NarrationTimelineEntryType,
)
from otio_app.discovery_v2.narration_paths import resolve_narration_relative_path
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


def _ready_project(tmp_path: Path, temp_db_path: Path, *, fps: float = 25.0):
    project = _script_coverage_project(tmp_path, temp_db_path)
    project = project.model_copy(update={"fps": fps})
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
    pause = start_pause_direction_run(project, sync=True)
    assert pause.started and pause.run is not None
    return project


def test_smoke_a_valid_lock_voice_pause_timing_reads_real_wavs(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _ready_project(tmp_path, temp_db_path)
    result = start_narration_timing_run(project, sync=True)
    assert result.started and result.run is not None
    assert result.run.status == NarrationRunStatus.COMPLETED
    conn = narration_repo.open_narration_registry(project.project_root_path)
    try:
        state = narration_repo.get_project_state(conn, project_id=project.id)
        assert state is not None and state.current_timeline_id
        timeline = narration_repo.get_timeline(conn, timeline_id=state.current_timeline_id)
        segments = narration_repo.list_voice_segments_for_run(conn, run_id=state.current_voice_run_id)
    finally:
        conn.close()
    assert timeline is not None
    assert any(entry.entry_type == NarrationTimelineEntryType.VOICE for entry in timeline.entries)
    assert any(entry.entry_type == NarrationTimelineEntryType.PAUSE for entry in timeline.entries)
    previous_end = 0
    for entry in timeline.entries:
        assert entry.start_frame == previous_end
        assert entry.end_frame > entry.start_frame
        previous_end = entry.end_frame
    for segment in segments:
        path = resolve_narration_relative_path(project.project_root_path, segment.relative_path)
        with wave.open(str(path), "rb") as wav:
            assert wav.getnframes() == segment.sample_count


@pytest.mark.parametrize("fps", [24000 / 1001, 24.0, 25.0, 30000 / 1001, 30.0])
def test_smoke_f_timebases_are_monotone_and_adjacent(
    tmp_path: Path, temp_db_path: Path, fps: float
) -> None:
    project = _ready_project(tmp_path, temp_db_path, fps=fps)
    result = start_narration_timing_run(project, sync=True)
    assert result.started
    conn = narration_repo.open_narration_registry(project.project_root_path)
    try:
        state = narration_repo.get_project_state(conn, project_id=project.id)
        timeline = narration_repo.get_timeline(conn, timeline_id=state.current_timeline_id)
    finally:
        conn.close()
    assert timeline is not None
    assert abs(timeline.timebase.fps - fps) < 0.01
    for previous, current in zip(timeline.entries, timeline.entries[1:]):
        assert current.start_frame == previous.end_frame
        assert current.start_seconds == pytest.approx(previous.end_seconds)
    assert timeline.total_frames == timeline.entries[-1].end_frame
