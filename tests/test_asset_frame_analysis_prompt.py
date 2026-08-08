"""Strukturierte Asset-Frame-Analyse (asset_v3_editorial)."""

from __future__ import annotations

from otio_app.services.gemini_client import (
    ASSET_DESCRIPTION_PROMPT_VERSION,
    build_asset_frame_analysis_prompt,
    parse_media_frame_analysis,
)


def _valid_v3_payload(**overrides: object) -> str:
    import json

    payload: dict[str, object] = {
        "description": (
            "Red sandstone walls glow in soft light. The canyon floor is quiet. "
            "Warm tones dominate the frame."
        ),
        "caption": "Red sandstone canyon walls in soft daylight.",
        "content_tags": ["canyon", "sandstone", "daylight"],
        "motion": {
            "type": "pan",
            "intensity": 40,
            "direction": "left_to_right",
            "confidence": 0.6,
        },
        "framing": {"type": "wide", "shot_scale": "wide"},
        "look": {
            "brightness": 55,
            "contrast": 50,
            "saturation": 45,
            "color_temperature": "warm",
            "dominant_colors": ["stone", "blue"],
        },
        "people": True,
        "people_action": "walking slowly",
        "quality": {
            "technical_quality": 80,
            "composition_quality": 85,
            "visual_appeal": 82,
            "subject_clarity": 88,
            "hero_potential": 75,
            "defect_severity": 10,
        },
        "defects": [
            {"type": "watermark", "severity": 40, "note": "corner mark"},
        ],
        "confidence": 0.85,
    }
    payload.update(overrides)
    return json.dumps(payload)


def test_prompt_contains_json_schema_and_field_rules() -> None:
    prompt = build_asset_frame_analysis_prompt("clip.mp4", "Sedona", "en")
    assert "clip.mp4" in prompt
    assert "Sedona" in prompt
    assert "Sprache für Freitext: en" in prompt
    assert '"caption"' in prompt
    assert '"quality"' in prompt
    assert '"content_tags"' in prompt
    assert "hero_potential" in prompt
    assert ASSET_DESCRIPTION_PROMPT_VERSION == "asset_v3_editorial"


def test_parse_full_v3_json() -> None:
    result = parse_media_frame_analysis(_valid_v3_payload())
    assert result.parse_ok is True
    assert "sandstone" in result.description
    assert result.caption.startswith("Red sandstone")
    assert result.motion == "pan"
    assert result.framing == "wide"
    assert result.people is True
    assert result.people_action == "walking slowly"
    assert result.motion_profile is not None
    assert result.motion_profile.direction == "left_to_right"
    assert result.quality_profile is not None
    assert result.quality_profile.hero_potential == 75
    assert len(result.defect_items) == 1
    assert result.defect_items[0].type == "watermark"
    assert result.defects is not None
    assert "watermark" in result.defects
    assert result.confidence == 0.85


def test_parse_invalid_json_fails() -> None:
    result = parse_media_frame_analysis("Nur Freitext ohne JSON.")
    assert result.parse_ok is False
    assert result.raw_response == "Nur Freitext ohne JSON."
    assert result.description == ""


def test_parse_missing_caption_fails() -> None:
    result = parse_media_frame_analysis(_valid_v3_payload(caption=""))
    assert result.parse_ok is False


def test_parse_missing_quality_fails() -> None:
    import json

    payload = json.loads(_valid_v3_payload())
    del payload["quality"]
    result = parse_media_frame_analysis(json.dumps(payload))
    assert result.parse_ok is False


def test_parse_invalid_enum_fails() -> None:
    result = parse_media_frame_analysis(
        _valid_v3_payload(motion={"type": "PANNING", "direction": "unknown"})
    )
    assert result.parse_ok is False


def test_parse_out_of_range_quality_fails() -> None:
    result = parse_media_frame_analysis(
        _valid_v3_payload(
            quality={
                "technical_quality": 180,
                "composition_quality": 80,
                "visual_appeal": 80,
                "subject_clarity": 80,
                "hero_potential": 70,
                "defect_severity": 0,
            }
        )
    )
    assert result.parse_ok is False


def test_parse_content_tags_trim_and_dedupe() -> None:
    result = parse_media_frame_analysis(
        _valid_v3_payload(content_tags=["  Canyon ", "canyon", "", "Daylight", "daylight"])
    )
    assert result.parse_ok is True
    assert result.content_tags == ("Canyon", "Daylight")


def test_parse_caption_clamped_to_180() -> None:
    long_caption = "A" * 250
    result = parse_media_frame_analysis(_valid_v3_payload(caption=long_caption))
    assert result.parse_ok is True
    assert len(result.caption) == 180


def test_parse_null_look_values_ok() -> None:
    result = parse_media_frame_analysis(
        _valid_v3_payload(
            look={
                "brightness": None,
                "contrast": None,
                "saturation": None,
                "color_temperature": "unknown",
                "dominant_colors": [],
            }
        )
    )
    assert result.parse_ok is True
    assert result.look_profile is not None
    assert result.look_profile.brightness is None
    assert result.look_profile.contrast is None


def test_parse_fenced_json_ok() -> None:
    raw = "```json\n" + _valid_v3_payload() + "\n```"
    result = parse_media_frame_analysis(raw)
    assert result.parse_ok is True
    assert result.motion == "pan"
