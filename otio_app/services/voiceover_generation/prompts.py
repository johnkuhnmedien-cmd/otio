"""Prompt-Builder für die Voice-over-Generierungs-Pipeline.

Phase 2: build_style_profile_prompt()
Phase 3: build_dramaturgy_prompt()
Phase 4: build_folder_voiceover_prompt(), build_voiceover_review_prompt(),
         build_voiceover_correction_prompt()
"""

from __future__ import annotations

from otio_app.services.voiceover_generation.models import (
    DramaturgyFolderEntry,
    DramaturgyPlan,
    FolderInventorySummary,
    FolderVoiceoverDraft,
    FolderVoiceoverSetting,
    IntroHookSettings,
    ProjectBrief,
    ValidationError,
    VoiceoverGenerationModelSettings,
    VoiceoverStyleProfile,
    VoiceoverStyleReferences,
)


def _non_empty(values: list[str]) -> list[str]:
    return [value.strip() for value in values if value and value.strip()]


def _numbered_block(label: str, texts: list[str]) -> str:
    if not texts:
        return "(keine)"
    return "\n\n".join(f"[{label} {index + 1}]\n{text}" for index, text in enumerate(texts))


def build_style_profile_prompt(
    project_brief: ProjectBrief,
    style_references: VoiceoverStyleReferences,
) -> str:
    """Baut den Prompt zur Ableitung eines wiederverwendbaren Style Profiles.

    Wichtig: Der Prompt fordert explizit, dass keine Formulierungen aus den
    Referenzskripten übernommen werden — nur Stilmerkmale sollen extrahiert
    werden (siehe Marker-Satz unten, von Tests geprüft)."""
    intro_refs = _non_empty(style_references.intro_reference_texts)
    segment_refs = _non_empty(style_references.segment_reference_texts)
    upload_texts = _non_empty(style_references.uploaded_file_texts)

    intro_block = _numbered_block("Intro-Referenz", intro_refs)
    segment_block = _numbered_block("Segment-Referenz", segment_refs)
    upload_block = _numbered_block("Hochgeladene Referenz", upload_texts)

    tone_tags = ", ".join(project_brief.tone_tags) or "(keine Angabe)"
    active_negative_rules = ", ".join(
        flag for flag, enabled in project_brief.negative_rule_flags.items() if enabled
    ) or "(keine Angabe)"
    forbidden_phrases = (
        "\n".join(f"- {phrase}" for phrase in project_brief.forbidden_phrases) or "(keine)"
    )

    return f"""You are a documentary style analyst. Analyze the reference scripts below \
and extract a reusable STYLE PROFILE for future narration prompts.

Do not copy phrases or sentences from the reference scripts. Extract style \
characteristics only.

## Project
- Video title: {project_brief.video_title or "(untitled)"}
- Target language: {project_brief.language}
- Desired tone tags: {tone_tags}
- Active negative rules: {active_negative_rules}
- Global negative rules (free text): {project_brief.negative_rules_freetext or "(none)"}
- Forbidden phrases:
{forbidden_phrases}
- Additional editor instructions: {project_brief.global_extra_prompt or "(none)"}

## Intro reference scripts (style analysis only — do not copy)
{intro_block}

## Segment/folder reference scripts (style analysis only — do not copy)
{segment_block}

## Uploaded reference material (style analysis only — do not copy)
{upload_block}

## Task
Extract the following style characteristics from the references above:
- overall_tone
- narration_style
- sentence_length
- pacing
- imagery_style (how visual descriptions are typically phrased)
- intro_hook_style (specifically derived from the intro references)
- segment_style (specifically derived from the segment references)
- do: a short list of concrete things to do
- dont: a short list of concrete things to avoid
- forbidden_phrases: merge the project's forbidden phrases above with anything \
you observed that should specifically be avoided for this style
- style_summary_for_prompts: a compact (3-6 sentence) summary of this style, \
written so it can be pasted directly into future generation prompts as a style \
instruction block, WITHOUT needing to re-read the original reference scripts.

Respond with JSON ONLY, no markdown code fences, no commentary, matching exactly \
this shape:

{{
  "language": "{project_brief.language}",
  "overall_tone": "...",
  "narration_style": "...",
  "sentence_length": "...",
  "pacing": "...",
  "imagery_style": "...",
  "intro_hook_style": "...",
  "segment_style": "...",
  "do": ["..."],
  "dont": ["..."],
  "forbidden_phrases": ["..."],
  "avoid_copying_reference_text": true,
  "style_summary_for_prompts": "..."
}}

IMPORTANT: Do not copy phrases or sentences from the reference scripts. Extract \
style characteristics only.
"""


def _folder_summary_block(summary: FolderInventorySummary) -> str:
    themes = ", ".join(summary.dominant_visual_themes) or "-"
    notable = "; ".join(summary.notable_asset_descriptions) or "-"
    risks = ", ".join(summary.risks) or "-"
    return (
        f"[{summary.folder_name}]\n"
        f"- asset_count: {summary.asset_count} (video: {summary.video_count}, "
        f"image: {summary.image_count})\n"
        f"- total_video_duration_sec: {summary.total_video_duration_sec}\n"
        f"- visual_strength_score: {summary.visual_strength_score}\n"
        f"- asset_diversity_score: {summary.asset_diversity_score}\n"
        f"- has_people: {summary.has_people}, has_motion: {summary.has_motion}, "
        f"has_wide_shots: {summary.has_wide_shots}, has_detail_shots: {summary.has_detail_shots}\n"
        f"- dominant_visual_themes: {themes}\n"
        f"- notable_asset_descriptions: {notable}\n"
        f"- estimated_voiceover_word_count: {summary.estimated_voiceover_word_count} "
        f"({summary.estimated_min_words}-{summary.estimated_max_words})\n"
        f"- risks: {risks}"
    )


def build_dramaturgy_prompt(
    *,
    project_brief: ProjectBrief,
    style_profile: VoiceoverStyleProfile | None,
    folder_summaries: list[FolderInventorySummary],
    model_settings: VoiceoverGenerationModelSettings | None = None,
) -> str:
    """Baut den Prompt zur Dramaturgieplanung über alle Ordner.

    `model_settings` ist Teil der Signatur für API-Symmetrie mit den anderen
    Rollen, wird aber aktuell nicht in den Prompt-Text eingebettet — welches
    Modell aufgerufen wird, ist eine Aufrufer-Entscheidung, kein Prompt-Inhalt.
    """
    del model_settings

    tone_tags = ", ".join(project_brief.tone_tags) or "(keine Angabe)"
    active_negative_rules = ", ".join(
        flag for flag, enabled in project_brief.negative_rule_flags.items() if enabled
    ) or "(keine Angabe)"

    style_block = "(kein Style Profile vorhanden — neutraler Standardstil)"
    if style_profile is not None:
        style_block = (
            f"- overall_tone: {style_profile.overall_tone or '-'}\n"
            f"- narration_style: {style_profile.narration_style or '-'}\n"
            f"- pacing: {style_profile.pacing or '-'}\n"
            f"- intro_hook_style: {style_profile.intro_hook_style or '-'}\n"
            f"- style_summary_for_prompts: {style_profile.style_summary_for_prompts or '-'}"
        )

    folders_block = (
        "\n\n".join(_folder_summary_block(summary) for summary in folder_summaries)
        or "(keine Ordner-Zusammenfassungen verfügbar)"
    )

    return f"""You are a documentary story editor. Plan the DRAMATURGY (narrative \
structure) of a travel/nature documentary across multiple locations (folders). \
This is NOT about describing assets — it is about ORDER, TENSION ARC, and the \
ROLE each location plays in the overall video.

## Project
- Project title: {project_brief.video_title or "(untitled)"}
- Target language: {project_brief.language}
- Desired tone tags: {tone_tags}
- Active negative rules: {active_negative_rules}
- Additional editor instructions: {project_brief.global_extra_prompt or "(none)"}

## Style Profile (already extracted — do not re-derive, just respect it)
{style_block}

## Location / folder summaries (one per location)
{folders_block}

## Task
Decide, for the whole set of locations above:
- Which location works best as the OPENER (hooks attention immediately)?
- Where does a CONTRAST between locations create interest?
- Which location works as the CLIMAX / escalation point?
- Which location works as a calm RESOLUTION / closer?
- What is the most compelling overall narrative arc connecting them?
- For EACH location: its role, a short reason, recommended word count for its \
voice-over section, and a transition idea toward the NEXT location.

Do NOT simply sort alphabetically. Do NOT simply sort by asset count. Consider \
visual strength, diversity, hook potential, contrasts between locations, and the \
overall narrative arc.

Respond with JSON ONLY, no markdown code fences, no commentary, matching exactly \
this shape:

{{
  "project_title": "...",
  "core_promise": "...",
  "narrative_arc": "...",
  "global_transition_strategy": "...",
  "recommended_folder_order": [
    {{
      "folder_name": "...",
      "order_index": 1,
      "enabled": true,
      "dramaturgy_role": "opener|setup|contrast|escalation|climax|resolution",
      "reason": "...",
      "visual_strength_score": 0.0,
      "asset_diversity_score": 0.0,
      "hook_potential_score": 0.0,
      "recommended_word_count": 90,
      "recommended_min_words": 80,
      "recommended_max_words": 100,
      "transition_goal_to_next": "...",
      "transition_from_previous_hint": "...",
      "contrast_or_commonality_hint": "...",
      "risks": []
    }}
  ],
  "risks": []
}}

Include exactly one entry per location listed above, using the EXACT folder_name \
values given. order_index must be unique and start at 1.
"""


def _style_summary_block(style_profile: VoiceoverStyleProfile | None) -> str:
    if style_profile is None:
        return "(kein Style Profile vorhanden — neutraler dokumentarischer Standardstil)"
    return (
        f"- overall_tone: {style_profile.overall_tone or '-'}\n"
        f"- narration_style: {style_profile.narration_style or '-'}\n"
        f"- sentence_length: {style_profile.sentence_length or '-'}\n"
        f"- pacing: {style_profile.pacing or '-'}\n"
        f"- imagery_style: {style_profile.imagery_style or '-'}\n"
        f"- segment_style: {style_profile.segment_style or '-'}\n"
        f"- style_summary_for_prompts: {style_profile.style_summary_for_prompts or '-'}"
    )


def _combined_forbidden_phrases(
    project_brief: ProjectBrief,
    style_profile: VoiceoverStyleProfile | None,
    setting: FolderVoiceoverSetting,
) -> list[str]:
    phrases = list(project_brief.forbidden_phrases)
    if style_profile is not None:
        phrases.extend(style_profile.forbidden_phrases)
    phrases.extend(setting.must_avoid)
    seen: set[str] = set()
    unique: list[str] = []
    for phrase in phrases:
        key = phrase.strip().lower()
        if key and key not in seen:
            seen.add(key)
            unique.append(phrase.strip())
    return unique


def _inventory_asset_block(inventory_assets: list[dict]) -> str:
    if not inventory_assets:
        return "(kein Inventory verfügbar — jeder Satz muss needs_supplement_asset=true setzen)"
    lines = []
    for asset in inventory_assets:
        duration = asset.get("duration_sec")
        duration_text = f"{duration:.1f}s" if isinstance(duration, (int, float)) and duration else "?"
        notable = asset.get("notable_frames") or ""
        notable_text = f" | notable_frames: {notable}" if notable else ""
        lines.append(
            f"- asset_id: {asset.get('asset_id', '')} | type: {asset.get('media_type', '?')} "
            f"| duration: {duration_text} | description: {asset.get('description', '')}"
            f"{notable_text}"
        )
    return "\n".join(lines)


def build_folder_voiceover_prompt(
    *,
    project_brief: ProjectBrief,
    style_profile: VoiceoverStyleProfile | None,
    dramaturgy_entry: DramaturgyFolderEntry,
    setting: FolderVoiceoverSetting,
    previous_folder_name: str | None,
    next_folder_name: str | None,
    inventory_assets: list[dict],
) -> str:
    """Baut den Prompt für die Erzeugung des Voice-over-Textes EINES Ordners.

    Fordert echte Doku-Prosa (kein Assetlisten-Stil) UND eine vollständige,
    strukturierte Satz-/Beat-zu-Asset-Zuordnung im selben JSON-Response."""
    tone_tags = ", ".join(project_brief.tone_tags) or "(keine Angabe)"
    active_negative_rules = ", ".join(
        flag for flag, enabled in project_brief.negative_rule_flags.items() if enabled
    ) or "(keine Angabe)"
    forbidden_phrases = _combined_forbidden_phrases(project_brief, style_profile, setting)
    forbidden_block = "\n".join(f"- {phrase}" for phrase in forbidden_phrases) or "(keine)"
    must_include_block = ", ".join(setting.must_include) or "(keine Angabe)"

    return f"""You are a documentary narration writer. Write the voice-over section \
for ONE location in a multi-location travel/nature documentary.

Do not merely describe the assets. Write polished documentary narration. Use the \
assets only as visual grounding for each sentence or beat.

WRONG (asset description): "You see a canyon with red rocks."
RIGHT (documentary prose): "Between the red rock walls, the light seems to make \
the stone glow from within."

## Project
- Project title: {project_brief.video_title or "(untitled)"}
- Target language: {project_brief.language}
- Desired tone tags: {tone_tags}
- Active global negative rules: {active_negative_rules}
- Global negative rules (free text): {project_brief.negative_rules_freetext or "(none)"}
- Forbidden phrases (global + style + folder must_avoid):
{forbidden_block}

## Style Profile (respect it — do not copy any reference text)
{_style_summary_block(style_profile)}

## This location
- folder_name: {dramaturgy_entry.folder_name}
- dramaturgy_role: {dramaturgy_entry.dramaturgy_role}
- reason for this role: {dramaturgy_entry.reason or "-"}
- transition goal toward the NEXT location: {dramaturgy_entry.transition_goal_to_next or "-"}
- previous location in the video: {previous_folder_name or "(none — this is the first location)"}
- next location in the video: {next_folder_name or "(none — this is the last location)"}
- use a transition from the previous location: {setting.transition_from_previous}
- callback to the previous location later in the text: {setting.callback_to_previous}
- use a contrast with the previous location: {setting.use_contrast_with_previous}
- use a commonality with the previous location: {setting.use_commonality_with_previous}
- factuality mode: {setting.factuality_mode} (strict_inventory_only = only claim what \
is visible in the assets below; normal_safe_general_knowledge = safe, well-known \
general facts allowed; atmospheric_no_hard_facts = avoid factual claims entirely, \
stay purely atmospheric/sensory)
- energy: {setting.energy}
- must include (topics/ideas, not literal phrases): {must_include_block}
- editor's extra instructions for this location: {setting.folder_extra_prompt or "(none)"}

## Target length
- target_words: {setting.target_words} (min {setting.min_words}, max {setting.max_words})

## Inventory for this location (asset_id values are EXACT — never invent new ones)
{_inventory_asset_block(inventory_assets)}

## Task
Write ONE flowing documentary voice-over text for this location (target_words above), \
then break it into sentence_items — one entry per sentence or narrative beat — each \
with a visual assignment.

Rules for sentence_items:
- Every sentence/beat needs an asset assignment OR needs_supplement_asset=true.
- primary_asset_id MUST be one of the EXACT asset_id values listed above, or empty.
- Never invent asset IDs that are not in the list above.
- backup_asset_ids MUST also only contain asset_id values from the list above.
- If no asset fits a sentence, set primary_asset_id to "", needs_supplement_asset=true, \
and give a concrete supplement_reason (what visual is missing).
- asset_confidence: 0.0-1.0, honestly reflecting how well the asset matches the sentence.
- Not every asset in the inventory needs to be used.

Respond with JSON ONLY, no markdown code fences, no commentary, matching exactly \
this shape:

{{
  "voiceover_text_full": "...",
  "sentence_items": [
    {{
      "sentence_id": "sentence_001",
      "beat_id": "beat_001",
      "text": "...",
      "visual_intent": "...",
      "primary_asset_id": "...",
      "backup_asset_ids": [],
      "asset_match_reason": "...",
      "asset_confidence": 0.0,
      "estimated_duration_sec": 0.0,
      "must_show": [],
      "avoid_showing": [],
      "needs_supplement_asset": false,
      "supplement_reason": "",
      "source_inventory_asset_ids_considered": []
    }}
  ],
  "transition_from_previous_used": false,
  "callback_to_previous_used": false,
  "contrast_or_commonality_used": false,
  "risks": []
}}
"""


def build_voiceover_review_prompt(
    *,
    project_brief: ProjectBrief,
    style_profile: VoiceoverStyleProfile | None,
    setting: FolderVoiceoverSetting,
    draft: FolderVoiceoverDraft,
) -> str:
    """Baut den Review-Prompt für die weichen (nicht-deterministischen) Kriterien.

    Harte Kriterien (Wortanzahl, Asset-IDs, verbotene Begriffe) werden bereits
    von Python geprüft — dieser Prompt fragt gezielt nur die Kriterien ab, die
    ein LLM beurteilen muss."""
    forbidden_phrases = _combined_forbidden_phrases(project_brief, style_profile, setting)
    forbidden_block = ", ".join(forbidden_phrases) or "(keine)"
    sentences_block = "\n".join(
        f"- [{item.sentence_id}] {item.text}" for item in draft.sentence_items
    ) or "(keine sentence_items)"

    error_types = ", ".join(
        [
            "LANGUAGE_MISMATCH",
            "GLOBAL_NEGATIVE_RULE_VIOLATED",
            "FOLDER_NEGATIVE_RULE_VIOLATED",
            "HALLUCINATED_FACT",
            "TOO_GENERIC",
            "TOO_ASSET_DESCRIPTIVE",
            "DOES_NOT_MATCH_ASSETS",
            "REPETITIVE_PHRASING",
            "STYLE_PROFILE_MISMATCH",
        ]
    )

    return f"""You are a strict documentary script editor reviewing a voice-over \
section for ONE location. Judge ONLY the criteria listed below — word count and \
asset-ID validity are already checked separately by code.

## Target language
{project_brief.language}

## Style Profile (the text should match this style)
{_style_summary_block(style_profile)}

## Negative rules / forbidden phrases (global + folder)
{forbidden_block}

## Factuality mode for this location
{setting.factuality_mode}

## Voice-over text to review
{draft.voiceover_text_full}

## Sentence/beat breakdown
{sentences_block}

## Task
Check ONLY for these error types: {error_types}.

- LANGUAGE_MISMATCH: text is not actually in the target language above.
- GLOBAL_NEGATIVE_RULE_VIOLATED / FOLDER_NEGATIVE_RULE_VIOLATED: violates a rule above.
- HALLUCINATED_FACT: states a specific, checkable fact that is not safely inferable.
- TOO_GENERIC: could apply to almost any location, no specific sensory detail.
- TOO_ASSET_DESCRIPTIVE: reads like "you see X" instead of narration.
- DOES_NOT_MATCH_ASSETS: a sentence's tone/content clearly contradicts its assigned asset.
- REPETITIVE_PHRASING: repeats the same words/structures too often.
- STYLE_PROFILE_MISMATCH: tone/pacing clearly does not match the Style Profile above.

Respond with JSON ONLY, no markdown code fences, no commentary, matching exactly \
this shape (empty list if the text passes all checks):

{{
  "errors": [
    {{
      "type": "TOO_ASSET_DESCRIPTIVE",
      "severity": "WARNING",
      "sentence_id": "sentence_002",
      "message": "...",
      "fix_hint": "..."
    }}
  ]
}}
"""


def build_voiceover_correction_prompt(
    *,
    project_brief: ProjectBrief,
    style_profile: VoiceoverStyleProfile | None,
    setting: FolderVoiceoverSetting,
    draft: FolderVoiceoverDraft,
    errors: list[ValidationError],
) -> str:
    """Baut den Correction-Prompt: identische Ausgabestruktur wie der
    Autor-Prompt, aber mit dem Original-Entwurf + konkreten Fehlern als Input."""
    errors_block = "\n".join(
        f"- [{error.type}] (sentence_id={error.sentence_id or '-'}) {error.message}"
        + (f" Hinweis: {error.fix_hint}" if error.fix_hint else "")
        for error in errors
    ) or "(keine Fehler übergeben)"

    sentence_items_json = "\n".join(
        f"- {item.sentence_id}: text=\"{item.text}\" primary_asset_id={item.primary_asset_id!r} "
        f"needs_supplement_asset={item.needs_supplement_asset}"
        for item in draft.sentence_items
    ) or "(keine sentence_items)"

    return f"""You previously wrote the voice-over section below for ONE location, \
but it has issues that must be fixed. Rewrite it, fixing ONLY the issues listed — \
keep everything else (structure, good sentences, asset assignments) unless the \
issue directly affects them.

## Target language
{project_brief.language}

## Style Profile
{_style_summary_block(style_profile)}

## Target length
target_words: {setting.target_words} (min {setting.min_words}, max {setting.max_words})

## Original voice-over text
{draft.voiceover_text_full}

## Original sentence/beat breakdown
{sentence_items_json}

## Issues that MUST be fixed
{errors_block}

## Task
Produce a corrected, complete replacement. The sentence/beat structure with asset \
assignment must be fully present again (not partial). Do not merely describe the \
assets — write polished documentary narration.

Respond with JSON ONLY, no markdown code fences, no commentary, using the EXACT \
same shape as before:

{{
  "voiceover_text_full": "...",
  "sentence_items": [
    {{
      "sentence_id": "sentence_001",
      "beat_id": "beat_001",
      "text": "...",
      "visual_intent": "...",
      "primary_asset_id": "...",
      "backup_asset_ids": [],
      "asset_match_reason": "...",
      "asset_confidence": 0.0,
      "estimated_duration_sec": 0.0,
      "must_show": [],
      "avoid_showing": [],
      "needs_supplement_asset": false,
      "supplement_reason": "",
      "source_inventory_asset_ids_considered": []
    }}
  ],
  "transition_from_previous_used": false,
  "callback_to_previous_used": false,
  "contrast_or_commonality_used": false,
  "risks": []
}}
"""


def _folder_voiceover_block(
    entry: DramaturgyFolderEntry | None,
    draft: FolderVoiceoverDraft,
    inventory_assets: list[dict],
) -> str:
    role = entry.dramaturgy_role if entry is not None else "-"
    sentence_lines = "\n".join(
        f"    - sentence_id={item.sentence_id} primary_asset_id={item.primary_asset_id or '(none)'}: {item.text}"
        for item in draft.sentence_items
    ) or "    (keine sentence_items)"
    inventory_lines = "\n".join(
        f"    - asset_id: {asset.get('asset_id', '')} | type: {asset.get('media_type', '?')} "
        f"| description: {asset.get('description', '')}"
        for asset in inventory_assets
    ) or "    (kein zusätzliches Inventory verfügbar)"
    return (
        f"[{draft.folder_name}] (dramaturgy_role: {role})\n"
        f"  Full voice-over text:\n  {draft.voiceover_text_full}\n"
        f"  Sentence/beat breakdown:\n{sentence_lines}\n"
        f"  Additional inventory for this location (for supplement options):\n{inventory_lines}"
    )


def build_intro_hook_prompt(
    *,
    project_brief: ProjectBrief,
    style_profile: VoiceoverStyleProfile | None,
    dramaturgy_plan: DramaturgyPlan,
    confirmed_folder_voiceovers: list[FolderVoiceoverDraft],
    settings: IntroHookSettings,
    inventory_by_folder: dict[str, list[dict]] | None = None,
) -> str:
    """Baut den Prompt zur Erzeugung von genau 5 Intro-Hook-Kandidaten.

    Fordert echte Doku-Prosa (kein Assetlisten-Stil, keine reine
    Zusammenfassung) UND eine vollständige visuelle Zuordnung (visual_beats)
    für jeden Kandidaten im selben JSON-Response."""
    inventory_by_folder = inventory_by_folder or {}
    entries_by_folder = {entry.folder_name: entry for entry in dramaturgy_plan.recommended_folder_order}

    tone_tags = ", ".join(project_brief.tone_tags) or "(keine Angabe)"
    active_negative_rules = ", ".join(
        flag for flag, enabled in project_brief.negative_rule_flags.items() if enabled
    ) or "(keine Angabe)"
    forbidden_phrases = list(project_brief.forbidden_phrases)
    if style_profile is not None:
        forbidden_phrases.extend(style_profile.forbidden_phrases)
    forbidden_phrases.extend(settings.forbidden_phrases)
    forbidden_phrases.extend(settings.must_avoid)
    forbidden_block = "\n".join(f"- {phrase}" for phrase in forbidden_phrases) or "(keine)"

    folder_blocks = "\n\n".join(
        _folder_voiceover_block(
            entries_by_folder.get(draft.folder_name), draft, inventory_by_folder.get(draft.folder_name, [])
        )
        for draft in confirmed_folder_voiceovers
    ) or "(keine bestätigten Folder Voice-overs verfügbar)"

    must_include_block = ", ".join(settings.must_include) or "(keine Angabe)"

    return f"""You are a documentary editor writing the OPENING HOOK for a \
multi-location travel/nature documentary. You have all confirmed location \
voice-overs available below.

Do not merely summarize the folder voice-overs. Create a strong documentary \
opening hook.

Do not invent asset IDs. Use only asset IDs present in the provided confirmed \
sentence_items or inventory summaries below.

## Project
- Project title: {project_brief.video_title or "(untitled)"}
- Target language: {settings.language or project_brief.language}
- Desired tone tags: {tone_tags}
- Hook tone (from settings): {settings.tone}
- Active global negative rules: {active_negative_rules}
- Forbidden phrases (global + style + hook settings):
{forbidden_block}
- Core narrative arc: {dramaturgy_plan.narrative_arc or "-"}
- Core promise: {dramaturgy_plan.core_promise or "-"}

## Style Profile (respect it — do not copy any reference text)
{_style_summary_block(style_profile)}

## Hook rules for this project
- allow_questions: {settings.allow_questions}
- allow_strong_claim: {settings.allow_strong_claim}
- allow_direct_place_name: {settings.allow_direct_place_name}
- allow_tease_multiple_places: {settings.allow_tease_multiple_places}
- must include (topics/ideas, not literal phrases): {must_include_block}
- editor's extra instructions: {settings.freeform_rule_for_llm or "(none)"}
- target_words: {settings.target_words} (min {settings.min_words}, max {settings.max_words})

## All confirmed location voice-overs (source material for the hook)
{folder_blocks}

## Task
Analyze all locations above and decide:
- Which location has the STRONGEST hook potential?
- Which CONTRAST between locations works best as an opener?
- What OPEN QUESTION creates suspense?
- Which visual motif works best as an entry point?
- Which COMBINATION of locations creates the strongest opening?
- Which hook best matches the desired documentary style?

Produce EXACTLY 5 hook candidates (exactly 5, no more, no fewer), each a \
distinct strategic approach (e.g. mystery, contrast, surprise, \
cinematic_promise, question, emotional). Each hook must read like real \
documentary prose — never like a list of assets or a plot summary.

For each candidate, also provide visual_beats: a full sentence/beat-to-asset \
breakdown of the hook text itself, exactly like the folder voice-over sentence \
breakdown above. Each visual_beat's primary_asset_id MUST be an asset_id taken \
from the sentence_items or inventory listed above — either reference an \
existing sentence_id from a folder (source_sentence_id) if you reuse that \
moment, or reference a fitting inventory asset_id directly. If nothing fits, \
set primary_asset_id to "", needs_supplement_asset=true, and give a concrete \
supplement_reason.

Respond with JSON ONLY, no markdown code fences, no commentary, matching exactly \
this shape:

{{
  "candidates": [
    {{
      "hook_id": "hook_001",
      "hook_text": "...",
      "hook_type": "mystery|contrast|surprise|cinematic_promise|question|emotional",
      "used_folders": [],
      "used_sentence_ids": [],
      "visual_beats": [
        {{
          "hook_beat_id": "hook_beat_001",
          "text": "...",
          "visual_intent": "...",
          "source_folder_name": "...",
          "source_sentence_id": "",
          "primary_asset_id": "...",
          "backup_asset_ids": [],
          "asset_match_reason": "...",
          "asset_confidence": 0.0,
          "needs_supplement_asset": false,
          "supplement_reason": ""
        }}
      ],
      "hook_potential_score": 0.0,
      "reason": "...",
      "risks": []
    }}
  ]
}}
"""
