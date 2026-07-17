"""Discovery V2 — Copy-Intake / Working Media (Phase 7B)."""

from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from otio_app.discovery_v2.adapters.byte_copy import (
    ByteCopyError,
    publish_byte_exact_copy,
)
from otio_app.discovery_v2.adapters.intake_job_launcher import (
    reset_intake_job_launcher_for_tests,
)
from otio_app.discovery_v2.adapters.media_probe import NormalizedMediaProbe
from otio_app.discovery_v2.adapters.source_hash import compute_sha256_hex
from otio_app.discovery_v2.application.asset_registry_service import (
    import_confirmed_selection,
)
from otio_app.discovery_v2.application.copy_intake_service import (
    can_start_copy_intake,
    get_copy_intake_status,
    start_copy_intake,
)
from otio_app.discovery_v2.application.inventory_service import run_inventory_scan
from otio_app.discovery_v2.application.media_intake_planning_service import (
    create_intake_plan,
)
from otio_app.discovery_v2.application.selection_service import (
    build_default_draft,
    confirm_selection,
)
from otio_app.discovery_v2.domain.inventory import MediaKind
from otio_app.discovery_v2.domain.media_intake import (
    IntakeRunAssetStatus,
    IntakeRunStatus,
)
from otio_app.discovery_v2.domain.technical_validation import (
    AssetValidationRecord,
    AssetValidationStatus,
    ValidationRunRecord,
    ValidationRunStatus,
)
from otio_app.discovery_v2.persistence import asset_registry_database as reg_db
from otio_app.discovery_v2.persistence import copy_intake_repository as copy_repo
from otio_app.discovery_v2.persistence import technical_validation_repository as val_repo
from otio_app.discovery_v2.ui import media_intake_page as intake_ui
from otio_app.models import Project, ProjectCreate, ProjectMode
from otio_app.project_repository import create_project
from otio_app.ui.navigation import (
    DISCOVERY_V2_NAVIGATION_OPTIONS,
    NAVIGATION_OPTIONS,
    VOICEOVER_GEN_NAVIGATION_OPTIONS,
)


@pytest.fixture(autouse=True)
def _reset_launcher() -> None:
    reset_intake_job_launcher_for_tests()
    yield
    reset_intake_job_launcher_for_tests()


def _write(path: Path, content: bytes = b"x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _now() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture
def media_root(tmp_path: Path) -> Path:
    root = tmp_path / "Project"
    root.mkdir()
    _write(root / "Florida" / "clip.mp4", b"fake-video-copy-a")
    _write(root / "Florida" / "still.jpg", b"fake-image-copy")
    _write(root / "Florida" / "sound.wav", b"fake-audio-copy")
    _write(root / "Chicago" / "hevc.mp4", b"fake-hevc-video")
    _write(root / "_otio" / "classic.mp4", b"classic")
    return root


@pytest.fixture
def discovery_project(media_root: Path, temp_db_path: Path) -> Project:
    return create_project(
        ProjectCreate(
            name="Copy Intake",
            project_root=str(media_root),
            project_mode=ProjectMode.DISCOVERY_V2,
            language="de",
        ),
        db_path=temp_db_path,
        asset_subdir_names=["Florida", "Chicago"],
        selected_asset_subdirs=["Florida", "Chicago"],
    )


@pytest.fixture
def imported(discovery_project: Project):
    snap = run_inventory_scan(discovery_project)
    draft = build_default_draft(snap)
    selection = confirm_selection(
        discovery_project, snap, draft, acknowledged=True
    )
    result = import_confirmed_selection(discovery_project)
    return snap, selection, result


def _seed_validation_and_plan(project: Project):
    """Terminal validation + intake plan with real file hashes."""
    root = project.project_root_path
    conn = reg_db.get_registry_connection(root)
    try:
        latest = val_repo.find_latest_import(conn, project_id=project.id)
        assert latest is not None
        import_id, selection_id, scan_id, asset_count = latest
        run = ValidationRunRecord(
            run_id=str(uuid4()),
            project_id=project.id,
            import_id=import_id,
            selection_id=selection_id,
            scan_id=scan_id,
            status=ValidationRunStatus.COMPLETED,
            created_at=_now(),
            started_at=_now(),
            completed_at=_now(),
            total_assets=asset_count,
            processed_assets=asset_count,
            successful_assets=asset_count,
            failed_assets=0,
        )
        val_repo.insert_run(conn, run)
        for asset in val_repo.list_assets_for_import(conn, import_id=import_id):
            kind = asset.media_kind.value
            src = root / asset.source_relative_path
            digest = compute_sha256_hex(src) if src.is_file() else "0" * 64
            is_hevc = "hevc" in asset.source_relative_path
            defaults = {
                "video": {
                    "video_codec": "hevc" if is_hevc else "h264",
                    "audio_codec": "aac",
                    "container_format": "mp4",
                    "width": 1920,
                    "height": 1080,
                    "frame_rate_numerator": 25,
                    "frame_rate_denominator": 1,
                    "pixel_format": "yuv420p",
                    "bit_depth": 8,
                },
                "audio": {
                    "audio_codec": "pcm_s16le",
                    "container_format": "wav",
                },
                "image": {
                    "container_format": "image2",
                    "width": 64,
                    "height": 48,
                },
            }.get(kind, {})
            val_repo.insert_asset_validation(
                conn,
                AssetValidationRecord(
                    validation_id=str(uuid4()),
                    run_id=run.run_id,
                    asset_id=asset.asset_id,
                    source_relative_path=asset.source_relative_path,
                    status=AssetValidationStatus.PROBE_SUCCEEDED,
                    sha256=digest,
                    media_kind=kind,
                    validated_at=_now(),
                    source_group=asset.source_group,
                    **defaults,
                ),
            )
        conn.commit()
    finally:
        conn.close()

    result = create_intake_plan(project)
    assert result.created and result.plan
    return result.plan


def _fake_probe(*args, **kwargs) -> NormalizedMediaProbe:
    return NormalizedMediaProbe(
        media_kind="video",
        container_format="mp4",
        video_codec="h264",
        width=1920,
        height=1080,
        pixel_format="yuv420p",
        bit_depth=8,
    )


def _source_snapshots(root: Path) -> dict[str, tuple[int, bytes]]:
    return {
        str(p): (p.stat().st_mtime_ns, p.read_bytes())
        for p in root.rglob("*")
        if p.is_file() and "_otio_v2" not in p.parts
    }


# --- Schema ------------------------------------------------------------------


def test_copy_intake_schema_tables(discovery_project) -> None:
    conn = reg_db.get_registry_connection(discovery_project.project_root_path)
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "intake_runs" in tables
    assert "intake_run_assets" in tables
    assert "working_media" in tables
    assert reg_db.read_schema_version(conn) == "5"
    conn.close()


# --- Voraussetzungen ---------------------------------------------------------


def test_wrong_mode_blocks_copy(temp_db_path: Path, tmp_path: Path) -> None:
    root = tmp_path / "classic"
    (root / "Florida").mkdir(parents=True)
    project = create_project(
        ProjectCreate(
            name="Classic",
            project_root=str(root),
            project_mode=ProjectMode.WITH_VOICEOVER,
            language="de",
        ),
        db_path=temp_db_path,
        asset_subdir_names=["Florida"],
        selected_asset_subdirs=["Florida"],
    )
    ok, msg, _ = can_start_copy_intake(project)
    assert ok is False
    assert msg


def test_missing_plan_blocks_copy(discovery_project, imported) -> None:
    ok, msg, _ = can_start_copy_intake(discovery_project)
    assert ok is False
    assert msg


def test_stale_plan_blocks_copy(discovery_project, imported) -> None:
    _seed_validation_and_plan(discovery_project)
    run_inventory_scan(discovery_project)
    ok, msg, _ = can_start_copy_intake(discovery_project)
    assert ok is False
    assert "veraltet" in (msg or "").lower() or "Plan" in (msg or "")


def test_valid_plan_allows_copy_start(discovery_project, imported) -> None:
    plan = _seed_validation_and_plan(discovery_project)
    assert plan.copy_count >= 1
    ok, msg, ctx = can_start_copy_intake(discovery_project)
    assert ok is True
    assert msg is None
    assert ctx is not None
    assert ctx["copy_item_count"] == plan.copy_count
    assert ctx["plan_id"] == plan.plan_id


# --- Byte-Copy Adapter -------------------------------------------------------


def test_byte_copy_publishes_atomically(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "P"
    root.mkdir()
    src = root / "clip.mp4"
    src.write_bytes(b"identical-bytes-123")
    v2 = root / "_otio_v2" / "media"
    temp = v2 / "temp" / "r1" / "a1.mp4"
    working = v2 / "working" / "clip.mp4"
    monkeypatch.setattr(
        "otio_app.discovery_v2.adapters.byte_copy.probe_source_media",
        _fake_probe,
    )
    result = publish_byte_exact_copy(
        project_root=root,
        source_path=src,
        temp_path=temp,
        working_path=working,
        media_kind=MediaKind.VIDEO,
        expected_source_sha256=compute_sha256_hex(src),
    )
    assert working.is_file()
    assert not temp.exists()
    assert result.source_sha256 == result.output_sha256 == compute_sha256_hex(src)
    assert working.read_bytes() == src.read_bytes()
    assert src.read_bytes() == b"identical-bytes-123"


def test_byte_copy_hash_mismatch_rejects(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "P"
    root.mkdir()
    src = root / "clip.mp4"
    src.write_bytes(b"abc")
    temp = root / "_otio_v2" / "media" / "temp" / "t.mp4"
    working = root / "_otio_v2" / "media" / "working" / "clip.mp4"

    def bad_hash(path, **kwargs):
        # Erste Quelle ok, Temp absichtlich falsch
        if "temp" in str(path):
            return "0" * 64
        return compute_sha256_hex.__wrapped__(path) if hasattr(compute_sha256_hex, "__wrapped__") else __import__(
            "otio_app.services.media_utils", fromlist=["file_sha256"]
        ).file_sha256(path).lower()

    calls = {"n": 0}
    real = compute_sha256_hex

    def flaky(path, **kwargs):
        calls["n"] += 1
        if calls["n"] >= 2:
            return "f" * 64
        return real(path)

    monkeypatch.setattr(
        "otio_app.discovery_v2.adapters.byte_copy.compute_sha256_hex", flaky
    )
    with pytest.raises(ByteCopyError) as exc:
        publish_byte_exact_copy(
            project_root=root,
            source_path=src,
            temp_path=temp,
            working_path=working,
            media_kind=MediaKind.VIDEO,
        )
    assert exc.value.code == "hash_mismatch"
    assert not working.exists()


# --- Run / Idempotenz / Medienfreiheit ---------------------------------------


def test_copy_run_copies_only_planned_copy_assets(
    discovery_project, imported, monkeypatch
) -> None:
    monkeypatch.setattr(
        "otio_app.discovery_v2.adapters.byte_copy.probe_source_media",
        _fake_probe,
    )
    plan = _seed_validation_and_plan(discovery_project)
    before = _source_snapshots(discovery_project.project_root_path)
    classic_before = list(
        (discovery_project.project_root_path / "_otio").rglob("*")
    )

    result = start_copy_intake(discovery_project, sync=True)
    assert result.started is True
    assert result.run is not None
    assert result.run.status in {
        IntakeRunStatus.COMPLETED,
        IntakeRunStatus.COMPLETED_WITH_ERRORS,
    }
    assert result.run.total_assets == plan.copy_count
    # HEVC → transcode im Plan, nicht im Copy-Run
    assert plan.transcode_count >= 1
    assert result.run.total_assets < plan.total_assets

    run, assets, working, err = get_copy_intake_status(discovery_project)
    assert err is None
    assert run is not None
    assert len(assets) == plan.copy_count
    assert all(a.planned_action.value == "copy" for a in assets)
    assert result.run.succeeded_assets + result.run.skipped_assets >= 1
    assert working
    for wm in working:
        abs_path = (
            discovery_project.project_root_path
            / "_otio_v2"
            / Path(wm.working_relative_path)
        )
        assert abs_path.is_file()
        src = discovery_project.project_root_path / wm.source_relative_path
        assert abs_path.read_bytes() == src.read_bytes()
        assert wm.source_sha256 == wm.output_sha256

    assert before == _source_snapshots(discovery_project.project_root_path)
    assert list((discovery_project.project_root_path / "_otio").rglob("*")) == classic_before
    report = copy_repo.intake_run_report_path(
        discovery_project.project_root_path, result.run.run_id
    )
    assert report.exists()
    assert copy_repo.latest_intake_run_pointer_path(
        discovery_project.project_root_path
    ).exists()


def test_copy_idempotent_second_run_skips(
    discovery_project, imported, monkeypatch
) -> None:
    monkeypatch.setattr(
        "otio_app.discovery_v2.adapters.byte_copy.probe_source_media",
        _fake_probe,
    )
    _seed_validation_and_plan(discovery_project)
    first = start_copy_intake(discovery_project, sync=True)
    assert first.started and first.run
    second = start_copy_intake(discovery_project, sync=True)
    assert second.started and second.run
    assert second.run.run_id != first.run.run_id
    assert second.run.skipped_assets == second.run.total_assets
    assert second.run.succeeded_assets == 0


def test_rerun_does_not_start_copy() -> None:
    source = Path(intake_ui.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    func = next(
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef)
        and n.name == "render_discovery_media_intake_page"
    )
    under_if: list[bool] = []

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.stack: list[ast.AST] = []

        def generic_visit(self, node: ast.AST) -> None:
            self.stack.append(node)
            super().generic_visit(node)
            self.stack.pop()

        def visit_Call(self, node: ast.Call) -> None:
            if isinstance(node.func, ast.Name) and node.func.id == "start_copy_intake":
                under_if.append(any(isinstance(n, ast.If) for n in self.stack))
            self.generic_visit(node)

    Visitor().visit(func)
    assert under_if == [True]
    assert "Copy-Intake starten" in source
    assert "Remux starten" not in source
    assert "Transkodieren" not in source
    assert "ffmpeg" not in source.lower() or "ffmpeg" not in source


def test_nav_classic_unchanged() -> None:
    assert "Media Intake" in DISCOVERY_V2_NAVIGATION_OPTIONS
    assert "Media Intake" not in NAVIGATION_OPTIONS
    assert "Media Intake" not in VOICEOVER_GEN_NAVIGATION_OPTIONS


def test_no_encode_in_copy_modules() -> None:
    root = Path(__file__).resolve().parents[1]
    modules = [
        "otio_app/discovery_v2/adapters/byte_copy.py",
        "otio_app/discovery_v2/jobs/copy_intake_worker.py",
        "otio_app/discovery_v2/application/copy_intake_service.py",
    ]
    for rel in modules:
        text = (root / rel).read_text(encoding="utf-8")
        assert "transcode_to_clean" not in text
        assert "subprocess" not in text
        assert "ffmpeg" not in text.lower()
        assert "libx264" not in text


def test_orphan_copy_run_recovered(discovery_project, imported, monkeypatch) -> None:
    from otio_app.discovery_v2.application.copy_intake_job_recovery import (
        reconcile_orphaned_copy_intake_run,
    )
    from otio_app.discovery_v2.domain.media_intake import IntakeRunRecord
    from otio_app.discovery_v2.persistence.copy_intake_repository import (
        insert_intake_run,
        open_registry,
    )

    plan = _seed_validation_and_plan(discovery_project)
    conn = open_registry(discovery_project.project_root_path)
    run = IntakeRunRecord(
        run_id=str(uuid4()),
        project_id=discovery_project.id,
        plan_id=plan.plan_id,
        import_id=plan.import_id,
        selection_id=plan.selection_id,
        scan_id=plan.scan_id,
        validation_run_id=plan.validation_run_id,
        status=IntakeRunStatus.RUNNING,
        created_at=_now(),
        started_at=_now(),
        total_assets=1,
    )
    insert_intake_run(conn, run)
    conn.commit()
    conn.close()

    updated = reconcile_orphaned_copy_intake_run(discovery_project)
    assert updated is not None
    assert updated.status == IntakeRunStatus.FAILED
    assert "worker_interrupted" in (updated.error_summary or "")

    ok, _, _ = can_start_copy_intake(discovery_project)
    assert ok is True


def test_failed_asset_does_not_publish(
    discovery_project, imported, monkeypatch
) -> None:
    monkeypatch.setattr(
        "otio_app.discovery_v2.adapters.byte_copy.probe_source_media",
        _fake_probe,
    )
    plan = _seed_validation_and_plan(discovery_project)
    # Eine Copy-Quelle entfernen
    copy_item = next(i for i in plan.items if i.planned_action.value == "copy")
    src = discovery_project.project_root_path / copy_item.source_relative_path
    src.unlink()

    result = start_copy_intake(discovery_project, sync=True)
    assert result.started
    assert result.run is not None
    assert result.run.failed_assets >= 1
    run, assets, working, _ = get_copy_intake_status(discovery_project)
    failed = [a for a in assets if a.asset_id == copy_item.asset_id][0]
    assert failed.status == IntakeRunAssetStatus.FAILED
    assert failed.working_relative_path is None
    # Keine Working-Datei für fehlgeschlagenes Asset
    for wm in working:
        assert wm.asset_id != copy_item.asset_id
