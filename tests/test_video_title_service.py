"""LLM-Videotitel aus Land/Region + Referenz-Titeln."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from otio_app.models import Project, ProjectMode
from otio_app.services.voiceover_generation.llm_trace_service import STATUS_FAIL, STATUS_PASS
from otio_app.services.voiceover_generation.prompts import build_video_title_prompt
from otio_app.services.voiceover_generation.video_title_service import generate_video_title


def _project(tmp_path: Path, *, video_place: str = "Griechenland") -> Project:
    root = tmp_path / "Greece"
    root.mkdir(parents=True, exist_ok=True)
    return Project(
        id="title-proj",
        name="PT_Greece",
        project_root=str(root),
        work_dir=str(root / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        language="pt",
        video_place=video_place,
        asset_subdir_names=["Athens"],
        selected_asset_subdirs=["Athens"],
    )


def test_build_video_title_prompt_marks_references_as_inspiration() -> None:
    prompt = build_video_title_prompt(
        language="PT",
        video_place="Griechenland",
        title_references=["As maravilhas de Itália", "The Wonders of Japan"],
        tone_tags=["cinematic"],
    )
    assert "inspiration only" in prompt.lower() or "NOT a template" in prompt
    assert "As maravilhas de Itália" in prompt
    assert "Griechenland" in prompt
    assert "Portuguese" in prompt
    assert "Do NOT just swap the" in prompt


def test_generate_video_title_requires_place(tmp_path: Path) -> None:
    result = generate_video_title(
        _project(tmp_path, video_place=""),
        language="PT",
        video_place="",
        title_references=["As maravilhas de Itália"],
        provider="gemini",
        model="gemini-test",
    )
    assert result.status == STATUS_FAIL
    assert "Land/Region" in result.error


def test_generate_video_title_requires_reference(tmp_path: Path) -> None:
    result = generate_video_title(
        _project(tmp_path),
        language="PT",
        video_place="Griechenland",
        title_references=["", "  "],
        provider="gemini",
        model="gemini-test",
    )
    assert result.status == STATUS_FAIL
    assert "Referenz" in result.error


def test_generate_video_title_parses_json(tmp_path: Path) -> None:
    class _Resp:
        raw_text = '{"title": "As maravilhas da Grécia"}'
        provider = "gemini"
        model = "gemini-test"
        latency_ms = 4
        token_usage = {}

    with patch(
        "otio_app.services.voiceover_generation.video_title_service.generate_plan_text_with_metadata",
        return_value=_Resp(),
    ):
        result = generate_video_title(
            _project(tmp_path),
            language="PT",
            video_place="Griechenland",
            title_references=["As maravilhas de Itália"],
            provider="gemini",
            model="gemini-test",
        )
    assert result.status == STATUS_PASS
    assert result.title == "As maravilhas da Grécia"
