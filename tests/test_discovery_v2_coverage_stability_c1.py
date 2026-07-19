"""Coverage Stability C1 — updated after C2: equivalent inputs reuse the audit.

Node IDs retained; assertions document the fixed idempotent product contract.
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
    assert_schema_20,
    build_script_ready_project,
    current_audit_id,
    dump_normalized,
    expected_fake_coverage_audit_id,
    gaps_for_audit,
    install_active_coverage_worker_gate,
    install_no_media_io_guards,
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
from otio_app.discovery_v2.domain.supplementation import CoverageGapStatus


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
    """C2: equivalent completed inputs reuse the same current audit ID."""

    project, snap_a, _progressed, inputs_a = _prepare_progressed_coverage(
        tmp_path, temp_db_path
    )
    second = start_coverage_run(project, sync=True)
    assert second.reused is True
    assert second.coverage_audit_id == snap_a.coverage_audit_id
    assert second.run is None
    inputs_b = normalize_fachliche_coverage_inputs(project)
    assert dump_normalized(inputs_a) == dump_normalized(inputs_b)
    assert current_audit_id(project) == snap_a.coverage_audit_id
    # Technical Fake audit formula still includes run_id for *new* audits.
    assert FAKE_COVERAGE_AUDIT_ID_PARTS[0] == "coverage"
    assert FAKE_COVERAGE_AUDIT_ID_NAMESPACE.endswith(":")
    recomputed_a = expected_fake_coverage_audit_id(
        project_id=project.id,
        script_id=snap_a.audit.script_id,
        observation_fingerprint=snap_a.audit.input_observation_fingerprint,
        run_id=snap_a.run_id,
    )
    assert recomputed_a == snap_a.coverage_audit_id
    assert_schema_20(project)


def test_equivalent_second_audit_supersedes_existing_gaps(
    tmp_path: Path, temp_db_path: Path
) -> None:
    """C2: reuse must not supersede gaps for equivalent inputs."""

    project, snap_a, progressed, _inputs = _prepare_progressed_coverage(
        tmp_path, temp_db_path
    )
    old_ids = {
        progressed.gap_user_decision_id,
        progressed.gap_photo_id,
        progressed.gap_candidate_id,
    }
    second = start_coverage_run(project, sync=True)
    assert second.reused
    assert current_audit_id(project) == snap_a.coverage_audit_id
    gaps = gaps_for_audit(project, snap_a.coverage_audit_id)
    assert {gap.gap_id for gap in gaps} >= old_ids
    assert all(gap.status != CoverageGapStatus.SUPERSEDED for gap in gaps)


def test_equivalent_second_audit_resets_escalation_and_user_decision(
    tmp_path: Path, temp_db_path: Path
) -> None:
    """C2: reuse preserves escalation and user_decision on the same gap IDs."""

    project, snap_a, progressed, _inputs = _prepare_progressed_coverage(
        tmp_path, temp_db_path
    )
    second = start_coverage_run(project, sync=True)
    assert second.reused
    gaps = {gap.gap_id: gap for gap in gaps_for_audit(project, snap_a.coverage_audit_id)}
    kept = gaps[progressed.gap_user_decision_id]
    assert kept.status == CoverageGapStatus.ACCEPTED_UNRESOLVED
    assert kept.user_decision == "accepted_unresolved"
    photo = gaps[progressed.gap_photo_id]
    assert photo.current_escalation_step.value == "photo"


def test_equivalent_second_audit_does_not_reuse_accepted_unresolved(
    tmp_path: Path, temp_db_path: Path
) -> None:
    """C2: accepted_unresolved is preserved on the same gap (name historical)."""

    project, snap_a, progressed, _inputs = _prepare_progressed_coverage(
        tmp_path, temp_db_path
    )
    second = start_coverage_run(project, sync=True)
    assert second.reused
    gap = next(
        item
        for item in gaps_for_audit(project, snap_a.coverage_audit_id)
        if item.gap_id == progressed.gap_user_decision_id
    )
    assert gap.status == CoverageGapStatus.ACCEPTED_UNRESOLVED
    assert gap.accepted_unresolved_risks


def test_completed_manual_and_automatic_triggers_have_no_shared_input_reuse(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C2: manual and automatic triggers share completed-audit reuse."""

    project = build_script_ready_project(tmp_path, temp_db_path)
    started_a = run_manual_coverage(project, sync=True)
    snap_a = snapshot_after_coverage_run(project, run_id=started_a.run.run_id)
    progress_three_gaps(project)
    manual = start_coverage_run(project, sync=True)
    assert manual.reused and manual.coverage_audit_id == snap_a.coverage_audit_id

    project2 = build_script_ready_project(tmp_path / "auto", temp_db_path)
    auto = run_automatic_coverage_revalidation(project2, sync=True)
    assert auto.coverage_started
    audit_auto = current_audit_id(project2)
    progress_three_gaps(project2)
    auto2 = run_automatic_coverage_revalidation(project2, sync=True)
    assert auto2.ok and auto2.coverage_started is False
    manual2 = start_coverage_run(project2, sync=True)
    assert manual2.reused and manual2.coverage_audit_id == audit_auto

    project3 = build_script_ready_project(tmp_path / "active", temp_db_path)
    release = threading.Event()
    entered = threading.Event()
    install_active_coverage_worker_gate(
        monkeypatch, release=release, entered=entered
    )
    first = start_coverage_run(project3, sync=False)
    assert first.started and first.run is not None and not first.reused
    assert entered.wait(timeout=5)
    second = start_coverage_run(project3, sync=False)
    assert second.reused and second.run is not None
    assert second.run.run_id == first.run.run_id
    release.set()
    deadline = time.time() + 10
    while time.time() < deadline and get_editorial_job_launcher().is_active(project3.id):
        time.sleep(0.05)
    assert current_audit_id(project3)


def test_reproduction_uses_no_real_gateway_and_no_media_io(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_text_config()
    assert config.provider == "fake"
    project = build_script_ready_project(tmp_path, temp_db_path)
    install_no_media_io_guards(monkeypatch)
    started = run_manual_coverage(project, sync=True)
    snap = snapshot_after_coverage_run(project, run_id=started.run.run_id)
    assert snap.audit.provider == "fake"
    progress_three_gaps(project)
    reused = start_coverage_run(project, sync=True)
    assert reused.reused
    assert load_text_config().provider == "fake"
    assert_schema_20(project)
