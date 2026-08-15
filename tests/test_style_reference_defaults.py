"""Globale Style-Reference-Defaults pro Sprache."""

from __future__ import annotations

from pathlib import Path

import pytest

from otio_app.models import Project, ProjectMode
from otio_app.services.voiceover_generation.models import (
    STYLE_MODE_PROFILE,
    STYLE_MODE_RAW_TEXT,
    VoiceoverStyleProfile,
    VoiceoverStyleReferences,
)
from otio_app.services.voiceover_generation.style_profile_service import load_style_profile
from otio_app.services.voiceover_generation.style_reference_defaults_service import (
    get_style_reference_defaults_path,
    load_language_style_defaults,
    normalize_style_reference_texts,
    save_language_style_defaults,
)
from otio_app.services.voiceover_generation.style_reference_service import (
    apply_language_style_defaults_to_project,
    default_style_references,
    load_style_references,
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
def style_defaults_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(
        "otio_app.services.voiceover_generation.style_reference_defaults_service.ensure_data_dir",
        lambda: data_dir,
    )
    return data_dir


def test_normalize_style_reference_texts_keeps_three_nonempty() -> None:
    assert normalize_style_reference_texts([" A ", "", "B", "C", "D"]) == ["A", "B", "C"]


def test_save_and_load_language_style_defaults(
    tmp_path: Path, style_defaults_dir: Path
) -> None:
    saved = save_language_style_defaults(
        "pt",
        VoiceoverStyleReferences(
            project_id="x",
            style_mode=STYLE_MODE_RAW_TEXT,
            raw_reference_text="Prosa PT",
            raw_intro_reference_text="Intro PT",
            intro_reference_texts=["soll im Raw-Standard nicht stören"],
            uploaded_file_names=["notes.txt"],
            uploaded_file_texts=["upload bleibt projektspezifisch"],
        ),
        style_profile=VoiceoverStyleProfile(project_id="x", overall_tone="calm"),
    )
    assert saved.style_mode == STYLE_MODE_RAW_TEXT
    assert saved.raw_reference_text == "Prosa PT"
    assert saved.style_profile is None
    loaded = load_language_style_defaults("PT")
    assert loaded is not None
    assert loaded.raw_intro_reference_text == "Intro PT"
    assert not hasattr(loaded, "uploaded_file_names")
    assert get_style_reference_defaults_path().is_relative_to(style_defaults_dir)


def test_profile_mode_standard_keeps_style_profile(
    tmp_path: Path, style_defaults_dir: Path
) -> None:
    profile = VoiceoverStyleProfile(
        project_id="source",
        overall_tone="cinematic",
        style_summary_for_prompts="Ruhig, konkret.",
    )
    saved = save_language_style_defaults(
        "de",
        VoiceoverStyleReferences(
            project_id="source",
            style_mode=STYLE_MODE_PROFILE,
            intro_reference_texts=["Intro A", "", "Intro B"],
            segment_reference_texts=["Segment A"],
        ),
        style_profile=profile,
    )
    assert saved.style_mode == STYLE_MODE_PROFILE
    assert saved.intro_reference_texts == ["Intro A", "Intro B"]
    assert saved.style_profile is not None
    assert saved.style_profile.overall_tone == "cinematic"


def test_default_refs_pick_up_language_standard(
    tmp_path: Path, style_defaults_dir: Path
) -> None:
    save_language_style_defaults(
        "pt",
        VoiceoverStyleReferences(
            project_id="x",
            style_mode=STYLE_MODE_RAW_TEXT,
            raw_reference_text="Padrão PT",
            raw_intro_reference_text="Intro padrão",
        ),
    )
    refs = default_style_references(_make_project(tmp_path, language="pt"))
    assert refs.style_mode == STYLE_MODE_RAW_TEXT
    assert refs.raw_reference_text == "Padrão PT"
    assert refs.raw_intro_reference_text == "Intro padrão"
    assert refs.uploaded_file_texts == []


def test_apply_language_standard_writes_refs_and_profile(
    tmp_path: Path, style_defaults_dir: Path
) -> None:
    save_language_style_defaults(
        "pt",
        VoiceoverStyleReferences(
            project_id="x",
            style_mode=STYLE_MODE_PROFILE,
            intro_reference_texts=["As maravilhas de Itália"],
            segment_reference_texts=["Capítulo calmo"],
        ),
        style_profile=VoiceoverStyleProfile(
            project_id="x",
            overall_tone="documentary",
            library_name="",
        ),
    )
    project = _make_project(tmp_path, language="PT")
    apply_language_style_defaults_to_project(project)
    loaded = load_style_references(project)
    assert loaded.intro_reference_texts == ["As maravilhas de Itália"]
    assert loaded.segment_reference_texts == ["Capítulo calmo"]
    profile = load_style_profile(project)
    assert profile is not None
    assert profile.overall_tone == "documentary"
    assert profile.project_id == project.id
