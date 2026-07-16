"""Discovery V2 Medienbestand — Scanner, Artefakte, Routing/UI-Isolation."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Callable
from unittest.mock import MagicMock

import pytest

import otio_app.ui.routing as routing
from otio_app.discovery_v2.adapters.filesystem_inventory import (
    InventoryScanError,
    scan_project_filesystem,
)
from otio_app.discovery_v2.application.inventory_service import (
    InventoryServiceError,
    get_latest_inventory,
    run_inventory_scan,
)
from otio_app.discovery_v2.domain.exclusions import (
    EXCLUDED_DIR_NAMES,
    is_excluded_dir_name,
    is_excluded_file_name,
)
from otio_app.discovery_v2.domain.inventory import (
    ROOT_SOURCE_GROUP,
    ROOT_SOURCE_GROUP_LABEL,
    MediaKind,
)
from otio_app.discovery_v2.persistence import inventory_artifact_store as store
from otio_app.discovery_v2.ui import inventory_page as inventory_ui
from otio_app.models import Project, ProjectCreate, ProjectMode
from otio_app.project_repository import create_project
from otio_app.ui.navigation import (
    DISCOVERY_V2_NAVIGATION_OPTIONS,
    NAVIGATION_OPTIONS,
    PAGE_DISCOVERY_INVENTORY,
    VOICEOVER_GEN_NAVIGATION_OPTIONS,
)


@dataclass
class _FakePage:
    render_fn: Callable
    title: str
    url_path: str = ""
    default: bool = False


@pytest.fixture(autouse=True)
def _fake_streamlit_page(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_page(render_fn, *, title, url_path="", default=False):
        return _FakePage(render_fn=render_fn, title=title, url_path=url_path, default=default)

    monkeypatch.setattr(routing.st, "Page", _fake_page)


def _noop() -> None:
    return None


def _write(path: Path, content: bytes = b"x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _build_sample_tree(root: Path) -> dict[str, Path]:
    files = {
        "florida_video": root / "Florida" / "Drohne" / "florida_01.mp4",
        "florida_image": root / "Florida" / "bild.jpg",
        "chicago_video": root / "Chicago" / "chicago_01.mov",
        "chicago_same": root / "Chicago" / "clip.mp4",
        "florida_same": root / "Florida" / "clip.mp4",
        "root_audio": root / "root_audio.wav",
        "root_other": root / "notes.txt",
        "classic": root / "_otio" / "classic_should_not_be_scanned.mp4",
        "v2": root / "_otio_v2" / "v2_should_not_be_scanned.mp4",
        "backup": root / "_otio_v2_backup" / "backup_should_be_scanned.mp4",
        "git": root / ".git" / "config",
        "ds": root / "Florida" / ".DS_Store",
        "nested_otio_backup": root / "Florida" / "_otio_v2_backup" / "video.mov",
    }
    for path in files.values():
        _write(path)
    return files


@pytest.fixture
def sample_root(tmp_path: Path) -> Path:
    root = tmp_path / "Project"
    root.mkdir()
    _build_sample_tree(root)
    return root


@pytest.fixture
def discovery_project(sample_root: Path, temp_db_path: Path) -> Project:
    return create_project(
        ProjectCreate(
            name="Inv Smoke",
            project_root=str(sample_root),
            project_mode=ProjectMode.DISCOVERY_V2,
            language="de",
        ),
        db_path=temp_db_path,
        asset_subdir_names=["Florida", "Chicago"],
        selected_asset_subdirs=["Florida", "Chicago"],
    )


# --- Ausschlussregeln -------------------------------------------------------


def test_exclusion_rules_exact_names_only() -> None:
    assert is_excluded_dir_name("_otio")
    assert is_excluded_dir_name("_otio_v2")
    assert is_excluded_dir_name(".git")
    assert not is_excluded_dir_name("_otio_v2_backup")
    assert "_otio_v2_backup" not in EXCLUDED_DIR_NAMES
    assert is_excluded_file_name(".DS_Store")
    assert is_excluded_file_name("file.part")
    assert is_excluded_file_name("._hidden")


# --- Scanner ----------------------------------------------------------------


def test_florida_chicago_as_separate_source_groups(sample_root: Path) -> None:
    result = scan_project_filesystem(sample_root)
    labels = {g.label for g in result.source_groups}
    assert "Florida" in labels
    assert "Chicago" in labels
    assert ROOT_SOURCE_GROUP_LABEL in labels


def test_nested_relative_paths_preserved(sample_root: Path) -> None:
    result = scan_project_filesystem(sample_root)
    rels = {f.relative_path for f in result.files if f.scan_status.value == "found"}
    assert "Florida/Drohne/florida_01.mp4" in rels
    assert "Florida/bild.jpg" in rels


def test_root_files_under_unsortiert(sample_root: Path) -> None:
    result = scan_project_filesystem(sample_root)
    root_files = [
        f for f in result.files if f.source_group == ROOT_SOURCE_GROUP and f.scan_status.value == "found"
    ]
    names = {f.filename for f in root_files}
    assert "root_audio.wav" in names
    assert all(f.source_group_label == ROOT_SOURCE_GROUP_LABEL for f in root_files)


def test_same_filename_in_different_groups_kept(sample_root: Path) -> None:
    result = scan_project_filesystem(sample_root)
    clips = [f for f in result.files if f.filename == "clip.mp4"]
    assert len(clips) == 2
    groups = {f.source_group for f in clips}
    assert groups == {"Florida", "Chicago"}


def test_media_kinds_classified(sample_root: Path) -> None:
    result = scan_project_filesystem(sample_root)
    by_rel = {f.relative_path: f for f in result.files if f.scan_status.value == "found"}
    assert by_rel["Florida/Drohne/florida_01.mp4"].media_kind == MediaKind.VIDEO
    assert by_rel["Florida/bild.jpg"].media_kind == MediaKind.IMAGE
    assert by_rel["root_audio.wav"].media_kind == MediaKind.AUDIO
    assert by_rel["notes.txt"].media_kind == MediaKind.OTHER


def test_otio_and_otio_v2_excluded(sample_root: Path) -> None:
    result = scan_project_filesystem(sample_root)
    rels = {f.relative_path for f in result.files}
    assert not any(r.startswith("_otio/") for r in rels)
    assert not any(r.startswith("_otio_v2/") for r in rels)
    assert not any(r == "v2_should_not_be_scanned.mp4" for r in rels)
    excluded_paths = {e.relative_path for e in result.excluded}
    assert "_otio" in excluded_paths or any(p.startswith("_otio") for p in excluded_paths)
    assert "_otio_v2" in excluded_paths or any(
        p == "_otio_v2" or p.startswith("_otio_v2/") for p in excluded_paths
    )


def test_git_and_system_files_excluded(sample_root: Path) -> None:
    result = scan_project_filesystem(sample_root)
    rels = {f.relative_path for f in result.files}
    assert not any(".git" in r.split("/") for r in rels)
    assert ".DS_Store" not in {Path(r).name for r in rels}


def test_otio_v2_backup_not_excluded(sample_root: Path) -> None:
    result = scan_project_filesystem(sample_root)
    rels = {f.relative_path for f in result.files if f.scan_status.value == "found"}
    assert "_otio_v2_backup/backup_should_be_scanned.mp4" in rels
    assert "Florida/_otio_v2_backup/video.mov" in rels


def test_symlink_directories_not_followed(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    outside = tmp_path / "outside"
    outside.mkdir()
    _write(outside / "secret.mp4")
    root.mkdir()
    _write(root / "Florida" / "ok.mp4")
    (root / "linkdir").symlink_to(outside, target_is_directory=True)
    result = scan_project_filesystem(root)
    rels = {f.relative_path for f in result.files}
    assert "Florida/ok.mp4" in rels
    assert not any("secret.mp4" in r for r in rels)


def test_symlink_file_outside_root_rejected(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "escape.mp4"
    _write(secret)
    root.mkdir()
    (root / "escape_link.mp4").symlink_to(secret)
    result = scan_project_filesystem(root)
    rels = {f.relative_path for f in result.files}
    assert "escape_link.mp4" not in rels
    assert any("außerhalb" in e.reason for e in result.excluded)


def test_missing_project_root_raises(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    with pytest.raises(InventoryScanError, match="existiert nicht"):
        scan_project_filesystem(missing)


def test_sort_is_deterministic(sample_root: Path) -> None:
    a = scan_project_filesystem(sample_root)
    b = scan_project_filesystem(sample_root)
    assert [f.relative_path for f in a.files] == [f.relative_path for f in b.files]
    assert [g.source_group for g in a.source_groups] == [g.source_group for g in b.source_groups]


def test_source_files_not_modified(sample_root: Path) -> None:
    before = {}
    for path in sample_root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            st = path.stat()
            before[str(path)] = (st.st_mtime_ns, st.st_size, path.read_bytes())
    scan_project_filesystem(sample_root)
    for key, (mtime, size, data) in before.items():
        path = Path(key)
        st = path.stat()
        assert st.st_mtime_ns == mtime
        assert st.st_size == size
        assert path.read_bytes() == data


# --- Artefakte --------------------------------------------------------------


def test_snapshot_written_under_inventory_snapshots(
    discovery_project: Project,
) -> None:
    snapshot = run_inventory_scan(discovery_project)
    path = store.snapshot_path(discovery_project.project_root_path, snapshot.scan_id)
    assert path.is_file()
    assert "_otio_v2/inventory/snapshots/" in path.as_posix()
    loaded = store.load_snapshot(path)
    assert loaded.scan_id == snapshot.scan_id
    assert loaded.file_count >= 1


def test_older_snapshot_preserved(discovery_project: Project) -> None:
    first = run_inventory_scan(discovery_project)
    second = run_inventory_scan(discovery_project)
    assert first.scan_id != second.scan_id
    assert store.snapshot_path(
        discovery_project.project_root_path, first.scan_id
    ).is_file()
    assert store.snapshot_path(
        discovery_project.project_root_path, second.scan_id
    ).is_file()


def test_latest_pointer_points_to_last_snapshot(discovery_project: Project) -> None:
    first = run_inventory_scan(discovery_project)
    second = run_inventory_scan(discovery_project)
    latest = store.latest_pointer_path(discovery_project.project_root_path)
    assert latest.is_file()
    data = json.loads(latest.read_text(encoding="utf-8"))
    assert data["scan_id"] == second.scan_id
    assert first.scan_id != second.scan_id
    loaded, warning = get_latest_inventory(discovery_project)
    assert warning is None
    assert loaded is not None
    assert loaded.scan_id == second.scan_id


def test_atomic_write_uses_replace(discovery_project: Project, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []
    real_replace = os.replace

    def _tracking_replace(src, dst):
        calls.append((str(src), str(dst)))
        return real_replace(src, dst)

    monkeypatch.setattr(store.os, "replace", _tracking_replace)
    run_inventory_scan(discovery_project)
    assert calls, "os.replace muss für atomisches Schreiben genutzt werden"
    assert any(dst.endswith("latest.json") for _src, dst in calls)


def test_failed_scan_does_not_replace_latest(discovery_project: Project, monkeypatch: pytest.MonkeyPatch) -> None:
    first = run_inventory_scan(discovery_project)
    latest_before = store.latest_pointer_path(discovery_project.project_root_path).read_text(
        encoding="utf-8"
    )

    def _boom(*_a, **_k):
        raise store.InventoryArtifactError("Schreibfehler simuliert")

    monkeypatch.setattr(store, "save_snapshot", _boom)
    # run_inventory_scan calls save_snapshot via module reference in service —
    # patch the service import.
    import otio_app.discovery_v2.application.inventory_service as svc

    monkeypatch.setattr(svc, "save_snapshot", _boom)
    with pytest.raises(InventoryServiceError, match="Schreibfehler"):
        run_inventory_scan(discovery_project)
    latest_after = store.latest_pointer_path(discovery_project.project_root_path).read_text(
        encoding="utf-8"
    )
    assert latest_before == latest_after
    loaded, _ = get_latest_inventory(discovery_project)
    assert loaded is not None
    assert loaded.scan_id == first.scan_id


def test_corrupted_latest_handled(discovery_project: Project) -> None:
    run_inventory_scan(discovery_project)
    latest = store.latest_pointer_path(discovery_project.project_root_path)
    latest.write_text("{not-json", encoding="utf-8")
    loaded, warning = get_latest_inventory(discovery_project)
    assert loaded is None
    assert warning is not None
    assert "beschädigt" in warning or "ungültig" in warning


def test_no_artifact_under_classic_otio(discovery_project: Project) -> None:
    run_inventory_scan(discovery_project)
    classic = discovery_project.project_root_path / "_otio"
    # Vorhandene Testdatei bleibt, aber kein inventory-Artefakt darunter
    assert not (classic / "inventory").exists()
    for path in classic.rglob("*.json"):
        assert "inventory" not in path.parts or path.name == "classic_should_not_be_scanned.mp4"


# --- UI / Routing -----------------------------------------------------------


def test_only_discovery_sees_medienbestand() -> None:
    discovery_titles = [p.title for p in routing._build_discovery_v2_pages(_noop, _noop)]
    classic_titles = [p.title for p in routing._build_with_voiceover_pages(_noop, _noop)]
    wvo_titles = [p.title for p in routing._build_without_voiceover_pages(_noop, _noop)]
    assert PAGE_DISCOVERY_INVENTORY in discovery_titles
    assert PAGE_DISCOVERY_INVENTORY not in classic_titles
    assert PAGE_DISCOVERY_INVENTORY not in wvo_titles
    assert PAGE_DISCOVERY_INVENTORY in DISCOVERY_V2_NAVIGATION_OPTIONS


def test_classic_nav_list_unchanged_with_inventory() -> None:
    titles = [p.title for p in routing._build_with_voiceover_pages(_noop, _noop)]
    assert titles == list(NAVIGATION_OPTIONS)


def test_without_vo_nav_list_unchanged_with_inventory() -> None:
    titles = [p.title for p in routing._build_without_voiceover_pages(_noop, _noop)]
    assert titles == list(VOICEOVER_GEN_NAVIGATION_OPTIONS)


def test_scan_only_via_explicit_button(
    discovery_project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Normaler Seitenaufruf startet keinen Scan; Button schon."""
    scan_calls: list[str] = []

    def _tracking_scan(project):
        scan_calls.append(project.id)
        return run_inventory_scan(project)

    buttons: dict[str, bool] = {
        "discovery_v2_inventory_scan_btn": False,
        "discovery_v2_confirm_selection_btn": False,
    }

    class _Session(dict):
        pass

    session = _Session()

    def _button(label, **kwargs):
        key = kwargs.get("key", label)
        return buttons.get(key, False)

    monkeypatch.setattr(inventory_ui, "st", MagicMock())
    inventory_ui.st.session_state = session
    inventory_ui.st.button = _button
    inventory_ui.st.checkbox = lambda *_a, **kwargs: kwargs.get("value", False)
    inventory_ui.st.text_input = lambda *_a, **_k: ""
    inventory_ui.st.divider = lambda: None
    inventory_ui.st.markdown = lambda *_a, **_k: None
    inventory_ui.st.title = lambda *_a, **_k: None
    inventory_ui.st.info = lambda *_a, **_k: None
    inventory_ui.st.write = lambda *_a, **_k: None
    inventory_ui.st.caption = lambda *_a, **_k: None
    inventory_ui.st.error = lambda *_a, **_k: None
    inventory_ui.st.warning = lambda *_a, **_k: None
    inventory_ui.st.success = lambda *_a, **_k: None
    inventory_ui.st.subheader = lambda *_a, **_k: None
    inventory_ui.st.columns = lambda n: [MagicMock() for _ in range(n)]
    inventory_ui.st.expander = lambda *_a, **_k: MagicMock(
        __enter__=lambda s: s, __exit__=lambda *_: None
    )
    inventory_ui.st.dataframe = lambda *_a, **_k: None
    inventory_ui.st.json = lambda *_a, **_k: None
    inventory_ui.st.metric = lambda *_a, **_k: None

    monkeypatch.setattr(inventory_ui, "active_discovery_project", lambda: discovery_project)
    monkeypatch.setattr(inventory_ui, "run_inventory_scan", _tracking_scan)
    monkeypatch.setattr(
        inventory_ui,
        "get_latest_inventory",
        lambda project: (None, None),
    )
    monkeypatch.setattr(
        inventory_ui,
        "get_latest_confirmed_selection",
        lambda project, current_scan_id=None: (None, None, None),
    )

    # Rerun ohne Button
    inventory_ui.render_discovery_inventory_page()
    assert scan_calls == []

    # Expliziter Button
    buttons["discovery_v2_inventory_scan_btn"] = True
    inventory_ui.render_discovery_inventory_page()
    assert scan_calls == [discovery_project.id]


def test_reload_shows_latest_snapshot(discovery_project: Project) -> None:
    snap = run_inventory_scan(discovery_project)
    loaded, warning = get_latest_inventory(discovery_project)
    assert warning is None
    assert loaded is not None
    assert loaded.scan_id == snap.scan_id
    assert any(g.label == "Florida" for g in loaded.source_groups)


def test_no_working_media_or_confirm_button_in_page_source() -> None:
    """Kein Working-Media-/Intake-Aktionsbutton — Auswahlbestätigung ist erlaubt."""
    source = Path(inventory_ui.__file__).read_text(encoding="utf-8")
    assert "kein Working Media" in source
    assert "Working Media erzeugen" not in source
    assert "Media Intake starten" not in source
    assert "discovery_v2_inventory_scan_btn" in source
    assert "Medienauswahl bestätigen" in source


def test_non_discovery_project_rejected(temp_db_path: Path, tmp_path: Path) -> None:
    root = tmp_path / "classic"
    (root / "Voice over" / "DE").mkdir(parents=True)
    (root / "Florida").mkdir()
    (root / "_otio").mkdir()
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
    with pytest.raises(InventoryServiceError, match="Discovery"):
        run_inventory_scan(project)
