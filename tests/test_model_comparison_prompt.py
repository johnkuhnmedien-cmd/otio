"""Tests für Vergleichs-Prompt."""

from __future__ import annotations

from otio_app.services.gemini_client import build_plan_folder_model_comparison_prompt


def test_model_comparison_prompt_omits_hard_rules() -> None:
    prompt = build_plan_folder_model_comparison_prompt(
        folder_name="Badlands",
        segment_lines='- beat_id="beat_001" start_sec=0.0 end_sec=10.0 text="Text."',
        asset_lines='- path="/a.mp4" description="Rock"',
        language="de",
        editor_hint="Ruhige Shots bevorzugen.",
    )
    assert "Badlands" in prompt
    assert "desired_duration_sec" in prompt
    assert "Ruhige Shots bevorzugen." in prompt
    assert "shot_min" not in prompt
    assert "shot_max" not in prompt
    assert "max_asset_usage" not in prompt
    assert "allowed_parts" not in prompt
