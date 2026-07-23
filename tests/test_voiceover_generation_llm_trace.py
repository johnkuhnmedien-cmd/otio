"""Phase 2: LLM-Traceability-Grundstruktur (llm_trace_service.py)."""

from __future__ import annotations

import json
from pathlib import Path

from otio_app.models import Project, ProjectMode
from otio_app.project_layout import get_llm_runs_dir, get_voiceover_generation_dir
from otio_app.services.voiceover_generation.llm_trace_service import (
    STAGE_STYLE_PROFILE,
    STATUS_PASS,
    content_hash,
    create_llm_run_dir,
    write_llm_manifest,
    write_llm_parsed_response,
    write_llm_prompt,
    write_llm_raw_response,
)
from otio_app.services.voiceover_generation.models import LlmRunManifest


def _make_project(tmp_path: Path) -> Project:
    project_root = tmp_path / "USA"
    project_root.mkdir()
    return Project(
        id="trace-project",
        name="Trace Test",
        project_root=str(project_root),
        work_dir=str(project_root / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )


def test_create_llm_run_dir_creates_unique_run_id_folder(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    run_id_1, run_dir_1 = create_llm_run_dir(project, STAGE_STYLE_PROFILE)
    run_id_2, run_dir_2 = create_llm_run_dir(project, STAGE_STYLE_PROFILE)

    assert run_id_1 != run_id_2
    assert run_dir_1 != run_dir_2
    assert run_dir_1.is_dir()
    assert run_dir_2.is_dir()
    assert run_dir_1.parent == get_llm_runs_dir(project.language_work_dir_path)


def test_run_dirs_are_isolated_under_voiceover_generation(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _, run_dir = create_llm_run_dir(project, STAGE_STYLE_PROFILE)
    assert run_dir.is_relative_to(get_voiceover_generation_dir(project.language_work_dir_path))


def test_write_llm_prompt_saves_exact_text(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _, run_dir = create_llm_run_dir(project, STAGE_STYLE_PROFILE)
    path = write_llm_prompt(run_dir, "This is the exact prompt text.")
    assert path.name == "prompt.txt"
    assert path.read_text(encoding="utf-8") == "This is the exact prompt text."


def test_write_llm_raw_response_saves_metadata(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _, run_dir = create_llm_run_dir(project, STAGE_STYLE_PROFILE)
    path = write_llm_raw_response(
        run_dir,
        raw_text='{"overall_tone": "calm"}',
        provider="anthropic",
        model="claude-sonnet-5",
        latency_ms=421,
        token_usage={"input_tokens": 10, "output_tokens": 20},
    )
    assert path.name == "raw_llm_response.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["raw_text"] == '{"overall_tone": "calm"}'
    assert payload["provider"] == "anthropic"
    assert payload["model"] == "claude-sonnet-5"
    assert payload["latency_ms"] == 421
    assert payload["token_usage"] == {"input_tokens": 10, "output_tokens": 20}


def test_write_llm_parsed_response_saves_dict(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _, run_dir = create_llm_run_dir(project, STAGE_STYLE_PROFILE)
    path = write_llm_parsed_response(run_dir, {"overall_tone": "calm"})
    assert path.name == "parsed_llm_response.json"
    assert json.loads(path.read_text(encoding="utf-8")) == {"overall_tone": "calm"}


def test_write_llm_parsed_response_saves_parse_error(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _, run_dir = create_llm_run_dir(project, STAGE_STYLE_PROFILE)
    path = write_llm_parsed_response(run_dir, {"parse_error": "Expecting value: line 1"})
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["parse_error"] == "Expecting value: line 1"


def test_write_llm_manifest_contains_required_fields(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    run_id, run_dir = create_llm_run_dir(project, STAGE_STYLE_PROFILE)
    manifest = LlmRunManifest(
        run_id=run_id,
        stage=STAGE_STYLE_PROFILE,
        provider="anthropic",
        model="claude-sonnet-5",
        prompt_hash="abc123",
        status=STATUS_PASS,
        latency_ms=500,
        token_usage={"total_tokens": 100},
    )
    path = write_llm_manifest(run_dir, manifest)
    assert path.name == "llm_request_manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["run_id"] == run_id
    assert payload["stage"] == STAGE_STYLE_PROFILE
    assert payload["provider"] == "anthropic"
    assert payload["model"] == "claude-sonnet-5"
    assert payload["prompt_hash"] == "abc123"
    assert payload["status"] == STATUS_PASS
    assert payload["latency_ms"] == 500
    assert payload["token_usage"] == {"total_tokens": 100}


def test_content_hash_is_deterministic() -> None:
    assert content_hash("hello") == content_hash("hello")
    assert content_hash("hello") != content_hash("world")
