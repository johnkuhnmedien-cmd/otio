"""Phase 2: Style References — Service-Tests."""

from __future__ import annotations

from pathlib import Path

from otio_app.models import Project, ProjectMode
from otio_app.project_layout import (
    get_style_references_uploads_dir,
    get_voiceover_generation_dir,
    get_voiceover_style_references_path,
)
from otio_app.services.voiceover_generation.models import VoiceoverStyleReferences
from otio_app.services.voiceover_generation.style_reference_service import (
    default_style_references,
    is_allowed_upload_filename,
    load_style_references,
    save_style_references,
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


def test_default_style_references_are_empty(tmp_path: Path) -> None:
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


def test_load_style_references_returns_default_when_missing(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    loaded = load_style_references(project)
    assert loaded.intro_reference_texts == []


def test_save_style_references_writes_only_under_voiceover_generation_dir(
    tmp_path: Path,
) -> None:
    project = _make_project(tmp_path)
    refs = VoiceoverStyleReferences(project_id=project.id, intro_reference_texts=["Hi"])
    save_style_references(project, refs)

    path = get_voiceover_style_references_path(project.work_dir_path)
    assert path.is_file()
    assert path.is_relative_to(get_voiceover_generation_dir(project.work_dir_path))
    assert not (project.work_dir_path / "edit_plan").exists()


def test_save_style_references_writes_upload_as_plain_text_file(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    refs = VoiceoverStyleReferences(
        project_id=project.id,
        uploaded_file_names=["intro_example.txt"],
        uploaded_file_texts=["Ein Beispieltext."],
    )
    save_style_references(project, refs)

    uploads_dir = get_style_references_uploads_dir(project.work_dir_path)
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
