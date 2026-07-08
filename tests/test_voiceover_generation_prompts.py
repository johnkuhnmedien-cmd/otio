"""Phase 2: Prompt-Builder-Tests (build_style_profile_prompt)."""

from __future__ import annotations

from otio_app.services.voiceover_generation.models import (
    FolderInventorySummary,
    ProjectBrief,
    VoiceoverStyleProfile,
    VoiceoverStyleReferences,
)
from otio_app.services.voiceover_generation.prompts import (
    build_dramaturgy_prompt,
    build_style_profile_prompt,
)


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


# --- build_dramaturgy_prompt (Phase 3) ---


def _sample_style_profile() -> VoiceoverStyleProfile:
    return VoiceoverStyleProfile(
        project_id="p1",
        overall_tone="calm, cinematic",
        style_summary_for_prompts="Calm, cinematic, third-person narration with sensory imagery.",
    )


def _sample_folder_summaries() -> list[FolderInventorySummary]:
    return [
        FolderInventorySummary(
            folder_name="Grand Canyon",
            asset_count=10,
            video_count=8,
            image_count=2,
            visual_strength_score=0.9,
            asset_diversity_score=0.8,
            estimated_voiceover_word_count=140,
            estimated_min_words=126,
            estimated_max_words=154,
            dominant_visual_themes=["schlucht", "sonnenuntergang"],
        ),
        FolderInventorySummary(
            folder_name="Yellowstone",
            asset_count=3,
            video_count=1,
            image_count=2,
            visual_strength_score=0.4,
            asset_diversity_score=0.3,
            estimated_voiceover_word_count=65,
            estimated_min_words=59,
            estimated_max_words=72,
            risks=["VERY_FEW_ASSETS"],
        ),
    ]


def test_dramaturgy_prompt_contains_project_title() -> None:
    prompt = build_dramaturgy_prompt(
        project_brief=_sample_brief(),
        style_profile=_sample_style_profile(),
        folder_summaries=_sample_folder_summaries(),
    )
    assert "Wunder der Wüste" in prompt


def test_dramaturgy_prompt_contains_style_summary() -> None:
    prompt = build_dramaturgy_prompt(
        project_brief=_sample_brief(),
        style_profile=_sample_style_profile(),
        folder_summaries=_sample_folder_summaries(),
    )
    assert "Calm, cinematic, third-person narration with sensory imagery." in prompt


def test_dramaturgy_prompt_contains_all_folder_summaries() -> None:
    prompt = build_dramaturgy_prompt(
        project_brief=_sample_brief(),
        style_profile=_sample_style_profile(),
        folder_summaries=_sample_folder_summaries(),
    )
    assert "Grand Canyon" in prompt
    assert "Yellowstone" in prompt
    assert "VERY_FEW_ASSETS" in prompt


def test_dramaturgy_prompt_requests_json_only() -> None:
    prompt = build_dramaturgy_prompt(
        project_brief=_sample_brief(),
        style_profile=_sample_style_profile(),
        folder_summaries=_sample_folder_summaries(),
    )
    assert "JSON ONLY" in prompt
    assert "recommended_folder_order" in prompt


def test_dramaturgy_prompt_does_not_sort_alphabetically_or_by_count() -> None:
    prompt = build_dramaturgy_prompt(
        project_brief=_sample_brief(),
        style_profile=_sample_style_profile(),
        folder_summaries=_sample_folder_summaries(),
    )
    assert "Do NOT simply sort alphabetically" in prompt
    assert "Do NOT simply sort by asset count" in prompt


def test_dramaturgy_prompt_handles_missing_style_profile() -> None:
    prompt = build_dramaturgy_prompt(
        project_brief=_sample_brief(),
        style_profile=None,
        folder_summaries=_sample_folder_summaries(),
    )
    assert "kein Style Profile vorhanden" in prompt
