"""Tests für Modellvergleich-Pipeline und Delta-Tracking."""

from __future__ import annotations

from otio_app.analysis_models import VoiceSegment
from otio_app.services.model_comparison_models import ParsedLlmBeat, ParsedLlmPart
from otio_app.services.model_comparison_pipeline import (
    DeltaRecorder,
    build_comparison_effective_rules,
    build_delta_document,
    content_hash,
    parse_llm_candidate_from_text,
)


def test_content_hash_is_stable() -> None:
    assert content_hash("hello") == content_hash("hello")
    assert content_hash("hello") != content_hash("world")


def test_effective_rules_shows_disabled_pipeline() -> None:
    rules = build_comparison_effective_rules(
        shot_min_sec=3.0,
        shot_max_sec=8.0,
        max_asset_usage=1,
        min_asset_reuse_distance_shots=2,
    )
    assert rules.shot_rules_enabled is False
    assert rules.max_asset_usage_enabled is False
    assert rules.pipeline.normalize_parts is False
    assert rules.pipeline.apply_edit_plan_rules is False


def test_parse_llm_candidate_invalid_json() -> None:
    parsed = parse_llm_candidate_from_text("not json", allowed_paths=set())
    assert parsed.parse_error
    assert parsed.proposed_part_count == 0


def test_parse_llm_candidate_extracts_optional_fields() -> None:
    raw = """
    {
      "beats": [{
        "beat_id": "beat_001",
        "parts": [{
          "text": "Park",
          "motif": "Landschaft",
          "asset_path": "/a.mp4",
          "match_quality": "gut",
          "desired_duration_sec": 6.0,
          "visual_intent": "wide",
          "reason": "establishing",
          "confidence": "high"
        }]
      }]
    }
    """
    parsed = parse_llm_candidate_from_text(raw, allowed_paths={"/a.mp4"})
    assert parsed.parse_error is None
    assert parsed.proposed_part_count == 1
    part = parsed.beats[0].parts[0]
    assert part.desired_duration_sec == 6.0
    assert part.visual_intent == "wide"


def test_delta_detects_duration_and_asset_changes() -> None:
    parsed = parse_llm_candidate_from_text(
        """
        {"beats":[{"beat_id":"beat_001","parts":[
          {"text":"A","motif":"m","asset_path":"/a.mp4","match_quality":"gut","desired_duration_sec":9.0}
        ]}]}
        """,
        allowed_paths={"/a.mp4"},
    )
    recorder = DeltaRecorder()
    recorder.record(
        beat_id="beat_001",
        part_index=0,
        field_name="duration_sec",
        before=9.0,
        after=4.5,
        reason="PYTHON_TIMING_ALLOCATION",
        function_name="allocate_time_by_text",
    )
    recorder.record(
        beat_id="beat_001",
        part_index=0,
        field_name="asset_path",
        before="/a.mp4",
        after=None,
        reason="MISSING_ASSET",
        function_name="build_technical_preview",
    )
    delta = build_delta_document(parsed=parsed, preview=None, recorder=recorder)
    assert delta.changes_count == 2
    assert delta.beat_summaries[0]["duration_changed"] is True
    assert delta.beat_summaries[0]["asset_changed"] is True


def test_delta_no_changes_note() -> None:
    parsed = parse_llm_candidate_from_text('{"beats":[]}', allowed_paths=set())
    delta = build_delta_document(parsed=parsed, preview=None, recorder=DeltaRecorder())
    assert delta.changes_count == 0
    assert "No Python changes detected" in delta.note
