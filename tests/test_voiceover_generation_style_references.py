"""Phase 2: Style References — Service-Tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from otio_app.models import Project, ProjectMode
from otio_app.project_layout import (
    get_style_references_uploads_dir,
    get_voiceover_generation_dir,
    get_voiceover_style_references_path,
)
from otio_app.services.voiceover_generation.models import (
    STYLE_MODE_PROFILE,
    STYLE_MODE_RAW_TEXT,
    VoiceoverStyleProfile,
    VoiceoverStyleReferences,
)
from otio_app.services.voiceover_generation.style_profile_service import save_style_profile
from otio_app.services.voiceover_generation.style_reference_service import (
    default_style_references,
    format_raw_style_reference_for_prompts,
    is_allowed_upload_filename,
    is_raw_style_mode,
    load_style_references,
    save_style_references,
    style_context_text_for_prompts,
    truncate_upload_text,
)


def _make_project(tmp_path: Path) -> Project:
    project_root = tmp_path / "USA"
    project_root.mkdir()
    return Project(
        id="refs-project",
        name="Refs Test",
        project_root=str(project_root),
        work_dir=str(project_root / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )


def test_default_style_references_are_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "otio_app.services.voiceover_generation.style_reference_defaults_service.ensure_data_dir",
        lambda: tmp_path / "data",
    )
    project = _make_project(tmp_path)
    refs = default_style_references(project)
    assert refs.intro_reference_texts == []
    assert refs.segment_reference_texts == []


def test_save_and_load_style_references_roundtrip(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    refs = VoiceoverStyleReferences(
        project_id=project.id,
        intro_reference_texts=["Intro 1", "Intro 2", ""],
        segment_reference_texts=["Segment 1", "", ""],
        uploaded_file_names=["notes.txt"],
        uploaded_file_texts=["Some uploaded content."],
    )
    save_style_references(project, refs)

    loaded = load_style_references(project)
    assert loaded.intro_reference_texts == ["Intro 1", "Intro 2", ""]
    assert loaded.segment_reference_texts == ["Segment 1", "", ""]
    assert loaded.uploaded_file_names == ["notes.txt"]
    assert loaded.uploaded_file_texts == ["Some uploaded content."]


def test_load_style_references_returns_default_when_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "otio_app.services.voiceover_generation.style_reference_defaults_service.ensure_data_dir",
        lambda: tmp_path / "data",
    )
    project = _make_project(tmp_path)
    loaded = load_style_references(project)
    assert loaded.intro_reference_texts == []


def test_save_style_references_writes_only_under_voiceover_generation_dir(
    tmp_path: Path,
) -> None:
    project = _make_project(tmp_path)
    refs = VoiceoverStyleReferences(project_id=project.id, intro_reference_texts=["Hi"])
    save_style_references(project, refs)

    path = get_voiceover_style_references_path(project.language_work_dir_path)
    assert path.is_file()
    assert path.is_relative_to(get_voiceover_generation_dir(project.language_work_dir_path))
    assert not (project.language_work_dir_path / "edit_plan").exists()


def test_save_style_references_writes_upload_as_plain_text_file(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    refs = VoiceoverStyleReferences(
        project_id=project.id,
        uploaded_file_names=["intro_example.txt"],
        uploaded_file_texts=["Ein Beispieltext."],
    )
    save_style_references(project, refs)

    uploads_dir = get_style_references_uploads_dir(project.language_work_dir_path)
    files = list(uploads_dir.glob("*.txt"))
    assert len(files) == 1
    assert files[0].read_text(encoding="utf-8") == "Ein Beispieltext."


def test_only_txt_and_md_uploads_are_accepted() -> None:
    assert is_allowed_upload_filename("script.txt") is True
    assert is_allowed_upload_filename("script.md") is True
    assert is_allowed_upload_filename("SCRIPT.TXT") is True
    assert is_allowed_upload_filename("script.pdf") is False
    assert is_allowed_upload_filename("script.docx") is False
    assert is_allowed_upload_filename("script") is False


def test_truncate_upload_text_within_limit_is_unchanged() -> None:
    text = "short text"
    truncated, was_truncated = truncate_upload_text(text, max_chars=100)
    assert truncated == text
    assert was_truncated is False


def test_truncate_upload_text_over_limit_is_truncated() -> None:
    text = "x" * 50
    truncated, was_truncated = truncate_upload_text(text, max_chars=10)
    assert truncated == "x" * 10
    assert was_truncated is True


def test_raw_style_mode_roundtrip_and_prompt_context(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    save_style_profile(
        project,
        VoiceoverStyleProfile(
            project_id=project.id,
            overall_tone="calm",
            style_summary_for_prompts="calm documentary",
        ),
    )
    refs = VoiceoverStyleReferences(
        project_id=project.id,
        style_mode=STYLE_MODE_RAW_TEXT,
        raw_reference_text="Speak like a quiet trail guide at dusk.",
        raw_intro_reference_text="Open with a single cinematic question.",
        intro_reference_texts=["ignored in raw mode"],
    )
    save_style_references(project, refs)

    loaded = load_style_references(project)
    assert loaded.style_mode == STYLE_MODE_RAW_TEXT
    assert is_raw_style_mode(loaded) is True
    assert loaded.raw_reference_text == "Speak like a quiet trail guide at dusk."
    assert loaded.raw_intro_reference_text == "Open with a single cinematic question."

    context = style_context_text_for_prompts(project, detailed=True)
    assert "RAW STYLE REFERENCE" in context
    assert "quiet trail guide" in context
    assert "cinematic question" not in context
    assert "overall_tone" not in context
    assert "calm documentary" not in context

    intro_context = style_context_text_for_prompts(project, for_intro=True)
    assert "RAW INTRO STRUCTURAL REFERENCE" in intro_context
    assert "STRUCTURAL TEMPLATE" in intro_context
    assert "cinematic question" in intro_context
    assert "quiet trail guide" not in intro_context


def test_raw_intro_falls_back_to_general_raw_text(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    save_style_references(
        project,
        VoiceoverStyleReferences(
            project_id=project.id,
            style_mode=STYLE_MODE_RAW_TEXT,
            raw_reference_text="General calm voice.",
            raw_intro_reference_text="   ",
        ),
    )
    intro_context = style_context_text_for_prompts(project, for_intro=True)
    assert "General calm voice." in intro_context


def test_raw_style_library_roundtrip(tmp_path: Path, monkeypatch) -> None:
    from otio_app.services.voiceover_generation import raw_style_library_service as lib

    monkeypatch.setattr(lib, "get_raw_style_library_path", lambda: tmp_path / "raw_lib.json")
    saved = lib.save_raw_to_library(
        "usa_v3",
        raw_reference_text="chapter style",
        raw_intro_reference_text="intro style",
    )
    assert [e.name for e in saved.entries] == ["usa_v3"]
    entry = lib.get_raw_from_library("usa_v3")
    assert entry is not None
    assert entry.raw_reference_text == "chapter style"
    assert entry.raw_intro_reference_text == "intro style"
    lib.delete_raw_from_library("usa_v3")
    assert lib.get_raw_from_library("usa_v3") is None


def test_profile_mode_still_uses_style_profile_json(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    save_style_profile(
        project,
        VoiceoverStyleProfile(
            project_id=project.id,
            overall_tone="calm",
            style_summary_for_prompts="calm documentary",
        ),
    )
    save_style_references(
        project,
        VoiceoverStyleReferences(
            project_id=project.id,
            style_mode=STYLE_MODE_PROFILE,
            raw_reference_text="should be ignored in profile mode",
        ),
    )
    context = style_context_text_for_prompts(project, detailed=True)
    assert "overall_tone" in context
    assert "calm" in context
    assert "RAW STYLE REFERENCE" not in context


def test_format_raw_style_reference_for_prompts_empty() -> None:
    text = format_raw_style_reference_for_prompts("   ")
    assert "kein Raw-Style-Text" in text


def test_format_raw_style_reference_structural_template() -> None:
    text = format_raw_style_reference_for_prompts(
        "Vignette. Pause. Name. Question.",
        label="RAW INTRO STRUCTURAL REFERENCE",
        as_structural_template=True,
    )
    assert "STRUCTURAL TEMPLATE" in text
    assert "Mirror this Intro's STRUCTURE" in text
    assert "Vignette. Pause. Name. Question." in text
    assert "Do NOT copy wording" in text
    assert "style inspiration" not in text
