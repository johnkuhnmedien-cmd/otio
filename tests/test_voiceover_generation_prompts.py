"""Phase 2: Prompt-Builder-Tests (build_style_profile_prompt)."""

from __future__ import annotations

from otio_app.defaults import BRIEF_NEGATIVE_RULE_INSTRUCTIONS
from otio_app.services.voiceover_generation.models import (
    DramaturgyFolderEntry,
    DramaturgyPlan,
    FolderInventorySummary,
    FolderVoiceoverDraft,
    FolderVoiceoverSetting,
    IntroHookSettings,
    ProjectBrief,
    SentenceItem,
    ValidationError,
    VoiceoverStyleProfile,
    VoiceoverStyleReferences,
)
from otio_app.services.voiceover_generation.prompts import (
    build_dramaturgy_prompt,
    build_folder_voiceover_prompt,
    build_intro_hook_prompt,
    build_style_profile_prompt,
    build_voiceover_correction_prompt,
    build_voiceover_review_prompt,
)


def _sample_brief() -> ProjectBrief:
    return ProjectBrief(
        project_id="p1",
        video_title="Wunder der Wüste",
        language="DE",
        tone_tags=["cinematic", "mysterious"],
        negative_rule_flags={
            "no_unverified_historical_claims": True,
            "no_party_scenes": True,
            "voice_not_ai_sounding": False,
        },
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
    assert "no_unverified_historical_claims" in prompt
    assert "no_party_scenes" in prompt
    assert "voice_not_ai_sounding" not in prompt  # deaktiviert, nicht in "active" Liste
    assert "atemberaubend" in prompt
    assert "must-see" in prompt
    assert "Keine Klischees über die Wüste." in prompt


def test_prompt_active_negative_rules_include_llm_instruction_text() -> None:
    """Nutzerfeedback (Juli 2026): Regel-Keys allein im Prompt sind für Mensch
    UND LLM missverständlich — die ausführliche Formulierung muss mit
    ausgegeben werden, nicht nur der kompakte Key."""
    prompt = build_style_profile_prompt(_sample_brief(), _sample_refs())
    assert BRIEF_NEGATIVE_RULE_INSTRUCTIONS["no_unverified_historical_claims"] in prompt
    assert BRIEF_NEGATIVE_RULE_INSTRUCTIONS["no_party_scenes"] in prompt
    assert BRIEF_NEGATIVE_RULE_INSTRUCTIONS["voice_not_ai_sounding"] not in prompt


def _sample_brief_with_new_rules() -> ProjectBrief:
    return _sample_brief().model_copy(
        update={
            "negative_rule_flags": {
                "biblical_chronology_required": True,
                "no_party_scenes": True,
                "voice_not_ai_sounding": True,
                "no_cliches": True,
            }
        }
    )


def test_prompt_contains_new_standard_negative_rules() -> None:
    prompt = build_style_profile_prompt(_sample_brief_with_new_rules(), _sample_refs())
    for flag in ("biblical_chronology_required", "no_party_scenes", "voice_not_ai_sounding", "no_cliches"):
        assert flag in prompt
        assert BRIEF_NEGATIVE_RULE_INSTRUCTIONS[flag] in prompt


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


def test_dramaturgy_prompt_contains_active_negative_rule_instructions() -> None:
    prompt = build_dramaturgy_prompt(
        project_brief=_sample_brief_with_new_rules(),
        style_profile=_sample_style_profile(),
        folder_summaries=_sample_folder_summaries(),
    )
    assert BRIEF_NEGATIVE_RULE_INSTRUCTIONS["biblical_chronology_required"] in prompt
    assert BRIEF_NEGATIVE_RULE_INSTRUCTIONS["voice_not_ai_sounding"] in prompt


# --- build_folder_voiceover_prompt / build_voiceover_review_prompt /
#     build_voiceover_correction_prompt (Phase 4) ---


def _sample_dramaturgy_entry() -> DramaturgyFolderEntry:
    return DramaturgyFolderEntry(
        folder_name="Grand Canyon",
        order_index=1,
        dramaturgy_role="opener",
        reason="Starkes visuelles Material.",
        transition_goal_to_next="Steigerung zum nächsten Ort.",
    )


def _sample_setting() -> FolderVoiceoverSetting:
    return FolderVoiceoverSetting(
        folder_name="Grand Canyon",
        order_index=1,
        target_words=140,
        min_words=126,
        max_words=154,
        must_avoid=["breathtaking"],
    )


def _sample_inventory_assets() -> list[dict]:
    return [
        {
            "asset_id": "asset_clip1",
            "path": "Grand Canyon/clip1.mp4",
            "media_type": "video",
            "duration_sec": 8.5,
            "description": "Weite Schlucht bei Sonnenuntergang.",
        },
        {
            "asset_id": "asset_clip2",
            "path": "Grand Canyon/clip2.mp4",
            "media_type": "video",
            "duration_sec": 5.0,
            "description": "Wanderer am Rand der Schlucht.",
        },
    ]


def test_folder_voiceover_prompt_contains_style_summary() -> None:
    style_profile = _sample_style_profile()
    prompt = build_folder_voiceover_prompt(
        project_brief=_sample_brief(),
        style_profile=style_profile,
        dramaturgy_entry=_sample_dramaturgy_entry(),
        setting=_sample_setting(),
        previous_folder_name=None,
        next_folder_name="Yellowstone",
        inventory_assets=_sample_inventory_assets(),
    )
    assert style_profile.style_summary_for_prompts in prompt


def test_folder_voiceover_prompt_contains_inventory_asset_ids() -> None:
    prompt = build_folder_voiceover_prompt(
        project_brief=_sample_brief(),
        style_profile=None,
        dramaturgy_entry=_sample_dramaturgy_entry(),
        setting=_sample_setting(),
        previous_folder_name=None,
        next_folder_name="Yellowstone",
        inventory_assets=_sample_inventory_assets(),
    )
    assert "asset_clip1" in prompt
    assert "asset_clip2" in prompt


def test_folder_voiceover_prompt_contains_do_not_merely_describe_hint() -> None:
    prompt = build_folder_voiceover_prompt(
        project_brief=_sample_brief(),
        style_profile=None,
        dramaturgy_entry=_sample_dramaturgy_entry(),
        setting=_sample_setting(),
        previous_folder_name=None,
        next_folder_name="Yellowstone",
        inventory_assets=_sample_inventory_assets(),
    )
    assert "Do not merely describe the assets" in prompt


def test_folder_voiceover_prompt_contains_target_word_count() -> None:
    prompt = build_folder_voiceover_prompt(
        project_brief=_sample_brief(),
        style_profile=None,
        dramaturgy_entry=_sample_dramaturgy_entry(),
        setting=_sample_setting(),
        previous_folder_name=None,
        next_folder_name="Yellowstone",
        inventory_assets=_sample_inventory_assets(),
    )
    assert "140" in prompt


def test_folder_voiceover_prompt_contains_active_negative_rule_instructions() -> None:
    prompt = build_folder_voiceover_prompt(
        project_brief=_sample_brief_with_new_rules(),
        style_profile=None,
        dramaturgy_entry=_sample_dramaturgy_entry(),
        setting=_sample_setting(),
        previous_folder_name=None,
        next_folder_name="Yellowstone",
        inventory_assets=_sample_inventory_assets(),
    )
    assert BRIEF_NEGATIVE_RULE_INSTRUCTIONS["no_party_scenes"] in prompt
    assert BRIEF_NEGATIVE_RULE_INSTRUCTIONS["no_cliches"] in prompt


def test_folder_voiceover_prompt_forbids_inventing_asset_ids() -> None:
    prompt = build_folder_voiceover_prompt(
        project_brief=_sample_brief(),
        style_profile=None,
        dramaturgy_entry=_sample_dramaturgy_entry(),
        setting=_sample_setting(),
        previous_folder_name=None,
        next_folder_name="Yellowstone",
        inventory_assets=_sample_inventory_assets(),
    )
    assert "invent" in prompt.lower()


def test_folder_voiceover_prompt_contains_transition_to_next_instruction() -> None:
    """Nutzerfeedback: neue Spalte 'Übergang zum nächsten Kapitel' — die
    Prompt-Zeile muss sich von 'Übergang von vorher' unterscheiden (kein
    identischer Wortlaut, klar als vorwärtsgerichteter Teaser am Ende
    formuliert)."""
    setting = _sample_setting().model_copy(update={"transition_to_next": True})
    prompt = build_folder_voiceover_prompt(
        project_brief=_sample_brief(),
        style_profile=None,
        dramaturgy_entry=_sample_dramaturgy_entry(),
        setting=setting,
        previous_folder_name=None,
        next_folder_name="Yellowstone",
        inventory_assets=_sample_inventory_assets(),
    )
    teaser_line = next(
        line for line in prompt.splitlines() if "teaser toward the NEXT location" in line
    )
    assert teaser_line.strip().endswith("True")
    # Die beiden Zeilen dürfen nicht identisch sein.
    transition_from_previous_line = next(
        line for line in prompt.splitlines() if "transition from the previous location" in line
    )
    assert transition_from_previous_line != teaser_line


def test_folder_voiceover_prompt_json_schema_includes_transition_to_next_used() -> None:
    prompt = build_folder_voiceover_prompt(
        project_brief=_sample_brief(),
        style_profile=None,
        dramaturgy_entry=_sample_dramaturgy_entry(),
        setting=_sample_setting(),
        previous_folder_name=None,
        next_folder_name="Yellowstone",
        inventory_assets=_sample_inventory_assets(),
    )
    assert '"transition_to_next_used": false' in prompt


def _sample_draft() -> FolderVoiceoverDraft:
    return FolderVoiceoverDraft(
        project_id="p1",
        folder_name="Grand Canyon",
        voiceover_text_full="Zwischen den roten Felswänden scheint das Licht von innen zu leuchten.",
        word_count=11,
        sentence_items=[
            SentenceItem(sentence_id="sentence_001", text="Zwischen den roten Felswänden...")
        ],
    )


def test_voiceover_review_prompt_contains_error_type_list() -> None:
    prompt = build_voiceover_review_prompt(
        project_brief=_sample_brief(),
        style_profile=_sample_style_profile(),
        setting=_sample_setting(),
        draft=_sample_draft(),
    )
    for error_type in ("TOO_GENERIC", "HALLUCINATED_FACT", "STYLE_PROFILE_MISMATCH"):
        assert error_type in prompt


def test_voiceover_correction_prompt_contains_original_text_and_errors() -> None:
    errors = [
        ValidationError(
            type="TOO_ASSET_DESCRIPTIVE",
            severity="BLOCKER",
            sentence_id="sentence_001",
            message="Klingt wie eine Assetbeschreibung.",
            fix_hint="Umformulieren als echte Erzählung.",
        )
    ]
    prompt = build_voiceover_correction_prompt(
        project_brief=_sample_brief(),
        style_profile=_sample_style_profile(),
        setting=_sample_setting(),
        draft=_sample_draft(),
        errors=errors,
    )
    assert "Zwischen den roten Felswänden" in prompt
    assert "TOO_ASSET_DESCRIPTIVE" in prompt
    assert "Klingt wie eine Assetbeschreibung." in prompt


# --- build_intro_hook_prompt (Phase 5) ---


def _sample_intro_settings() -> IntroHookSettings:
    return IntroHookSettings(project_id="p1", target_words=70, min_words=60, max_words=80)


def _sample_dramaturgy_plan() -> DramaturgyPlan:
    return DramaturgyPlan(
        project_id="p1",
        narrative_arc="Von ruhig zu überwältigend.",
        core_promise="Eine Reise durch die eindrucksvollsten Naturwunder.",
        recommended_folder_order=[
            DramaturgyFolderEntry(folder_name="Grand Canyon", order_index=1, dramaturgy_role="opener"),
            DramaturgyFolderEntry(folder_name="Yellowstone", order_index=2, dramaturgy_role="climax"),
        ],
    )


def _sample_confirmed_folder_voiceovers() -> list[FolderVoiceoverDraft]:
    return [
        FolderVoiceoverDraft(
            project_id="p1",
            folder_name="Grand Canyon",
            order_index=1,
            voiceover_text_full="Zwischen den roten Felswänden scheint das Licht von innen zu leuchten.",
            word_count=11,
            sentence_items=[
                SentenceItem(
                    sentence_id="sentence_001",
                    text="Zwischen den roten Felswänden scheint das Licht von innen zu leuchten.",
                    primary_asset_id="asset_clip1",
                )
            ],
        ),
        FolderVoiceoverDraft(
            project_id="p1",
            folder_name="Yellowstone",
            order_index=2,
            voiceover_text_full="Heiße Quellen brodeln unter einem Himmel aus Dampf.",
            word_count=8,
            sentence_items=[
                SentenceItem(
                    sentence_id="sentence_001",
                    text="Heiße Quellen brodeln unter einem Himmel aus Dampf.",
                    primary_asset_id="asset_geyser1",
                )
            ],
        ),
    ]


def test_intro_hook_prompt_contains_project_title() -> None:
    prompt = build_intro_hook_prompt(
        project_brief=_sample_brief(),
        style_profile=_sample_style_profile(),
        dramaturgy_plan=_sample_dramaturgy_plan(),
        confirmed_folder_voiceovers=_sample_confirmed_folder_voiceovers(),
        settings=_sample_intro_settings(),
    )
    assert "Wunder der Wüste" in prompt


def test_intro_hook_prompt_contains_style_summary() -> None:
    style_profile = _sample_style_profile()
    prompt = build_intro_hook_prompt(
        project_brief=_sample_brief(),
        style_profile=style_profile,
        dramaturgy_plan=_sample_dramaturgy_plan(),
        confirmed_folder_voiceovers=_sample_confirmed_folder_voiceovers(),
        settings=_sample_intro_settings(),
    )
    assert style_profile.style_summary_for_prompts in prompt


def test_intro_hook_prompt_contains_confirmed_folder_voiceovers() -> None:
    prompt = build_intro_hook_prompt(
        project_brief=_sample_brief(),
        style_profile=None,
        dramaturgy_plan=_sample_dramaturgy_plan(),
        confirmed_folder_voiceovers=_sample_confirmed_folder_voiceovers(),
        settings=_sample_intro_settings(),
    )
    assert "Zwischen den roten Felswänden scheint das Licht von innen zu leuchten." in prompt
    assert "Heiße Quellen brodeln unter einem Himmel aus Dampf." in prompt


def test_intro_hook_prompt_contains_sentence_items() -> None:
    prompt = build_intro_hook_prompt(
        project_brief=_sample_brief(),
        style_profile=None,
        dramaturgy_plan=_sample_dramaturgy_plan(),
        confirmed_folder_voiceovers=_sample_confirmed_folder_voiceovers(),
        settings=_sample_intro_settings(),
    )
    assert "sentence_001" in prompt
    assert "asset_clip1" in prompt
    assert "asset_geyser1" in prompt


def test_intro_hook_prompt_requests_exactly_5_candidates() -> None:
    prompt = build_intro_hook_prompt(
        project_brief=_sample_brief(),
        style_profile=None,
        dramaturgy_plan=_sample_dramaturgy_plan(),
        confirmed_folder_voiceovers=_sample_confirmed_folder_voiceovers(),
        settings=_sample_intro_settings(),
    )
    assert "EXACTLY 5" in prompt


def test_intro_hook_prompt_contains_do_not_merely_summarize() -> None:
    prompt = build_intro_hook_prompt(
        project_brief=_sample_brief(),
        style_profile=None,
        dramaturgy_plan=_sample_dramaturgy_plan(),
        confirmed_folder_voiceovers=_sample_confirmed_folder_voiceovers(),
        settings=_sample_intro_settings(),
    )
    assert "Do not merely summarize" in prompt


def test_intro_hook_prompt_forbids_inventing_asset_ids() -> None:
    prompt = build_intro_hook_prompt(
        project_brief=_sample_brief(),
        style_profile=None,
        dramaturgy_plan=_sample_dramaturgy_plan(),
        confirmed_folder_voiceovers=_sample_confirmed_folder_voiceovers(),
        settings=_sample_intro_settings(),
    )
    assert "Do not invent asset IDs" in prompt


def test_intro_hook_prompt_contains_active_negative_rule_instructions() -> None:
    prompt = build_intro_hook_prompt(
        project_brief=_sample_brief_with_new_rules(),
        style_profile=None,
        dramaturgy_plan=_sample_dramaturgy_plan(),
        confirmed_folder_voiceovers=_sample_confirmed_folder_voiceovers(),
        settings=_sample_intro_settings(),
    )
    assert BRIEF_NEGATIVE_RULE_INSTRUCTIONS["biblical_chronology_required"] in prompt
    assert BRIEF_NEGATIVE_RULE_INSTRUCTIONS["no_party_scenes"] in prompt
