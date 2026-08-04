"""Keyword Flow: Style, Prompt, Words, Onset, Pause, Fit, Maps, Closing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
    UNIFIED_CUT_STYLE_KEYWORD_FLOW,
    UNIFIED_CUT_STYLE_KEYWORD_SYNC,
    UNIFIED_CUT_STYLE_RHYTHM,
    CutPlanOptions,
    _normalize_unified_cut_style,
    is_keyword_flow_unified_style,
    is_keyword_sync_unified_style,
)
from otio_app.services.without_voiceover_enhanced.keyword_flow_timing import (
    KeywordFlowTimingError,
    apply_keyword_flow_onset_tolerance,
    choose_onset_within_tolerance,
)
from otio_app.services.without_voiceover_enhanced.models import (
    CutBoundary,
    CutSlot,
    PauseDirective,
    SegmentTiming,
    SentenceTiming,
    UnifiedCutPlanDocument,
)
from otio_app.services.without_voiceover_enhanced.pause_config import (
    KEYWORD_FLOW_PAUSE_DURATION_SECONDS,
    resolve_keyword_flow_pause_duration_seconds,
    resolve_pause_duration_seconds,
)
from otio_app.services.without_voiceover_enhanced.pause_resolver import (
    PauseResolveError,
    build_narration_timeline,
    safe_pause_window_timeline,
)
from otio_app.services.without_voiceover_enhanced.script_prompts import (
    build_intro_unified_cut_prompt,
    build_keyword_flow_unified_cut_prompt,
    build_keyword_sync_unified_cut_prompt,
    build_unified_cut_prompt,
)
from otio_app.services.without_voiceover_enhanced.sentence_timing_prompt import (
    chapter_has_usable_keyword_flow_words,
    clean_words_for_keyword_flow_prompt,
    is_direction_or_non_speech_token,
)
from otio_app.services.without_voiceover_enhanced.unified_cut_plan import (
    parse_unified_cut_response,
    unified_to_rough,
)


def test_style_normalization_keyword_flow_and_aliases() -> None:
    assert (
        _normalize_unified_cut_style("keyword_flow", default="rhythm")
        == UNIFIED_CUT_STYLE_KEYWORD_FLOW
    )
    assert (
        _normalize_unified_cut_style("keyword-flow", default="rhythm")
        == UNIFIED_CUT_STYLE_KEYWORD_FLOW
    )
    assert (
        _normalize_unified_cut_style("semantic_keyword_flow", default="rhythm")
        == UNIFIED_CUT_STYLE_KEYWORD_FLOW
    )
    assert (
        _normalize_unified_cut_style("semantic-keyword-flow", default="rhythm")
        == UNIFIED_CUT_STYLE_KEYWORD_FLOW
    )
    assert (
        _normalize_unified_cut_style("unknown_style", default="rhythm")
        == UNIFIED_CUT_STYLE_RHYTHM
    )
    opts = CutPlanOptions(unified_cut_style=UNIFIED_CUT_STYLE_KEYWORD_FLOW)
    assert is_keyword_flow_unified_style(opts)
    assert not is_keyword_sync_unified_style(opts)


def test_ui_contains_keyword_flow_label() -> None:
    source = Path(
        "otio_app/ui/without_voiceover_enhanced/cut_plan_tab.py"
    ).read_text(encoding="utf-8")
    assert 'UNIFIED_CUT_STYLE_KEYWORD_FLOW: "Keyword Flow"' in source
    assert "echten Wort-Onsets" in source
    assert "±1,5-s-Platzierung" in source


def test_dispatch_uses_keyword_flow_builder() -> None:
    source = Path(
        "otio_app/services/without_voiceover_enhanced/cut_plan_service.py"
    ).read_text(encoding="utf-8")
    assert "build_keyword_flow_unified_cut_prompt" in source
    assert "is_keyword_flow_unified_style" in source
    assert "Keyword Flow benötigt echte ElevenLabs-Wort-Timestamps" in source


def test_keyword_flow_prompt_contract() -> None:
    prompt = build_keyword_flow_unified_cut_prompt(
        locked_script_json="{}",
        segment_timings_json="{}",
        local_assets_json="[]",
        style_profile_text="style",
        dramaturgy_text="dram",
        folder_name="Glendalough",
        folder_slug="Glendalough",
        shot_constraints_text=(
            "SHOT / ASSET CONSTRAINTS\n"
            "- max asset usage 2\n"
            "- reuse gap 4\n"
            "- shot_min 4 / shot_max 9\n"
        ),
        sentence_timings_json='[{"sentence_id":"s","words":[{"text":"tower","offset_seconds":1.2}]}]',
    )
    assert "KEYWORD FLOW MARKER" in prompt
    assert "context-first" in prompt
    assert "NAMED ENTITY PRIORITY" in prompt
    assert "Salto Ángel" in prompt
    assert "ATMOSPHERIC PASSAGES WITHOUT KEYWORD" in prompt
    assert "NEVER invent or estimate word onsets" in prompt
    assert "±1.5" in prompt or "1.5 seconds" in prompt
    assert "shot_min" in prompt and "shot_max" in prompt
    assert "max asset usage 2" in prompt
    assert "reuse gap 4" in prompt
    assert "5 timeline frames" in prompt
    assert "Do NOT plan the Maps folder opener" in prompt
    assert "closing_fallback_asset_id" in prompt
    assert "unified-cut-v1" in prompt
    assert "CUT RHYTHM TARGETS" not in prompt
    assert "local_asset_id MUST be null" in prompt


def test_rhythm_and_keyword_sync_prompts_unchanged_markers() -> None:
    rhythm = build_unified_cut_prompt(
        locked_script_json="{}",
        segment_timings_json="{}",
        local_assets_json="[]",
        style_profile_text="s",
        dramaturgy_text="d",
        cut_rhythm_targets_text="CUT RHYTHM TARGETS\n",
    )
    assert "pause_directives are disabled" in rhythm or '"pause_directives": []' in rhythm
    sync = build_keyword_sync_unified_cut_prompt(
        locked_script_json="{}",
        segment_timings_json="{}",
        local_assets_json="[]",
        style_profile_text="s",
        dramaturgy_text="d",
    )
    assert "KEYWORD-SYNC cut planner" in sync
    assert "KEYWORD FLOW MARKER" not in sync
    intro = build_intro_unified_cut_prompt(
        locked_script_json="{}",
        segment_timings_json="{}",
        bundled_inventory_json="{}",
        style_profile_text="s",
        dramaturgy_text="d",
    )
    assert "KEYWORD FLOW MARKER" not in intro


def test_word_cleaning_keeps_speech_drops_tags() -> None:
    raw = [
        {"text": "[cinematic]", "start_seconds": 0.0, "end_seconds": 0.1, "offset_seconds": 0.0, "original_word_index": 0},
        {"text": "tower,", "start_seconds": 0.2, "end_seconds": 0.5, "offset_seconds": 0.2, "original_word_index": 1},
        {"text": "monastery.", "start_seconds": 0.6, "end_seconds": 1.0, "offset_seconds": 0.6, "original_word_index": 2},
        {"text": "[pause", "start_seconds": 1.1, "end_seconds": 1.2, "offset_seconds": 1.1, "original_word_index": 3},
        {"text": "2", "start_seconds": 1.2, "end_seconds": 1.3, "offset_seconds": 1.2, "original_word_index": 4},
        {"text": "seconds]", "start_seconds": 1.3, "end_seconds": 1.4, "offset_seconds": 1.3, "original_word_index": 5},
        {"text": "—", "start_seconds": 1.5, "end_seconds": 1.6, "offset_seconds": 1.5, "original_word_index": 6},
        {"text": "...", "start_seconds": 1.7, "end_seconds": 1.8, "offset_seconds": 1.7, "original_word_index": 7},
        {"text": "waterfall?", "start_seconds": 2.0, "end_seconds": 2.4, "offset_seconds": 2.0, "original_word_index": 8},
        {"text": "400", "start_seconds": 2.5, "end_seconds": 2.8, "offset_seconds": 2.5, "original_word_index": 9},
        {"text": "1889", "start_seconds": 2.9, "end_seconds": 3.2, "offset_seconds": 2.9, "original_word_index": 10},
        {"text": "12.5", "start_seconds": 3.3, "end_seconds": 3.6, "offset_seconds": 3.3, "original_word_index": 11},
    ]
    cleaned = clean_words_for_keyword_flow_prompt(raw, sentence_id="seg__s001")
    texts = [w["text"] for w in cleaned]
    assert texts == ["tower,", "monastery.", "waterfall?", "400", "1889", "12.5"]
    assert cleaned[0]["start_seconds"] == 0.2
    assert cleaned[0]["word_ref"] == "seg__s001#1"
    assert is_direction_or_non_speech_token("[cinematic]")
    assert is_direction_or_non_speech_token("-")
    assert not is_direction_or_non_speech_token("400")
    assert not chapter_has_usable_keyword_flow_words(
        [{"words": [{"text": "[cinematic]", "offset_seconds": 0}]}]
    )


def test_parse_keyword_flow_nullifies_weak_and_keeps_pauses() -> None:
    payload = {
        "closing_fallback_asset_id": "asset_b",
        "closing_fallback_asset_fit": "strong",
        "closing_fallback_asset_fit_reason": "reserve closer",
        "closing_fallback_visual_intent": "same closing intent as primary",
        "pause_directives": [
            {
                "after_segment_id": "seg",
                "after_sentence_id": "seg__s001",
                "pause_function": "anticipation",
                "duration_class": "long",
                "visual_behavior": "next_shot_may_start_during_pause",
                "editorial_reason": "space",
            }
        ],
        "boundaries": [
            {
                "cut_id": "c0",
                "sentence_id": "seg__s001",
                "position": "start",
                "offset_seconds": 0,
                "alignment": "sentence_boundary",
            },
            {
                "cut_id": "c1",
                "sentence_id": "seg__s001",
                "position": "end",
                "offset_seconds": None,
                "alignment": "sentence_boundary",
            },
            {
                "cut_id": "c2",
                "sentence_id": "seg__s002",
                "position": "end",
                "offset_seconds": None,
                "alignment": "sentence_boundary",
            },
        ],
        "slots": [
            {
                "slot_id": "s1",
                "local_asset_id": "asset_weak",
                "asset_fit": "weak",
                "asset_fit_reason": "near miss",
                "needed_visual": "exact named waterfall",
                "search_concepts": ["named waterfall cliff", "cascade mist aerial"],
                "coverage_gap_id": "g1",
            },
            {
                "slot_id": "s2",
                "local_asset_id": "asset_a",
                "asset_fit": "strong",
                "asset_fit_reason": "closing",
            },
        ],
    }
    plan = parse_unified_cut_response(
        payload,
        "script-v1",
        folder_slug="Chap",
        allow_pause_directives=True,
        nullify_weak_assets=True,
    )
    assert plan.slots[0].local_asset_id is None
    assert plan.slots[0].coverage_gap_id
    assert plan.slots[1].local_asset_id == "asset_a"
    assert plan.closing_fallback_asset_id == "asset_b"
    assert plan.closing_fallback_asset_fit == "strong"
    assert plan.closing_fallback_asset_fit_reason == "reserve closer"
    assert plan.closing_fallback_visual_intent == "same closing intent as primary"
    assert len(plan.pause_directives) == 1
    assert plan.pause_directives[0].duration_class == "long"
    rough, coverage = unified_to_rough(plan)
    assert coverage.gaps
    assert rough.shots[0].local_asset_id is None


def test_parse_rhythm_still_keeps_weak_asset_and_strips_pauses() -> None:
    payload = {
        "pause_directives": [
            {
                "after_sentence_id": "seg__s001",
                "pause_function": "breath",
                "duration_class": "short",
            }
        ],
        "boundaries": [
            {
                "cut_id": "c0",
                "sentence_id": "a",
                "position": "start",
                "offset_seconds": 0,
                "alignment": "sentence_boundary",
            },
            {
                "cut_id": "c1",
                "sentence_id": "a",
                "position": "end",
                "alignment": "sentence_boundary",
            },
        ],
        "slots": [
            {
                "slot_id": "s1",
                "local_asset_id": "keep_me",
                "asset_fit": "weak",
                "needed_visual": "x",
                "search_concepts": ["a b", "c d"],
            }
        ],
    }
    plan = parse_unified_cut_response(payload, "script-v1")
    assert plan.slots[0].local_asset_id == "keep_me"
    assert plan.pause_directives == []


def test_keyword_flow_pause_durations_exact() -> None:
    assert resolve_keyword_flow_pause_duration_seconds("short") == pytest.approx(0.35)
    assert resolve_keyword_flow_pause_duration_seconds("medium") == pytest.approx(0.80)
    assert resolve_keyword_flow_pause_duration_seconds("long") == pytest.approx(1.50)
    # Legacy rhythm durations unchanged.
    assert resolve_pause_duration_seconds("short") == pytest.approx(0.50)
    assert KEYWORD_FLOW_PAUSE_DURATION_SECONDS["long"] == 1.50


def test_safe_pause_window_five_frames() -> None:
    start, end = safe_pause_window_timeline(
        previous_word_end_timeline=10.0,
        next_word_start_timeline=12.0,
        fps=25.0,
    )
    assert start == pytest.approx(10.2)
    assert end == pytest.approx(11.8)
    start29, end29 = safe_pause_window_timeline(
        previous_word_end_timeline=10.0,
        next_word_start_timeline=12.0,
        fps=29.97,
    )
    margin = 5 / 29.97
    assert start29 == pytest.approx(10.0 + margin)
    assert end29 == pytest.approx(12.0 - margin)


def test_keyword_flow_pause_allows_small_natural_gap_when_extra_creates_safety() -> None:
    """Natural gap < 10 frames is OK if inserted silence creates ±5-frame window."""
    sentences = {
        "seg__s001": SentenceTiming(
            sentence_id="seg__s001",
            segment_id="seg",
            text="One.",
            start_seconds=0.0,
            end_seconds=1.0,
            duration_seconds=1.0,
        ),
        "seg__s002": SentenceTiming(
            sentence_id="seg__s002",
            segment_id="seg",
            text="Two.",
            start_seconds=1.1,
            end_seconds=2.0,
            duration_seconds=0.9,
        ),
    }
    # Natural gap only 0.2s (5 frames @25fps) — previously blocked as < 0.4s.
    words = [
        {"text": "One", "start_seconds": 0.0, "end_seconds": 0.95},
        {"text": "Two", "start_seconds": 1.15, "end_seconds": 1.9},
    ]
    timeline = build_narration_timeline(
        script_version="v1",
        segment_timings=[
            SegmentTiming(
                segment_id="seg",
                script_version="v1",
                audio_path="/tmp/x.wav",
                duration_seconds=2.0,
                audio_status="valid",
            )
        ],
        pause_directives=[
            PauseDirective(
                after_sentence_id="seg__s001",
                pause_function="anticipation",
                duration_class="long",
                visual_behavior="next_shot_may_start_during_pause",
            )
        ],
        sentence_index=sentences,
        enable_keyword_flow_pauses=True,
        segment_words_by_id={"seg": words},
        fps=25.0,
        repairs=[],
    )
    assert timeline.entries[0].intra_pauses
    assert timeline.entries[0].intra_pauses[0].pause_seconds == pytest.approx(1.5)
    assert words[1]["start_seconds"] == 1.15


def test_keyword_flow_pauses_shift_timeline_not_source_words() -> None:
    sentences = {
        "seg__s001": SentenceTiming(
            sentence_id="seg__s001",
            segment_id="seg",
            text="One.",
            start_seconds=0.0,
            end_seconds=1.0,
            duration_seconds=1.0,
        ),
        "seg__s002": SentenceTiming(
            sentence_id="seg__s002",
            segment_id="seg",
            text="Two.",
            start_seconds=1.5,
            end_seconds=2.5,
            duration_seconds=1.0,
        ),
    }
    words = [
        {"text": "One", "start_seconds": 0.0, "end_seconds": 0.8},
        {"text": "Two", "start_seconds": 1.6, "end_seconds": 2.4},
    ]
    directives = [
        PauseDirective(
            after_segment_id="seg",
            after_sentence_id="seg__s001",
            pause_function="anticipation",
            duration_class="long",
            visual_behavior="next_shot_may_start_during_pause",
        )
    ]
    repairs: list[str] = []
    timeline = build_narration_timeline(
        script_version="v1",
        segment_timings=[
            SegmentTiming(
                segment_id="seg",
                script_version="v1",
                audio_path="/tmp/x.wav",
                duration_seconds=3.0,
                audio_status="valid",
            )
        ],
        pause_directives=directives,
        sentence_index=sentences,
        enable_keyword_flow_pauses=True,
        segment_words_by_id={"seg": words},
        fps=25.0,
        repairs=repairs,
    )
    assert timeline.entries[0].intra_pauses
    assert timeline.entries[0].intra_pauses[0].pause_seconds == pytest.approx(1.5)
    # Source-relative word times unchanged.
    assert words[1]["start_seconds"] == 1.6
    assert any("keyword_flow_pause" in r for r in repairs)


def test_keyword_flow_pause_disabled_by_default() -> None:
    timeline = build_narration_timeline(
        script_version="v1",
        segment_timings=[
            SegmentTiming(
                segment_id="seg",
                script_version="v1",
                audio_path="/tmp/x.wav",
                duration_seconds=2.0,
                audio_status="valid",
            )
        ],
        pause_directives=[
            PauseDirective(
                after_sentence_id="x",
                pause_function="breath",
                duration_class="long",
            )
        ],
    )
    assert timeline.entries[0].intra_pauses == []
    assert timeline.entries[0].pause_after_seconds == 0.0


def test_pause_without_words_blocks() -> None:
    sentences = {
        "seg__s001": SentenceTiming(
            sentence_id="seg__s001",
            segment_id="seg",
            text="One.",
            start_seconds=0.0,
            end_seconds=1.0,
            duration_seconds=1.0,
        ),
        "seg__s002": SentenceTiming(
            sentence_id="seg__s002",
            segment_id="seg",
            text="Two.",
            start_seconds=1.2,
            end_seconds=2.0,
            duration_seconds=0.8,
        ),
    }
    with pytest.raises(PauseResolveError, match="ohne vorheriges Wortende"):
        build_narration_timeline(
            script_version="v1",
            segment_timings=[
                SegmentTiming(
                    segment_id="seg",
                    script_version="v1",
                    audio_path="/tmp/x.wav",
                    duration_seconds=2.0,
                    audio_status="valid",
                )
            ],
            pause_directives=[
                PauseDirective(
                    after_sentence_id="seg__s001",
                    pause_function="breath",
                    duration_class="short",
                )
            ],
            sentence_index=sentences,
            enable_keyword_flow_pauses=True,
            segment_words_by_id={"seg": []},
            fps=25.0,
        )


def test_onset_tolerance_priority_and_block() -> None:
    assert choose_onset_within_tolerance(
        onset=10.0, candidates=[10.0, 11.0, 9.0]
    ) == pytest.approx(10.0)
    assert choose_onset_within_tolerance(
        onset=10.0, candidates=[11.2, 8.8]
    ) == pytest.approx(11.2)
    assert choose_onset_within_tolerance(
        onset=10.0, candidates=[8.5]
    ) == pytest.approx(8.5)
    assert choose_onset_within_tolerance(onset=10.0, candidates=[12.0]) is None

    plan = UnifiedCutPlanDocument(
        script_version="v1",
        boundaries=[
            CutBoundary(
                cut_id="b0",
                sentence_id="s",
                position="start",
                offset_seconds=0,
                alignment="sentence_boundary",
            ),
            CutBoundary(
                cut_id="b1",
                sentence_id="s",
                position="middle",
                offset_seconds=1.0,
                alignment="mid_sentence",
            ),
            CutBoundary(
                cut_id="b2",
                sentence_id="s",
                position="end",
                alignment="sentence_boundary",
            ),
        ],
        slots=[
            CutSlot(slot_id="a", local_asset_id="x", asset_fit="strong"),
            CutSlot(slot_id="b", local_asset_id="y", asset_fit="strong"),
        ],
    )
    repairs: list[str] = []
    out = apply_keyword_flow_onset_tolerance(
        plan=plan,
        raw_times=[0.0, 5.0, 12.0],
        clamped_times=[0.0, 6.2, 12.0],
        repairs=repairs,
    )
    assert out[1] == pytest.approx(6.2)
    assert any("onset shift" in r for r in repairs)
    with pytest.raises(KeywordFlowTimingError, match="überschreitet"):
        apply_keyword_flow_onset_tolerance(
            plan=plan,
            raw_times=[0.0, 5.0, 12.0],
            clamped_times=[0.0, 7.0, 12.0],
            repairs=[],
        )
    overflow_repairs: list[str] = []
    overflow = apply_keyword_flow_onset_tolerance(
        plan=plan,
        raw_times=[0.0, 5.0, 12.0],
        clamped_times=[0.0, 7.0, 12.0],
        repairs=overflow_repairs,
        allow_overflow=True,
    )
    assert overflow[1] == pytest.approx(7.0)
    assert any("WARNING accepted onset overflow" in r for r in overflow_repairs)


def test_twelve_second_theme_pause_extends_second_shot() -> None:
    """12s Thema, shot_max 9 / shot_min 4: 3s + long(+1.5) → 4.5s Shot."""
    # Narration pieces conceptually 9s + 3s; pause adds 1.5 to second shot span.
    first = 9.0
    second_narration = 3.0
    pause = resolve_keyword_flow_pause_duration_seconds("long")
    second_shot = second_narration + pause
    assert first <= 9.0 + 1e-9
    assert second_shot == pytest.approx(4.5)
    assert second_shot >= 4.0


def test_map_decision_missing_and_intro_skip(tmp_path: Path) -> None:
    from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
    from otio_app.models import Project, ProjectMode
    from otio_app.services.without_voiceover_enhanced.keyword_flow_maps import (
        decide_map_opener,
    )

    root = tmp_path / "proj"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    project = Project(
        name="MapTest",
        project_root=str(root),
        work_dir=str(work),
        language="en",
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        fps=25.0,
        frames_per_shot=3,
        selected_asset_subdirs=["ChapterA"],
        asset_subdir_names=["ChapterA"],
    )
    missing = decide_map_opener(project, "ChapterA")
    assert missing.status == "missing"
    assert "keine Map" in missing.warning
    intro = decide_map_opener(project, "Intro")
    assert intro.status == "skipped_intro"


def test_closing_validator_requires_distinct_fallback() -> None:
    from otio_app.services.without_voiceover_enhanced.keyword_flow_closing import (
        validate_keyword_flow_closing,
    )

    plan = UnifiedCutPlanDocument(
        script_version="v1",
        closing_fallback_asset_id="same",
        closing_fallback_asset_fit="acceptable",
        closing_fallback_asset_fit_reason="x",
        closing_fallback_visual_intent="y",
        boundaries=[
            CutBoundary(
                cut_id="b0",
                sentence_id="s",
                position="start",
                offset_seconds=0,
                alignment="sentence_boundary",
            ),
            CutBoundary(
                cut_id="b1",
                sentence_id="s",
                position="end",
                alignment="sentence_boundary",
            ),
        ],
        slots=[
            CutSlot(
                slot_id="last",
                local_asset_id="same",
                asset_fit="strong",
            )
        ],
    )
    errs = validate_keyword_flow_closing(plan)
    assert any("nicht dem Primary" in e for e in errs)


def test_legacy_plan_without_fallback_fit_still_parses() -> None:
    """Ältere Pläne bleiben lesbar; Fit-Felder sind optional im Schema."""
    payload = {
        "closing_fallback_asset_id": "asset_b",
        "boundaries": [
            {
                "cut_id": "c0",
                "sentence_id": "seg__s001",
                "position": "start",
                "offset_seconds": 0,
                "alignment": "sentence_boundary",
            },
            {
                "cut_id": "c1",
                "sentence_id": "seg__s001",
                "position": "end",
                "alignment": "sentence_boundary",
            },
        ],
        "slots": [
            {
                "slot_id": "s1",
                "local_asset_id": "asset_a",
                "asset_fit": "strong",
            }
        ],
    }
    plan = parse_unified_cut_response(payload, "script-v1")
    assert plan.closing_fallback_asset_id == "asset_b"
    assert plan.closing_fallback_asset_fit is None
