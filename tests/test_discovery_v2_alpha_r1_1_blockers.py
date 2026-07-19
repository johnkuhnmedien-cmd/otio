"""R1.1 Coverage-/Script-Lock-Blocker and coverage persist atomicy tests."""

from __future__ import annotations

from pathlib import Path
import sqlite3
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from otio_app.discovery_v2.adapters.analysis_job_launcher import reset_analysis_job_launcher_for_tests
from otio_app.discovery_v2.adapters.editorial_job_launcher import reset_editorial_job_launcher_for_tests
from otio_app.discovery_v2.adapters.narration_job_launcher import reset_narration_job_launcher_for_tests
from otio_app.discovery_v2.adapters.supplementation_job_launcher import (
    reset_supplementation_job_launcher_for_tests,
)
from otio_app.discovery_v2.adapters.text_fake import reset_fake_text_test_hook
from otio_app.discovery_v2.application.coverage_gap_service import (
    accept_gap_unresolved,
    escalate_gap,
    evaluate_gap_accept_unresolved_eligibility,
    materialize_gaps_from_current_coverage,
)
from otio_app.discovery_v2.application.editorial_service import (
    get_editorial_view,
    start_coverage_run,
    start_script_run,
)
from otio_app.discovery_v2.application.script_lock_service import (
    create_script_lock,
    preview_script_lock,
)
from otio_app.discovery_v2.application.supplementation_service import (
    record_candidate_decision,
    record_claim_decision,
    start_search_run,
)
from otio_app.discovery_v2.domain.editorial import EDITORIAL_ERROR_COVERAGE_AUDIT_PERSIST_FAILED
from otio_app.discovery_v2.domain.supplementation import (
    ACCEPTABLE_MISSING_PROPERTY_RISK_MAP,
    CoverageGapStatus,
    CoverageLevel,
    CoverageRiskFlag,
    EscalationStep,
    derive_acceptable_risks_from_missing_properties,
    merge_gap_risk_flags,
)
from otio_app.discovery_v2.persistence import editorial_repository as editorial_repo
from otio_app.discovery_v2.persistence import supplementation_repository as repo
from otio_app.discovery_v2.ui import editorial_page

from test_discovery_v2_editorial_script import _accepted_editorial_project, _brief_to_narrative


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


def _coverage_project(tmp_path: Path, temp_db_path: Path):
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


def _escalate_to_user_decision(project, gap_id: str):
    for _ in range(len(EscalationStep)):
        result = escalate_gap(project, gap_id=gap_id)
        assert result.ok and result.gap is not None
        if result.gap.current_escalation_step == EscalationStep.USER_DECISION:
            return result.gap
    raise AssertionError("failed to reach user_decision")


def _reject_all_candidates(project, gap_id: str) -> list[str]:
    conn = repo.open_supplementation_registry(project.project_root_path)
    try:
        candidates = repo.list_stock_candidates_for_gap(conn, gap_id=gap_id)
    finally:
        conn.close()
    ids = []
    for candidate in candidates:
        result = record_candidate_decision(
            project,
            candidate_id=candidate.candidate_id,
            decision="rejected",
            reason="R1.1 fixture",
        )
        assert result.ok
        ids.append(candidate.candidate_id)
    return ids


def _reproduced_blocker_fixture(tmp_path: Path, temp_db_path: Path):
    """Exact reproduced Alpha blocker state for one gap + siblings resolved."""
    project = _coverage_project(tmp_path, temp_db_path)
    gaps = materialize_gaps_from_current_coverage(project).gaps
    assert gaps
    primary = gaps[0]
    assert primary.coverage_level == CoverageLevel.PARTIALLY_COVERED
    assert "exact_match_not_verified" in primary.missing_properties
    # Derived risk must be visible after materialize.
    assert CoverageRiskFlag.COVERAGE_EXACT_MATCH_NOT_VERIFIED in primary.risk_flags
    primary = _escalate_to_user_decision(project, primary.gap_id)
    assert primary.status == CoverageGapStatus.IN_PROGRESS
    assert primary.current_escalation_step == EscalationStep.USER_DECISION
    assert start_search_run(project, gap_ids=[primary.gap_id], sync=True).started
    rejected = _reject_all_candidates(project, primary.gap_id)
    assert len(rejected) >= 3
    for other in gaps[1:]:
        from otio_app.discovery_v2.application.coverage_gap_service import (
            mark_gap_resolved_with_local_asset,
        )

        assert mark_gap_resolved_with_local_asset(
            project, gap_id=other.gap_id, asset_id="asset-local"
        ).ok
    _decide_all_claims(project)
    return project, primary.gap_id


def test_r1_exact_match_maps_to_visible_acceptable_risk() -> None:
    derived = derive_acceptable_risks_from_missing_properties(["exact_match_not_verified"])
    assert derived == [CoverageRiskFlag.COVERAGE_EXACT_MATCH_NOT_VERIFIED]
    assert (
        ACCEPTABLE_MISSING_PROPERTY_RISK_MAP["exact_match_not_verified"]
        == CoverageRiskFlag.COVERAGE_EXACT_MATCH_NOT_VERIFIED
    )


def test_r1_unknown_missing_property_is_not_auto_acceptable() -> None:
    assert derive_acceptable_risks_from_missing_properties(["totally_unknown_property"]) == []
    merged = merge_gap_risk_flags([], ["totally_unknown_property", "exact_match_not_verified"])
    assert merged == [CoverageRiskFlag.COVERAGE_EXACT_MATCH_NOT_VERIFIED]


def test_r1_user_decision_exact_match_allows_accept_unresolved(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project, gap_id = _reproduced_blocker_fixture(tmp_path, temp_db_path)
    eligibility = evaluate_gap_accept_unresolved_eligibility(project, gap_id=gap_id)
    assert eligibility.ok, eligibility.blockers
    assert CoverageRiskFlag.COVERAGE_EXACT_MATCH_NOT_VERIFIED in eligibility.visible_risks


def test_r1_candidate_needs_review_blocks_accept(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project, gap_id = _reproduced_blocker_fixture(tmp_path, temp_db_path)
    conn = repo.open_supplementation_registry(project.project_root_path)
    try:
        candidate = repo.list_stock_candidates_for_gap(conn, gap_id=gap_id)[0]
    finally:
        conn.close()
    assert record_candidate_decision(
        project,
        candidate_id=candidate.candidate_id,
        decision="needs_review",
        reason="open",
    ).ok
    eligibility = evaluate_gap_accept_unresolved_eligibility(project, gap_id=gap_id)
    assert not eligibility.ok
    assert any(item.startswith("candidate_needs_review:") for item in eligibility.blockers)


def test_r1_candidate_accepted_for_import_blocks_accept(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project, gap_id = _reproduced_blocker_fixture(tmp_path, temp_db_path)
    conn = repo.open_supplementation_registry(project.project_root_path)
    try:
        candidate = repo.list_stock_candidates_for_gap(conn, gap_id=gap_id)[0]
    finally:
        conn.close()
    assert record_candidate_decision(
        project,
        candidate_id=candidate.candidate_id,
        decision="accepted_for_import",
        reason="want",
    ).ok
    eligibility = evaluate_gap_accept_unresolved_eligibility(project, gap_id=gap_id)
    assert not eligibility.ok
    assert any(
        item.startswith("candidate_accepted_for_import:") for item in eligibility.blockers
    )


def test_r1_historical_candidate_decisions_use_current_only(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project, gap_id = _reproduced_blocker_fixture(tmp_path, temp_db_path)
    conn = repo.open_supplementation_registry(project.project_root_path)
    try:
        candidate = repo.list_stock_candidates_for_gap(conn, gap_id=gap_id)[0]
    finally:
        conn.close()
    assert record_candidate_decision(
        project,
        candidate_id=candidate.candidate_id,
        decision="accepted_for_import",
        reason="old",
    ).ok
    assert record_candidate_decision(
        project,
        candidate_id=candidate.candidate_id,
        decision="rejected",
        reason="current",
    ).ok
    eligibility = evaluate_gap_accept_unresolved_eligibility(project, gap_id=gap_id)
    assert eligibility.ok, eligibility.blockers


def test_r1_accept_unresolved_makes_gap_terminal_and_exposes_fingerprint(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project, gap_id = _reproduced_blocker_fixture(tmp_path, temp_db_path)
    accepted = accept_gap_unresolved(
        project,
        gap_id=gap_id,
        confirmed_risks=["coverage_exact_match_not_verified"],
        user_confirmed=True,
    )
    assert accepted.ok and accepted.gap is not None
    assert accepted.gap.status == CoverageGapStatus.ACCEPTED_UNRESOLVED
    assert [
        risk.value for risk in accepted.gap.accepted_unresolved_risks
    ] == ["coverage_exact_match_not_verified"]
    conn = repo.open_supplementation_registry(project.project_root_path)
    try:
        events = repo.list_gap_events(conn, gap_id=gap_id)
    finally:
        conn.close()
    assert any(event.event_type.value == "user_decision_recorded" for event in events)
    key = f"{gap_id}:coverage_exact_match_not_verified"
    preview = preview_script_lock(project)
    # Fingerprint visible without UI checkboxes; lock gated by confirmation.
    assert preview.ok
    assert preview.lock_fingerprint
    assert not preview.can_lock
    assert any(key in item for item in preview.confirmation_blockers)
    gated = create_script_lock(
        project,
        user_confirmed=False,
        confirmed_fingerprint=preview.lock_fingerprint,
        accepted_unresolved_risk_confirmations={key: True},
    )
    assert gated.preview is not None
    assert gated.preview.lock_fingerprint
    assert gated.preview.fingerprint_display
    assert gated.preview.can_lock
    locked = create_script_lock(
        project,
        user_confirmed=True,
        confirmed_fingerprint=gated.preview.lock_fingerprint,
        accepted_unresolved_risk_confirmations={key: True},
    )
    assert locked.ok, (locked.message, locked.error_code, locked.preview.blockers if locked.preview else None)


def test_r1_accept_double_click_is_idempotent(tmp_path: Path, temp_db_path: Path) -> None:
    project, gap_id = _reproduced_blocker_fixture(tmp_path, temp_db_path)
    first = accept_gap_unresolved(
        project,
        gap_id=gap_id,
        confirmed_risks=["coverage_exact_match_not_verified"],
    )
    second = accept_gap_unresolved(
        project,
        gap_id=gap_id,
        confirmed_risks=["coverage_exact_match_not_verified"],
    )
    assert first.ok and second.ok
    conn = repo.open_supplementation_registry(project.project_root_path)
    try:
        events = [
            event
            for event in repo.list_gap_events(conn, gap_id=gap_id)
            if event.event_type.value == "user_decision_recorded"
        ]
    finally:
        conn.close()
    assert len(events) == 1


def test_smoke_a_reproduced_user_blocker_to_script_lock(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project, gap_id = _reproduced_blocker_fixture(tmp_path, temp_db_path)
    eligibility = evaluate_gap_accept_unresolved_eligibility(project, gap_id=gap_id)
    assert eligibility.ok
    assert accept_gap_unresolved(
        project,
        gap_id=gap_id,
        confirmed_risks=["coverage_exact_match_not_verified"],
    ).ok
    key = f"{gap_id}:coverage_exact_match_not_verified"
    gate = create_script_lock(
        project,
        user_confirmed=False,
        confirmed_fingerprint="pending",
        accepted_unresolved_risk_confirmations={key: True},
    )
    assert gate.preview is not None
    assert gate.preview.lock_fingerprint
    assert gate.preview.fulfilled_requirements
    locked = create_script_lock(
        project,
        user_confirmed=True,
        confirmed_fingerprint=gate.preview.lock_fingerprint,
        accepted_unresolved_risk_confirmations={key: True},
    )
    assert locked.ok and locked.lock is not None


def test_smoke_b_open_candidate_blocks_accept(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project, gap_id = _reproduced_blocker_fixture(tmp_path, temp_db_path)
    conn = repo.open_supplementation_registry(project.project_root_path)
    try:
        candidate = repo.list_stock_candidates_for_gap(conn, gap_id=gap_id)[0]
    finally:
        conn.close()
    assert record_candidate_decision(
        project,
        candidate_id=candidate.candidate_id,
        decision="accepted_for_import",
        reason="open path",
    ).ok
    eligibility = evaluate_gap_accept_unresolved_eligibility(project, gap_id=gap_id)
    assert not eligibility.ok
    assert any("accepted_for_import" in item for item in eligibility.blockers)


def test_smoke_c_atomic_coverage_retry_preserves_current_audit(
    tmp_path: Path, temp_db_path: Path, monkeypatch
) -> None:
    project = _coverage_project(tmp_path, temp_db_path)
    conn = editorial_repo.open_editorial_registry(project.project_root_path)
    try:
        state = editorial_repo.get_project_state(conn, project_id=project.id)
        assert state is not None and state.active_coverage_audit_id
        prior = state.active_coverage_audit_id
        audits_before = conn.execute("select count(*) from coverage_audits").fetchone()[0]
    finally:
        conn.close()

    # Force deterministic collision path used before the R1.1 FakeText fix:
    # inject an audit_id that already exists.
    from otio_app.discovery_v2.jobs import editorial_worker as worker

    original_insert = editorial_repo.insert_coverage_audit
    calls = {"n": 0}

    def flaky_insert(conn, audit, relative):
        calls["n"] += 1
        if calls["n"] == 1:
            raise sqlite3.IntegrityError(
                "UNIQUE constraint failed: coverage_audits.coverage_audit_id"
            )
        return original_insert(conn, audit, relative)

    monkeypatch.setattr(editorial_repo, "insert_coverage_audit", flaky_insert)
    # Also patch the worker's bound repo reference.
    monkeypatch.setattr(worker.repo, "insert_coverage_audit", flaky_insert)

    # force_recompute bypasses completed-audit reuse so the persist/retry path runs.
    failed = start_coverage_run(project, sync=True, execution_mode="force_recompute")
    assert failed.started
    assert failed.run is not None
    assert failed.run.status.value == "failed"
    assert failed.run.error_code == EDITORIAL_ERROR_COVERAGE_AUDIT_PERSIST_FAILED

    conn = editorial_repo.open_editorial_registry(project.project_root_path)
    try:
        state = editorial_repo.get_project_state(conn, project_id=project.id)
        assert state is not None
        assert state.active_coverage_audit_id == prior
        audits_mid = conn.execute("select count(*) from coverage_audits").fetchone()[0]
    finally:
        conn.close()
    assert audits_mid == audits_before

    # Retry succeeds with unique run-scoped audit id (FakeText includes run_id).
    monkeypatch.setattr(editorial_repo, "insert_coverage_audit", original_insert)
    monkeypatch.setattr(worker.repo, "insert_coverage_audit", original_insert)
    ok = start_coverage_run(project, sync=True, execution_mode="force_recompute")
    assert ok.started and ok.run is not None
    assert ok.run.status.value == "completed"
    conn = editorial_repo.open_editorial_registry(project.project_root_path)
    try:
        state = editorial_repo.get_project_state(conn, project_id=project.id)
        assert state is not None
        assert state.active_coverage_audit_id != prior
        audits_after = conn.execute("select count(*) from coverage_audits").fetchone()[0]
        gaps = conn.execute("select count(*) from coverage_gaps").fetchone()[0]
    finally:
        conn.close()
    assert audits_after == audits_before + 1
    # No half gaps from failed persist.
    assert gaps >= 0


def test_r1_repeated_coverage_run_no_longer_unique_collision(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _coverage_project(tmp_path, temp_db_path)
    # Normal second call reuses (C2). force_recompute still proves R1.1 UNIQUE safety.
    reused = start_coverage_run(project, sync=True)
    assert reused.reused and reused.coverage_audit_id
    second = start_coverage_run(project, sync=True, execution_mode="force_recompute")
    assert second.started and second.run is not None
    assert second.run.status.value == "completed"
    assert second.run.error_code is None


def test_r1_historical_failed_coverage_run_does_not_block_valid_audit(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project, gap_id = _reproduced_blocker_fixture(tmp_path, temp_db_path)
    # Simulate historical failed run while current audit remains valid.
    conn = editorial_repo.open_editorial_registry(project.project_root_path)
    try:
        from datetime import datetime, timezone
        from otio_app.discovery_v2.domain.editorial import (
            EDITORIAL_RUN_SCOPE_COVERAGE,
            EditorialRun,
            EditorialRunStatus,
        )

        editorial_repo.insert_editorial_run(
            conn,
            EditorialRun(
                run_id=editorial_repo.new_editorial_run_id(),
                project_id=project.id,
                scope=EDITORIAL_RUN_SCOPE_COVERAGE,
                status=EditorialRunStatus.FAILED,
                error_code="editorial_registry_write_failed",
                error_message="historical",
                created_at=datetime.now(timezone.utc),
                finished_at=datetime.now(timezone.utc),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    assert accept_gap_unresolved(
        project,
        gap_id=gap_id,
        confirmed_risks=["coverage_exact_match_not_verified"],
    ).ok
    key = f"{gap_id}:coverage_exact_match_not_verified"
    preview = create_script_lock(
        project,
        user_confirmed=False,
        confirmed_fingerprint="x",
        accepted_unresolved_risk_confirmations={key: True},
    )
    assert preview.preview is not None
    assert preview.preview.lock_fingerprint
    assert "historical" not in " ".join(preview.preview.blockers)


def test_r1_stale_audit_blocks_with_coverage_audit_stale(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _coverage_project(tmp_path, temp_db_path)
    from otio_app.discovery_v2.application.coverage_gap_service import (
        mark_gap_resolved_with_local_asset,
    )

    for gap in materialize_gaps_from_current_coverage(project).gaps:
        mark_gap_resolved_with_local_asset(project, gap_id=gap.gap_id, asset_id="a")
    _decide_all_claims(project)
    conn = editorial_repo.open_editorial_registry(project.project_root_path)
    try:
        state = editorial_repo.get_project_state(conn, project_id=project.id)
        assert state is not None
        audit = editorial_repo.get_coverage_audit(
            conn, coverage_audit_id=state.active_coverage_audit_id
        )
        assert audit is not None
        # Corrupt observation fingerprint on audit JSON via repository path.
        stale = audit.model_copy(update={"input_observation_fingerprint": "stale-fingerprint"})
        editorial_repo.save_coverage_json(project.project_root_path, stale)
    finally:
        conn.close()
    preview = preview_script_lock(project)
    assert "coverage_audit_stale" in preview.blockers


def test_smoke_d_fingerprint_mismatch_on_stale_display(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project, gap_id = _reproduced_blocker_fixture(tmp_path, temp_db_path)
    assert accept_gap_unresolved(
        project,
        gap_id=gap_id,
        confirmed_risks=["coverage_exact_match_not_verified"],
    ).ok
    key = f"{gap_id}:coverage_exact_match_not_verified"
    preview = create_script_lock(
        project,
        user_confirmed=False,
        confirmed_fingerprint="x",
        accepted_unresolved_risk_confirmations={key: True},
    )
    assert preview.preview and preview.preview.lock_fingerprint
    displayed = preview.preview.lock_fingerprint
    # Mutate claim decision to change lock inputs.
    view = get_editorial_view(project)
    claim = view.script_bundle["claims"][0]
    record_claim_decision(
        project,
        script_id=view.script.script_id,
        claim_id=claim["claim_id"],
        claim_text=claim["statement"],
        decision="accepted_as_uncertain",
        reason="changed",
    )
    mismatched = create_script_lock(
        project,
        user_confirmed=True,
        confirmed_fingerprint=displayed,
        accepted_unresolved_risk_confirmations={key: True},
    )
    assert mismatched.error_code == "script_lock_fingerprint_mismatch"


def test_r1_lock_uses_server_fingerprint_without_text_input() -> None:
    source = Path(editorial_page.__file__).read_text(encoding="utf-8")
    assert "Zu bestaetigender Lock-Fingerprint" not in source
    assert "discovery_v2_lock_displayed_fingerprint" in source
    assert "Ich bestaetige genau diesen aktuellen Stand." in source


def test_r1_ui_accept_and_lock_trigger_targeted_rerun(monkeypatch) -> None:
    calls: list[str] = []

    class _Session(dict):
        def pop(self, key, default=None):
            return dict.pop(self, key, default)

    session = _Session()
    st = MagicMock()
    st.session_state = session
    st.rerun.side_effect = lambda: calls.append("rerun")
    st.checkbox.return_value = True
    st.button.return_value = False
    monkeypatch.setattr(editorial_page, "st", st)
    from otio_app.discovery_v2.ui import flash as flash_mod
    from otio_app.discovery_v2.ui.flash import FLASH_KEY

    monkeypatch.setattr(flash_mod, "st", st)
    editorial_page._flash_and_rerun("ok")
    assert session[FLASH_KEY] == {"level": "success", "message": "ok"}
    assert calls == ["rerun"]


def test_r1_editorial_ui_double_render_no_gateway_or_job(tmp_path, monkeypatch) -> None:
    from test_discovery_v2_editorial_ui import _FakeStreamlit, _project

    fake_st = _FakeStreamlit()
    project = _project(tmp_path)
    monkeypatch.setattr(editorial_page, "st", fake_st)
    monkeypatch.setattr(editorial_page, "active_discovery_project", lambda: project)
    monkeypatch.setattr(
        editorial_page,
        "get_editorial_view",
        lambda _p: MagicMock(
            ok=True,
            can_start_coverage=False,
            can_start_narrative=False,
            can_start_script=False,
            can_start_structure=False,
            coverage_audit=None,
            script=None,
            script_bundle=None,
            brief=None,
            narrative_plan=None,
            hooks=[],
            selected_hook_id=None,
            runs=[],
        ),
    )
    monkeypatch.setattr(
        editorial_page,
        "get_supplementation_view",
        lambda _p: MagicMock(
            ok=True, gaps=[], candidates_by_gap={}, script_locks=[], active_run=None, message=None
        ),
    )
    monkeypatch.setattr(
        editorial_page,
        "resolve_editorial_script_lock_gate",
        lambda _p, **_kwargs: MagicMock(
            has_effective_current_lock=False,
            effective_lock=None,
            historical_locks=(),
            current_preview=MagicMock(
                ok=False,
                lock_fingerprint=None,
                fingerprint_display=None,
                fulfilled_requirements=[],
                blocking_requirements=["aktuelles Script"],
                blockers=["script_missing"],
            ),
            current_fingerprint=None,
            required_risk_keys=(),
            confirmed_risk_keys=(),
            confirmations_complete=False,
            can_create_lock=False,
            blocking_reason_codes=(),
            diagnostics=[],
        ),
    )
    start_cov = MagicMock()
    monkeypatch.setattr(editorial_page, "start_coverage_run", start_cov)
    editorial_page.render_discovery_editorial_page()
    editorial_page.render_discovery_editorial_page()
    start_cov.assert_not_called()


def test_r1_schema_remains_20(tmp_path: Path, temp_db_path: Path) -> None:
    from otio_app.discovery_v2.domain.asset_registry import REGISTRY_SCHEMA_VERSION
    from otio_app.discovery_v2.persistence import asset_registry_database as reg_db

    project = _coverage_project(tmp_path, temp_db_path)
    conn = reg_db.get_registry_connection(project.project_root_path)
    try:
        assert reg_db.read_schema_version(conn) == REGISTRY_SCHEMA_VERSION == "20"
    finally:
        conn.close()


def test_r1_no_classic_otio_write_from_accept(tmp_path: Path, temp_db_path: Path) -> None:
    project, gap_id = _reproduced_blocker_fixture(tmp_path, temp_db_path)
    assert accept_gap_unresolved(
        project,
        gap_id=gap_id,
        confirmed_risks=["coverage_exact_match_not_verified"],
    ).ok
    classic = Path(project.project_root_path) / "_otio"
    assert not classic.exists() or not any(classic.rglob("*"))
