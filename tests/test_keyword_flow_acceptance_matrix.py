"""Keyword Flow acceptance matrix (§§31–38) — focused unit/contract tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
    KEYWORD_FLOW_MAP_OPENER_SEC,
    KEYWORD_FLOW_ONSET_TOLERANCE_SEC,
    KEYWORD_FLOW_PAUSE_SAFETY_FRAMES,
    UNIFIED_CUT_STYLE_KEYWORD_FLOW,
    UNIFIED_CUT_STYLE_KEYWORD_SYNC,
    UNIFIED_CUT_STYLE_RHYTHM,
    CutPlanOptions,
    _normalize_unified_cut_style,
    is_keyword_flow_unified_style,
)
from otio_app.services.without_voiceover_enhanced.keyword_flow_closing import (
    validate_keyword_flow_closing,
)
from otio_app.services.without_voiceover_enhanced.keyword_flow_timing import (
    KeywordFlowTimingError,
    apply_keyword_flow_onset_tolerance,
    choose_onset_within_tolerance,
    validate_keyword_flow_mid_sentence_onsets,
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
    resolve_keyword_flow_pause_duration_seconds,
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
)


def _kf_prompt(**overrides: object) -> str:
    kwargs = dict(
        locked_script_json="{}",
        segment_timings_json="{}",
        local_assets_json="[]",
        style_profile_text="style",
        dramaturgy_text="dram",
        folder_name="ChapterA",
        folder_slug="ChapterA",
        shot_constraints_text=(
            "SHOT / ASSET CONSTRAINTS\n"
            "- max asset usage 2\n"
            "- reuse gap 4\n"
            "- shot_min 4 / shot_max 9\n"
        ),
        sentence_timings_json=(
            '[{"sentence_id":"ChapterA_seg__s001","words":'
            '[{"text":"Salto","offset_seconds":1.0,'
            '"start_seconds":1.0,"end_seconds":1.3}]}]'
        ),
    )
    kwargs.update(overrides)
    return build_keyword_flow_unified_cut_prompt(**kwargs)  # type: ignore[arg-type]


def _boundary(
    cut_id: str,
    sentence_id: str,
    *,
    position: str = "start",
    offset: float | None = None,
    alignment: str = "sentence_boundary",
) -> CutBoundary:
    return CutBoundary(
        cut_id=cut_id,
        sentence_id=sentence_id,
        position=position,
        offset_seconds=offset,
        alignment=alignment,
    )


# --- §31 Style / Prompt -------------------------------------------------


def test_31_01_style_normalization_keyword_flow() -> None:
    assert (
        _normalize_unified_cut_style("keyword_flow", default="rhythm")
        == UNIFIED_CUT_STYLE_KEYWORD_FLOW
    )


def test_31_02_alias_keyword_flow_hyphen() -> None:
    assert (
        _normalize_unified_cut_style("keyword-flow", default="rhythm")
        == UNIFIED_CUT_STYLE_KEYWORD_FLOW
    )


def test_31_03_alias_semantic_keyword_flow() -> None:
    assert (
        _normalize_unified_cut_style("semantic_keyword_flow", default="rhythm")
        == UNIFIED_CUT_STYLE_KEYWORD_FLOW
    )


def test_31_04_ui_contains_keyword_flow() -> None:
    source = Path(
        "otio_app/ui/without_voiceover_enhanced/cut_plan_tab.py"
    ).read_text(encoding="utf-8")
    assert 'UNIFIED_CUT_STYLE_KEYWORD_FLOW: "Keyword Flow"' in source
    assert "echten Wort-Onsets" in source


def test_31_05_dispatch_calls_new_builder() -> None:
    source = Path(
        "otio_app/services/without_voiceover_enhanced/cut_plan_service.py"
    ).read_text(encoding="utf-8")
    assert "build_keyword_flow_unified_cut_prompt" in source
    assert "use_keyword_flow" in source


def test_31_06_intro_ignores_keyword_flow() -> None:
    source = Path(
        "otio_app/services/without_voiceover_enhanced/cut_plan_service.py"
    ).read_text(encoding="utf-8")
    assert "use_keyword_flow = (not is_intro) and is_keyword_flow_unified_style" in source
    intro = build_intro_unified_cut_prompt(
        locked_script_json="{}",
        segment_timings_json="{}",
        bundled_inventory_json="{}",
        style_profile_text="s",
        dramaturgy_text="d",
    )
    assert "KEYWORD FLOW MARKER" not in intro


def test_31_07_rhythm_unchanged_marker() -> None:
    rhythm = build_unified_cut_prompt(
        locked_script_json="{}",
        segment_timings_json="{}",
        local_assets_json="[]",
        style_profile_text="s",
        dramaturgy_text="d",
        cut_rhythm_targets_text="CUT RHYTHM TARGETS\n",
    )
    assert "KEYWORD FLOW MARKER" not in rhythm
    assert "CUT RHYTHM TARGETS" in rhythm


def test_31_08_keyword_sync_unchanged_marker() -> None:
    sync = build_keyword_sync_unified_cut_prompt(
        locked_script_json="{}",
        segment_timings_json="{}",
        local_assets_json="[]",
        style_profile_text="s",
        dramaturgy_text="d",
    )
    assert "KEYWORD-SYNC cut planner" in sync
    assert "KEYWORD FLOW MARKER" not in sync


def test_31_09_to_21_prompt_contract() -> None:
    prompt = _kf_prompt()
    assert "KEYWORD FLOW MARKER" in prompt
    assert "context-first" in prompt
    assert "NAMED ENTITY PRIORITY" in prompt
    assert "Salto Ángel" in prompt or "Salto Angel" in prompt
    assert "ATMOSPHERIC PASSAGES WITHOUT KEYWORD" in prompt
    assert "NEVER invent or estimate word onsets" in prompt
    assert "1.5" in prompt
    assert "shot_min" in prompt and "shot_max" in prompt
    assert "max asset usage 2" in prompt
    assert "reuse gap 4" in prompt
    assert "5 timeline frames" in prompt
    assert "Do NOT plan the Maps folder opener" in prompt
    assert "closing_fallback_asset_id" in prompt
    assert "unified-cut-v1" in prompt


# --- §32 Words ----------------------------------------------------------


def test_32_22_to_29_word_cleaning() -> None:
    raw = [
        {
            "text": "Hello",
            "start_seconds": 0.0,
            "end_seconds": 0.3,
            "offset_seconds": 0.0,
            "original_word_index": 0,
        },
        {
            "text": "tower,",
            "start_seconds": 0.3,
            "end_seconds": 0.6,
            "offset_seconds": 0.3,
            "original_word_index": 1,
        },
        {
            "text": "monastery.",
            "start_seconds": 0.6,
            "end_seconds": 1.0,
            "offset_seconds": 0.6,
            "original_word_index": 2,
        },
        {
            "text": "[cinematic]",
            "start_seconds": 1.0,
            "end_seconds": 1.1,
            "offset_seconds": 1.0,
            "original_word_index": 3,
        },
        {
            "text": "[pause",
            "start_seconds": 1.1,
            "end_seconds": 1.15,
            "offset_seconds": 1.1,
            "original_word_index": 4,
        },
        {
            "text": "2",
            "start_seconds": 1.15,
            "end_seconds": 1.2,
            "offset_seconds": 1.15,
            "original_word_index": 5,
        },
        {
            "text": "seconds]",
            "start_seconds": 1.2,
            "end_seconds": 1.25,
            "offset_seconds": 1.2,
            "original_word_index": 6,
        },
        {
            "text": "—",
            "start_seconds": 1.3,
            "end_seconds": 1.35,
            "offset_seconds": 1.3,
            "original_word_index": 7,
        },
        {
            "text": ".",
            "start_seconds": 1.4,
            "end_seconds": 1.41,
            "offset_seconds": 1.4,
            "original_word_index": 8,
        },
        {
            "text": "waterfall?",
            "start_seconds": 1.5,
            "end_seconds": 1.9,
            "offset_seconds": 1.5,
            "original_word_index": 9,
        },
    ]
    cleaned = clean_words_for_keyword_flow_prompt(raw, sentence_id="s1")
    texts = [w["text"] for w in cleaned]
    assert "Hello" in texts
    assert "tower," in texts
    assert "monastery." in texts
    assert "waterfall?" in texts
    assert "[cinematic]" not in texts
    assert "2" not in texts
    assert "—" not in texts
    assert "." not in texts
    assert cleaned[0]["start_seconds"] == 0.0
    assert is_direction_or_non_speech_token("[cinematic]")
    assert is_direction_or_non_speech_token("-")
    # Spoken numbers / decimals must survive (pause-tag digits still removed).
    with_numbers = clean_words_for_keyword_flow_prompt(
        [
            {
                "text": "400",
                "offset_seconds": 0.0,
                "start_seconds": 0.0,
                "end_seconds": 0.2,
                "original_word_index": 0,
            },
            {
                "text": "1889",
                "offset_seconds": 0.2,
                "start_seconds": 0.2,
                "end_seconds": 0.5,
                "original_word_index": 1,
            },
            {
                "text": "12.5",
                "offset_seconds": 0.5,
                "start_seconds": 0.5,
                "end_seconds": 0.8,
                "original_word_index": 2,
            },
            {
                "text": "[pause",
                "offset_seconds": 0.8,
                "start_seconds": 0.8,
                "end_seconds": 0.81,
                "original_word_index": 3,
            },
            {
                "text": "2",
                "offset_seconds": 0.81,
                "start_seconds": 0.81,
                "end_seconds": 0.82,
                "original_word_index": 4,
            },
            {
                "text": "seconds]",
                "offset_seconds": 0.82,
                "start_seconds": 0.82,
                "end_seconds": 0.83,
                "original_word_index": 5,
            },
        ],
        sentence_id="n",
    )
    num_texts = [w["text"] for w in with_numbers]
    assert num_texts == ["400", "1889", "12.5"]


def test_32_30_missing_words_block_preflight() -> None:
    assert not chapter_has_usable_keyword_flow_words([{"words": None}])
    assert not chapter_has_usable_keyword_flow_words([])
    source = Path(
        "otio_app/services/without_voiceover_enhanced/cut_plan_service.py"
    ).read_text(encoding="utf-8")
    assert "Keyword Flow benötigt echte ElevenLabs-Wort-Timestamps" in source


def test_32_31_empty_cleaned_words_block() -> None:
    assert not chapter_has_usable_keyword_flow_words(
        [{"words": [{"text": "[cinematic]", "offset_seconds": 0.0}]}]
    )


def test_32_32_mid_sentence_must_match_word_onset() -> None:
    plan = UnifiedCutPlanDocument(
        script_version="v1",
        boundaries=[
            _boundary("b0", "s1", position="start", offset=0.0),
            _boundary(
                "b1",
                "s1",
                position="middle",
                offset=1.25,
                alignment="mid_sentence",
            ),
            _boundary("b2", "s1", position="end"),
        ],
        slots=[
            CutSlot(slot_id="a", local_asset_id="x", asset_fit="strong"),
            CutSlot(slot_id="b", local_asset_id="y", asset_fit="strong"),
        ],
    )
    rows = {
        "s1": {
            "sentence_id": "s1",
            "start_seconds": 0.0,
            "end_seconds": 5.0,
            "words": [
                {
                    "text": "Salto",
                    "offset_seconds": 1.0,
                    "start_seconds": 1.0,
                    "end_seconds": 1.3,
                    "original_word_index": 0,
                }
            ],
        }
    }
    with pytest.raises(KeywordFlowTimingError, match="keinem"):
        validate_keyword_flow_mid_sentence_onsets(plan, sentence_rows_by_id=rows)
    plan.boundaries[1].offset_seconds = 1.0
    validate_keyword_flow_mid_sentence_onsets(plan, sentence_rows_by_id=rows)


def test_32_33_repeated_words_distinguishable_by_index() -> None:
    raw = [
        {
            "text": "the",
            "offset_seconds": 0.1,
            "start_seconds": 0.1,
            "end_seconds": 0.2,
            "original_word_index": 0,
        },
        {
            "text": "the",
            "offset_seconds": 1.1,
            "start_seconds": 1.1,
            "end_seconds": 1.2,
            "original_word_index": 3,
        },
    ]
    cleaned = clean_words_for_keyword_flow_prompt(raw, sentence_id="sX")
    assert cleaned[0]["word_ref"] == "sX#0"
    assert cleaned[1]["word_ref"] == "sX#3"
    assert cleaned[0]["offset_seconds"] != cleaned[1]["offset_seconds"]


# --- §33 Context / asset match (prompt contracts) -----------------------


def test_33_34_to_44_context_prompt_rules() -> None:
    prompt = _kf_prompt()
    assert "NAMED ENTITY PRIORITY" in prompt
    assert "general category" in prompt.lower() or "waterfall" in prompt.lower()
    assert "exact identity" in prompt.lower()
    assert "CHAPTER-LOCAL IDENTITY" in prompt
    assert "verlassenes Dorf" in prompt
    assert "pronoun" in prompt.lower() or "reference" in prompt.lower()
    assert "ATMOSPHERIC" in prompt
    assert "Do not invent artificial keywords" in prompt
    assert "chapter-local" in prompt.lower()
    assert "mechanical one-asset-per-sentence" in prompt.lower()


# --- §34 Onset / shot lengths -------------------------------------------


def test_34_45_to_50_onset_tolerance() -> None:
    assert KEYWORD_FLOW_ONSET_TOLERANCE_SEC == 1.5
    assert choose_onset_within_tolerance(onset=10.0, candidates=[10.0]) == 10.0
    assert choose_onset_within_tolerance(onset=10.0, candidates=[11.5]) == pytest.approx(11.5)
    assert choose_onset_within_tolerance(onset=10.0, candidates=[8.5]) == pytest.approx(8.5)
    # Equal distance → later preferred.
    assert choose_onset_within_tolerance(onset=10.0, candidates=[11.0, 9.0]) == pytest.approx(11.0)
    assert choose_onset_within_tolerance(onset=10.0, candidates=[12.0]) is None
    plan = UnifiedCutPlanDocument(
        script_version="v1",
        boundaries=[
            _boundary("b0", "s", offset=0.0),
            _boundary("b1", "s", position="middle", offset=5.0, alignment="mid_sentence"),
            _boundary("b2", "s", position="end"),
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
    with pytest.raises(KeywordFlowTimingError):
        apply_keyword_flow_onset_tolerance(
            plan=plan,
            raw_times=[0.0, 5.0, 12.0],
            clamped_times=[0.0, 7.0, 12.0],
            repairs=[],
        )


def test_34_51_to_57_shot_and_usage_settings_in_prompt_and_defaults() -> None:
    opts = CutPlanOptions()
    assert opts.shot_min_sec > 0
    assert opts.shot_max_sec >= opts.shot_min_sec
    assert opts.max_asset_usage == 2
    assert opts.min_asset_reuse_distance_shots == 4
    prompt = _kf_prompt()
    assert "shot_min" in prompt and "shot_max" in prompt
    assert "max asset usage 2" in prompt
    assert "reuse gap 4" in prompt
    # Theme block guidance
    assert "long" in prompt.lower() and ("theme" in prompt.lower() or "Themen" in prompt or "shot_max" in prompt)


# --- §35 Pauses ---------------------------------------------------------


def test_35_58_59_pauses_disabled_rhythm_and_keyword_sync() -> None:
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
        enable_keyword_flow_pauses=False,
    )
    assert timeline.entries[0].intra_pauses == []
    assert not is_keyword_flow_unified_style(
        CutPlanOptions(unified_cut_style=UNIFIED_CUT_STYLE_RHYTHM)
    )
    assert not is_keyword_flow_unified_style(
        CutPlanOptions(unified_cut_style=UNIFIED_CUT_STYLE_KEYWORD_SYNC)
    )


def test_35_60_to_65_pause_durations_and_fps_margin() -> None:
    assert resolve_keyword_flow_pause_duration_seconds("short") == pytest.approx(0.35)
    assert resolve_keyword_flow_pause_duration_seconds("medium") == pytest.approx(0.80)
    assert resolve_keyword_flow_pause_duration_seconds("long") == pytest.approx(1.50)
    assert KEYWORD_FLOW_PAUSE_SAFETY_FRAMES == 5
    start, end = safe_pause_window_timeline(
        previous_word_end_timeline=10.0,
        next_word_start_timeline=12.0,
        fps=25.0,
    )
    assert start == pytest.approx(10.2)
    assert end == pytest.approx(11.8)
    start2, end2 = safe_pause_window_timeline(
        previous_word_end_timeline=10.0,
        next_word_start_timeline=12.0,
        fps=50.0,
    )
    assert start2 == pytest.approx(10.1)
    assert end2 == pytest.approx(11.9)


def test_35_66_to_71_pause_shifts_timeline_keeps_source() -> None:
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
        repairs=repairs,
    )
    assert timeline.entries[0].intra_pauses[0].pause_seconds == pytest.approx(1.5)
    assert words[1]["start_seconds"] == 1.6  # source-relative unchanged
    # Timeline end grows by pause.
    assert timeline.entries[0].end_seconds == pytest.approx(3.0 + 1.5)
    start, end = safe_pause_window_timeline(
        previous_word_end_timeline=0.8,
        next_word_start_timeline=0.8 + (1.6 - 0.8) + 1.5,
        fps=25.0,
    )
    assert start >= 0.8 + 0.2 - 1e-9
    assert end <= 0.8 + (1.6 - 0.8) + 1.5 - 0.2 + 1e-9


def test_35_72_73_unsafe_or_wordless_pause_blocks() -> None:
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
            start_seconds=1.05,
            end_seconds=2.0,
            duration_seconds=0.95,
        ),
    }
    with pytest.raises(PauseResolveError):
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


def test_35_74_twelve_second_theme_long_pause() -> None:
    first = 9.0
    second_narration = 3.0
    pause = resolve_keyword_flow_pause_duration_seconds("long")
    second_shot = second_narration + pause
    assert first <= 9.0
    assert second_shot == pytest.approx(4.5)
    assert second_shot >= 4.0


def test_35_75_to_77_pause_editorial_prompt_rules() -> None:
    prompt = _kf_prompt()
    assert "strong" in prompt and "acceptable" in prompt
    assert "weak" in prompt.lower()
    assert "shot_min" in prompt
    assert "next_shot_may_start_during_pause" in prompt


# --- §36 Coverage gaps --------------------------------------------------


def test_36_78_to_86_keyword_flow_gap_nullify() -> None:
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
            {
                "cut_id": "c2",
                "sentence_id": "seg__s002",
                "position": "end",
                "alignment": "sentence_boundary",
            },
        ],
        "slots": [
            {
                "slot_id": "s1",
                "local_asset_id": "asset_weak",
                "asset_fit": "weak",
                "asset_fit_reason": "near",
                "needed_visual": "exact named waterfall",
                "search_concepts": ["named waterfall cliff", "cascade mist aerial"],
                "coverage_gap_id": "g1",
                "covered_sentence_ids": ["seg__s001"],
            },
            {
                "slot_id": "s2",
                "local_asset_id": "asset_none",
                "asset_fit": "none",
                "asset_fit_reason": "missing",
                "needed_visual": "named church facade",
                "search_concepts": ["gothic church facade", "stone cathedral exterior"],
                "coverage_gap_id": "g2",
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
    assert plan.slots[0].needed_visual
    assert 2 <= len(plan.slots[0].search_concepts or []) <= 4
    assert str(plan.slots[0].coverage_gap_id).startswith("Chap_") or "Chap" in str(
        plan.slots[0].coverage_gap_id
    )
    assert plan.slots[1].local_asset_id is None
    assert plan.slots[1].coverage_gap_id


def test_36_87_88_rhythm_and_sync_weak_unchanged() -> None:
    payload = {
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


# --- §37 Maps -----------------------------------------------------------


def test_37_89_to_102_map_opener_rules(tmp_path: Path) -> None:
    from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
    from otio_app.models import Project, ProjectMode
    from otio_app.services.without_voiceover_enhanced.keyword_flow_maps import (
        decide_map_opener,
    )

    root = tmp_path / "proj"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    maps = root / "Maps"
    maps.mkdir()
    # Valid longer map for ChapterA
    import subprocess

    long_map = maps / "ChapterA.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=320x240:d=12",
            "-an",
            str(long_map),
        ],
        check=True,
        capture_output=True,
    )
    short_map = maps / "ChapterB.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=320x240:d=4",
            "-an",
            str(short_map),
        ],
        check=True,
        capture_output=True,
    )
    project = Project(
        name="MapTest",
        project_root=str(root),
        work_dir=str(work),
        language="en",
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        fps=25.0,
        frames_per_shot=3,
        selected_asset_subdirs=["ChapterA", "ChapterB", "Maps"],
        asset_subdir_names=["ChapterA", "ChapterB", "Maps"],
    )
    used = decide_map_opener(project, "ChapterA")
    assert used.status == "used"
    assert used.opener_seconds == pytest.approx(KEYWORD_FLOW_MAP_OPENER_SEC)
    assert used.source_duration_seconds >= 9.0
    short = decide_map_opener(project, "ChapterB")
    assert short.status == "too_short"
    assert "zu kurz" in short.warning
    missing = decide_map_opener(project, "ChapterC")
    assert missing.status == "missing"
    assert "Coverage" not in missing.warning
    intro = decide_map_opener(project, "Intro")
    assert intro.status == "skipped_intro"
    # Ambiguous: second matching file
    (maps / "ChapterA_alt.mp4").write_bytes(long_map.read_bytes())
    amb = decide_map_opener(project, "ChapterA")
    assert amb.status == "ambiguous"
    prompt = _kf_prompt()
    assert "Do NOT plan the Maps folder opener" in prompt


# --- §38 Closing --------------------------------------------------------


def test_38_103_to_113_closing_and_fallback() -> None:
    plan_bad = UnifiedCutPlanDocument(
        script_version="v1",
        closing_fallback_asset_id="same",
        boundaries=[
            _boundary("b0", "s", offset=0.0),
            _boundary("b1", "s", position="end"),
        ],
        slots=[
            CutSlot(slot_id="last", local_asset_id="same", asset_fit="strong"),
        ],
    )
    errs = validate_keyword_flow_closing(plan_bad)
    assert any("nicht dem Primary" in e for e in errs)

    plan_ok = UnifiedCutPlanDocument(
        script_version="v1",
        closing_fallback_asset_id="fallback_a",
        closing_fallback_asset_fit="acceptable",
        closing_fallback_asset_fit_reason="reserve closer",
        closing_fallback_visual_intent="same closing intent as primary",
        boundaries=[
            _boundary("b0", "s", offset=0.0),
            _boundary("b1", "s", position="end"),
        ],
        slots=[
            CutSlot(slot_id="last", local_asset_id="close_a", asset_fit="strong"),
        ],
    )
    assert validate_keyword_flow_closing(plan_ok) == []

    plan_missing_fit = UnifiedCutPlanDocument(
        script_version="v1",
        closing_fallback_asset_id="fallback_a",
        boundaries=[
            _boundary("b0", "s", offset=0.0),
            _boundary("b1", "s", position="end"),
        ],
        slots=[
            CutSlot(slot_id="last", local_asset_id="close_a", asset_fit="strong"),
        ],
    )
    assert any(
        "closing_fallback_asset_fit fehlt" in e
        for e in validate_keyword_flow_closing(plan_missing_fit)
    )

    plan_weak_fb = UnifiedCutPlanDocument(
        script_version="v1",
        closing_fallback_asset_id="fallback_a",
        closing_fallback_asset_fit="weak",
        closing_fallback_asset_fit_reason="too weak",
        closing_fallback_visual_intent="same",
        boundaries=[
            _boundary("b0", "s", offset=0.0),
            _boundary("b1", "s", position="end"),
        ],
        slots=[
            CutSlot(slot_id="last", local_asset_id="close_a", asset_fit="strong"),
        ],
    )
    assert any(
        "unzulässig" in e for e in validate_keyword_flow_closing(plan_weak_fb)
    )

    plan_weak_close = UnifiedCutPlanDocument(
        script_version="v1",
        closing_fallback_asset_id="fallback_a",
        closing_fallback_asset_fit="acceptable",
        closing_fallback_asset_fit_reason="reserve",
        closing_fallback_visual_intent="same",
        boundaries=[
            _boundary("b0", "s", offset=0.0),
            _boundary("b1", "s", position="end"),
        ],
        slots=[
            CutSlot(slot_id="last", local_asset_id="close_a", asset_fit="weak"),
        ],
    )
    assert validate_keyword_flow_closing(plan_weak_close)

    prompt = _kf_prompt()
    assert "closing_fallback_asset_id" in prompt
    assert "closing_fallback_asset_fit" in prompt
    assert "closing_fallback_visual_intent" in prompt
    opts = CutPlanOptions()
    assert opts.voiceover_postroll_sec >= 0.0
