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


def build_enhanced_script_prompt(
    *,
    project_brief_text: str,
    dramaturgy_text: str,
    style_profile_text: str,
    verified_facts_text: str,
    asset_inventory_summary: str,
    language: str = "de",
) -> str:
    forbidden = "\n".join(f'- "{p}"' for p in FORBIDDEN_PHRASES)
    return f"""\
You are writing documentary narration for a travel/place film.

LANGUAGE: {language}

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

STRICTLY AVOID these phrases and patterns:
{forbidden}
- pure inventories of visible objects
- describing every available asset
- mechanical one-sentence-per-image assignment

ALLOWED:
- atmospheric language
- concrete local details
- history and peculiarities
- dramaturgical transitions
- occasional visually tellable wording (NOT image captions)

BAD: "Das Bild zeigt Berge bei Sonnenuntergang."
GOOD: "Am Abend, wenn die Sonne hinter den Gipfeln verschwindet, beginnen die Felsen beinahe wie Kristalle zu funkeln."

OUTPUT (JSON only):
{{
  "narration_full": "... spoken narration ...",
  "segments": [
    {{
      "segment_id": "segment_001",
      "text": "...",
      "sequence_index": 1,
      "semantic_function": "atmosphere|history|geography|culture|fact|transition",
      "visual_intent_ids": ["intent_001"],
      "fact_check_required": false
    }}
  ],
  "visual_beats": [
    {{
      "beat_id": "beat_001",
      "description": "...",
      "related_segment_ids": ["segment_001"],
      "visual_intent_ids": ["intent_001"]
    }}
  ],
  "visual_intents": [
    {{
      "intent_id": "intent_001",
      "description": "...",
      "subject": "...",
      "location": "...",
      "preferred_media_type": "video|photo"
    }}
  ],
  "coverage_needs": [
    {{
      "need_id": "need_001",
      "visual_intent_id": "intent_001",
      "subject": "...",
      "reason": "...",
      "search_queries": ["..."]
    }}
  ],
  "fact_check_hints": [
    {{
      "hint_id": "fact_001",
      "related_segment_id": "segment_001",
      "claim": "...",
      "status": "fact_check_required",
      "note": "..."
    }}
  ]
}}

RULES FOR SEGMENTS
- A segment may be a short sentence, several tightly related sentences, or a clause at a natural speech boundary.
- Never split mid-word.
- visual_intents are SEPARATE from spoken text.
- Do NOT assign one asset per sentence.
- Do NOT bind narration to listing available assets.

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
