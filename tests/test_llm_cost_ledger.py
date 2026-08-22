"""Tests für das LLM-Kostenledger (echte Tokens × interne Preisliste)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from otio_app.models import Project, ProjectMode
from otio_app.services.plan_llm_client import PlanLlmResponse, generate_plan_text_with_metadata
from otio_app.services.voiceover_generation.llm_cost_ledger import (
    STAGE_ENHANCED_SCRIPT,
    STAGE_FUNNEL_GEMINI,
    append_llm_cost_event,
    format_eur,
    format_family_cost_line,
    iter_recent_cost_events,
    llm_cost_scope,
    llm_costs_jsonl_path,
    llm_costs_summary_path,
    load_llm_costs_summary,
    record_gemini_response_cost,
    record_plan_llm_cost,
)
from otio_app.ui.navigation import (
    PAGE_API_KEYS,
    PAGE_COSTS,
    PAGE_FINAL_OUTPUT_ENHANCED,
    VOICEOVER_GEN_ENHANCED_NAVIGATION_OPTIONS,
    VOICEOVER_GEN_ENHANCED_WORKFLOW_PAGES,
    VOICEOVER_GEN_NAVIGATION_OPTIONS,
)


def _project(tmp_path: Path, *, language: str = "de", name: str = "Cost") -> Project:
    root = tmp_path / name
    root.mkdir(exist_ok=True)
    asset = root / "Grand Canyon"
    asset.mkdir(exist_ok=True)
    work = root / "_otio_enhanced"
    work.mkdir(exist_ok=True)
    return Project(
        id=f"cost-{language}-{name}",
        name=name,
        project_root=str(root),
        work_dir=str(work),
        language=language,
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )


def test_append_updates_summary_and_second_call_adds(tmp_path: Path) -> None:
    project = _project(tmp_path)
    first = append_llm_cost_event(
        project,
        stage=STAGE_ENHANCED_SCRIPT,
        provider="openai",
        model="gpt-5.6-terra",
        status="ok",
        input_tokens=1_000_000,
        output_tokens=100_000,
    )
    assert first is not None
    assert abs(first["cost_usd"] - 4.0) < 1e-9

    second = append_llm_cost_event(
        project,
        stage=STAGE_ENHANCED_SCRIPT,
        provider="openai",
        model="gpt-5.6-terra",
        status="ok",
        input_tokens=1_000_000,
        output_tokens=0,
    )
    assert second is not None

    summary = load_llm_costs_summary(project)
    assert summary["call_count"] == 2
    assert summary["input_tokens"] == 2_000_000
    assert summary["output_tokens"] == 100_000
    assert abs(float(summary["cost_usd"]) - 6.5) < 1e-9
    stage = summary["by_stage"][STAGE_ENHANCED_SCRIPT]
    assert stage["call_count"] == 2
    assert llm_costs_jsonl_path(project).is_file()
    assert llm_costs_summary_path(project).is_file()
    events = iter_recent_cost_events(project, limit=10)
    assert len(events) == 2


def test_fail_without_tokens_is_skipped_fail_with_tokens_is_kept(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    skipped = append_llm_cost_event(
        project,
        stage=STAGE_ENHANCED_SCRIPT,
        provider="openai",
        model="gpt-5.6-terra",
        status="fail",
        input_tokens=0,
        output_tokens=0,
        error="timeout",
    )
    assert skipped is None
    assert not llm_costs_jsonl_path(project).is_file()

    kept = append_llm_cost_event(
        project,
        stage=STAGE_ENHANCED_SCRIPT,
        provider="openai",
        model="gpt-5.6-terra",
        status="fail",
        input_tokens=2_000,
        output_tokens=0,
        error="parse",
    )
    assert kept is not None
    summary = load_llm_costs_summary(project)
    assert summary["call_count"] == 1
    assert summary["input_tokens"] == 2_000


def test_success_with_zero_tokens_still_counts(tmp_path: Path) -> None:
    project = _project(tmp_path)
    event = append_llm_cost_event(
        project,
        stage=STAGE_ENHANCED_SCRIPT,
        provider="openai",
        model="gpt-5.6-terra",
        status="ok",
        input_tokens=0,
        output_tokens=0,
    )
    assert event is not None
    assert load_llm_costs_summary(project)["call_count"] == 1


def test_generate_plan_text_without_project_or_scope_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    fake = PlanLlmResponse(
        provider="openai",
        model="gpt-5.6-terra",
        raw_text="ok",
        token_usage={"input_tokens": 100, "output_tokens": 20},
        resolved_model_id="openai:gpt-5.6-terra",
    )
    with patch(
        "otio_app.services.plan_llm_client._dispatch_plan_text",
        return_value=fake,
    ):
        text = generate_plan_text_with_metadata(
            prompt="x",
            model="openai:gpt-5.6-terra",
        )
    assert text.raw_text == "ok"
    assert not llm_costs_jsonl_path(project).is_file()


def test_generate_plan_text_with_project_writes_jsonl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    fake = PlanLlmResponse(
        provider="openai",
        model="gpt-5.6-terra",
        raw_text="ok",
        token_usage={"input_tokens": 1_000_000, "output_tokens": 100_000},
        resolved_model_id="openai:gpt-5.6-terra",
    )
    with patch(
        "otio_app.services.plan_llm_client._dispatch_plan_text",
        return_value=fake,
    ):
        generate_plan_text_with_metadata(
            prompt="x",
            model="openai:gpt-5.6-terra",
            project=project,
            stage=STAGE_ENHANCED_SCRIPT,
            folder_name="Grand Canyon",
        )
    summary = load_llm_costs_summary(project)
    assert summary["call_count"] == 1
    assert abs(float(summary["cost_usd"]) - 4.0) < 1e-9
    events = iter_recent_cost_events(project)
    assert events[0]["folder_name"] == "Grand Canyon"
    assert events[0]["stage"] == STAGE_ENHANCED_SCRIPT


def test_format_family_cost_line_missing_file_is_empty(tmp_path: Path) -> None:
    project = _project(tmp_path)
    assert format_family_cost_line([project]) == ""


def test_format_family_cost_line_two_languages(tmp_path: Path) -> None:
    jp = _project(tmp_path, language="ja", name="Family")
    de = _project(tmp_path, language="de", name="Family")
    append_llm_cost_event(
        jp,
        stage=STAGE_ENHANCED_SCRIPT,
        provider="openai",
        model="gpt-5.6-terra",
        status="ok",
        input_tokens=1_000_000,
        output_tokens=100_000,
    )
    append_llm_cost_event(
        de,
        stage=STAGE_ENHANCED_SCRIPT,
        provider="openai",
        model="gpt-5.6-terra",
        status="ok",
        input_tokens=400_000,
        output_tokens=40_000,
    )
    line = format_family_cost_line([jp, de])
    assert "DE" in line and "JP" in line
    assert "Σ" in line
    # 4.00 + 1.60 USD = 5.60 → 5,15 € at 0.92
    assert format_eur(5.6) in line
    assert format_eur(4.0) in line
    assert format_eur(1.6) in line


def test_format_eur_uses_comma() -> None:
    assert format_eur(1.0) == "0,92 €"
    assert format_eur(0.0) == "0,00 €"


def test_gemini_without_scope_writes_nothing(tmp_path: Path) -> None:
    project = _project(tmp_path)
    response = SimpleNamespace(
        usage_metadata=SimpleNamespace(prompt_token_count=10, candidates_token_count=4)
    )
    assert record_gemini_response_cost(response, model="gemini-3.5-flash") is None
    assert not llm_costs_jsonl_path(project).is_file()


def test_gemini_with_scope_records_funnel_stage(tmp_path: Path) -> None:
    project = _project(tmp_path)
    response = SimpleNamespace(
        usage_metadata=SimpleNamespace(
            prompt_token_count=1_000_000,
            candidates_token_count=1_000_000,
        )
    )
    with llm_cost_scope(project, stage=STAGE_FUNNEL_GEMINI):
        event = record_gemini_response_cost(response, model="gemini-3.5-flash")
    assert event is not None
    assert event["stage"] == STAGE_FUNNEL_GEMINI
    assert event["provider"] == "gemini"
    # Flash 3.5: $0.50 / $3.00 pro 1M
    assert abs(float(event["cost_usd"]) - 3.5) < 1e-9
    summary = load_llm_costs_summary(project)
    assert summary["call_count"] == 1
    assert STAGE_FUNNEL_GEMINI in summary["by_stage"]


def test_record_plan_llm_cost_uses_scope_when_project_omitted(tmp_path: Path) -> None:
    project = _project(tmp_path)
    with llm_cost_scope(project, stage=STAGE_ENHANCED_SCRIPT, folder_name="Athens"):
        event = record_plan_llm_cost(
            provider="openai",
            model="gpt-5.6-terra",
            token_usage={"input_tokens": 100, "output_tokens": 10},
        )
    assert event is not None
    assert event["folder_name"] == "Athens"
    assert event["stage"] == STAGE_ENHANCED_SCRIPT


def test_kosten_page_is_enhanced_only_after_final_output() -> None:
    assert PAGE_COSTS == "Kosten"
    assert PAGE_COSTS in VOICEOVER_GEN_ENHANCED_NAVIGATION_OPTIONS
    assert PAGE_COSTS not in VOICEOVER_GEN_NAVIGATION_OPTIONS
    assert PAGE_COSTS not in VOICEOVER_GEN_ENHANCED_WORKFLOW_PAGES
    options = VOICEOVER_GEN_ENHANCED_NAVIGATION_OPTIONS
    assert options.index(PAGE_FINAL_OUTPUT_ENHANCED) < options.index(PAGE_COSTS)
    assert options.index(PAGE_COSTS) < options.index(PAGE_API_KEYS)
