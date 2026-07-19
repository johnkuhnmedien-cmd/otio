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
from otio_app.discovery_v2.adapters.voice_fake import (
    fake_voice_call_count,
    reset_fake_voice_call_count,
)
from otio_app.discovery_v2.application.editorial_service import save_user_script_edit
from otio_app.discovery_v2.application.script_lock_service import (
    create_script_lock,
    preview_script_lock,
)
from otio_app.discovery_v2.application.narration_timing_service import (
    start_narration_timing_run,
)
from otio_app.discovery_v2.application.pause_direction_service import (
    start_pause_direction_run,
)
from otio_app.discovery_v2.application.voice_generation_service import (
    rotate_fake_voice_profile,
    start_voice_generation_run,
)
from otio_app.discovery_v2.domain.narration import (
    NARRATION_ERROR_SCRIPT_LOCK_INVALIDATED,
    NARRATION_ERROR_SCRIPT_LOCK_MISSING,
    NARRATION_ERROR_VOICE_GENERATION_FAILED,
    VOICE_CHANNELS,
    VOICE_SAMPLE_RATE_HZ,
    NarrationRunStatus,
    VoiceProfileStatus,
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
    reset_fake_voice_call_count()
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
    result = create_script_lock(
        project,
        user_confirmed=True,
        confirmed_fingerprint=preview.lock_fingerprint,
    )
    assert result.ok
    return project


def test_voice_generation_requires_effective_script_lock(tmp_path: Path, temp_db_path: Path) -> None:
    project = _script_coverage_project(tmp_path, temp_db_path)
    result = start_voice_generation_run(project, sync=True)
    assert not result.started


def test_phase10_regression_accepted_candidate_without_intake_cannot_start_narration(
    tmp_path: Path, temp_db_path: Path
) -> None:
    """accepted_for_import alone never yields an effective lock / narration start."""
    from otio_app.discovery_v2.application.coverage_gap_service import (
        mark_gap_resolved_with_local_asset,
        materialize_gaps_from_current_coverage,
    )
    from otio_app.discovery_v2.application.supplementation_service import (
        record_candidate_decision,
        start_search_run,
    )
    from otio_app.discovery_v2.persistence import supplementation_repository as supp_repo

    project = _script_coverage_project(tmp_path, temp_db_path)
    gaps = materialize_gaps_from_current_coverage(project).gaps
    assert start_search_run(project, gap_ids=[gaps[0].gap_id], sync=True).started
    conn = supp_repo.open_supplementation_registry(project.project_root_path)
    try:
        candidate = supp_repo.list_stock_candidates_for_gap(conn, gap_id=gaps[0].gap_id)[0]
    finally:
        conn.close()
    assert record_candidate_decision(
        project,
        candidate_id=candidate.candidate_id,
        decision="accepted_for_import",
        reason="Metadaten passen",
    ).ok
    for gap in gaps[1:]:
        mark_gap_resolved_with_local_asset(project, gap_id=gap.gap_id, asset_id="asset-local")
    _decide_all_claims(project)
    preview = preview_script_lock(project)
    assert not preview.ok
    result = start_voice_generation_run(project, sync=True)
    assert not result.started
    assert fake_voice_call_count() == 0


def test_voice_generation_writes_real_wavs_for_locked_sentences(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _locked_project(tmp_path, temp_db_path)
    result = start_voice_generation_run(project, sync=True)
    assert result.started and result.run is not None
    assert result.run.status == NarrationRunStatus.COMPLETED
    conn = narration_repo.open_narration_registry(project.project_root_path)
    try:
        segments = narration_repo.list_voice_segments_for_run(conn, run_id=result.run.run_id)
    finally:
        conn.close()
    assert segments
    for segment in segments:
        path = resolve_narration_relative_path(project.project_root_path, segment.relative_path)
        with wave.open(str(path), "rb") as wav:
            assert wav.getframerate() == VOICE_SAMPLE_RATE_HZ
            assert wav.getnchannels() == VOICE_CHANNELS
            assert wav.getnframes() == segment.sample_count


def test_smoke_b_second_explicit_voice_run_reuses_segments_without_adapter_calls(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _locked_project(tmp_path, temp_db_path)
    first = start_voice_generation_run(project, sync=True)
    assert first.started and first.run is not None
    first_calls = fake_voice_call_count()
    second = start_voice_generation_run(project, sync=True)
    assert second.started and second.run is not None
    assert fake_voice_call_count() == first_calls
    assert second.run.segments_reused == first.run.sentence_count
    conn = narration_repo.open_narration_registry(project.project_root_path)
    try:
        first_segments = narration_repo.list_voice_segments_for_run(conn, run_id=first.run.run_id)
        second_segments = narration_repo.list_voice_segments_for_run(conn, run_id=second.run.run_id)
    finally:
        conn.close()
    assert [s.segment_id for s in first_segments] == [s.segment_id for s in second_segments]


def test_smoke_c_new_voice_profile_creates_new_segments_and_stales_pause_timeline(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _locked_project(tmp_path, temp_db_path)
    first = start_voice_generation_run(project, sync=True)
    assert first.started and first.run is not None
    assert start_pause_direction_run(project, sync=True).started
    assert start_narration_timing_run(project, sync=True).started
    conn = narration_repo.open_narration_registry(project.project_root_path)
    try:
        state_before = narration_repo.get_project_state(conn, project_id=project.id)
        assert state_before is not None
        assert state_before.current_pause_plan_id
        assert state_before.current_timeline_id
        old_segments = narration_repo.list_voice_segments_for_run(
            conn, run_id=first.run.run_id
        )
        old_paths = [
            resolve_narration_relative_path(project.project_root_path, segment.relative_path)
            for segment in old_segments
        ]
        old_profile = narration_repo.get_voice_profile(
            conn, voice_profile_id=first.run.voice_profile_id
        )
    finally:
        conn.close()
    assert old_profile is not None
    for path in old_paths:
        assert path.is_file()

    new_profile = rotate_fake_voice_profile(
        project, voice_settings_version="fake-voice-settings-v2"
    )
    assert new_profile.voice_profile_id != old_profile.voice_profile_id
    assert new_profile.voice_settings_version == "fake-voice-settings-v2"
    assert new_profile.version == old_profile.version + 1
    assert new_profile.supersedes_voice_profile_id == old_profile.voice_profile_id

    second = start_voice_generation_run(project, sync=True)
    assert second.started and second.run is not None
    assert second.run.voice_profile_id == new_profile.voice_profile_id
    assert second.run.segments_created == first.run.sentence_count

    conn = narration_repo.open_narration_registry(project.project_root_path)
    try:
        superseded = narration_repo.get_voice_profile(
            conn, voice_profile_id=old_profile.voice_profile_id
        )
        state_after = narration_repo.get_project_state(conn, project_id=project.id)
        new_segments = narration_repo.list_voice_segments_for_run(
            conn, run_id=second.run.run_id
        )
    finally:
        conn.close()
    assert superseded is not None
    assert superseded.status == VoiceProfileStatus.SUPERSEDED
    assert state_after is not None
    assert state_after.current_pause_plan_id is None
    assert state_after.current_timeline_id is None
    assert {segment.segment_id for segment in new_segments}.isdisjoint(
        {segment.segment_id for segment in old_segments}
    )
    for path in old_paths:
        assert path.is_file()


def test_smoke_d_invalidated_lock_blocks_voice_without_audio(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _locked_project(tmp_path, temp_db_path)
    from otio_app.discovery_v2.application.editorial_service import get_editorial_view

    view = get_editorial_view(project)
    assert view.script is not None
    assert save_user_script_edit(project, full_text=view.script.full_text + " Neuer Satz.").ok
    result = start_voice_generation_run(project, sync=True)
    assert not result.started
    # L4 clears Editorial current on edit → missing or invalidated both block Voice.
    assert result.error_code in {
        NARRATION_ERROR_SCRIPT_LOCK_INVALIDATED,
        "script_lock_invalidated",
        "script_lock_missing",
        NARRATION_ERROR_SCRIPT_LOCK_MISSING,
    }
    assert fake_voice_call_count() == 0


def test_partial_voice_error_does_not_complete_run(tmp_path: Path, temp_db_path: Path) -> None:
    project = _script_coverage_project(tmp_path, temp_db_path)
    result = start_voice_generation_run(project, sync=True)
    assert not result.started
    assert result.error_code
