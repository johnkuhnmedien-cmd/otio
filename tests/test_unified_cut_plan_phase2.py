"""Phase 2: Unified-Cut-Prompt + Parse → UnifiedCutPlanDocument."""

from __future__ import annotations

import json

import pytest

from otio_app.services.without_voiceover_enhanced.script_prompts import (
    DEFAULT_CUT_RHYTHM_TARGETS,
    build_unified_cut_prompt,
)
from otio_app.services.without_voiceover_enhanced.unified_cut_plan import (
    UnifiedCutPlanError,
    parse_unified_cut_response,
    unified_to_rough,
)


def test_build_unified_cut_prompt_contains_core_contract() -> None:
    prompt = build_unified_cut_prompt(
        locked_script_json='{"segments":[]}',
        segment_timings_json="[]",
        local_assets_json="[]",
        style_profile_text="style",
        dramaturgy_text="drama",
        folder_name="Rocamadour",
        folder_slug="Rocamadour",
        previous_folder_name="Albarracin",
        next_folder_name="Castle Combe",
        sentence_timings_json='[{"sentence_id":"Rocamadour_segment_001__s001"}]',
        cut_rhythm_targets_text=DEFAULT_CUT_RHYTHM_TARGETS,
        used_in_ledger_text="asset_x used 1x",
    )
    assert "UNIFIED cut planner" in prompt
    assert "N slots and exactly N+1 boundaries" in prompt
    assert "len(slots) == len(boundaries) - 1" in prompt
    assert "strong | acceptable | weak | none" in prompt
    assert "Plan ONLY the chapter \"Rocamadour\"" in prompt
    assert "Rocamadour_cut_000" in prompt
    assert "SENTENCE TIMINGS" in prompt
    assert "USED-IN LEDGER" in prompt
    assert "every 4th–6th" in prompt or "every 4th-6th" in prompt
    assert "medium: ~2–3s" in prompt or "medium: ~2-3s" in prompt
    assert "Vorlauf/Nachlauf are applied later by Python" in prompt
    assert "No video-hold assumptions" in prompt
    assert "LOCKED SCRIPT:" in prompt
    assert "LOCAL ASSETS" in prompt


def test_build_unified_cut_prompt_optional_blocks_omitted_when_empty() -> None:
    prompt = build_unified_cut_prompt(
        locked_script_json="{}",
        segment_timings_json="[]",
        local_assets_json="[]",
        style_profile_text="",
        dramaturgy_text="",
    )
    assert "CHAPTER SCOPE" not in prompt
    assert "SENTENCE TIMINGS (authoritative" not in prompt
    assert "USED-IN LEDGER" not in prompt
    assert "MIDDLE-FRAME VISION" not in prompt


def _sample_payload(*, folder_prefix: str = "") -> dict:
    p = folder_prefix
    return {
        "pause_directives": [
            {
                "after_segment_id": f"{p}A_segment_001" if p else "A_segment_001",
                "after_sentence_id": f"{p}A_segment_001__s002"
                if p
                else "A_segment_001__s002",
                "pause_function": "breath",
                "duration_class": "medium",
                "visual_behavior": "hold_current_shot",
                "editorial_reason": "beat",
            }
        ],
        "boundaries": [
            {
                "cut_id": "cut_000",
                "sentence_id": "A_segment_001__s001",
                "position": "start",
                "offset_seconds": None,
                "alignment": "sentence_boundary",
            },
            {
                "cut_id": "cut_001",
                "sentence_id": "A_segment_001__s002",
                "position": "middle",
                "alignment": "mid_sentence",
            },
            {
                "cut_id": "cut_002",
                "sentence_id": "A_segment_001__s003",
                "position": "end",
                "alignment": "sentence_boundary",
            },
        ],
        "slots": [
            {
                "slot_id": "slot_001",
                "local_asset_id": "loc_a",
                "asset_fit": "strong",
                "asset_fit_reason": "clear match",
                "visual_intent": "establishing",
                "narrative_function": "chapter_open",
                "coverage_gap_id": None,
            },
            {
                "slot_id": "slot_002",
                "local_asset_id": None,
                "asset_fit": "none",
                "asset_fit_reason": "missing street detail",
                "visual_intent": "street",
                "narrative_function": "evidence",
                "needed_visual": "narrow alley with stone walls",
                "search_concepts": ["alley", "stone street"],
                "desired_motion": "tracking",
                "desired_framing": "medium",
            },
        ],
    }


def test_parse_unified_cut_response_from_dict() -> None:
    plan = parse_unified_cut_response(_sample_payload(), "script-v2")
    assert plan.schema_version == "unified-cut-v1"
    assert plan.script_version == "script-v2"
    assert len(plan.boundaries) == 3
    assert len(plan.slots) == 2
    assert len(plan.pause_directives) == 1
    assert plan.slots[0].asset_fit == "strong"
    assert plan.slots[0].coverage_gap_id is None
    assert plan.slots[1].asset_fit == "none"
    assert plan.slots[1].local_asset_id is None
    assert plan.slots[1].coverage_gap_id == "gap_slot_002"
    assert plan.slots[1].needed_visual.startswith("narrow alley")


def test_parse_unified_cut_response_from_json_string_with_fence() -> None:
    raw = "```json\n" + json.dumps(_sample_payload()) + "\n```"
    plan = parse_unified_cut_response(raw, "v1")
    assert len(plan.slots) == 2
    assert plan.boundaries[1].alignment == "mid_sentence"


def test_parse_applies_folder_slug_prefix() -> None:
    plan = parse_unified_cut_response(
        _sample_payload(),
        "v1",
        folder_slug="Rocamadour",
    )
    assert plan.boundaries[0].cut_id == "Rocamadour_cut_000"
    assert plan.slots[0].slot_id == "Rocamadour_slot_001"
    assert plan.slots[1].coverage_gap_id == "gap_Rocamadour_slot_002"


def test_parse_clears_gap_id_for_strong_and_nulls_asset_for_none() -> None:
    payload = _sample_payload()
    payload["slots"][0]["coverage_gap_id"] = "should_be_cleared"
    payload["slots"][1]["local_asset_id"] = "should_be_nulled"
    plan = parse_unified_cut_response(payload, "v1")
    assert plan.slots[0].coverage_gap_id is None
    assert plan.slots[1].local_asset_id is None


def test_parse_rejects_missing_sentence_id() -> None:
    payload = _sample_payload()
    payload["boundaries"][0]["sentence_id"] = ""
    with pytest.raises(UnifiedCutPlanError, match="sentence_id fehlt"):
        parse_unified_cut_response(payload, "v1")


def test_parse_rejects_invalid_position() -> None:
    payload = _sample_payload()
    payload["boundaries"][1]["position"] = "halfway"
    with pytest.raises(UnifiedCutPlanError, match="ungültige position"):
        parse_unified_cut_response(payload, "v1")


def test_parse_rejects_broken_boundary_slot_invariant() -> None:
    payload = _sample_payload()
    payload["slots"] = payload["slots"][:1]
    with pytest.raises(UnifiedCutPlanError, match="Invariante"):
        parse_unified_cut_response(payload, "v1")


def test_parse_then_unified_to_rough_roundtrip() -> None:
    plan = parse_unified_cut_response(_sample_payload(), "script-v2")
    rough, coverage = unified_to_rough(plan)
    assert len(rough.shots) == 2
    assert rough.shots[0].asset_fit == "strong"
    assert rough.shots[0].coverage_gap_id is None
    assert rough.shots[1].coverage_gap_id == "gap_slot_002"
    assert len(coverage.gaps) == 1
    assert coverage.gaps[0].priority == "high"
    assert coverage.gaps[0].search_concepts == ["alley", "stone street"]
    assert rough.shots[0].start_anchor.sentence_id == "A_segment_001__s001"
    assert rough.shots[1].end_anchor.sentence_id == "A_segment_001__s003"
