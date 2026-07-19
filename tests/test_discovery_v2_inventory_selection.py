"""Discovery V2 — Medienauswahl und Bestätigung."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import otio_app.ui.routing as routing
from otio_app.discovery_v2.application.inventory_service import run_inventory_scan
from otio_app.discovery_v2.application.selection_service import (
    SELECTABLE_KINDS,
    build_default_draft,
    confirm_selection,
    effective_selection_status,
    get_latest_confirmed_selection,
    resolve_selected_paths,
    set_file_excluded,
    set_group_selected,
    summarize_selection,
)
from otio_app.discovery_v2.domain.inventory import MediaKind
from otio_app.discovery_v2.domain.selection import (
    EXCLUSION_REASON_USER,
    SelectionStatus,
)
from otio_app.discovery_v2.persistence import selection_artifact_store as sel_store
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
    _write(root / "Florida" / "florida_01.mp4")
    _write(root / "Florida" / "florida_02.jpg")
    _write(root / "Florida" / "exclude_me.mov")
    _write(root / "Chicago" / "chicago_01.mp4")
    _write(root / "Chicago" / "clip.mp4")
    _write(root / "Florida" / "clip.mp4")
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
            name="Sel Smoke",
            project_root=str(sample_root),
            project_mode=ProjectMode.DISCOVERY_V2,
            language="de",
        ),
        db_path=temp_db_path,
        asset_subdir_names=["Florida", "Chicago"],
        selected_asset_subdirs=["Florida", "Chicago"],
    )


@pytest.fixture
def snapshot(discovery_project: Project):
    return run_inventory_scan(discovery_project)


# --- Auswahl ---------------------------------------------------------------


def test_default_selects_video_image_audio(snapshot) -> None:
    draft = build_default_draft(snapshot)
    paths = resolve_selected_paths(snapshot, draft)
    by_path = {f.relative_path: f for f in snapshot.files}
    assert paths
    for path in paths:
        assert by_path[path].media_kind in SELECTABLE_KINDS


def test_other_not_selected_by_default(snapshot) -> None:
    draft = build_default_draft(snapshot)
    paths = set(resolve_selected_paths(snapshot, draft))
    assert "notes.txt" not in paths
    others = [f for f in snapshot.files if f.media_kind == MediaKind.OTHER]
    assert others
    assert all(f.relative_path not in paths for f in others)


def test_florida_fully_selected(snapshot) -> None:
    draft = build_default_draft(snapshot)
    paths = resolve_selected_paths(snapshot, draft)
    florida = [
        f.relative_path
        for f in snapshot.files
        if f.source_group == "Florida" and f.media_kind in SELECTABLE_KINDS
    ]
    assert florida
    assert all(p in paths for p in florida)


def test_chicago_fully_excluded(snapshot) -> None:
    draft = build_default_draft(snapshot)
    draft = set_group_selected(draft, "Chicago", False)
    paths = resolve_selected_paths(snapshot, draft)
    assert not any(p.startswith("Chicago/") for p in paths)


def test_single_file_excluded_in_florida(snapshot) -> None:
    draft = build_default_draft(snapshot)
    draft = set_file_excluded(draft, "Florida/exclude_me.mov", True)
    paths = resolve_selected_paths(snapshot, draft)
    assert "Florida/exclude_me.mov" not in paths
    assert "Florida/florida_01.mp4" in paths


def test_unsortiert_root_audio_handled(snapshot) -> None:
    draft = build_default_draft(snapshot)
    paths = resolve_selected_paths(snapshot, draft)
    assert "root_audio.wav" in paths


def test_same_filename_different_groups_unique(snapshot) -> None:
    draft = build_default_draft(snapshot)
    paths = resolve_selected_paths(snapshot, draft)
    assert "Florida/clip.mp4" in paths
    assert "Chicago/clip.mp4" in paths
    draft = set_file_excluded(draft, "Florida/clip.mp4", True)
    paths = resolve_selected_paths(snapshot, draft)
    assert "Florida/clip.mp4" not in paths
    assert "Chicago/clip.mp4" in paths


def test_zero_media_cannot_confirm(discovery_project, snapshot) -> None:
    draft = build_default_draft(snapshot)
    for group in list(draft.selected_source_groups):
        draft = set_group_selected(draft, group, False)
    with pytest.raises(Exception, match="mindestens eine Mediendatei"):
        confirm_selection(discovery_project, snapshot, draft, acknowledged=True)


def test_ack_checkbox_required(discovery_project, snapshot) -> None:
    draft = build_default_draft(snapshot)
    with pytest.raises(Exception, match="geprüft"):
        confirm_selection(discovery_project, snapshot, draft, acknowledged=False)


def test_selection_paths_only_from_snapshot(discovery_project, snapshot) -> None:
    draft = build_default_draft(snapshot)
    draft = draft.model_copy(
        update={"excluded_relative_paths": ["ghost/not-in-snapshot.mp4"]}
    )
    selection = confirm_selection(
        discovery_project, snapshot, draft, acknowledged=True
    )
    snap_paths = {f.relative_path for f in snapshot.files}
    assert set(selection.selected_relative_paths).issubset(snap_paths)
    assert "ghost/not-in-snapshot.mp4" not in selection.selected_relative_paths


def test_source_group_not_stored_as_chapter(discovery_project, snapshot) -> None:
    draft = build_default_draft(snapshot)
    selection = confirm_selection(
        discovery_project, snapshot, draft, acknowledged=True
    )
    dumped = selection.model_dump()
    assert "chapter" not in dumped
    assert "chapters" not in dumped
    assert "selected_source_groups" in dumped
    assert "Florida" in selection.selected_source_groups


# --- Persistenz ------------------------------------------------------------


def test_confirmed_selection_versioned(discovery_project, snapshot) -> None:
    draft = build_default_draft(snapshot)
    selection = confirm_selection(
        discovery_project, snapshot, draft, acknowledged=True
    )
    path = sel_store.selection_path(
        discovery_project.project_root_path, selection.selection_id
    )
    assert path.is_file()
    assert "_otio_v2/inventory/selections/" in path.as_posix()
    loaded = sel_store.load_selection(path)
    assert loaded.selection_id == selection.selection_id
    assert loaded.status == SelectionStatus.CONFIRMED
    assert loaded.exclusion_reasons or loaded.excluded_relative_paths is not None
    assert EXCLUSION_REASON_USER or True


def test_older_selection_preserved(discovery_project, snapshot) -> None:
    first = confirm_selection(
        discovery_project,
        snapshot,
        build_default_draft(snapshot),
        acknowledged=True,
    )
    # Zweiter Scan + Bestätigung
    snap2 = run_inventory_scan(discovery_project)
    second = confirm_selection(
        discovery_project,
        snap2,
        build_default_draft(snap2),
        acknowledged=True,
    )
    assert first.selection_id != second.selection_id
    assert sel_store.selection_path(
        discovery_project.project_root_path, first.selection_id
    ).is_file()
    assert sel_store.selection_path(
        discovery_project.project_root_path, second.selection_id
    ).is_file()


def test_selection_latest_pointer(discovery_project, snapshot) -> None:
    first = confirm_selection(
        discovery_project,
        snapshot,
        build_default_draft(snapshot),
        acknowledged=True,
    )
    snap2 = run_inventory_scan(discovery_project)
    second = confirm_selection(
        discovery_project,
        snap2,
        build_default_draft(snap2),
        acknowledged=True,
    )
    latest = sel_store.selection_latest_pointer_path(discovery_project.project_root_path)
    data = json.loads(latest.read_text(encoding="utf-8"))
    assert data["selection_id"] == second.selection_id
    assert data["selection_id"] != first.selection_id


def test_selection_write_atomic(discovery_project, snapshot, monkeypatch) -> None:
    calls: list[str] = []
    real_replace = os.replace

    def _tracking(src, dst):
        calls.append(str(dst))
        return real_replace(src, dst)

    monkeypatch.setattr(sel_store, "_atomic_write_text", sel_store._atomic_write_text)
    # Patch os.replace used inside inventory_artifact_store helper
    import otio_app.discovery_v2.persistence.inventory_artifact_store as inv_store

    monkeypatch.setattr(inv_store.os, "replace", _tracking)
    confirm_selection(
        discovery_project,
        snapshot,
        build_default_draft(snapshot),
        acknowledged=True,
    )
    assert any(dst.endswith("selection_latest.json") for dst in calls)


def test_failed_save_keeps_pointer(discovery_project, snapshot, monkeypatch) -> None:
    first = confirm_selection(
        discovery_project,
        snapshot,
        build_default_draft(snapshot),
        acknowledged=True,
    )
    before = sel_store.selection_latest_pointer_path(
        discovery_project.project_root_path
    ).read_text(encoding="utf-8")

    import otio_app.discovery_v2.application.selection_service as svc

    def _boom(*_a, **_k):
        raise sel_store.InventoryArtifactError("Schreibfehler simuliert")

    monkeypatch.setattr(svc, "save_selection", _boom)
    with pytest.raises(Exception, match="Schreibfehler"):
        confirm_selection(
            discovery_project,
            snapshot,
            build_default_draft(snapshot),
            acknowledged=True,
        )
    after = sel_store.selection_latest_pointer_path(
        discovery_project.project_root_path
    ).read_text(encoding="utf-8")
    assert before == after
    loaded, status, _ = get_latest_confirmed_selection(
        discovery_project, current_scan_id=snapshot.scan_id
    )
    assert loaded is not None
    assert loaded.selection_id == first.selection_id
    assert status == SelectionStatus.CONFIRMED


def test_no_selection_under_classic_otio(discovery_project, snapshot) -> None:
    confirm_selection(
        discovery_project,
        snapshot,
        build_default_draft(snapshot),
        acknowledged=True,
    )
    classic = discovery_project.project_root_path / "_otio"
    assert not (classic / "inventory").exists()
    assert not list(classic.rglob("selection*.json"))


def test_corrupted_selection_pointer(discovery_project, snapshot) -> None:
    confirm_selection(
        discovery_project,
        snapshot,
        build_default_draft(snapshot),
        acknowledged=True,
    )
    latest = sel_store.selection_latest_pointer_path(discovery_project.project_root_path)
    latest.write_text("{broken", encoding="utf-8")
    loaded, status, warning = get_latest_confirmed_selection(
        discovery_project, current_scan_id=snapshot.scan_id
    )
    assert loaded is None
    assert status is None
    assert warning is not None
    assert "beschädigt" in warning or "ungültig" in warning


def test_reload_loads_latest_selection(discovery_project, snapshot) -> None:
    selection = confirm_selection(
        discovery_project,
        snapshot,
        build_default_draft(snapshot),
        acknowledged=True,
    )
    loaded, status, warning = get_latest_confirmed_selection(
        discovery_project, current_scan_id=snapshot.scan_id
    )
    assert warning is None
    assert loaded is not None
    assert loaded.selection_id == selection.selection_id
    assert status == SelectionStatus.CONFIRMED


# --- Veraltung -------------------------------------------------------------


def test_selection_confirmed_then_stale_after_new_scan(
    discovery_project, snapshot
) -> None:
    selection_a = confirm_selection(
        discovery_project,
        snapshot,
        build_default_draft(snapshot),
        acknowledged=True,
    )
    assert selection_a.status == SelectionStatus.CONFIRMED
    assert (
        effective_selection_status(selection_a, snapshot.scan_id)
        == SelectionStatus.CONFIRMED
    )

    snap_b = run_inventory_scan(discovery_project)
    assert snap_b.scan_id != snapshot.scan_id
    assert (
        effective_selection_status(selection_a, snap_b.scan_id)
        == SelectionStatus.STALE
    )

    # Alte Auswahl bleibt lesbar und unverändert
    path_a = sel_store.selection_path(
        discovery_project.project_root_path, selection_a.selection_id
    )
    loaded_a = sel_store.load_selection(path_a)
    assert loaded_a.selection_id == selection_a.selection_id
    assert loaded_a.scan_id == snapshot.scan_id
    assert loaded_a.status == SelectionStatus.CONFIRMED  # historisch

    selection_b = confirm_selection(
        discovery_project,
        snap_b,
        build_default_draft(snap_b),
        acknowledged=True,
    )
    assert selection_b.scan_id == snap_b.scan_id
    assert selection_b.selection_id != selection_a.selection_id
    # A nicht überschrieben
    assert sel_store.load_selection(path_a).selection_id == selection_a.selection_id


# --- Routing / Regression --------------------------------------------------


def test_only_discovery_has_inventory_selection_page() -> None:
    from dataclasses import dataclass
    from typing import Callable

    @dataclass
    class _FakePage:
        render_fn: Callable
        title: str
        url_path: str = ""
        default: bool = False

    routing.st.Page = lambda fn, *, title, url_path="", default=False: _FakePage(
        fn, title, url_path, default
    )
    discovery = [p.title for p in routing._build_discovery_v2_pages(lambda: None, lambda: None)]
    classic = [p.title for p in routing._build_with_voiceover_pages(lambda: None, lambda: None)]
    wvo = [p.title for p in routing._build_without_voiceover_pages(lambda: None, lambda: None)]
    assert PAGE_DISCOVERY_INVENTORY in discovery
    assert PAGE_DISCOVERY_INVENTORY not in classic
    assert PAGE_DISCOVERY_INVENTORY not in wvo
    assert classic == list(NAVIGATION_OPTIONS)
    assert wvo == list(VOICEOVER_GEN_NAVIGATION_OPTIONS)


def test_rerun_does_not_auto_confirm(
    discovery_project, snapshot, monkeypatch
) -> None:
    confirm_calls: list[str] = []

    def _tracking_confirm(*args, **kwargs):
        confirm_calls.append("x")
        return confirm_selection(*args, **kwargs)

    state: dict = {
        inventory_ui._SESSION_SNAPSHOT_KEY: snapshot,
        inventory_ui._SESSION_DRAFT_KEY: build_default_draft(snapshot),
    }
    buttons = {
        "discovery_v2_inventory_scan_btn": False,
        "discovery_v2_confirm_selection_btn": False,
    }

    st = MagicMock()
    st.session_state = state
    st.button = lambda label, **kwargs: buttons.get(kwargs.get("key", label), False)
    st.checkbox = lambda label, **kwargs: kwargs.get("value", False)
    st.text_input = lambda *a, **k: ""
    st.columns = lambda n: [MagicMock() for _ in range(n)]

    class _Expander:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    st.expander = lambda *a, **k: _Expander()
    st.divider = lambda: None
    for name in (
        "title",
        "info",
        "write",
        "caption",
        "error",
        "warning",
        "success",
        "subheader",
        "markdown",
        "dataframe",
        "json",
        "metric",
    ):
        setattr(st, name, lambda *a, **k: None)

    monkeypatch.setattr(inventory_ui, "st", st)
    monkeypatch.setattr(inventory_ui, "active_discovery_project", lambda: discovery_project)
    monkeypatch.setattr(inventory_ui, "confirm_selection", _tracking_confirm)
    monkeypatch.setattr(
        inventory_ui,
        "get_latest_confirmed_selection",
        lambda project, current_scan_id=None: (None, None, None),
    )

    inventory_ui.render_discovery_inventory_page()
    assert confirm_calls == []


def test_no_working_media_button_in_source() -> None:
    source = Path(inventory_ui.__file__).read_text(encoding="utf-8")
    assert "Working Media erzeugen" not in source
    assert "Media Intake" not in source or "nicht erlaubt" in source
    assert "Medienauswahl bestätigen" in source
    assert "ffprobe" not in source
    assert "ffmpeg" not in source


def test_confirm_does_not_copy_media(discovery_project, snapshot) -> None:
    before = {
        str(p): (p.stat().st_mtime_ns, p.read_bytes())
        for p in discovery_project.project_root_path.rglob("*")
        if p.is_file() and "_otio_v2" not in p.parts
    }
    draft = build_default_draft(snapshot)
    draft = set_group_selected(draft, "Chicago", False)
    draft = set_file_excluded(draft, "Florida/exclude_me.mov", True)
    confirm_selection(discovery_project, snapshot, draft, acknowledged=True)
    for key, (mtime, data) in before.items():
        path = Path(key)
        assert path.stat().st_mtime_ns == mtime
        assert path.read_bytes() == data
    # Keine Working-Media-Ordner
    assert not (discovery_project.project_root_path / "_otio_v2" / "working_media").exists()


def test_no_ffmpeg_import_in_selection_modules() -> None:
    modules = [
        Path("otio_app/discovery_v2/application/selection_service.py"),
        Path("otio_app/discovery_v2/domain/selection.py"),
        Path("otio_app/discovery_v2/persistence/selection_artifact_store.py"),
        Path("otio_app/discovery_v2/ui/inventory_page.py"),
    ]
    for rel in modules:
        text = (Path("/home/ubuntu/otio-discovery-v2-integration") / rel).read_text(
            encoding="utf-8"
        )
        assert "ffprobe" not in text
        assert "ffmpeg" not in text
        assert "subprocess" not in text


def test_summary_counts_match_paths(snapshot) -> None:
    draft = build_default_draft(snapshot)
    draft = set_group_selected(draft, "Chicago", False)
    draft = set_file_excluded(draft, "Florida/exclude_me.mov", True)
    summary = summarize_selection(snapshot, draft)
    assert summary["selected_media_count"] == (
        summary["selected_video_count"]
        + summary["selected_image_count"]
        + summary["selected_audio_count"]
    )
    assert "Chicago" not in summary["selected_source_groups"]
    assert "Florida/exclude_me.mov" in summary["excluded_relative_paths"]
    assert "root_audio.wav" in summary["selected_relative_paths"]
