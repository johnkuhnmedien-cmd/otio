"""ENHANCED-AUTHOR-PAUSES-FROM-RAW-STYLE-001."""

from __future__ import annotations

import json
from pathlib import Path

from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.services.without_voiceover_enhanced.models import (
    EnhancedScriptDocument,
    PauseDirective,
    ScriptSegment,
    SegmentTiming,
)
from otio_app.services.without_voiceover_enhanced.pause_resolver import (
    build_narration_timeline,
)
from otio_app.services.without_voiceover_enhanced.raw_chapter_style_structure import (
    analyze_raw_chapter_style_structure,
    detect_raw_chapter_style_violations,
    prepare_raw_chapter_reference,
)
from otio_app.services.without_voiceover_enhanced.script_author_service import (
    parse_enhanced_script_response,
)
from otio_app.services.without_voiceover_enhanced.script_chapter_text import (
    chapter_display_text,
    join_spoken_segment_texts,
    parse_chapter_display_text,
)
from otio_app.services.without_voiceover_enhanced.script_prompts import (
    build_enhanced_folder_script_prompt,
)
from otio_app.services.voiceover_generation.models import STYLE_MODE_RAW_TEXT
from otio_app.services.voiceover_generation.style_reference_service import (
    format_raw_chapter_reference_for_prompts,
)


def test_raw_reference_keeps_timed_pause_values() -> None:
    raw = (
        "Fact one.\n\n"
        "[pause 3 seconds]\n\n"
        "Fact two.\n\n"
        "[pause 2 seconds]\n\n"
        "Fact three."
    )
    prepared = prepare_raw_chapter_reference(raw)
    assert prepared.beat_texts == ["Fact one.", "Fact two.", "Fact three."]
    assert [(p.after_beat_index, p.seconds) for p in prepared.pauses] == [
        (0, 3.0),
        (1, 2.0),
    ]
    structure = analyze_raw_chapter_style_structure(prepared)
    assert structure.pause_count == 2
    assert structure.pause_seconds_sequence == [3.0, 2.0]
    assert structure.median_pause_seconds == 2.5
    block = format_raw_chapter_reference_for_prompts(raw)
    assert "REFERENCE PAUSE RHYTHM" in block
    assert "3s" in block and "2s" in block
    assert "author_pause_after_seconds" in block


def test_prompt_requires_author_pause_field_in_raw_mode() -> None:
    style = format_raw_chapter_reference_for_prompts(
        "Place A lies nearby.\n\n[pause 3 seconds]\n\nA landmark stands nearby."
    )
    prompt = build_enhanced_folder_script_prompt(
        project_brief_text="Brief",
        film_context_text="ctx",
        chapter_dramaturgy_text="meta",
        style_profile_text=style,
        verified_facts_text="Facts",
        folder_name="Dublin",
        folder_slug="dublin",
        dramaturgy_role="hook",
        target_words=100,
        min_words=80,
        max_words=120,
        previous_folder_name=None,
        next_folder_name=None,
        language="en",
        style_is_raw_chapter=True,
    )
    assert "author_pause_after_seconds" in prompt
    assert "AUTHOR PAUSES" in prompt
    assert "Do not write [pause X seconds] inside segment.text" in prompt


def test_parser_stores_author_pause_seconds() -> None:
    payload = {
        "narration_full": "Achill Island lies off County Mayo. Keem Bay lies west.",
        "segments": [
            {
                "segment_id": "a_001",
                "text": "Achill Island lies off County Mayo.",
                "sequence_index": 1,
                "semantic_function": "geography",
                "author_pause_after_seconds": 3,
                "paragraph_break_after": True,
                "folder_name": "Achill Island",
            },
            {
                "segment_id": "a_002",
                "text": "Keem Bay lies west.",
                "sequence_index": 2,
                "semantic_function": "geography",
                "author_pause_after_seconds": 0,
                "folder_name": "Achill Island",
            },
        ],
        "fact_check_hints": [],
        "rhetoric_usage": [],
    }
    doc = parse_enhanced_script_response(
        payload, folder_name="Achill Island", folder_order_index=1
    )
    assert doc.segments[0].author_pause_after_seconds == 3.0
    assert doc.segments[0].paragraph_break_after is True
    assert "[pause" not in doc.narration_full


def test_display_roundtrip_preserves_pauses() -> None:
    segments = [
        ScriptSegment(
            segment_id="s1",
            text="Text one.",
            sequence_index=1,
            folder_name="Dublin",
            author_pause_after_seconds=3.0,
        ),
        ScriptSegment(
            segment_id="s2",
            text="Text two.",
            sequence_index=2,
            folder_name="Dublin",
            author_pause_after_seconds=2.5,
        ),
    ]
    rendered = chapter_display_text(segments)
    assert "[pause 3 seconds]" in rendered
    assert "[pause 2.5 seconds]" in rendered
    parsed = parse_chapter_display_text(
        rendered,
        folder_name="Dublin",
        segment_id_prefix="dublin_segment",
    )
    assert [s.text for s in parsed] == ["Text one.", "Text two."]
    assert [s.author_pause_after_seconds for s in parsed] == [3.0, 2.5]
    assert join_spoken_segment_texts(parsed) == join_spoken_segment_texts(segments)


def test_style_guard_flags_missing_author_pauses() -> None:
    structure = analyze_raw_chapter_style_structure(
        prepare_raw_chapter_reference(
            "A lies here.\n\n[pause 3 seconds]\n\n"
            "B stands there.\n\n[pause 2 seconds]\n\n"
            "C extends west.\n\n[pause 4 seconds]\n\n"
            "D remains."
        )
    )
    segments = [
        ScriptSegment(
            segment_id=f"s{i}",
            text=f"Sentence {i}.",
            sequence_index=i,
            folder_name="Dublin",
            author_pause_after_seconds=0.0,
        )
        for i in range(1, 5)
    ]
    errors = detect_raw_chapter_style_violations(
        " ".join(s.text for s in segments),
        structure=structure,
        folder_name="Dublin",
        segments=segments,
    )
    assert any("author_pause_after_seconds" in e or "Pausen" in e for e in errors)
    # Markers in spoken text still fail.
    assert detect_raw_chapter_style_violations(
        "Dublin lies here. [pause 3 seconds] More.",
        structure=structure,
        folder_name="Dublin",
        segments=segments,
    )


def test_narration_timeline_applies_author_and_keyword_pauses() -> None:
    repairs: list[str] = []
    timeline = build_narration_timeline(
        script_version="script-v1",
        segment_timings=[
            SegmentTiming(
                segment_id="A",
                script_version="script-v1",
                audio_path="a.mp3",
                duration_seconds=10.0,
                audio_status="valid",
            ),
            SegmentTiming(
                segment_id="B",
                script_version="script-v1",
                audio_path="b.mp3",
                duration_seconds=8.0,
                audio_status="valid",
            ),
        ],
        pause_directives=[],
        author_pause_after_by_segment={"A": 3.0},
        repairs=repairs,
    )
    assert timeline.entries[0].start_seconds == 0.0
    assert timeline.entries[0].end_seconds == 10.0
    assert timeline.entries[0].pause_after_seconds == 3.0
    assert timeline.entries[1].start_seconds == 13.0
    assert timeline.total_duration_seconds >= 21.0
    assert any("author_pause: A +3.00s" in note for note in repairs)

    # Additive with keyword-flow trailing pause reported separately.
    from otio_app.services.without_voiceover_enhanced.models import SentenceTiming

    repairs2: list[str] = ["keyword_flow_pause: A__s001 +0.80s at source 9.000s"]
    timeline2 = build_narration_timeline(
        script_version="script-v1",
        segment_timings=[
            SegmentTiming(
                segment_id="A",
                script_version="script-v1",
                audio_path="a.mp3",
                duration_seconds=10.0,
                audio_status="valid",
            ),
            SegmentTiming(
                segment_id="B",
                script_version="script-v1",
                audio_path="b.mp3",
                duration_seconds=8.0,
                audio_status="valid",
            ),
        ],
        pause_directives=[],
        enable_keyword_flow_pauses=False,
        author_pause_after_by_segment={"A": 3.0},
        repairs=repairs2,
    )
    assert timeline2.entries[0].pause_after_seconds == 3.0
    assert timeline2.entries[1].start_seconds == 13.0
    assert any(note.startswith("author_pause:") for note in repairs2)
    assert any(note.startswith("keyword_flow_pause:") for note in repairs2)
