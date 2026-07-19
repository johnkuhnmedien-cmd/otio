"""L1 Script-Lock Current-State deadlock fixtures — document current behaviour only."""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fixtures.script_lock_current_state_l1 import (
    LOCK_IDENTITY_FIELDS,
    assert_schema_20,
    build_fixture_a_usa_v2_deadlock,
    build_fixture_b_latest_locked_fallback,
    build_fixture_c_stale_narration_after_invalidation,
    build_lock_ready_matching_project,
    current_observation_fingerprint,
    identity_mismatches,
    install_no_media_io_guards,
    list_project_script_locks,
    read_editorial_current_script_lock_id,
    read_latest_locked_script_lock,
    read_narration_current_script_lock_id,
    read_narration_state,
    read_script_lock,
    snapshot_editorial_identity,
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
from otio_app.discovery_v2.adapters.text_config import load_text_config
from otio_app.discovery_v2.adapters.text_fake import reset_fake_text_test_hook
from otio_app.discovery_v2.adapters.voice_fake import (
    fake_voice_call_count,
    reset_fake_voice_call_count,
)
from otio_app.discovery_v2.application.editorial_service import get_editorial_view
from otio_app.discovery_v2.application.narration_timing_service import (
    start_narration_timing_run,
)
from otio_app.discovery_v2.application.observation_review_service import (
    list_editorial_ready_observations,
    submit_observation_review,
)
from otio_app.discovery_v2.application.pause_direction_service import (
    start_pause_direction_run,
)
from otio_app.discovery_v2.application.script_lock_service import (
    get_effective_script_lock,
    preview_script_lock,
)
from otio_app.discovery_v2.application.supplementation_service import (
    get_supplementation_view,
)
from otio_app.discovery_v2.application.voice_generation_service import (
    NarrationServiceError,
    get_narration_view,
    require_effective_lock_for_narration,
    start_voice_generation_run,
)
from otio_app.discovery_v2.domain.asset_registry import REGISTRY_SCHEMA_VERSION
from otio_app.discovery_v2.domain.narration import (
    NARRATION_ERROR_SCRIPT_LOCK_INVALIDATED,
    NARRATION_ERROR_SCRIPT_LOCK_MISSING,
)
from otio_app.discovery_v2.domain.script_lock_current_state import (
    SCRIPT_LOCK_CURRENT_POINTER_MISSING,
)
from otio_app.discovery_v2.domain.supplementation import ScriptLockStatus
from otio_app.discovery_v2.persistence.asset_registry_database import (
    get_registry_connection,
    read_schema_version,
)
from otio_app.discovery_v2.ui import editorial_page
from otio_app.discovery_v2.ui import narration_page


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


class _FakeContext:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeStreamlit:
    """Streamlit stub that records caption/button disabled state for L1 UI proofs."""

    def __init__(self, *, checkbox_value: bool = False) -> None:
        self.buttons: list[str] = []
        self.button_calls: list[dict] = []
        self.messages: list[str] = []
        self.session_state: dict = {}
        self._checkbox_value = checkbox_value

    def title(self, text):
        self.messages.append(str(text))

    def info(self, text):
        self.messages.append(str(text))

    def warning(self, text):
        self.messages.append(str(text))

    def success(self, text):
        self.messages.append(str(text))

    def caption(self, text):
        self.messages.append(str(text))

    def subheader(self, text):
        self.messages.append(str(text))

    def markdown(self, text):
        self.messages.append(str(text))

    def write(self, text):
        self.messages.append(str(text))

    def code(self, *args, **kwargs):
        if args:
            self.messages.append(str(args[0]))
        return None

    def dataframe(self, *args, **kwargs):
        return None

    def form(self, *args, **kwargs):
        return _FakeContext()

    def container(self):
        return _FakeContext()

    def expander(self, *args, **kwargs):
        return _FakeContext()

    def columns(self, count):
        return [_FakeContext() for _ in range(count)]

    def text_input(self, label, value="", **kwargs):
        return value

    def text_area(self, label, value="", **kwargs):
        return value

    def number_input(self, label, value=0, **kwargs):
        return value

    def checkbox(self, label, value=False, **kwargs):
        return self._checkbox_value

    def form_submit_button(self, label, **kwargs):
        self.buttons.append(label)
        self.button_calls.append({"label": label, **kwargs})
        return False

    def button(self, label, **kwargs):
        self.buttons.append(label)
        self.button_calls.append({"label": label, **kwargs})
        return False

    def rerun(self):
        return None


def _render_editorial_script_lock(project, monkeypatch, *, checkbox_value: bool = False):
    fake_st = _FakeStreamlit(checkbox_value=checkbox_value)
    monkeypatch.setattr(editorial_page, "st", fake_st)
    view = get_editorial_view(project)
    editorial_page._render_script_lock(project, view)
    return fake_st


def _render_narration_page(project, monkeypatch):
    fake_st = _FakeStreamlit()
    monkeypatch.setattr(narration_page, "st", fake_st)
    monkeypatch.setattr(narration_page, "active_discovery_project", lambda: project)
    narration_page.render_discovery_narration_page()
    return fake_st


def test_l1_fixture_a_has_historical_locked_row_without_editorial_pointer(
    tmp_path: Path, temp_db_path: Path
) -> None:
    """L1 Fixture-Nachweis (pre-L3 UI deadlock surface retained as data proof).

    Replaces UI expectation that list_script_locks[0] was shown as
    ``Aktueller Lock``. Product contract for Current/History is asserted in L3.
    """

    fx = build_fixture_a_usa_v2_deadlock(
        tmp_path, temp_db_path, with_pause_and_timeline=False
    )
    assert fx.editorial_current_script_lock_id is None
    locks = list_project_script_locks(fx.project)
    assert locks
    assert locks[0].lock_id == fx.lock_a.lock_id
    assert locks[0].status == ScriptLockStatus.LOCKED

    supp = get_supplementation_view(fx.project)
    assert supp.script_locks
    assert supp.script_locks[0].lock_id == fx.lock_a.lock_id
    assert read_editorial_current_script_lock_id(fx.project) is None
    # Historical locked row exists while Editorial Current pointer is NULL.
    assert fx.lock_a.lock_id == locks[0].lock_id


def test_l1_fixture_a_preview_ready_with_unchecked_confirmations_surface(
    tmp_path: Path, temp_db_path: Path
) -> None:
    """L1 Fixture-Nachweis: Script B preview ready; confirmations not yet set.

    Pre-L3 UI showed a disabled New-Lock button beside a false Current caption.
    L3 product behaviour for the button is covered separately.
    """

    fx = build_fixture_a_usa_v2_deadlock(
        tmp_path, temp_db_path, with_pause_and_timeline=False
    )
    assert fx.editorial_current_script_lock_id is None
    preview = preview_script_lock(fx.project)
    assert preview.ok and preview.lock_fingerprint
    assert preview.lock_fingerprint == fx.preview_fingerprint_b
    assert preview.lock_fingerprint != fx.lock_fingerprint_a


def test_l1_fixture_a_historical_fingerprint_differs_from_current_preview(
    tmp_path: Path, temp_db_path: Path
) -> None:
    """L1 Fixture-Nachweis: historical lock FP ≠ current preview FP.

    Pre-L3 UI mixed both under Current labels; L3 separates the labels.
    """

    fx = build_fixture_a_usa_v2_deadlock(
        tmp_path, temp_db_path, with_pause_and_timeline=False
    )
    preview = preview_script_lock(fx.project)
    assert preview.lock_fingerprint
    assert fx.lock_fingerprint_a
    assert preview.lock_fingerprint != fx.lock_fingerprint_a
    assert fx.preview_fingerprint_b == preview.lock_fingerprint


def test_l1_narration_rejects_stale_lock_for_new_script_and_coverage_state(
    tmp_path: Path, temp_db_path: Path, monkeypatch
) -> None:
    fx = build_fixture_a_usa_v2_deadlock(
        tmp_path, temp_db_path, with_pause_and_timeline=False
    )
    assert fx.narration_current_script_lock_id == fx.lock_a.lock_id
    assert fx.lock_a.script_id != fx.script_b_id
    assert fx.lock_a.coverage_audit_id != fx.coverage_audit_b_id

    fake_st = _render_narration_page(fx.project, monkeypatch)
    assert any("Kein wirksamer Script Lock vorhanden" in m for m in fake_st.messages)

    view = get_narration_view(fx.project)
    assert view.effective_lock is None
    assert view.can_start_voice is False
    # L2: missing editorial pointer → no latest-fallback → lock row stays locked.
    lock_after = read_script_lock(fx.project, lock_id=fx.lock_a.lock_id)
    assert lock_after is not None
    assert lock_after.status == ScriptLockStatus.LOCKED
    assert read_narration_current_script_lock_id(fx.project) == fx.lock_a.lock_id


def test_l1_voice_remains_blocked_when_only_stale_narration_pointer_exists(
    tmp_path: Path, temp_db_path: Path
) -> None:
    fx = build_fixture_a_usa_v2_deadlock(
        tmp_path, temp_db_path, with_pause_and_timeline=False
    )
    assert fx.narration_current_script_lock_id == fx.lock_a.lock_id
    assert fx.editorial_current_script_lock_id is None
    calls_before = fake_voice_call_count()
    blocked = start_voice_generation_run(fx.project, sync=True)
    assert not blocked.started
    # No editorial pointer → gate reports missing (not invalidated-via-fallback).
    assert blocked.error_code == NARRATION_ERROR_SCRIPT_LOCK_MISSING
    assert fake_voice_call_count() == calls_before
    # L4: get_effective_script_lock clears stale Narration current on miss.
    assert read_narration_current_script_lock_id(fx.project) is None
    assert (
        read_script_lock(fx.project, lock_id=fx.lock_a.lock_id).status
        == ScriptLockStatus.LOCKED
    )


def test_l1_old_pause_and_timeline_artifacts_are_not_current_for_new_editorial_state(
    tmp_path: Path, temp_db_path: Path
) -> None:
    fx = build_fixture_a_usa_v2_deadlock(
        tmp_path, temp_db_path, with_pause_and_timeline=True
    )
    narr = read_narration_state(fx.project)
    assert narr is not None
    assert narr.current_script_lock_id == fx.lock_a.lock_id
    assert narr.current_pause_plan_id == fx.pause_plan_id
    assert narr.current_timeline_id == fx.timeline_id
    # Historical artifacts remain readable / listable.
    view = get_narration_view(fx.project)
    assert any(p.pause_plan_id == fx.pause_plan_id for p in view.pause_plans)
    assert any(t.timeline_id == fx.timeline_id for t in view.timelines)
    assert view.effective_lock is None
    # L3: get_narration_view uses the read-only resolver — Lock A stays locked.
    assert (
        read_script_lock(fx.project, lock_id=fx.lock_a.lock_id).status
        == ScriptLockStatus.LOCKED
    )
    blocked_pause = start_pause_direction_run(fx.project, sync=True)
    assert not blocked_pause.started
    assert blocked_pause.error_code in {
        NARRATION_ERROR_SCRIPT_LOCK_INVALIDATED,
        NARRATION_ERROR_SCRIPT_LOCK_MISSING,
    }
    blocked_timing = start_narration_timing_run(fx.project, sync=True)
    assert not blocked_timing.started
    assert blocked_timing.error_code in {
        NARRATION_ERROR_SCRIPT_LOCK_INVALIDATED,
        NARRATION_ERROR_SCRIPT_LOCK_MISSING,
    }
    # L4: start paths clear stale Narration current via get_effective invalidation.
    assert read_narration_current_script_lock_id(fx.project) is None


def test_l1_missing_editorial_pointer_uses_or_attempts_latest_locked_fallback(
    tmp_path: Path, temp_db_path: Path
) -> None:
    """L1 Fixture B surface — L2 removed latest-locked fallback from gates.

    Historical locked row still exists with Editorial pointer NULL. After L2,
    ``get_effective_script_lock`` must not treat that row as current.
    """

    from otio_app.discovery_v2.application.script_lock_current_state_service import (
        resolve_effective_current_script_lock,
    )
    from otio_app.discovery_v2.domain.script_lock_current_state import (
        SCRIPT_LOCK_CURRENT_POINTER_MISSING,
    )

    fx = build_fixture_b_latest_locked_fallback(tmp_path, temp_db_path)
    assert fx.editorial_current_script_lock_id is None
    latest = read_latest_locked_script_lock(fx.project)
    assert latest is not None
    assert latest.lock_id == fx.latest_locked_id
    assert latest.status.value == "locked"

    resolution = resolve_effective_current_script_lock(fx.project)
    assert resolution.is_effective is False
    assert resolution.reason_code == SCRIPT_LOCK_CURRENT_POINTER_MISSING
    assert resolution.effective_lock is None

    effective = get_effective_script_lock(fx.project)
    assert effective.ok is False
    assert effective.lock is None
    # Pointer remains NULL; historical row remains locked (no mutation via L2).
    assert read_editorial_current_script_lock_id(fx.project) is None
    still_latest = read_latest_locked_script_lock(fx.project)
    assert still_latest is not None
    assert still_latest.lock_id == fx.latest_locked_id
    assert still_latest.status.value == "locked"


def test_l1_invalidation_clears_editorial_and_narration_current_pointers(
    tmp_path: Path, temp_db_path: Path
) -> None:
    """L4 product contract: Fixture C now clears both Current pointers."""

    fx = build_fixture_c_stale_narration_after_invalidation(tmp_path, temp_db_path)
    assert fx.editorial_current_script_lock_id is None
    assert fx.narration_current_script_lock_id is None
    assert fx.lock_status_after == ScriptLockStatus.INVALIDATED.value
    assert "apply_script_lock_context_invalidation" in fx.invalidation_path
    assert "save_user_script_edit" in fx.invalidation_path


def test_l1_lock_identity_mismatch_matrix_is_fail_closed_in_narration_gate(
    tmp_path: Path, temp_db_path: Path
) -> None:
    fx = build_fixture_a_usa_v2_deadlock(
        tmp_path, temp_db_path, with_pause_and_timeline=False
    )
    current = snapshot_editorial_identity(fx.project)
    mismatches = identity_mismatches(fx.lock_a, current)
    # Core USA_v2-style identity divergences must be visible without a resolver.
    for required in (
        "script_id",
        "script_version",
        "narrative_plan_id",
        "selected_hook_id",
        "coverage_audit_id",
        "script_lock_fingerprint",
        "risk_confirmation_set",
    ):
        assert required in mismatches, (required, mismatches)

    # Observation fingerprint may still match if observations were unchanged;
    # prove fail-closed recognition on that dimension via reject-path below.
    if "observation_fingerprint" not in mismatches:
        project, lock = build_lock_ready_matching_project(
            tmp_path / "obs-mismatch", temp_db_path
        )
        assert read_editorial_current_script_lock_id(project) == lock.lock_id
        observations = list_editorial_ready_observations(project)
        assert observations
        before = current_observation_fingerprint(project)
        assert submit_observation_review(
            project,
            observation_id=observations[0].observation_id,
            decision="rejected",
            reason_code="l1_matrix_obs_mismatch",
        ).ok
        after = current_observation_fingerprint(project)
        assert after != before
        assert after != lock.observation_set_fingerprint
        # L4 observation change clears Editorial current and invalidates Lock.
        assert read_editorial_current_script_lock_id(project) is None
        assert read_script_lock(project, lock_id=lock.lock_id).status == (
            ScriptLockStatus.INVALIDATED
        )
        effective = get_effective_script_lock(project)
        assert effective.ok is False
        assert effective.error_code in {None, "script_lock_invalidated"}

    with pytest.raises(NarrationServiceError) as excinfo:
        require_effective_lock_for_narration(fx.project)
    # Fixture A has NULL editorial pointer → L2 fail-closed as missing.
    assert excinfo.value.code in {
        NARRATION_ERROR_SCRIPT_LOCK_MISSING,
        NARRATION_ERROR_SCRIPT_LOCK_INVALIDATED,
    }
    from otio_app.discovery_v2.application.script_lock_current_state_service import (
        resolve_effective_current_script_lock,
    )

    resolution = resolve_effective_current_script_lock(fx.project)
    assert resolution.is_effective is False
    assert resolution.reason_code == SCRIPT_LOCK_CURRENT_POINTER_MISSING

    # Matrix covers all documented identity dimensions at least once.
    covered = set(mismatches) | (
        {"observation_fingerprint"}
        if "observation_fingerprint" not in mismatches
        else set()
    )
    assert set(LOCK_IDENTITY_FIELDS).issubset(covered)


def test_l1_reproduction_uses_schema20_fake_only_and_no_media_io(
    tmp_path: Path, temp_db_path: Path, monkeypatch
) -> None:
    # Build first (prepare/intake may touch local media helpers), then guard.
    fx = build_fixture_b_latest_locked_fallback(tmp_path, temp_db_path)
    install_no_media_io_guards(monkeypatch)
    assert_schema_20(fx.project)
    assert REGISTRY_SCHEMA_VERSION == "20"
    conn = get_registry_connection(fx.project.project_root_path)
    try:
        assert read_schema_version(conn) == "20"
    finally:
        conn.close()
    config = load_text_config()
    assert "fake" in config.provider.lower() or "fake" in config.model_identifier.lower()
    from otio_app.discovery_v2.application.script_lock_current_state_service import (
        resolve_effective_current_script_lock,
    )
    from otio_app.discovery_v2.domain.script_lock_current_state import (
        SCRIPT_LOCK_CURRENT_POINTER_MISSING,
    )

    # Fixture B: pointer NULL + matching locked row → L2 fail-closed, no I/O.
    resolution = resolve_effective_current_script_lock(fx.project)
    assert resolution.is_effective is False
    assert resolution.reason_code == SCRIPT_LOCK_CURRENT_POINTER_MISSING
    effective = get_effective_script_lock(fx.project)
    assert effective.ok is False
    # L1 fixtures must not embed a production resolver helper.
    import fixtures.script_lock_current_state_l1 as l1_fix

    assert not hasattr(l1_fix, "resolve_effective_current_script_lock")
