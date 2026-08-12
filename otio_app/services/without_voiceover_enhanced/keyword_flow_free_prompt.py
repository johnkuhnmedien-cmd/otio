"""Keyword Flow Free: short hierarchical unified-cut prompt (isolated from Keyword Flow)."""

from __future__ import annotations

KEYWORD_FLOW_FREE_MARKER = "KEYWORD FLOW FREE MARKER"


def build_keyword_flow_free_prompt(
    *,
    locked_script_json: str,
    segment_timings_json: str,
    local_assets_json: str,
    style_profile_text: str,
    dramaturgy_text: str,
    continuous_word_flow_json: str,
    folder_name: str = "",
    folder_slug: str = "",
    previous_folder_name: str | None = None,
    next_folder_name: str | None = None,
    include_middle_frames: bool = False,
    shot_constraints_text: str = "",
    used_in_ledger_text: str = "",
) -> str:
    """Build the Keyword Flow Free LLM prompt (unified-cut-v1).

    Editorial core stays short and hierarchical. Continuous word flow is the
    primary timing view; sentence_id is only a technical address for Python.
    """
    slug = folder_slug or folder_name or "chapter"
    prev = previous_folder_name or "(none — this is the first chapter)"
    nxt = next_folder_name or "(none — this is the last chapter)"

    chapter_context = ""
    if folder_name:
        chapter_context = f"""\
CHAPTER CONTEXT

- Plan ONLY the chapter "{folder_name}" (id prefix: {slug}_).
- Inputs below contain only this chapter.
- Use only segment_ids / sentence_ids / local_asset_id values from this chapter.
- Prefix every cut_id, slot_id and coverage_gap_id with "{slug}_".
- Previous chapter: {prev}
- Next chapter: {nxt}
- Do NOT plan the Maps folder opener — Python inserts it when applicable.
- Style profile:
{style_profile_text}
- Dramaturgy:
{dramaturgy_text}
"""
    else:
        chapter_context = f"""\
CHAPTER CONTEXT

- Style profile:
{style_profile_text}
- Dramaturgy:
{dramaturgy_text}
"""

    vision_block = ""
    if include_middle_frames:
        vision_block = """
- Optional JPEG stills may follow, labeled IMAGE for local_asset_id=<id>.
  Use them with LOCAL ASSETS metadata; never invent asset IDs.
"""

    ledger_block = ""
    if used_in_ledger_text.strip():
        ledger_block = f"""
USED-IN LEDGER (filmwide so far — respect max usage / reuse distance):
{used_in_ledger_text.strip()}
"""

    constraints = shot_constraints_text.strip() or "(see project shot settings)"

    return f"""\
{KEYWORD_FLOW_FREE_MARKER}

ROLE

You are the visual editor of a documentary.
Plan one continuous visual sequence across the complete narration of this chapter.


EDITORIAL GOAL

Treat narration as a continuous spoken flow, not as a list of sentences.

Sentence IDs exist only so Python can resolve real word timings.
A sentence boundary has no preferred editorial meaning.

A sentence may contain multiple shots.
A shot may continue across multiple sentences.

Do not give every sentence its own visual.


CUT DECISION

First ask: does the visual story need a new shot here?
Only then choose a real word onset from CONTINUOUS WORD FLOW as the cut point.

Create a new shot only when there is a visual or editorial reason:
- an important visible subject or entity appears,
- the visual idea changes,
- a deliberate change of scale or perspective improves the sequence,
- a reveal, contrast or transition benefits from a cut,
- or the current shot would become editorially too long.

Keywords are useful timing anchors, not mandatory cuts.
Keyword ≠ Pflicht-Cut.

Cuts may occur on real word onsets inside a sentence.
A shot may continue into the next sentence until the next meaningful visual anchor.


ASSET DECISION

First determine what the narration should visually communicate.
Then choose the strongest available asset that genuinely supports that intent.

Do not choose an asset from an isolated word match.
Named entities and concrete places must stay correct — never a wrong location
from a similar motif.
Prefer video over photo when a video truly shows the required motif.

If no suitable asset exists for a genuinely necessary visual beat, create an
honest coverage gap.
Do not create a gap merely because a new sentence begins.
weak / none must not be masked with unsuitable material.


TIMING CONTRACT

For mid_sentence cuts, copy sentence_id and offset_seconds verbatim from one
existing CONTINUOUS WORD FLOW entry. Same entry for both fields.

Never estimate, interpolate, invent, or reuse timing numbers from this prompt's
examples or schema.
Do not invent absolute timeline seconds or frames.
Python resolves final timing, shot_min/shot_max repairs, frames and OTIO.
sentence_id is a technical timing address only.


OUTPUT CONTRACT

Return one chronological boundary chain and one visual slot between each pair
of boundaries.

Use only supplied asset IDs.
Keep all IDs valid and unique.
Always return \"pause_directives\": [].
Return strict JSON matching the provided schema (unified-cut-v1).

Allowed editorial shape (structure only — timings come from CONTINUOUS WORD FLOW):
- multiple mid_sentence cuts may share the same sentence_id
- a shot may cross into the next sentence
- a cut may land several words into that next sentence

Example structure (NOT timing values to copy):
  cut_000 → sentence_id from word-flow, position=start
  cut_001 → same sentence_id, mid_sentence, offset copied from an earlier word entry
  cut_002 → same sentence_id, mid_sentence, offset copied from a later word entry
  cut_003 → next sentence_id, mid_sentence, offset copied from a word several entries into that sentence
  cut_00N → last sentence, position=end


{chapter_context}
CONTINUOUS WORD FLOW

Primary timing view. Chronological spoken words with real ElevenLabs onsets.
offset_seconds is relative to the owning sentence_id (technical address only).
For every mid_sentence boundary: copy sentence_id + offset_seconds from one row.
NEVER invent, estimate, interpolate, or take offsets from the schema example.
{continuous_word_flow_json}

LOCKED SCRIPT:
{locked_script_json}

SEGMENT TIMINGS:
{segment_timings_json}
{ledger_block}{vision_block}
LOCAL ASSETS

Slim inventory (description/tags + duration/usable_in_s + motion/framing when present):
{local_assets_json}


SHOT CONSTRAINTS

{constraints}


OUTPUT SCHEMA

Field shapes only. For mid_sentence, replace COPY_FROM_WORD_FLOW_* with values
copied from one real CONTINUOUS WORD FLOW row (never invent numbers).

{{
  "voiceover_preroll_sec": null,
  "voiceover_postroll_sec": null,
  "closing_fallback_asset_id": "existing_asset_id_not_equal_last_slot",
  "closing_fallback_asset_fit": "strong|acceptable",
  "closing_fallback_asset_fit_reason": "why this reserve closer is strong/acceptable",
  "closing_fallback_visual_intent": "same closing intent as primary",
  "pause_directives": [],
  "boundaries": [
    {{
      "cut_id": "{slug}_cut_000",
      "sentence_id": "COPY_FROM_WORD_FLOW_sentence_id",
      "position": "start",
      "offset_seconds": 0,
      "alignment": "sentence_boundary"
    }},
    {{
      "cut_id": "{slug}_cut_001",
      "sentence_id": "COPY_FROM_WORD_FLOW_sentence_id_same_as_cut_000",
      "position": "middle",
      "offset_seconds": "COPY_FROM_WORD_FLOW_offset_seconds",
      "alignment": "mid_sentence"
    }},
    {{
      "cut_id": "{slug}_cut_002",
      "sentence_id": "COPY_FROM_WORD_FLOW_sentence_id_same_as_cut_000",
      "position": "middle",
      "offset_seconds": "COPY_FROM_WORD_FLOW_offset_seconds_later_word_same_sentence",
      "alignment": "mid_sentence"
    }},
    {{
      "cut_id": "{slug}_cut_003",
      "sentence_id": "COPY_FROM_WORD_FLOW_sentence_id_next_sentence",
      "position": "middle",
      "offset_seconds": "COPY_FROM_WORD_FLOW_offset_seconds_several_words_into_next_sentence",
      "alignment": "mid_sentence"
    }},
    {{
      "cut_id": "{slug}_cut_00N",
      "sentence_id": "COPY_FROM_WORD_FLOW_sentence_id_last",
      "position": "end",
      "offset_seconds": null,
      "alignment": "sentence_boundary"
    }}
  ],
  "slots": [
    {{
      "slot_id": "{slug}_slot_001",
      "local_asset_id": "existing_asset_id_or_null",
      "asset_fit": "strong|acceptable|weak|none",
      "asset_fit_reason": "context-first match or why identity is missing",
      "visual_intent": "...",
      "narrative_function": "chapter_open|orientation|context|evidence|atmosphere|transition|contrast|reveal|reflection|chapter_close",
      "coverage_gap_id": "{slug}_gap_001_or_null",
      "source_range_intent": "representative_middle_section",
      "needed_visual": "prose description of the missing visual",
      "search_concepts": [
        "2-4 English stock search phrases",
        "each 2-5 words, no full sentences"
      ],
      "must_include": ["..."],
      "must_avoid": ["..."],
      "desired_motion": "static|pan|tilt|tracking|drone|handheld|zoom|unknown",
      "desired_framing": "close|medium|wide|aerial|pov",
      "preferred_media_type": "video|photo|either",
      "fact_check_required": false,
      "covered_sentence_ids": ["COPY_FROM_WORD_FLOW_sentence_id"]
    }}
  ]
}}
"""


# Compatibility alias used by routing imports.
build_keyword_flow_free_unified_cut_prompt = build_keyword_flow_free_prompt
