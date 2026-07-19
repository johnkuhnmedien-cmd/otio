"""Discovery V2 Phase-6 Preflight: Pfadvertrag und Job-Recovery."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from otio_app.discovery_v2.adapters.validation_job_launcher import (
    get_validation_job_launcher,
    reset_validation_job_launcher_for_tests,
)
from otio_app.discovery_v2.application.asset_registry_service import (
    import_confirmed_selection,
)
from otio_app.discovery_v2.application.inventory_service import run_inventory_scan
from otio_app.discovery_v2.application.selection_service import (
    build_default_draft,
    confirm_selection,
)
from otio_app.discovery_v2.application.technical_validation_service import (
    can_start_technical_validation,
    get_validation_status,
    start_technical_validation,
)
from otio_app.discovery_v2.application.validation_job_recovery import (
    reconcile_orphaned_validation_run,
)
from otio_app.discovery_v2.domain.technical_validation import (
    WORKER_INTERRUPTED_ERROR_CODE,
    ValidationRunRecord,
    ValidationRunStatus,
)
from otio_app.discovery_v2.paths import (
    assert_path_is_under_discovery_v2,
    get_discovery_v2_root,
)
from otio_app.discovery_v2.persistence import technical_validation_repository as val_repo
from otio_app.discovery_v2.ui import technical_validation_page as val_ui
from otio_app.models import ProjectCreate, ProjectMode
from otio_app.project_repository import create_project
from otio_app.project_work_root import resolve_project_work_root
from otio_app.ui.navigation import (
    NAVIGATION_OPTIONS,
    VOICEOVER_GEN_NAVIGATION_OPTIONS,
)


def _write(path: Path, content: bytes = b"x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


@pytest.fixture(autouse=True)
def _reset_launcher() -> None:
    reset_validation_job_launcher_for_tests()
    yield
    reset_validation_job_launcher_for_tests()


@pytest.fixture
def media_root(tmp_path: Path) -> Path:
    root = tmp_path / "Project"
    root.mkdir()
    (root / "Florida").mkdir()
    _write(root / "Florida" / "clip.mp4", b"video")
    return root


def _discovery(root: Path, temp_db_path: Path):
    return create_project(
        ProjectCreate(
            name="Preflight",
            project_root=str(root),
            project_mode=ProjectMode.DISCOVERY_V2,
            language="de",
        ),
        db_path=temp_db_path,
        asset_subdir_names=["Florida"],
        selected_asset_subdirs=["Florida"],
    )


def _imported(project):
    snap = run_inventory_scan(project)
    selection = confirm_selection(
        project, snap, build_default_draft(snap), acknowledged=True
    )
    result = import_confirmed_selection(project)
    return snap, selection, result


# --- Pfadvertrag -----------------------------------------------------------


def test_classic_work_root_remains_otio(tmp_path, temp_db_path) -> None:
    root = tmp_path / "Classic"
    root.mkdir()
    (root / "A").mkdir()
    _write(root / "A" / "a.mp4")
    (root / "Voice over").mkdir()
    project = create_project(
        ProjectCreate(
            name="C",
            project_root=str(root),
            project_mode=ProjectMode.WITH_VOICEOVER,
            language="de",
            voice_over_subdir="Voice over",
        ),
        db_path=temp_db_path,
        asset_subdir_names=["A"],
        selected_asset_subdirs=["A"],
    )
    resolved = resolve_project_work_root(project)
    assert resolved == (root / "_otio").resolve()
    assert project.resolved_work_root == resolved
    assert resolved.name == "_otio"


def test_without_vo_work_root_remains_otio(tmp_path, temp_db_path) -> None:
    root = tmp_path / "NoVO"
    root.mkdir()
    (root / "A").mkdir()
    _write(root / "A" / "a.mp4")
    project = create_project(
        ProjectCreate(
            name="N",
            project_root=str(root),
            project_mode=ProjectMode.WITHOUT_VOICEOVER,
            language="de",
        ),
        db_path=temp_db_path,
        asset_subdir_names=["A"],
        selected_asset_subdirs=["A"],
    )
    assert resolve_project_work_root(project).name == "_otio"


def test_discovery_work_root_is_otio_v2(media_root, temp_db_path) -> None:
    project = _discovery(media_root, temp_db_path)
    resolved = resolve_project_work_root(project)
    assert resolved == get_discovery_v2_root(media_root)
    assert resolved.name == "_otio_v2"
    assert resolved.parts[-1] == "_otio_v2"
    assert resolved.parts[-2] != "_otio_v2"


def test_discovery_ignores_stored_classic_work_dir(media_root, temp_db_path) -> None:
    project = _discovery(media_root, temp_db_path)
    # DB/String zeigt oft Classic — produktive Auflösung darf das ignorieren.
    assert "_otio" in project.work_dir or project.work_dir.endswith("_otio")
    misleading = project.model_copy(
        update={"work_dir": str(media_root / "_otio" / "nested")}
    )
    resolved = resolve_project_work_root(misleading)
    assert resolved == (media_root / "_otio_v2").resolve()
    assert "_otio_v2" in resolved.parts
    assert resolved != Path(misleading.work_dir).resolve()


def test_discovery_resolver_does_not_create_otio(media_root, temp_db_path) -> None:
    project = _discovery(media_root, temp_db_path)
    resolve_project_work_root(project)
    project.resolved_work_root
    get_discovery_v2_root(media_root)
    assert not (media_root / "_otio").exists()


def test_discovery_paths_cannot_escape_otio_v2(media_root) -> None:
    root = get_discovery_v2_root(media_root)
    ok = root / "inventory" / "x.json"
    assert assert_path_is_under_discovery_v2(ok, media_root) == ok.resolve()

    classic = media_root / "_otio" / "x.json"
    with pytest.raises(ValueError, match="_otio"):
        assert_path_is_under_discovery_v2(classic, media_root)

    escape = root / ".." / "_otio" / "evil.json"
    with pytest.raises(ValueError):
        assert_path_is_under_discovery_v2(escape, media_root)


def test_nav_lists_unchanged() -> None:
    assert "Technische Prüfung" not in NAVIGATION_OPTIONS
    assert "Technische Prüfung" not in VOICEOVER_GEN_NAVIGATION_OPTIONS


# --- Job-Recovery ----------------------------------------------------------


def _insert_orphan_running(project, *, import_id: str, selection_id: str, scan_id: str):
    conn = val_repo.open_registry(project.project_root_path)
    run = ValidationRunRecord(
        run_id=str(uuid4()),
        project_id=project.id,
        import_id=import_id,
        selection_id=selection_id,
        scan_id=scan_id,
        status=ValidationRunStatus.RUNNING,
        created_at=datetime.now(timezone.utc),
        started_at=datetime.now(timezone.utc),
        total_assets=1,
        processed_assets=0,
    )
    val_repo.insert_run(conn, run)
    conn.commit()
    conn.close()
    return run


def test_orphaned_running_job_detected(media_root, temp_db_path) -> None:
    project = _discovery(media_root, temp_db_path)
    _, selection, imp = _imported(project)
    orphan = _insert_orphan_running(
        project,
        import_id=imp.import_id,
        selection_id=selection.selection_id,
        scan_id=selection.scan_id,
    )
    assert get_validation_job_launcher().is_active(project.id) is False
    reconciled = reconcile_orphaned_validation_run(project)
    assert reconciled is not None
    assert reconciled.run_id == orphan.run_id
    assert reconciled.status == ValidationRunStatus.FAILED
    assert WORKER_INTERRUPTED_ERROR_CODE in (reconciled.error_summary or "")


def test_orphaned_job_gets_controlled_failure_status(media_root, temp_db_path) -> None:
    project = _discovery(media_root, temp_db_path)
    _, selection, imp = _imported(project)
    _insert_orphan_running(
        project,
        import_id=imp.import_id,
        selection_id=selection.selection_id,
        scan_id=selection.scan_id,
    )
    run, _, _ = get_validation_status(project)
    assert run is not None
    assert run.status == ValidationRunStatus.FAILED
    assert WORKER_INTERRUPTED_ERROR_CODE in (run.error_summary or "")


def test_new_job_can_start_after_orphan_recovery(media_root, temp_db_path) -> None:
    project = _discovery(media_root, temp_db_path)
    _, selection, imp = _imported(project)
    _insert_orphan_running(
        project,
        import_id=imp.import_id,
        selection_id=selection.selection_id,
        scan_id=selection.scan_id,
    )
    ok, msg, _ = can_start_technical_validation(project)
    assert ok is True, msg
    result = start_technical_validation(project, sync=True)
    assert result.started is True
    assert result.run is not None
    assert result.run.status in {
        ValidationRunStatus.COMPLETED,
        ValidationRunStatus.COMPLETED_WITH_ERRORS,
    }


def test_completed_job_not_modified_by_recovery(media_root, temp_db_path) -> None:
    project = _discovery(media_root, temp_db_path)
    _imported(project)
    first = start_technical_validation(project, sync=True)
    assert first.run is not None
    completed_id = first.run.run_id
    completed_status = first.run.status
    completed_summary = first.run.error_summary

    again = reconcile_orphaned_validation_run(project)
    assert again is None
    conn = val_repo.open_registry(project.project_root_path)
    stored = val_repo.get_run(conn, run_id=completed_id)
    conn.close()
    assert stored is not None
    assert stored.status == completed_status
    assert stored.error_summary == completed_summary


def test_streamlit_rerun_does_not_start_job(
    media_root, temp_db_path, monkeypatch
) -> None:
    project = _discovery(media_root, temp_db_path)
    _imported(project)
    st = MagicMock()
    st.session_state = {}
    st.button = lambda *a, **k: False
    st.columns = lambda n: [MagicMock() for _ in range(n)]
    st.expander = MagicMock()
    st.expander.return_value.__enter__ = MagicMock(return_value=MagicMock())
    st.expander.return_value.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr(val_ui, "st", st)
    monkeypatch.setattr(val_ui, "active_discovery_project", lambda: project)
    val_ui.render_discovery_technical_validation_page()
    conn = val_repo.open_registry(project.project_root_path)
    count = conn.execute("SELECT COUNT(*) AS c FROM validation_runs").fetchone()["c"]
    conn.close()
    assert count == 0


def test_active_live_worker_not_reconciled(media_root, temp_db_path) -> None:
    project = _discovery(media_root, temp_db_path)
    _, selection, imp = _imported(project)
    orphan = _insert_orphan_running(
        project,
        import_id=imp.import_id,
        selection_id=selection.selection_id,
        scan_id=selection.scan_id,
    )
    launcher = get_validation_job_launcher()
    # Simuliere lebenden Worker
    import threading

    event = threading.Event()

    def _block():
        event.wait(timeout=5)

    thread = threading.Thread(target=_block, daemon=True)
    launcher._threads[project.id] = thread
    thread.start()
    try:
        assert reconcile_orphaned_validation_run(project) is None
        conn = val_repo.open_registry(project.project_root_path)
        stored = val_repo.get_run(conn, run_id=orphan.run_id)
        conn.close()
        assert stored is not None
        assert stored.status == ValidationRunStatus.RUNNING
    finally:
        event.set()
        thread.join(timeout=2)
        launcher._threads.pop(project.id, None)
