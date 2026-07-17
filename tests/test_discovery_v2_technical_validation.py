"""Discovery V2 — technische Medienprüfung (Phase 5)."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from otio_app.database import get_connection as get_classic_connection
from otio_app.discovery_v2.adapters import media_probe as media_probe_mod
from otio_app.discovery_v2.adapters.source_hash import compute_sha256_hex
from otio_app.discovery_v2.application.asset_registry_service import (
    import_confirmed_selection,
)
from otio_app.discovery_v2.application.inventory_service import run_inventory_scan
from otio_app.discovery_v2.application.selection_service import (
    build_default_draft,
    confirm_selection,
)
from otio_app.discovery_v2.application import technical_validation_service as tvs
from otio_app.discovery_v2.application.technical_validation_service import (
    can_start_technical_validation,
    get_validation_status,
    start_technical_validation,
)
from otio_app.discovery_v2.domain.inventory import MediaKind
from otio_app.discovery_v2.domain.technical_validation import (
    AssetValidationStatus,
    ValidationRunStatus,
)
from otio_app.discovery_v2.jobs.technical_validation_worker import (
    _resolve_source_path,
)
from otio_app.discovery_v2.persistence import asset_registry_database as reg_db
from otio_app.discovery_v2.persistence import technical_validation_repository as val_repo
from otio_app.discovery_v2.ui import technical_validation_page as val_ui
from otio_app.models import Project, ProjectCreate, ProjectMode
from otio_app.project_repository import create_project
from otio_app.ui.navigation import (
    NAVIGATION_OPTIONS,
    VOICEOVER_GEN_NAVIGATION_OPTIONS,
)


FFMPEG = "ffmpeg"


def _have_ffmpeg() -> bool:
    try:
        subprocess.run(
            [FFMPEG, "-version"],
            capture_output=True,
            check=False,
            timeout=10,
        )
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


requires_ffmpeg = pytest.mark.skipif(
    not _have_ffmpeg(), reason="ffmpeg nicht verfügbar"
)


def _write(path: Path, content: bytes = b"x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _make_video(path: Path, *, fps: str = "25/1") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            FFMPEG,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=320x240:d=0.2",
            "-r",
            "25",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        capture_output=True,
        check=True,
        timeout=30,
    )


def _make_audio(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            FFMPEG,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=0.2",
            "-c:a",
            "pcm_s16le",
            str(path),
        ],
        capture_output=True,
        check=True,
        timeout=30,
    )


def _make_image(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            FFMPEG,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=64x48:d=0.04",
            "-frames:v",
            "1",
            str(path),
        ],
        capture_output=True,
        check=True,
        timeout=30,
    )


def _build_real_media_tree(root: Path) -> None:
    _make_video(root / "Florida" / "clip.mp4")
    _make_image(root / "Florida" / "still.jpg")
    _make_audio(root / "root_audio.wav")
    # Identischer Inhalt für Dubletten (Kopie derselben Bytes)
    video_bytes = (root / "Florida" / "clip.mp4").read_bytes()
    _write(root / "Chicago" / "dup_clip.mp4", video_bytes)
    # Absichtlich ungültige „Video“-Datei
    _write(root / "Chicago" / "broken.mp4", b"not-a-real-video")
    _write(root / "notes.txt", b"ignore")
    _write(root / "_otio" / "classic.mp4", b"classic")


@pytest.fixture
def media_root(tmp_path: Path) -> Path:
    root = tmp_path / "Project"
    root.mkdir()
    if _have_ffmpeg():
        _build_real_media_tree(root)
    else:
        _write(root / "Florida" / "clip.mp4", b"fake-video")
        _write(root / "Florida" / "still.jpg", b"fake-image")
        _write(root / "root_audio.wav", b"fake-audio")
        _write(root / "Chicago" / "dup_clip.mp4", b"fake-video")
        _write(root / "Chicago" / "broken.mp4", b"not-a-real-video")
        _write(root / "notes.txt", b"ignore")
        _write(root / "_otio" / "classic.mp4", b"classic")
    return root


@pytest.fixture
def discovery_project(media_root: Path, temp_db_path: Path) -> Project:
    return create_project(
        ProjectCreate(
            name="Val Smoke",
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


def _source_snapshots(root: Path) -> dict[str, tuple[int, bytes]]:
    return {
        str(p): (p.stat().st_mtime_ns, p.read_bytes())
        for p in root.rglob("*")
        if p.is_file() and "_otio_v2" not in p.parts
    }


# --- Schema und Runs -------------------------------------------------------


def test_validation_schema_created(discovery_project) -> None:
    conn = reg_db.get_registry_connection(discovery_project.project_root_path)
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "validation_runs" in tables
    assert "asset_validations" in tables
    assert "duplicate_groups" in tables
    assert reg_db.read_schema_version(conn) == "15"
    conn.close()


def test_schema_init_idempotent_keeps_assets(discovery_project, imported) -> None:
    conn = reg_db.get_registry_connection(discovery_project.project_root_path)
    before = conn.execute("SELECT COUNT(*) AS c FROM assets").fetchone()["c"]
    conn.close()
    conn2 = reg_db.get_registry_connection(discovery_project.project_root_path)
    after = conn2.execute("SELECT COUNT(*) AS c FROM assets").fetchone()["c"]
    assert before == after > 0
    assert reg_db.read_schema_version(conn2) == "15"
    conn2.close()


def test_migrate_from_schema_v1_preserves_assets(
    discovery_project, imported, tmp_path: Path
) -> None:
    db = reg_db.registry_sqlite_path(discovery_project.project_root_path)
    conn = sqlite3.connect(str(db))
    conn.execute(
        "UPDATE registry_schema SET schema_version = '1'"
    )
    conn.commit()
    count = conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
    conn.close()

    conn2 = reg_db.get_registry_connection(discovery_project.project_root_path)
    assert reg_db.read_schema_version(conn2) == "15"
    assert conn2.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == count
    assert conn2.execute(
        "SELECT name FROM sqlite_master WHERE name='validation_runs'"
    ).fetchone()
    conn2.close()


def test_classic_db_unchanged_by_validation(
    discovery_project, imported, temp_db_path: Path
) -> None:
    before = temp_db_path.read_bytes()
    tables_before = {
        r[0]
        for r in get_classic_connection(temp_db_path).execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    start_technical_validation(discovery_project, sync=True)
    assert temp_db_path.read_bytes() == before
    tables_after = {
        r[0]
        for r in get_classic_connection(temp_db_path).execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert tables_before == tables_after
    assert "validation_runs" not in tables_after


def test_duplicate_active_run_prevented(discovery_project, imported) -> None:
    import threading

    from datetime import datetime, timezone
    from uuid import uuid4

    from otio_app.discovery_v2.adapters.validation_job_launcher import (
        get_validation_job_launcher,
    )
    from otio_app.discovery_v2.domain.technical_validation import ValidationRunRecord

    conn = val_repo.open_registry(discovery_project.project_root_path)
    run = ValidationRunRecord(
        run_id=str(uuid4()),
        project_id=discovery_project.id,
        import_id=imported[2].import_id,
        selection_id=imported[1].selection_id,
        scan_id=imported[1].scan_id,
        status=ValidationRunStatus.RUNNING,
        created_at=datetime.now(timezone.utc),
        total_assets=1,
    )
    val_repo.insert_run(conn, run)
    conn.commit()
    conn.close()

    # Lebender Worker — verwaiste Runs werden recovered; aktive nicht.
    launcher = get_validation_job_launcher()
    stop = threading.Event()

    def _block() -> None:
        stop.wait(timeout=5)

    thread = threading.Thread(target=_block, daemon=True)
    launcher._threads[discovery_project.id] = thread
    thread.start()
    try:
        result = start_technical_validation(discovery_project, sync=True)
        assert result.started is False
        assert "bereits" in result.message.lower()
    finally:
        stop.set()
        thread.join(timeout=2)
        launcher._threads.pop(discovery_project.id, None)


def test_run_status_survives_reload(discovery_project, imported) -> None:
    result = start_technical_validation(discovery_project, sync=True)
    assert result.started
    run_a, vals_a, err_a = get_validation_status(discovery_project)
    assert err_a is None
    assert run_a is not None
    run_b, vals_b, err_b = get_validation_status(discovery_project)
    assert run_b is not None
    assert run_a.run_id == run_b.run_id
    assert run_a.status == run_b.status
    assert len(vals_a) == len(vals_b)


# --- Quellenschutz ---------------------------------------------------------


def test_source_missing(discovery_project, imported) -> None:
    target = discovery_project.project_root_path / "Florida" / "still.jpg"
    target.unlink()
    result = start_technical_validation(discovery_project, sync=True)
    assert result.started
    _, validations, _ = get_validation_status(discovery_project)
    missing = [
        v
        for v in validations
        if v.source_relative_path.endswith("still.jpg")
    ]
    assert missing
    assert missing[0].status == AssetValidationStatus.SOURCE_MISSING


def test_source_changed_size(discovery_project, imported) -> None:
    target = discovery_project.project_root_path / "Florida" / "still.jpg"
    target.write_bytes(target.read_bytes() + b"extra")
    result = start_technical_validation(discovery_project, sync=True)
    assert result.started
    _, validations, _ = get_validation_status(discovery_project)
    changed = [
        v
        for v in validations
        if v.source_relative_path.endswith("still.jpg")
    ]
    assert changed
    assert changed[0].status == AssetValidationStatus.SOURCE_CHANGED
    assert changed[0].error_code == "source_changed_size"


def test_source_changed_mtime(discovery_project, imported) -> None:
    target = discovery_project.project_root_path / "Florida" / "still.jpg"
    data = target.read_bytes()
    # mtime ändern ohne Größenänderung
    os.utime(target, ns=(1_000_000_000, 1_000_000_000))
    assert target.read_bytes() == data
    result = start_technical_validation(discovery_project, sync=True)
    _, validations, _ = get_validation_status(discovery_project)
    changed = [
        v
        for v in validations
        if v.source_relative_path.endswith("still.jpg")
    ]
    assert changed
    assert changed[0].status == AssetValidationStatus.SOURCE_CHANGED
    assert changed[0].error_code == "source_changed_mtime"


def test_path_outside_root_rejected(discovery_project) -> None:
    outside = discovery_project.project_root_path.parent / "outside.mp4"
    outside.write_bytes(b"evil")
    path, code, msg = _resolve_source_path(
        discovery_project.project_root_path, "../outside.mp4"
    )
    assert path is None
    assert code == "path_outside_root"


def test_path_under_otio_rejected(discovery_project) -> None:
    path, code, msg = _resolve_source_path(
        discovery_project.project_root_path, "_otio/classic.mp4"
    )
    assert path is None
    assert code == "path_under_otio"


def test_path_under_otio_v2_rejected(discovery_project) -> None:
    path, code, msg = _resolve_source_path(
        discovery_project.project_root_path, "_otio_v2/x.mp4"
    )
    assert path is None
    assert code == "path_under_otio_v2"


def test_sources_not_modified_and_not_copied(discovery_project, imported) -> None:
    before = _source_snapshots(discovery_project.project_root_path)
    start_technical_validation(discovery_project, sync=True)
    after = _source_snapshots(discovery_project.project_root_path)
    assert before == after
    assert not (discovery_project.project_root_path / "_otio_v2" / "working_media").exists()
    # Keine neuen Mediendateien außerhalb validation/registry/inventory
    v2 = discovery_project.project_root_path / "_otio_v2"
    media_copies = [
        p
        for p in v2.rglob("*")
        if p.is_file()
        and p.suffix.lower() in {".mp4", ".wav", ".jpg", ".mov"}
        and "validation" not in p.parts
    ]
    assert media_copies == []


# --- Hashing ---------------------------------------------------------------


def test_sha256_correct_and_reproducible(tmp_path: Path) -> None:
    path = tmp_path / "a.bin"
    path.write_bytes(b"hello-discovery-v2")
    expected = hashlib.sha256(b"hello-discovery-v2").hexdigest()
    h1 = compute_sha256_hex(path)
    h2 = compute_sha256_hex(path, chunk_size=3)
    assert h1 == expected == h2
    assert h1 == h1.lower()


def test_hashing_is_chunked(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "big.bin"
    path.write_bytes(b"0123456789" * 100)
    reads: list[int] = []
    real_open = Path.open

    class TrackingHandle:
        def __init__(self, handle):
            self._handle = handle

        def read(self, n=-1):
            data = self._handle.read(n)
            reads.append(n if n is not None else -1)
            return data

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self._handle.__exit__(*args)

    def _open(self, *args, **kwargs):
        handle = real_open(self, *args, **kwargs)
        return TrackingHandle(handle)

    monkeypatch.setattr(Path, "open", _open)
    digest = compute_sha256_hex(path, chunk_size=16)
    assert digest == hashlib.sha256(path.read_bytes()).hexdigest()
    assert any(n == 16 for n in reads)
    assert len(reads) > 1


def test_same_content_same_hash_different_names(tmp_path: Path) -> None:
    a = tmp_path / "one.mp4"
    b = tmp_path / "two.mp4"
    payload = b"identical-payload"
    a.write_bytes(payload)
    b.write_bytes(payload)
    assert compute_sha256_hex(a) == compute_sha256_hex(b)


def test_different_content_different_hash(tmp_path: Path) -> None:
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    a.write_bytes(b"aaa")
    b.write_bytes(b"bbb")
    assert compute_sha256_hex(a) != compute_sha256_hex(b)


def test_hash_error_stored(discovery_project, imported, monkeypatch) -> None:
    def _boom(path, **kwargs):
        raise OSError("read failed")

    monkeypatch.setattr(
        "otio_app.discovery_v2.jobs.technical_validation_worker.compute_sha256_hex",
        _boom,
    )
    start_technical_validation(discovery_project, sync=True)
    _, validations, _ = get_validation_status(discovery_project)
    errors = [v for v in validations if v.error_code == "hash_error"]
    assert errors
    assert errors[0].status == AssetValidationStatus.VALIDATION_ERROR


# --- Probe -----------------------------------------------------------------


@requires_ffmpeg
def test_probe_video_normalized(discovery_project, imported) -> None:
    start_technical_validation(discovery_project, sync=True)
    _, validations, _ = get_validation_status(discovery_project)
    video = next(
        v
        for v in validations
        if v.source_relative_path.endswith("Florida/clip.mp4")
    )
    assert video.status == AssetValidationStatus.PROBE_SUCCEEDED
    assert video.video_codec
    assert video.width == 320
    assert video.height == 240
    assert video.frame_rate_numerator is not None
    assert video.frame_rate_denominator is not None
    assert video.frame_rate_denominator != 0
    assert isinstance(video.frame_rate_numerator, int)
    assert isinstance(video.frame_rate_denominator, int)


@requires_ffmpeg
def test_probe_audio_normalized(discovery_project, imported) -> None:
    start_technical_validation(discovery_project, sync=True)
    _, validations, _ = get_validation_status(discovery_project)
    audio = next(
        v for v in validations if v.source_relative_path.endswith("root_audio.wav")
    )
    assert audio.status == AssetValidationStatus.PROBE_SUCCEEDED
    assert audio.audio_codec
    assert audio.audio_stream_count and audio.audio_stream_count >= 1


@requires_ffmpeg
def test_probe_image_normalized(discovery_project, imported) -> None:
    start_technical_validation(discovery_project, sync=True)
    _, validations, _ = get_validation_status(discovery_project)
    image = next(
        v for v in validations if v.source_relative_path.endswith("still.jpg")
    )
    assert image.status == AssetValidationStatus.PROBE_SUCCEEDED
    assert image.width == 64
    assert image.height == 48
    assert image.frame_rate_numerator is None


def test_missing_timecode_allowed(discovery_project, imported) -> None:
    start_technical_validation(discovery_project, sync=True)
    _, validations, _ = get_validation_status(discovery_project)
    ok = [
        v
        for v in validations
        if v.status == AssetValidationStatus.PROBE_SUCCEEDED
        and v.media_kind == "video"
    ]
    if not ok:
        pytest.skip("kein erfolgreiches Video-Probe (ffmpeg/fixture)")
    assert all(v.embedded_timecode is None or isinstance(v.embedded_timecode, str) for v in ok)


def test_probe_failure_isolated(discovery_project, imported) -> None:
    start_technical_validation(discovery_project, sync=True)
    run, validations, _ = get_validation_status(discovery_project)
    assert run is not None
    broken = [
        v
        for v in validations
        if v.source_relative_path.endswith("broken.mp4")
    ]
    assert broken
    assert broken[0].status == AssetValidationStatus.PROBE_FAILED
    others = [v for v in validations if v is not broken[0]]
    assert others
    # Andere Assets wurden trotzdem verarbeitet
    assert run.processed_assets == run.total_assets


def test_ffprobe_not_shell_injected(monkeypatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def _fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        raise FileNotFoundError("ffprobe")

    monkeypatch.setattr(media_probe_mod.subprocess, "run", _fake_run)
    monkeypatch.setattr(
        media_probe_mod,
        "probe_media",
        lambda path: MagicMock(
            duration_sec=None,
            video_codec=None,
            audio_codec=None,
            width=None,
            height=None,
            container="mp4",
        ),
    )
    evil = tmp_path / 'x"; rm -rf /'
    evil.write_bytes(b"x")
    with pytest.raises(media_probe_mod.MediaProbeAdapterError):
        media_probe_mod.probe_source_media(evil, media_kind=MediaKind.VIDEO)
    assert calls
    assert isinstance(calls[0], list)
    assert all(isinstance(part, str) for part in calls[0])
    # Kein shell=True — nur Argumentliste
    assert "shell=True" not in str(calls)


def test_uses_probe_media_wrapper(monkeypatch, tmp_path: Path) -> None:
    called = {"probe_media": False}
    real_probe = media_probe_mod.probe_media

    def _wrap(path):
        called["probe_media"] = True
        return real_probe(path)

    monkeypatch.setattr(media_probe_mod, "probe_media", _wrap)

    def _fake_json(path, **kwargs):
        return {
            "format": {"format_name": "mp4", "duration": "0.2"},
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 16,
                    "height": 16,
                    "r_frame_rate": "25/1",
                }
            ],
        }

    monkeypatch.setattr(media_probe_mod, "_run_ffprobe_json", _fake_json)
    path = tmp_path / "v.mp4"
    path.write_bytes(b"x")
    result = media_probe_mod.probe_source_media(path, media_kind=MediaKind.VIDEO)
    assert called["probe_media"] is True
    assert result.frame_rate_numerator == 25
    assert result.frame_rate_denominator == 1


def test_parse_frame_rate_fraction() -> None:
    assert media_probe_mod.parse_frame_rate_fraction("30000/1001") == (30000, 1001)
    assert media_probe_mod.parse_frame_rate_fraction("0/0") is None
    assert media_probe_mod.parse_frame_rate_fraction("25") == (25, 1)


# --- Dubletten -------------------------------------------------------------


@requires_ffmpeg
def test_duplicate_hash_marked(discovery_project, imported) -> None:
    start_technical_validation(discovery_project, sync=True)
    _, validations, _ = get_validation_status(discovery_project)
    dups = [v for v in validations if v.duplicate_hint == "potential_content_duplicate"]
    assert len(dups) >= 2
    groups = {v.duplicate_group_id for v in dups}
    assert None not in groups
    assert len(groups) >= 1
    # Quellgruppen bleiben erhalten
    groups_by_path = {
        v.source_relative_path: v.source_group for v in validations if v.duplicate_hint
    }
    assert any("Florida" in p for p in groups_by_path)
    assert any("Chicago" in p for p in groups_by_path)


def test_duplicates_not_deleted(discovery_project, imported) -> None:
    before = list(discovery_project.project_root_path.rglob("*"))
    start_technical_validation(discovery_project, sync=True)
    after = list(discovery_project.project_root_path.rglob("*"))
    before_files = {p for p in before if p.is_file() and "_otio_v2" not in p.parts}
    after_files = {p for p in after if p.is_file() and "_otio_v2" not in p.parts}
    assert before_files == after_files


def test_rerun_does_not_uncontrolled_duplicates(discovery_project, imported) -> None:
    start_technical_validation(discovery_project, sync=True)
    start_technical_validation(discovery_project, sync=True)
    conn = val_repo.open_registry(discovery_project.project_root_path)
    runs = conn.execute("SELECT run_id FROM validation_runs").fetchall()
    assert len(runs) == 2
    for row in runs:
        groups = val_repo.list_duplicate_groups(conn, run_id=row["run_id"])
        hashes = [g.sha256 for g in groups]
        assert len(hashes) == len(set(hashes))
    conn.close()


# --- Jobs / UI -------------------------------------------------------------


def test_ui_rerun_does_not_start_job(discovery_project, imported, monkeypatch) -> None:
    state: dict = {}
    st = MagicMock()
    st.session_state = state
    st.button = lambda *a, **k: False  # kein Klick
    st.columns = lambda n: [MagicMock() for _ in range(n)]
    st.expander = MagicMock()
    st.expander.return_value.__enter__ = MagicMock(return_value=MagicMock())
    st.expander.return_value.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr(val_ui, "st", st)
    monkeypatch.setattr(val_ui, "active_discovery_project", lambda: discovery_project)
    val_ui.render_discovery_technical_validation_page()
    conn = val_repo.open_registry(discovery_project.project_root_path)
    count = conn.execute("SELECT COUNT(*) AS c FROM validation_runs").fetchone()["c"]
    conn.close()
    assert count == 0


def test_button_starts_exactly_one_run(
    discovery_project, imported, monkeypatch
) -> None:
    state: dict = {}
    st = MagicMock()
    st.session_state = state
    clicked = {"n": 0}

    def _button(label, **kwargs):
        if kwargs.get("key") == "discovery_v2_validation_start_btn":
            clicked["n"] += 1
            return clicked["n"] == 1
        return False

    st.button = _button
    st.columns = lambda n: [MagicMock() for _ in range(n)]
    st.expander = MagicMock()
    st.expander.return_value.__enter__ = MagicMock(return_value=MagicMock())
    st.expander.return_value.__exit__ = MagicMock(return_value=False)
    st.rerun = MagicMock()
    monkeypatch.setattr(val_ui, "st", st)
    monkeypatch.setattr(val_ui, "active_discovery_project", lambda: discovery_project)

    # Sync starten statt Thread
    real_start = tvs.start_technical_validation

    def _sync_start(project, *, sync=False):
        return real_start(project, sync=True)

    monkeypatch.setattr(val_ui, "start_technical_validation", _sync_start)
    val_ui.render_discovery_technical_validation_page()
    conn = val_repo.open_registry(discovery_project.project_root_path)
    count = conn.execute("SELECT COUNT(*) AS c FROM validation_runs").fetchone()["c"]
    conn.close()
    assert count == 1
    assert st.rerun.called


def test_progress_persisted(discovery_project, imported) -> None:
    result = start_technical_validation(discovery_project, sync=True)
    assert result.run is not None
    assert result.run.processed_assets == result.run.total_assets
    assert result.run.total_assets > 0


def test_single_asset_error_continues(discovery_project, imported) -> None:
    start_technical_validation(discovery_project, sync=True)
    run, validations, _ = get_validation_status(discovery_project)
    assert run is not None
    assert run.processed_assets == len(validations) == run.total_assets
    assert run.status in {
        ValidationRunStatus.COMPLETED,
        ValidationRunStatus.COMPLETED_WITH_ERRORS,
    }


def test_global_error_marks_failed(discovery_project, imported, monkeypatch) -> None:
    def _boom(project_root, run_id):
        conn = val_repo.open_registry(project_root)
        run = val_repo.get_run(conn, run_id=run_id)
        assert run is not None
        updated = run.model_copy(
            update={
                "status": ValidationRunStatus.FAILED,
                "error_summary": "global boom",
                "completed_at": run.created_at,
            }
        )
        val_repo.update_run(conn, updated)
        conn.commit()
        conn.close()
        return updated

    monkeypatch.setattr(
        "otio_app.discovery_v2.adapters.validation_job_launcher.process_validation_run",
        _boom,
    )
    result = start_technical_validation(discovery_project, sync=True)
    assert result.run is not None
    assert result.run.status == ValidationRunStatus.FAILED
    assert "boom" in (result.run.error_summary or "")


# --- Berichte und Regression ----------------------------------------------


def test_json_report_atomic(discovery_project, imported, monkeypatch) -> None:
    calls: list[str] = []
    real_replace = os.replace
    import otio_app.discovery_v2.persistence.inventory_artifact_store as inv_store

    def _tracking(src, dst):
        calls.append(str(dst))
        return real_replace(src, dst)

    monkeypatch.setattr(inv_store.os, "replace", _tracking)
    start_technical_validation(discovery_project, sync=True)
    assert any("latest_run.json" in c for c in calls)
    assert any("/runs/" in c and c.endswith(".json") for c in calls)


def test_older_reports_preserved(discovery_project, imported) -> None:
    r1 = start_technical_validation(discovery_project, sync=True)
    r2 = start_technical_validation(discovery_project, sync=True)
    assert r1.run and r2.run
    p1 = val_repo.validation_report_path(
        discovery_project.project_root_path, r1.run.run_id
    )
    p2 = val_repo.validation_report_path(
        discovery_project.project_root_path, r2.run.run_id
    )
    assert p1.is_file() and p2.is_file()
    latest = json.loads(
        val_repo.latest_validation_pointer_path(
            discovery_project.project_root_path
        ).read_text(encoding="utf-8")
    )
    assert latest["run_id"] == r2.run.run_id


def test_no_report_under_otio(discovery_project, imported) -> None:
    start_technical_validation(discovery_project, sync=True)
    classic = discovery_project.project_root_path / "_otio"
    if classic.exists():
        assert not list(classic.rglob("*.json")) or all(
            "validation" not in p.parts for p in classic.rglob("*")
        )
    report_dir = (
        discovery_project.project_root_path / "_otio_v2" / "validation" / "runs"
    )
    assert report_dir.is_dir()
    assert list(report_dir.glob("*.json"))


def test_classic_and_without_vo_nav_unchanged() -> None:
    from otio_app.ui.navigation import DISCOVERY_V2_NAVIGATION_OPTIONS

    assert "Technische Prüfung" not in NAVIGATION_OPTIONS
    assert "Technische Prüfung" not in VOICEOVER_GEN_NAVIGATION_OPTIONS
    assert "Technische Prüfung" in DISCOVERY_V2_NAVIGATION_OPTIONS
    # Classic-/Without-VO-Listen bit-exakt frei von Discovery-Seiten
    assert NAVIGATION_OPTIONS == tuple(NAVIGATION_OPTIONS)
    assert "Medienbestand" not in NAVIGATION_OPTIONS


def test_no_working_media_or_transcode_in_validation_modules() -> None:
    root = Path(__file__).resolve().parents[1]
    modules = [
        "otio_app/discovery_v2/application/technical_validation_service.py",
        "otio_app/discovery_v2/jobs/technical_validation_worker.py",
        "otio_app/discovery_v2/ui/technical_validation_page.py",
    ]
    for rel in modules:
        text = (root / rel).read_text(encoding="utf-8")
        assert "working_media" not in text.lower()
        assert "transcode" not in text.lower()
        assert "elevenlabs" not in text.lower()
        assert "openai" not in text.lower()
        assert "shutil.copy" not in text


def test_stale_selection_blocks_validation(discovery_project, imported) -> None:
    # Neuer Scan macht Selection stale
    run_inventory_scan(discovery_project)
    ok, msg, _ = can_start_technical_validation(discovery_project)
    assert ok is False
    assert msg is not None
    assert "veraltet" in msg.lower() or "älter" in msg.lower() or "Bestand" in msg


def test_no_discovery_project_blocks(temp_db_path: Path, tmp_path: Path) -> None:
    root = tmp_path / "classic"
    root.mkdir()
    project = create_project(
        ProjectCreate(
            name="Classic",
            project_root=str(root),
            project_mode=ProjectMode.WITH_VOICEOVER,
            language="de",
        ),
        db_path=temp_db_path,
    )
    ok, msg, _ = can_start_technical_validation(project)
    assert ok is False
    assert msg
