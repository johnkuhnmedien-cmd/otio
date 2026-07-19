"""L5 structure persistence: crash-consistent publish + coverage-safe intents."""

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
from otio_app.discovery_v2.adapters.text_fake import (
    FakeTextAdapter,
    reset_fake_text_test_hook,
    set_fake_text_test_hook,
)
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
    EDITORIAL_ERROR_ARTIFACT_WRITE_FAILED,
    EDITORIAL_ERROR_REGISTRY_ARTIFACT_MISMATCH,
    EDITORIAL_ERROR_REGISTRY_WRITE_FAILED,
    EDITORIAL_ERROR_SCRIPT_IDENTITY_MISMATCH,
    EDITORIAL_ERROR_STRUCTURE_REPLACEMENT_CONFLICTS_WITH_COVERAGE,
    CoverageAuditStatus,
    EditorialRunStatus,
    ScriptDraftStatus,
)
from otio_app.discovery_v2.editorial_paths import (
    editorial_latest_script_relative_path,
    editorial_script_json_relative_path,
    editorial_temp_dir,
    resolve_editorial_relative_path,
)
from otio_app.discovery_v2.jobs import editorial_worker as worker
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


def _count_structure(project, *, script_id: str) -> dict[str, int]:
    conn = editorial_repo.open_editorial_registry(project.project_root_path)
    try:
        sentences = conn.execute(
            "SELECT COUNT(*) AS n FROM script_sentences WHERE script_id = ?",
            (script_id,),
        ).fetchone()["n"]
        beats = conn.execute(
            "SELECT COUNT(*) AS n FROM visual_beats WHERE script_id = ?",
            (script_id,),
        ).fetchone()["n"]
        intents = conn.execute(
            """
            SELECT COUNT(*) AS n FROM visual_intents
            WHERE visual_beat_id IN (
                SELECT visual_beat_id FROM visual_beats WHERE script_id = ?
            )
            """,
            (script_id,),
        ).fetchone()["n"]
        return {
            "sentences": int(sentences),
            "beats": int(beats),
            "intents": int(intents),
        }
    finally:
        conn.close()


def _force_json_review_requested_while_db_pending(project, *, script_id: str) -> None:
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
    finally:
        conn.close()


def test_structure_json_is_not_published_before_registry_commit(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _pending_structured_project(tmp_path, temp_db_path)
    script_id = _active_script_id(project)
    assert script_id
    order: list[str] = []
    real_replace = editorial_repo.replace_script_structure
    real_publish = editorial_repo.publish_staged_script_bundle_json

    def _replace(*args, **kwargs):
        order.append("replace")
        assert _read_script_json_status(project, script_id=script_id) == (
            ScriptDraftStatus.STRUCTURE_PENDING.value
        )
        return real_replace(*args, **kwargs)

    def _publish(*args, **kwargs):
        order.append("publish")
        assert _db_script_status(project, script_id=script_id) == (
            ScriptDraftStatus.REVIEW_REQUESTED.value
        )
        return real_publish(*args, **kwargs)

    monkeypatch.setattr(editorial_repo, "replace_script_structure", _replace)
    monkeypatch.setattr(editorial_repo, "publish_staged_script_bundle_json", _publish)
    assert start_structure_run(project, sync=True).started
    assert order.index("replace") < order.index("publish")


def test_structure_failure_before_commit_leaves_current_json_untouched(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _pending_structured_project(tmp_path, temp_db_path)
    script_id = _active_script_id(project)
    assert script_id
    before_json = _read_script_json_status(project, script_id=script_id)
    before_latest = _read_latest_script_status(project)

    def _boom(*args, **kwargs):
        raise sqlite3.IntegrityError("FOREIGN KEY constraint failed")

    monkeypatch.setattr(editorial_repo, "replace_script_structure", _boom)
    result = start_structure_run(project, sync=True)
    assert result.started is False
    assert result.error_code == EDITORIAL_ERROR_REGISTRY_WRITE_FAILED
    assert _db_script_status(project, script_id=script_id) == (
        ScriptDraftStatus.STRUCTURE_PENDING.value
    )
    assert _read_script_json_status(project, script_id=script_id) == before_json
    assert _read_latest_script_status(project) == before_latest
    # Staged temps must not remain as Current.
    temp_root = editorial_temp_dir(project.project_root_path, result.run.run_id)  # type: ignore[union-attr]
    assert not temp_root.exists() or not any(temp_root.rglob("*.json"))


def test_structure_failure_after_commit_blocks_preview_until_artifact_recovery(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _pending_structured_project(tmp_path, temp_db_path)
    script_id = _active_script_id(project)
    assert script_id
    real_publish = editorial_repo.publish_staged_script_bundle_json

    def _boom_publish(*args, **kwargs):
        raise OSError("simulated publish failure after commit")

    monkeypatch.setattr(
        editorial_repo, "publish_staged_script_bundle_json", _boom_publish
    )
    result = start_structure_run(project, sync=True)
    assert result.started is False
    assert result.error_code == EDITORIAL_ERROR_ARTIFACT_WRITE_FAILED
    assert result.message != "Struktur aktualisiert."
    assert _db_script_status(project, script_id=script_id) == (
        ScriptDraftStatus.REVIEW_REQUESTED.value
    )
    assert _read_script_json_status(project, script_id=script_id) == (
        ScriptDraftStatus.STRUCTURE_PENDING.value
    )
    preview = build_current_script_lock_preview(project)
    assert preview.ok is False
    assert preview.lock_fingerprint in (None, "")
    assert EDITORIAL_ERROR_REGISTRY_ARTIFACT_MISMATCH in preview.blockers

    monkeypatch.setattr(
        editorial_repo, "publish_staged_script_bundle_json", real_publish
    )
    before = _count_structure(project, script_id=script_id)
    recovered = start_structure_run(project, sync=True)
    assert recovered.started is True
    after = _count_structure(project, script_id=script_id)
    assert after == before
    assert _read_script_json_status(project, script_id=script_id) == (
        ScriptDraftStatus.REVIEW_REQUESTED.value
    )
    assert _read_latest_script_status(project) == (
        ScriptDraftStatus.REVIEW_REQUESTED.value
    )


def test_structure_crash_between_versioned_and_latest_publish_is_fail_closed(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _pending_structured_project(tmp_path, temp_db_path)
    script_id = _active_script_id(project)
    assert script_id
    real_publish = editorial_repo.publish_staged_script_bundle_json

    def _partial_publish(project_root, *, script_id, staged):
        # Publish versioned only; leave latest alias on the prior stand.
        from otio_app.discovery_v2.editorial_paths import (
            editorial_script_json_relative_path,
            resolve_editorial_relative_path,
        )

        target = resolve_editorial_relative_path(
            project_root, editorial_script_json_relative_path(script_id)
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        Path(staged["versioned"]).replace(target)
        raise OSError("crash before latest alias publish")

    monkeypatch.setattr(
        editorial_repo, "publish_staged_script_bundle_json", _partial_publish
    )
    result = start_structure_run(project, sync=True)
    assert result.started is False
    assert result.error_code == EDITORIAL_ERROR_ARTIFACT_WRITE_FAILED
    assert _read_script_json_status(project, script_id=script_id) == (
        ScriptDraftStatus.REVIEW_REQUESTED.value
    )
    assert _read_latest_script_status(project) == (
        ScriptDraftStatus.STRUCTURE_PENDING.value
    )
    preview = build_current_script_lock_preview(project)
    assert preview.ok is False
    assert preview.lock_fingerprint in (None, "")
    assert EDITORIAL_ERROR_REGISTRY_ARTIFACT_MISMATCH in preview.blockers
    # Canonical reader uses versioned registry path, not latest alias.
    conn = editorial_repo.open_editorial_registry(project.project_root_path)
    try:
        active = editorial_repo.get_active_script(conn, project_id=project.id)
        assert active is not None
        assert active.status == ScriptDraftStatus.REVIEW_REQUESTED
    finally:
        conn.close()
    # Retry repairs the alias.
    monkeypatch.setattr(
        editorial_repo, "publish_staged_script_bundle_json", real_publish
    )
    assert start_structure_run(project, sync=True).started
    assert _read_latest_script_status(project) == (
        ScriptDraftStatus.REVIEW_REQUESTED.value
    )


def test_structure_retry_repairs_artifacts_without_duplicate_rows(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _pending_structured_project(tmp_path, temp_db_path)
    script_id = _active_script_id(project)
    assert script_id
    real_publish = editorial_repo.publish_staged_script_bundle_json

    def _boom(*args, **kwargs):
        raise OSError("publish failed")

    monkeypatch.setattr(editorial_repo, "publish_staged_script_bundle_json", _boom)
    assert start_structure_run(project, sync=True).started is False
    before = _count_structure(project, script_id=script_id)
    monkeypatch.setattr(
        editorial_repo, "publish_staged_script_bundle_json", real_publish
    )
    assert start_structure_run(project, sync=True).started
    after = _count_structure(project, script_id=script_id)
    assert after == before
    assert after["sentences"] > 0
    assert after["intents"] > 0


def test_latest_script_alias_is_not_source_of_truth(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _pending_structured_project(tmp_path, temp_db_path)
    assert start_structure_run(project, sync=True).started
    script_id = _active_script_id(project)
    assert script_id
    # Corrupt only the alias.
    latest = resolve_editorial_relative_path(
        project.project_root_path, editorial_latest_script_relative_path()
    )
    payload = json.loads(latest.read_text(encoding="utf-8"))
    payload["script"]["status"] = ScriptDraftStatus.STRUCTURE_PENDING.value
    latest.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    conn = editorial_repo.open_editorial_registry(project.project_root_path)
    try:
        active = editorial_repo.get_active_script(conn, project_id=project.id)
        assert active is not None
        assert active.status == ScriptDraftStatus.REVIEW_REQUESTED
        assert editorial_repo.script_latest_alias_mismatch(conn, script_id=script_id)
    finally:
        conn.close()
    preview = build_current_script_lock_preview(project)
    assert preview.ok is False
    assert EDITORIAL_ERROR_REGISTRY_ARTIFACT_MISMATCH in preview.blockers


def test_referenced_visual_intents_are_not_silently_deleted(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _pending_structured_project(tmp_path, temp_db_path)
    assert start_structure_run(project, sync=True).started
    assert start_coverage_run(project, sync=True).started
    conn = editorial_repo.open_editorial_registry(project.project_root_path)
    try:
        state = editorial_repo.get_project_state(conn, project_id=project.id)
        assert state is not None and state.active_coverage_audit_id
        audit_id = state.active_coverage_audit_id
        before_results = conn.execute(
            "SELECT COUNT(*) AS n FROM coverage_intent_results WHERE coverage_audit_id = ?",
            (audit_id,),
        ).fetchone()["n"]
        assert before_results > 0
        intent_ids = {
            str(row["visual_intent_id"])
            for row in conn.execute(
                "SELECT visual_intent_id FROM coverage_intent_results WHERE coverage_audit_id = ?",
                (audit_id,),
            ).fetchall()
        }
    finally:
        conn.close()
    # Idempotent structure retry must keep coverage results.
    assert start_structure_run(project, sync=True).started
    conn = editorial_repo.open_editorial_registry(project.project_root_path)
    try:
        after_results = conn.execute(
            "SELECT COUNT(*) AS n FROM coverage_intent_results WHERE coverage_audit_id = ?",
            (audit_id,),
        ).fetchone()["n"]
        assert after_results == before_results
        still = {
            str(row["visual_intent_id"])
            for row in conn.execute(
                "SELECT visual_intent_id FROM coverage_intent_results WHERE coverage_audit_id = ?",
                (audit_id,),
            ).fetchall()
        }
        assert still == intent_ids
        for intent_id in intent_ids:
            row = conn.execute(
                "SELECT 1 FROM visual_intents WHERE visual_intent_id = ?",
                (intent_id,),
            ).fetchone()
            assert row is not None
    finally:
        conn.close()


def test_structure_change_removing_referenced_intent_fails_closed(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _pending_structured_project(tmp_path, temp_db_path)
    assert start_structure_run(project, sync=True).started
    assert start_coverage_run(project, sync=True).started
    adapter = FakeTextAdapter()

    def hook(request):
        if request.request_kind != "structure":
            return None
        payload = adapter._script_or_structure(request)
        # Drop all intents / replace with a never-before-seen id → would remove refs.
        payload["visual_intents"] = [
            {
                "visual_intent_id": "intent-conflicts-with-coverage",
                "visual_beat_id": payload["visual_beats"][0]["visual_beat_id"],
                "desired_motif": "Konflikt",
                "action": "ersetzt referenzierte Intents",
                "setting": "test",
                "geographic_requirements": None,
                "authenticity_requirements": [],
                "allowed_media_kinds": ["image"],
                "priority": 1,
            }
        ]
        return payload

    set_fake_text_test_hook(hook)
    result = start_structure_run(project, sync=True)
    assert result.started is False
    assert (
        result.error_code
        == EDITORIAL_ERROR_STRUCTURE_REPLACEMENT_CONFLICTS_WITH_COVERAGE
    )
    conn = editorial_repo.open_editorial_registry(project.project_root_path)
    try:
        state = editorial_repo.get_project_state(conn, project_id=project.id)
        assert state is not None and state.active_coverage_audit_id
        audit = editorial_repo.get_coverage_audit(
            conn, coverage_audit_id=state.active_coverage_audit_id
        )
        assert audit is not None
        assert audit.status == CoverageAuditStatus.COMPLETED
        results = conn.execute(
            "SELECT COUNT(*) AS n FROM coverage_intent_results WHERE coverage_audit_id = ?",
            (state.active_coverage_audit_id,),
        ).fetchone()["n"]
        assert results > 0
    finally:
        conn.close()


def test_current_coverage_audit_remains_complete_with_all_intent_results(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _pending_structured_project(tmp_path, temp_db_path)
    assert start_structure_run(project, sync=True).started
    assert start_coverage_run(project, sync=True).started
    conn = editorial_repo.open_editorial_registry(project.project_root_path)
    try:
        state = editorial_repo.get_project_state(conn, project_id=project.id)
        assert state is not None
        audit_id = state.active_coverage_audit_id
        assert audit_id
        before = conn.execute(
            "SELECT COUNT(*) AS n FROM coverage_intent_results WHERE coverage_audit_id = ?",
            (audit_id,),
        ).fetchone()["n"]
    finally:
        conn.close()
    assert start_structure_run(project, sync=True).started
    conn = editorial_repo.open_editorial_registry(project.project_root_path)
    try:
        state = editorial_repo.get_project_state(conn, project_id=project.id)
        assert state is not None
        assert state.active_coverage_audit_id == audit_id
        audit = editorial_repo.get_coverage_audit(conn, coverage_audit_id=audit_id)
        assert audit is not None
        assert audit.status == CoverageAuditStatus.COMPLETED
        after = conn.execute(
            "SELECT COUNT(*) AS n FROM coverage_intent_results WHERE coverage_audit_id = ?",
            (audit_id,),
        ).fetchone()["n"]
        assert after == before
    finally:
        conn.close()


def test_successful_structure_publish_sets_run_completed_only_after_both_artifacts(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _pending_structured_project(tmp_path, temp_db_path)
    order: list[str] = []
    real_publish = editorial_repo.publish_staged_script_bundle_json
    real_complete = worker._complete_run

    def _publish(*args, **kwargs):
        order.append("publish_both")
        return real_publish(*args, **kwargs)

    def _complete(conn, run):
        order.append("complete_run")
        script_id = run.script_id
        assert script_id
        assert _read_script_json_status(project, script_id=script_id) == (
            ScriptDraftStatus.REVIEW_REQUESTED.value
        )
        assert _read_latest_script_status(project) == (
            ScriptDraftStatus.REVIEW_REQUESTED.value
        )
        return real_complete(conn, run)

    monkeypatch.setattr(editorial_repo, "publish_staged_script_bundle_json", _publish)
    monkeypatch.setattr(worker, "_complete_run", _complete)
    result = start_structure_run(project, sync=True)
    assert result.started is True
    assert result.run is not None
    assert result.run.status == EditorialRunStatus.COMPLETED
    assert order == ["publish_both", "complete_run"]


def test_successful_structure_publish_produces_consistent_db_and_json(
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
    conn = editorial_repo.open_editorial_registry(project.project_root_path)
    try:
        state = editorial_repo.get_project_state(conn, project_id=project.id)
        script = editorial_repo.get_active_script(conn, project_id=project.id)
        assert state is not None and script is not None
        assert state.active_script_id == script.script_id == script_id
        assert state.active_narrative_plan_id == script.narrative_plan_id
        assert state.selected_hook_id == script.selected_hook_id
        assert not editorial_repo.script_registry_json_status_mismatch(
            conn, script_id=script_id
        )
        assert not editorial_repo.script_latest_alias_mismatch(
            conn, script_id=script_id
        )
    finally:
        conn.close()


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


# --- retained prior atomicity / preview contracts ---


def test_structure_registry_failure_does_not_publish_review_requested_json(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _pending_structured_project(tmp_path, temp_db_path)
    script_id = _active_script_id(project)
    assert script_id

    def _boom(*args, **kwargs):
        raise sqlite3.IntegrityError("FOREIGN KEY constraint failed")

    monkeypatch.setattr(editorial_repo, "replace_script_structure", _boom)
    result = start_structure_run(project, sync=True)
    assert result.started is False
    assert result.error_code == EDITORIAL_ERROR_REGISTRY_WRITE_FAILED
    assert _read_script_json_status(project, script_id=script_id) == (
        ScriptDraftStatus.STRUCTURE_PENDING.value
    )


def test_structure_foreign_key_failure_is_reported_not_shown_as_success(
    tmp_path: Path, temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _pending_structured_project(tmp_path, temp_db_path)

    def _boom(*args, **kwargs):
        raise sqlite3.IntegrityError("FOREIGN KEY constraint failed")

    monkeypatch.setattr(editorial_repo, "replace_script_structure", _boom)
    result = start_structure_run(project, sync=True)
    assert result.started is False
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
    assert EDITORIAL_ERROR_REGISTRY_ARTIFACT_MISMATCH in preview.blockers


def test_preview_rejects_missing_active_script_pointer(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _pending_structured_project(tmp_path, temp_db_path)
    conn = editorial_repo.open_editorial_registry(project.project_root_path)
    try:
        state = editorial_repo.get_project_state(conn, project_id=project.id)
        assert state is not None
        editorial_repo.upsert_project_state(
            conn, state.model_copy(update={"active_script_id": None})
        )
        conn.commit()
    finally:
        conn.close()
    preview = build_current_script_lock_preview(project)
    assert preview.ok is False
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
            conn, state.model_copy(update={"selected_hook_id": "hook-mismatch-test"})
        )
        conn.commit()
    finally:
        conn.close()
    preview = build_current_script_lock_preview(project)
    assert preview.ok is False
    assert EDITORIAL_ERROR_SCRIPT_IDENTITY_MISMATCH in preview.blockers


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


def test_structure_retry_is_idempotent(tmp_path: Path, temp_db_path: Path) -> None:
    project = _pending_structured_project(tmp_path, temp_db_path)
    assert start_structure_run(project, sync=True).started
    script_id = _active_script_id(project)
    assert script_id
    before = _count_structure(project, script_id=script_id)
    assert start_structure_run(project, sync=True).started
    after = _count_structure(project, script_id=script_id)
    assert after == before
    view = get_editorial_view(project)
    assert view.script is not None
    assert view.script.status == ScriptDraftStatus.REVIEW_REQUESTED
