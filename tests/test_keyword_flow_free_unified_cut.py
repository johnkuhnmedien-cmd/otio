"""Keyword Flow Free: isolation, continuous word flow, mid-sentence, gaps, timing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
    UNIFIED_CUT_STYLE_KEYWORD_FLOW,
    UNIFIED_CUT_STYLE_KEYWORD_FLOW_FREE,
    UNIFIED_CUT_STYLE_KEYWORD_SYNC,
    UNIFIED_CUT_STYLE_RHYTHM,
    CutPlanOptions,
    _normalize_unified_cut_style,
    default_cut_plan_options,
    is_keyword_flow_free_unified_style,
    is_keyword_flow_unified_style,
    is_keyword_sync_unified_style,
    uses_keyword_onset_timing_rules,
)
from otio_app.services.without_voiceover_enhanced.keyword_flow_free_input import (
    build_continuous_word_flow,
    build_continuous_word_flow_from_sentence_rows,
)
from otio_app.services.without_voiceover_enhanced.keyword_flow_free_prompt import (
    KEYWORD_FLOW_FREE_MARKER,
    build_keyword_flow_free_prompt,
)
from otio_app.services.without_voiceover_enhanced.keyword_flow_timing import (
    KeywordFlowTimingError,
    validate_keyword_flow_mid_sentence_onsets,
)
from otio_app.services.without_voiceover_enhanced.models import (
    CutBoundary,
    CutSlot,
    UnifiedCutPlanDocument,
)
from otio_app.services.without_voiceover_enhanced.script_prompts import (
    build_keyword_flow_unified_cut_prompt,
    build_keyword_sync_unified_cut_prompt,
    build_unified_cut_prompt,
)
from otio_app.services.without_voiceover_enhanced.unified_cut_plan import (
    unified_to_rough,
)


def test_style_normalization_and_defaults_keyword_flow_free() -> None:
    assert (
        _normalize_unified_cut_style("keyword_flow_free", default="rhythm")
        == UNIFIED_CUT_STYLE_KEYWORD_FLOW_FREE
    )
    assert (
        _normalize_unified_cut_style("keyword-flow-free", default="rhythm")
        == UNIFIED_CUT_STYLE_KEYWORD_FLOW_FREE
    )
    assert (
        _normalize_unified_cut_style("free_keyword_flow", default="rhythm")
        == UNIFIED_CUT_STYLE_KEYWORD_FLOW_FREE
    )
    defaults = default_cut_plan_options()
    assert defaults.unified_cut_style == UNIFIED_CUT_STYLE_RHYTHM
    free = CutPlanOptions(unified_cut_style=UNIFIED_CUT_STYLE_KEYWORD_FLOW_FREE)
    assert is_keyword_flow_free_unified_style(free)
    assert not is_keyword_flow_unified_style(free)
    assert not is_keyword_sync_unified_style(free)
    assert uses_keyword_onset_timing_rules(free)
    kf = CutPlanOptions(unified_cut_style=UNIFIED_CUT_STYLE_KEYWORD_FLOW)
    assert is_keyword_flow_unified_style(kf)
    assert not is_keyword_flow_free_unified_style(kf)
    assert uses_keyword_onset_timing_rules(kf)


def test_ui_contains_keyword_flow_free_label() -> None:
    source = Path(
        "otio_app/ui/without_voiceover_enhanced/cut_plan_tab.py"
    ).read_text(encoding="utf-8")
    assert 'UNIFIED_CUT_STYLE_KEYWORD_FLOW_FREE: "Keyword Flow Free"' in source
    assert "kontinuierlichem Wortfluss" in source
    assert 'UNIFIED_CUT_STYLE_KEYWORD_FLOW: "Keyword Flow"' in source


def test_dispatch_routes_free_before_keyword_flow() -> None:
    source = Path(
        "otio_app/services/without_voiceover_enhanced/cut_plan_service.py"
    ).read_text(encoding="utf-8")
    free_idx = source.index("if use_keyword_flow_free:")
    kf_idx = source.index("elif use_keyword_flow:")
    sync_idx = source.index("elif is_keyword_sync_unified_style(options):")
    assert free_idx < kf_idx < sync_idx
    assert "build_keyword_flow_free_prompt" in source
    assert "build_keyword_flow_unified_cut_prompt" in source
    assert (
        "use_keyword_flow = (not is_intro) and is_keyword_flow_unified_style(options)"
        in source
    )
    assert "Keyword Flow Free benötigt echte ElevenLabs-Wort-Timestamps" in source


def test_flow_isolation_prompts() -> None:
    free = build_keyword_flow_free_prompt(
        locked_script_json="{}",
        segment_timings_json="{}",
        local_assets_json="[]",
        style_profile_text="style",
        dramaturgy_text="dram",
        continuous_word_flow_json='[{"word_ref":"s001#0","text":"Heute"}]',
        folder_name="Island",
        folder_slug="Island",
        shot_constraints_text="SHOT / ASSET CONSTRAINTS\n- shot_min 3\n",
    )
    assert KEYWORD_FLOW_FREE_MARKER in free
    assert "CONTINUOUS WORD FLOW" in free
    assert "Treat narration as a continuous spoken flow" in free
    assert "KEYWORD FLOW MARKER" not in free
    assert "CUT RHYTHM TARGETS" not in free
    # Structural freedom without pseudo-realistic timing numbers to copy.
    assert "COPY_FROM_WORD_FLOW_offset_seconds" in free
    assert "same sentence_id" in free
    assert "several words into that next sentence" in free
    assert "1.05" not in free
    assert "3.40" not in free
    assert "1.82" not in free
    assert "First ask: does the visual story need a new shot here?" in free
    assert "Keyword ≠ Pflicht-Cut" in free or "not mandatory cuts" in free
    assert "copy sentence_id and offset_seconds verbatim" in free

    kf = build_keyword_flow_unified_cut_prompt(
        locked_script_json="{}",
        segment_timings_json="{}",
        local_assets_json="[]",
        style_profile_text="s",
        dramaturgy_text="d",
        sentence_timings_json="[]",
    )
    assert "KEYWORD FLOW MARKER" in kf
    assert KEYWORD_FLOW_FREE_MARKER not in kf
    assert "CONTINUOUS WORD FLOW" not in kf

    sync = build_keyword_sync_unified_cut_prompt(
        locked_script_json="{}",
        segment_timings_json="{}",
        local_assets_json="[]",
        style_profile_text="s",
        dramaturgy_text="d",
    )
    assert "KEYWORD-SYNC cut planner" in sync
    assert KEYWORD_FLOW_FREE_MARKER not in sync

    rhythm = build_unified_cut_prompt(
        locked_script_json="{}",
        segment_timings_json="{}",
        local_assets_json="[]",
        style_profile_text="s",
        dramaturgy_text="d",
        cut_rhythm_targets_text="CUT RHYTHM TARGETS\n",
    )
    assert "CUT RHYTHM TARGETS" in rhythm
    assert KEYWORD_FLOW_FREE_MARKER not in rhythm


def test_continuous_word_flow_is_flat_not_nested() -> None:
    rows = [
        {
            "sentence_id": "chapter_s001",
            "words": [
                {
                    "text": "vulkanischen",
                    "offset_seconds": 2.14,
                    "start_seconds": 2.14,
                    "end_seconds": 2.5,
                    "original_word_index": 7,
                },
                {
                    "text": "Kräften",
                    "offset_seconds": 2.61,
                    "start_seconds": 2.61,
                    "end_seconds": 3.0,
                    "original_word_index": 8,
                },
            ],
        },
        {
            "sentence_id": "chapter_s002",
            "words": [
                {
                    "text": "Heute",
                    "offset_seconds": 0.0,
                    "start_seconds": 5.0,
                    "end_seconds": 5.2,
                    "original_word_index": 0,
                },
                {
                    "text": "liegen",
                    "offset_seconds": 0.43,
                    "start_seconds": 5.43,
                    "end_seconds": 5.7,
                    "original_word_index": 1,
                },
            ],
        },
    ]
    flow = build_continuous_word_flow(rows)
    assert [w["text"] for w in flow] == [
        "vulkanischen",
        "Kräften",
        "Heute",
        "liegen",
    ]
    assert flow[0]["word_ref"] == "chapter_s001#7"
    assert flow[0]["sentence_id"] == "chapter_s001"
    assert flow[0]["offset_seconds"] == 2.14
    assert flow[2]["word_ref"] == "chapter_s002#0"
    assert flow[2]["offset_seconds"] == 0.0
    # Flat list — no nested words arrays.
    assert all("words" not in entry for entry in flow)


def test_mid_sentence_two_boundaries_same_sentence_valid() -> None:
    sentence_id = "chapter_s001"
    rows = {
        sentence_id: {
            "sentence_id": sentence_id,
            "start_seconds": 0.0,
            "end_seconds": 10.0,
            "words": [
                {
                    "text": "schwarzen",
                    "offset_seconds": 1.0,
                    "start_seconds": 1.0,
                    "end_seconds": 1.3,
                    "original_word_index": 2,
                },
                {
                    "text": "weißen",
                    "offset_seconds": 3.5,
                    "start_seconds": 3.5,
                    "end_seconds": 3.9,
                    "original_word_index": 5,
                },
                {
                    "text": "Meer",
                    "offset_seconds": 6.0,
                    "start_seconds": 6.0,
                    "end_seconds": 6.4,
                    "original_word_index": 9,
                },
            ],
        }
    }
    plan = UnifiedCutPlanDocument(
        script_version="v1",
        boundaries=[
            CutBoundary(
                cut_id="c0",
                sentence_id=sentence_id,
                position="start",
                offset_seconds=0.0,
                alignment="sentence_boundary",
            ),
            CutBoundary(
                cut_id="c1",
                sentence_id=sentence_id,
                position="middle",
                offset_seconds=1.0,
                alignment="mid_sentence",
            ),
            CutBoundary(
                cut_id="c2",
                sentence_id=sentence_id,
                position="middle",
                offset_seconds=3.5,
                alignment="mid_sentence",
            ),
            CutBoundary(
                cut_id="c3",
                sentence_id=sentence_id,
                position="end",
                alignment="sentence_boundary",
            ),
        ],
        slots=[
            CutSlot(slot_id="s1", local_asset_id="a1", asset_fit="strong"),
            CutSlot(slot_id="s2", local_asset_id="a2", asset_fit="strong"),
            CutSlot(slot_id="s3", local_asset_id="a3", asset_fit="strong"),
        ],
    )
    validate_keyword_flow_mid_sentence_onsets(plan, sentence_rows_by_id=rows)
    mid = [b for b in plan.boundaries if b.alignment == "mid_sentence"]
    assert len(mid) == 2
    assert mid[0].sentence_id == mid[1].sentence_id == sentence_id
    assert mid[0].offset_seconds != mid[1].offset_seconds


def test_sentence_overflow_and_no_forced_sentence_cut() -> None:
    """s001 → s002 word #4 yields a shot that crosses the sentence boundary."""
    s1 = "chapter_s001"
    s2 = "chapter_s002"
    plan = UnifiedCutPlanDocument(
        script_version="v1",
        boundaries=[
            CutBoundary(
                cut_id="c0",
                sentence_id=s1,
                position="start",
                offset_seconds=0.0,
                alignment="sentence_boundary",
            ),
            CutBoundary(
                cut_id="c1",
                sentence_id=s2,
                position="middle",
                offset_seconds=1.6,
                alignment="mid_sentence",
            ),
            CutBoundary(
                cut_id="c2",
                sentence_id=s2,
                position="end",
                alignment="sentence_boundary",
            ),
        ],
        slots=[
            CutSlot(
                slot_id="cross",
                local_asset_id="island",
                asset_fit="strong",
                covered_sentence_ids=[s1, s2],
            ),
            CutSlot(
                slot_id="harbor",
                local_asset_id="harbor",
                asset_fit="strong",
                covered_sentence_ids=[s2],
            ),
        ],
    )
    # Cross-boundary shot: first slot ends at mid_sentence in s002 (word #4).
    assert plan.boundaries[1].sentence_id == s2
    assert plan.boundaries[1].offset_seconds == 1.6
    assert s1 in (plan.slots[0].covered_sentence_ids or [])
    assert s2 in (plan.slots[0].covered_sentence_ids or [])
    # No forced cut at sentence start of s002 — only one mid cut after word 4.
    sentence_starts = [
        b
        for b in plan.boundaries
        if b.sentence_id == s2 and b.alignment == "sentence_boundary" and b.position == "start"
    ]
    assert sentence_starts == []


def test_coverage_gap_not_from_sentence_change() -> None:
    """A new sentence without its own asset is fine if prior shot continues."""
    s1 = "chapter_s001"
    s2 = "chapter_s002"
    plan = UnifiedCutPlanDocument(
        script_version="v1",
        boundaries=[
            CutBoundary(
                cut_id="c0",
                sentence_id=s1,
                position="start",
                offset_seconds=0.0,
                alignment="sentence_boundary",
            ),
            CutBoundary(
                cut_id="c1",
                sentence_id=s2,
                position="end",
                alignment="sentence_boundary",
            ),
        ],
        slots=[
            CutSlot(
                slot_id="hold",
                local_asset_id="caldera",
                asset_fit="strong",
                covered_sentence_ids=[s1, s2],
            )
        ],
    )
    _rough, coverage = unified_to_rough(plan)
    assert coverage.gaps == []

    # Genuine missing beat still creates a gap.
    gap_plan = UnifiedCutPlanDocument(
        script_version="v1",
        boundaries=[
            CutBoundary(
                cut_id="g0",
                sentence_id=s1,
                position="start",
                offset_seconds=0.0,
                alignment="sentence_boundary",
            ),
            CutBoundary(
                cut_id="g1",
                sentence_id=s1,
                position="end",
                alignment="sentence_boundary",
            ),
        ],
        slots=[
            CutSlot(
                slot_id="missing",
                local_asset_id=None,
                asset_fit="none",
                coverage_gap_id="gap_1",
                needed_visual="exact named volcano crater",
                search_concepts=["volcanic crater aerial", "caldera rim drone"],
            )
        ],
    )
    _rough2, coverage2 = unified_to_rough(gap_plan)
    assert len(coverage2.gaps) == 1


def test_invented_onset_fail_closed() -> None:
    sentence_id = "chapter_s001"
    rows = {
        sentence_id: {
            "sentence_id": sentence_id,
            "start_seconds": 0.0,
            "end_seconds": 5.0,
            "words": [
                {
                    "text": "Hafen",
                    "offset_seconds": 2.0,
                    "start_seconds": 2.0,
                    "end_seconds": 2.4,
                    "original_word_index": 4,
                }
            ],
        }
    }
    plan = UnifiedCutPlanDocument(
        script_version="v1",
        boundaries=[
            CutBoundary(
                cut_id="c0",
                sentence_id=sentence_id,
                position="start",
                offset_seconds=0.0,
                alignment="sentence_boundary",
            ),
            CutBoundary(
                cut_id="c1",
                sentence_id=sentence_id,
                position="middle",
                offset_seconds=1.234,
                alignment="mid_sentence",
            ),
            CutBoundary(
                cut_id="c2",
                sentence_id=sentence_id,
                position="end",
                alignment="sentence_boundary",
            ),
        ],
        slots=[
            CutSlot(slot_id="a", local_asset_id="x", asset_fit="strong"),
            CutSlot(slot_id="b", local_asset_id="y", asset_fit="strong"),
        ],
    )
    with pytest.raises(KeywordFlowTimingError, match="echten bereinigten Wort-Onset"):
        validate_keyword_flow_mid_sentence_onsets(plan, sentence_rows_by_id=rows)


def test_prompt_sections_are_hierarchical_and_short() -> None:
    prompt = build_keyword_flow_free_prompt(
        locked_script_json="{}",
        segment_timings_json="{}",
        local_assets_json="[]",
        style_profile_text="s",
        dramaturgy_text="d",
        continuous_word_flow_json="[]",
    )
    for section in (
        "ROLE",
        "EDITORIAL GOAL",
        "CUT DECISION",
        "ASSET DECISION",
        "TIMING CONTRACT",
        "OUTPUT CONTRACT",
        "CONTINUOUS WORD FLOW",
        "LOCAL ASSETS",
        "SHOT CONSTRAINTS",
        "OUTPUT SCHEMA",
    ):
        assert section in prompt
    # Must not smuggle the old KF long-form sections.
    assert "NAMED ENTITY PRIORITY (BINDING)" not in prompt
    assert "PAUSE RULES (DISABLED)" not in prompt
    assert "ATMOSPHERIC PASSAGES WITHOUT KEYWORD" not in prompt
    # No pseudo-realistic mid-sentence floats that could prime the LLM.
    assert '"offset_seconds": 1.05' not in prompt
    assert '"offset_seconds": 3.40' not in prompt
    assert '"offset_seconds": 1.82' not in prompt
    assert "Never estimate, interpolate, invent" in prompt
    assert "Do not create a gap merely because a new sentence begins" in prompt


def test_prompt_has_no_cut_quotas() -> None:
    prompt = build_keyword_flow_free_prompt(
        locked_script_json="{}",
        segment_timings_json="{}",
        local_assets_json="[]",
        style_profile_text="s",
        dramaturgy_text="d",
        continuous_word_flow_json="[]",
    )
    lowered = prompt.lower()
    assert "mindestens 2 shots" not in lowered
    assert "mindestens 50 %" not in lowered
    assert "jeder relevante begriff benötigt einen cut" not in lowered


def test_existing_keyword_flow_prompt_body_untouched() -> None:
    """Regression guard: KF prompt still has its established markers."""
    kf = build_keyword_flow_unified_cut_prompt(
        locked_script_json="{}",
        segment_timings_json="{}",
        local_assets_json="[]",
        style_profile_text="s",
        dramaturgy_text="d",
    )
    assert "KEYWORD FLOW MARKER" in kf
    assert "NAMED ENTITY PRIORITY (BINDING)" in kf
    assert "PAUSE RULES (DISABLED)" in kf
    source = Path(
        "otio_app/services/without_voiceover_enhanced/script_prompts.py"
    ).read_text(encoding="utf-8")
    # Free prompt must live in its own module, not as if-branches inside KF builder.
    assert "keyword_flow_free" not in source
    assert "KEYWORD FLOW FREE" not in source


def test_build_continuous_word_flow_from_sentence_rows_alias() -> None:
    rows = [
        {
            "sentence_id": "s",
            "words": [
                {
                    "text": "Hallo",
                    "offset_seconds": 0.0,
                    "start_seconds": 0.0,
                    "end_seconds": 0.2,
                    "original_word_index": 0,
                }
            ],
        }
    ]
    assert build_continuous_word_flow_from_sentence_rows(rows) == build_continuous_word_flow(
        rows
    )
