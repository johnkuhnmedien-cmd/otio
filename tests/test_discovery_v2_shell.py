"""Discovery V2 Grundgerüst — Mode, Routing, Pfade, Persistenz."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import pytest

import otio_app.ui.routing as routing
from otio_app.database import get_connection
from otio_app.defaults import (
    DEFAULT_DISCOVERY_V2_WORK_SUBDIR,
    DEFAULT_WORK_SUBDIR,
    PROJECT_MODE_CHOICES,
    PROJECT_MODE_DISCOVERY_V2,
    PROJECT_MODE_LABELS,
    PROJECT_MODE_WITH_VOICEOVER,
    PROJECT_MODE_WITHOUT_VOICEOVER,
)
from otio_app.discovery_v2.paths import (
    assert_path_is_under_discovery_v2,
    get_discovery_v2_root,
    is_under_discovery_v2,
)
from otio_app.models import ProjectCreate, ProjectMode
from otio_app.project_repository import (
    create_project,
    find_project_by_root_and_language,
    get_project_by_id,
)
from otio_app.ui.navigation import (
    DISCOVERY_V2_NAVIGATION_OPTIONS,
    NAVIGATION_OPTIONS,
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


def test_three_project_modes_are_valid() -> None:
    assert ProjectMode.WITH_VOICEOVER.value == "with_voiceover"
    assert ProjectMode.WITHOUT_VOICEOVER.value == "without_voiceover"
    assert ProjectMode.DISCOVERY_V2.value == "discovery_v2"
    assert PROJECT_MODE_WITH_VOICEOVER == "with_voiceover"
    assert PROJECT_MODE_WITHOUT_VOICEOVER == "without_voiceover"
    assert PROJECT_MODE_DISCOVERY_V2 == "discovery_v2"
    assert set(PROJECT_MODE_CHOICES) == {
        "with_voiceover",
        "without_voiceover",
        "discovery_v2",
    }
    assert PROJECT_MODE_LABELS[PROJECT_MODE_WITH_VOICEOVER] == "Projekt mit Voice-Over"
    assert PROJECT_MODE_LABELS[PROJECT_MODE_WITHOUT_VOICEOVER] == "Projekt ohne Voice-Over"
    assert PROJECT_MODE_LABELS[PROJECT_MODE_DISCOVERY_V2] == "Discovery V2"


def test_existing_mode_values_unchanged() -> None:
    assert ProjectMode.WITH_VOICEOVER.value == "with_voiceover"
    assert ProjectMode.WITHOUT_VOICEOVER.value == "without_voiceover"


def test_classic_routing_list_unchanged() -> None:
    pages = routing._build_with_voiceover_pages(_noop, _noop)
    titles = [page.title for page in pages]
    assert titles == [
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


def test_without_vo_routing_list_unchanged() -> None:
    pages = routing._build_without_voiceover_pages(_noop, _noop)
    titles = [page.title for page in pages]
    assert titles == [
        "Neues Projekt",
        "Gespeicherte Projekte",
        "⓪ Clean Media",
        "① Analysen",
        "① Project Brief",
        "② Style References",
        "③ Dramaturgie",
        "④ Folder Voice-overs",
        "⑤ Intro",
        "⑥ Audio / ElevenLabs",
        "⑦ Final Output",
        "⑧ Cut Plan",
        "🔑 API-Schlüssel",
        "Systemstatus",
    ]


def test_discovery_has_only_placeholder_navigation() -> None:
    pages = routing._build_discovery_v2_pages(_noop, _noop)
    titles = [page.title for page in pages]
    assert titles == [
        "Neues Projekt",
        "Gespeicherte Projekte",
        "Discovery V2 – Übersicht",
        "Medienbestand",
        "Projekteinstellungen",
        "🔑 API-Schlüssel",
        "Systemstatus",
    ]
    forbidden = {
        "⓪ Clean Media",
        "① Analysen",
        "② Zuordnung",
        "②½ Supplement Assets",
        "③ Schnittplan",
        "① Project Brief",
        "③ Dramaturgie",
        "⑥ Audio / ElevenLabs",
        "⑧ Cut Plan",
    }
    assert forbidden.isdisjoint(titles)
    assert "Discovery V2 – Übersicht" in DISCOVERY_V2_NAVIGATION_OPTIONS
    assert "Medienbestand" in DISCOVERY_V2_NAVIGATION_OPTIONS
    assert "Projekteinstellungen" in DISCOVERY_V2_NAVIGATION_OPTIONS
    # Bestehende Listen unverändert und getrennt
    assert "Discovery V2 – Übersicht" not in NAVIGATION_OPTIONS
    assert "Medienbestand" not in NAVIGATION_OPTIONS
    assert "Discovery V2 – Übersicht" not in VOICEOVER_GEN_NAVIGATION_OPTIONS
    assert "Medienbestand" not in VOICEOVER_GEN_NAVIGATION_OPTIONS


def test_active_project_mode_defaults_remain_with_voiceover(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(routing.st, "session_state", {})
    assert routing._active_project_mode() == ProjectMode.WITH_VOICEOVER


def test_active_project_mode_reads_discovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_project = SimpleNamespace(project_mode=ProjectMode.DISCOVERY_V2)
    monkeypatch.setattr(routing.st, "session_state", {"active_project_id": "p1"})
    monkeypatch.setattr(routing, "get_project_by_id", lambda project_id: fake_project)
    assert routing._active_project_mode() == ProjectMode.DISCOVERY_V2


def test_run_app_navigation_dispatches_discovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import contextlib

    captured: dict[str, list] = {}

    class _FakeNavigation:
        def __init__(self, pages: list) -> None:
            self.pages = pages

        def run(self) -> None:
            return None

    def _fake_navigation(pages, position="sidebar"):
        captured["pages"] = pages
        return _FakeNavigation(pages)

    monkeypatch.setattr(routing.st, "navigation", _fake_navigation)
    monkeypatch.setattr(routing.st, "sidebar", contextlib.nullcontext())
    monkeypatch.setattr(routing.st, "caption", lambda *_a, **_k: None)
    monkeypatch.setattr(routing, "render_activity_panel", lambda: None)
    monkeypatch.setattr(routing, "format_build_label", lambda: "test-build")

    fake_project = SimpleNamespace(project_mode=ProjectMode.DISCOVERY_V2)
    monkeypatch.setattr(routing.st, "session_state", {"active_project_id": "p1"})
    monkeypatch.setattr(routing, "get_project_by_id", lambda project_id: fake_project)
    routing.run_app_navigation(render_new_project=_noop, render_project_list=_noop)
    titles = [page.title for page in captured["pages"]]
    assert "Discovery V2 – Übersicht" in titles
    assert "② Zuordnung" not in titles
    assert "① Project Brief" not in titles


def test_discovery_artifact_root_is_otio_v2(tmp_path: Path) -> None:
    project_root = tmp_path / "proj"
    project_root.mkdir()
    root = get_discovery_v2_root(project_root)
    assert root.name == DEFAULT_DISCOVERY_V2_WORK_SUBDIR
    assert root.name == "_otio_v2"
    assert root.parent == project_root.resolve()
    assert root.name != DEFAULT_WORK_SUBDIR


def test_discovery_must_not_write_to_otio(tmp_path: Path) -> None:
    project_root = tmp_path / "proj"
    project_root.mkdir()
    classic = project_root / "_otio" / "inventory" / "x.json"
    discovery = project_root / "_otio_v2" / "notes.json"
    assert is_under_discovery_v2(discovery, project_root) is True
    assert is_under_discovery_v2(classic, project_root) is False
    with pytest.raises(ValueError, match="_otio"):
        assert_path_is_under_discovery_v2(classic, project_root)
    resolved = assert_path_is_under_discovery_v2(discovery, project_root)
    assert resolved.name == "notes.json"


def test_discovery_mode_persists_after_reload(
    temp_project_layout: dict[str, Path],
    temp_db_path: Path,
) -> None:
    data = ProjectCreate(
        name="Discovery Shell",
        project_root=str(temp_project_layout["project_root"]),
        project_mode=ProjectMode.DISCOVERY_V2,
        language="de",
    )
    saved = create_project(
        data,
        db_path=temp_db_path,
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )
    assert saved.project_mode == ProjectMode.DISCOVERY_V2
    assert saved.is_discovery_v2 is True
    assert saved.discovery_v2_root.name == "_otio_v2"

    loaded = get_project_by_id(saved.id, db_path=temp_db_path)
    assert loaded is not None
    assert loaded.project_mode == ProjectMode.DISCOVERY_V2
    assert loaded.project_mode.value == "discovery_v2"


def test_same_root_language_different_modes_allowed(
    temp_project_layout: dict[str, Path],
    temp_db_path: Path,
) -> None:
    root = str(temp_project_layout["project_root"])
    classic = create_project(
        ProjectCreate(
            name="Classic",
            project_root=root,
            project_mode=ProjectMode.WITH_VOICEOVER,
            language="de",
        ),
        db_path=temp_db_path,
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )
    discovery = create_project(
        ProjectCreate(
            name="Discovery",
            project_root=root,
            project_mode=ProjectMode.DISCOVERY_V2,
            language="de",
        ),
        db_path=temp_db_path,
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )
    assert classic.project_mode != discovery.project_mode
    assert classic.project_root == discovery.project_root
    assert classic.language == discovery.language

    found = find_project_by_root_and_language(
        root, "de", db_path=temp_db_path, project_mode=ProjectMode.DISCOVERY_V2
    )
    assert found is not None
    assert found.id == discovery.id


def test_same_root_language_same_mode_rejected(
    temp_project_layout: dict[str, Path],
    temp_db_path: Path,
) -> None:
    root = str(temp_project_layout["project_root"])
    create_project(
        ProjectCreate(
            name="Discovery 1",
            project_root=root,
            project_mode=ProjectMode.DISCOVERY_V2,
            language="de",
        ),
        db_path=temp_db_path,
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )
    with pytest.raises(ValueError, match="bereits ein Projekt"):
        create_project(
            ProjectCreate(
                name="Discovery 2",
                project_root=root,
                project_mode=ProjectMode.DISCOVERY_V2,
                language="DE",
            ),
            db_path=temp_db_path,
            asset_subdir_names=["Grand Canyon"],
            selected_asset_subdirs=["Grand Canyon"],
        )


def test_unique_index_includes_project_mode(temp_db_path: Path) -> None:
    conn = get_connection(temp_db_path)
    try:
        names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='projects'"
            ).fetchall()
        }
        assert "idx_projects_root_language_mode" in names
        assert "idx_projects_root_language" not in names
    finally:
        conn.close()


def test_migration_case_a_legacy_index_to_mode_index(temp_db_path: Path) -> None:
    """Fall A: alte DB mit idx_projects_root_language + with_voiceover-Projekt."""
    import sqlite3

    legacy = sqlite3.connect(temp_db_path)
    try:
        legacy.executescript(
            """
            CREATE TABLE projects (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                project_root TEXT NOT NULL,
                work_dir TEXT NOT NULL,
                project_mode TEXT NOT NULL DEFAULT 'with_voiceover',
                voice_over_subdir TEXT NOT NULL DEFAULT 'Voice over',
                language TEXT NOT NULL DEFAULT 'de',
                frames_per_shot INTEGER NOT NULL DEFAULT 3,
                fps REAL NOT NULL DEFAULT 25.0,
                width INTEGER NOT NULL DEFAULT 3840,
                height INTEGER NOT NULL DEFAULT 2160,
                aspect_ratio TEXT NOT NULL DEFAULT '16:9',
                target_platform TEXT NOT NULL DEFAULT 'YouTube',
                status TEXT NOT NULL DEFAULT 'DRAFT',
                asset_subdir_names TEXT NOT NULL DEFAULT '[]',
                selected_asset_subdirs TEXT NOT NULL DEFAULT '[]',
                notes TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX idx_projects_root_language
                ON projects(project_root, lower(language));
            INSERT INTO projects (
                id, name, project_root, work_dir, project_mode, language,
                created_at, updated_at
            ) VALUES (
                'legacy-1', 'Classic Legacy', '/tmp/legacy-root',
                '/tmp/legacy-root/_otio', 'with_voiceover', 'de', 't', 't'
            );
            """
        )
        legacy.commit()
    finally:
        legacy.close()

    conn = get_connection(temp_db_path)
    try:
        row = conn.execute(
            "SELECT id, project_mode, name FROM projects WHERE id = 'legacy-1'"
        ).fetchone()
        assert row is not None
        assert row["project_mode"] == "with_voiceover"
        assert row["name"] == "Classic Legacy"
        names = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='projects'"
            ).fetchall()
        }
        assert "idx_projects_root_language_mode" in names
        assert "idx_projects_root_language" not in names
    finally:
        conn.close()


def test_migration_case_b_idempotent(temp_db_path: Path) -> None:
    """Fall B: Migration zweimal ausführen — stabil."""
    conn1 = get_connection(temp_db_path)
    names1 = {
        r[0]
        for r in conn1.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='projects'"
        ).fetchall()
    }
    count1 = conn1.execute("SELECT COUNT(*) AS c FROM projects").fetchone()["c"]
    conn1.close()

    conn2 = get_connection(temp_db_path)
    names2 = {
        r[0]
        for r in conn2.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='projects'"
        ).fetchall()
    }
    count2 = conn2.execute("SELECT COUNT(*) AS c FROM projects").fetchone()["c"]
    conn2.close()

    assert names1 == names2
    assert "idx_projects_root_language_mode" in names2
    assert count1 == count2 == 0


def test_migration_case_c_three_modes_parallel(
    temp_project_layout: dict[str, Path],
    temp_db_path: Path,
) -> None:
    """Fall C: with_voiceover + without_voiceover + discovery_v2 am selben Root+Lang."""
    root = str(temp_project_layout["project_root"])
    assets = ["Grand Canyon"]
    modes = (
        (ProjectMode.WITH_VOICEOVER, "Classic"),
        (ProjectMode.WITHOUT_VOICEOVER, "WithoutVO"),
        (ProjectMode.DISCOVERY_V2, "Discovery"),
    )
    ids = []
    for mode, name in modes:
        project = create_project(
            ProjectCreate(
                name=name,
                project_root=root,
                project_mode=mode,
                language="de",
            ),
            db_path=temp_db_path,
            asset_subdir_names=assets,
            selected_asset_subdirs=assets,
        )
        ids.append(project.id)
    assert len(set(ids)) == 3
    for mode, _name in modes:
        found = find_project_by_root_and_language(
            root, "de", db_path=temp_db_path, project_mode=mode
        )
        assert found is not None
        assert found.project_mode == mode


def test_migration_case_e_multiple_existing_projects_preserved(
    temp_project_layout: dict[str, Path],
    temp_db_path: Path,
    tmp_path: Path,
) -> None:
    """Fall E: mehrere Classic-/Without-VO-Projekte bleiben erhalten."""
    root_a = temp_project_layout["project_root"]
    root_b = tmp_path / "OtherProj"
    (root_b / "Voice over" / "DE").mkdir(parents=True)
    (root_b / "AssetA").mkdir()
    (root_b / "_otio").mkdir()

    a = create_project(
        ProjectCreate(
            name="USA Classic",
            project_root=str(root_a),
            project_mode=ProjectMode.WITH_VOICEOVER,
            language="de",
        ),
        db_path=temp_db_path,
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )
    b = create_project(
        ProjectCreate(
            name="USA Without",
            project_root=str(root_a),
            project_mode=ProjectMode.WITHOUT_VOICEOVER,
            language="en",
        ),
        db_path=temp_db_path,
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )
    c = create_project(
        ProjectCreate(
            name="Other Classic",
            project_root=str(root_b),
            project_mode=ProjectMode.WITH_VOICEOVER,
            language="de",
        ),
        db_path=temp_db_path,
        asset_subdir_names=["AssetA"],
        selected_asset_subdirs=["AssetA"],
    )

    # Migration erneut anstoßen
    conn = get_connection(temp_db_path)
    try:
        rows = conn.execute(
            "SELECT id, name, project_mode, language FROM projects ORDER BY name"
        ).fetchall()
        assert len(rows) == 3
        by_id = {row["id"]: row for row in rows}
        assert by_id[a.id]["project_mode"] == "with_voiceover"
        assert by_id[b.id]["project_mode"] == "without_voiceover"
        assert by_id[c.id]["project_mode"] == "with_voiceover"
        assert by_id[b.id]["language"] == "en"
    finally:
        conn.close()


def test_path_no_double_otio_v2(tmp_path: Path) -> None:
    project_root = tmp_path / "Mein Projekt"
    project_root.mkdir()
    root = get_discovery_v2_root(project_root)
    assert root == project_root.resolve() / "_otio_v2"
    # Exakt ein Path-Segment `_otio_v2` — kein `_otio_v2/_otio_v2`.
    assert root.parts[-1] == "_otio_v2"
    assert root.parts[-2] != "_otio_v2"


def test_path_spaces_and_umlauts(tmp_path: Path) -> None:
    project_root = tmp_path / "Übersee Medien"
    project_root.mkdir()
    root = get_discovery_v2_root(project_root)
    nested = root / "ordner äöü" / "datei.json"
    assert is_under_discovery_v2(nested, project_root) is True
    assert assert_path_is_under_discovery_v2(nested, project_root) == nested.resolve()
    classic = project_root / "_otio" / "x.json"
    with pytest.raises(ValueError, match="_otio"):
        assert_path_is_under_discovery_v2(classic, project_root)


def test_otio_v2_backup_not_reserved_as_system_folder(tmp_path: Path) -> None:
    """Nur exakter Name `_otio_v2` ist reserviert — nicht `_otio_v2_backup`."""
    from otio_app.project_layout import classify_subdirectories_no_voiceover

    project_root = tmp_path / "proj"
    work_dir = project_root / "_otio"
    project_root.mkdir()
    work_dir.mkdir()
    names = ["_otio", "_otio_v2", "_otio_v2_backup", "Grand Canyon"]
    scan = classify_subdirectories_no_voiceover(names, work_dir, project_root)
    assert "_otio" in scan.system_folder_names
    assert "_otio_v2" in scan.system_folder_names
    assert "_otio_v2_backup" in scan.asset_subdir_names
    assert "Grand Canyon" in scan.asset_subdir_names
