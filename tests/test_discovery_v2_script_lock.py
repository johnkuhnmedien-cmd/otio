"""Phase 10 script lock fake E2E smokes."""

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
from otio_app.discovery_v2.adapters.voice_fake import fake_voice_call_count, reset_fake_voice_call_count
from otio_app.discovery_v2.application.coverage_gap_service import (
    accept_gap_unresolved,
    mark_gap_resolved_with_local_asset,
    materialize_gaps_from_current_coverage,
)
from otio_app.discovery_v2.application.editorial_service import (
    get_editorial_view,
    save_user_script_edit,
    start_coverage_run,
    start_script_run,
    start_structure_run,
)
from otio_app.discovery_v2.application.script_lock_service import (
    create_script_lock,
    get_effective_script_lock,
    preview_script_lock,
)
from otio_app.discovery_v2.application.narration_timing_service import start_narration_timing_run
from otio_app.discovery_v2.application.pause_direction_service import start_pause_direction_run
from otio_app.discovery_v2.application.supplementation_service import (
    link_imported_completed_asset_to_gap,
    record_candidate_decision,
    record_claim_decision,
    start_search_run,
)
from otio_app.discovery_v2.application.voice_generation_service import start_voice_generation_run
from otio_app.discovery_v2.domain.supplementation import (
    CoverageGapStatus,
    CoverageRiskFlag,
    ScriptLockStatus,
)
from otio_app.discovery_v2.persistence import editorial_repository as editorial_repo
from otio_app.discovery_v2.persistence import supplementation_repository as repo
from otio_app.discovery_v2.persistence import asset_analysis_repository as analysis_repo
from otio_app.discovery_v2.application.observation_review_service import submit_observation_review

from test_discovery_v2_editorial_script import _accepted_editorial_project, _brief_to_narrative
from test_discovery_v2_analysis_prepare import _now


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


def _script_coverage_project(tmp_path: Path, temp_db_path: Path):
    project = _accepted_editorial_project(tmp_path, temp_db_path)
    _brief_to_narrative(project)
    assert start_script_run(project, sync=True).started
    assert start_coverage_run(project, sync=True).started
    return project


def _decide_all_claims(project) -> None:
    view = get_editorial_view(project)
    assert view.script is not None and view.script_bundle is not None
    for claim in view.script_bundle["claims"]:
        record_claim_decision(
            project,
            script_id=view.script.script_id,
            claim_id=claim["claim_id"],
            claim_text=claim["statement"],
            decision="confirmed",
        )


def _resolve_all_gaps_locally(project) -> list:
    gaps = materialize_gaps_from_current_coverage(project).gaps
    for gap in gaps:
        mark_gap_resolved_with_local_asset(
            project,
            gap_id=gap.gap_id,
            asset_id="asset-local",
        )
    return materialize_gaps_from_current_coverage(project).gaps


def test_smoke_a_local_gap_resolution_allows_explicit_script_lock(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _script_coverage_project(tmp_path, temp_db_path)
    _resolve_all_gaps_locally(project)
    _decide_all_claims(project)
    preview = preview_script_lock(project)
    assert preview.ok, preview.blockers
    result = create_script_lock(
        project,
        user_confirmed=True,
        confirmed_fingerprint=preview.lock_fingerprint,
    )
    assert result.ok and result.lock is not None
    assert result.lock.status == ScriptLockStatus.LOCKED


def test_smoke_b_accepted_candidate_metadata_does_not_unlock_without_original(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _script_coverage_project(tmp_path, temp_db_path)
    gaps = materialize_gaps_from_current_coverage(project).gaps
    assert start_search_run(project, gap_ids=[gaps[0].gap_id], sync=True).started
    conn = repo.open_supplementation_registry(project.project_root_path)
    try:
        candidate = repo.list_stock_candidates_for_gap(conn, gap_id=gaps[0].gap_id)[0]
    finally:
        conn.close()
    accepted = record_candidate_decision(
        project,
        candidate_id=candidate.candidate_id,
        decision="accepted_for_import",
        reason="Metadaten passen",
    )
    assert accepted.ok
    for gap in gaps[1:]:
        mark_gap_resolved_with_local_asset(project, gap_id=gap.gap_id, asset_id="asset-local")
    _decide_all_claims(project)
    preview = preview_script_lock(project)
    assert not preview.ok
    assert any(gaps[0].gap_id in blocker for blocker in preview.blockers)


def test_smoke_c_manual_original_import_link_resolves_gap_then_lock(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _script_coverage_project(tmp_path, temp_db_path)
    gaps = materialize_gaps_from_current_coverage(project).gaps
    assert start_search_run(project, gap_ids=[gaps[0].gap_id], sync=True).started
    conn = repo.open_supplementation_registry(project.project_root_path)
    try:
        candidate = repo.list_stock_candidates_for_gap(conn, gap_id=gaps[0].gap_id)[0]
        conn.execute(
            """
            INSERT INTO assets (
                asset_id, project_id, source_relative_path, source_group, file_name,
                extension, media_kind, size_bytes, mtime_ns, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "asset-manual-original",
                project.id,
                "Media/manual-original.jpg",
                "Media",
                "manual-original.jpg",
                ".jpg",
                "image",
                1,
                1,
                _now().isoformat(),
                _now().isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    assert record_candidate_decision(
        project,
        candidate_id=candidate.candidate_id,
        decision="accepted_for_import",
        reason="Original wird manuell importiert",
    ).ok
    linked = link_imported_completed_asset_to_gap(
        project,
        gap_id=gaps[0].gap_id,
        candidate_id=candidate.candidate_id,
        asset_id="asset-manual-original",
    )
    assert linked.ok and linked.gap is not None
    assert linked.gap.status == CoverageGapStatus.RESOLVED_WITH_SUPPLEMENT
    for gap in gaps[1:]:
        mark_gap_resolved_with_local_asset(project, gap_id=gap.gap_id, asset_id="asset-local")
    _decide_all_claims(project)
    preview = preview_script_lock(project)
    assert preview.ok, preview.blockers


def test_smoke_e_accepted_unresolved_requires_per_risk_confirmation_for_lock(
    tmp_path: Path, temp_db_path: Path
) -> None:
    from otio_app.discovery_v2.application.coverage_gap_service import escalate_gap
    from otio_app.discovery_v2.domain.supplementation import EscalationStep

    project = _script_coverage_project(tmp_path, temp_db_path)
    gaps = materialize_gaps_from_current_coverage(project).gaps
    gap = gaps[0]
    conn = repo.open_supplementation_registry(project.project_root_path)
    try:
        risk_gap = gap.model_copy(
            update={
                "risk_flags": [CoverageRiskFlag.TOO_GENERIC],
                "current_escalation_step": EscalationStep.USER_DECISION,
                "status": CoverageGapStatus.IN_PROGRESS,
            }
        )
        repo.update_coverage_gap(conn, risk_gap)
        conn.commit()
    finally:
        conn.close()
    assert accept_gap_unresolved(
        project,
        gap_id=gap.gap_id,
        confirmed_risks=["too_generic"],
    ).ok
    for other in gaps[1:]:
        mark_gap_resolved_with_local_asset(project, gap_id=other.gap_id, asset_id="asset-local")
    _decide_all_claims(project)
    preview = preview_script_lock(project)
    # Fingerprint is visible without UI checkboxes; lock remains gated.
    assert preview.ok
    assert preview.lock_fingerprint
    assert not preview.can_lock
    assert preview.confirmation_blockers
    key = f"{gap.gap_id}:too_generic"
    assert any(key in item for item in preview.confirmation_blockers)
    ok = create_script_lock(
        project,
        user_confirmed=True,
        confirmed_fingerprint=create_script_lock.__name__,
        accepted_unresolved_risk_confirmations={key: True},
    )
    assert ok.error_code == "script_lock_fingerprint_mismatch"
    result_preview = create_script_lock(
        project,
        user_confirmed=False,
        confirmed_fingerprint=preview.lock_fingerprint,
        accepted_unresolved_risk_confirmations={key: True},
    )
    assert result_preview.error_code == "script_lock_confirmation_required"
    ready = create_script_lock(
        project,
        user_confirmed=True,
        confirmed_fingerprint=result_preview.preview.lock_fingerprint if result_preview.preview else None,
        accepted_unresolved_risk_confirmations={key: True},
    )
    assert ready.ok
    assert key in (ready.lock.accepted_open_risks if ready.lock else [])


def test_smoke_d_sentence_revision_creates_new_version_and_no_auto_lock(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _script_coverage_project(tmp_path, temp_db_path)
    view = get_editorial_view(project)
    assert view.script is not None
    edited = save_user_script_edit(project, full_text=view.script.full_text + " Gezielt umformuliert.")
    assert edited.ok and edited.script is not None
    assert edited.script.script_version == view.script.script_version + 1
    blocked = preview_script_lock(project)
    assert "script_structure_pending" in blocked.blockers
    assert start_structure_run(project, sync=True).started
    assert start_coverage_run(project, sync=True).started
    assert get_effective_script_lock(project).ok is False


def test_smoke_f_stale_observation_is_excluded_from_lock(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _script_coverage_project(tmp_path, temp_db_path)
    _resolve_all_gaps_locally(project)
    _decide_all_claims(project)
    conn = analysis_repo.open_analysis_registry(project.project_root_path)
    try:
        obs = analysis_repo.list_visual_observations_for_project(conn, project_id=project.id)[0]
    finally:
        conn.close()
    assert submit_observation_review(
        project,
        observation_id=obs.observation_id,
        decision="rejected",
        reason_code="stale_for_lock",
    ).ok
    preview = preview_script_lock(project)
    assert "coverage_audit_stale" in preview.blockers or "observation_fingerprint_stale" in preview.blockers


def test_smoke_g_new_script_version_invalidates_existing_lock_without_voice(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _script_coverage_project(tmp_path, temp_db_path)
    _resolve_all_gaps_locally(project)
    _decide_all_claims(project)
    preview = preview_script_lock(project)
    locked = create_script_lock(
        project,
        user_confirmed=True,
        confirmed_fingerprint=preview.lock_fingerprint,
    )
    assert locked.ok and locked.lock is not None
    view = get_editorial_view(project)
    assert view.script is not None
    edited = save_user_script_edit(project, full_text=view.script.full_text + " Neuer Satz.")
    assert edited.ok
    effective = get_effective_script_lock(project)
    assert effective.error_code == "script_lock_invalidated"
    assert not hasattr(locked.lock, "voice_id")
    assert start_structure_run(project, sync=True).started


def test_historical_script_lock_never_becomes_effective_for_narration(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _script_coverage_project(tmp_path, temp_db_path)
    _resolve_all_gaps_locally(project)
    _decide_all_claims(project)
    preview = preview_script_lock(project)
    current = create_script_lock(
        project,
        user_confirmed=True,
        confirmed_fingerprint=preview.lock_fingerprint,
    )
    assert current.ok and current.lock is not None

    historical = current.lock.model_copy(
        update={
            "lock_id": "historical-lock",
            "lock_version": current.lock.lock_version + 10,
            "status": ScriptLockStatus.SUPERSEDED,
            "created_at": current.lock.created_at.replace(year=current.lock.created_at.year - 1),
        }
    )
    conn = repo.open_supplementation_registry(project.project_root_path)
    try:
        repo.insert_script_lock(
            conn,
            historical,
            "editorial/script_locks/historical-lock.json",
        )
        conn.commit()
    finally:
        conn.close()

    effective = get_effective_script_lock(project)
    assert effective.ok and effective.lock is not None
    assert effective.lock.lock_id == current.lock.lock_id
    voice = start_voice_generation_run(project, sync=True)
    assert voice.started and voice.run is not None
    assert voice.run.script_lock_id == current.lock.lock_id

    conn = repo.open_supplementation_registry(project.project_root_path)
    try:
        state = editorial_repo.get_project_state(conn, project_id=project.id)
        assert state is not None
        editorial_repo.upsert_project_state(
            conn,
            state.model_copy(
                update={
                    "current_script_lock_id": historical.lock_id,
                    "updated_at": current.lock.created_at,
                }
            ),
        )
        conn.commit()
    finally:
        conn.close()

    calls_before_blocked = fake_voice_call_count()
    blocked_voice = start_voice_generation_run(project, sync=True)
    assert not blocked_voice.started
    assert blocked_voice.error_code == "script_lock_invalidated"
    assert fake_voice_call_count() == calls_before_blocked

    conn = repo.open_supplementation_registry(project.project_root_path)
    try:
        repo.update_script_lock_status(
            conn,
            lock_id=historical.lock_id,
            status=ScriptLockStatus.INVALIDATED,
        )
        conn.commit()
    finally:
        conn.close()

    blocked_pause = start_pause_direction_run(project, sync=True)
    assert not blocked_pause.started
    assert blocked_pause.error_code == "script_lock_invalidated"
    blocked_timing = start_narration_timing_run(project, sync=True)
    assert not blocked_timing.started
    assert blocked_timing.error_code == "script_lock_invalidated"
