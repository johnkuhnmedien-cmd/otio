"""Strukturierte Asset-Frame-Analyse (asset_v3_editorial_r2)."""

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


def test_prompt_version_is_r2() -> None:
    assert ASSET_DESCRIPTION_PROMPT_VERSION == "asset_v3_editorial_r2"


def test_prompt_omits_media_and_folder_name_literals() -> None:
    folder = "Paris France Secret Location"
    media = "Golden_Gate_Bridge.mp4"
    prompt = build_asset_frame_analysis_prompt(media, folder, "de")
    assert folder not in prompt
    assert media not in prompt
    assert "Golden_Gate" not in prompt
    assert "Paris" not in prompt
    assert "keine visuelle Evidenz" in prompt
    assert "ableiten" in prompt
    assert "Dateiname" in prompt and "Ordnername" in prompt


def test_prompt_contains_score_bands_and_rare_90() -> None:
    prompt = build_asset_frame_analysis_prompt("x.mp4", "y", "en")
    assert "0–19" in prompt or "0-19" in prompt
    assert "20–39" in prompt or "20-39" in prompt
    assert "40–59" in prompt or "40-59" in prompt
    assert "60–74" in prompt or "60-74" in prompt
    assert "75–89" in prompt or "75-89" in prompt
    assert "90–100" in prompt or "90-100" in prompt
    assert "außergewöhnlich" in prompt
    assert "selten" in prompt
    assert "Nicht automatisch bei 80" in prompt


def test_prompt_separates_quality_dimensions() -> None:
    prompt = build_asset_frame_analysis_prompt("a.mp4", "b", "de")
    assert "technical_quality" in prompt
    assert "composition_quality" in prompt
    assert "visual_appeal" in prompt
    assert "nicht Schönheit" in prompt or "nicht Schönheit oder Motivwert" in prompt
    assert "Dateigröße" in prompt or "Bitrate" in prompt
    assert "Auflösung" in prompt


def test_prompt_has_no_numeric_quality_anchors_in_schema_example() -> None:
    prompt = build_asset_frame_analysis_prompt("a.mp4", "b", "de")
    assert '"technical_quality": 80' not in prompt
    assert '"composition_quality": 80' not in prompt
    assert '"visual_appeal": 80' not in prompt
    assert '"subject_clarity": 80' not in prompt
    assert '"hero_potential": 70' not in prompt
    assert "<int_0_100>" in prompt


def test_prompt_framing_priority_aerial_pov_and_independent_shot_scale() -> None:
    prompt = build_asset_frame_analysis_prompt("a.mp4", "b", "de")
    assert "aerial" in prompt and "pov" in prompt
    assert "Priorität" in prompt
    assert "shot_scale unabhängig" in prompt or "unabhängig bestimmen" in prompt
    assert "Kajakbug" in prompt or "Fahrzeug" in prompt
    assert "Drohnenansicht" in prompt or "Luft-/Drohnenperspektive" in prompt


def test_prompt_motion_is_camera_not_subject() -> None:
    prompt = build_asset_frame_analysis_prompt("a.mp4", "b", "de")
    assert "Kamerabewegung" in prompt
    assert "Wasserfall" in prompt
    assert "Motivbewegung" in prompt or "keine Kamerabewegung" in prompt
    assert "nicht automatisch motion.type=drone" in prompt or "nicht automatisch" in prompt


def test_prompt_lensflare_not_automatic_defect() -> None:
    prompt = build_asset_frame_analysis_prompt("a.mp4", "b", "de")
    assert "Lensflare" in prompt
    assert "nicht automatisch" in prompt


def test_prompt_forbids_metadata_place_names() -> None:
    prompt = build_asset_frame_analysis_prompt("a.mp4", "b", "de")
    assert "Ortsnamen" in prompt
    assert "Ordnername" in prompt or "Dateiname" in prompt
    assert "generisch" in prompt


def test_prompt_separates_description_caption_tags() -> None:
    prompt = build_asset_frame_analysis_prompt("a.mp4", "b", "de")
    assert "Retrieval-Satz" in prompt or "caption:" in prompt
    assert "2–3 sachliche Sätze" in prompt or "2-3 sachliche Sätze" in prompt
    assert "3–8 kurze" in prompt or "3-8 kurze" in prompt
    assert "unterschiedliche Aufgaben" in prompt or "Freitextfelder" in prompt


def test_prompt_confidence_rubric_and_no_default_095() -> None:
    prompt = build_asset_frame_analysis_prompt("a.mp4", "b", "de")
    assert "0.90–1.00" in prompt or "0.90-1.00" in prompt
    assert "0.70–0.89" in prompt or "0.70-0.89" in prompt
    assert "0.50–0.69" in prompt or "0.50-0.69" in prompt
    assert "0.95 nicht als Standard" in prompt
    assert '"confidence": 0.85' not in prompt
    assert '"confidence": 0.95' not in prompt


def test_prompt_contains_json_schema_keys_and_language() -> None:
    prompt = build_asset_frame_analysis_prompt("clip.mp4", "Sedona", "en")
    assert "Sprache für Freitext: en" in prompt
    assert '"caption"' in prompt
    assert '"quality"' in prompt
    assert '"content_tags"' in prompt
    assert "hero_potential" in prompt
    # Namen dürfen nicht als Evidenz im Prompt stehen
    assert "clip.mp4" not in prompt
    assert "Sedona" not in prompt


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


def test_parse_accepts_full_score_range() -> None:
    for score in (25, 48, 67, 84, 96):
        result = parse_media_frame_analysis(
            _valid_v3_payload(
                quality={
                    "technical_quality": score,
                    "composition_quality": score,
                    "visual_appeal": score,
                    "subject_clarity": score,
                    "hero_potential": score,
                    "defect_severity": 0,
                }
            )
        )
        assert result.parse_ok is True, score
        assert result.quality_profile is not None
        assert result.quality_profile.technical_quality == score


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


def test_parse_boolean_score_fails() -> None:
    result = parse_media_frame_analysis(
        _valid_v3_payload(
            quality={
                "technical_quality": True,
                "composition_quality": 80,
                "visual_appeal": 80,
                "subject_clarity": 80,
                "hero_potential": 70,
                "defect_severity": 0,
            }
        )
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
