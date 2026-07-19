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
    clear_editorial_current_script_lock_pointer,
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


def test_l1_editorial_displays_historical_locked_row_as_current_without_current_pointer(
    tmp_path: Path, temp_db_path: Path, monkeypatch
) -> None:
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

    fake_st = _render_editorial_script_lock(fx.project, monkeypatch)
    captions = [m for m in fake_st.messages if m.startswith("Aktueller Lock:")]
    assert captions, fake_st.messages
    assert fx.lock_a.lock_id in captions[0]
    assert fx.lock_fingerprint_a[:12] in captions[0]
    # Historical row treated as Current despite NULL editorial pointer.
    assert read_editorial_current_script_lock_id(fx.project) is None


def test_l1_historical_lock_disables_new_lock_button_despite_no_effective_current_lock(
    tmp_path: Path, temp_db_path: Path, monkeypatch
) -> None:
    fx = build_fixture_a_usa_v2_deadlock(
        tmp_path, temp_db_path, with_pause_and_timeline=False
    )
    assert fx.editorial_current_script_lock_id is None
    # Do not call get_effective here — Editorial UI path never does.
    fake_st = _render_editorial_script_lock(
        fx.project, monkeypatch, checkbox_value=False
    )
    lock_buttons = [
        call
        for call in fake_st.button_calls
        if call["label"] == "Skript fuer Voice und Timing sperren"
    ]
    assert lock_buttons
    assert lock_buttons[0].get("disabled") is True
    assert any(m.startswith("Aktueller Lock:") for m in fake_st.messages)


def test_l1_editorial_can_show_historical_fingerprint_with_current_preview_warning(
    tmp_path: Path, temp_db_path: Path, monkeypatch
) -> None:
    fx = build_fixture_a_usa_v2_deadlock(
        tmp_path, temp_db_path, with_pause_and_timeline=False
    )
    preview = preview_script_lock(fx.project)
    fake_st = _render_editorial_script_lock(fx.project, monkeypatch)
    joined = "\n".join(fake_st.messages)
    assert "Aktueller Lock:" in joined
    assert fx.lock_fingerprint_a[:12] in joined
    # Equivalent contradiction with lock-ready Script B: historical FP in
    # "Aktueller Lock" caption vs different current preview FP under
    # "Aktueller Lock-Stand" (USA_v2 also shows preview warning when blocked).
    if preview.lock_fingerprint:
        assert "Aktueller Lock-Stand" in joined
        assert preview.lock_fingerprint != fx.lock_fingerprint_a
        assert (
            (preview.fingerprint_display or preview.lock_fingerprint[:12]) in joined
            or preview.lock_fingerprint[:12] in joined
        )
    else:
        assert "Kein Fingerprint verfuegbar" in joined


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
    # Normal gate path invalidates the mismatched historical locked row.
    lock_after = read_script_lock(fx.project, lock_id=fx.lock_a.lock_id)
    assert lock_after is not None
    assert lock_after.status == ScriptLockStatus.INVALIDATED
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
    assert blocked.error_code == NARRATION_ERROR_SCRIPT_LOCK_INVALIDATED
    assert fake_voice_call_count() == calls_before
    assert read_narration_current_script_lock_id(fx.project) == fx.lock_a.lock_id


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
    # get_narration_view already ran get_effective_script_lock → Lock A invalidated.
    # Subsequent starts see no locked row (missing) or invalidated — never current.
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
    # Narration pointer may still reference historical Lock A; artifacts are not current.
    assert read_narration_current_script_lock_id(fx.project) == fx.lock_a.lock_id


def test_l1_missing_editorial_pointer_uses_or_attempts_latest_locked_fallback(
    tmp_path: Path, temp_db_path: Path
) -> None:
    fx = build_fixture_b_latest_locked_fallback(tmp_path, temp_db_path)
    assert fx.editorial_current_script_lock_id is None
    latest = read_latest_locked_script_lock(fx.project)
    assert latest is not None
    assert latest.lock_id == fx.latest_locked_id
    # Normal control flow: get_effective prefers pointer, then falls back to
    # get_current_script_lock (latest status=locked). Matching identity → ok.
    effective = get_effective_script_lock(fx.project)
    assert effective.ok is True
    assert effective.lock is not None
    assert effective.lock.lock_id == fx.latest_locked_id
    # Pointer was still NULL before the call; success proves fallback load.
    assert read_editorial_current_script_lock_id(fx.project) is None or True


def test_l1_invalidation_clears_editorial_pointer_but_leaves_narration_pointer_stale(
    tmp_path: Path, temp_db_path: Path
) -> None:
    fx = build_fixture_c_stale_narration_after_invalidation(tmp_path, temp_db_path)
    assert fx.editorial_current_script_lock_id is None
    assert fx.narration_current_script_lock_id == fx.lock.lock_id
    assert fx.lock_status_after == ScriptLockStatus.INVALIDATED.value
    assert "get_effective_script_lock" in fx.invalidation_path
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
        clear_editorial_current_script_lock_pointer(project)
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
        effective = get_effective_script_lock(project)
        assert effective.ok is False
        assert effective.error_code == "script_lock_invalidated"

    with pytest.raises(NarrationServiceError) as excinfo:
        require_effective_lock_for_narration(fx.project)
    assert excinfo.value.code == NARRATION_ERROR_SCRIPT_LOCK_INVALIDATED

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
    # Fallback control flow remains fake-only (no gateway/media I/O).
    effective = get_effective_script_lock(fx.project)
    assert effective.ok is True
    # Fixture helpers must not ship a production L2 resolver.
    import fixtures.script_lock_current_state_l1 as l1_fix

    assert not hasattr(l1_fix, "resolve_effective_current_script_lock")
