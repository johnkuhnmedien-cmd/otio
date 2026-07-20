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
    folder_name: str = "",
    folder_slug: str = "",
    previous_folder_name: str | None = None,
    next_folder_name: str | None = None,
    include_middle_frames: bool = False,
) -> str:
    chapter_scope = ""
    if folder_name:
        slug = folder_slug or folder_name
        prev = previous_folder_name or "(none — this is the first chapter)"
        nxt = next_folder_name or "(none — this is the last chapter)"
        chapter_scope = f"""
CHAPTER SCOPE (CRITICAL):

- Plan ONLY the chapter "{folder_name}" (id prefix: {slug}_).
- The LOCKED SCRIPT / SEGMENT TIMINGS / LOCAL ASSETS below contain only this chapter.
- Use only segment IDs from this chapter.
- Prefix every shot_id and coverage_gap_id with "{slug}_" (e.g. {slug}_shot_001).
- Do not invent shots for previous or next chapters.
- Previous chapter: {prev}
- Next chapter: {nxt}
- If a next chapter exists, prefer pause_function "chapter_transition" after this chapter's last segment when editorially justified.
"""

    vision_rules = ""
    if include_middle_frames:
        vision_rules = """
MIDDLE-FRAME VISION (OPTIONAL INPUT):

- After the text prompt you may receive JPEG stills labeled
  "IMAGE for local_asset_id=<id>".
- Each image is the middle analysis frame of that local asset (or the only
  frame for still photos).
- Use these images together with LOCAL ASSETS metadata/descriptions to choose
  the most suitable local_asset_id for each shot.
- Prefer assets whose visible content matches the shot's visual_intent.
- VISUAL DIVERSITY: consecutive shots should not look nearly identical.
  Avoid back-to-back picks that share the same framing, subject distance,
  color palette and composition unless a deliberate hold/match-cut is justified
  in continuity_notes / asset_fit_reason.
- Do not invent visual details that are not supported by the image or description.
- If no attached image exists for an asset, fall back to its text description.
- Never invent an asset ID that is not listed in LOCAL ASSETS.
"""

    return f"""\
You are LLM 2, the editorial rough-cut planner for a documentary pipeline.

Your task is to create:

1. editorial pause decisions,
2. a rough visual edit plan,
3. concrete coverage gaps where no suitable local asset exists.

The locked narration provides a continuous time carpet.
The visual edit plan cuts freely across that time carpet.
{chapter_scope}{vision_rules}
NON-NEGOTIABLE EDITORIAL RULES:

- A sentence is not a shot.
- A sentence is not an asset.
- A voice segment is not a shot.
- A visual beat is not a sentence.
- A sentence ending is not automatically a picture cut.
- A pause does not automatically require a picture cut.
- A picture cut may happen during narration or during a pause.
- One shot may span multiple narration segments.
- Multiple shots may occur inside one narration segment.
- Do not create one shot per sentence or one shot per segment.
- Prefer meaningful editorial shot spans over unnecessary rapid cutting.

SCRIPT LOCK:

- The locked script is immutable.
- Do not rewrite, shorten, expand, summarize or correct the narration.
- Do not reorder narration segments.
- Do not create new narration text or new narration segments.
- Use only segment IDs that exist in the provided inputs.

TIMING RULES:

- Segment timings are authoritative measured durations.
- Use them only to judge pacing and relative shot length.
- Do not invent or modify audio durations.
- Do not output seconds, milliseconds, timecodes or frames.
- Do not calculate final timeline positions.

Use only these editorial anchor positions:

start | early | middle | late | end

A segment anchor must have this form:

{{
  "type": "segment",
  "segment_id": "segment_001",
  "position": "start|early|middle|late|end"
}}

When a shot boundary is explicitly tied to a pause, a pause anchor may be used:

{{
  "type": "pause",
  "after_segment_id": "segment_001",
  "position": "start|middle|end"
}}

ASSET RULES:

- Use only local_asset_id values that exist in LOCAL ASSETS.
- Never invent an asset ID, file path, URL, provider result or media description.
- Do not infer visual content from a filename or file extension alone.
- Judge an asset from the metadata/analysis in LOCAL ASSETS
  (and from attached middle-frame images when provided).
- Existing local assets are visual resources, not the narrative boundary.
- Do not force an unsuitable local asset into a shot.
- If no suitable local asset exists, set local_asset_id to null.
- Every shot with local_asset_id null must reference exactly one coverage_gap_id.
- A shot with a suitable local asset must have coverage_gap_id null.
- Prefer varied local assets across neighboring shots when equally suitable.

PAUSE RULES:

Allowed pause_function values:

breath | emphasis | anticipation | reveal |
chapter_transition | reflection | no_pause

Allowed duration_class values:

short | medium | long

Allowed visual_behavior values:

hold_current_shot |
next_shot_may_start_during_pause |
cut_at_pause_start |
cut_at_pause_end |
editorial_choice

- Do not output pause durations in seconds or milliseconds.
- Emit a pause directive only when the boundary decision is editorially meaningful.
- Use no_pause only when an important boundary should explicitly remain continuous.
- Every pause directive must include an editorial reason.
- At most one pause directive may exist for the same after_segment_id.

SHOT RULES:

- Shots must be ordered chronologically.
- Shot anchors must not run backwards.
- Do not create overlapping shots.
- A shot may start or end inside a segment.
- A shot may span several segments.
- Multiple shots may use the same local asset when editorially justified.
- Do not change pictures merely because a sentence or segment ends.
- visual_intent describes what the image should communicate.
- It must not fabricate details about an unseen asset.

COVERAGE-GAP RULES:

- Create a coverage gap only for a concrete shot that lacks a suitable local asset.
- Each gap must describe the missing visual need precisely enough for later stock search or media generation.
- Do not select a stock provider.
- Do not invent search results.
- Mark fact_check_required as true when the requested visual depends on uncertain historical, geographical or cultural claims.

RETURN STRICT JSON ONLY.

Do not use Markdown.
Do not add explanations before or after the JSON.
Do not use comments.
Do not use trailing commas.
Use empty arrays when no entries exist.

OUTPUT SCHEMA:

{{
  "pause_directives": [
    {{
      "after_segment_id": "segment_001",
      "pause_function": "breath|emphasis|anticipation|reveal|chapter_transition|reflection|no_pause",
      "duration_class": "short|medium|long",
      "visual_behavior": "hold_current_shot|next_shot_may_start_during_pause|cut_at_pause_start|cut_at_pause_end|editorial_choice",
      "editorial_reason": "Concise editorial explanation."
    }}
  ],
  "shots": [
    {{
      "shot_id": "shot_001",
      "start_anchor": {{
        "type": "segment|pause",
        "segment_id": "segment_001",
        "after_segment_id": null,
        "position": "start|early|middle|late|end"
      }},
      "end_anchor": {{
        "type": "segment|pause",
        "segment_id": "segment_002",
        "after_segment_id": null,
        "position": "start|early|middle|late|end"
      }},
      "narrative_function": "orientation|context|evidence|atmosphere|transition|contrast|reveal|reflection",
      "visual_intent": "What the shot should communicate editorially.",
      "local_asset_id": "existing_asset_id_or_null",
      "asset_fit": "strong|acceptable|none",
      "asset_fit_reason": "Why the asset is or is not suitable.",
      "continuity_notes": "Relevant movement, composition or transition guidance.",
      "coverage_gap_id": "gap_001_or_null"
    }}
  ],
  "coverage_gaps": [
    {{
      "coverage_gap_id": "gap_001",
      "shot_id": "shot_001",
      "needed_visual": "Concrete description of the missing visual.",
      "editorial_purpose": "Why this visual is needed.",
      "preferred_media_type": "photo|video|map|archive|illustration|either",
      "search_concepts": [
        "concise search concept"
      ],
      "must_include": [
        "required visible element"
      ],
      "must_avoid": [
        "misleading or unsuitable element"
      ],
      "fact_check_required": false
    }}
  ]
}}

FINAL VALIDATION BEFORE RETURNING JSON:

- All IDs are unique where required.
- All referenced segment IDs exist.
- All referenced local asset IDs exist.
- Shots are chronological and non-overlapping.
- No seconds, milliseconds, timecodes or frames are present.
- Every shot without a suitable asset has exactly one linked coverage gap.
- No shot is created merely because a sentence or segment ends.
- The locked narration has not been changed.

LOCKED SCRIPT:
{locked_script_json}

SEGMENT TIMINGS:
{segment_timings_json}

LOCAL ASSETS:
{local_assets_json}

STYLE PROFILE:
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
    folder_name: str = "",
    folder_slug: str = "",
    previous_folder_name: str | None = None,
    next_folder_name: str | None = None,
) -> str:
    chapter_scope = ""
    if folder_name:
        slug = folder_slug or folder_name
        prev = previous_folder_name or "(none — this is the first chapter)"
        nxt = next_folder_name or "(none — this is the last chapter)"
        chapter_scope = f"""
CHAPTER SCOPE (CRITICAL):

- Finalize ONLY the chapter "{folder_name}" (id prefix: {slug}_).
- Inputs below are scoped to this chapter (script, timeline slice, rough cut, assets).
- Use only segment IDs from this chapter.
- Prefix every shot_id with "{slug}_".
- Do not create shots for previous or next chapters.
- Previous chapter: {prev}
- Next chapter: {nxt}
- Accepted supplements may be used when they fit this chapter's coverage needs.
"""

    return f"""\
Create the FINAL editorial cut plan.

You decide:
- shot order and asset choice (local + accepted supplements only)
- narration ranges via anchors
- continuity, holds across statements, detail shots inside a sentence
- cuts during or outside pauses
- avoid unnecessary repetition

You do NOT decide (Python finalizes these later):
- final absolute timeline times / frame numbers
- technical source timecodes
- validated source ranges
- final frame rounding
{chapter_scope}
EDITORIAL GUIDANCE: Prefer a varied shot structure. One sentence may map to \
one asset when that is the best cut — but do not default to a rigid \
one-sentence-one-asset grid for the whole film.

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
