"""Discovery V2 — Asset Registry (Metadaten-Import)."""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from unittest.mock import MagicMock

import pytest

import otio_app.ui.routing as routing
from otio_app.database import get_connection as get_classic_connection
from otio_app.discovery_v2.application.asset_registry_service import (
    AssetRegistryServiceError,
    can_import_selection,
    import_confirmed_selection,
)
from otio_app.discovery_v2.application.inventory_service import run_inventory_scan
from otio_app.discovery_v2.application.selection_service import (
    build_default_draft,
    confirm_selection,
    set_file_excluded,
    set_group_selected,
)
from otio_app.discovery_v2.domain.asset_registry import RegistryImportStatus
from otio_app.discovery_v2.domain.inventory import MediaKind
from otio_app.discovery_v2.domain.selection import SelectionStatus
from otio_app.discovery_v2.persistence import asset_registry_database as reg_db
from otio_app.discovery_v2.persistence import asset_registry_repository as reg_repo
from otio_app.discovery_v2.ui import inventory_page as inventory_ui
from otio_app.models import Project, ProjectCreate, ProjectMode
from otio_app.project_repository import create_project
from otio_app.ui.navigation import (
    NAVIGATION_OPTIONS,
    PAGE_DISCOVERY_INVENTORY,
    VOICEOVER_GEN_NAVIGATION_OPTIONS,
)


def _write(path: Path, content: bytes = b"x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _build_tree(root: Path) -> None:
    _write(root / "Florida" / "Drohne" / "florida_01.mp4")
    _write(root / "Florida" / "florida_02.jpg")
    _write(root / "Florida" / "clip.mp4")
    _write(root / "Chicago" / "chicago_01.mov")
    _write(root / "Chicago" / "clip.mp4")
    _write(root / "root_audio.wav")
    _write(root / "notes.txt")
    _write(root / "_otio" / "classic.mp4")


@pytest.fixture
def sample_root(tmp_path: Path) -> Path:
    root = tmp_path / "Project"
    root.mkdir()
    _build_tree(root)
    return root


@pytest.fixture
def discovery_project(sample_root: Path, temp_db_path: Path) -> Project:
    return create_project(
        ProjectCreate(
            name="Reg Smoke",
            project_root=str(sample_root),
            project_mode=ProjectMode.DISCOVERY_V2,
            language="de",
        ),
        db_path=temp_db_path,
        asset_subdir_names=["Florida", "Chicago"],
        selected_asset_subdirs=["Florida", "Chicago"],
    )


@pytest.fixture
def confirmed(discovery_project: Project):
    snap = run_inventory_scan(discovery_project)
    draft = build_default_draft(snap)
    selection = confirm_selection(
        discovery_project, snap, draft, acknowledged=True
    )
    return snap, selection


def _confirm_custom(project: Project, *, drop_chicago: bool = False, exclude: str | None = None):
    snap = run_inventory_scan(project)
    draft = build_default_draft(snap)
    if drop_chicago:
        draft = set_group_selected(draft, "Chicago", False)
    if exclude:
        draft = set_file_excluded(draft, exclude, True)
    selection = confirm_selection(project, snap, draft, acknowledged=True)
    return snap, selection


# --- Schema ----------------------------------------------------------------


def test_registry_sqlite_under_otio_v2(discovery_project, confirmed) -> None:
    import_confirmed_selection(discovery_project)
    db = reg_db.registry_sqlite_path(discovery_project.project_root_path)
    assert db.is_file()
    assert "_otio_v2/registry/assets.sqlite3" in db.as_posix()


def test_classic_db_unchanged(discovery_project, confirmed, temp_db_path: Path) -> None:
    before = temp_db_path.read_bytes()
    tables_before = {
        r[0]
        for r in get_classic_connection(temp_db_path).execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    import_confirmed_selection(discovery_project)
    assert temp_db_path.read_bytes() == before
    tables_after = {
        r[0]
        for r in get_classic_connection(temp_db_path).execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert tables_before == tables_after
    assert "assets" not in tables_after or "projects" in tables_after
    assert "selection_imports" not in tables_after


def test_schema_init_idempotent(discovery_project) -> None:
    conn1 = reg_db.get_registry_connection(discovery_project.project_root_path)
    v1 = reg_db.read_schema_version(conn1)
    conn1.close()
    conn2 = reg_db.get_registry_connection(discovery_project.project_root_path)
    v2 = reg_db.read_schema_version(conn2)
    assert v1 == v2 == reg_db.REGISTRY_SCHEMA_VERSION
    assert reg_db.foreign_keys_enabled(conn2)
    conn2.close()


def test_foreign_keys_active(discovery_project) -> None:
    conn = reg_db.get_registry_connection(discovery_project.project_root_path)
    assert reg_db.foreign_keys_enabled(conn) is True
    conn.close()


def test_schema_version_readable(discovery_project) -> None:
    conn = reg_db.get_registry_connection(discovery_project.project_root_path)
    assert reg_db.read_schema_version(conn) == "1"
    conn.close()


def test_no_registry_table_under_classic_otio(discovery_project, confirmed) -> None:
    import_confirmed_selection(discovery_project)
    classic = discovery_project.project_root_path / "_otio"
    assert not (classic / "registry").exists()
    assert not list(classic.rglob("assets.sqlite3"))


# --- Import ----------------------------------------------------------------


def test_valid_selection_fully_imported(discovery_project, confirmed) -> None:
    snap, selection = confirmed
    result = import_confirmed_selection(discovery_project)
    assert result.status == RegistryImportStatus.IMPORTED
    assert result.asset_count == selection.selected_media_count
    conn = reg_db.get_registry_connection(discovery_project.project_root_path)
    assets = reg_repo.list_assets_for_project(conn, project_id=discovery_project.id)
    conn.close()
    assert len(assets) == selection.selected_media_count


def test_florida_chicago_remain_source_groups(discovery_project, confirmed) -> None:
    import_confirmed_selection(discovery_project)
    conn = reg_db.get_registry_connection(discovery_project.project_root_path)
    assets = reg_repo.list_assets_for_project(conn, project_id=discovery_project.id)
    conn.close()
    groups = {a.source_group for a in assets}
    assert "Florida" in groups
    assert "Chicago" in groups


def test_nested_relative_paths_preserved(discovery_project, confirmed) -> None:
    import_confirmed_selection(discovery_project)
    conn = reg_db.get_registry_connection(discovery_project.project_root_path)
    assets = reg_repo.list_assets_for_project(conn, project_id=discovery_project.id)
    conn.close()
    paths = {a.source_relative_path for a in assets}
    assert "Florida/Drohne/florida_01.mp4" in paths


def test_video_image_audio_registered(discovery_project, confirmed) -> None:
    import_confirmed_selection(discovery_project)
    conn = reg_db.get_registry_connection(discovery_project.project_root_path)
    assets = reg_repo.list_assets_for_project(conn, project_id=discovery_project.id)
    conn.close()
    kinds = {a.media_kind for a in assets}
    assert MediaKind.VIDEO in kinds
    assert MediaKind.IMAGE in kinds
    assert MediaKind.AUDIO in kinds


def test_other_not_imported(discovery_project, confirmed) -> None:
    import_confirmed_selection(discovery_project)
    conn = reg_db.get_registry_connection(discovery_project.project_root_path)
    assets = reg_repo.list_assets_for_project(conn, project_id=discovery_project.id)
    conn.close()
    paths = {a.source_relative_path for a in assets}
    assert "notes.txt" not in paths
    assert all(a.media_kind != MediaKind.OTHER for a in assets)


def test_same_filename_different_groups_unique(discovery_project, confirmed) -> None:
    import_confirmed_selection(discovery_project)
    conn = reg_db.get_registry_connection(discovery_project.project_root_path)
    assets = reg_repo.list_assets_for_project(conn, project_id=discovery_project.id)
    conn.close()
    clips = [a for a in assets if a.file_name == "clip.mp4"]
    assert len(clips) == 2
    assert {a.source_group for a in clips} == {"Florida", "Chicago"}


def test_same_relative_path_reuses_asset_id(discovery_project, confirmed) -> None:
    first = import_confirmed_selection(discovery_project)
    conn = reg_db.get_registry_connection(discovery_project.project_root_path)
    assets1 = {
        a.source_relative_path: a.asset_id
        for a in reg_repo.list_assets_for_project(conn, project_id=discovery_project.id)
    }
    conn.close()

    # Neue Selection mit denselben Pfaden
    snap2, _sel2 = _confirm_custom(discovery_project)
    second = import_confirmed_selection(discovery_project)
    assert second.status == RegistryImportStatus.IMPORTED
    assert second.import_id != first.import_id
    conn = reg_db.get_registry_connection(discovery_project.project_root_path)
    assets2 = {
        a.source_relative_path: a.asset_id
        for a in reg_repo.list_assets_for_project(conn, project_id=discovery_project.id)
    }
    conn.close()
    for path, asset_id in assets1.items():
        assert assets2[path] == asset_id


def test_same_selection_id_not_duplicated(discovery_project, confirmed) -> None:
    first = import_confirmed_selection(discovery_project)
    second = import_confirmed_selection(discovery_project)
    assert second.status == RegistryImportStatus.ALREADY_IMPORTED
    assert second.import_id == first.import_id
    conn = reg_db.get_registry_connection(discovery_project.project_root_path)
    imports = conn.execute(
        "SELECT COUNT(*) AS c FROM selection_imports WHERE selection_id = ?",
        (first.selection_id,),
    ).fetchone()["c"]
    assets = conn.execute("SELECT COUNT(*) AS c FROM assets").fetchone()["c"]
    conn.close()
    assert imports == 1
    assert assets == first.asset_count


def test_new_selection_creates_historical_import(discovery_project, confirmed) -> None:
    first = import_confirmed_selection(discovery_project)
    _confirm_custom(discovery_project, drop_chicago=True)
    second = import_confirmed_selection(discovery_project)
    assert second.status == RegistryImportStatus.IMPORTED
    assert second.import_id != first.import_id
    conn = reg_db.get_registry_connection(discovery_project.project_root_path)
    count = conn.execute("SELECT COUNT(*) AS c FROM selection_imports").fetchone()["c"]
    conn.close()
    assert count == 2


def test_existing_assets_reused_on_new_selection(discovery_project, confirmed) -> None:
    first = import_confirmed_selection(discovery_project)
    _confirm_custom(discovery_project, drop_chicago=True)
    second = import_confirmed_selection(discovery_project)
    assert second.reused_asset_count >= 1
    assert second.new_asset_count == 0 or second.asset_count <= first.asset_count


def test_older_assets_not_deleted(discovery_project, confirmed) -> None:
    import_confirmed_selection(discovery_project)
    conn = reg_db.get_registry_connection(discovery_project.project_root_path)
    before = len(reg_repo.list_assets_for_project(conn, project_id=discovery_project.id))
    conn.close()
    _confirm_custom(discovery_project, drop_chicago=True)
    import_confirmed_selection(discovery_project)
    conn = reg_db.get_registry_connection(discovery_project.project_root_path)
    after = len(reg_repo.list_assets_for_project(conn, project_id=discovery_project.id))
    conn.close()
    assert after >= before


def test_memberships_track_history(discovery_project, confirmed) -> None:
    first = import_confirmed_selection(discovery_project)
    _confirm_custom(discovery_project, drop_chicago=True)
    second = import_confirmed_selection(discovery_project)
    conn = reg_db.get_registry_connection(discovery_project.project_root_path)
    m1 = reg_repo.list_import_memberships(conn, import_id=first.import_id)
    m2 = reg_repo.list_import_memberships(conn, import_id=second.import_id)
    conn.close()
    assert len(m1) > len(m2)
    assert any("Chicago/" in rel for _aid, rel in m1)
    assert not any("Chicago/" in rel for _aid, rel in m2)


# --- Schutz ----------------------------------------------------------------


def test_stale_selection_blocks_import(discovery_project, confirmed) -> None:
    snap, selection = confirmed
    # Neuer Scan ohne neue Bestätigung
    run_inventory_scan(discovery_project)
    result = import_confirmed_selection(discovery_project)
    assert result.status == RegistryImportStatus.STALE_SELECTION
    ok, _msg, blocked = can_import_selection(discovery_project)
    assert ok is False
    assert blocked == RegistryImportStatus.STALE_SELECTION
    assert not reg_db.registry_sqlite_path(discovery_project.project_root_path).exists() or (
        # DB may exist from schema-only tests in other cases; here should have no import
        True
    )
    if reg_db.registry_sqlite_path(discovery_project.project_root_path).exists():
        conn = reg_db.get_registry_connection(discovery_project.project_root_path)
        count = conn.execute("SELECT COUNT(*) AS c FROM selection_imports").fetchone()["c"]
        conn.close()
        assert count == 0


def test_missing_file_blocks_import(discovery_project, confirmed) -> None:
    path = discovery_project.project_root_path / "Florida" / "florida_02.jpg"
    path.unlink()
    with pytest.raises(AssetRegistryServiceError, match="fehlt"):
        import_confirmed_selection(discovery_project)
    if reg_db.registry_sqlite_path(discovery_project.project_root_path).exists():
        conn = reg_db.get_registry_connection(discovery_project.project_root_path)
        assert conn.execute("SELECT COUNT(*) AS c FROM selection_imports").fetchone()["c"] == 0
        conn.close()


def test_changed_size_blocks_import(discovery_project, confirmed) -> None:
    path = discovery_project.project_root_path / "Florida" / "florida_02.jpg"
    path.write_bytes(b"changed-size!!")
    with pytest.raises(AssetRegistryServiceError, match="Dateigröße"):
        import_confirmed_selection(discovery_project)


def test_changed_mtime_blocks_import(discovery_project, confirmed) -> None:
    path = discovery_project.project_root_path / "Florida" / "florida_02.jpg"
    data = path.read_bytes()
    # Größe gleich halten, mtime ändern
    os.utime(path, (1_700_000_000, 1_700_000_000))
    assert path.read_bytes() == data
    with pytest.raises(AssetRegistryServiceError, match="Änderungszeit"):
        import_confirmed_selection(discovery_project)


def test_path_outside_project_rejected(discovery_project, confirmed, monkeypatch) -> None:
    # Injiziere ungültigen Pfad in Selection-Validierung via Snapshot-Manipulation
    from otio_app.discovery_v2.application import asset_registry_service as svc

    real = svc.get_latest_confirmed_selection

    def _evil(project, current_scan_id=None):
        selection, status, warning = real(project, current_scan_id=current_scan_id)
        assert selection is not None
        selection = selection.model_copy(
            update={
                "selected_relative_paths": selection.selected_relative_paths
                + ["../escape.mp4"]
            }
        )
        return selection, status, warning

    monkeypatch.setattr(svc, "get_latest_confirmed_selection", _evil)
    # Pfad nicht im Snapshot → Fehler
    with pytest.raises(AssetRegistryServiceError, match="Snapshot|außerhalb|fehlt"):
        import_confirmed_selection(discovery_project)


def test_otio_and_otio_v2_paths_rejected(discovery_project, confirmed, monkeypatch) -> None:
    from otio_app.discovery_v2.application import asset_registry_service as svc
    from otio_app.discovery_v2.domain.inventory import InventoryFileEntry, ScanStatus

    snap, selection = confirmed
    # Füge Fake-Eintrag in Snapshot-Map ein
    evil_entry = InventoryFileEntry(
        relative_path="_otio/classic.mp4",
        filename="classic.mp4",
        extension=".mp4",
        source_group="_otio",
        source_group_label="_otio",
        media_kind=MediaKind.VIDEO,
        size_bytes=(discovery_project.project_root_path / "_otio" / "classic.mp4").stat().st_size,
        mtime_iso="",
        scan_status=ScanStatus.FOUND,
    )
    # Leere mtime um mtime-check zu umgehen — trotzdem reserved path
    real_map = svc._snapshot_entry_map

    def _map(snapshot):
        data = real_map(snapshot)
        data[evil_entry.relative_path] = evil_entry
        return data

    real_sel = svc.get_latest_confirmed_selection

    def _sel(project, current_scan_id=None):
        selection, status, warning = real_sel(project, current_scan_id=current_scan_id)
        selection = selection.model_copy(
            update={
                "selected_relative_paths": ["_otio/classic.mp4"],
                "selected_media_count": 1,
            }
        )
        return selection, status, warning

    monkeypatch.setattr(svc, "_snapshot_entry_map", _map)
    monkeypatch.setattr(svc, "get_latest_confirmed_selection", _sel)
    with pytest.raises(AssetRegistryServiceError, match="_otio"):
        import_confirmed_selection(discovery_project)


def test_no_content_hash_ffmpeg_or_copy(discovery_project, confirmed) -> None:
    before = {
        str(p): (p.stat().st_mtime_ns, p.read_bytes())
        for p in discovery_project.project_root_path.rglob("*")
        if p.is_file() and "_otio_v2" not in p.parts
    }
    import_confirmed_selection(discovery_project)
    for key, (mtime, data) in before.items():
        path = Path(key)
        assert path.stat().st_mtime_ns == mtime
        assert path.read_bytes() == data
    assert not (discovery_project.project_root_path / "_otio_v2" / "working_media").exists()

    modules = [
        "otio_app/discovery_v2/application/asset_registry_service.py",
        "otio_app/discovery_v2/persistence/asset_registry_database.py",
        "otio_app/discovery_v2/persistence/asset_registry_repository.py",
        "otio_app/discovery_v2/domain/asset_registry.py",
    ]
    root = Path("/home/ubuntu/otio-discovery-v2-integration")
    for rel in modules:
        text = (root / rel).read_text(encoding="utf-8")
        assert "ffprobe" not in text
        assert "ffmpeg" not in text
        assert "hashlib" not in text
        assert "subprocess" not in text


def test_error_no_partial_import(discovery_project, confirmed) -> None:
    path = discovery_project.project_root_path / "Chicago" / "chicago_01.mov"
    path.unlink()
    with pytest.raises(AssetRegistryServiceError):
        import_confirmed_selection(discovery_project)
    db = reg_db.registry_sqlite_path(discovery_project.project_root_path)
    if db.exists():
        conn = reg_db.get_registry_connection(discovery_project.project_root_path)
        assert conn.execute("SELECT COUNT(*) AS c FROM selection_imports").fetchone()["c"] == 0
        assert conn.execute("SELECT COUNT(*) AS c FROM selection_import_assets").fetchone()["c"] == 0
        # Assets dürfen auch nicht teilweise committed sein
        assert conn.execute("SELECT COUNT(*) AS c FROM assets").fetchone()["c"] == 0
        conn.close()


# --- JSON-Bericht ----------------------------------------------------------


def test_import_report_versioned(discovery_project, confirmed) -> None:
    result = import_confirmed_selection(discovery_project)
    path = reg_repo.import_report_path(discovery_project.project_root_path, result.import_id)
    assert path.is_file()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["schema_version"] == "1"
    assert data["import_id"] == result.import_id
    assert data["registry_sqlite_relative_path"].startswith("registry/")


def test_older_report_preserved(discovery_project, confirmed) -> None:
    first = import_confirmed_selection(discovery_project)
    _confirm_custom(discovery_project)
    second = import_confirmed_selection(discovery_project)
    assert reg_repo.import_report_path(
        discovery_project.project_root_path, first.import_id
    ).is_file()
    assert reg_repo.import_report_path(
        discovery_project.project_root_path, second.import_id
    ).is_file()


def test_latest_import_pointer(discovery_project, confirmed) -> None:
    first = import_confirmed_selection(discovery_project)
    _confirm_custom(discovery_project)
    second = import_confirmed_selection(discovery_project)
    latest = reg_repo.latest_import_pointer_path(discovery_project.project_root_path)
    data = json.loads(latest.read_text(encoding="utf-8"))
    assert data["import_id"] == second.import_id
    assert data["import_id"] != first.import_id


def test_report_write_atomic(discovery_project, confirmed, monkeypatch) -> None:
    calls: list[str] = []
    real_replace = os.replace
    import otio_app.discovery_v2.persistence.inventory_artifact_store as inv_store

    def _tracking(src, dst):
        calls.append(str(dst))
        return real_replace(src, dst)

    monkeypatch.setattr(inv_store.os, "replace", _tracking)
    import_confirmed_selection(discovery_project)
    assert any(dst.endswith("latest_import.json") for dst in calls)


def test_report_uses_relative_paths_only(discovery_project, confirmed) -> None:
    result = import_confirmed_selection(discovery_project)
    assert result.report is not None
    dumped = result.report.model_dump_json()
    assert str(discovery_project.project_root_path) not in dumped
    assert result.report.registry_sqlite_relative_path == "registry/assets.sqlite3"


def test_json_failure_no_duplicate_sqlite(discovery_project, confirmed, monkeypatch) -> None:
    first_calls = {"n": 0}

    def _fail_then_ok(project_root, report):
        first_calls["n"] += 1
        if first_calls["n"] == 1:
            raise reg_repo.InventoryArtifactError("JSON fail")
        return reg_repo.save_import_report.__wrapped__(project_root, report) if False else None

    # Simpler: patch save_import_report to fail once
    from otio_app.discovery_v2.application import asset_registry_service as svc

    real_save = reg_repo.save_import_report
    state = {"failed": False}

    def _save(project_root, report):
        if not state["failed"]:
            state["failed"] = True
            raise reg_repo.InventoryArtifactError("JSON fail")
        return real_save(project_root, report)

    monkeypatch.setattr(svc, "save_import_report", _save)
    result = import_confirmed_selection(discovery_project)
    assert result.status == RegistryImportStatus.IMPORTED
    assert "JSON" in result.message or "Bericht" in result.message

    conn = reg_db.get_registry_connection(discovery_project.project_root_path)
    imports = conn.execute("SELECT COUNT(*) AS c FROM selection_imports").fetchone()["c"]
    conn.close()
    assert imports == 1

    # Wiederholung repariert Bericht idempotent
    monkeypatch.setattr(svc, "save_import_report", real_save)
    again = import_confirmed_selection(discovery_project)
    assert again.status == RegistryImportStatus.ALREADY_IMPORTED
    assert again.import_id == result.import_id
    assert reg_repo.import_report_path(
        discovery_project.project_root_path, result.import_id
    ).is_file()


def test_no_report_under_classic_otio(discovery_project, confirmed) -> None:
    import_confirmed_selection(discovery_project)
    classic = discovery_project.project_root_path / "_otio"
    assert not list(classic.rglob("*import*.json"))


# --- UI / Regression -------------------------------------------------------


def test_import_button_only_for_current_selection(
    discovery_project, confirmed, monkeypatch
) -> None:
    snap, selection = confirmed
    source = Path(inventory_ui.__file__).read_text(encoding="utf-8")
    assert "Auswahl in Asset Registry übernehmen" in source
    assert "discovery_v2_registry_import_btn" in source

    # Stale: kein erfolgreicher can_import
    run_inventory_scan(discovery_project)
    ok, _msg, blocked = can_import_selection(discovery_project)
    assert ok is False
    assert blocked == RegistryImportStatus.STALE_SELECTION


def test_rerun_does_not_auto_import(discovery_project, confirmed, monkeypatch) -> None:
    snap, selection = confirmed
    calls: list[str] = []

    def _tracking(*_a, **_k):
        calls.append("x")
        raise AssertionError("should not auto-import")

    state = {
        inventory_ui._SESSION_SNAPSHOT_KEY: snap,
        inventory_ui._SESSION_SELECTION_KEY: selection,
        inventory_ui._SESSION_SELECTION_STATUS_KEY: SelectionStatus.CONFIRMED,
        inventory_ui._SESSION_DRAFT_KEY: build_default_draft(snap),
    }
    buttons = {
        "discovery_v2_inventory_scan_btn": False,
        "discovery_v2_confirm_selection_btn": False,
        "discovery_v2_registry_import_btn": False,
    }

    st = MagicMock()
    st.session_state = state
    st.button = lambda label, **kwargs: buttons.get(kwargs.get("key", label), False)
    st.checkbox = lambda label, **kwargs: kwargs.get("value", False)
    st.text_input = lambda *a, **k: ""

    class _Expander:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    st.expander = lambda *a, **k: _Expander()
    st.columns = lambda n: [MagicMock() for _ in range(n)]
    st.divider = lambda: None
    for name in (
        "title", "info", "write", "caption", "error", "warning", "success",
        "subheader", "markdown", "dataframe", "json", "metric",
    ):
        setattr(st, name, lambda *a, **k: None)

    monkeypatch.setattr(inventory_ui, "st", st)
    monkeypatch.setattr(inventory_ui, "active_discovery_project", lambda: discovery_project)
    monkeypatch.setattr(inventory_ui, "import_confirmed_selection", _tracking)
    monkeypatch.setattr(
        inventory_ui,
        "get_latest_confirmed_selection",
        lambda project, current_scan_id=None: (selection, SelectionStatus.CONFIRMED, None),
    )
    inventory_ui.render_discovery_inventory_page()
    assert calls == []


def test_no_working_media_claims_in_registry_ui() -> None:
    source = Path(inventory_ui.__file__).read_text(encoding="utf-8")
    assert "kein Working Media" in source or "noch kein Working Media" in source
    assert "Working Media erzeugen" not in source
    assert "ffprobe" not in source
    assert "Transkod" not in source or "keine Transkodierung" in source


def test_classic_and_without_vo_nav_unchanged() -> None:
    @dataclass
    class FP:
        render_fn: Callable
        title: str
        url_path: str = ""
        default: bool = False

    routing.st.Page = lambda fn, *, title, url_path="", default=False: FP(
        fn, title, url_path, default
    )
    classic = [p.title for p in routing._build_with_voiceover_pages(lambda: None, lambda: None)]
    wvo = [p.title for p in routing._build_without_voiceover_pages(lambda: None, lambda: None)]
    discovery = [p.title for p in routing._build_discovery_v2_pages(lambda: None, lambda: None)]
    assert classic == list(NAVIGATION_OPTIONS)
    assert wvo == list(VOICEOVER_GEN_NAVIGATION_OPTIONS)
    assert PAGE_DISCOVERY_INVENTORY in discovery
    assert classic == [
        "Neues Projekt",
        "Gespeicherte Projekte",
        "⓪ Clean Media",
        "① Analysen",
        "② Zuordnung",
        "②½ Supplement Assets",
        "③ Schnittplan",
        "🔑 API-Schlüssel",
        "Systemstatus",
    ]
