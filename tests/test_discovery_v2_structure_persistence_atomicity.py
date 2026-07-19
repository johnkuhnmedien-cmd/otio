"""L5 structure persistence atomicity: registry commit before JSON publish."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fixtures.coverage_stability_c1 import assert_schema_20, install_no_media_io_guards
from fixtures.script_lock_current_state_l1 import (
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
from otio_app.discovery_v2.application.editorial_service import (
    get_editorial_view,
    start_coverage_run,
    start_structure_run,
)
from otio_app.discovery_v2.application.script_lock_service import (
    build_current_script_lock_preview,
    preview_script_lock,
)
from otio_app.discovery_v2.domain.asset_registry import REGISTRY_SCHEMA_VERSION
from otio_app.discovery_v2.domain.editorial import (
    EDITORIAL_ERROR_ACTIVE_SCRIPT_POINTER_MISSING,
    EDITORIAL_ERROR_REGISTRY_ARTIFACT_MISMATCH,
    EDITORIAL_ERROR_REGISTRY_WRITE_FAILED,
    EDITORIAL_ERROR_SCRIPT_IDENTITY_MISMATCH,
    EditorialRunStatus,
    ScriptDraftStatus,
)
from otio_app.discovery_v2.editorial_paths import (
    editorial_latest_script_relative_path,
    editorial_script_json_relative_path,
    resolve_editorial_relative_path,
)
from otio_app.discovery_v2.persistence import editorial_repository as editorial_repo
from otio_app.discovery_v2.persistence.asset_registry_database import (
    get_registry_connection,
    read_schema_version,
)
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


def _read_script_json_status(project, *, script_id: str) -> str:
    relative = editorial_script_json_relative_path(script_id)
    path = resolve_editorial_relative_path(project.project_root_path, relative)
    payload = json.loads(path.read_text(encoding="utf-8"))
    return str((payload.get("script") or {}).get("status") or "")


def _read_latest_script_status(project) -> str:
    relative = editorial_latest_script_relative_path()
    path = resolve_editorial_relative_path(project.project_root_path, relative)
    payload = json.loads(path.read_text(encoding="utf-8"))
    return str((payload.get("script") or {}).get("status") or "")


def _db_script_status(project, *, script_id: str) -> str:
    conn = editorial_repo.open_editorial_registry(project.project_root_path)
    try:
        row = conn.execute(
            "SELECT status FROM script_drafts WHERE script_id = ?",
            (script_id,),
        ).fetchone()
        assert row is not None
        return str(row["status"])
    finally:
        conn.close()


def _active_script_id(project) -> str | None:
    conn = editorial_repo.open_editorial_registry(project.project_root_path)
    try:
        state = editorial_repo.get_project_state(conn, project_id=project.id)
        return None if state is None else state.active_script_id
    finally:
        conn.close()


def _force_json_review_requested_while_db_pending(project, *, script_id: str) -> None:
    """Reproduce USA_v2 divergence: JSON review_requested, DB structure_pending."""

    conn = editorial_repo.open_editorial_registry(project.project_root_path)
    try:
        bundle = editorial_repo.get_script_bundle(conn, script_id=script_id)
        assert bundle is not None
        script = dict(bundle["script"])
        script["status"] = ScriptDraftStatus.REVIEW_REQUESTED.value
        published = {**bundle, "script": script}
        for relative in (
            editorial_script_json_relative_path(script_id),
            editorial_latest_script_relative_path(),
        ):
            path = resolve_editorial_relative_path(project.project_root_path, relative)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(published, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        row = conn.execute(
            "SELECT status FROM script_drafts WHERE script_id = ?",
            (script_id,),
        ).fetchone()
        assert str(row["status"]) == ScriptDraftStatus.STRUCTURE_PENDING.value
    finally:
        conn.close()


def test_structure_registry_failure_does_not_publish_review_requested_json(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _pending_structured_project(tmp_path, temp_db_path)
    script_id = _active_script_id(project)
    assert script_id
    assert _read_script_json_status(project, script_id=script_id) == (
        ScriptDraftStatus.STRUCTURE_PENDING.value
    )

    def _boom(*args, **kwargs):
        raise sqlite3.IntegrityError("FOREIGN KEY constraint failed")

    monkeypatch.setattr(editorial_repo, "replace_script_structure", _boom)
    result = start_structure_run(project, sync=True)
    assert result.started is False
    assert result.error_code == EDITORIAL_ERROR_REGISTRY_WRITE_FAILED
    assert "Struktur aktualisiert" not in (result.message or "")
    assert _db_script_status(project, script_id=script_id) == (
        ScriptDraftStatus.STRUCTURE_PENDING.value
    )
    assert _read_script_json_status(project, script_id=script_id) == (
        ScriptDraftStatus.STRUCTURE_PENDING.value
    )
    assert _read_latest_script_status(project) == ScriptDraftStatus.STRUCTURE_PENDING.value


def test_structure_registry_failure_keeps_database_and_json_consistent(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """JSON may be written after replace; post-publish failure must restore."""

    project = _pending_structured_project(tmp_path, temp_db_path)
    script_id = _active_script_id(project)
    assert script_id
    real_complete = editorial_repo.update_editorial_attempt

    def _selective(conn, attempt):
        status = getattr(attempt, "status", None)
        value = status.value if hasattr(status, "value") else str(status)
        if value == "completed":
            raise sqlite3.IntegrityError("FOREIGN KEY constraint failed")
        return real_complete(conn, attempt)

    monkeypatch.setattr(
        "otio_app.discovery_v2.jobs.editorial_worker.repo.update_editorial_attempt",
        _selective,
    )
    result = start_structure_run(project, sync=True)
    assert result.started is False
    assert result.error_code == EDITORIAL_ERROR_REGISTRY_WRITE_FAILED
    assert _db_script_status(project, script_id=script_id) == (
        ScriptDraftStatus.STRUCTURE_PENDING.value
    )
    assert _read_script_json_status(project, script_id=script_id) == (
        ScriptDraftStatus.STRUCTURE_PENDING.value
    )
    assert _read_latest_script_status(project) == ScriptDraftStatus.STRUCTURE_PENDING.value


def test_structure_foreign_key_failure_is_reported_not_shown_as_success(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _pending_structured_project(tmp_path, temp_db_path)

    def _boom(*args, **kwargs):
        raise sqlite3.IntegrityError("FOREIGN KEY constraint failed")

    monkeypatch.setattr(editorial_repo, "replace_script_structure", _boom)
    result = start_structure_run(project, sync=True)
    assert result.started is False
    assert result.error_code == EDITORIAL_ERROR_REGISTRY_WRITE_FAILED
    assert "FOREIGN KEY" in (result.message or "") or result.error_code == (
        EDITORIAL_ERROR_REGISTRY_WRITE_FAILED
    )
    assert result.message != "Struktur aktualisiert."
    assert result.run is not None
    assert result.run.status == EditorialRunStatus.FAILED


def test_preview_rejects_json_review_requested_when_database_is_structure_pending(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _pending_structured_project(tmp_path, temp_db_path)
    script_id = _active_script_id(project)
    assert script_id
    _force_json_review_requested_while_db_pending(project, script_id=script_id)
    preview = build_current_script_lock_preview(project)
    assert preview.ok is False
    assert preview.lock_fingerprint in (None, "")
    assert EDITORIAL_ERROR_REGISTRY_ARTIFACT_MISMATCH in preview.blockers
    assert "script_structure_pending" in preview.blockers


def test_preview_rejects_missing_active_script_pointer(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _pending_structured_project(tmp_path, temp_db_path)
    conn = editorial_repo.open_editorial_registry(project.project_root_path)
    try:
        state = editorial_repo.get_project_state(conn, project_id=project.id)
        assert state is not None
        editorial_repo.upsert_project_state(
            conn,
            state.model_copy(update={"active_script_id": None}),
        )
        conn.commit()
    finally:
        conn.close()
    preview = build_current_script_lock_preview(project)
    assert preview.ok is False
    assert preview.lock_fingerprint in (None, "")
    assert EDITORIAL_ERROR_ACTIVE_SCRIPT_POINTER_MISSING in preview.blockers


def test_preview_rejects_narrative_plan_identity_mismatch(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _pending_structured_project(tmp_path, temp_db_path)
    assert start_structure_run(project, sync=True).started
    conn = editorial_repo.open_editorial_registry(project.project_root_path)
    try:
        state = editorial_repo.get_project_state(conn, project_id=project.id)
        assert state is not None
        editorial_repo.upsert_project_state(
            conn,
            state.model_copy(
                update={"active_narrative_plan_id": "narrative-plan-mismatch-test"}
            ),
        )
        conn.commit()
    finally:
        conn.close()
    preview = build_current_script_lock_preview(project)
    assert preview.ok is False
    assert preview.lock_fingerprint in (None, "")
    assert EDITORIAL_ERROR_SCRIPT_IDENTITY_MISMATCH in preview.blockers


def test_preview_rejects_selected_hook_identity_mismatch(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _pending_structured_project(tmp_path, temp_db_path)
    assert start_structure_run(project, sync=True).started
    conn = editorial_repo.open_editorial_registry(project.project_root_path)
    try:
        state = editorial_repo.get_project_state(conn, project_id=project.id)
        assert state is not None
        editorial_repo.upsert_project_state(
            conn,
            state.model_copy(update={"selected_hook_id": "hook-mismatch-test"}),
        )
        conn.commit()
    finally:
        conn.close()
    preview = build_current_script_lock_preview(project)
    assert preview.ok is False
    assert preview.lock_fingerprint in (None, "")
    assert EDITORIAL_ERROR_SCRIPT_IDENTITY_MISMATCH in preview.blockers


def test_successful_structure_commit_updates_database_and_json_consistently(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _pending_structured_project(tmp_path, temp_db_path)
    script_id = _active_script_id(project)
    assert script_id
    result = start_structure_run(project, sync=True)
    assert result.started is True
    assert result.message == "Struktur aktualisiert."
    assert _db_script_status(project, script_id=script_id) == (
        ScriptDraftStatus.REVIEW_REQUESTED.value
    )
    assert _read_script_json_status(project, script_id=script_id) == (
        ScriptDraftStatus.REVIEW_REQUESTED.value
    )
    assert _read_latest_script_status(project) == (
        ScriptDraftStatus.REVIEW_REQUESTED.value
    )
    view = get_editorial_view(project)
    assert view.script is not None
    assert view.script.status == ScriptDraftStatus.REVIEW_REQUESTED
    assert view.script.script_id == script_id


def test_successful_structure_commit_sets_completed_run_only_after_persistence(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _pending_structured_project(tmp_path, temp_db_path)
    order: list[str] = []
    real_replace = editorial_repo.replace_script_structure
    real_save = editorial_repo.save_script_bundle_json
    real_complete_run = None

    from otio_app.discovery_v2.jobs import editorial_worker as worker

    real_complete_run = worker._complete_run

    def _replace(*args, **kwargs):
        order.append("replace")
        return real_replace(*args, **kwargs)

    def _save(*args, **kwargs):
        order.append("json_publish")
        return real_save(*args, **kwargs)

    def _complete(conn, run):
        order.append("complete_run")
        # Persistence must already have review_requested in DB + JSON.
        script_id = run.script_id
        assert script_id
        assert _db_script_status(project, script_id=script_id) == (
            ScriptDraftStatus.REVIEW_REQUESTED.value
        )
        assert _read_script_json_status(project, script_id=script_id) == (
            ScriptDraftStatus.REVIEW_REQUESTED.value
        )
        return real_complete_run(conn, run)

    monkeypatch.setattr(editorial_repo, "replace_script_structure", _replace)
    monkeypatch.setattr(editorial_repo, "save_script_bundle_json", _save)
    monkeypatch.setattr(worker, "_complete_run", _complete)
    result = start_structure_run(project, sync=True)
    assert result.started is True
    assert result.run is not None
    assert result.run.status == EditorialRunStatus.COMPLETED
    assert order.index("replace") < order.index("json_publish")
    assert order.index("json_publish") < order.index("complete_run")


def test_successful_consistent_structure_allows_preview_fingerprint(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _pending_structured_project(tmp_path, temp_db_path)
    assert start_structure_run(project, sync=True).started
    assert start_coverage_run(project, sync=True).started
    _resolve_all_gaps_locally(project)
    _decide_all_claims(project)
    preview = preview_script_lock(project)
    assert preview.ok is True
    assert preview.lock_fingerprint
    assert EDITORIAL_ERROR_REGISTRY_ARTIFACT_MISMATCH not in preview.blockers
    assert EDITORIAL_ERROR_SCRIPT_IDENTITY_MISMATCH not in preview.blockers
    assert EDITORIAL_ERROR_ACTIVE_SCRIPT_POINTER_MISSING not in preview.blockers


def test_structure_retry_is_idempotent(tmp_path: Path, temp_db_path: Path) -> None:
    project = _pending_structured_project(tmp_path, temp_db_path)
    assert start_structure_run(project, sync=True).started
    script_id = _active_script_id(project)
    assert script_id
    conn = editorial_repo.open_editorial_registry(project.project_root_path)
    try:
        before_sentences = conn.execute(
            "SELECT COUNT(*) AS n FROM script_sentences WHERE script_id = ?",
            (script_id,),
        ).fetchone()["n"]
        before_beats = conn.execute(
            "SELECT COUNT(*) AS n FROM visual_beats WHERE script_id = ?",
            (script_id,),
        ).fetchone()["n"]
    finally:
        conn.close()
    assert start_structure_run(project, sync=True).started
    conn = editorial_repo.open_editorial_registry(project.project_root_path)
    try:
        after_sentences = conn.execute(
            "SELECT COUNT(*) AS n FROM script_sentences WHERE script_id = ?",
            (script_id,),
        ).fetchone()["n"]
        after_beats = conn.execute(
            "SELECT COUNT(*) AS n FROM visual_beats WHERE script_id = ?",
            (script_id,),
        ).fetchone()["n"]
        status = conn.execute(
            "SELECT status FROM script_drafts WHERE script_id = ?",
            (script_id,),
        ).fetchone()["status"]
    finally:
        conn.close()
    assert after_sentences == before_sentences
    assert after_beats == before_beats
    assert status == ScriptDraftStatus.REVIEW_REQUESTED.value
    assert _read_script_json_status(project, script_id=script_id) == (
        ScriptDraftStatus.REVIEW_REQUESTED.value
    )


def test_schema20_fake_only_no_gateway_no_media_io(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _pending_structured_project(tmp_path, temp_db_path)
    install_no_media_io_guards(monkeypatch)
    assert_schema_20(project)
    assert REGISTRY_SCHEMA_VERSION == "20"
    assert start_structure_run(project, sync=True).started
    conn = get_registry_connection(project.project_root_path)
    try:
        assert read_schema_version(conn) == "20"
    finally:
        conn.close()
