"""Active-script pointer recovery for active_script_pointer_missing."""

from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fixtures.coverage_stability_c1 import assert_schema_20, install_no_media_io_guards
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
from otio_app.discovery_v2.application.active_script_recovery_service import (
    diagnose_active_script_recovery,
    recover_active_script_current_state,
)
from otio_app.discovery_v2.application.editorial_service import (
    get_editorial_view,
    start_structure_run,
)
from otio_app.discovery_v2.application.script_lock_service import (
    build_current_script_lock_preview,
)
from otio_app.discovery_v2.domain.asset_registry import REGISTRY_SCHEMA_VERSION
from otio_app.discovery_v2.domain.editorial import (
    EDITORIAL_ERROR_ACTIVE_SCRIPT_POINTER_MISSING,
    EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERED,
    EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERY_AMBIGUOUS,
    EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERY_CONFIRMATION_REQUIRED,
    EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERY_COVERAGE_MISMATCH,
    EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERY_IDENTITY_MISMATCH,
    ScriptDraftStatus,
)
from otio_app.discovery_v2.editorial_paths import (
    editorial_script_json_relative_path,
    resolve_editorial_relative_path,
)
from otio_app.discovery_v2.persistence import editorial_repository as editorial_repo
from otio_app.discovery_v2.persistence import narration_repository as narration_repo
from otio_app.discovery_v2.persistence.asset_registry_database import (
    get_registry_connection,
    read_schema_version,
)
from otio_app.discovery_v2.ui import editorial_page
from test_discovery_v2_structure_finalization import _pending_structured_project


@pytest.fixture(autouse=True)
def _reset_state() -> None:
    reset_analysis_job_launcher_for_tests()
    reset_editorial_job_launcher_for_tests()
    reset_narration_job_launcher_for_tests()
    reset_supplementation_job_launcher_for_tests()
    reset_fake_text_test_hook()
    yield
    reset_fake_text_test_hook()
    reset_supplementation_job_launcher_for_tests()
    reset_narration_job_launcher_for_tests()
    reset_editorial_job_launcher_for_tests()
    reset_analysis_job_launcher_for_tests()


def _clear_active_script_pointer(project) -> str:
    conn = editorial_repo.open_editorial_registry(project.project_root_path)
    try:
        state = editorial_repo.get_project_state(conn, project_id=project.id)
        assert state is not None and state.active_script_id
        script_id = state.active_script_id
        editorial_repo.upsert_project_state(
            conn, state.model_copy(update={"active_script_id": None})
        )
        conn.commit()
        return script_id
    finally:
        conn.close()


def _script_row(project, *, script_id: str):
    conn = editorial_repo.open_editorial_registry(project.project_root_path)
    try:
        return conn.execute(
            "SELECT * FROM script_drafts WHERE script_id = ?",
            (script_id,),
        ).fetchone()
    finally:
        conn.close()


def test_recovery_diagnosis_finds_single_verified_script_candidate(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _pending_structured_project(tmp_path, temp_db_path)
    script_id = _clear_active_script_pointer(project)
    diagnosis = diagnose_active_script_recovery(project)
    assert diagnosis.pointer_missing is True
    assert diagnosis.ok is True
    assert diagnosis.candidate is not None
    assert diagnosis.candidate.script_id == script_id
    assert diagnosis.candidate.script_version == 2
    preview = build_current_script_lock_preview(project)
    assert EDITORIAL_ERROR_ACTIVE_SCRIPT_POINTER_MISSING in preview.blockers


def test_recovery_diagnosis_rejects_multiple_candidates(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _pending_structured_project(tmp_path, temp_db_path)
    script_id = _clear_active_script_pointer(project)
    conn = editorial_repo.open_editorial_registry(project.project_root_path)
    try:
        # Reactivate superseded v1 so two active-status rows exist.
        conn.execute(
            """
            UPDATE script_drafts
            SET status = 'review_requested'
            WHERE project_id = ? AND script_id != ?
            """,
            (project.id, script_id),
        )
        conn.commit()
    finally:
        conn.close()
    diagnosis = diagnose_active_script_recovery(project)
    assert diagnosis.ok is False
    assert EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERY_AMBIGUOUS in diagnosis.blockers


def test_recovery_diagnosis_rejects_script_json_database_mismatch(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _pending_structured_project(tmp_path, temp_db_path)
    script_id = _clear_active_script_pointer(project)
    path = resolve_editorial_relative_path(
        project.project_root_path, editorial_script_json_relative_path(script_id)
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["script"]["content_sha256"] = "0" * 64
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    diagnosis = diagnose_active_script_recovery(project)
    assert diagnosis.ok is False
    assert (
        EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERY_IDENTITY_MISMATCH in diagnosis.blockers
    )


def test_recovery_diagnosis_rejects_narrative_hook_mismatch(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _pending_structured_project(tmp_path, temp_db_path)
    script_id = _clear_active_script_pointer(project)
    # Break JSON/DB hook identity without violating SQLite FKs.
    path = resolve_editorial_relative_path(
        project.project_root_path, editorial_script_json_relative_path(script_id)
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["script"]["selected_hook_id"] = "hook-does-not-match-db"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    diagnosis = diagnose_active_script_recovery(project)
    assert diagnosis.ok is False
    assert (
        EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERY_IDENTITY_MISMATCH in diagnosis.blockers
    )


def test_recovery_diagnosis_rejects_coverage_script_mismatch(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _pending_structured_project(tmp_path, temp_db_path)
    assert start_structure_run(project, sync=True).started
    from otio_app.discovery_v2.application.editorial_service import start_coverage_run

    assert start_coverage_run(project, sync=True).started
    script_id = _clear_active_script_pointer(project)
    conn = editorial_repo.open_editorial_registry(project.project_root_path)
    try:
        state = editorial_repo.get_project_state(conn, project_id=project.id)
        assert state is not None and state.active_coverage_audit_id
        # Keep script_id FK-valid but force a version mismatch.
        conn.execute(
            """
            UPDATE coverage_audits
            SET script_version = 99
            WHERE coverage_audit_id = ?
            """,
            (state.active_coverage_audit_id,),
        )
        conn.commit()
    finally:
        conn.close()
    diagnosis = diagnose_active_script_recovery(project)
    assert diagnosis.ok is False
    assert (
        EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERY_COVERAGE_MISMATCH in diagnosis.blockers
    )
    assert script_id  # still present, but not coverage-bound


def test_recovery_requires_explicit_user_confirmation(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _pending_structured_project(tmp_path, temp_db_path)
    script_id = _clear_active_script_pointer(project)
    result = recover_active_script_current_state(
        project, script_id=script_id, user_confirmed=False
    )
    assert result.ok is False
    assert result.error_code == (
        EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERY_CONFIRMATION_REQUIRED
    )
    conn = editorial_repo.open_editorial_registry(project.project_root_path)
    try:
        state = editorial_repo.get_project_state(conn, project_id=project.id)
        assert state is not None
        assert state.active_script_id is None
    finally:
        conn.close()


def test_recovery_atomically_sets_script_narrative_and_hook_pointers(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _pending_structured_project(tmp_path, temp_db_path)
    script_id = _clear_active_script_pointer(project)
    row = _script_row(project, script_id=script_id)
    assert row is not None
    # Scramble state narrative/hook to prove recovery restores from script.
    conn = editorial_repo.open_editorial_registry(project.project_root_path)
    try:
        state = editorial_repo.get_project_state(conn, project_id=project.id)
        assert state is not None
        editorial_repo.upsert_project_state(
            conn,
            state.model_copy(
                update={
                    "active_script_id": None,
                    "active_narrative_plan_id": "wrong-narrative",
                    "selected_hook_id": "wrong-hook",
                }
            ),
        )
        conn.commit()
    finally:
        conn.close()
    result = recover_active_script_current_state(
        project, script_id=script_id, user_confirmed=True
    )
    assert result.ok is True
    assert result.error_code == EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERED
    conn = editorial_repo.open_editorial_registry(project.project_root_path)
    try:
        state = editorial_repo.get_project_state(conn, project_id=project.id)
        assert state is not None
        assert state.active_script_id == script_id
        assert state.active_narrative_plan_id == row["narrative_plan_id"]
        assert state.selected_hook_id == row["selected_hook_id"]
    finally:
        conn.close()


def test_recovery_does_not_change_script_content_or_version(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _pending_structured_project(tmp_path, temp_db_path)
    script_id = _clear_active_script_pointer(project)
    before = _script_row(project, script_id=script_id)
    path = resolve_editorial_relative_path(
        project.project_root_path, editorial_script_json_relative_path(script_id)
    )
    before_text = path.read_text(encoding="utf-8")
    assert recover_active_script_current_state(
        project, script_id=script_id, user_confirmed=True
    ).ok
    after = _script_row(project, script_id=script_id)
    assert after["script_version"] == before["script_version"]
    assert after["content_sha256"] == before["content_sha256"]
    assert after["status"] == before["status"] == ScriptDraftStatus.STRUCTURE_PENDING.value
    assert path.read_text(encoding="utf-8") == before_text


def test_recovery_preserves_coverage_gaps_and_risk_confirmations(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _pending_structured_project(tmp_path, temp_db_path)
    assert start_structure_run(project, sync=True).started
    from otio_app.discovery_v2.application.editorial_service import start_coverage_run
    from fixtures.script_lock_current_state_l1 import _resolve_all_gaps_locally

    assert start_coverage_run(project, sync=True).started
    _resolve_all_gaps_locally(project)
    conn = editorial_repo.open_editorial_registry(project.project_root_path)
    try:
        state = editorial_repo.get_project_state(conn, project_id=project.id)
        assert state is not None
        audit_id = state.active_coverage_audit_id
        assert audit_id
        gaps_before = conn.execute(
            "SELECT gap_id, status, accepted_unresolved_risks_json FROM coverage_gaps WHERE project_id = ? ORDER BY gap_id",
            (project.id,),
        ).fetchall()
        results_before = conn.execute(
            "SELECT COUNT(*) AS n FROM coverage_intent_results WHERE coverage_audit_id = ?",
            (audit_id,),
        ).fetchone()["n"]
        script_id = state.active_script_id
        editorial_repo.upsert_project_state(
            conn, state.model_copy(update={"active_script_id": None})
        )
        conn.commit()
    finally:
        conn.close()
    assert recover_active_script_current_state(
        project, script_id=script_id, user_confirmed=True
    ).ok
    conn = editorial_repo.open_editorial_registry(project.project_root_path)
    try:
        state = editorial_repo.get_project_state(conn, project_id=project.id)
        assert state is not None
        assert state.active_coverage_audit_id == audit_id
        gaps_after = conn.execute(
            "SELECT gap_id, status, accepted_unresolved_risks_json FROM coverage_gaps WHERE project_id = ? ORDER BY gap_id",
            (project.id,),
        ).fetchall()
        results_after = conn.execute(
            "SELECT COUNT(*) AS n FROM coverage_intent_results WHERE coverage_audit_id = ?",
            (audit_id,),
        ).fetchone()["n"]
        assert [(r["gap_id"], r["status"], r["accepted_unresolved_risks_json"]) for r in gaps_after] == [
            (r["gap_id"], r["status"], r["accepted_unresolved_risks_json"]) for r in gaps_before
        ]
        assert results_after == results_before
    finally:
        conn.close()


def test_recovery_does_not_create_script_lock_or_narration_artifacts(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _pending_structured_project(tmp_path, temp_db_path)
    script_id = _clear_active_script_pointer(project)
    conn = editorial_repo.open_editorial_registry(project.project_root_path)
    try:
        locks_before = conn.execute("SELECT COUNT(*) AS n FROM script_locks").fetchone()["n"]
    finally:
        conn.close()
    assert recover_active_script_current_state(
        project, script_id=script_id, user_confirmed=True
    ).ok
    conn = editorial_repo.open_editorial_registry(project.project_root_path)
    try:
        locks_after = conn.execute("SELECT COUNT(*) AS n FROM script_locks").fetchone()["n"]
        assert locks_after == locks_before == 0
        state = editorial_repo.get_project_state(conn, project_id=project.id)
        assert state is not None
        assert state.current_script_lock_id is None
        narr = narration_repo.get_project_state(conn, project_id=project.id)
        if narr is not None:
            assert narr.current_script_lock_id is None
            assert narr.current_voice_run_id is None
    finally:
        conn.close()


def test_recovery_render_is_read_only_without_button_click(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _pending_structured_project(tmp_path, temp_db_path)
    script_id = _clear_active_script_pointer(project)

    class _NoClickSt:
        def __init__(self) -> None:
            self.session_state: dict = {}
            self.messages: list[str] = []

        def button(self, *args, **kwargs):
            return False

        def columns(self, n):
            return [self, self][:n]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def warning(self, text):
            self.messages.append(str(text))

        def info(self, text):
            self.messages.append(str(text))

        def success(self, *a, **k):
            return None

        def title(self, *a, **k):
            return None

        def write(self, *a, **k):
            return None

        def markdown(self, *a, **k):
            return None

        def caption(self, *a, **k):
            return None

        def text_area(self, *args, **kwargs):
            return kwargs.get("value", "")

        def text_input(self, *args, **kwargs):
            return kwargs.get("value", "")

        def number_input(self, *args, **kwargs):
            return kwargs.get("value", 0)

        def checkbox(self, *args, **kwargs):
            return False

        def dataframe(self, *args, **kwargs):
            return None

        def form(self, *args, **kwargs):
            return self

        def form_submit_button(self, *args, **kwargs):
            return False

        def container(self):
            return self

        def expander(self, *args, **kwargs):
            return self

        def code(self, *a, **k):
            return None

        def subheader(self, *a, **k):
            return None

        def rerun(self):
            return None

    fake = _NoClickSt()
    monkeypatch.setattr(editorial_page, "st", fake)
    monkeypatch.setattr(editorial_page, "active_discovery_project", lambda: project)
    called = {"recover": 0}

    def _guard(*args, **kwargs):
        called["recover"] += 1
        raise AssertionError("recovery must not run without button click")

    monkeypatch.setattr(editorial_page, "recover_active_script_current_state", _guard)
    editorial_page.render_discovery_editorial_page()
    assert called["recover"] == 0
    assert any("Aktives Script fehlt" in msg for msg in fake.messages)
    conn = editorial_repo.open_editorial_registry(project.project_root_path)
    try:
        state = editorial_repo.get_project_state(conn, project_id=project.id)
        assert state is not None
        assert state.active_script_id is None
        assert script_id
    finally:
        conn.close()


def test_recovered_state_allows_normal_structure_finalization_retry(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _pending_structured_project(tmp_path, temp_db_path)
    script_id = _clear_active_script_pointer(project)
    # Structure refuses while pointer missing.
    blocked = start_structure_run(project, sync=True)
    assert blocked.started is False
    assert blocked.error_code == EDITORIAL_ERROR_ACTIVE_SCRIPT_POINTER_MISSING
    assert recover_active_script_current_state(
        project, script_id=script_id, user_confirmed=True
    ).ok
    result = start_structure_run(project, sync=True)
    assert result.started is True
    assert result.message == "Struktur aktualisiert."
    view = get_editorial_view(project)
    assert view.script is not None
    assert view.script.script_id == script_id
    assert view.script.status == ScriptDraftStatus.REVIEW_REQUESTED


def test_schema20_fake_only_no_gateway_no_media_io(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _pending_structured_project(tmp_path, temp_db_path)
    script_id = _clear_active_script_pointer(project)
    install_no_media_io_guards(monkeypatch)
    assert_schema_20(project)
    assert REGISTRY_SCHEMA_VERSION == "20"
    assert recover_active_script_current_state(
        project, script_id=script_id, user_confirmed=True
    ).ok
    conn = get_registry_connection(project.project_root_path)
    try:
        assert read_schema_version(conn) == "20"
    finally:
        conn.close()


def test_classic_without_vo_isolation(tmp_path: Path, temp_db_path: Path) -> None:
    project = _pending_structured_project(tmp_path, temp_db_path)
    script_id = _clear_active_script_pointer(project)
    assert recover_active_script_current_state(
        project, script_id=script_id, user_confirmed=True
    ).ok
    for rel in (
        "otio_app/discovery_v2/application/active_script_recovery_service.py",
        "otio_app/discovery_v2/ui/editorial_page.py",
    ):
        source = Path(rel).read_text(encoding="utf-8").lower()
        assert "without_vo" not in source
        assert "classic_migration" not in source
    classic = Path(project.project_root_path) / "_otio"
    assert not classic.exists() or not any(classic.rglob("*"))
