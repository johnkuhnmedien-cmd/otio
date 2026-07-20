"""Prompts für freiere Skripterzeugung (without_voiceover_enhanced).

Assets sind visuelle Ressource, nicht Inhaltsgrenze.
"""

from __future__ import annotations

FORBIDDEN_PHRASES = (
    "Das Bild zeigt",
    "Hier sehen wir",
    "Auf diesem Foto ist",
    "In diesem Video erkennt man",
    "The image shows",
    "Here we see",
)


_SHARED_SCRIPT_RULES = """\
GOAL
Write editorial narration focused on:
- history of the place
- origin and development
- geographic peculiarities
- cultural peculiarities
- local details
- surprising facts
- connections and atmosphere
- change over time
- natural phenomena
- interesting contrasts

Existing local assets are a VISUAL RESOURCE only.
They must NOT fully constrain the script topic.
Do NOT invent historical, geographic, or cultural facts.
Use ONLY:
- confirmed Project Brief data
- verified project data / provided research
- reliable existing metadata

Unverified claims must be omitted OR marked fact_check_required=true.
Never invent years, events, names, or superlatives.

ALLOWED:
- atmospheric language
- concrete local details
- history and peculiarities
- dramaturgical transitions
- occasional visually tellable wording (NOT image captions)

BAD: "Das Bild zeigt Berge bei Sonnenuntergang."
GOOD: "Am Abend, wenn die Sonne hinter den Gipfeln verschwindet, beginnen die Felsen beinahe wie Kristalle zu funkeln."

RULES FOR SEGMENTS
- A segment may be a short sentence, several tightly related sentences, or a clause at a natural speech boundary.
- Never split mid-word.
- visual_intents are SEPARATE from spoken text.
- Do NOT assign one asset per sentence.
- Do NOT bind narration to listing available assets.
"""


def _json_schema_block(*, id_prefix: str = "") -> str:
    seg = f"{id_prefix}segment_001" if id_prefix else "segment_001"
    intent = f"{id_prefix}intent_001" if id_prefix else "intent_001"
    beat = f"{id_prefix}beat_001" if id_prefix else "beat_001"
    need = f"{id_prefix}need_001" if id_prefix else "need_001"
    fact = f"{id_prefix}fact_001" if id_prefix else "fact_001"
    return f"""\
OUTPUT (JSON only):
{{
  "narration_full": "... spoken narration for THIS chapter only ...",
  "segments": [
    {{
      "segment_id": "{seg}",
      "text": "...",
      "sequence_index": 1,
      "semantic_function": "atmosphere|history|geography|culture|fact|transition",
      "visual_intent_ids": ["{intent}"],
      "fact_check_required": false,
      "folder_name": "EXACT_FOLDER_NAME"
    }}
  ],
  "visual_beats": [
    {{
      "beat_id": "{beat}",
      "description": "...",
      "related_segment_ids": ["{seg}"],
      "visual_intent_ids": ["{intent}"]
    }}
  ],
  "visual_intents": [
    {{
      "intent_id": "{intent}",
      "description": "...",
      "subject": "...",
      "location": "EXACT_FOLDER_NAME",
      "preferred_media_type": "video|photo",
      "folder_name": "EXACT_FOLDER_NAME"
    }}
  ],
  "coverage_needs": [
    {{
      "need_id": "{need}",
      "visual_intent_id": "{intent}",
      "subject": "...",
      "reason": "...",
      "search_queries": ["..."]
    }}
  ],
  "fact_check_hints": [
    {{
      "hint_id": "{fact}",
      "related_segment_id": "{seg}",
      "claim": "...",
      "status": "fact_check_required",
      "note": "..."
    }}
  ]
}}
"""


def build_enhanced_script_prompt(
    *,
    project_brief_text: str,
    dramaturgy_text: str,
    style_profile_text: str,
    verified_facts_text: str,
    asset_inventory_summary: str,
    language: str = "de",
) -> str:
    """Legacy: gesamtes Film-Skript in einem Call (nicht mehr UI-Standard)."""
    forbidden = "\n".join(f'- "{p}"' for p in FORBIDDEN_PHRASES)
    return f"""\
You are writing documentary narration for a travel/place film.

LANGUAGE: {language}

{_SHARED_SCRIPT_RULES}

STRICTLY AVOID these phrases and patterns:
{forbidden}
- pure inventories of visible objects
- describing every available asset
- mechanical one-sentence-per-image assignment

{_json_schema_block()}

PROJECT BRIEF:
{project_brief_text}

DRAMATURGY:
{dramaturgy_text}

STYLE PROFILE:
{style_profile_text}

VERIFIED FACTS / METADATA (only these may be stated as facts):
{verified_facts_text}

LOCAL ASSET INVENTORY (visual resource, not content limit):
{asset_inventory_summary}
"""


def build_enhanced_script_revision_prompt(
    *,
    editor_instructions: str,
    current_script: str,
    folder_name: str,
    language: str = "de",
) -> str:
    """Minimaler Revisions-Prompt: nur Freitext-Anweisung + bestehendes Skript."""
    instructions = (editor_instructions or "").strip()
    script = (current_script or "").strip()
    return f"""\
Revise the spoken narration for ONE documentary chapter.

LANGUAGE: {language}
CHAPTER / folder_name: {folder_name}

EDITOR INSTRUCTIONS (follow these; they override the current wording where they conflict):
{instructions or "(no instructions provided)"}

CURRENT SCRIPT:
{script or "(empty)"}

Return ONLY the revised spoken narration as plain text.
No JSON, no markdown code fences, no commentary, no bullet lists of notes —
only the narration that should be spoken aloud for this chapter.
"""


def build_enhanced_folder_script_prompt(
    *,
    project_brief_text: str,
    film_context_text: str,
    chapter_dramaturgy_text: str,
    style_profile_text: str,
    verified_facts_text: str,
    asset_inventory_summary: str,
    folder_name: str,
    folder_slug: str,
    dramaturgy_role: str,
    target_words: int,
    min_words: int,
    max_words: int,
    previous_folder_name: str | None,
    next_folder_name: str | None,
    language: str = "de",
) -> str:
    """Ein Dramaturgie-Kapitel / Ordner — analog zur klassischen Folder-VO-Pipeline."""
    forbidden = "\n".join(f'- "{p}"' for p in FORBIDDEN_PHRASES)
    id_prefix = f"{folder_slug}_"
    prev = previous_folder_name or "(none — first enabled chapter)"
    nxt = next_folder_name or "(none — last enabled chapter)"
    return f"""\
You are writing documentary narration for ONE chapter of a multi-location travel film.

LANGUAGE: {language}

{_SHARED_SCRIPT_RULES}

STRICTLY AVOID these phrases and patterns:
{forbidden}
- pure inventories of visible objects
- describing every available asset
- mechanical one-sentence-per-image assignment

THIS CHAPTER ONLY
- folder_name (EXACT): {folder_name}
- dramaturgy_role: {dramaturgy_role}
- previous chapter in the film: {prev}
- next chapter in the film: {nxt}
- target_words: {target_words} (soft target; stay within {min_words}-{max_words})
- Write ONLY the spoken narration for this chapter — not the whole film.
- Every segment/intent MUST set folder_name to exactly "{folder_name}".
- Use ID prefixes starting with "{id_prefix}" (e.g. {id_prefix}segment_001).

{_json_schema_block(id_prefix=id_prefix).replace("EXACT_FOLDER_NAME", folder_name)}

PROJECT BRIEF:
{project_brief_text}

FILM CONTEXT (global arc — do not rewrite other chapters):
{film_context_text}

THIS CHAPTER DRAMATURGY:
{chapter_dramaturgy_text}

STYLE PROFILE:
{style_profile_text}

VERIFIED FACTS / METADATA (only these may be stated as facts):
{verified_facts_text}

LOCAL ASSETS FOR THIS CHAPTER (visual resource, not content limit):
{asset_inventory_summary}
"""


def build_rough_cut_prompt(
    *,
    locked_script_json: str,
    segment_timings_json: str,
    local_assets_json: str,
    style_profile_text: str,
    dramaturgy_text: str,
) -> str:
    return f"""\
You are an editorial planner for a documentary cut.

Create:
1) pause_directives (editorial pauses only — NO milliseconds, NO frames)
2) rough visual edit plan (shots may span multiple segments; multiple shots may sit inside one segment)
3) concrete coverage_gaps for shots without a suitable local asset

FORBIDDEN:
- one sentence = one asset
- one voice segment = one shot
- sentence end = picture cut
- number of shots must equal number of sentences
- final frames / exact timeline times

pause_function: breath|emphasis|anticipation|reveal|chapter_transition|reflection|no_pause
duration_class: short|medium|long
visual_behavior: hold_current_shot|next_shot_may_start_during_pause|cut_at_pause_start|cut_at_pause_end|editorial_choice

OUTPUT JSON:
{{
  "pause_directives": [
    {{
      "after_segment_id": "segment_012",
      "pause_function": "anticipation",
      "duration_class": "medium",
      "visual_behavior": "next_shot_may_start_during_pause",
      "editorial_reason": "..."
    }}
  ],
  "shots": [
    {{
      "shot_id": "shot_007",
      "narration_start_anchor": {{"segment_id": "segment_003", "offset_seconds": 1.2}},
      "narration_end_anchor": {{"segment_id": "segment_005", "offset_seconds": 0.4}},
      "visual_intent_id": "intent_004",
      "asset_id": null,
      "candidate_asset_ids": [],
      "editorial_function": "orientation",
      "editorial_reason": "...",
      "visual_behavior": "hold",
      "may_overlap_pause": true
    }}
  ],
  "coverage_gaps": [
    {{
      "gap_id": "gap_008",
      "related_shot_ids": ["shot_007"],
      "visual_intent_id": "intent_004",
      "subject": "...",
      "location": "...",
      "action": "wide establishing shot",
      "editorial_function": "orientation",
      "preferred_media_type": "video",
      "fallback_media_type": "photo",
      "minimum_resolution": "1920x1080",
      "priority": "high",
      "reason": "...",
      "search_queries": ["...", "..."]
    }}
  ]
}}

LOCKED SCRIPT:
{locked_script_json}

SEGMENT TIMINGS (real measured durations — do not invent):
{segment_timings_json}

LOCAL ASSETS:
{local_assets_json}

STYLE:
{style_profile_text}

DRAMATURGY:
{dramaturgy_text}
"""


def build_final_cut_prompt(
    *,
    locked_script_json: str,
    narration_timeline_json: str,
    pause_directives_json: str,
    rough_cut_json: str,
    local_assets_json: str,
    accepted_supplements_json: str,
    style_profile_text: str,
) -> str:
    return f"""\
Create the FINAL editorial cut plan.

You decide:
- shot order and asset choice (local + accepted supplements only)
- narration ranges via anchors
- continuity, holds across statements, detail shots inside a sentence
- cuts during or outside pauses
- avoid unnecessary repetition

You do NOT decide:
- final frames
- technical source timecodes
- validated source ranges
- final frame rounding

FORBIDDEN one-to-one sentence/asset assignment.

OUTPUT JSON:
{{
  "shots": [
    {{
      "shot_id": "shot_019",
      "narration_start_anchor": {{"segment_id": "segment_009", "offset_seconds": 0.8}},
      "narration_end_anchor": {{"segment_id": "segment_011", "offset_seconds": 1.1}},
      "asset_id": "asset_423",
      "editorial_function": "historical_context",
      "editorial_reason": "...",
      "transition_behavior": "straight_cut",
      "source_range_intent": "representative_middle_section",
      "may_overlap_pause": false
    }}
  ]
}}

LOCKED SCRIPT:
{locked_script_json}

NARRATION TIMELINE:
{narration_timeline_json}

PAUSE DIRECTIVES:
{pause_directives_json}

ROUGH CUT:
{rough_cut_json}

LOCAL ASSETS:
{local_assets_json}

ACCEPTED SUPPLEMENTS ONLY:
{accepted_supplements_json}

STYLE:
{style_profile_text}
"""
