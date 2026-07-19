"""L3 Script-Lock Editorial / Narration gate integration."""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fixtures.script_lock_current_state_l1 import (
    assert_schema_20,
    build_fixture_a_usa_v2_deadlock,
    build_lock_ready_matching_project,
    clear_editorial_current_script_lock_pointer,
    install_no_media_io_guards,
    list_project_script_locks,
    read_editorial_current_script_lock_id,
    read_narration_current_script_lock_id,
    read_narration_state,
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
from otio_app.discovery_v2.adapters.voice_fake import (
    fake_voice_call_count,
    reset_fake_voice_call_count,
)
from otio_app.discovery_v2.application.editorial_script_lock_gate_service import (
    resolve_editorial_script_lock_gate,
)
from otio_app.discovery_v2.application.editorial_service import get_editorial_view
from otio_app.discovery_v2.application.narration_gate_service import (
    resolve_narration_gate_state,
)
from otio_app.discovery_v2.application.script_lock_current_state_service import (
    resolve_effective_current_script_lock,
)
from otio_app.discovery_v2.application.script_lock_service import (
    create_script_lock,
    preview_script_lock,
)
from otio_app.discovery_v2.application.voice_generation_service import (
    get_narration_view,
    start_voice_generation_run,
)
from otio_app.discovery_v2.domain.asset_registry import REGISTRY_SCHEMA_VERSION
from otio_app.discovery_v2.domain.script_lock_current_state import (
    NARRATION_PAUSE_PLAN_NOT_CURRENT,
    NARRATION_POINTER_MATCHING,
    NARRATION_POINTER_MISSING,
    NARRATION_POINTER_STALE,
    NARRATION_SCRIPT_LOCK_MISSING,
    NARRATION_SCRIPT_LOCK_STALE,
    NARRATION_VOICE_NOT_CURRENT,
    SCRIPT_LOCK_CURRENT_POINTER_MISSING,
    SCRIPT_LOCK_EFFECTIVE,
)
from otio_app.discovery_v2.domain.supplementation import ScriptLockStatus
from otio_app.discovery_v2.persistence import narration_repository as narration_repo
from otio_app.discovery_v2.persistence.asset_registry_database import (
    get_registry_connection,
    read_schema_version,
)
from otio_app.discovery_v2.ui import editorial_page
from otio_app.discovery_v2.ui import narration_page
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


class _FakeContext:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeStreamlit:
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


def _app_state_snapshot(project) -> dict:
    return {
        "editorial_pointer": read_editorial_current_script_lock_id(project),
        "narration_pointer": read_narration_current_script_lock_id(project),
        "narration_state": (
            None
            if read_narration_state(project) is None
            else read_narration_state(project).model_dump(mode="json")
        ),
        "lock_statuses": {
            lock.lock_id: lock.status.value
            for lock in list_project_script_locks(project)
        },
    }


def _build_effective_lock_with_stale_narration_pointer(tmp_path, temp_db_path):
    """Lock A + voice → Lock B effective; narration pointer remains on A."""

    project = _script_coverage_project(tmp_path, temp_db_path)
    risk_key = _accept_one_gap_unresolved(project)
    _decide_all_claims(project)
    lock_a = _create_lock(
        project,
        accepted_unresolved_risk_confirmations={risk_key: True},
    )
    voice = start_voice_generation_run(project, sync=True)
    assert voice.started
    assert read_narration_current_script_lock_id(project) == lock_a.lock_id

    # New fachlicher Stand under same script lineage: resolve gaps again if needed
    # and create a second lock after a claim-compatible re-preview.
    # Use a second accepted-risk stand by rotating coverage via local resolve path
    # when preview still ready — create_script_lock supersedes Lock A.
    preview = preview_script_lock(project)
    # Same stand would conflict on fingerprint — force a new lockable stand by
    # accepting nothing extra: supersede via create after mutating risk set.
    # Practical path: clear pointer is not enough. Advance by creating lock on
    # identical stand is blocked. Use save_user_script_edit invalidation path
    # then rebuild lock-ready Script under new structure.
    from otio_app.discovery_v2.application.editorial_service import (
        save_user_script_edit,
        start_coverage_run,
        start_structure_run,
    )

    view = get_editorial_view(project)
    assert view.script is not None
    edited = save_user_script_edit(
        project,
        full_text=view.script.full_text + " L3 second lock sentence.",
    )
    assert edited.ok
    # Invalidate Lock A via get_effective (pointer cleared); narration stays on A.
    from otio_app.discovery_v2.application.script_lock_service import (
        get_effective_script_lock,
    )

    assert get_effective_script_lock(project).ok is False
    assert start_structure_run(project, sync=True).started
    # Promote structure like Fixture A helper when FakeText leaves pending.
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
    lock_b = _create_lock(project)
    assert read_editorial_current_script_lock_id(project) == lock_b.lock_id
    assert read_narration_current_script_lock_id(project) == lock_a.lock_id
    return project, lock_a, lock_b, voice.run


def test_l3_editorial_does_not_present_historical_lock_as_current(
    tmp_path: Path, temp_db_path: Path, monkeypatch
) -> None:
    fx = build_fixture_a_usa_v2_deadlock(
        tmp_path, temp_db_path, with_pause_and_timeline=False
    )
    fake_st = _render_editorial_script_lock(fx.project, monkeypatch)
    joined = "\n".join(fake_st.messages)
    assert "Kein wirksamer Script Lock vorhanden." in joined
    assert not any(m.startswith("Aktueller Lock:") for m in fake_st.messages)
    assert "Aktueller wirksamer Script Lock" not in joined


def test_l3_editorial_lists_historical_locks_separately(
    tmp_path: Path, temp_db_path: Path, monkeypatch
) -> None:
    fx = build_fixture_a_usa_v2_deadlock(
        tmp_path, temp_db_path, with_pause_and_timeline=False
    )
    fake_st = _render_editorial_script_lock(fx.project, monkeypatch)
    joined = "\n".join(fake_st.messages)
    assert "Historische Script Locks" in joined
    assert any(
        m.startswith("Historischer Lock:") and fx.lock_a.lock_id in m
        for m in fake_st.messages
    )


def test_l3_historical_lock_does_not_disable_new_lock_button(
    tmp_path: Path, temp_db_path: Path, monkeypatch
) -> None:
    fx = build_fixture_a_usa_v2_deadlock(
        tmp_path, temp_db_path, with_pause_and_timeline=False
    )
    gate = resolve_editorial_script_lock_gate(
        fx.project,
        user_confirmed=True,
        risk_confirmations={key: True for key in (
            resolve_editorial_script_lock_gate(fx.project).required_risk_keys
        )},
    )
    assert gate.has_effective_current_lock is False
    assert gate.historical_locks
    assert gate.can_create_lock is True
    fake_st = _render_editorial_script_lock(
        fx.project, monkeypatch, checkbox_value=True
    )
    lock_buttons = [
        call
        for call in fake_st.button_calls
        if call["label"] == "Skript fuer Voice und Timing sperren"
    ]
    assert lock_buttons
    assert lock_buttons[0].get("disabled") is False


def test_l3_new_lock_button_enabled_when_current_preview_and_confirmations_are_complete(
    tmp_path: Path, temp_db_path: Path, monkeypatch
) -> None:
    fx = build_fixture_a_usa_v2_deadlock(
        tmp_path, temp_db_path, with_pause_and_timeline=False
    )
    fake_st = _render_editorial_script_lock(
        fx.project, monkeypatch, checkbox_value=True
    )
    lock_buttons = [
        call
        for call in fake_st.button_calls
        if call["label"] == "Skript fuer Voice und Timing sperren"
    ]
    assert lock_buttons
    assert lock_buttons[0].get("disabled") is False


def test_l3_new_lock_button_disabled_when_current_preview_is_unavailable(
    tmp_path: Path, temp_db_path: Path, monkeypatch
) -> None:
    project = _script_coverage_project(tmp_path, temp_db_path)
    # Gaps open → no fingerprint.
    gate = resolve_editorial_script_lock_gate(
        project, user_confirmed=True, risk_confirmations={}
    )
    assert gate.current_fingerprint is None
    assert gate.can_create_lock is False
    fake_st = _render_editorial_script_lock(
        project, monkeypatch, checkbox_value=True
    )
    joined = "\n".join(fake_st.messages)
    assert "Script-Lock-Fingerprint verfuegbar" in joined
    lock_buttons = [
        call
        for call in fake_st.button_calls
        if call["label"] == "Skript fuer Voice und Timing sperren"
    ]
    assert lock_buttons
    assert lock_buttons[0].get("disabled") is True


def test_l3_new_lock_button_disabled_when_current_risk_confirmation_is_missing(
    tmp_path: Path, temp_db_path: Path, monkeypatch
) -> None:
    project = _script_coverage_project(tmp_path, temp_db_path)
    risk_key = _accept_one_gap_unresolved(project)
    _decide_all_claims(project)
    gate = resolve_editorial_script_lock_gate(
        project,
        user_confirmed=True,
        risk_confirmations={},
    )
    assert risk_key in gate.required_risk_keys
    assert gate.current_fingerprint
    assert gate.can_create_lock is False
    fake_st = _render_editorial_script_lock(
        project, monkeypatch, checkbox_value=False
    )
    lock_buttons = [
        call
        for call in fake_st.button_calls
        if call["label"] == "Skript fuer Voice und Timing sperren"
    ]
    assert lock_buttons
    assert lock_buttons[0].get("disabled") is True


def test_l3_effective_current_lock_is_displayed_as_current(
    tmp_path: Path, temp_db_path: Path, monkeypatch
) -> None:
    project, lock = build_lock_ready_matching_project(tmp_path, temp_db_path)
    fake_st = _render_editorial_script_lock(project, monkeypatch)
    joined = "\n".join(fake_st.messages)
    assert "Aktueller wirksamer Script Lock" in joined
    assert lock.lock_id in joined
    assert "Kein wirksamer Script Lock vorhanden." not in joined


def test_l3_effective_current_lock_disables_duplicate_lock_creation(
    tmp_path: Path, temp_db_path: Path, monkeypatch
) -> None:
    project, lock = build_lock_ready_matching_project(tmp_path, temp_db_path)
    gate = resolve_editorial_script_lock_gate(
        project,
        user_confirmed=True,
        risk_confirmations={
            key: True
            for key in resolve_editorial_script_lock_gate(project).required_risk_keys
        },
    )
    assert gate.has_effective_current_lock is True
    assert gate.effective_lock.lock_id == lock.lock_id
    assert gate.can_create_lock is False
    fake_st = _render_editorial_script_lock(
        project, monkeypatch, checkbox_value=True
    )
    lock_buttons = [
        call
        for call in fake_st.button_calls
        if call["label"] == "Skript fuer Voice und Timing sperren"
    ]
    assert lock_buttons
    assert lock_buttons[0].get("disabled") is True


def test_l3_current_preview_and_effective_lock_fingerprints_are_labeled_separately(
    tmp_path: Path, temp_db_path: Path, monkeypatch
) -> None:
    project, lock = build_lock_ready_matching_project(tmp_path, temp_db_path)
    fake_st = _render_editorial_script_lock(project, monkeypatch)
    joined = "\n".join(fake_st.messages)
    assert "Current Preview Fingerprint" in joined
    assert "Effective Lock Fingerprint" in joined
    assert lock.lock_fingerprint[:12] in joined


def test_l3_editorial_ui_has_no_historical_current_fingerprint_contradiction(
    tmp_path: Path, temp_db_path: Path, monkeypatch
) -> None:
    fx = build_fixture_a_usa_v2_deadlock(
        tmp_path, temp_db_path, with_pause_and_timeline=False
    )
    fake_st = _render_editorial_script_lock(fx.project, monkeypatch)
    joined = "\n".join(fake_st.messages)
    assert "Aktueller Lock:" not in joined
    assert "Aktueller Lock-Stand" not in joined
    assert "Current Preview Fingerprint" in joined
    assert fx.preview_fingerprint_b[:12] in joined
    # Historical fingerprint only under Historie, never as Current.
    hist_lines = [m for m in fake_st.messages if m.startswith("Historischer Lock:")]
    assert hist_lines
    assert fx.lock_fingerprint_a[:12] in hist_lines[0]


def test_l3_narration_uses_strict_effective_lock_resolution(
    tmp_path: Path, temp_db_path: Path, monkeypatch
) -> None:
    fx = build_fixture_a_usa_v2_deadlock(
        tmp_path, temp_db_path, with_pause_and_timeline=False
    )
    resolution = resolve_effective_current_script_lock(fx.project)
    gate = resolve_narration_gate_state(fx.project)
    view = get_narration_view(fx.project)
    assert resolution.is_effective is False
    assert resolution.reason_code == SCRIPT_LOCK_CURRENT_POINTER_MISSING
    assert gate.effective_script_lock_id is None
    assert view.effective_lock is None
    fake_st = _render_narration_page(fx.project, monkeypatch)
    assert any("Kein wirksamer Script Lock vorhanden" in m for m in fake_st.messages)


def test_l3_stale_narration_pointer_does_not_unlock_voice(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project, lock_a, lock_b, _voice = _build_effective_lock_with_stale_narration_pointer(
        tmp_path, temp_db_path
    )
    gate = resolve_narration_gate_state(project)
    assert gate.effective_script_lock_id == lock_b.lock_id
    assert gate.narration_pointer_state == NARRATION_POINTER_STALE
    assert gate.narration_current_script_lock_id == lock_a.lock_id
    assert gate.can_start_voice is False
    assert NARRATION_SCRIPT_LOCK_STALE in gate.blocking_reason_codes
    calls_before = fake_voice_call_count()
    blocked = start_voice_generation_run(project, sync=True)
    assert not blocked.started
    assert blocked.error_code == NARRATION_SCRIPT_LOCK_STALE
    assert fake_voice_call_count() == calls_before


def test_l3_missing_narration_pointer_with_effective_lock_allows_voice_gate(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project, lock = build_lock_ready_matching_project(tmp_path, temp_db_path)
    assert read_narration_current_script_lock_id(project) is None
    gate = resolve_narration_gate_state(project)
    assert gate.effective_script_lock_id == lock.lock_id
    assert gate.narration_pointer_state == NARRATION_POINTER_MISSING
    assert gate.can_start_voice is True


def test_l3_matching_narration_pointer_with_effective_lock_allows_voice_gate(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project, lock = build_lock_ready_matching_project(tmp_path, temp_db_path)
    # Matching pointer without requiring a completed voice run.
    conn = narration_repo.open_narration_registry(project.project_root_path)
    try:
        state = narration_repo.get_project_state(conn, project_id=project.id)
        from datetime import datetime, timezone

        from otio_app.discovery_v2.domain.narration import NarrationProjectState

        now = datetime.now(timezone.utc)
        if state is None:
            state = NarrationProjectState(project_id=project.id, updated_at=now)
        narration_repo.upsert_project_state(
            conn,
            state.model_copy(
                update={
                    "current_script_lock_id": lock.lock_id,
                    "updated_at": now,
                }
            ),
        )
        conn.commit()
    finally:
        conn.close()
    gate = resolve_narration_gate_state(project)
    assert gate.narration_pointer_state == NARRATION_POINTER_MATCHING
    assert gate.can_start_voice is True


def test_l3_no_effective_lock_blocks_voice_pause_and_timing(
    tmp_path: Path, temp_db_path: Path, monkeypatch
) -> None:
    fx = build_fixture_a_usa_v2_deadlock(
        tmp_path, temp_db_path, with_pause_and_timeline=True
    )
    gate = resolve_narration_gate_state(fx.project)
    assert gate.can_start_voice is False
    assert gate.can_start_pause_direction is False
    assert gate.can_resolve_timing is False
    assert NARRATION_SCRIPT_LOCK_MISSING in gate.blocking_reason_codes
    view = get_narration_view(fx.project)
    assert view.can_start_voice is False
    assert view.can_start_pause is False
    assert view.can_resolve_timing is False
    fake_st = _render_narration_page(fx.project, monkeypatch)
    voice_btn = [
        c for c in fake_st.button_calls if c["label"] == "Voice erzeugen"
    ][0]
    pause_btn = [
        c for c in fake_st.button_calls if c["label"] == "Pausenregie erzeugen"
    ][0]
    timing_btn = [
        c for c in fake_st.button_calls if c["label"] == "Narration Timing aufloesen"
    ][0]
    assert voice_btn.get("disabled") is True
    assert pause_btn.get("disabled") is True
    assert timing_btn.get("disabled") is True


def test_l3_historical_voice_run_is_not_current_for_new_effective_lock(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project, lock_a, lock_b, voice_run = (
        _build_effective_lock_with_stale_narration_pointer(tmp_path, temp_db_path)
    )
    # Clear stale narration pointer so Voice gate allows, but voice artifact stays old.
    conn = narration_repo.open_narration_registry(project.project_root_path)
    try:
        state = narration_repo.get_project_state(conn, project_id=project.id)
        assert state is not None
        from datetime import datetime, timezone

        narration_repo.upsert_project_state(
            conn,
            state.model_copy(
                update={
                    "current_script_lock_id": None,
                    "updated_at": datetime.now(timezone.utc),
                }
            ),
        )
        conn.commit()
    finally:
        conn.close()
    gate = resolve_narration_gate_state(project)
    assert gate.effective_script_lock_id == lock_b.lock_id
    assert gate.current_voice_run is None
    assert NARRATION_VOICE_NOT_CURRENT in gate.diagnostics or (
        NARRATION_VOICE_NOT_CURRENT in gate.blocking_reason_codes
    )
    narr = read_narration_state(project)
    assert narr is not None
    assert narr.current_voice_run_id == voice_run.run_id
    assert voice_run.script_lock_id == lock_a.lock_id


def test_l3_historical_pause_plan_is_not_current_for_new_effective_lock(
    tmp_path: Path, temp_db_path: Path
) -> None:
    fx = build_fixture_a_usa_v2_deadlock(
        tmp_path, temp_db_path, with_pause_and_timeline=True
    )
    # Create Lock B under Script B (preview ready) while pause/timeline stay on A.
    risks = {
        key: True
        for key in resolve_editorial_script_lock_gate(fx.project).required_risk_keys
    }
    preview = preview_script_lock(fx.project)
    created = create_script_lock(
        fx.project,
        user_confirmed=True,
        confirmed_fingerprint=preview.lock_fingerprint,
        accepted_unresolved_risk_confirmations=risks,
    )
    assert created.ok and created.lock is not None
    gate = resolve_narration_gate_state(fx.project)
    assert gate.effective_script_lock_id == created.lock.lock_id
    assert gate.current_pause_plan is None
    assert gate.can_start_pause_direction is False


def test_l3_historical_timeline_is_not_current_for_new_effective_lock(
    tmp_path: Path, temp_db_path: Path
) -> None:
    fx = build_fixture_a_usa_v2_deadlock(
        tmp_path, temp_db_path, with_pause_and_timeline=True
    )
    risks = {
        key: True
        for key in resolve_editorial_script_lock_gate(fx.project).required_risk_keys
    }
    preview = preview_script_lock(fx.project)
    created = create_script_lock(
        fx.project,
        user_confirmed=True,
        confirmed_fingerprint=preview.lock_fingerprint,
        accepted_unresolved_risk_confirmations=risks,
    )
    assert created.ok
    gate = resolve_narration_gate_state(fx.project)
    assert gate.current_narration_timeline is None
    assert gate.can_resolve_timing is False


def test_l3_pause_requires_voice_for_same_effective_lock(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project, lock_a, lock_b, voice_run = (
        _build_effective_lock_with_stale_narration_pointer(tmp_path, temp_db_path)
    )
    # Point narration lock to B but leave voice run from A as current_voice_run_id.
    conn = narration_repo.open_narration_registry(project.project_root_path)
    try:
        state = narration_repo.get_project_state(conn, project_id=project.id)
        assert state is not None
        from datetime import datetime, timezone

        narration_repo.upsert_project_state(
            conn,
            state.model_copy(
                update={
                    "current_script_lock_id": lock_b.lock_id,
                    "current_voice_run_id": voice_run.run_id,
                    "updated_at": datetime.now(timezone.utc),
                }
            ),
        )
        conn.commit()
    finally:
        conn.close()
    gate = resolve_narration_gate_state(project)
    assert gate.can_start_voice is True  # matching pointer
    assert gate.current_voice_run is None
    assert gate.can_start_pause_direction is False
    assert NARRATION_VOICE_NOT_CURRENT in gate.blocking_reason_codes


def test_l3_timing_requires_voice_and_pause_for_same_effective_lock(
    tmp_path: Path, temp_db_path: Path
) -> None:
    fx = build_fixture_a_usa_v2_deadlock(
        tmp_path, temp_db_path, with_pause_and_timeline=True
    )
    risks = {
        key: True
        for key in resolve_editorial_script_lock_gate(fx.project).required_risk_keys
    }
    preview = preview_script_lock(fx.project)
    created = create_script_lock(
        fx.project,
        user_confirmed=True,
        confirmed_fingerprint=preview.lock_fingerprint,
        accepted_unresolved_risk_confirmations=risks,
    )
    assert created.ok
    # Matching pointer on new lock; historical pause/voice remain.
    conn = narration_repo.open_narration_registry(fx.project.project_root_path)
    try:
        state = narration_repo.get_project_state(conn, project_id=fx.project.id)
        assert state is not None
        from datetime import datetime, timezone

        narration_repo.upsert_project_state(
            conn,
            state.model_copy(
                update={
                    "current_script_lock_id": created.lock.lock_id,
                    "updated_at": datetime.now(timezone.utc),
                }
            ),
        )
        conn.commit()
    finally:
        conn.close()
    gate = resolve_narration_gate_state(fx.project)
    assert gate.can_resolve_timing is False
    assert gate.current_voice_run is None
    assert gate.current_pause_plan is None
    assert NARRATION_VOICE_NOT_CURRENT in gate.blocking_reason_codes
    assert NARRATION_PAUSE_PLAN_NOT_CURRENT in gate.blocking_reason_codes


def test_l3_gate_resolution_is_read_only(
    tmp_path: Path, temp_db_path: Path
) -> None:
    fx = build_fixture_a_usa_v2_deadlock(
        tmp_path, temp_db_path, with_pause_and_timeline=False
    )
    before = _app_state_snapshot(fx.project)
    resolve_editorial_script_lock_gate(fx.project, user_confirmed=True)
    resolve_narration_gate_state(fx.project)
    resolve_effective_current_script_lock(fx.project)
    after = _app_state_snapshot(fx.project)
    assert before == after
    assert (
        read_script_lock(fx.project, lock_id=fx.lock_a.lock_id).status
        == ScriptLockStatus.LOCKED
    )


def test_l3_ui_render_does_not_mutate_current_pointers(
    tmp_path: Path, temp_db_path: Path, monkeypatch
) -> None:
    fx = build_fixture_a_usa_v2_deadlock(
        tmp_path, temp_db_path, with_pause_and_timeline=False
    )
    before = _app_state_snapshot(fx.project)
    _render_editorial_script_lock(fx.project, monkeypatch, checkbox_value=True)
    _render_narration_page(fx.project, monkeypatch)
    after = _app_state_snapshot(fx.project)
    assert before == after


def test_l3_gate_resolution_calls_no_gateway_and_reads_no_media(
    tmp_path: Path, temp_db_path: Path, monkeypatch
) -> None:
    fx = build_fixture_a_usa_v2_deadlock(
        tmp_path, temp_db_path, with_pause_and_timeline=False
    )
    install_no_media_io_guards(monkeypatch)
    calls_before = fake_voice_call_count()
    resolve_editorial_script_lock_gate(fx.project, user_confirmed=True)
    resolve_narration_gate_state(fx.project)
    assert fake_voice_call_count() == calls_before


def test_l3_schema20_classic_without_vo_isolation(
    tmp_path: Path, temp_db_path: Path
) -> None:
    fx = build_fixture_a_usa_v2_deadlock(
        tmp_path, temp_db_path, with_pause_and_timeline=False
    )
    assert_schema_20(fx.project)
    assert REGISTRY_SCHEMA_VERSION == "20"
    conn = get_registry_connection(fx.project.project_root_path)
    try:
        assert read_schema_version(conn) == "20"
    finally:
        conn.close()
    # Gate path does not touch Classic / Without-VO modules.
    import otio_app.discovery_v2.application.editorial_script_lock_gate_service as eg
    import otio_app.discovery_v2.application.narration_gate_service as ng

    source = (Path(eg.__file__).read_text() + Path(ng.__file__).read_text()).lower()
    assert "without_vo" not in source
    assert "classic_migration" not in source


# --- Pflicht-Smokes A–F ---


def test_l3_smoke_a_historical_lock_preview_ready_enables_new_lock(
    tmp_path: Path, temp_db_path: Path, monkeypatch
) -> None:
    fx = build_fixture_a_usa_v2_deadlock(
        tmp_path, temp_db_path, with_pause_and_timeline=False
    )
    fake_st = _render_editorial_script_lock(
        fx.project, monkeypatch, checkbox_value=True
    )
    joined = "\n".join(fake_st.messages)
    assert "Kein wirksamer Script Lock vorhanden." in joined
    assert "Historische Script Locks" in joined
    lock_buttons = [
        c
        for c in fake_st.button_calls
        if c["label"] == "Skript fuer Voice und Timing sperren"
    ]
    assert lock_buttons and lock_buttons[0].get("disabled") is False


def test_l3_smoke_b_valid_effective_lock_shown_no_duplicate(
    tmp_path: Path, temp_db_path: Path, monkeypatch
) -> None:
    project, lock = build_lock_ready_matching_project(tmp_path, temp_db_path)
    fake_st = _render_editorial_script_lock(
        project, monkeypatch, checkbox_value=True
    )
    joined = "\n".join(fake_st.messages)
    assert "Aktueller wirksamer Script Lock" in joined
    assert lock.lock_id in joined
    lock_buttons = [
        c
        for c in fake_st.button_calls
        if c["label"] == "Skript fuer Voice und Timing sperren"
    ]
    assert lock_buttons and lock_buttons[0].get("disabled") is True


def test_l3_smoke_c_no_effective_lock_stale_narration_blocks_all(
    tmp_path: Path, temp_db_path: Path, monkeypatch
) -> None:
    fx = build_fixture_a_usa_v2_deadlock(
        tmp_path, temp_db_path, with_pause_and_timeline=True
    )
    gate = resolve_narration_gate_state(fx.project)
    assert gate.narration_pointer_state == NARRATION_POINTER_STALE
    assert gate.can_start_voice is False
    assert gate.can_start_pause_direction is False
    assert gate.can_resolve_timing is False
    fake_st = _render_narration_page(fx.project, monkeypatch)
    assert all(
        c.get("disabled") is True
        for c in fake_st.button_calls
        if c["label"]
        in {
            "Voice erzeugen",
            "Pausenregie erzeugen",
            "Narration Timing aufloesen",
        }
    )


def test_l3_smoke_d_effective_lock_missing_narration_pointer_allows_voice(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project, lock = build_lock_ready_matching_project(tmp_path, temp_db_path)
    gate = resolve_narration_gate_state(project)
    assert gate.effective_script_lock_id == lock.lock_id
    assert gate.narration_pointer_state == NARRATION_POINTER_MISSING
    assert gate.can_start_voice is True


def test_l3_smoke_e_new_effective_lock_old_voice_blocks_pause(
    tmp_path: Path, temp_db_path: Path
) -> None:
    fx = build_fixture_a_usa_v2_deadlock(
        tmp_path, temp_db_path, with_pause_and_timeline=False
    )
    risks = {
        key: True
        for key in resolve_editorial_script_lock_gate(fx.project).required_risk_keys
    }
    preview = preview_script_lock(fx.project)
    created = create_script_lock(
        fx.project,
        user_confirmed=True,
        confirmed_fingerprint=preview.lock_fingerprint,
        accepted_unresolved_risk_confirmations=risks,
    )
    assert created.ok
    gate = resolve_narration_gate_state(fx.project)
    assert gate.effective_script_lock_id == created.lock.lock_id
    assert gate.current_voice_run is None
    assert gate.can_start_pause_direction is False


def test_l3_smoke_f_editorial_and_narration_render_read_only(
    tmp_path: Path, temp_db_path: Path, monkeypatch
) -> None:
    fx = build_fixture_a_usa_v2_deadlock(
        tmp_path, temp_db_path, with_pause_and_timeline=False
    )
    before = _app_state_snapshot(fx.project)
    _render_editorial_script_lock(fx.project, monkeypatch, checkbox_value=True)
    _render_narration_page(fx.project, monkeypatch)
    after = _app_state_snapshot(fx.project)
    assert before == after
