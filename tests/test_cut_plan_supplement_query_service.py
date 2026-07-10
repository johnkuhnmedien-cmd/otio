"""Phase 11.1: LLM-gestützte Pexels-Suchqueries für den Cut-Plan-Supplement-
Workflow (cut_plan_supplement_query_service.py)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from otio_app.models import Project, ProjectMode
from otio_app.project_layout import get_llm_runs_dir
from otio_app.services.plan_llm_client import PlanLlmResponse
from otio_app.services.voiceover_generation.cut_plan_supplement_models import CutPlanSupplementRequest
from otio_app.services.voiceover_generation.cut_plan_supplement_query_service import (
    MAX_LLM_SUPPLEMENT_QUERIES,
    build_cut_plan_supplement_query_prompt,
    generate_cut_plan_supplement_queries,
)

_SERVICE_MODULE = "otio_app.services.voiceover_generation.cut_plan_supplement_query_service"


def _make_project(tmp_path: Path) -> Project:
    project_root = tmp_path / "USA"
    project_root.mkdir()
    return Project(
        id="query-service-project",
        name="Query Service Test",
        project_root=str(project_root),
        work_dir=str(project_root / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=["Havasu Falls"],
        selected_asset_subdirs=["Havasu Falls"],
    )


def _make_request() -> CutPlanSupplementRequest:
    return CutPlanSupplementRequest(
        request_id="cutreq_havasu",
        cut_item_id="cut_havasu_001",
        folder_name="Havasu Falls",
        text="Noch vor kurzem stand ich am fallenden Wasser der Havasu Falls, spürte seine Kühle auf der Haut.",
        visual_intent="Wasserfall, Person spürt die Kühle des Wassers.",
        reason="Es fehlt Aufnahmematerial vom Wasserfall selbst.",
    )


def _fake_response(raw_text: str) -> PlanLlmResponse:
    return PlanLlmResponse(provider="gemini", model="gemini-3.1-flash-lite", raw_text=raw_text)


VALID_QUERY_RESPONSE = json.dumps(
    {
        "queries": [
            "Havasu Falls waterfall woman",
            "Havasu Falls blue water waterfall",
            "Havasu Falls Arizona waterfall",
        ]
    }
)


def test_prompt_mentions_location_text_and_example() -> None:
    prompt = build_cut_plan_supplement_query_prompt(
        folder_name="Havasu Falls",
        text="Noch vor kurzem stand ich am fallenden Wasser der Havasu Falls.",
        visual_intent="Wasserfall",
        reason="Fehlt Material",
    )
    assert "Havasu Falls" in prompt
    assert "ENGLISCH" in prompt
    assert "Noch vor kurzem stand ich am fallenden Wasser der Havasu Falls." in prompt
    assert '"queries"' in prompt


def test_generate_queries_pass_returns_up_to_three_location_prefixed(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    request = _make_request()
    with patch(f"{_SERVICE_MODULE}.generate_plan_text_with_metadata", return_value=_fake_response(VALID_QUERY_RESPONSE)):
        result = generate_cut_plan_supplement_queries(
            project, request, provider="gemini", model="gemini-3.1-flash-lite"
        )

    assert result.status == "PASS"
    assert result.queries == [
        "Havasu Falls waterfall woman",
        "Havasu Falls blue water waterfall",
        "Havasu Falls Arizona waterfall",
    ]
    assert result.run_id
    assert result.error == ""

    run_dir = get_llm_runs_dir(project.work_dir_path) / result.run_id
    assert (run_dir / "prompt.txt").is_file()
    assert (run_dir / "raw_llm_response.json").is_file()
    parsed = json.loads((run_dir / "parsed_llm_response.json").read_text(encoding="utf-8"))
    assert parsed["queries"] == result.queries
    manifest = json.loads((run_dir / "llm_request_manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "PASS"
    assert manifest["stage"] == "cut_plan_supplement_query"


def test_generate_queries_truncates_to_max_three(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    request = _make_request()
    raw = json.dumps({"queries": ["Havasu Falls a", "Havasu Falls b", "Havasu Falls c", "Havasu Falls d"]})
    with patch(f"{_SERVICE_MODULE}.generate_plan_text_with_metadata", return_value=_fake_response(raw)):
        result = generate_cut_plan_supplement_queries(
            project, request, provider="gemini", model="gemini-3.1-flash-lite"
        )
    assert result.status == "PASS"
    assert len(result.queries) == MAX_LLM_SUPPLEMENT_QUERIES
    assert result.queries == ["Havasu Falls a", "Havasu Falls b", "Havasu Falls c"]


def test_generate_queries_enforces_location_prefix_even_if_llm_omits_it(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    request = _make_request()
    raw = json.dumps({"queries": ["waterfall woman"]})
    with patch(f"{_SERVICE_MODULE}.generate_plan_text_with_metadata", return_value=_fake_response(raw)):
        result = generate_cut_plan_supplement_queries(
            project, request, provider="gemini", model="gemini-3.1-flash-lite"
        )
    assert result.status == "PASS"
    assert result.queries == ["Havasu Falls waterfall woman"]


def test_generate_queries_llm_exception_returns_fail_status_not_crash(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    request = _make_request()
    with patch(f"{_SERVICE_MODULE}.generate_plan_text_with_metadata", side_effect=RuntimeError("network down")):
        result = generate_cut_plan_supplement_queries(
            project, request, provider="gemini", model="gemini-3.1-flash-lite"
        )
    assert result.status == "FAIL"
    assert result.queries == []
    assert "network down" in result.error


# --- Phase 9 (Asset-bewusste Cut-Plan-Vorbereitung): supplement_search_hint ---


def test_prompt_includes_supplement_search_hint_when_present() -> None:
    prompt = build_cut_plan_supplement_query_prompt(
        folder_name="Havasu Falls",
        text="Noch vor kurzem stand ich am fallenden Wasser der Havasu Falls.",
        visual_intent="Wasserfall",
        reason="Fehlt Material",
        supplement_search_hint="Havasu Falls waterfall woman mist",
    )
    assert "Havasu Falls waterfall woman mist" in prompt
    assert "Bereits beim Skriptschreiben vorbereiteter Suchvorschlag" in prompt
    assert "verwerfe ihn nicht ohne guten Grund" in prompt


def test_prompt_omits_hint_block_when_hint_is_empty() -> None:
    prompt = build_cut_plan_supplement_query_prompt(
        folder_name="Havasu Falls",
        text="Text",
        visual_intent="Wasserfall",
        reason="Fehlt Material",
        supplement_search_hint="",
    )
    assert "Bereits beim Skriptschreiben vorbereiteter Suchvorschlag" not in prompt


def test_prompt_default_hint_parameter_is_empty_backward_compatible() -> None:
    """Rückwärtskompatibilität: Aufrufer ohne das neue Keyword-Argument
    (z. B. älterer Testcode) erhalten exakt dieselbe Prompt-Struktur wie vor
    Phase 9."""
    prompt_without_kw = build_cut_plan_supplement_query_prompt(
        folder_name="Havasu Falls", text="Text", visual_intent="Wasserfall", reason="Fehlt Material"
    )
    prompt_with_empty_hint = build_cut_plan_supplement_query_prompt(
        folder_name="Havasu Falls",
        text="Text",
        visual_intent="Wasserfall",
        reason="Fehlt Material",
        supplement_search_hint="",
    )
    assert prompt_without_kw == prompt_with_empty_hint


def test_generate_queries_passes_supplement_search_hint_into_prompt(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    request = _make_request().model_copy(
        update={"supplement_search_hint": "Havasu Falls waterfall woman mist"}
    )
    with patch(f"{_SERVICE_MODULE}.generate_plan_text_with_metadata", return_value=_fake_response(VALID_QUERY_RESPONSE)):
        result = generate_cut_plan_supplement_queries(
            project, request, provider="gemini", model="gemini-3.1-flash-lite"
        )

    assert result.status == "PASS"
    run_dir = get_llm_runs_dir(project.work_dir_path) / result.run_id
    prompt_text = (run_dir / "prompt.txt").read_text(encoding="utf-8")
    assert "Havasu Falls waterfall woman mist" in prompt_text


def test_generate_queries_without_hint_omits_hint_block_from_prompt(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    request = _make_request()
    assert request.supplement_search_hint == ""
    with patch(f"{_SERVICE_MODULE}.generate_plan_text_with_metadata", return_value=_fake_response(VALID_QUERY_RESPONSE)):
        result = generate_cut_plan_supplement_queries(
            project, request, provider="gemini", model="gemini-3.1-flash-lite"
        )

    run_dir = get_llm_runs_dir(project.work_dir_path) / result.run_id
    prompt_text = (run_dir / "prompt.txt").read_text(encoding="utf-8")
    assert "Bereits beim Skriptschreiben vorbereiteter Suchvorschlag" not in prompt_text


@pytest.mark.parametrize(
    "raw_text",
    [
        "not json at all",
        json.dumps({"no_queries_field": True}),
        json.dumps({"queries": []}),
        json.dumps({"queries": ["", "   "]}),
        json.dumps({"queries": "not a list"}),
    ],
)
def test_generate_queries_invalid_response_returns_parse_failed(tmp_path: Path, raw_text: str) -> None:
    project = _make_project(tmp_path)
    request = _make_request()
    with patch(f"{_SERVICE_MODULE}.generate_plan_text_with_metadata", return_value=_fake_response(raw_text)):
        result = generate_cut_plan_supplement_queries(
            project, request, provider="gemini", model="gemini-3.1-flash-lite"
        )
    assert result.status == "PARSE_FAILED"
    assert result.queries == []
