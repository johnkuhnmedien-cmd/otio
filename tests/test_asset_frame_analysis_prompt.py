"""Strukturierte Asset-Frame-Analyse (asset_v2_structured)."""

from __future__ import annotations

from otio_app.services.gemini_client import (
    ASSET_DESCRIPTION_PROMPT_VERSION,
    build_asset_frame_analysis_prompt,
    parse_media_frame_analysis,
)


def test_prompt_contains_json_schema_and_field_rules() -> None:
    prompt = build_asset_frame_analysis_prompt("clip.mp4", "Sedona", "en")
    assert "clip.mp4" in prompt
    assert "Sedona" in prompt
    assert "Sprache für Freitext: en" in prompt
    assert '"motion"' in prompt
    assert '"framing"' in prompt
    assert "static" in prompt
    assert "aerial" in prompt
    assert ASSET_DESCRIPTION_PROMPT_VERSION == "asset_v2_structured"


def test_parse_structured_json() -> None:
    raw = """
    {
      "description": "Red sandstone walls glow in soft light. The canyon floor is quiet.",
      "motion": "pan",
      "framing": "wide",
      "people": true,
      "people_action": "walking slowly",
      "defects": null
    }
    """
    result = parse_media_frame_analysis(raw)
    assert result.parse_ok is True
    assert "sandstone" in result.description
    assert result.motion == "pan"
    assert result.framing == "wide"
    assert result.people is True
    assert result.people_action == "walking slowly"
    assert result.defects is None


def test_parse_fenced_json_and_normalize_enums() -> None:
    raw = """```json
{"description":"A still desert vista at dusk.","motion":"PANNING","framing":"WIDE","people":false,"people_action":null,"defects":"watermark"}
```"""
    result = parse_media_frame_analysis(raw)
    assert result.parse_ok is True
    assert result.motion == "unknown"  # invalid enum → unknown
    assert result.framing == "wide"
    assert result.people is False
    assert result.people_action is None
    assert result.defects == "watermark"


def test_parse_fallback_keeps_raw_text() -> None:
    raw = "Nur Freitext ohne JSON."
    result = parse_media_frame_analysis(raw)
    assert result.parse_ok is False
    assert result.description == raw
