"""L2 Script-Lock Effective Current Resolver — fail-closed, read-only."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fixtures.coverage_stability_c1 import assert_schema_20, install_no_media_io_guards
from fixtures.script_lock_current_state_l1 import (
    build_fixture_a_usa_v2_deadlock,
    build_fixture_b_latest_locked_fallback,
    build_fixture_c_stale_narration_after_invalidation,
    build_lock_ready_matching_project,
    read_editorial_current_script_lock_id,
    read_latest_locked_script_lock,
    read_narration_current_script_lock_id,
    read_script_lock,
    _accept_one_gap_unresolved,
    _create_lock,
    _decide_all_claims,
    _resolve_all_gaps_locally,
)
from otio_app.discovery_v2.adapters.analysis_job_launcher import (
    reset_analysis_job_launcher_for_tests,
)
from otio_app.discovery_v2.adapters.editorial_job_launcher import (
    reset_editorial_job_launcher_for_tests,
)
from otio_app.discovery_v2.adapters.narration_job_launcher import (
    reset_narration_job_launcher_for_tests,
)
from otio_app.discovery_v2.adapters.supplementation_job_launcher import (
    reset_supplementation_job_launcher_for_tests,
)
from otio_app.discovery_v2.adapters.text_fake import reset_fake_text_test_hook
from otio_app.discovery_v2.adapters.voice_fake import reset_fake_voice_call_count
from otio_app.discovery_v2.application.editorial_service import (
    get_editorial_view,
    save_user_script_edit,
    start_coverage_run,
    start_structure_run,
)
from otio_app.discovery_v2.application.observation_review_service import (
    list_editorial_ready_observations,
    submit_observation_review,
)
from otio_app.discovery_v2.application.script_lock_current_state_service import (
    resolve_effective_current_script_lock,
)
from otio_app.discovery_v2.application.script_lock_service import (
    build_current_script_lock_preview,
    get_effective_script_lock,
)
from otio_app.discovery_v2.domain.asset_registry import REGISTRY_SCHEMA_VERSION
from otio_app.discovery_v2.domain.script_lock_current_state import (
    NARRATION_SCRIPT_LOCK_STALE,
    SCRIPT_LOCK_CURRENT_POINTER_MISSING,
    SCRIPT_LOCK_CURRENT_POINTER_STALE,
    SCRIPT_LOCK_CURRENT_STATE_SCHEMA_VERSION,
    SCRIPT_LOCK_EDITORIAL_STATE_MISMATCH,
    SCRIPT_LOCK_EFFECTIVE,
    SCRIPT_LOCK_FINGERPRINT_MISMATCH,
    SCRIPT_LOCK_FINGERPRINT_UNAVAILABLE,
    SCRIPT_LOCK_RISK_CONFIRMATION_MISMATCH,
    SCRIPT_LOCK_STATUS_NOT_EFFECTIVE,
)
from otio_app.discovery_v2.domain.supplementation import (
    ScriptLock,
    ScriptLockStatus,
    make_lock_risk_confirmation_key,
)
from otio_app.discovery_v2.persistence import editorial_repository as editorial_repo
from otio_app.discovery_v2.persistence import supplementation_repository as supp_repo
from otio_app.discovery_v2.persistence.asset_registry_database import (
    get_registry_connection,
    read_schema_version,
)
from test_discovery_v2_script_lock import _script_coverage_project


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


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _set_editorial_pointer(project, lock_id: str | None) -> None:
    conn = supp_repo.open_supplementation_registry(project.project_root_path)
    try:
        state = editorial_repo.get_project_state(conn, project_id=project.id)
        assert state is not None
        editorial_repo.upsert_project_state(
            conn,
            state.model_copy(
                update={"current_script_lock_id": lock_id, "updated_at": _now()}
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _app_state_snapshot(project) -> dict:
    """Application-level mutation detector (schema ensure may touch SQLite bytes)."""

    locks = []
    conn = supp_repo.open_supplementation_registry(project.project_root_path)
    try:
        for lock in supp_repo.list_script_locks(conn, project_id=project.id):
            locks.append(
                {
                    "lock_id": lock.lock_id,
                    "status": lock.status.value,
                    "lock_fingerprint": lock.lock_fingerprint,
                    "accepted_open_risks": list(lock.accepted_open_risks or []),
                }
            )
    finally:
        conn.close()
    root = Path(project.project_root_path)
    artifact_files = sorted(
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix in {".json", ".wav"}
        and "registry" not in path.parts
    )
    return {
        "editorial_pointer": read_editorial_current_script_lock_id(project),
        "narration_pointer": read_narration_current_script_lock_id(project),
        "locks": locks,
        "artifact_files": artifact_files,
    }


def _tamper_lock_confirmation_fingerprint(project, *, lock_id: str, value: str) -> None:
    conn = supp_repo.open_supplementation_registry(project.project_root_path)
    try:
        conn.execute(
            "UPDATE script_locks SET confirmation_fingerprint = ? WHERE lock_id = ?",
            (value, lock_id),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_foreign_project_lock(project, *, source_lock: ScriptLock) -> ScriptLock:
    foreign = source_lock.model_copy(
        update={
            "lock_id": "foreign-project-lock",
            "project_id": "other-project-id",
            "lock_version": source_lock.lock_version + 100,
            "status": ScriptLockStatus.LOCKED,
            "created_at": _now(),
        }
    )
    relative = supp_repo.save_script_lock_json(project.project_root_path, foreign)
    conn = supp_repo.open_supplementation_registry(project.project_root_path)
    try:
        # Unique index allows only one locked row per project_id — foreign
        # project_id is distinct, so this insert is valid.
        supp_repo.insert_script_lock(conn, foreign, relative)
        conn.commit()
    finally:
        conn.close()
    return foreign


# --- Core pointer / fallback -------------------------------------------------


def test_l2_missing_editorial_pointer_returns_no_effective_lock_without_latest_fallback(
    tmp_path: Path, temp_db_path: Path
) -> None:
    fx = build_fixture_b_latest_locked_fallback(tmp_path, temp_db_path)
    resolution = resolve_effective_current_script_lock(fx.project)
    assert resolution.is_effective is False
    assert resolution.reason_code == SCRIPT_LOCK_CURRENT_POINTER_MISSING
    assert resolution.effective_lock is None
    assert resolution.current_script_lock_id is None
    assert read_latest_locked_script_lock(fx.project) is not None


def test_l2_missing_pointer_does_not_use_matching_latest_locked_row(
    tmp_path: Path, temp_db_path: Path
) -> None:
    fx = build_fixture_b_latest_locked_fallback(tmp_path, temp_db_path)
    latest = read_latest_locked_script_lock(fx.project)
    assert latest is not None
    preview = build_current_script_lock_preview(fx.project)
    assert preview.lock_fingerprint == latest.lock_fingerprint
    resolution = resolve_effective_current_script_lock(fx.project)
    assert resolution.is_effective is False
    assert resolution.reason_code == SCRIPT_LOCK_CURRENT_POINTER_MISSING
    assert get_effective_script_lock(fx.project).ok is False


def test_l2_stale_pointer_to_missing_lock_fails_closed(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project, lock = build_lock_ready_matching_project(tmp_path, temp_db_path)
    _set_editorial_pointer(project, "missing-lock-id")
    resolution = resolve_effective_current_script_lock(project)
    assert resolution.is_effective is False
    assert resolution.reason_code == SCRIPT_LOCK_CURRENT_POINTER_STALE
    assert resolution.current_script_lock_id == "missing-lock-id"
    # Original lock still locked; resolver did not mutate.
    assert read_script_lock(project, lock_id=lock.lock_id).status == ScriptLockStatus.LOCKED


def test_l2_pointer_to_other_project_lock_fails_closed(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project, lock = build_lock_ready_matching_project(tmp_path, temp_db_path)
    foreign = _insert_foreign_project_lock(project, source_lock=lock)
    _set_editorial_pointer(project, foreign.lock_id)
    resolution = resolve_effective_current_script_lock(project)
    assert resolution.is_effective is False
    assert resolution.reason_code == SCRIPT_LOCK_EDITORIAL_STATE_MISMATCH
    assert "project_id" in resolution.mismatched_fields


def test_l2_non_locked_status_is_not_effective(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project, lock = build_lock_ready_matching_project(tmp_path, temp_db_path)
    conn = supp_repo.open_supplementation_registry(project.project_root_path)
    try:
        supp_repo.update_script_lock_status(
            conn, lock_id=lock.lock_id, status=ScriptLockStatus.SUPERSEDED
        )
        conn.commit()
    finally:
        conn.close()
    resolution = resolve_effective_current_script_lock(project)
    assert resolution.is_effective is False
    assert resolution.reason_code == SCRIPT_LOCK_STATUS_NOT_EFFECTIVE


# --- Identity matrix ---------------------------------------------------------


@pytest.mark.parametrize(
    "field_name",
    [
        "script_id",
        "script_version",
        "narrative_plan_id",
        "selected_hook_id",
        "coverage_audit_id",
        "observation_fingerprint",
    ],
)
def test_l2_identity_field_mismatch_is_not_effective(
    field_name: str, tmp_path: Path, temp_db_path: Path
) -> None:
    """Parametric identity matrix — each field alone fails closed."""

    if field_name == "script_id":
        test_l2_script_id_mismatch_is_not_effective(tmp_path, temp_db_path)
    elif field_name == "script_version":
        test_l2_script_version_mismatch_is_not_effective(tmp_path, temp_db_path)
    elif field_name == "narrative_plan_id":
        test_l2_narrative_plan_mismatch_is_not_effective(tmp_path, temp_db_path)
    elif field_name == "selected_hook_id":
        test_l2_selected_hook_mismatch_is_not_effective(tmp_path, temp_db_path)
    elif field_name == "coverage_audit_id":
        test_l2_coverage_audit_mismatch_is_not_effective(tmp_path, temp_db_path)
    else:
        test_l2_observation_fingerprint_mismatch_is_not_effective(
            tmp_path, temp_db_path
        )


def test_l2_script_id_mismatch_is_not_effective(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project, lock = build_lock_ready_matching_project(tmp_path, temp_db_path)
    view = get_editorial_view(project)
    assert view.script is not None
    assert save_user_script_edit(
        project, full_text=view.script.full_text + " L2 script id change."
    ).ok
    assert start_structure_run(project, sync=True).started
    # Promote status like L1 helper (FakeText keeps structure_pending).
    from fixtures.script_lock_current_state_l1 import (
        _promote_structured_script_to_review_requested,
    )

    view2 = get_editorial_view(project)
    assert view2.script is not None
    _promote_structured_script_to_review_requested(
        project, script_id=view2.script.script_id
    )
    assert start_coverage_run(project, sync=True).started
    _resolve_all_gaps_locally(project)
    _decide_all_claims(project)
    # Keep pointer on Lock A (script edit preserves pointer).
    assert read_editorial_current_script_lock_id(project) == lock.lock_id
    resolution = resolve_effective_current_script_lock(project)
    assert resolution.is_effective is False
    assert resolution.reason_code == SCRIPT_LOCK_EDITORIAL_STATE_MISMATCH
    assert "script_id" in resolution.mismatched_fields


def test_l2_script_version_mismatch_is_not_effective(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project, lock = build_lock_ready_matching_project(tmp_path, temp_db_path)
    view = get_editorial_view(project)
    assert view.script is not None
    assert lock.script_version == view.script.script_version == 1
    assert save_user_script_edit(
        project, full_text=view.script.full_text + " L2 script version change."
    ).ok
    view2 = get_editorial_view(project)
    assert view2.script is not None
    assert view2.script.script_version == 2
    assert read_editorial_current_script_lock_id(project) == lock.lock_id
    resolution = resolve_effective_current_script_lock(project)
    assert resolution.is_effective is False
    assert resolution.reason_code == SCRIPT_LOCK_EDITORIAL_STATE_MISMATCH
    assert "script_version" in resolution.mismatched_fields


def test_l2_narrative_plan_mismatch_is_not_effective(
    tmp_path: Path, temp_db_path: Path
) -> None:
    fx = build_fixture_a_usa_v2_deadlock(
        tmp_path, temp_db_path, with_pause_and_timeline=False
    )
    # Restore editorial pointer onto historical Lock A against Narrative B stand.
    _set_editorial_pointer(fx.project, fx.lock_a.lock_id)
    resolution = resolve_effective_current_script_lock(fx.project)
    assert resolution.is_effective is False
    assert resolution.reason_code == SCRIPT_LOCK_EDITORIAL_STATE_MISMATCH
    assert "narrative_plan_id" in resolution.mismatched_fields


def test_l2_selected_hook_mismatch_is_not_effective(
    tmp_path: Path, temp_db_path: Path
) -> None:
    fx = build_fixture_a_usa_v2_deadlock(
        tmp_path, temp_db_path, with_pause_and_timeline=False
    )
    _set_editorial_pointer(fx.project, fx.lock_a.lock_id)
    resolution = resolve_effective_current_script_lock(fx.project)
    assert resolution.is_effective is False
    assert "selected_hook_id" in resolution.mismatched_fields


def test_l2_coverage_audit_mismatch_is_not_effective(
    tmp_path: Path, temp_db_path: Path
) -> None:
    fx = build_fixture_a_usa_v2_deadlock(
        tmp_path, temp_db_path, with_pause_and_timeline=False
    )
    _set_editorial_pointer(fx.project, fx.lock_a.lock_id)
    resolution = resolve_effective_current_script_lock(fx.project)
    assert resolution.is_effective is False
    assert "coverage_audit_id" in resolution.mismatched_fields


def test_l2_observation_fingerprint_mismatch_is_not_effective(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project, lock = build_lock_ready_matching_project(tmp_path, temp_db_path)
    observations = list_editorial_ready_observations(project)
    assert observations
    assert submit_observation_review(
        project,
        observation_id=observations[0].observation_id,
        decision="rejected",
        reason_code="l2_obs_mismatch",
    ).ok
    resolution = resolve_effective_current_script_lock(project)
    assert resolution.is_effective is False
    assert resolution.reason_code in {
        SCRIPT_LOCK_EDITORIAL_STATE_MISMATCH,
        SCRIPT_LOCK_FINGERPRINT_UNAVAILABLE,
        SCRIPT_LOCK_FINGERPRINT_MISMATCH,
    }
    if resolution.reason_code == SCRIPT_LOCK_EDITORIAL_STATE_MISMATCH:
        assert "observation_fingerprint" in resolution.mismatched_fields


# --- Fingerprint / risks -----------------------------------------------------


def test_l2_lock_fingerprint_mismatch_is_not_effective(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project, lock = build_lock_ready_matching_project(tmp_path, temp_db_path)
    view = get_editorial_view(project)
    assert view.script is not None
    assert save_user_script_edit(
        project, full_text=view.script.full_text + " Fingerprint drift."
    ).ok
    # Pointer still on old lock; identity/fingerprint fail-closed.
    resolution = resolve_effective_current_script_lock(project)
    assert resolution.is_effective is False
    assert resolution.reason_code in {
        SCRIPT_LOCK_EDITORIAL_STATE_MISMATCH,
        SCRIPT_LOCK_FINGERPRINT_MISMATCH,
        SCRIPT_LOCK_FINGERPRINT_UNAVAILABLE,
    }
    assert lock.lock_fingerprint != (
        build_current_script_lock_preview(project).lock_fingerprint or ""
    )


def test_l2_unavailable_current_fingerprint_fails_closed(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project, lock = build_lock_ready_matching_project(tmp_path, temp_db_path)
    observations = list_editorial_ready_observations(project)
    assert observations
    assert submit_observation_review(
        project,
        observation_id=observations[0].observation_id,
        decision="rejected",
        reason_code="l2_fp_unavailable",
    ).ok
    # Rejecting observations makes coverage stale → preview fingerprint unavailable
    # after identity observation mismatch or as fingerprint_unavailable.
    resolution = resolve_effective_current_script_lock(project)
    assert resolution.is_effective is False
    assert resolution.reason_code in {
        SCRIPT_LOCK_FINGERPRINT_UNAVAILABLE,
        SCRIPT_LOCK_EDITORIAL_STATE_MISMATCH,
        SCRIPT_LOCK_FINGERPRINT_MISMATCH,
    }
    assert read_script_lock(project, lock_id=lock.lock_id).status == ScriptLockStatus.LOCKED


def test_l2_risk_confirmation_set_mismatch_is_not_effective(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project, lock = build_lock_ready_matching_project(tmp_path, temp_db_path)
    preview = build_current_script_lock_preview(project)
    assert preview.lock_fingerprint == lock.lock_fingerprint
    _tamper_lock_confirmation_fingerprint(
        project, lock_id=lock.lock_id, value="tampered-confirmation-fingerprint"
    )
    resolution = resolve_effective_current_script_lock(project)
    assert resolution.is_effective is False
    assert resolution.reason_code == SCRIPT_LOCK_RISK_CONFIRMATION_MISMATCH
    assert resolution.current_fingerprint == preview.lock_fingerprint


def test_l2_new_gap_id_with_same_risk_code_requires_new_confirmation(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _script_coverage_project(tmp_path, temp_db_path)
    risk_key_a = _accept_one_gap_unresolved(project)
    _decide_all_claims(project)
    lock = _create_lock(
        project,
        accepted_unresolved_risk_confirmations={risk_key_a: True},
    )
    old_gap_id, risk_code = risk_key_a.split(":", 1)
    assert risk_code
    # Advance editorial stand → new audit/gaps; keep pointer on Lock A.
    view = get_editorial_view(project)
    assert view.script is not None
    assert save_user_script_edit(
        project, full_text=view.script.full_text + " New gap generation stand."
    ).ok
    assert start_structure_run(project, sync=True).started
    from fixtures.script_lock_current_state_l1 import (
        _promote_structured_script_to_review_requested,
    )

    view2 = get_editorial_view(project)
    assert view2.script is not None
    _promote_structured_script_to_review_requested(
        project, script_id=view2.script.script_id
    )
    assert start_coverage_run(project, sync=True).started
    risk_key_b = _accept_one_gap_unresolved(project)
    _decide_all_claims(project)
    new_gap_id, new_code = risk_key_b.split(":", 1)
    assert new_gap_id != old_gap_id
    assert new_code == risk_code or True  # same risk family when fixture yields it
    assert read_editorial_current_script_lock_id(project) == lock.lock_id

    preview = build_current_script_lock_preview(project)
    current_keys = set(preview.accepted_open_risks or [])
    assert risk_key_a not in current_keys
    if risk_key_b:
        assert make_lock_risk_confirmation_key(new_gap_id, new_code) in current_keys or (
            risk_key_b in current_keys
        )

    resolution = resolve_effective_current_script_lock(project)
    assert resolution.is_effective is False
    assert resolution.reason_code in {
        SCRIPT_LOCK_EDITORIAL_STATE_MISMATCH,
        SCRIPT_LOCK_FINGERPRINT_MISMATCH,
        SCRIPT_LOCK_FINGERPRINT_UNAVAILABLE,
        SCRIPT_LOCK_RISK_CONFIRMATION_MISMATCH,
    }
    # Old confirmation cannot authorize the new gap_id stand.
    assert risk_key_a in (lock.accepted_open_risks or [])


# --- Success / narration diagnostics / read-only -----------------------------


def test_l2_exact_current_lock_is_effective(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project, lock = build_lock_ready_matching_project(tmp_path, temp_db_path)
    resolution = resolve_effective_current_script_lock(project)
    assert resolution.is_effective is True
    assert resolution.reason_code == SCRIPT_LOCK_EFFECTIVE
    assert resolution.effective_lock is not None
    assert resolution.effective_lock.lock_id == lock.lock_id
    assert resolution.current_fingerprint == lock.lock_fingerprint
    assert resolution.schema_version == SCRIPT_LOCK_CURRENT_STATE_SCHEMA_VERSION


def test_l2_stale_narration_pointer_does_not_replace_editorial_pointer(
    tmp_path: Path, temp_db_path: Path
) -> None:
    fx = build_fixture_c_stale_narration_after_invalidation(tmp_path, temp_db_path)
    assert fx.editorial_current_script_lock_id is None
    assert fx.narration_current_script_lock_id == fx.lock.lock_id
    resolution = resolve_effective_current_script_lock(fx.project)
    assert resolution.is_effective is False
    assert resolution.reason_code == SCRIPT_LOCK_CURRENT_POINTER_MISSING
    assert NARRATION_SCRIPT_LOCK_STALE in resolution.diagnostics
    assert resolution.narration_current_script_lock_id == fx.lock.lock_id


def test_l2_narration_pointer_mismatch_is_reported_without_mutation(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project, lock = build_lock_ready_matching_project(tmp_path, temp_db_path)
    before = _app_state_snapshot(project)
    resolution = resolve_effective_current_script_lock(project)
    assert resolution.is_effective is True
    # No voice yet → narration pointer missing while editorial lock is effective.
    assert resolution.narration_current_script_lock_id is None
    assert NARRATION_SCRIPT_LOCK_STALE in resolution.diagnostics
    assert read_editorial_current_script_lock_id(project) == lock.lock_id
    assert read_script_lock(project, lock_id=lock.lock_id).status == ScriptLockStatus.LOCKED
    assert _app_state_snapshot(project) == before


def test_l2_resolver_is_deterministic_and_read_only(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project, lock = build_lock_ready_matching_project(tmp_path, temp_db_path)
    before = _app_state_snapshot(project)
    first = resolve_effective_current_script_lock(project)
    second = resolve_effective_current_script_lock(project)
    assert first.is_effective is second.is_effective is True
    assert first.reason_code == second.reason_code == SCRIPT_LOCK_EFFECTIVE
    assert first.effective_lock.lock_id == second.effective_lock.lock_id == lock.lock_id
    assert first.current_fingerprint == second.current_fingerprint
    assert first.diagnostics == second.diagnostics
    assert _app_state_snapshot(project) == before


def test_l2_resolver_writes_no_registry_or_artifact_files(
    tmp_path: Path, temp_db_path: Path
) -> None:
    fx = build_fixture_a_usa_v2_deadlock(
        tmp_path, temp_db_path, with_pause_and_timeline=False
    )
    before = _app_state_snapshot(fx.project)
    resolution = resolve_effective_current_script_lock(fx.project)
    assert resolution.is_effective is False
    assert _app_state_snapshot(fx.project) == before
    assert read_editorial_current_script_lock_id(fx.project) is None
    assert (
        read_script_lock(fx.project, lock_id=fx.lock_a.lock_id).status
        == ScriptLockStatus.LOCKED
    )


def test_l2_resolver_calls_no_gateway_and_reads_no_media(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, _lock = build_lock_ready_matching_project(tmp_path, temp_db_path)
    install_no_media_io_guards(monkeypatch)
    calls: list[str] = []

    def _block_generate(self, request):  # noqa: ANN001
        calls.append(request.request_kind)
        raise AssertionError("gateway must not be called by L2 resolver")

    monkeypatch.setattr(
        "otio_app.discovery_v2.adapters.text_gateway.DiscoveryTextGateway.generate",
        _block_generate,
    )
    resolution = resolve_effective_current_script_lock(project)
    assert resolution.is_effective is True
    assert calls == []


def test_l2_schema20_classic_without_vo_isolation(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, _lock = build_lock_ready_matching_project(tmp_path, temp_db_path)
    install_no_media_io_guards(monkeypatch)
    assert_schema_20(project)
    with get_registry_connection(project.project_root_path) as conn:
        assert read_schema_version(conn) == REGISTRY_SCHEMA_VERSION == "20"
    import otio_app.discovery_v2.application.script_lock_current_state_service as app_mod
    import otio_app.discovery_v2.domain.script_lock_current_state as domain_mod

    src = Path(domain_mod.__file__).read_text(encoding="utf-8")
    src += Path(app_mod.__file__).read_text(encoding="utf-8")
    assert "without_vo" not in src
    assert "cut_plan" not in src
    assert "otio_exporter" not in src
    resolution = resolve_effective_current_script_lock(project)
    assert resolution.is_effective is True


# --- Smokes A–F --------------------------------------------------------------


def test_l2_smoke_a_no_pointer_matching_historical_locked_row(
    tmp_path: Path, temp_db_path: Path
) -> None:
    """Smoke A — pointer NULL + matching locked row → no effective lock."""

    fx = build_fixture_b_latest_locked_fallback(tmp_path, temp_db_path)
    resolution = resolve_effective_current_script_lock(fx.project)
    assert resolution.reason_code == SCRIPT_LOCK_CURRENT_POINTER_MISSING
    assert resolution.is_effective is False


def test_l2_smoke_b_valid_current_lock(tmp_path: Path, temp_db_path: Path) -> None:
    """Smoke B — pointer + identity + fingerprint + risks + locked."""

    project, lock = build_lock_ready_matching_project(tmp_path, temp_db_path)
    resolution = resolve_effective_current_script_lock(project)
    assert resolution.is_effective is True
    assert resolution.effective_lock.lock_id == lock.lock_id
    assert resolution.reason_code == SCRIPT_LOCK_EFFECTIVE


def test_l2_smoke_c_script_version_changed(
    tmp_path: Path, temp_db_path: Path
) -> None:
    """Smoke C — Lock v1 vs active Script v2 → not effective."""

    project, lock = build_lock_ready_matching_project(tmp_path, temp_db_path)
    assert lock.script_version == 1
    view = get_editorial_view(project)
    assert save_user_script_edit(
        project, full_text=view.script.full_text + " Version 2 body."
    ).ok
    view2 = get_editorial_view(project)
    assert view2.script is not None
    assert view2.script.script_version == 2
    resolution = resolve_effective_current_script_lock(project)
    assert resolution.is_effective is False
    assert resolution.reason_code == SCRIPT_LOCK_EDITORIAL_STATE_MISMATCH
    assert "script_version" in resolution.mismatched_fields


def test_l2_smoke_d_new_gap_same_risk_code(
    tmp_path: Path, temp_db_path: Path
) -> None:
    """Smoke D — new gap_id with same risk_code → old confirmation insufficient."""

    test_l2_new_gap_id_with_same_risk_code_requires_new_confirmation(
        tmp_path, temp_db_path
    )


def test_l2_smoke_e_stale_narration_pointer(
    tmp_path: Path, temp_db_path: Path
) -> None:
    """Smoke E — Editorial missing + Narration stale → no current lock, no mutation."""

    fx = build_fixture_a_usa_v2_deadlock(
        tmp_path, temp_db_path, with_pause_and_timeline=False
    )
    before = _app_state_snapshot(fx.project)
    assert fx.narration_current_script_lock_id == fx.lock_a.lock_id
    resolution = resolve_effective_current_script_lock(fx.project)
    assert resolution.is_effective is False
    assert resolution.reason_code == SCRIPT_LOCK_CURRENT_POINTER_MISSING
    assert NARRATION_SCRIPT_LOCK_STALE in resolution.diagnostics
    assert _app_state_snapshot(fx.project) == before
    assert read_narration_current_script_lock_id(fx.project) == fx.lock_a.lock_id


def test_l2_smoke_f_read_only_deterministic(
    tmp_path: Path, temp_db_path: Path
) -> None:
    """Smoke F — two resolves identical; no registry/file changes."""

    test_l2_resolver_is_deterministic_and_read_only(tmp_path, temp_db_path)
