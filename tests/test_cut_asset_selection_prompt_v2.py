"""Phase 2B: shared cut-asset-selection-v2 guidance in productive builders."""

from __future__ import annotations

import json

from otio_app.services.inventory_prompt_view import slim_assets_from_slim_document
from otio_app.services.without_voiceover_enhanced.script_prompts import (
    CUT_ASSET_SELECTION_GUIDANCE,
    CUT_ASSET_SELECTION_PROMPT_VERSION,
    build_final_cut_prompt,
    build_intro_unified_cut_prompt,
    build_keyword_flow_unified_cut_prompt,
    build_keyword_sync_unified_cut_prompt,
    build_rough_cut_prompt,
    build_unified_cut_prompt,
)


def _sample_v2_assets_json() -> str:
    slim = {
        "schema_version": "asset-slim-v2",
        "chapter": "Albarracín",
        "assets": [
            {
                "id": "asset_adobestock_544058849",
                "file": "AdobeStock_544058849.mov",
                "type": "video",
                "duration_s": 24.833,
                "caption": "Luftaufnahme einer historischen Bergstadt mit Wehrmauer.",
                "tags": ["Bergstadt", "Wehrmauer", "Kirchturm", "Luftaufnahme"],
                "motion": {
                    "type": "drone",
                    "intensity": 35,
                    "direction": "forward",
                },
                "framing": {"type": "aerial", "scale": "wide"},
                "quality": {
                    "technical": 82,
                    "composition": 85,
                    "appeal": 86,
                    "clarity": 88,
                    "hero": 85,
                    "defect": 0,
                },
                "look": {
                    "brightness": 65,
                    "contrast": 70,
                    "saturation": 60,
                    "temperature": "warm",
                    "colors": ["Ziegelrot", "Beige", "Rotbraun"],
                },
                "people": False,
                "usable_in_s": 0.12,
            }
        ],
    }
    rows = slim_assets_from_slim_document(slim, folder_name="Albarracín")
    return json.dumps(rows, ensure_ascii=False)


def _assert_shared_guidance(prompt: str) -> None:
    assert CUT_ASSET_SELECTION_PROMPT_VERSION == "cut-asset-selection-v2"
    assert "ASSET SELECTION FROM SLIM INVENTORY" in prompt
    assert CUT_ASSET_SELECTION_GUIDANCE.splitlines()[0] in prompt
    assert "Content/exact identity ALWAYS outranks beauty and scores" in prompt
    assert "NEVER pick an asset only because it has the highest" in prompt
    assert "hero:" in prompt
    assert "clarity:" in prompt
    assert "defect:" in prompt
    assert "Missing scores on Slim-v1" in prompt
    assert "Sequence harmony" in prompt
    assert "identical framing" in prompt or "identical shot_scale" in prompt


def test_shared_guidance_in_all_productive_cut_builders() -> None:
    assets = "[]"
    prompts = [
        build_rough_cut_prompt(
            locked_script_json="{}",
            segment_timings_json="{}",
            local_assets_json=assets,
            style_profile_text="s",
            dramaturgy_text="d",
        ),
        build_unified_cut_prompt(
            locked_script_json="{}",
            segment_timings_json="{}",
            local_assets_json=assets,
            style_profile_text="s",
            dramaturgy_text="d",
        ),
        build_keyword_sync_unified_cut_prompt(
            locked_script_json="{}",
            segment_timings_json="{}",
            local_assets_json=assets,
            style_profile_text="s",
            dramaturgy_text="d",
        ),
        build_keyword_flow_unified_cut_prompt(
            locked_script_json="{}",
            segment_timings_json="{}",
            local_assets_json=assets,
            style_profile_text="s",
            dramaturgy_text="d",
        ),
        build_intro_unified_cut_prompt(
            locked_script_json="{}",
            segment_timings_json="{}",
            bundled_inventory_json="{}",
            style_profile_text="s",
            dramaturgy_text="d",
        ),
        build_final_cut_prompt(
            locked_script_json="{}",
            narration_timeline_json="{}",
            pause_directives_json="[]",
            rough_cut_json="{}",
            local_assets_json=assets,
            accepted_supplements_json="[]",
            style_profile_text="s",
        ),
    ]
    for prompt in prompts:
        _assert_shared_guidance(prompt)
        assert "Never invent an asset ID" in prompt or "never invent" in prompt.lower()


def test_body_cut_prompt_embeds_slim_v2_decision_fields() -> None:
    assets_json = _sample_v2_assets_json()
    prompt = build_keyword_flow_unified_cut_prompt(
        locked_script_json="{}",
        segment_timings_json="{}",
        local_assets_json=assets_json,
        style_profile_text="s",
        dramaturgy_text="d",
        folder_name="Albarracín",
        folder_slug="Albarracin",
    )
    assert "asset_adobestock_544058849" in prompt
    assert "Luftaufnahme einer historischen Bergstadt mit Wehrmauer." in prompt
    assert '"tags"' in prompt
    assert "motion_intensity" in prompt
    assert "motion_direction" in prompt
    assert "shot_scale" in prompt
    assert '"hero": 85' in prompt or '"hero":85' in prompt
    assert "warm" in prompt
    assert "Ziegelrot" in prompt
    assert "analysis_confidence" not in prompt
    assert "analysis_raw_response" not in prompt
    assert "coverage gap" in prompt.lower() or "coverage_gap" in prompt
    assert "exact" in prompt.lower()
