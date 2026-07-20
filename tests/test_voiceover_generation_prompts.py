"""Phase 2: Prompt-Builder-Tests (build_style_profile_prompt)."""

from __future__ import annotations

from otio_app.defaults import BRIEF_NEGATIVE_RULE_INSTRUCTIONS
from otio_app.services.voiceover_generation.folder_asset_readiness import (
    ISSUE_TYPE_CLOSING_SHOT_MISSING,
    SentenceAssetReadinessIssue,
)
from otio_app.services.voiceover_generation.models import (
    ClosingVisualPlan,
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
    build_asset_allocation_correction_prompt,
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


def test_dramaturgy_prompt_excludes_asset_descriptions() -> None:
    summaries = _sample_folder_summaries()
    summaries[0].notable_asset_descriptions = [
        "Eindrucksvolle Aufnahme von Grand Canyon bei Sonnenuntergang."
    ]
    prompt = build_dramaturgy_prompt(
        project_brief=_sample_brief(),
        style_profile=_sample_style_profile(),
        folder_summaries=summaries,
    )
    assert "notable_asset_descriptions" not in prompt
    assert "Eindrucksvolle Aufnahme von Grand Canyon" not in prompt
    assert "Chapters / locations" in prompt


def test_dramaturgy_prompt_geography_mode_contains_geography_instructions() -> None:
    prompt = build_dramaturgy_prompt(
        project_brief=_sample_brief(),
        style_profile=_sample_style_profile(),
        folder_summaries=_sample_folder_summaries(),
        planning_mode="geography",
    )
    assert "GEOGRAPHY FIRST" in prompt
    assert "coherent travel journey" in prompt


def test_dramaturgy_prompt_variety_mode_contains_variety_instructions() -> None:
    prompt = build_dramaturgy_prompt(
        project_brief=_sample_brief(),
        style_profile=_sample_style_profile(),
        folder_summaries=_sample_folder_summaries(),
        planning_mode="variety",
    )
    assert "MAXIMUM VARIETY" in prompt
    assert "narrative contrast" in prompt


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
    assert 'teaser toward "Yellowstone"' in prompt
    # Die beiden Zeilen dürfen nicht identisch sein.
    transition_from_previous_line = next(
        line for line in prompt.splitlines() if "transition from the previous location" in line
    )
    teaser_line = next(line for line in prompt.splitlines() if "brief teaser toward" in line)
    assert transition_from_previous_line != teaser_line


def test_folder_voiceover_prompt_contains_pause_after_instruction() -> None:
    """Nutzerfeedback: Pausen zwischen Abschnitten wie in den Style-References-
    Beispielen ('[pause 4 seconds]'). Der Prompt muss pause_after als
    qualitatives Feld (kurz/mittel/lang, nicht exakte Sekunden) einführen."""
    prompt = build_folder_voiceover_prompt(
        project_brief=_sample_brief(),
        style_profile=None,
        dramaturgy_entry=_sample_dramaturgy_entry(),
        setting=_sample_setting(),
        previous_folder_name=None,
        next_folder_name="Yellowstone",
        inventory_assets=_sample_inventory_assets(),
    )
    assert "pause_after" in prompt
    assert "'short', 'medium', 'long'" in prompt
    assert '"pause_after": ""' in prompt


def test_folder_voiceover_prompt_forbids_deferral_language_for_transition_to_next() -> None:
    """Nutzerfeedback (Live-Test): 'von der später noch die Rede sein wird'
    impliziert, dass der nächste Ort erst viel später im Video kommt — der
    Prompt muss explizit klarmachen, dass es der UNMITTELBAR nächste Abschnitt
    ist, und aufschiebende Formulierungen verbieten."""
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
    assert "VERY NEXT section" in prompt
    assert "immediately after this one" in prompt
    assert "von der später noch die Rede sein wird" in prompt  # als verbotenes Beispiel genannt
    assert "deferral language" in prompt


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


# --- build_asset_allocation_correction_prompt (Nutzervorgabe Juli 2026) ---


def _sample_draft_with_closing() -> FolderVoiceoverDraft:
    return FolderVoiceoverDraft(
        project_id="p1",
        folder_name="Grand Canyon",
        voiceover_text_full="Zwischen den roten Felswänden scheint das Licht von innen zu leuchten.",
        word_count=11,
        sentence_items=[
            SentenceItem(sentence_id="sentence_001", text="Zwischen den roten Felswänden...",
                         primary_asset_id="asset_clip1"),
        ],
        closing_visual_plan=ClosingVisualPlan(primary_asset_id="asset_clip1"),
    )


def test_asset_allocation_correction_prompt_contains_original_text_and_issues() -> None:
    issues = [
        SentenceAssetReadinessIssue(
            sentence_id="closing",
            issue_type=ISSUE_TYPE_CLOSING_SHOT_MISSING,
            message="Kein Closing Shot geplant.",
        )
    ]
    prompt = build_asset_allocation_correction_prompt(
        project_brief=_sample_brief(),
        style_profile=_sample_style_profile(),
        setting=_sample_setting(),
        draft=_sample_draft_with_closing(),
        inventory_assets=_sample_inventory_assets(),
        issues=issues,
    )
    assert "Zwischen den roten Felswänden" in prompt
    assert ISSUE_TYPE_CLOSING_SHOT_MISSING in prompt
    assert "Kein Closing Shot geplant." in prompt


def test_asset_allocation_correction_prompt_json_schema_includes_closing_visual_plan() -> None:
    prompt = build_asset_allocation_correction_prompt(
        project_brief=_sample_brief(),
        style_profile=None,
        setting=_sample_setting(),
        draft=_sample_draft_with_closing(),
        inventory_assets=_sample_inventory_assets(),
        issues=[],
    )
    assert '"closing_visual_plan"' in prompt


def test_asset_allocation_correction_prompt_instructs_not_to_rewrite_text() -> None:
    prompt = build_asset_allocation_correction_prompt(
        project_brief=_sample_brief(),
        style_profile=None,
        setting=_sample_setting(),
        draft=_sample_draft_with_closing(),
        inventory_assets=_sample_inventory_assets(),
        issues=[],
    )
    assert "do NOT rewrite the voice-over text" in prompt


def test_asset_allocation_correction_prompt_repeats_allocation_rules() -> None:
    prompt = build_asset_allocation_correction_prompt(
        project_brief=_sample_brief(),
        style_profile=None,
        setting=_sample_setting(),
        draft=_sample_draft_with_closing(),
        inventory_assets=_sample_inventory_assets(),
        issues=[],
    )
    assert "at or below 3" in prompt
    assert "at least 4 shot positions" in prompt


# --- Phase 3 (Asset-bewusste Cut-Plan-Vorbereitung): Visual editing awareness ---


def test_folder_voiceover_prompt_contains_visual_editing_awareness_section() -> None:
    prompt = build_folder_voiceover_prompt(
        project_brief=_sample_brief(),
        style_profile=None,
        dramaturgy_entry=_sample_dramaturgy_entry(),
        setting=_sample_setting(),
        previous_folder_name=None,
        next_folder_name="Yellowstone",
        inventory_assets=_sample_inventory_assets(),
    )
    assert "Visual editing awareness" in prompt


def test_folder_voiceover_prompt_forbids_consecutive_same_primary_asset() -> None:
    prompt = build_folder_voiceover_prompt(
        project_brief=_sample_brief(),
        style_profile=None,
        dramaturgy_entry=_sample_dramaturgy_entry(),
        setting=_sample_setting(),
        previous_folder_name=None,
        next_folder_name="Yellowstone",
        inventory_assets=_sample_inventory_assets(),
    )
    assert "SAME asset as primary_asset_id to two sentences" in prompt
    assert "Do not assign the same primary_asset_id to two consecutive sentence_items" in prompt


def test_folder_voiceover_prompt_instructs_not_to_omit_details_without_asset() -> None:
    """Nutzergrundsatz: das Skript soll weiterhin auf vorhandenen Assets
    aufbauen, darf aber wichtige erzählerische Details nicht nur deshalb
    auslassen, weil kein lokales Asset existiert — dann Supplement statt
    Detailverlust."""
    prompt = build_folder_voiceover_prompt(
        project_brief=_sample_brief(),
        style_profile=None,
        dramaturgy_entry=_sample_dramaturgy_entry(),
        setting=_sample_setting(),
        previous_folder_name=None,
        next_folder_name="Yellowstone",
        inventory_assets=_sample_inventory_assets(),
    )
    assert "Do NOT omit an important narrative detail just because no local asset shows it" in prompt


def test_folder_voiceover_prompt_instructs_backup_asset_diversity() -> None:
    prompt = build_folder_voiceover_prompt(
        project_brief=_sample_brief(),
        style_profile=None,
        dramaturgy_entry=_sample_dramaturgy_entry(),
        setting=_sample_setting(),
        previous_folder_name=None,
        next_folder_name="Yellowstone",
        inventory_assets=_sample_inventory_assets(),
    )
    assert "backup_asset_ids" in prompt
    assert "further plausible, DIFFERENT assets" in prompt


def test_folder_voiceover_prompt_instructs_visual_coverage_for_long_sentences() -> None:
    prompt = build_folder_voiceover_prompt(
        project_brief=_sample_brief(),
        style_profile=None,
        dramaturgy_entry=_sample_dramaturgy_entry(),
        setting=_sample_setting(),
        previous_folder_name=None,
        next_folder_name="Yellowstone",
        inventory_assets=_sample_inventory_assets(),
    )
    assert "needs enough distinct, usable local coverage for that split" in prompt
    assert "prefer several SHORTER sentences/beats" in prompt


def test_folder_voiceover_prompt_instructs_concrete_visual_intent() -> None:
    prompt = build_folder_voiceover_prompt(
        project_brief=_sample_brief(),
        style_profile=None,
        dramaturgy_entry=_sample_dramaturgy_entry(),
        setting=_sample_setting(),
        previous_folder_name=None,
        next_folder_name="Yellowstone",
        inventory_assets=_sample_inventory_assets(),
    )
    assert "visual_intent must state a concrete visual purpose" in prompt
    assert "not a generic restatement of the sentence text" in prompt


def test_folder_voiceover_prompt_json_schema_includes_second_backup_asset_ids() -> None:
    prompt = build_folder_voiceover_prompt(
        project_brief=_sample_brief(),
        style_profile=None,
        dramaturgy_entry=_sample_dramaturgy_entry(),
        setting=_sample_setting(),
        previous_folder_name=None,
        next_folder_name="Yellowstone",
        inventory_assets=_sample_inventory_assets(),
    )
    assert '"second_backup_asset_ids": []' in prompt


def test_folder_voiceover_prompt_json_schema_includes_visual_asset_plan() -> None:
    prompt = build_folder_voiceover_prompt(
        project_brief=_sample_brief(),
        style_profile=None,
        dramaturgy_entry=_sample_dramaturgy_entry(),
        setting=_sample_setting(),
        previous_folder_name=None,
        next_folder_name="Yellowstone",
        inventory_assets=_sample_inventory_assets(),
    )
    assert '"visual_asset_plan"' in prompt
    assert '"preferred_cut_count": 1' in prompt
    assert '"reuse_risk": ""' in prompt
    assert '"needs_visual_variety": false' in prompt
    assert '"supplement_search_hint": ""' in prompt


def test_folder_voiceover_prompt_instructs_second_backup_must_genuinely_fit() -> None:
    """Nutzergrundsatz: second_backup_asset_ids muss unbedingt auch passend
    sein, sonst weichen wir einfach auf Supplement-Assets aus — ein
    beliebiges Füllasset dort ist ausdrücklich verboten."""
    prompt = build_folder_voiceover_prompt(
        project_brief=_sample_brief(),
        style_profile=None,
        dramaturgy_entry=_sample_dramaturgy_entry(),
        setting=_sample_setting(),
        previous_folder_name=None,
        next_folder_name="Yellowstone",
        inventory_assets=_sample_inventory_assets(),
    )
    assert "NEVER add an asset there just to fill the list" in prompt
    assert "a weak filler asset is worse than an honest gap" in prompt


def test_folder_voiceover_prompt_instructs_supplement_search_hint_semantics() -> None:
    prompt = build_folder_voiceover_prompt(
        project_brief=_sample_brief(),
        style_profile=None,
        dramaturgy_entry=_sample_dramaturgy_entry(),
        setting=_sample_setting(),
        previous_folder_name=None,
        next_folder_name="Yellowstone",
        inventory_assets=_sample_inventory_assets(),
    )
    assert "supplement_search_hint" in prompt
    assert "location-prefixed search phrase" in prompt


def test_folder_voiceover_prompt_json_schema_includes_planned_segments() -> None:
    prompt = build_folder_voiceover_prompt(
        project_brief=_sample_brief(),
        style_profile=None,
        dramaturgy_entry=_sample_dramaturgy_entry(),
        setting=_sample_setting(),
        previous_folder_name=None,
        next_folder_name="Yellowstone",
        inventory_assets=_sample_inventory_assets(),
    )
    assert '"planned_segments": []' in prompt


def test_folder_voiceover_prompt_instructs_planned_segments_semantics() -> None:
    """Default-Modus (PER_SENTENCE, Phase 7.1) — siehe eigene Mode-Tests
    weiter unten für die anderen beiden Modi."""
    prompt = build_folder_voiceover_prompt(
        project_brief=_sample_brief(),
        style_profile=None,
        dramaturgy_entry=_sample_dramaturgy_entry(),
        setting=_sample_setting(),
        previous_folder_name=None,
        next_folder_name="Yellowstone",
        inventory_assets=_sample_inventory_assets(),
    )
    assert "leave visual_asset_plan.preferred_cut_count at 1 and" in prompt
    assert "planned_segments empty" in prompt


def test_folder_voiceover_prompt_rules_require_valid_planned_segment_asset_ids() -> None:
    prompt = build_folder_voiceover_prompt(
        project_brief=_sample_brief(),
        style_profile=None,
        dramaturgy_entry=_sample_dramaturgy_entry(),
        setting=_sample_setting(),
        previous_folder_name=None,
        next_folder_name="Yellowstone",
        inventory_assets=_sample_inventory_assets(),
    )
    assert "planned_segments (if used) MUST also only reference asset_id values" in prompt
    assert "segment_order MUST start at 1 and be unique" in prompt


# --- Phase 7.1 (Asset-bewusste Cut-Plan-Vorbereitung): Segment-Planungsmodus ---


# --- Closing shot + global asset allocation (Nutzervorgabe Juli 2026) ---


def test_folder_voiceover_prompt_json_schema_includes_closing_visual_plan() -> None:
    prompt = build_folder_voiceover_prompt(
        project_brief=_sample_brief(),
        style_profile=None,
        dramaturgy_entry=_sample_dramaturgy_entry(),
        setting=_sample_setting(),
        previous_folder_name=None,
        next_folder_name="Yellowstone",
        inventory_assets=_sample_inventory_assets(),
    )
    assert '"closing_visual_plan"' in prompt
    assert '"needs_supplement_asset": false' in prompt
    assert '"supplement_search_hint"' in prompt


def test_folder_voiceover_prompt_requires_closing_shot() -> None:
    prompt = build_folder_voiceover_prompt(
        project_brief=_sample_brief(),
        style_profile=None,
        dramaturgy_entry=_sample_dramaturgy_entry(),
        setting=_sample_setting(),
        previous_folder_name=None,
        next_folder_name="Yellowstone",
        inventory_assets=_sample_inventory_assets(),
    )
    assert "Closing shot for this location (required)" in prompt


def test_folder_voiceover_prompt_forbids_closing_shot_reusing_last_two_sentences() -> None:
    prompt = build_folder_voiceover_prompt(
        project_brief=_sample_brief(),
        style_profile=None,
        dramaturgy_entry=_sample_dramaturgy_entry(),
        setting=_sample_setting(),
        previous_folder_name=None,
        next_folder_name="Yellowstone",
        inventory_assets=_sample_inventory_assets(),
    )
    assert "SECOND-TO-LAST sentence/beat" in prompt
    assert "MUST NOT be the" in prompt


def test_folder_voiceover_prompt_prefers_video_and_aerial_for_closing_shot() -> None:
    prompt = build_folder_voiceover_prompt(
        project_brief=_sample_brief(),
        style_profile=None,
        dramaturgy_entry=_sample_dramaturgy_entry(),
        setting=_sample_setting(),
        previous_folder_name=None,
        next_folder_name="Yellowstone",
        inventory_assets=_sample_inventory_assets(),
    )
    assert "Prefer a VIDEO over a photo" in prompt
    assert "aerial/drone shot" in prompt


def test_folder_voiceover_prompt_instructs_max_three_total_occurrences() -> None:
    prompt = build_folder_voiceover_prompt(
        project_brief=_sample_brief(),
        style_profile=None,
        dramaturgy_entry=_sample_dramaturgy_entry(),
        setting=_sample_setting(),
        previous_folder_name=None,
        next_folder_name="Yellowstone",
        inventory_assets=_sample_inventory_assets(),
    )
    assert "at or below 3" in prompt


def test_folder_voiceover_prompt_instructs_minimum_shot_distance() -> None:
    prompt = build_folder_voiceover_prompt(
        project_brief=_sample_brief(),
        style_profile=None,
        dramaturgy_entry=_sample_dramaturgy_entry(),
        setting=_sample_setting(),
        previous_folder_name=None,
        next_folder_name="Yellowstone",
        inventory_assets=_sample_inventory_assets(),
    )
    assert "at least 4 shot positions" in prompt


def test_folder_voiceover_prompt_instructs_scarce_asset_priority() -> None:
    prompt = build_folder_voiceover_prompt(
        project_brief=_sample_brief(),
        style_profile=None,
        dramaturgy_entry=_sample_dramaturgy_entry(),
        setting=_sample_setting(),
        previous_folder_name=None,
        next_folder_name="Yellowstone",
        inventory_assets=_sample_inventory_assets(),
    )
    assert "KEEPS that asset" in prompt
    assert "MORE FLEXIBLE sentence/beat" in prompt


def test_folder_voiceover_prompt_default_mode_is_per_sentence() -> None:
    """Default (kein explizit gesetzter Modus) muss dem heutigen Verhalten
    entsprechen — bestehende Projekte ändern sich dadurch nicht."""
    from otio_app.defaults import SEGMENT_ASSET_PLANNING_MODE_PER_SENTENCE

    setting = _sample_setting()
    assert setting.segment_asset_planning_mode == SEGMENT_ASSET_PLANNING_MODE_PER_SENTENCE


def test_folder_voiceover_prompt_per_sentence_mode_forbids_planned_segments() -> None:
    from otio_app.defaults import SEGMENT_ASSET_PLANNING_MODE_PER_SENTENCE

    setting = _sample_setting().model_copy(
        update={"segment_asset_planning_mode": SEGMENT_ASSET_PLANNING_MODE_PER_SENTENCE}
    )
    prompt = build_folder_voiceover_prompt(
        project_brief=_sample_brief(),
        style_profile=None,
        dramaturgy_entry=_sample_dramaturgy_entry(),
        setting=setting,
        previous_folder_name=None,
        next_folder_name="Yellowstone",
        inventory_assets=_sample_inventory_assets(),
    )
    assert "ONE asset per sentence" in prompt
    assert "Do not propose your own multi-shot breakdown" in prompt


def test_folder_voiceover_prompt_per_segment_mode_encourages_splitting() -> None:
    from otio_app.defaults import SEGMENT_ASSET_PLANNING_MODE_PER_SEGMENT

    setting = _sample_setting().model_copy(
        update={"segment_asset_planning_mode": SEGMENT_ASSET_PLANNING_MODE_PER_SEGMENT}
    )
    prompt = build_folder_voiceover_prompt(
        project_brief=_sample_brief(),
        style_profile=None,
        dramaturgy_entry=_sample_dramaturgy_entry(),
        setting=setting,
        previous_folder_name=None,
        next_folder_name="Yellowstone",
        inventory_assets=_sample_inventory_assets(),
    )
    assert "split into multiple shots where it helps" in prompt
    assert "Use this multi-shot planning generously" in prompt
    # Die PER_SENTENCE-spezifische Verbots-Formulierung darf NICHT auftauchen.
    assert "Do not propose your own multi-shot breakdown" not in prompt


def test_folder_voiceover_prompt_llm_discretion_mode_balances_variety_and_calm() -> None:
    from otio_app.defaults import SEGMENT_ASSET_PLANNING_MODE_LLM_DISCRETION

    setting = _sample_setting().model_copy(
        update={"segment_asset_planning_mode": SEGMENT_ASSET_PLANNING_MODE_LLM_DISCRETION}
    )
    prompt = build_folder_voiceover_prompt(
        project_brief=_sample_brief(),
        style_profile=None,
        dramaturgy_entry=_sample_dramaturgy_entry(),
        setting=setting,
        previous_folder_name=None,
        next_folder_name="Yellowstone",
        inventory_assets=_sample_inventory_assets(),
    )
    assert "varied, but never restless" in prompt
    assert "do NOT cut between shots just to add movement" in prompt
    assert "prefer the calmer option (single shot)" in prompt


def test_folder_voiceover_prompt_unknown_mode_falls_back_to_per_sentence() -> None:
    """Schutz vor einem ungültigen/veralteten Wert — darf niemals
    versehentlich aktives Multi-Shot-Planen auslösen."""
    setting = _sample_setting().model_copy(update={"segment_asset_planning_mode": "not_a_real_mode"})
    prompt = build_folder_voiceover_prompt(
        project_brief=_sample_brief(),
        style_profile=None,
        dramaturgy_entry=_sample_dramaturgy_entry(),
        setting=setting,
        previous_folder_name=None,
        next_folder_name="Yellowstone",
        inventory_assets=_sample_inventory_assets(),
    )
    assert "ONE asset per sentence" in prompt


def test_voiceover_correction_prompt_respects_segment_asset_planning_mode() -> None:
    from otio_app.defaults import SEGMENT_ASSET_PLANNING_MODE_PER_SEGMENT

    setting = _sample_setting().model_copy(
        update={"segment_asset_planning_mode": SEGMENT_ASSET_PLANNING_MODE_PER_SEGMENT}
    )
    prompt = build_voiceover_correction_prompt(
        project_brief=_sample_brief(),
        style_profile=_sample_style_profile(),
        setting=setting,
        draft=_sample_draft(),
        errors=[],
    )
    assert "split into multiple shots where it helps" in prompt


def test_voiceover_correction_prompt_json_schema_includes_planned_segments() -> None:
    prompt = build_voiceover_correction_prompt(
        project_brief=_sample_brief(),
        style_profile=_sample_style_profile(),
        setting=_sample_setting(),
        draft=_sample_draft(),
        errors=[],
    )
    assert '"planned_segments": []' in prompt


def test_voiceover_correction_prompt_json_schema_includes_new_phase4_fields() -> None:
    prompt = build_voiceover_correction_prompt(
        project_brief=_sample_brief(),
        style_profile=_sample_style_profile(),
        setting=_sample_setting(),
        draft=_sample_draft(),
        errors=[],
    )
    assert '"second_backup_asset_ids": []' in prompt
    assert '"visual_asset_plan"' in prompt


def test_voiceover_correction_prompt_forbids_filler_second_backup() -> None:
    prompt = build_voiceover_correction_prompt(
        project_brief=_sample_brief(),
        style_profile=_sample_style_profile(),
        setting=_sample_setting(),
        draft=_sample_draft(),
        errors=[],
    )
    assert "never as filler" in prompt


def test_voiceover_correction_prompt_reminds_of_visual_editing_awareness() -> None:
    """Auch bei einer Korrektur (die nur konkrete Fehler beheben soll) darf
    die asset-bewusste Schnittlogik nicht verloren gehen."""
    errors = [
        ValidationError(
            type="TOO_ASSET_DESCRIPTIVE",
            severity="BLOCKER",
            sentence_id="sentence_001",
            message="Klingt wie eine Assetbeschreibung.",
        )
    ]
    prompt = build_voiceover_correction_prompt(
        project_brief=_sample_brief(),
        style_profile=_sample_style_profile(),
        setting=_sample_setting(),
        draft=_sample_draft(),
        errors=errors,
    )
    assert "don't assign the same primary_asset_id to two consecutive sentence_items" in prompt
    assert "needs_supplement_asset=true" in prompt


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


def test_intro_hook_prompt_uses_chapter_narration_not_sentence_assets() -> None:
    prompt = build_intro_hook_prompt(
        project_brief=_sample_brief(),
        style_profile=None,
        dramaturgy_plan=_sample_dramaturgy_plan(),
        confirmed_folder_voiceovers=_sample_confirmed_folder_voiceovers(),
        settings=_sample_intro_settings(),
    )
    assert "Chapter narrations" in prompt
    assert "Zwischen den roten Felswänden scheint das Licht von innen zu leuchten." in prompt
    assert "sentence_001" not in prompt
    assert "asset_clip1" not in prompt
    assert "asset_geyser1" not in prompt
    assert "Sentence/beat breakdown" not in prompt


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
    assert "No sentence_items or inventory are provided" in prompt


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


def test_native_speaker_block_in_folder_voiceover_and_dramaturgy() -> None:
    from otio_app.services.voiceover_generation.prompts import native_speaker_language_block

    block = native_speaker_language_block("EN")
    assert "Target language code: EN" in block
    assert "English" in block
    assert "NATIVE SPEAKER" in block
    assert "CONTENT SOURCE ONLY" in block
    assert "NEVER copy" in block

    vo = build_folder_voiceover_prompt(
        project_brief=_sample_brief(),
        style_profile=None,
        dramaturgy_entry=_sample_dramaturgy_entry(),
        setting=FolderVoiceoverSetting(folder_name="Antelope Canyon", target_words=135),
        previous_folder_name=None,
        next_folder_name=None,
        inventory_assets=_sample_inventory_assets(),
    )
    assert "native-speaker rule (MANDATORY)" in vo
    assert "CONTENT SOURCE ONLY" in vo
    assert "German" in vo  # sample brief language DE

    dram = build_dramaturgy_prompt(
        project_brief=_sample_brief(),
        style_profile=None,
        folder_summaries=[],
    )
    assert "native-speaker rule (MANDATORY)" in dram


def test_intro_and_review_prompts_include_native_speaker_rule() -> None:
    intro = build_intro_hook_prompt(
        project_brief=_sample_brief(),
        style_profile=None,
        dramaturgy_plan=_sample_dramaturgy_plan(),
        confirmed_folder_voiceovers=_sample_confirmed_folder_voiceovers(),
        settings=_sample_intro_settings(),
    )
    assert "native-speaker rule (MANDATORY)" in intro

    review = build_voiceover_review_prompt(
        project_brief=_sample_brief(),
        style_profile=None,
        setting=FolderVoiceoverSetting(folder_name="Antelope Canyon", target_words=135),
        draft=_sample_confirmed_folder_voiceovers()[0],
    )
    assert "native-speaker rule (MANDATORY)" in review


def test_dramaturgy_prompt_omits_craft_flags_entirely() -> None:
    prompt = build_dramaturgy_prompt(
        project_brief=_sample_brief(),
        style_profile=_sample_style_profile(),
        folder_summaries=_sample_folder_summaries(),
    )
    assert "use_transition_from_previous" not in prompt
    assert "use_transition_to_next" not in prompt
    assert "use_callback_to_previous" not in prompt
    assert "use_contrast_with_previous" not in prompt
    assert "use_commonality_with_previous" not in prompt
    assert "transition_goal_to_next" not in prompt
    assert "transition_from_previous_hint" not in prompt
    assert "contrast_or_commonality_hint" not in prompt
    assert "Do NOT output per-chapter transition/callback/contrast checkboxes" in prompt
    assert '"recommended_word_count": 150' in prompt
    assert "about 150 words" in prompt
    assert "120–180" in prompt or "120-180" in prompt
    assert "Do NOT use rigid fixed pairs" in prompt
    assert "estimated_voiceover_word_count" not in prompt
    assert '"risks": []' in prompt


def test_folder_voiceover_prompt_omits_craft_params_when_flags_inactive() -> None:
    entry = _sample_dramaturgy_entry().model_copy(
        update={
            "transition_from_previous_hint": "Leave the canyon silence behind.",
            "contrast_or_commonality_hint": "Contrast stone vs steam.",
        }
    )
    prompt = build_folder_voiceover_prompt(
        project_brief=_sample_brief(),
        style_profile=None,
        dramaturgy_entry=entry,
        setting=_sample_setting(),
        previous_folder_name="Antelope Canyon",
        next_folder_name="Yellowstone",
        inventory_assets=_sample_inventory_assets(),
    )
    assert "none active for this location" in prompt
    assert "Leave the canyon silence behind." not in prompt
    assert "Contrast stone vs steam." not in prompt
    assert "brief teaser toward" not in prompt


def test_folder_voiceover_prompt_includes_craft_params_when_flag_active() -> None:
    entry = _sample_dramaturgy_entry().model_copy(
        update={
            "transition_from_previous_hint": "Leave the canyon silence behind.",
            "contrast_or_commonality_hint": "Contrast stone vs steam.",
        }
    )
    setting = _sample_setting().model_copy(
        update={
            "transition_from_previous": True,
            "use_contrast_with_previous": True,
        }
    )
    prompt = build_folder_voiceover_prompt(
        project_brief=_sample_brief(),
        style_profile=None,
        dramaturgy_entry=entry,
        setting=setting,
        previous_folder_name="Antelope Canyon",
        next_folder_name="Yellowstone",
        inventory_assets=_sample_inventory_assets(),
    )
    assert "Leave the canyon silence behind." in prompt
    assert "Contrast stone vs steam." in prompt
    assert "transition from the previous location" in prompt
