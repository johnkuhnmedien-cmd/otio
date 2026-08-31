"""Geschwisterprojekt in anderer Sprache am gleichen Medienordner."""

from __future__ import annotations

from pathlib import Path

import pytest

from otio_app.defaults import BRIEF_LANGUAGE_CHOICES
from otio_app.models import ProjectCreate, ProjectMode
from otio_app.project_repository import create_project, find_projects_by_root
from otio_app.services.language_sibling_project import (
    LanguageSiblingError,
    clone_project_for_language,
    family_display_name,
    family_language_statuses,
    group_saved_projects,
    missing_sibling_languages,
    open_languages_for_auto_run,
    resolve_sibling_project,
    selected_languages_in_order,
    sibling_project_name,
)


@pytest.mark.parametrize(
    ("name", "source", "target", "expected"),
    [
        ("DE_Test Automatic", "de", "pt", "PT_Test Automatic"),
        ("DE_Greece", "DE", "en", "EN_Greece"),
        ("FR USA", "fr", "it", "IT USA"),
        ("Greece", "de", "pt", "PT_Greece"),
        ("DE", "de", "pt", "PT"),
    ],
)
def test_sibling_project_name(
    name: str, source: str, target: str, expected: str
) -> None:
    assert sibling_project_name(name, source, target) == expected


@pytest.mark.parametrize(
    ("name", "language", "expected"),
    [
        ("IT_Test Automatic", "IT", "Test Automatic"),
        ("FR_Test Automatic", "fr", "Test Automatic"),
        ("DE_Greece", "DE", "Greece"),
        ("Greece", "de", "Greece"),
        ("FR USA", "FR", "USA"),
    ],
)
def test_family_display_name(name: str, language: str, expected: str) -> None:
    assert family_display_name(name, language) == expected


def _enhanced_source(
    layout: dict[str, Path], db_path: Path, *, name: str = "DE_Test Automatic"
):
    work = layout["project_root"] / "_otio_enhanced"
    return create_project(
        ProjectCreate(
            name=name,
            project_root=str(layout["project_root"]),
            work_dir=str(work),
            project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
            language="de",
            video_place="Griechenland",
        ),
        db_path=db_path,
        asset_subdir_names=["Grand Canyon", "Yellowstone"],
        selected_asset_subdirs=["Grand Canyon"],
    )


def test_clone_shares_root_assets_and_opens_new_language_scope(
    temp_project_layout: dict[str, Path],
    temp_db_path: Path,
) -> None:
    source = _enhanced_source(temp_project_layout, temp_db_path)
    cloned = clone_project_for_language(source, "PT", db_path=temp_db_path)
    assert cloned.id != source.id
    assert cloned.language == "pt"
    assert cloned.name == "PT_Test Automatic"
    assert cloned.project_root == source.project_root
    assert cloned.work_dir == source.work_dir
    assert cloned.project_mode == ProjectMode.WITHOUT_VOICEOVER_ENHANCED
    assert cloned.video_place == "Griechenland"
    assert cloned.selected_asset_subdirs == ["Grand Canyon"]
    assert cloned.asset_subdir_names == ["Grand Canyon", "Yellowstone"]
    assert cloned.language_work_dir_path.name == "PT"
    assert source.language_work_dir_path.name == "DE"
    assert cloned.language_work_dir_path.is_dir()
    japan = clone_project_for_language(source, "JP", db_path=temp_db_path)
    assert japan.language == "jp"
    assert japan.name == "JP_Test Automatic"
    assert japan.language_work_dir_path.name == "JP"
    korea = clone_project_for_language(source, "ko", db_path=temp_db_path)
    assert korea.language == "kr"
    assert korea.language_work_dir_path.name == "KR"
    siblings = find_projects_by_root(source.project_root, db_path=temp_db_path)
    assert {item.language for item in siblings} == {"de", "pt", "jp", "kr"}


def test_clone_rejects_duplicate_language(
    temp_project_layout: dict[str, Path],
    temp_db_path: Path,
) -> None:
    source = _enhanced_source(temp_project_layout, temp_db_path)
    clone_project_for_language(source, "PT", db_path=temp_db_path)
    with pytest.raises(LanguageSiblingError, match="PT gibt es"):
        clone_project_for_language(source, "pt", db_path=temp_db_path)


def test_clone_rejects_same_language(
    temp_project_layout: dict[str, Path],
    temp_db_path: Path,
) -> None:
    source = _enhanced_source(temp_project_layout, temp_db_path)
    with pytest.raises(LanguageSiblingError, match="bereits Sprache"):
        clone_project_for_language(source, "DE", db_path=temp_db_path)


def test_missing_sibling_languages_skips_existing(
    temp_project_layout: dict[str, Path],
    temp_db_path: Path,
) -> None:
    source = _enhanced_source(temp_project_layout, temp_db_path)
    clone_project_for_language(source, "PT", db_path=temp_db_path)
    siblings = find_projects_by_root(source.project_root, db_path=temp_db_path)
    missing = missing_sibling_languages(source, siblings)
    assert "PT" not in missing
    assert "DE" not in missing
    assert "EN" in missing
    assert "FR" in missing
    assert "IT" in missing


def test_clone_starts_auto_run_when_requested(
    temp_project_layout: dict[str, Path],
    temp_db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started: list[str] = []

    class _FakeManager:
        def start(self, project) -> bool:
            started.append(project.id)
            return True

    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.enhanced_auto_run_job.get_enhanced_auto_run_job_manager",
        lambda: _FakeManager(),
    )
    source = _enhanced_source(temp_project_layout, temp_db_path)
    cloned = clone_project_for_language(
        source, "EN", db_path=temp_db_path, start_auto_run=True
    )
    assert started == [cloned.id]


def test_start_auto_run_requires_video_place_before_create(
    temp_project_layout: dict[str, Path],
    temp_db_path: Path,
) -> None:
    source = create_project(
        ProjectCreate(
            name="DE_NoPlace",
            project_root=str(temp_project_layout["project_root"]),
            work_dir=str(temp_project_layout["project_root"] / "_otio_enhanced"),
            project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
            language="de",
            video_place="",
        ),
        db_path=temp_db_path,
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )
    with pytest.raises(LanguageSiblingError, match="Land/Region"):
        clone_project_for_language(
            source, "PT", db_path=temp_db_path, start_auto_run=True
        )
    siblings = find_projects_by_root(source.project_root, db_path=temp_db_path)
    assert [item.language for item in siblings] == ["de"]


def test_open_languages_includes_missing_and_incomplete(
    temp_project_layout: dict[str, Path],
    temp_db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _enhanced_source(temp_project_layout, temp_db_path)
    clone_project_for_language(source, "PT", db_path=temp_db_path)
    siblings = find_projects_by_root(source.project_root, db_path=temp_db_path)
    monkeypatch.setattr(
        "otio_app.services.language_sibling_project.auto_run_pipeline_complete",
        lambda _project, **_kwargs: False,
    )
    open_langs = open_languages_for_auto_run(source, siblings)
    assert "DE" not in open_langs
    assert "PT" in open_langs
    assert "EN" in open_langs
    assert "FR" in open_langs
    assert "JP" in open_langs
    assert "KR" in open_langs
    assert open_langs == [lang for lang in BRIEF_LANGUAGE_CHOICES if lang != "DE"]


def test_open_languages_skips_complete_sibling(
    temp_project_layout: dict[str, Path],
    temp_db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _enhanced_source(temp_project_layout, temp_db_path)
    clone_project_for_language(source, "PT", db_path=temp_db_path)
    siblings = find_projects_by_root(source.project_root, db_path=temp_db_path)

    def fake_complete(project, **_kwargs) -> bool:
        return str(project.language).lower() == "pt"

    monkeypatch.setattr(
        "otio_app.services.language_sibling_project.auto_run_pipeline_complete",
        fake_complete,
    )
    open_langs = open_languages_for_auto_run(source, siblings)
    assert "PT" not in open_langs
    assert "DE" not in open_langs
    assert "EN" in open_langs


def test_selected_languages_keep_open_order() -> None:
    assert selected_languages_in_order(
        ["EN", "FR", "ES", "PT", "IT"],
        ["pt", "EN", "XX"],
    ) == ["EN", "PT"]
    assert selected_languages_in_order(["EN", "FR"], []) == []
    assert selected_languages_in_order(["EN", "FR"], None) == []


def test_resolve_sibling_returns_existing(
    temp_project_layout: dict[str, Path],
    temp_db_path: Path,
) -> None:
    source = _enhanced_source(temp_project_layout, temp_db_path)
    existing = clone_project_for_language(source, "PT", db_path=temp_db_path)
    resolved = resolve_sibling_project(source, "pt", db_path=temp_db_path)
    assert resolved.id == existing.id


def test_resolve_sibling_clones_when_missing(
    temp_project_layout: dict[str, Path],
    temp_db_path: Path,
) -> None:
    source = _enhanced_source(temp_project_layout, temp_db_path)
    resolved = resolve_sibling_project(source, "EN", db_path=temp_db_path)
    assert resolved.id != source.id
    assert resolved.language == "en"
    assert resolved.name == "EN_Test Automatic"


def test_auto_run_pipeline_complete_false_on_error() -> None:
    from types import SimpleNamespace

    from otio_app.services.language_sibling_project import auto_run_pipeline_complete

    assert auto_run_pipeline_complete(SimpleNamespace()) is False


def test_open_languages_include_current_when_requested(
    temp_project_layout: dict[str, Path],
    temp_db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _enhanced_source(temp_project_layout, temp_db_path)
    siblings = find_projects_by_root(source.project_root, db_path=temp_db_path)
    monkeypatch.setattr(
        "otio_app.services.language_sibling_project.auto_run_pipeline_complete",
        lambda _project, **_kwargs: False,
    )
    open_langs = open_languages_for_auto_run(
        source, siblings, include_current=True
    )
    assert open_langs[0] == "DE"
    assert "PT" in open_langs


def test_resolve_sibling_returns_source_for_same_language(
    temp_project_layout: dict[str, Path],
    temp_db_path: Path,
) -> None:
    source = _enhanced_source(temp_project_layout, temp_db_path)
    resolved = resolve_sibling_project(source, "DE", db_path=temp_db_path)
    assert resolved.id == source.id


def test_group_saved_projects_collapses_enhanced_siblings(
    temp_project_layout: dict[str, Path],
    temp_db_path: Path,
) -> None:
    from datetime import datetime, timezone
    from types import SimpleNamespace

    source = _enhanced_source(temp_project_layout, temp_db_path)
    clone_project_for_language(source, "PT", db_path=temp_db_path)
    clone_project_for_language(source, "IT", db_path=temp_db_path)
    voice = SimpleNamespace(
        id="voice-1",
        name="Voice Project",
        project_root="/tmp/other-root",
        project_mode=ProjectMode.WITH_VOICEOVER,
        is_without_voiceover_enhanced=False,
        language="de",
        created_at=datetime.now(timezone.utc),
    )
    listed = find_projects_by_root(
        str(temp_project_layout["project_root"]), db_path=temp_db_path
    ) + [voice]
    groups = group_saved_projects(listed)
    enhanced = [item for item in groups if item.grouped]
    singles = [item for item in groups if not item.grouped]
    assert len(enhanced) == 1
    assert enhanced[0].display_name == "Test Automatic"
    langs = {
        str(item.language).upper() for item in enhanced[0].projects
    }
    assert langs == {"DE", "PT", "IT"}
    assert len(singles) == 1
    assert singles[0].representative.id == "voice-1"


def test_family_language_statuses_mark_missing(
    temp_project_layout: dict[str, Path],
    temp_db_path: Path,
) -> None:
    source = _enhanced_source(temp_project_layout, temp_db_path)
    clone_project_for_language(source, "IT", db_path=temp_db_path)
    siblings = find_projects_by_root(source.project_root, db_path=temp_db_path)
    rows = family_language_statuses(siblings)
    assert [row.language for row in rows] == list(BRIEF_LANGUAGE_CHOICES)
    by_lang = {row.language: row for row in rows}
    assert by_lang["DE"].exists is True
    assert by_lang["IT"].exists is True
    assert by_lang["ES"].exists is False
    assert by_lang["ES"].next_label == "anlegen"
    assert by_lang["JP"].exists is False
    assert by_lang["KR"].exists is False
    assert by_lang["DE"].youtube_done is False
    assert by_lang["DE"].funnel_done is False


def test_saved_projects_page_wires_language_buttons() -> None:
    source = Path(__file__).resolve().parents[1] / "app.py"
    text = source.read_text(encoding="utf-8")
    assert "group_saved_projects" in text
    assert "render_enhanced_saved_family" in text
    ui = (
        Path(__file__).resolve().parents[1]
        / "otio_app"
        / "ui"
        / "language_sibling_ui.py"
    ).read_text(encoding="utf-8")
    assert "Stand je Sprache" in ui
    assert "bis Funnel" in ui
    assert "bis YouTube" in ui
    assert "lang_queue_start_funnel_" in ui
    assert "lang_queue_start_youtube_" in ui
    assert "Gewählte Sprachen" not in ui
    assert "Alle offenen Sprachen" not in ui
    assert "fragment_once" in ui
    assert "rerun_fragment" in ui
    assert "cached_family_status_rows" in ui
    assert "_render_language_queue_picks" in ui
    routing = (
        Path(__file__).resolve().parents[1] / "otio_app" / "ui" / "routing.py"
    ).read_text(encoding="utf-8")
    assert "SAVED_PROJECTS_STATUS_EPOCH_KEY" in routing
    assert "PAGE_LIST" in routing


def test_cached_family_status_rows_reuses_until_token_changes() -> None:
    from otio_app.ui.language_sibling_ui import cached_family_status_rows

    session: dict = {}
    calls = {"n": 0}

    def compute():
        calls["n"] += 1
        return ["row"]

    first = cached_family_status_rows(
        session, family_id="fam", token=("a",), compute=compute
    )
    second = cached_family_status_rows(
        session, family_id="fam", token=("a",), compute=compute
    )
    third = cached_family_status_rows(
        session, family_id="fam", token=("b",), compute=compute
    )
    assert first == ["row"]
    assert second == ["row"]
    assert third == ["row"]
    assert calls["n"] == 2
