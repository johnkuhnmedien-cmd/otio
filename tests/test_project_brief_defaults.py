"""Globale Project-Brief-Defaults pro Sprache."""

from __future__ import annotations

from pathlib import Path

import pytest

from otio_app.models import Project, ProjectMode
from otio_app.services.voiceover_generation.models import (
    ProjectBrief,
    ProjectBriefLanguageDefaults,
)
from otio_app.services.voiceover_generation.project_brief_defaults_service import (
    get_project_brief_defaults_path,
    load_language_brief_defaults,
    normalize_brief_language,
    normalize_title_references,
    save_language_brief_defaults,
    title_references_for_ui,
)
from otio_app.services.voiceover_generation.project_brief_service import (
    default_project_brief,
)


def _make_project(tmp_path: Path, *, language: str = "pt") -> Project:
    root = tmp_path / "Greece"
    root.mkdir(parents=True, exist_ok=True)
    return Project(
        id="pt-greece",
        name="PT_Greece",
        project_root=str(root),
        work_dir=str(root / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        language=language,
        video_place="Griechenland",
        asset_subdir_names=["Athens"],
        selected_asset_subdirs=["Athens"],
    )


@pytest.fixture()
def brief_defaults_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(
        "otio_app.services.voiceover_generation.project_brief_defaults_service.ensure_data_dir",
        lambda: data_dir,
    )
    return data_dir


def test_normalize_brief_language() -> None:
    assert normalize_brief_language("pt") == "PT"
    assert normalize_brief_language("DE") == "DE"
    assert normalize_brief_language("xx") == "DE"


def test_normalize_title_references_keeps_three_nonempty() -> None:
    assert normalize_title_references([" A ", "", "B", "C", "D"]) == ["A", "B", "C"]
    assert title_references_for_ui(["A"]) == ["A", "", ""]


def test_save_and_load_language_brief_defaults(
    tmp_path: Path, brief_defaults_dir: Path
) -> None:
    saved = save_language_brief_defaults(
        "pt",
        ProjectBriefLanguageDefaults(
            tone_tags=["cinematic"],
            title_references=["As maravilhas de Itália", "As maravilhas do Japão"],
            global_extra_prompt="Tom documental.",
        ),
    )
    assert saved.tone_tags == ["cinematic"]
    loaded = load_language_brief_defaults("PT")
    assert loaded is not None
    assert loaded.title_references == [
        "As maravilhas de Itália",
        "As maravilhas do Japão",
    ]
    assert loaded.global_extra_prompt == "Tom documental."
    assert get_project_brief_defaults_path().is_relative_to(brief_defaults_dir)


def test_default_brief_picks_up_language_standard(
    tmp_path: Path, brief_defaults_dir: Path
) -> None:
    save_language_brief_defaults(
        "pt",
        ProjectBrief(
            project_id="x",
            language="PT",
            video_title="soll nicht in den Standard",
            tone_tags=["dramatic"],
            title_references=["Ref 1"],
            forbidden_phrases=["incrível"],
        ),
    )
    brief = default_project_brief(_make_project(tmp_path, language="pt"))
    assert brief.language == "PT"
    assert brief.video_title == ""
    assert brief.tone_tags == ["dramatic"]
    assert brief.title_references == ["Ref 1"]
    assert brief.forbidden_phrases == ["incrível"]
