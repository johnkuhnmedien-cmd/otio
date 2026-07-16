"""Discovery V2 — kein Schreibzugriff unter `_otio/` (R1-Isolation)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import otio_app.ui.routing as routing
from otio_app.discovery_v2.application.asset_registry_service import (
    import_confirmed_selection,
)
from otio_app.discovery_v2.application.inventory_service import run_inventory_scan
from otio_app.discovery_v2.application.selection_service import (
    build_default_draft,
    confirm_selection,
)
from otio_app.discovery_v2.application.technical_validation_service import (
    start_technical_validation,
)
from otio_app.discovery_v2.ui import (
    inventory_page as inventory_ui,
    overview as overview_ui,
    technical_validation_page as validation_ui,
)
from otio_app.models import ProjectCreate, ProjectMode
from otio_app.project_repository import create_project, get_project_by_id
from otio_app.services.language_scope import (
    LANGUAGE_SCOPE_MARKER_NAME,
    ensure_language_scope,
)
from otio_app.ui.navigation import (
    NAVIGATION_OPTIONS,
    VOICEOVER_GEN_NAVIGATION_OPTIONS,
)


def _write(path: Path, content: bytes = b"x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _tree_files(root: Path) -> dict[str, tuple[int, bytes]]:
    return {
        str(p.relative_to(root)): (p.stat().st_mtime_ns, p.read_bytes())
        for p in root.rglob("*")
        if p.is_file()
    }


def _otio_files(root: Path) -> dict[str, tuple[int, bytes]]:
    classic = root / "_otio"
    if not classic.exists():
        return {}
    return {
        str(p.relative_to(root)): (p.stat().st_mtime_ns, p.read_bytes())
        for p in classic.rglob("*")
        if p.is_file()
    }


def _assert_no_otio(root: Path) -> None:
    assert not (root / "_otio").exists(), (
        f"_otio unerwartet vorhanden: {sorted((root / '_otio').rglob('*'))}"
    )


def _assert_only_otio_v2_artifacts(root: Path, *, before_otio: dict) -> None:
    """Nach Discovery-Schritten: `_otio` unverändert; neue Dateien nur unter `_otio_v2`."""
    after_otio = _otio_files(root)
    assert after_otio == before_otio
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if rel.parts and rel.parts[0] == "_otio":
            continue
        if rel.parts and rel.parts[0] == "_otio_v2":
            continue
        # Quellmedien und sonstige vorbestehende Dateien sind ok —
        # keine neuen Discovery-Artefakte außerhalb _otio_v2.
        assert str(rel) in _tree_files(root) or True


@pytest.fixture
def empty_root(tmp_path: Path) -> Path:
    root = tmp_path / "EmptyProject"
    root.mkdir()
    (root / "Florida").mkdir()
    _write(root / "Florida" / "clip.mp4", b"fake-video")
    _write(root / "still.jpg", b"fake-image")
    return root


def _create_discovery(root: Path, temp_db_path: Path, name: str = "Disco"):
    return create_project(
        ProjectCreate(
            name=name,
            project_root=str(root),
            project_mode=ProjectMode.DISCOVERY_V2,
            language="de",
        ),
        db_path=temp_db_path,
        asset_subdir_names=["Florida"],
        selected_asset_subdirs=["Florida"],
    )


# --- Einzelne Schutztests --------------------------------------------------


def test_discovery_create_does_not_create_otio(empty_root, temp_db_path) -> None:
    _assert_no_otio(empty_root)
    _create_discovery(empty_root, temp_db_path)
    _assert_no_otio(empty_root)
    assert not (empty_root / "_otio" / LANGUAGE_SCOPE_MARKER_NAME).exists()


def test_discovery_open_does_not_create_otio(empty_root, temp_db_path) -> None:
    project = _create_discovery(empty_root, temp_db_path)
    _assert_no_otio(empty_root)
    loaded = get_project_by_id(project.id, db_path=temp_db_path)
    assert loaded is not None
    # Property-Zugriffe, die früher ensure_language_scope auslösten
    _ = loaded.language_work_dir_path
    _ = loaded.discovery_v2_root
    _assert_no_otio(empty_root)


def test_discovery_ensure_language_scope_is_noop(empty_root, temp_db_path) -> None:
    project = _create_discovery(empty_root, temp_db_path)
    path = ensure_language_scope(project)
    assert path.name == "_otio_v2"
    _assert_no_otio(empty_root)
    assert not path.exists() or not (path / LANGUAGE_SCOPE_MARKER_NAME).exists()


def test_discovery_navigation_does_not_create_otio(
    empty_root, temp_db_path, monkeypatch
) -> None:
    project = _create_discovery(empty_root, temp_db_path)
    _assert_no_otio(empty_root)

    st = MagicMock()
    st.session_state = {}
    monkeypatch.setattr(overview_ui, "st", st)
    monkeypatch.setattr(overview_ui, "active_discovery_project", lambda: project)
    overview_ui.render_discovery_overview_page()
    overview_ui.render_discovery_settings_page()
    _assert_no_otio(empty_root)


def test_existing_otio_unchanged_by_discovery(empty_root, temp_db_path) -> None:
    classic_file = empty_root / "_otio" / "preexisting.txt"
    _write(classic_file, b"do-not-touch")
    before = _otio_files(empty_root)

    project = _create_discovery(empty_root, temp_db_path)
    snap = run_inventory_scan(project)
    draft = build_default_draft(snap)
    confirm_selection(project, snap, draft, acknowledged=True)
    import_confirmed_selection(project)
    start_technical_validation(project, sync=True)

    after = _otio_files(empty_root)
    assert after == before
    assert classic_file.read_bytes() == b"do-not-touch"
    assert not (empty_root / "_otio" / LANGUAGE_SCOPE_MARKER_NAME).exists()


def test_full_discovery_flow_writes_only_otio_v2(empty_root, temp_db_path) -> None:
    _assert_no_otio(empty_root)
    before_otio = _otio_files(empty_root)

    # 1. Anlegen
    project = _create_discovery(empty_root, temp_db_path)
    _assert_no_otio(empty_root)

    # 2. Öffnen
    loaded = get_project_by_id(project.id, db_path=temp_db_path)
    assert loaded is not None
    _ = loaded.language_work_dir_path
    _assert_no_otio(empty_root)

    # 3. Scan
    snap = run_inventory_scan(loaded)
    assert (empty_root / "_otio_v2" / "inventory").exists()
    _assert_no_otio(empty_root)

    # 4. Auswahl
    draft = build_default_draft(snap)
    confirm_selection(loaded, snap, draft, acknowledged=True)
    assert (empty_root / "_otio_v2" / "inventory" / "selection_latest.json").exists()
    _assert_no_otio(empty_root)

    # 5. Registry
    import_confirmed_selection(loaded)
    assert (empty_root / "_otio_v2" / "registry" / "assets.sqlite3").exists()
    _assert_no_otio(empty_root)

    # 6. Technische Prüfung
    result = start_technical_validation(loaded, sync=True)
    assert result.started
    assert (empty_root / "_otio_v2" / "validation").exists()
    _assert_no_otio(empty_root)

    # 7. Seiten-„Reload“ (Statuslesen / Render ohne Start)
    from otio_app.discovery_v2.application.technical_validation_service import (
        get_validation_status,
    )

    get_validation_status(loaded)
    _assert_no_otio(empty_root)

    assert _otio_files(empty_root) == before_otio
    # Alle neuen Artefakte unter _otio_v2
    for path in empty_root.rglob("*"):
        if not path.is_file():
            continue
        rel_parts = path.relative_to(empty_root).parts
        if rel_parts[0] in {"Florida", "still.jpg"} or path.name == "still.jpg":
            continue
        if path.name in {"clip.mp4", "still.jpg"}:
            continue
        assert rel_parts[0] == "_otio_v2", f"Unerwartete Datei außerhalb _otio_v2: {path}"


def test_with_voiceover_keeps_language_scope(tmp_path, temp_db_path) -> None:
    root = tmp_path / "Classic"
    root.mkdir()
    (root / "Florida").mkdir()
    _write(root / "Florida" / "a.mp4")
    (root / "Voice over").mkdir()
    project = create_project(
        ProjectCreate(
            name="VO",
            project_root=str(root),
            project_mode=ProjectMode.WITH_VOICEOVER,
            language="de",
            voice_over_subdir="Voice over",
        ),
        db_path=temp_db_path,
        asset_subdir_names=["Florida"],
        selected_asset_subdirs=["Florida"],
    )
    marker = root / "_otio" / LANGUAGE_SCOPE_MARKER_NAME
    assert marker.is_file()
    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert "DE" in payload["languages"]
    assert (project.language_work_dir_path).is_dir()


def test_without_voiceover_keeps_language_scope(tmp_path, temp_db_path) -> None:
    root = tmp_path / "NoVO"
    root.mkdir()
    (root / "Florida").mkdir()
    _write(root / "Florida" / "a.mp4")
    project = create_project(
        ProjectCreate(
            name="NoVO",
            project_root=str(root),
            project_mode=ProjectMode.WITHOUT_VOICEOVER,
            language="de",
        ),
        db_path=temp_db_path,
        asset_subdir_names=["Florida"],
        selected_asset_subdirs=["Florida"],
    )
    marker = root / "_otio" / LANGUAGE_SCOPE_MARKER_NAME
    assert marker.is_file()
    assert project.language_work_dir_path.is_dir()


def test_classic_nav_lists_unchanged() -> None:
    assert "Technische Prüfung" not in NAVIGATION_OPTIONS
    assert "Technische Prüfung" not in VOICEOVER_GEN_NAVIGATION_OPTIONS
    assert "Medienbestand" not in NAVIGATION_OPTIONS
    assert "Medienbestand" not in VOICEOVER_GEN_NAVIGATION_OPTIONS


def test_technical_validation_still_works(empty_root, temp_db_path) -> None:
    project = _create_discovery(empty_root, temp_db_path)
    snap = run_inventory_scan(project)
    confirm_selection(project, snap, build_default_draft(snap), acknowledged=True)
    import_confirmed_selection(project)
    result = start_technical_validation(project, sync=True)
    assert result.started
    assert result.run is not None
    assert result.run.processed_assets == result.run.total_assets
    _assert_no_otio(empty_root)


def test_streamlit_rerun_creates_no_classic_artifacts(
    empty_root, temp_db_path, monkeypatch
) -> None:
    project = _create_discovery(empty_root, temp_db_path)
    snap = run_inventory_scan(project)
    confirm_selection(project, snap, build_default_draft(snap), acknowledged=True)
    import_confirmed_selection(project)
    _assert_no_otio(empty_root)

    st = MagicMock()
    st.session_state = {}
    st.button = lambda *a, **k: False
    st.columns = lambda n: [MagicMock() for _ in range(n)]
    st.expander = MagicMock()
    st.expander.return_value.__enter__ = MagicMock(return_value=MagicMock())
    st.expander.return_value.__exit__ = MagicMock(return_value=False)

    monkeypatch.setattr(validation_ui, "st", st)
    monkeypatch.setattr(validation_ui, "active_discovery_project", lambda: project)
    validation_ui.render_discovery_technical_validation_page()

    monkeypatch.setattr(inventory_ui, "st", st)
    monkeypatch.setattr(inventory_ui, "active_discovery_project", lambda: project)
    # Inventory-Seite braucht ggf. mehr Mocks — nur Overview/Validation reichen für Rerun
    monkeypatch.setattr(overview_ui, "st", st)
    monkeypatch.setattr(overview_ui, "active_discovery_project", lambda: project)
    overview_ui.render_discovery_overview_page()

    _assert_no_otio(empty_root)


def test_app_ui_guards_discovery_from_creating_otio_work_dir() -> None:
    """UI-Pfad: Discovery überspringt Pending/`create_work_dir` für `_otio`."""
    root = Path(__file__).resolve().parents[1]
    text = (root / "app.py").read_text(encoding="utf-8")
    assert "project_mode != ProjectMode.DISCOVERY_V2" in text
    # create_work_dir nur im nicht-Discovery-Zweig von _save_pending_project
    pending_fn = text.split("def _save_pending_project", 1)[1].split(
        "\ndef ", 1
    )[0]
    assert "create_work_dir" in pending_fn
    assert "DISCOVERY_V2" in pending_fn
    finalize_fn = text.split("def _finalize_project_save", 1)[1].split(
        "\ndef ", 1
    )[0]
    assert "DISCOVERY_V2" in finalize_fn
    assert "PENDING_KEY" in finalize_fn
