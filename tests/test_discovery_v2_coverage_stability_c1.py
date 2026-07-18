"""Coverage Stability C1 — reproduce equivalent-run audit/gap reset (current bug).

These tests document current product behavior and must stay green until C2+.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fixtures.coverage_stability_c1 import (
    FAKE_COVERAGE_AUDIT_ID_NAMESPACE,
    FAKE_COVERAGE_AUDIT_ID_PARTS,
    CallOrderRecorder,
    assert_schema_20,
    build_script_ready_project,
    current_audit_id,
    dump_normalized,
    expected_fake_coverage_audit_id,
    gaps_for_audit,
    install_active_coverage_worker_gate,
    install_no_media_io_guards,
    list_all_gaps,
    load_audit,
    normalize_fachliche_coverage_inputs,
    progress_three_gaps,
    run_automatic_coverage_revalidation,
    run_manual_coverage,
    snapshot_after_coverage_run,
)
from otio_app.discovery_v2.adapters.analysis_job_launcher import (
    reset_analysis_job_launcher_for_tests,
)
from otio_app.discovery_v2.adapters.editorial_job_launcher import (
    get_editorial_job_launcher,
    reset_editorial_job_launcher_for_tests,
)
from otio_app.discovery_v2.adapters.narration_job_launcher import (
    reset_narration_job_launcher_for_tests,
)
from otio_app.discovery_v2.adapters.supplementation_job_launcher import (
    reset_supplementation_job_launcher_for_tests,
)
from otio_app.discovery_v2.adapters.text_config import load_text_config
from otio_app.discovery_v2.adapters.text_fake import reset_fake_text_test_hook
from otio_app.discovery_v2.application.coverage_gap_service import (
    materialize_gaps_from_current_coverage,
)
from otio_app.discovery_v2.application.editorial_service import (
    get_editorial_view,
    start_coverage_run,
)
from otio_app.discovery_v2.application.script_lock_service import preview_script_lock
from otio_app.discovery_v2.domain.editorial import EDITORIAL_ERROR_RUN_ALREADY_ACTIVE
from otio_app.discovery_v2.domain.supplementation import (
    CoverageGapStatus,
    EscalationStep,
)
from otio_app.discovery_v2.persistence import supplementation_repository as supp_repo


@pytest.fixture(autouse=True)
def _reset_state() -> None:
    reset_analysis_job_launcher_for_tests()
    reset_editorial_job_launcher_for_tests()
    reset_supplementation_job_launcher_for_tests()
    reset_narration_job_launcher_for_tests()
    reset_fake_text_test_hook()
    yield
    reset_fake_text_test_hook()
    reset_narration_job_launcher_for_tests()
    reset_supplementation_job_launcher_for_tests()
    reset_editorial_job_launcher_for_tests()
    reset_analysis_job_launcher_for_tests()


def _prepare_progressed_coverage(tmp_path: Path, temp_db_path: Path):
    project = build_script_ready_project(tmp_path, temp_db_path)
    inputs_before = normalize_fachliche_coverage_inputs(project)
    started_a = run_manual_coverage(project, sync=True)
    snap_a = snapshot_after_coverage_run(project, run_id=started_a.run.run_id)
    progressed = progress_three_gaps(project)
    inputs_after = normalize_fachliche_coverage_inputs(project)
    assert dump_normalized(inputs_before) == dump_normalized(inputs_after)
    return project, snap_a, progressed, inputs_before


def test_equivalent_completed_runs_mint_different_audit_ids(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project, snap_a, _progressed, inputs_a = _prepare_progressed_coverage(
        tmp_path, temp_db_path
    )
    started_b = run_manual_coverage(project, sync=True)
    snap_b = snapshot_after_coverage_run(project, run_id=started_b.run.run_id)
    inputs_b = normalize_fachliche_coverage_inputs(project)

    assert dump_normalized(inputs_a) == dump_normalized(inputs_b)
    assert dump_normalized(snap_a.fachliche_audit) == dump_normalized(
        snap_b.fachliche_audit
    )
    assert snap_a.run_id != snap_b.run_id
    assert snap_a.coverage_audit_id != snap_b.coverage_audit_id
    assert current_audit_id(project) == snap_b.coverage_audit_id
    assert snap_a.audit.status.value == "completed"
    assert snap_b.audit.status.value == "completed"

    # Exact FakeText formula: uuid5(NAMESPACE_URL, "otio-discovery-v2-editorial:" +
    # "coverage:project:script:fingerprint:run_id")
    assert FAKE_COVERAGE_AUDIT_ID_PARTS[0] == "coverage"
    assert FAKE_COVERAGE_AUDIT_ID_NAMESPACE.endswith(":")
    recomputed_a = expected_fake_coverage_audit_id(
        project_id=project.id,
        script_id=snap_a.audit.script_id,
        observation_fingerprint=snap_a.audit.input_observation_fingerprint,
        run_id=snap_a.run_id,
    )
    recomputed_b = expected_fake_coverage_audit_id(
        project_id=project.id,
        script_id=snap_b.audit.script_id,
        observation_fingerprint=snap_b.audit.input_observation_fingerprint,
        run_id=snap_b.run_id,
    )
    assert recomputed_a == snap_a.coverage_audit_id
    assert recomputed_b == snap_b.coverage_audit_id
    assert recomputed_a != recomputed_b
    assert_schema_20(project)


def test_equivalent_second_audit_supersedes_existing_gaps(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, snap_a, progressed, _inputs = _prepare_progressed_coverage(
        tmp_path, temp_db_path
    )
    old_ids = {
        progressed.gap_user_decision_id,
        progressed.gap_photo_id,
        progressed.gap_candidate_id,
    }
    recorder = CallOrderRecorder()
    recorder.install(monkeypatch)

    started_b = run_manual_coverage(project, sync=True)
    snap_b = snapshot_after_coverage_run(project, run_id=started_b.run.run_id)
    assert snap_b.coverage_audit_id != snap_a.coverage_audit_id
    assert current_audit_id(project) == snap_b.coverage_audit_id

    # Current switches in the worker before gap materialization.
    assert "insert_coverage_audit" in recorder.events
    current_events = [
        item
        for item in recorder.events
        if item.startswith("upsert_project_state:active_coverage_audit_id=")
    ]
    assert any(snap_b.coverage_audit_id in item for item in current_events)
    idx_insert = recorder.events.index("insert_coverage_audit")
    idx_current = next(
        i
        for i, item in enumerate(recorder.events)
        if item
        == f"upsert_project_state:active_coverage_audit_id={snap_b.coverage_audit_id}"
    )
    assert idx_insert < idx_current
    assert not any(
        item.startswith("supersede_gaps_not_in_audit") for item in recorder.events
    ), "supersede must not run inside coverage worker"

    materialize = materialize_gaps_from_current_coverage(project)
    assert materialize.ok
    new_gaps = gaps_for_audit(project, snap_b.coverage_audit_id)
    old_gaps = gaps_for_audit(project, snap_a.coverage_audit_id)
    new_ids = {gap.gap_id for gap in new_gaps}
    assert old_ids.isdisjoint(new_ids)
    assert all(gap.status == CoverageGapStatus.SUPERSEDED for gap in old_gaps)
    # Product Fake path materializes new gaps as open (not in_progress).
    assert all(gap.status == CoverageGapStatus.OPEN for gap in new_gaps)
    assert {gap.visual_intent_id for gap in new_gaps} >= set(
        progressed.visual_intent_ids
    )

    supersede_idx = next(
        i
        for i, item in enumerate(recorder.events)
        if item.startswith("supersede_gaps_not_in_audit")
    )
    first_insert_gap = next(
        i for i, item in enumerate(recorder.events) if item.startswith("insert_coverage_gap")
    )
    assert idx_current < supersede_idx < first_insert_gap
    # Documented window: current points at B while old gaps remain until materialize.
    assert (
        recorder.events[supersede_idx]
        == f"supersede_gaps_not_in_audit:audit={snap_b.coverage_audit_id}"
    )


def test_equivalent_second_audit_resets_escalation_and_user_decision(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project, snap_a, progressed, _inputs = _prepare_progressed_coverage(
        tmp_path, temp_db_path
    )
    conn = supp_repo.open_supplementation_registry(project.project_root_path)
    try:
        old_events = supp_repo.list_gap_events(
            conn, gap_id=progressed.gap_user_decision_id
        )
        old_event_ids = {event.event_id for event in old_events}
        assert progressed.gap1_event_ids
        assert old_event_ids >= set(progressed.gap1_event_ids)
    finally:
        conn.close()

    started_b = run_manual_coverage(project, sync=True)
    snap_b = snapshot_after_coverage_run(project, run_id=started_b.run.run_id)
    materialize_gaps_from_current_coverage(project)
    new_gaps = {
        gap.visual_intent_id: gap for gap in gaps_for_audit(project, snap_b.coverage_audit_id)
    }
    old_by_intent = {
        gap.visual_intent_id: gap for gap in gaps_for_audit(project, snap_a.coverage_audit_id)
    }

    for intent_id in progressed.visual_intent_ids:
        old = old_by_intent[intent_id]
        new = new_gaps[intent_id]
        assert old.gap_id != new.gap_id
        assert old.status == CoverageGapStatus.SUPERSEDED
        assert new.status == CoverageGapStatus.OPEN
        assert new.current_escalation_step == EscalationStep.LOCAL_DEEPER_REVIEW
        assert new.user_decision is None
        assert new.prior_attempt_summaries == []

    photo_old = old_by_intent[
        next(
            gap.visual_intent_id
            for gap in progressed.gaps
            if gap.gap_id == progressed.gap_photo_id
        )
    ]
    photo_new = new_gaps[photo_old.visual_intent_id]
    assert photo_old.current_escalation_step == EscalationStep.PHOTO
    assert photo_new.current_escalation_step == EscalationStep.LOCAL_DEEPER_REVIEW

    conn = supp_repo.open_supplementation_registry(project.project_root_path)
    try:
        still_there = supp_repo.list_gap_events(
            conn, gap_id=progressed.gap_user_decision_id
        )
        # Historical events remain; supersede appends an additional event.
        assert old_event_ids <= {event.event_id for event in still_there}
        assert any(event.event_type.value == "superseded" for event in still_there)
        new_primary = new_gaps[
            next(
                gap.visual_intent_id
                for gap in progressed.gaps
                if gap.gap_id == progressed.gap_user_decision_id
            )
        ]
        new_events = supp_repo.list_gap_events(conn, gap_id=new_primary.gap_id)
        assert new_events
        assert all(event.event_type.value != "escalated" for event in new_events)
        decisions = supp_repo.list_candidate_decisions(
            conn, gap_id=progressed.gap_candidate_id
        )
        assert decisions
        new_candidate_gap = new_gaps[
            next(
                gap.visual_intent_id
                for gap in progressed.gaps
                if gap.gap_id == progressed.gap_candidate_id
            )
        ]
        new_decisions = supp_repo.list_candidate_decisions(
            conn, gap_id=new_candidate_gap.gap_id
        )
        assert new_decisions == []
    finally:
        conn.close()

    preview = preview_script_lock(project)
    assert not preview.ok or not preview.can_lock
    assert preview.blockers or preview.confirmation_blockers or not preview.ok


def test_equivalent_second_audit_does_not_reuse_accepted_unresolved(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project, snap_a, progressed, _inputs = _prepare_progressed_coverage(
        tmp_path, temp_db_path
    )
    old = next(
        gap for gap in progressed.gaps if gap.gap_id == progressed.gap_user_decision_id
    )
    assert old.status == CoverageGapStatus.ACCEPTED_UNRESOLVED
    assert old.user_decision == "accepted_unresolved"
    assert old.accepted_unresolved_risks

    started_b = run_manual_coverage(project, sync=True)
    snap_b = snapshot_after_coverage_run(project, run_id=started_b.run.run_id)
    materialize_gaps_from_current_coverage(project)
    new = next(
        gap
        for gap in gaps_for_audit(project, snap_b.coverage_audit_id)
        if gap.visual_intent_id == old.visual_intent_id
    )
    assert new.gap_id != old.gap_id
    assert new.status == CoverageGapStatus.OPEN
    assert new.user_decision is None
    assert new.accepted_unresolved_risks == []
    assert new.coverage_audit_id == snap_b.coverage_audit_id
    assert load_audit(project, snap_a.coverage_audit_id).coverage_audit_id == snap_a.coverage_audit_id

    preview = preview_script_lock(project)
    assert not getattr(preview, "can_lock", False)


def test_completed_manual_and_automatic_triggers_have_no_shared_input_reuse(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Variant A: manual → completed → manual again → new audit (no completed reuse).
    project = build_script_ready_project(tmp_path, temp_db_path)
    inputs = normalize_fachliche_coverage_inputs(project)
    started_a = run_manual_coverage(project, sync=True)
    snap_a = snapshot_after_coverage_run(project, run_id=started_a.run.run_id)
    progress_three_gaps(project)
    started_b = run_manual_coverage(project, sync=True)
    snap_b = snapshot_after_coverage_run(project, run_id=started_b.run.run_id)
    assert dump_normalized(inputs) == dump_normalized(
        normalize_fachliche_coverage_inputs(project)
    )
    assert snap_a.coverage_audit_id != snap_b.coverage_audit_id
    assert dump_normalized(snap_a.fachliche_audit) == dump_normalized(
        snap_b.fachliche_audit
    )

    # Variant B: automatic revalidation → completed → manual → another new audit.
    project2 = build_script_ready_project(tmp_path / "auto", temp_db_path)
    auto = run_automatic_coverage_revalidation(project2, sync=True)
    snap_auto = snapshot_after_coverage_run(project2, run_id=auto.run_id)
    progress_three_gaps(project2)
    started_manual = run_manual_coverage(project2, sync=True)
    snap_manual = snapshot_after_coverage_run(
        project2, run_id=started_manual.run.run_id
    )
    assert snap_auto.coverage_audit_id != snap_manual.coverage_audit_id
    assert dump_normalized(snap_auto.fachliche_audit) == dump_normalized(
        snap_manual.fachliche_audit
    )
    # Both paths enter start_coverage_run / editorial_coverage worker.
    assert started_a.run.scope == "editorial_coverage_only"
    assert started_b.run.scope == "editorial_coverage_only"
    assert started_manual.run.scope == "editorial_coverage_only"

    # Active identical run is blocked (no second concurrent coverage start).
    project3 = build_script_ready_project(tmp_path / "active", temp_db_path)
    release = threading.Event()
    entered = threading.Event()
    install_active_coverage_worker_gate(
        monkeypatch, release=release, entered=entered
    )
    first = start_coverage_run(project3, sync=False)
    assert first.started and first.run is not None
    assert entered.wait(timeout=5)
    second = start_coverage_run(project3, sync=False)
    assert second.started is False
    assert second.error_code == EDITORIAL_ERROR_RUN_ALREADY_ACTIVE
    release.set()
    deadline = time.time() + 10
    while time.time() < deadline:
        if not get_editorial_job_launcher().is_active(project3.id):
            try:
                if current_audit_id(project3):
                    break
            except AssertionError:
                pass
        time.sleep(0.05)
    assert current_audit_id(project3)
    assert get_editorial_view(project3).ok


def test_reproduction_uses_no_real_gateway_and_no_media_io(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_text_config()
    assert config.provider == "fake"
    project = build_script_ready_project(tmp_path, temp_db_path)
    # Guards after intake/setup: coverage re-runs must stay Fake-only / no media I/O.
    install_no_media_io_guards(monkeypatch)
    started = run_manual_coverage(project, sync=True)
    snap = snapshot_after_coverage_run(project, run_id=started.run.run_id)
    assert snap.audit.provider == "fake"
    progress_three_gaps(project)
    started_b = run_manual_coverage(project, sync=True)
    snap_b = snapshot_after_coverage_run(project, run_id=started_b.run.run_id)
    assert snap_b.audit.provider == "fake"
    assert load_text_config().provider == "fake"
    assert_schema_20(project)
    # No product test hooks / force_* switches required for reproduction.
    assert not hasattr(start_coverage_run, "force_new_audit")
