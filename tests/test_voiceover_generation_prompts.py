"""Phase 2: Prompt-Builder-Tests (build_style_profile_prompt)."""

from __future__ import annotations

from otio_app.services.voiceover_generation.models import (
    ProjectBrief,
    VoiceoverStyleReferences,
)
from otio_app.services.voiceover_generation.prompts import build_style_profile_prompt


def _sample_brief() -> ProjectBrief:
    return ProjectBrief(
        project_id="p1",
        video_title="Wunder der Wüste",
        language="DE",
        tone_tags=["cinematic", "mysterious"],
        negative_rule_flags={"no_invented_facts": True, "no_repetition": True, "no_clickbait_phrases": False},
        negative_rules_freetext="Keine Klischees über die Wüste.",
        forbidden_phrases=["atemberaubend", "must-see"],
        global_extra_prompt="Schreibe wie ein Naturfilm-Kommentator.",
    )


def _sample_refs() -> VoiceoverStyleReferences:
    return VoiceoverStyleReferences(
        project_id="p1",
        intro_reference_texts=["Ein Ort, an dem die Zeit stillsteht.", "", ""],
        segment_reference_texts=["Zwischen den Dünen wirkt jeder Schritt wie eine Reise.", "", ""],
        uploaded_file_names=[],
        uploaded_file_texts=[],
    )


def test_prompt_contains_do_not_copy_instruction() -> None:
    prompt = build_style_profile_prompt(_sample_brief(), _sample_refs())
    assert "do not copy" in prompt.lower()


def test_prompt_contains_intro_and_segment_references() -> None:
    prompt = build_style_profile_prompt(_sample_brief(), _sample_refs())
    assert "Ein Ort, an dem die Zeit stillsteht." in prompt
    assert "Zwischen den Dünen wirkt jeder Schritt wie eine Reise." in prompt
    assert "Intro-Referenz" in prompt
    assert "Segment-Referenz" in prompt


def test_prompt_contains_negative_rules_and_forbidden_phrases() -> None:
    prompt = build_style_profile_prompt(_sample_brief(), _sample_refs())
    assert "no_invented_facts" in prompt
    assert "no_repetition" in prompt
    assert "no_clickbait_phrases" not in prompt  # deaktiviert, nicht in "active" Liste
    assert "atemberaubend" in prompt
    assert "must-see" in prompt
    assert "Keine Klischees über die Wüste." in prompt


def test_prompt_requests_json_only_output() -> None:
    prompt = build_style_profile_prompt(_sample_brief(), _sample_refs())
    assert "JSON" in prompt
    assert "style_summary_for_prompts" in prompt


def test_prompt_contains_no_hard_timing_rules() -> None:
    """Analog zum Modellvergleichs-Workflow: keine harten Produktionsregeln
    (shot_min_sec/shot_max_sec/max_asset_usage) im Style-Profile-Prompt."""
    prompt = build_style_profile_prompt(_sample_brief(), _sample_refs())
    assert "shot_min_sec" not in prompt
    assert "shot_max_sec" not in prompt
    assert "max_asset_usage" not in prompt


def test_prompt_handles_empty_references_gracefully() -> None:
    empty_refs = VoiceoverStyleReferences(project_id="p1")
    prompt = build_style_profile_prompt(_sample_brief(), empty_refs)
    assert "(keine)" in prompt
