"""Geschwisterprojekt in anderer Sprache am gleichen Medienordner."""

from __future__ import annotations

from pathlib import Path

import pytest

from otio_app.models import ProjectCreate, ProjectMode
from otio_app.project_repository import create_project, find_projects_by_root
from otio_app.services.language_sibling_project import (
    LanguageSiblingError,
    clone_project_for_language,
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
    siblings = find_projects_by_root(source.project_root, db_path=temp_db_path)
    assert {item.language for item in siblings} == {"de", "pt"}


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
        lambda _project: False,
    )
    open_langs = open_languages_for_auto_run(source, siblings)
    assert "DE" not in open_langs
    assert "PT" in open_langs
    assert "EN" in open_langs
    assert "FR" in open_langs
    assert open_langs == ["EN", "FR", "ES", "PT", "IT"]


def test_open_languages_skips_complete_sibling(
    temp_project_layout: dict[str, Path],
    temp_db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _enhanced_source(temp_project_layout, temp_db_path)
    clone_project_for_language(source, "PT", db_path=temp_db_path)
    siblings = find_projects_by_root(source.project_root, db_path=temp_db_path)

    def fake_complete(project) -> bool:
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


def test_saved_projects_page_wires_language_buttons() -> None:
    source = Path(__file__).resolve().parents[1] / "app.py"
    text = source.read_text(encoding="utf-8")
    assert "render_language_sibling_actions" in text
    ui = (
        Path(__file__).resolve().parents[1]
        / "otio_app"
        / "ui"
        / "language_sibling_ui.py"
    ).read_text(encoding="utf-8")
    assert "lang_sibling_" in ui
    assert "Gewählte Sprachen" in ui
    assert "lang_queue_pick_" in ui
    assert "lang_queue_start_" in ui
    assert "Alle offenen Sprachen" not in ui
