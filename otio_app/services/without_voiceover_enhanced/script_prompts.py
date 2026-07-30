"""Prompts für freiere Skripterzeugung (without_voiceover_enhanced).

Schritt ④: nur gesprochene Narration — Asset-Zuordnung erfolgt später im Cut Plan.
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

# Quantitative Stilziele (Referenz: WUNDER DEUTSCHLANDS Stichprobe).
# Nur in Prompts, wenn Aufrufer cut_rhythm_targets_text setzt.
DEFAULT_CUT_RHYTHM_TARGETS = """\
CUT RHYTHM TARGETS (BINDING STYLE TARGETS — aim for this distribution):

- Shot length: typically 10–17 seconds of narration time (median around 13.5s).
  Prefer this band unless a deliberate hold or micro-cut is editorially justified.
- Cut placement mix (self-classify each boundary via alignment):
  - ~65% mid_sentence (cut during a spoken sentence, not at its edge)
  - ~25% sentence_boundary (cut at a sentence start/end)
  - ~10% in_pause (cut during an explicit pause)
- Pause style: prefer more frequent pulled pauses — roughly every 4th–6th
  sentence boundary when editorially justified (esp. paragraph ends / reveals).
  duration_class mapping (Python applies seconds later):
  - short: keep original silence (~0.3–0.8s)
  - medium: ~2–3s pulled pause
  - long: ~3–5s pulled pause
  - chapter_transition: ~3–8s after the chapter's last segment
- Shots often continue across pauses (visual_behavior hold_current_shot /
  cut_at_pause_*). Do not invent sentence boundaries missing from SENTENCE TIMINGS.
"""


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

BAD: "Das Bild zeigt Berge bei Sonnenuntergang."
GOOD: "Am Abend, wenn die Sonne hinter den Gipfeln verschwindet, beginnen die Felsen beinahe wie Kristalle zu funkeln."

RULES FOR SEGMENTS
- A segment may be a short sentence, several tightly related sentences, or a clause at a natural speech boundary.
- Never split mid-word.
- Write spoken narration only — no shot lists, asset IDs, or visual editing plans.
"""


def _json_schema_block(*, id_prefix: str = "") -> str:
    seg = f"{id_prefix}segment_001" if id_prefix else "segment_001"
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
      "fact_check_required": false,
      "folder_name": "EXACT_FOLDER_NAME"
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
- image-caption narration ("the picture shows…")

{_json_schema_block()}

PROJECT BRIEF:
{project_brief_text}

DRAMATURGY:
{dramaturgy_text}

STYLE PROFILE:
{style_profile_text}

VERIFIED FACTS / METADATA (only these may be stated as facts):
{verified_facts_text}
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
    folder_name: str,
    folder_slug: str,
    dramaturgy_role: str,
    target_words: int,
    min_words: int,
    max_words: int,
    previous_folder_name: str | None,
    next_folder_name: str | None,
    chapter_order_text: str = "",
    recent_neighbor_excerpts_text: str = "",
    editorial_neighbor_craft_text: str = "",
    language: str = "de",
) -> str:
    """Ein Dramaturgie-Kapitel / Ordner — nur gesprochene Narration (keine Assets)."""
    forbidden = "\n".join(f'- "{p}"' for p in FORBIDDEN_PHRASES)
    id_prefix = f"{folder_slug}_"
    prev = previous_folder_name or "(none — first enabled chapter)"
    nxt = next_folder_name or "(none — last enabled chapter)"
    chapter_order_block = ""
    if (chapter_order_text or "").strip():
        chapter_order_block = f"""
FILM CHAPTER ORDER (headings only — use for orientation; do not narrate the whole list):
{chapter_order_text.strip()}
"""
    neighbor_excerpts_block = ""
    if (recent_neighbor_excerpts_text or "").strip():
        neighbor_excerpts_block = f"""
{recent_neighbor_excerpts_text.strip()}
"""
    editorial_neighbor_block = ""
    if (editorial_neighbor_craft_text or "").strip():
        editorial_neighbor_block = f"""
{editorial_neighbor_craft_text.strip()}
"""
    return f"""\
You are writing documentary narration for ONE chapter of a multi-location travel film.

LANGUAGE: {language}

{_SHARED_SCRIPT_RULES}

STRICTLY AVOID these phrases and patterns:
{forbidden}
- pure inventories of visible objects
- image-caption narration ("the picture shows…")

THIS CHAPTER ONLY
- folder_name (EXACT): {folder_name}
- dramaturgy_role: {dramaturgy_role}
- previous chapter in the film: {prev}
- next chapter in the film: {nxt}
- target_words: {target_words} (soft target; stay within {min_words}-{max_words})
- Write ONLY the spoken narration for this chapter — not the whole film.
- Every segment MUST set folder_name to exactly "{folder_name}".
- Use ID prefixes starting with "{id_prefix}" (e.g. {id_prefix}segment_001).
{chapter_order_block}{neighbor_excerpts_block}{editorial_neighbor_block}
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
    shot_constraints_text: str = "",
    sentence_timings_json: str = "",
    cut_rhythm_targets_text: str = "",
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

    sentence_block = ""
    sentence_anchor_rules = ""
    if sentence_timings_json.strip():
        sentence_block = f"""
SENTENCE TIMINGS (authoritative, relative to each segment's audio):
{sentence_timings_json}
"""
        sentence_anchor_rules = """
SENTENCE ANCHORS (when SENTENCE TIMINGS are provided):

- Prefer sentence anchors for mid-sentence cuts and sentence-boundary cuts.
- A sentence anchor has this form:
  {"type": "sentence", "sentence_id": "Sedona_segment_001__s002", "position": "start|early|middle|late|end"}
- You may still use segment or pause anchors when appropriate.
- Pause inside a segment (between sentences): set pause_directives[].after_sentence_id
  to the sentence_id AFTER which the pause occurs (and after_segment_id to that sentence's segment).
- Every shot MUST set start_cut_alignment to exactly one of:
  mid_sentence | sentence_boundary | in_pause
- Do not invent sentence_ids. Use only IDs from SENTENCE TIMINGS.
"""

    rhythm_block = ""
    if cut_rhythm_targets_text.strip():
        rhythm_block = f"""
{cut_rhythm_targets_text.strip()}
"""

    return f"""\
You are LLM 2, the editorial rough-cut planner for a documentary pipeline.

Your task is to create:

1. editorial pause decisions,
2. a rough visual edit plan,
3. concrete coverage gaps where no suitable local asset exists.

The locked narration provides a continuous time carpet.
The visual edit plan cuts freely across that time carpet.
{chapter_scope}{vision_rules}{rhythm_block}{sentence_anchor_rules}
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
- Still respect the SHOT / ASSET CONSTRAINTS below when judging relative length
  (e.g. avoid planning one shot across a span that is clearly longer than shot_max).
{shot_constraints_text}
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
- Usable video length ≈ duration_seconds - usable_in_s (black/lead-in). Prefer assets
  whose usable length covers the intended shot span.
- Photos/stills: do not plan long static holds as if they were motion clips;
  keep still spans short unless a deliberate still is justified.

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
- For every shot, start_anchor must be chronologically before end_anchor
  (e.g. never start=end with end=middle on the same span).
- Do not create overlapping shots.
- Do not leave uncovered narration spans between consecutive shots (no visual
  holes on the time carpet). Every part of the chapter narration must be
  covered by some shot — either with a local asset or with a shot linked to a
  coverage_gap.
- A shot may start or end inside a segment.
- A shot may span several segments.
- Multiple shots may use the same local asset when editorially justified.
- Do not change pictures merely because a sentence or segment ends.
- visual_intent describes what the image should communicate.
- It must not fabricate details about an unseen asset.
- Never rely on freeze-frame / tpad / video-hold padding. If a motion video is
  too short for the intended span, shorten the shot, pick a longer asset, or
  emit a coverage_gap shot for the uncovered beat.
- Per chapter: include an OPENING shot at the first narration start that covers
  the configured Vorlauf/preroll, and a CLOSING shot at the last narration end
  that continues for the configured Nachlauf/postroll (see SHOT / ASSET CONSTRAINTS).
- Opening asset ≠ next shot; closing asset ≠ previous shot. Max usage and reuse
  distance apply to opening/closing with no exemption.

COVERAGE-GAP RULES:

- Create a coverage gap for every concrete shot that lacks a suitable local asset
  OR whose best local asset is too short for the intended span.
- Do not silently skip a narration beat — that becomes a black gap later.
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
      "after_sentence_id": "segment_001__s002_or_null",
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
        "type": "segment|pause|sentence",
        "segment_id": "segment_001",
        "sentence_id": "segment_001__s002_or_null",
        "after_segment_id": null,
        "position": "start|early|middle|late|end"
      }},
      "end_anchor": {{
        "type": "segment|pause|sentence",
        "segment_id": "segment_002",
        "sentence_id": null,
        "after_segment_id": null,
        "position": "start|early|middle|late|end"
      }},
      "start_cut_alignment": "mid_sentence|sentence_boundary|in_pause",
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
      "covered_sentence_ids": ["segment_001__s002"],
      "desired_motion": "static|pan|tilt|tracking|drone|handheld|zoom|unknown",
      "desired_framing": "close|medium|wide|aerial|pov",
      "fact_check_required": false
    }}
  ]
}}

FINAL VALIDATION BEFORE RETURNING JSON:

- All IDs are unique where required.
- All referenced segment IDs exist.
- All referenced local asset IDs exist.
- Shots are chronological and non-overlapping.
- Consecutive shots abut on the narration carpet — no uncovered spans between them.
- The first shot starts at chapter narration start; the last shot ends at chapter
  narration end (plus opening/closing Vorlauf/Nachlauf intent).
- No seconds, milliseconds, timecodes or frames are present.
- Every shot without a suitable asset has exactly one linked coverage gap.
- No shot assumes video freeze/hold padding.
- No shot is created merely because a sentence or segment ends.
- The locked narration has not been changed.

LOCKED SCRIPT:
{locked_script_json}

SEGMENT TIMINGS:
{segment_timings_json}
{sentence_block}
LOCAL ASSETS:
{local_assets_json}

STYLE PROFILE:
{style_profile_text}

DRAMATURGY:
{dramaturgy_text}
"""


def build_unified_cut_prompt(
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
    shot_constraints_text: str = "",
    sentence_timings_json: str = "",
    cut_rhythm_targets_text: str = "",
    used_in_ledger_text: str = "",
) -> str:
    """Ein LLM-Lauf: Grenzen-Kette + Slots (+ Pausen + Gap-Specs)."""
    chapter_scope = ""
    if folder_name:
        slug = folder_slug or folder_name
        prev = previous_folder_name or "(none — this is the first chapter)"
        nxt = next_folder_name or "(none — this is the last chapter)"
        chapter_scope = f"""
CHAPTER SCOPE (CRITICAL):

- Plan ONLY the chapter "{folder_name}" (id prefix: {slug}_).
- Inputs below contain only this chapter.
- Use only segment_ids / sentence_ids from this chapter.
- Prefix every cut_id, slot_id and coverage_gap_id with "{slug}_"
  (e.g. {slug}_cut_000, {slug}_slot_001, {slug}_gap_001).
- Do not invent material for previous or next chapters.
- Previous chapter: {prev}
- Next chapter: {nxt}
- If a next chapter exists, prefer pause_function "chapter_transition" after
  this chapter's last VO sentence when editorially justified.
"""

    vision_rules = ""
    if include_middle_frames:
        vision_rules = """
MIDDLE-FRAME VISION (OPTIONAL INPUT):

- After the text prompt you may receive JPEG stills labeled
  "IMAGE for local_asset_id=<id>".
- Use images together with LOCAL ASSETS metadata to choose assets.
- Consecutive slots should not look nearly identical unless justified.
- Never invent an asset ID that is not listed in LOCAL ASSETS.
"""

    sentence_block = ""
    if sentence_timings_json.strip():
        sentence_block = f"""
SENTENCE TIMINGS (authoritative, relative to each segment's audio).
Each sentence may include words[] from ElevenLabs character timestamps:
text, start_seconds, end_seconds, offset_seconds (from that sentence start).
Prefer words[].offset_seconds for precise mid_sentence cuts when present.
{sentence_timings_json}
"""

    rhythm_block = ""
    if cut_rhythm_targets_text.strip():
        rhythm_block = f"""
{cut_rhythm_targets_text.strip()}
"""

    ledger_block = ""
    if used_in_ledger_text.strip():
        ledger_block = f"""
USED-IN LEDGER (filmwide so far — respect max usage / reuse distance):
{used_in_ledger_text.strip()}
"""

    return f"""\
You are the UNIFIED cut planner for a documentary pipeline (single LLM pass).

Your task is to create ONE complete chapter plan:

1. editorial pause decisions,
2. a continuous cut-boundary chain across the VO (voice-over) time carpet,
3. one slot between every consecutive pair of boundaries,
4. honest local asset_fit ratings; for weak/none include inline gap specs.

FORMAT PRINCIPLE (CRITICAL):

- Output N slots and exactly N+1 boundaries.
- Boundary i is the end of slot i and the start of slot i+1.
- Gaps/overlaps between slots are impossible by format — do not emit per-shot
  start/end ranges that can drift apart.
- Boundaries cover ONLY the VO window:
  first boundary = VO start (first sentence start),
  last boundary = VO end (last sentence end).
- Vorlauf/Nachlauf are applied later by Python on the first/last slot —
  do NOT invent sentence anchors before s001 or after the last sentence.
{chapter_scope}{vision_rules}{rhythm_block}{ledger_block}
NON-NEGOTIABLE EDITORIAL RULES:

- A sentence is not a slot.
- A sentence is not an asset.
- Prefer meaningful editorial spans over one-sentence-one-asset grids.
- Do not rewrite the locked narration.

TIMING RULES:

- Use SEGMENT TIMINGS + SENTENCE TIMINGS (with words[] when present).
- When words[] exist, prefer word onset offset_seconds over text-proportion
  guesses for mid_sentence cuts.
- Do NOT output absolute timeline seconds, timecodes, or frames.
- Boundaries use sentence_id + position (start|early|middle|late|end)
  and/or a small sentence-relative offset_seconds.
- When both are present, offset_seconds wins.
- Keep mid_sentence cuts ≥ ~0.4s from sentence edges unless alignment is
  sentence_boundary.
{shot_constraints_text}
BOUNDARY RULES:

- Every boundary MUST set alignment to exactly one of:
  mid_sentence | sentence_boundary | in_pause
- Boundaries must be chronologically non-decreasing on the VO carpet.
- First boundary: first chapter sentence at position start (or offset 0).
- Last boundary: last chapter sentence at position end (or end offset).

SLOT / ASSET RULES:

- Every slot MUST get the best available local_asset_id OR null.
- asset_fit must be exactly one of: strong | acceptable | weak | none
- strong/acceptable: coverage_gap_id null; no gap spec required.
- weak: keep best local asset AND set coverage_gap_id + inline gap fields
  (upgrade gap).
- none: local_asset_id null AND coverage_gap_id + inline gap fields
  (required gap).
- Prefer assets whose usable length (duration_seconds - usable_in_s) covers
  the intended span. Never assume freeze/tpad video-hold padding.
- Opening slot (first) and closing slot (last): different assets from their
  immediate neighbor; max usage + reuse distance apply with no exemption.
- narrative_function for first/last may be chapter_open / chapter_close.

PAUSE RULES:

Allowed pause_function:
breath | emphasis | anticipation | reveal |
chapter_transition | reflection | no_pause

Allowed duration_class: short | medium | long

Allowed visual_behavior:
hold_current_shot | next_shot_may_start_during_pause |
cut_at_pause_start | cut_at_pause_end | editorial_choice

- Prefer after_sentence_id for pauses inside a segment.
- Do not output pause durations in seconds.

RETURN STRICT JSON ONLY. No Markdown. No comments. No trailing commas.

OUTPUT SCHEMA:

{{
  "voiceover_preroll_sec": null,
  "voiceover_postroll_sec": null,
  "pause_directives": [
    {{
      "after_segment_id": "segment_001",
      "after_sentence_id": "segment_001__s004_or_null",
      "pause_function": "breath|emphasis|anticipation|reveal|chapter_transition|reflection|no_pause",
      "duration_class": "short|medium|long",
      "visual_behavior": "hold_current_shot|next_shot_may_start_during_pause|cut_at_pause_start|cut_at_pause_end|editorial_choice",
      "editorial_reason": "..."
    }}
  ],
  "boundaries": [
    {{
      "cut_id": "cut_000",
      "sentence_id": "segment_001__s001",
      "position": "start|early|middle|late|end",
      "offset_seconds": null,
      "alignment": "mid_sentence|sentence_boundary|in_pause"
    }}
  ],
  "slots": [
    {{
      "slot_id": "slot_001",
      "local_asset_id": "existing_asset_id_or_null",
      "asset_fit": "strong|acceptable|weak|none",
      "asset_fit_reason": "...",
      "visual_intent": "...",
      "narrative_function": "chapter_open|orientation|context|evidence|atmosphere|transition|contrast|reveal|reflection|chapter_close",
      "coverage_gap_id": "gap_001_or_null",
      "source_range_intent": "representative_middle_section",
      "needed_visual": "prose description of the missing visual (context only)",
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
      "covered_sentence_ids": ["segment_001__s001"]
    }}
  ]
}}

Include voiceover_preroll_sec / voiceover_postroll_sec only when SHOT/ASSET
CONSTRAINTS ask the LLM to decide; otherwise null/omit.

GAP / SEARCH CONCEPT RULES (CRITICAL):

- When coverage_gap_id is set (weak or none), search_concepts is REQUIRED.
- search_concepts = 2–4 stock-search phrases in ENGLISH.
- Each phrase: 2–5 words, keywords only — never prose or full sentences.
- No German, no punctuation-heavy clauses, no "a shot of …" sentence stems.
- needed_visual stays free-form prose context; search_concepts are the queries.

FINAL VALIDATION BEFORE RETURNING JSON:

- len(slots) == len(boundaries) - 1
- All cut_id / slot_id / coverage_gap_id unique where present
- All sentence_ids exist in SENTENCE TIMINGS
- All local_asset_id values exist in LOCAL ASSETS (or null)
- Boundaries chronological; first=VO start; last=VO end
- weak/none slots have coverage_gap_id + needed_visual + search_concepts
  (2–4 English keyword phrases, 2–5 words each — not prose)
- strong/acceptable slots have coverage_gap_id null
- No absolute timeline seconds / frames
- No video-hold assumptions

LOCKED SCRIPT:
{locked_script_json}

SEGMENT TIMINGS:
{segment_timings_json}
{sentence_block}
LOCAL ASSETS (slim; use duration_seconds / usable_in_s / motion / framing / people):
{local_assets_json}

STYLE PROFILE:
{style_profile_text}

DRAMATURGY:
{dramaturgy_text}
"""


def build_keyword_sync_unified_cut_prompt(
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
    shot_constraints_text: str = "",
    sentence_timings_json: str = "",
    used_in_ledger_text: str = "",
) -> str:
    """Kapitel-Unified-Cut: Wort↔Bild-Sync mit Word-Timestamps + Cut Settings.

    Separater Modus zum Rhythmus-Prompt: Buzzwords/konkrete Motive sollen
    mit passendem Asset und möglichst exaktem Keyword-Onset geschnitten
    werden — analog zur Intro KEYWORD SYNC-Logik, aber mit Kapitel-Inventar
    und normalem asset_fit (strong|acceptable|weak|none).
    """
    chapter_scope = ""
    if folder_name:
        slug = folder_slug or folder_name
        prev = previous_folder_name or "(none — this is the first chapter)"
        nxt = next_folder_name or "(none — this is the last chapter)"
        chapter_scope = f"""
CHAPTER SCOPE (CRITICAL):

- Plan ONLY the chapter "{folder_name}" (id prefix: {slug}_).
- Inputs below contain only this chapter.
- Use only segment_ids / sentence_ids from this chapter.
- Prefix every cut_id, slot_id and coverage_gap_id with "{slug}_"
  (e.g. {slug}_cut_000, {slug}_slot_001, {slug}_gap_001).
- Do not invent material for previous or next chapters.
- Previous chapter: {prev}
- Next chapter: {nxt}
- If a next chapter exists, prefer pause_function "chapter_transition" after
  this chapter's last VO sentence when editorially justified.
"""

    vision_rules = ""
    if include_middle_frames:
        vision_rules = """
MIDDLE-FRAME VISION (OPTIONAL INPUT):

- After the text prompt you may receive JPEG stills labeled
  "IMAGE for local_asset_id=<id>".
- Use images together with LOCAL ASSETS metadata to choose assets.
- Consecutive slots should not look nearly identical unless justified.
- Never invent an asset ID that is not listed in LOCAL ASSETS.
"""

    sentence_block = ""
    if sentence_timings_json.strip():
        sentence_block = f"""
SENTENCE TIMINGS (authoritative; from ElevenLabs character timestamps).
Times are relative to each segment's audio. Each sentence may include
words[] with text / start_seconds / end_seconds / offset_seconds
(offset_seconds = seconds from that sentence's start_seconds).
Prefer words[].offset_seconds for keyword onset cuts when present.
{sentence_timings_json}
"""

    ledger_block = ""
    if used_in_ledger_text.strip():
        ledger_block = f"""
USED-IN LEDGER (filmwide so far — respect max usage / reuse distance):
{used_in_ledger_text.strip()}
"""

    slug = folder_slug or folder_name or "chapter"

    return f"""\
You are the KEYWORD-SYNC cut planner for a documentary pipeline (unified format).

Your task is to create ONE complete chapter plan:

1. editorial pause decisions,
2. a continuous cut-boundary chain across the VO (voice-over) time carpet,
3. one slot between every consecutive pair of boundaries,
4. honest local asset_fit ratings; for weak/none include inline gap specs.

MODE RULES (CRITICAL — differ from rhythm chapter cuts):

- Prefer precise word↔picture matches over long atmospheric holds when the VO
  names a concrete visual subject.
- Obey SHOT / ASSET CONSTRAINTS below (incl. shot_min / shot_max) — Python
  enforces them too. Prefer a keyword-true cut inside the allowed band; if a
  precise onset would violate shot_min, keep the onset and extend the shot
  forward until the next justified cut or VO end rather than showing the
  subject early.
{chapter_scope}{vision_rules}{ledger_block}{shot_constraints_text}
KEYWORD / BUZZWORD SYNC (CRITICAL — do not show subjects early):

- When the VO names a concrete visual subject — place, landmark, animal,
  object, action, or list item (e.g. \"waterfall\", \"bridge\", \"eagle\",
  \"Antelope Canyon\") — choose an asset that actually shows that subject
  and start that picture at the spoken keyword onset.
- Bad: show a waterfall while the VO is still on a previous topic / still
  leading into the word \"waterfall\".
- Good: cut to the waterfall asset exactly as \"waterfall\" (or the local
  language equivalent in the locked script) begins.
- Lists / enumerations: one short picture cut per list item, each starting
  at that item's keyword onset.
- Do NOT pre-roll the next keyword picture during filler / connective speech
  before its keyword.
- Between keyword moments, prefer the best matching local asset for the
  current spoken content; do not invent absolute seconds.
- After the LAST keyword cut in a span: that picture may continue through
  remaining connective VO until the next keyword boundary or the true VO end.
- Prefer WORD TIMINGS: for keyword cuts, set alignment \"mid_sentence\" and
  offset_seconds from words[].offset_seconds of the spoken keyword (or the
  first word of a multi-word place name). Fall back to text proportion only
  when words[] is missing.
- Example (interior keyword cut — NOT the final boundary):
  words[] contains {{\"text\":\"waterfall\",\"offset_seconds\":1.2}} →
  boundary at that sentence_id with offset_seconds=1.2, position=\"middle\",
  alignment=\"mid_sentence\", and the following slot's asset shows a waterfall.

FORMAT PRINCIPLE (CRITICAL):

- Output N slots and exactly N+1 boundaries.
- Boundary i is the end of slot i and the start of slot i+1.
- Gaps/overlaps between slots are impossible by format — do not emit per-shot
  start/end ranges that can drift apart.
- Boundaries cover ONLY the VO window:
  first boundary = VO start (first sentence start),
  last boundary = VO end (last sentence end).
- Vorlauf/Nachlauf are applied later by Python on the first/last slot —
  do NOT invent sentence anchors before s001 or after the last sentence.
- CRITICAL: preroll/postroll do NOT fill gaps inside the VO. Your boundaries
  must already cover the full VO carpet (start→end).

NON-NEGOTIABLE EDITORIAL RULES:

- A sentence is not a slot.
- A sentence is not an asset.
- Prefer keyword-true spans over one-sentence-one-asset grids when a sentence
  contains multiple concrete visual nouns.
- Do not rewrite the locked narration.

TIMING / BOUNDARY RULES:

- Use SEGMENT TIMINGS + SENTENCE TIMINGS (with words[] when present).
- Prefer words[].offset_seconds for keyword onset; fall back to text
  proportion only when words[] is missing for that sentence.
- Do NOT output absolute timeline seconds, timecodes, or frames.
- TWO DIFFERENT FIELDS — do not mix them:
  - position = ONLY start|early|middle|late|end (coarse place in the sentence)
  - alignment = ONLY mid_sentence|sentence_boundary|in_pause (cut type)
  - NEVER put mid_sentence / sentence_boundary / in_pause into position.
- Boundaries use sentence_id + position and/or offset_seconds (seconds from
  that sentence start). When both are present, offset_seconds wins.
- For keyword cuts (interior boundaries): set offset_seconds explicitly AND
  alignment=\"mid_sentence\". Example:
  {{\"sentence_id\":\"…\",\"position\":\"middle\",\"offset_seconds\":1.2,
  \"alignment\":\"mid_sentence\"}}
- First boundary: first chapter sentence, position \"start\", offset_seconds 0
  or null, alignment \"sentence_boundary\".
- Last boundary: last chapter sentence, position \"end\", offset_seconds null
  (or end-of-sentence), alignment \"sentence_boundary\". Never end the plan
  on a mid_sentence keyword offset.
- Keep mid_sentence cuts ≥ ~0.4s from sentence edges unless alignment is
  sentence_boundary.
- Boundaries must be chronologically non-decreasing on the VO carpet.

SLOT / ASSET RULES:

- Every slot MUST get the best available local_asset_id OR null.
- For keyword slots: the asset MUST depict the spoken subject when a matching
  local asset exists. If none matches, use asset_fit \"none\" (or \"weak\" with
  best near-miss) and write a gap — do not pretend a wrong subject is strong.
- asset_fit must be exactly one of: strong | acceptable | weak | none
- strong/acceptable: coverage_gap_id null; no gap spec required.
- weak: keep best local asset AND set coverage_gap_id + inline gap fields
  (upgrade gap).
- none: local_asset_id null AND coverage_gap_id + inline gap fields
  (required gap).
- Prefer assets whose usable length covers the intended span when possible.
  Never assume freeze/tpad video-hold padding. Obey shot_min/shot_max from
  SHOT / ASSET CONSTRAINTS.
- Opening slot (first) and closing slot (last): different assets from their
  immediate neighbor when both assigned; max usage + reuse distance apply.
- narrative_function for first/last may be chapter_open / chapter_close.
- The last slot must span from its start boundary through the full remaining
  VO to the last boundary (VO end).

PAUSE RULES:

Allowed pause_function:
breath | emphasis | anticipation | reveal |
chapter_transition | reflection | no_pause

Allowed duration_class: short | medium | long

Allowed visual_behavior:
hold_current_shot | next_shot_may_start_during_pause |
cut_at_pause_start | cut_at_pause_end | editorial_choice

- Prefer after_sentence_id for pauses inside a segment.
- Do not output pause durations in seconds.

RETURN STRICT JSON ONLY. No Markdown. No comments. No trailing commas.

OUTPUT SCHEMA:

{{
  "voiceover_preroll_sec": null,
  "voiceover_postroll_sec": null,
  "pause_directives": [
    {{
      "after_segment_id": "segment_001",
      "after_sentence_id": "segment_001__s004_or_null",
      "pause_function": "breath|emphasis|anticipation|reveal|chapter_transition|reflection|no_pause",
      "duration_class": "short|medium|long",
      "visual_behavior": "hold_current_shot|next_shot_may_start_during_pause|cut_at_pause_start|cut_at_pause_end|editorial_choice",
      "editorial_reason": "..."
    }}
  ],
  "boundaries": [
    {{
      "cut_id": "{slug}_cut_000",
      "sentence_id": "segment_001__s001",
      "position": "start",
      "offset_seconds": 0,
      "alignment": "sentence_boundary"
    }},
    {{
      "cut_id": "{slug}_cut_001",
      "sentence_id": "segment_001__s002",
      "position": "middle",
      "offset_seconds": 1.2,
      "alignment": "mid_sentence"
    }},
    {{
      "cut_id": "{slug}_cut_00N",
      "sentence_id": "segment_00N__s00N",
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
      "asset_fit_reason": "Why this asset matches the spoken keyword/subject — or why not.",
      "visual_intent": "...",
      "narrative_function": "chapter_open|orientation|context|evidence|atmosphere|transition|contrast|reveal|reflection|chapter_close",
      "coverage_gap_id": "{slug}_gap_001_or_null",
      "source_range_intent": "representative_middle_section",
      "needed_visual": "prose description of the missing visual (context only)",
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
      "covered_sentence_ids": ["segment_001__s001"]
    }}
  ]
}}

Omit voiceover_preroll_sec / voiceover_postroll_sec (or set null) — Python
applies chapter envelope settings later. Do not invent holds inside the VO.

GAP / SEARCH CONCEPT RULES (CRITICAL):

- When coverage_gap_id is set (weak or none), search_concepts is REQUIRED.
- search_concepts = 2–4 stock-search phrases in ENGLISH.
- Each phrase: 2–5 words, keywords only — never prose or full sentences.
- Prefer phrases that name the missing spoken subject (e.g. \"waterfall mist\",
  \"stone arch bridge\").
- needed_visual stays free-form prose context; search_concepts are the queries.

FINAL VALIDATION BEFORE RETURNING JSON:

- len(slots) == len(boundaries) - 1
- All cut_id / slot_id / coverage_gap_id unique where present
- All sentence_ids exist in SENTENCE TIMINGS
- All local_asset_id values exist in LOCAL ASSETS (or null)
- Boundaries chronological; first=VO start; last=VO end
- Last boundary is sentence end (not a keyword mid_sentence)
- Keyword picture cuts use mid_sentence + explicit offset_seconds at onset
- Prefer words[].offset_seconds when words[] is present
- No keyword picture starts before its spoken keyword
- weak/none slots have coverage_gap_id + needed_visual + search_concepts
- strong/acceptable slots have coverage_gap_id null
- No absolute timeline seconds / frames
- No video-hold assumptions
- Respect shot_min / shot_max from SHOT / ASSET CONSTRAINTS

LOCKED SCRIPT:
{locked_script_json}

SEGMENT TIMINGS:
{segment_timings_json}
{sentence_block}
LOCAL ASSETS (slim; use description / duration_seconds / usable_in_s / motion / framing / people):
{local_assets_json}

STYLE PROFILE:
{style_profile_text}

DRAMATURGY:
{dramaturgy_text}
"""


def build_intro_unified_cut_prompt(
    *,
    locked_script_json: str,
    segment_timings_json: str,
    bundled_inventory_json: str,
    style_profile_text: str,
    dramaturgy_text: str,
    folder_name: str = "Intro",
    folder_slug: str = "Intro",
    sentence_timings_json: str = "",
    intro_audio_duration_seconds: float = 0.0,
) -> str:
    """Unified-Schema, aber Intro-Sonderregeln (strong-only, bundeltes Inventar)."""
    slug = folder_slug or folder_name or "Intro"
    duration = max(0.1, float(intro_audio_duration_seconds or 0.0))
    sentence_block = ""
    if sentence_timings_json.strip():
        sentence_block = f"""
SENTENCE TIMINGS (authoritative; from ElevenLabs character timestamps,
relative to each segment's audio). Each sentence may include words[] with
text / start_seconds / end_seconds / offset_seconds (offset_seconds =
seconds from that sentence's start_seconds). Prefer words[].offset_seconds
for keyword/list onset cuts when present.
{sentence_timings_json}
"""
    return f"""\
You are the INTRO cut planner (unified format) for a documentary pipeline.

Plan ONLY the Intro chapter "{folder_name}" (id prefix: {slug}_).
Do not plan body chapters.

INTRO-SPECIFIC RULES (CRITICAL — differ from chapter cuts):

- You receive ALL chapter inventories in ONE bundled JSON (by folder).
- Prefer precise, tight cuts. Shots around ~1 second are allowed and desirable.
- Chapter Cut-Plan shot_min/shot_max (e.g. 5–8s) are NOT enforced by Python on
  Intro slots; only technical floor/ceiling (~0.4s / ~120s) apply. Cut freely
  for lists / reveals, and keep the last picture through remaining VO.
- Every assigned local asset MUST have asset_fit \"strong\".
- Never use asset_fit \"acceptable\" or \"weak\". If the best local asset is only
  acceptable/weak, set local_asset_id to null, asset_fit \"none\", and create a
  coverage_gap (inline gap fields + search_concepts).
- Opening: Python will hold the first slot for 4.0s BEFORE Intro VO starts.
  Set voiceover_preroll_sec to 4.0.
- Closing: Python will hold the last slot for 5–8s AFTER Intro VO ends.
  Set voiceover_postroll_sec between 5 and 8 (prefer ~6.5 unless justified).
- CRITICAL: preroll/postroll do NOT fill gaps inside the VO. Your boundaries
  must already cover the full VO carpet (start→end). Python only adds hold
  before first VO and after last VO — it will NOT extend a last shot that
  ends early during narration.
- Opening and closing slots must use different local_asset_id values when both
  have strong assets.
- Prefix every cut_id, slot_id and coverage_gap_id with \"{slug}_\".
- Use only segment_ids / sentence_ids from the Intro inputs.
- Use only local_asset_id values that exist in BUNDLED INVENTORY.

KEYWORD / ENUMERATION SYNC (CRITICAL — do not show places early):

- When the VO names a concrete place, landmark, chapter topic, or list item,
  the picture for that item MUST start at the spoken keyword onset — not
  earlier in the same sentence or list.
- Bad: show Antelope Canyon while the VO is still on a previous place / still
  leading into the list.
- Good: cut to Antelope Canyon exactly as \"Antelope\" / the place keyword begins.
- Lists / enumerations in the hook: one short picture cut per list item, each
  starting at that item's keyword onset.
- Do NOT pre-roll list-item pictures during filler / connective speech before
  their keyword.
- After the LAST list/keyword cut: that closing picture continues through any
  remaining VO (outro line, tag, breath) until the true VO end. Do NOT place
  the final boundary at the last keyword — place it at the last sentence end.
- Opening hold is separate: slot 1 may begin 4.0s before VO (Python preroll).
  From VO start onward, keyword-onset sync applies to every place/list cut.
- Prefer WORD TIMINGS: for keyword/list cuts, set alignment \"mid_sentence\"
  and offset_seconds from words[].offset_seconds of the spoken keyword (or
  the first word of a multi-word place name). Fall back to text proportion
  only when words[] is missing for that sentence.
- Example (mid-list cut only — NOT the final boundary):
  words[] contains {{\"text\":\"Antelope\",\"offset_seconds\":1.4}} →
  boundary at that sentence_id with offset_seconds=1.4, position=\"middle\",
  alignment=\"mid_sentence\".

INTRO VO duration (measured): {duration:.3f}s.

FORMAT (same as unified chapter plans):

- Output N slots and exactly N+1 boundaries.
- Boundary i is the end of slot i and the start of slot i+1.
- Boundaries cover ONLY the VO window:
  first boundary = VO start (first Intro sentence, position start / offset 0),
  last boundary = VO end (last Intro sentence, position end — no mid-keyword
  offset on the final boundary).
- Do NOT invent absolute timeline seconds/frames for body timing.

TIMING / BOUNDARY RULES:

- Use SEGMENT TIMINGS + SENTENCE TIMINGS (with words[] when present).
- Prefer words[].offset_seconds for keyword/list onset; fall back to text
  proportion only when words[] is missing for that sentence.
- TWO DIFFERENT FIELDS — do not mix them:
  - position = ONLY start|early|middle|late|end (coarse place in the sentence)
  - alignment = ONLY mid_sentence|sentence_boundary|in_pause (cut type)
  - NEVER put mid_sentence / sentence_boundary / in_pause into position.
- Boundaries use sentence_id + position and/or offset_seconds (seconds from
  that sentence start). When both are present, offset_seconds wins.
- For keyword/list cuts (interior boundaries only): set offset_seconds
  explicitly AND alignment=\"mid_sentence\". Example interior cut:
  {{\"sentence_id\":\"…\",\"position\":\"middle\",\"offset_seconds\":1.4,
  \"alignment\":\"mid_sentence\"}}
- First boundary: first Intro sentence, position \"start\", offset_seconds 0
  or null, alignment \"sentence_boundary\".
- Last boundary: last Intro sentence, position \"end\", offset_seconds null
  (or end-of-sentence), alignment \"sentence_boundary\". Never end the plan
  on a mid_sentence keyword offset.
- Keep mid_sentence cuts ≥ ~0.4s from sentence edges unless alignment is
  sentence_boundary.
- Boundaries must be chronologically non-decreasing on the VO carpet.

SLOT / ASSET RULES:

- asset_fit must be exactly: strong | none
- strong: coverage_gap_id null
- none: local_asset_id null AND coverage_gap_id + needed_visual + search_concepts
- narrative_function for first/last may be chapter_open / chapter_close
- The last slot is the closing picture: it must span from its start boundary
  through the full remaining VO to the last boundary (VO end).

RETURN STRICT JSON ONLY. No Markdown. No comments. No trailing commas.

OUTPUT SCHEMA:

{{
  "voiceover_preroll_sec": 4.0,
  "voiceover_postroll_sec": 6.5,
  "pause_directives": [],
  "boundaries": [
    {{
      "cut_id": "{slug}_cut_000",
      "sentence_id": "Intro_segment_001__s001",
      "position": "start",
      "offset_seconds": 0,
      "alignment": "sentence_boundary"
    }},
    {{
      "cut_id": "{slug}_cut_001",
      "sentence_id": "Intro_segment_001__s001",
      "position": "middle",
      "offset_seconds": 1.4,
      "alignment": "mid_sentence"
    }},
    {{
      "cut_id": "{slug}_cut_00N",
      "sentence_id": "Intro_segment_001__s00N",
      "position": "end",
      "offset_seconds": null,
      "alignment": "sentence_boundary"
    }}
  ],
  "slots": [
    {{
      "slot_id": "{slug}_slot_001",
      "local_asset_id": "existing_asset_id_or_null",
      "asset_fit": "strong|none",
      "asset_fit_reason": "Why strong — or why no strong asset exists.",
      "visual_intent": "...",
      "narrative_function": "chapter_open|orientation|context|evidence|atmosphere|transition|contrast|reveal|reflection|chapter_close",
      "coverage_gap_id": "{slug}_gap_001_or_null",
      "source_range_intent": "representative_middle_section",
      "needed_visual": "prose description of the missing visual",
      "search_concepts": ["2-4 English stock search phrases"],
      "must_include": ["..."],
      "must_avoid": ["..."],
      "desired_motion": "static|pan|tilt|tracking|drone|handheld|zoom|unknown",
      "desired_framing": "close|medium|wide|aerial|pov",
      "preferred_media_type": "video|photo|either",
      "fact_check_required": false,
      "covered_sentence_ids": ["Intro_segment_001__s001"]
    }}
  ]
}}

GAP / SEARCH CONCEPT RULES:

- When coverage_gap_id is set, search_concepts is REQUIRED (2–4 English
  keyword phrases, 2–5 words each).

FINAL VALIDATION:

- len(slots) == len(boundaries) - 1
- Boundaries chronological; first = VO start; last = VO end
- First boundary: first Intro sentence, position start (offset 0/null)
- Last boundary: last Intro sentence, position end (not a keyword mid_sentence)
- Last slot covers through remaining VO after the last keyword cut
- asset_fit is only strong or none
- Opening/closing assets differ when both assigned
- All local_asset_id values exist in BUNDLED INVENTORY (or null)
- Keyword/list picture cuts use mid_sentence + words[].offset_seconds when
  words[] is present (else text-proportion offset_seconds)
- No place/list picture starts before its spoken keyword (except slot-1 preroll)
- Preroll/postroll are outside the VO window — do not leave narration uncovered

LOCKED SCRIPT (Intro only):
{locked_script_json}

SEGMENT TIMINGS:
{segment_timings_json}
{sentence_block}
BUNDLED INVENTORY (all chapters, one JSON):
{bundled_inventory_json}

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
    shot_constraints_text: str = "",
    sentence_alignment_json: str = "",
    cut_rhythm_targets_text: str = "",
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

    sentence_block = ""
    sentence_rules = ""
    if sentence_alignment_json.strip():
        sentence_block = f"""
SENTENCE TIMINGS (authoritative, relative to each segment's audio):
{sentence_alignment_json}
"""
        sentence_rules = """
SENTENCE ANCHOR RULES:

- Narration anchors may include optional sentence_id.
- When sentence_id is set, offset_seconds is RELATIVE TO THAT SENTENCE START
  (not the segment start).
- Keep cuts ≥ 0.4s away from sentence edges unless start_cut_alignment is
  sentence_boundary.
- Every shot MUST set start_cut_alignment:
  mid_sentence | sentence_boundary | in_pause
- Do not invent sentence_ids.
"""

    rhythm_block = ""
    if cut_rhythm_targets_text.strip():
        rhythm_block = f"""
{cut_rhythm_targets_text.strip()}
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
{chapter_scope}{shot_constraints_text}{rhythm_block}{sentence_rules}
Use narration_timeline start/end seconds together with asset duration_seconds
(and usable_in_s when present) to keep each shot within shot_min/shot_max and
within the chosen asset's usable length.
If an asset is too short, pick another asset or shorten the narration span.

EDITORIAL GUIDANCE: Prefer a varied shot structure. One sentence may map to \
one asset when that is the best cut — but do not default to a rigid \
one-sentence-one-asset grid for the whole film.

RETURN STRICT JSON ONLY.
Do not use Markdown.
Do not add explanations before or after the JSON.

OUTPUT JSON:
{{
  "voiceover_preroll_sec": 1.0,
  "voiceover_postroll_sec": 3.0,
  "shots": [
    {{
      "shot_id": "shot_019",
      "narration_start_anchor": {{
        "segment_id": "segment_009",
        "sentence_id": "segment_009__s002_or_null",
        "offset_seconds": 0.8
      }},
      "narration_end_anchor": {{
        "segment_id": "segment_011",
        "sentence_id": null,
        "offset_seconds": 1.1
      }},
      "start_cut_alignment": "mid_sentence|sentence_boundary|in_pause",
      "asset_id": "asset_423",
      "editorial_function": "historical_context",
      "editorial_reason": "...",
      "transition_behavior": "straight_cut",
      "source_range_intent": "representative_middle_section",
      "may_overlap_pause": false
    }}
  ]
}}

Include voiceover_preroll_sec / voiceover_postroll_sec when the project settings
ask the LLM to decide (see SHOT / ASSET CONSTRAINTS). Otherwise omit them or
mirror the fixed setting values. Always keep Intro coverage complete.

Per chapter you MUST include:
- an OPENING SHOT: dedicated first shot that runs for the configured
  Vorlauf/preroll seconds at chapter start (picture before/into the VO) and
  covers the first narration start;
- a CLOSING SHOT: dedicated last shot that covers the last narration end and
  continues for the configured Nachlauf/postroll seconds after the VO.
Opening asset_id must differ from the next shot; closing asset_id must differ
from the previous shot (and from the next chapter's opening). Max asset usage
and reuse distance apply to these shots with no exemption.
Do not leave leading or trailing narration seconds without a planned shot.
Do not skip chapters that have narration.

FINAL VALIDATION BEFORE RETURNING JSON:
- All shot_ids unique; all segment_ids / sentence_ids exist in inputs.
- Offsets are non-negative; sentence-relative offsets stay within that sentence.
- start_cut_alignment set on every shot.
- Shots chronological and non-overlapping on the narration carpet.
- No uncovered narration spans between consecutive shots.
- Asset usable length covers each shot (duration_seconds - usable_in_s).
  Never assume freeze/tpad video-hold padding for short motion video.
- Every chapter has opening coverage at narration start and closing coverage at
  narration end, including the configured preroll/postroll intent.
- No two consecutive shots share the same non-intro asset_id.

LOCKED SCRIPT:
{locked_script_json}

NARRATION TIMELINE:
{narration_timeline_json}
{sentence_block}
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
