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
  - ~70% mid_sentence (cut during a spoken sentence, not at its edge)
  - ~30% sentence_boundary (cut at a sentence start/end)
- Do NOT emit pause_directives — pulled pauses are disabled. Prefer
  mid_sentence / sentence_boundary only (alignment \"in_pause\" is unused).
- Do not invent sentence boundaries missing from SENTENCE TIMINGS.
"""

# Binding for body-chapter unified cuts (rhythm / keyword_sync / keyword_flow).
VIDEO_OVER_PHOTO_ASSET_RULES = """\
VIDEO OVER PHOTO (BINDING — OUTRANKS NAME-TAGGED STILLS):

- Prefer chapter-local VIDEO over PHOTO/still whenever a video clearly shows the
  spoken subject or required motif (sheep, deserted village / abandoned cottages,
  bay/beach, cliffs, Atlantic Drive coastal road, etc.).
- Do NOT prefer a PHOTO just because its description contains the English proper
  name (Deserted Village, Slievemore, Keem Bay, Atlantic Drive) while a local
  VIDEO shows the same motif in German or without that exact English label.
  Example: video \"Ruinen eines verlassenen Dorfes\" / stone cottages beats a
  still titled \"Deserted Village at Slievemore\".
- Manual/stock stills (ids often starting with manual_) are LAST RESORT for body
  slots — use them only when no suitable local video shows the motif.
- Do NOT open a coverage gap when a local video already shows the subject.
- Set preferred_media_type to \"video\" for body slots unless no suitable video
  exists. Photos are not equals of videos.
- Closing fallback may still use a long photo/atmosphere asset when no suitable
  closing video remains.
"""

# Shared decision hierarchy for all cut planners that consume local inventories.
CUT_ASSET_SELECTION_PROMPT_VERSION = "cut-asset-selection-v2"
CUT_ASSET_SELECTION_GUIDANCE = """\
ASSET SELECTION FROM SLIM INVENTORY (BINDING — cut-asset-selection-v2):

Decision priority (highest first):
1. Content match to the spoken meaning of the current passage
2. Exact entity / person / place / object when the text requires identity
3. Usable length and timing (duration_seconds, usable_in_s, shot span)
4. Technical usability and visible defects (quality.technical, quality.defect, defects)
5. Dramaturgical / editorial function of the slot
6. Composition, appeal, clarity, and hero potential
7. Harmony with neighboring shots (motion, framing/shot_scale, look)

Hard rules:
- Content/exact identity ALWAYS outranks beauty and scores. A high hero, appeal,
  or composition score must NEVER make a content-wrong asset a good match.
- NEVER pick an asset only because it has the highest overall or hero score.
- If no content-fitting local asset exists for a required statement, use the
  existing gap / supplement path. Do NOT substitute a pretty but wrong picture.
- description and tags help semantic search; tags are hints, not proof of exact
  identity or exact place.
- Scores are decision aids, not objective truth. Missing scores on Slim-v1 /
  legacy rows are NEUTRAL — do not downgrade a legacy asset only for lacking
  quality/look/tags/shot_scale fields.
- Keep existing rules: video-over-photo, asset reuse / max usage, exact IDs only,
  and never invent asset IDs or hallucinate media.

Quality fields (when present):
- technical: technical usability of the visible material
- composition: framing order / visual structure
- appeal: immediate visual attractiveness
- clarity: how clearly the main subject is recognizable
- hero: suitability for strong openers, reveals, chapter peaks, or closings —
  do NOT prefer hero for every slot; use it for visually important moments
- defect: severity of visible problems (higher = worse)

Sequence harmony (after content + timing):
- Avoid long runs of identical framing + identical shot_scale unless deliberate.
- Shot-scale changes can create rhythm and visual progression.
- Use motion type / direction / intensity for continuity or intentional contrast.
- Use look (brightness, temperature, dominant colors) for visual continuity;
  avoid abrupt look jumps unless dramaturgically intended.
- Content fit and timing still outrank visual harmony. No fixed score formulas
  or rigid numeric thresholds.

Intro / closing note: hero and appeal help opener/closing choices, but teaser
function, content fit, and mood still outrank raw scores.
"""


_SHARED_SCRIPT_CORE_RULES = """\
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

DRAMATURGY VS. SPOKEN NARRATION

- Dramaturgy metadata is silent editorial guidance.
- It controls what this chapter emphasizes, not how the narrator travels between chapters.
- Write this chapter as an independent mini-documentary about its location.
- Begin directly with the place, its defining feature, its significance, a concrete landmark, or a verified fact.
- Do not describe leaving the previous chapter, travelling toward this chapter, arriving here, or continuing toward the next chapter unless explicit spoken-link permission is provided below.
- Do not verbalize dramaturgy_role, reason, narrative_arc, chapter order, or transition strategy.

INFORMATIVE CHAPTER PROSE

Prefer:
- geographic orientation
- historical development
- cultural importance
- concrete landmarks
- visible and explainable features
- dates, names and cause-and-effect relationships when supported
- one or two restrained atmospheric observations

Avoid:
- abstract literary interpretation
- personification of cities or landscapes
- vague statements about feeling history
- travel-blog filler
- road, route, departure and arrival narration
- poetic language replacing factual information

Do not use routine formulas such as:
- Leaving [place] behind...
- Leave [place] behind...
- From here, the journey continues...
- The road leads...
- The road out of...
- Before long, [place] appears...
- Moving on...
- Heading toward...
- Our next stop...
- The landscape changes again...
- Wir verlassen [Ort]...
- Von hier aus führt die Reise...
- Die Reise geht weiter...
- Unser nächster Halt...
- En quittant...
- La route mène / continue...
- Notre prochain arrêt...
- Saliendo de...
- El camino conduce / continúa...
- Nuestra próxima parada...
- Deixando ... para trás...
- A estrada leva / continua...
- Lasciando ... alle spalle...
- La strada porta / continua...
- La nostra prossima tappa...

RULES FOR SEGMENTS
- A segment may be a short sentence, several tightly related sentences, or a clause at a natural speech boundary.
- Never split mid-word.
- Write spoken narration only — no shot lists, asset IDs, or visual editing plans.
- Optional paragraph_break_after=true marks a natural factual/topic beat boundary (not a spoken pause label).
- Timed pauses use author_pause_after_seconds (0..8). Never write [pause X seconds] into segment.text.

SPOKEN NUMBERS
- In spoken narration, write every number as words in LANGUAGE — never Arabic digits.
- This includes years, quantities, ordinals, centuries, and measurements
  (1879 → "eighteen seventy-nine" / "achtzehnhundertneunundsiebzig";
   5 → "five" / "fünf"; 19th → "nineteenth" / "neunzehnten").
- Do not write forms such as 1879, 5, or 19th in narration_full or segment.text.
- JSON numeric fields (sequence_index, author_pause_after_seconds) stay numbers.
"""

_DEFAULT_DOCUMENTARY_STYLE_RULES = """\
DEFAULT DOCUMENTARY STYLE EXAMPLES (only when no binding Raw Chapter Reference is active):

BAD: "Das Bild zeigt Berge bei Sonnenuntergang."
GOOD: "Am Abend, wenn die Sonne hinter den Gipfeln verschwindet, beginnen die Felsen beinahe wie Kristalle zu funkeln."
"""

ASSET_GROUNDED_SCRIPT_RULES = """\
ASSET-GROUNDED SCRIPT MODE (BINDING)

You receive CHAPTER VISUAL PALETTE for THIS folder only.
It is a visual palette and a constraint — not a rundown, not a shot list,
and not a checklist of files to mention.

WHAT TO WRITE
- A mini-documentary about this place: history, origin, development,
  landmarks/sights (castle, church, named buildings), geographic and cultural
  peculiarities, verified local facts.
- Use ONLY PROJECT BRIEF + VERIFIED FACTS / METADATA for historical, geographic,
  and cultural claims. Captions are not a source of years, legends, or superlatives.
- Begin with the place or its defining sight — not with a picture description.
- Go into the sights that belong to this chapter (for example a castle or a church):
  dedication, origin, function, documented tradition. Do not stay at postcard level.

WHAT NOT TO WRITE
- Do NOT narrate the inventory or travel through the files.
- Do NOT mention every asset. Do NOT stretch the text to use leftover files.
- Do NOT repeat the same establishing motif in different words
  (e.g. lake + island + castle as a postcard, then again, then again).
- Do NOT describe an image just because it exists, and do not strain to paint
  remaining clusters.
- NEVER speak asset IDs, filenames, "aerial", "drone", "Luftaufnahme",
  "im Vordergrund", "hier sehen wir", "das Bild zeigt", "als Nächstes".
- Do NOT copy caption adjectives (malerisch, markant, atemberaubend).
- Incidental people/activity in the palette is not a chapter topic unless it is
  historically or culturally relevant.

WHEN A VISUAL DETAIL MAY ENTER THE PROSE
- Only when it truly fits a fact, a sight, or a cultural explanation you are
  already making.
- Prefer details that CHAPTER VISUAL PALETTE actually shows.
- There is NO quota: no minimum and no maximum number of visual touches.
  Use a visible detail only when it really fits; otherwise leave it unspoken.

FACTS vs PICTURES
- Prefer to stay with what the available material can show.
- If a detail is important to the place or the sight (history, dedication,
  origin, a well-known count of steps, a documented tradition), include it
  even when no matching asset exists. Do not drop an important fact only
  because the palette has no close-up of it.
- Mark unverified legends as tradition / Überlieferung, never as hard fact.
- Do NOT invent a close-up or interior the palette does not contain and then
  describe it as if it were on screen.
- If many files show one motif, collapse them: one motif → at most one spoken beat.

DRAMATURGY remains silent editorial guidance (role, arc, order, CTAs, word count).
Do not verbalize it. Do not turn it into journey/road narration.

BAD:
"Hier sehen wir zuerst die Burg auf dem Felsen, dann die Inselkirche im Abendlicht,
danach die Pletnas am Steg."

GOOD:
"Die Burg Bled wird im Jahr eintausendelf urkundlich genannt. Die Marienkirche
ist Mariä Himmelfahrt geweiht; der weiße Turm steht aus einem dichten Baumkranz."
"""

# Rückwärtskompatibel für Legacy-Prompts / Imports.
_SHARED_SCRIPT_RULES = _SHARED_SCRIPT_CORE_RULES + "\n" + _DEFAULT_DOCUMENTARY_STYLE_RULES


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
      "semantic_function": "atmosphere|history|geography|culture|fact|transition|cta_stay|cta_like",
      "fact_check_required": false,
      "paragraph_break_after": false,
      "author_pause_after_seconds": 0.0,
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
  ],
  "rhetoric_usage": [
    {{
      "slot_id": "stay_tuned_payoff|named_future_highlight|callback_early_chapter|film_arc_echo|superlative_unique_once|distant_contrast|distant_commonality|opener_rhetorical_question|opener_time_of_day|opener_wide_landscape",
      "used": true,
      "evidence_quote": "exact phrase from narration_full",
      "related_chapter_ref": "chapter heading from FILM CHAPTER MAP or empty"
    }}
  ],
  "chapter_link_usage": {{
    "from_previous": false,
    "to_next": false,
    "callback": false,
    "evidence_quotes": []
  }},
  "style_reference_usage": {{
    "mode": "raw_text|style_profile|default",
    "matched_features": ["direct opening", "short factual beats", "restrained atmosphere"],
    "intentional_deviations": []
  }}
}}

rhetoric_usage:
- Prefer an empty array [].
- Include only slots you actually used (used=true). Omit unused slots.
- At most 2 used:true entries per chapter.
- evidence_quote MUST appear in narration_full.

chapter_link_usage:
- Audit only — not spoken.
- evidence_quotes MUST appear exactly in narration_full.
- Without a used spoken link: all booleans false and evidence_quotes [].
- Never set a boolean true unless SPOKEN CHAPTER LINK PERMISSIONS allow that direction.

style_reference_usage:
- Audit only — not spoken.
- matched_features must describe prose traits, never copy facts or wording from the reference.

AUTHOR PAUSES
- When the Raw Chapter Reference uses explicit timed pauses, record that rhythm with
  author_pause_after_seconds on segments (for ElevenLabs TTS pause tags).
- Use 0 when no pause follows.
- Use the reference's observed duration range.
- Shorter pauses connect closely related facts.
- Longer pauses follow a major geographic change, important historical statement,
  strong visual reveal, or completed subject block.
- Do not use the same duration mechanically after every segment.
- Do not write [pause X seconds] inside segment.text or narration_full.
- The application maps author_pause_after_seconds to [pause N seconds] markers
  in the chapter TTS call (same form as in the Folder Voice-over editor).
- Cut planning uses measured ElevenLabs timestamps from the chapter audio —
  not these pause fields as cut instructions.
- Every positive author pause should normally also set paragraph_break_after=true.
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


DEFAULT_ENHANCED_SCRIPT_REVISION_INSTRUCTIONS = (
    "change the text so it sounds more human. "
    "Keep the structure and the pause tags in brackets. "
    "Spell out every number in the spoken text as words in the script language "
    "(years, counts, ordinals) — never leave digits such as 1879. "
    "It must not be detected by AI Detectors."
)


def build_enhanced_script_revision_prompt(
    *,
    editor_instructions: str,
    current_script: str,
    folder_name: str,
    language: str = "de",
) -> str:
    """Minimaler Revisions-Prompt: Freitext + Kapiteltext inkl. [pause N seconds]."""
    instructions = (editor_instructions or "").strip()
    script = (current_script or "").strip()
    return f"""\
Revise the spoken narration for ONE documentary chapter.

LANGUAGE: {language}
CHAPTER / folder_name: {folder_name}

EDITOR INSTRUCTIONS (follow these; they override the current wording where they conflict):
{instructions or "(no instructions provided)"}

CURRENT SCRIPT (includes timed pause markers on their own lines):
{script or "(empty)"}

PAUSE MARKERS — copy 1:1:
- The input may contain lines like [pause 3 seconds] or [pause 4 seconds].
- Keep every such marker exactly as written (same numbers, same positions between beats).
- Do NOT write bare [pause] without a duration.
- Do NOT invent new pause markers unless the editor instructions explicitly ask for them.
- Only rewrite the spoken prose between those markers.

SPOKEN NUMBERS (BINDING):
- In the spoken prose between pause markers, write every number as words in LANGUAGE.
- Never leave Arabic digits in the spoken lines (years, counts, ordinals, measurements).
- Pause marker lines such as [pause 5 seconds] keep their digits exactly — do not spell those out.

Return ONLY the revised narration as plain text, including the preserved [pause N seconds] lines.
No JSON, no markdown code fences, no commentary, no bullet lists of notes.
"""


def _permission_label(allowed: bool) -> str:
    return "ALLOWED" if allowed else "FORBIDDEN"


def build_spoken_chapter_link_permissions_block(
    *,
    transition_from_previous: bool = False,
    transition_to_next: bool = False,
    callback_to_previous: bool = False,
    use_contrast_with_previous: bool = False,
    use_commonality_with_previous: bool = False,
) -> str:
    return (
        "SPOKEN CHAPTER LINK PERMISSIONS\n\n"
        f"- transition from previous: {_permission_label(transition_from_previous)}\n"
        f"- transition to next: {_permission_label(transition_to_next)}\n"
        f"- callback to previous: {_permission_label(callback_to_previous)}\n"
        f"- content contrast with previous: {_permission_label(use_contrast_with_previous)}\n"
        f"- content commonality with previous: {_permission_label(use_commonality_with_previous)}\n"
        "\n"
        "Contrast/commonality may shape which facts you emphasize. They do NOT grant "
        "departure, arrival, road, or journey formulas and do NOT require an opening bridge."
    )


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
    film_wide_editorial_links_text: str = "",
    recent_neighbor_excerpts_text: str = "",
    editorial_neighbor_craft_text: str = "",
    rhetoric_ledger_text: str = "",
    opening_inventory_text: str = "",
    language: str = "de",
    transition_from_previous: bool = False,
    transition_to_next: bool = False,
    callback_to_previous: bool = False,
    use_contrast_with_previous: bool = False,
    use_commonality_with_previous: bool = False,
    style_is_raw_chapter: bool = False,
    repair_instruction: str = "",
    chapter_end_cta_text: str = "",
    chapter_visual_palette_text: str = "",
) -> str:
    """Ein Dramaturgie-Kapitel / Ordner — nur gesprochene Narration.

    Ohne ``chapter_visual_palette_text``: Bilder erst im Cut.
    Mit Palette: asset-grounded — Inventar als Motiv-Palette, kein Shotlist-Skript.
    """
    forbidden = "\n".join(f'- "{p}"' for p in FORBIDDEN_PHRASES)
    id_prefix = f"{folder_slug}_"

    def _optional_block(text: str) -> str:
        cleaned = (text or "").strip()
        return f"\n{cleaned}\n" if cleaned else ""

    shared_rules = _SHARED_SCRIPT_CORE_RULES
    if not style_is_raw_chapter:
        shared_rules = _SHARED_SCRIPT_RULES

    # Nachbarnamen nur nennen, wenn eine passende Erlaubnis aktiv ist.
    show_previous = any(
        (
            transition_from_previous,
            callback_to_previous,
            use_contrast_with_previous,
            use_commonality_with_previous,
        )
    )
    show_next = transition_to_next
    prev_line = (
        f"- previous chapter in the film: {previous_folder_name}"
        if show_previous and previous_folder_name
        else "- previous chapter in the film: (silent orientation only — do not name unless permitted below)"
    )
    next_line = (
        f"- next chapter in the film: {next_folder_name}"
        if show_next and next_folder_name
        else "- next chapter in the film: (silent orientation only — do not name unless permitted below)"
    )

    permissions_block = build_spoken_chapter_link_permissions_block(
        transition_from_previous=transition_from_previous,
        transition_to_next=transition_to_next,
        callback_to_previous=callback_to_previous,
        use_contrast_with_previous=use_contrast_with_previous,
        use_commonality_with_previous=use_commonality_with_previous,
    )

    chapter_order_block = _optional_block(chapter_order_text)
    film_wide_block = _optional_block(film_wide_editorial_links_text)
    neighbor_excerpts_block = _optional_block(recent_neighbor_excerpts_text)
    editorial_neighbor_block = _optional_block(editorial_neighbor_craft_text)
    rhetoric_ledger_block = _optional_block(rhetoric_ledger_text)
    opening_inventory_block = _optional_block(opening_inventory_text)
    repair_block = _optional_block(repair_instruction)
    cta_block = _optional_block(chapter_end_cta_text)
    palette_text = (chapter_visual_palette_text or "").strip()
    asset_grounded_rules = ASSET_GROUNDED_SCRIPT_RULES if palette_text else ""
    palette_block = _optional_block(palette_text)

    body_words_line = (
        f"- target_words: {target_words} (soft target; stay within {min_words}-{max_words})"
    )
    if cta_block:
        body_words_line = (
            f"- target_words: {target_words} (soft target for the CHAPTER BODY; "
            f"stay within {min_words}-{max_words} before any chapter-end CTAs). "
            "Planned chapter-end CTAs are EXTRA words after the body."
        )

    style_label = (
        "RAW CHAPTER PROSE REFERENCE / STYLE CONTEXT"
        if style_is_raw_chapter
        else "STYLE PROFILE"
    )
    # Raw-Modus: Binding-Referenz früh (nach Rolle/Sprache/Sicherheit), vor Dramaturgie.
    if style_is_raw_chapter:
        early_style_block = f"\n{style_label}:\n{style_profile_text}\n"
        late_style_block = ""
        priority_block = """
PROMPT PRIORITY (BINDING)
1. Schema, safety and factuality
2. Explicit editor instructions / must-include / must-avoid
3. Raw Chapter Prose Architecture
4. Project-specific chapter content
5. Silent dramaturgy metadata
6. Generic default style rules

When generic style advice conflicts with the Raw Chapter Reference, follow the Raw Reference for prose form.
"""
    else:
        early_style_block = ""
        late_style_block = f"\n{style_label}:\n{style_profile_text}\n"
        priority_block = ""

    return f"""\
You are writing documentary narration for ONE chapter of a multi-location travel film.

LANGUAGE: {language}

{shared_rules}
{asset_grounded_rules}
{priority_block}{early_style_block}
STRICTLY AVOID these phrases and patterns:
{forbidden}
- pure inventories of visible objects
- image-caption narration ("the picture shows…")
- repeating the same first-sentence template as earlier chapters (see OPENING INVENTORY)

THIS CHAPTER ONLY
- folder_name (EXACT): {folder_name}
- dramaturgy_role (SILENT METADATA — do not verbalize): {dramaturgy_role}
{prev_line}
{next_line}
{body_words_line}
- Write ONLY the spoken narration for this chapter — not the whole film.
- Every segment MUST set folder_name to exactly "{folder_name}".
- Use ID prefixes starting with "{id_prefix}" (e.g. {id_prefix}segment_001).

{permissions_block}
{cta_block}{repair_block}{chapter_order_block}{film_wide_block}{opening_inventory_block}{rhetoric_ledger_block}{neighbor_excerpts_block}{editorial_neighbor_block}
{_json_schema_block(id_prefix=id_prefix).replace("EXACT_FOLDER_NAME", folder_name)}

PROJECT BRIEF:
{project_brief_text}

FILM CONTEXT (silent orientation — do not rewrite other chapters; do not verbalize narrative_arc):
{film_context_text}

THIS CHAPTER DRAMATURGY:
{chapter_dramaturgy_text}
{late_style_block}
VERIFIED FACTS / METADATA (only these may be stated as facts):
{verified_facts_text}
{palette_block}"""


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
- You may still use segment anchors when appropriate.
- pause_directives are DISABLED — always return [].
- Every shot MUST set start_cut_alignment to exactly one of:
  mid_sentence | sentence_boundary | in_pause
  (prefer mid_sentence / sentence_boundary; in_pause is unused).
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

1. a rough visual edit plan,
2. concrete coverage gaps where no suitable local asset exists.
   (pause_directives are disabled — always return an empty array.)

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
- Usable video length for planning =
  max(0, duration_seconds - usable_in_s - 1.0) (black/lead-in + 1.0s safety).
  Prefer assets whose planning usable length covers the intended shot span.
- Photos/stills: do not plan long static holds as if they were motion clips;
  keep still spans short unless a deliberate still is justified.

{CUT_ASSET_SELECTION_GUIDANCE}
PAUSE RULES (DISABLED):

- Always return \"pause_directives\": [].
- Do not invent pulled pauses; natural TTS silence in the audio is enough.
- Chapter spacing uses Vorlauf/Nachlauf envelopes, not pause_directives.

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
  "pause_directives": [],
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
LOCAL ASSETS (slim; description/tags + duration/usable_in_s + motion/framing/shot_scale + quality/look when present):
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

1. a continuous cut-boundary chain across the VO (voice-over) time carpet,
2. one slot between every consecutive pair of boundaries,
3. honest local asset_fit ratings; for weak/none include inline gap specs.
   (pause_directives are disabled — always return an empty array.)

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
- Prefer mid_sentence or sentence_boundary (in_pause is unused — pauses off).
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
{VIDEO_OVER_PHOTO_ASSET_RULES}- Prefer assets whose planning usable length
  (duration_seconds - usable_in_s - 1.0s safety) covers the intended span
  (closing slot: include Nachlauf/postroll). Never assume freeze/tpad video-hold
  padding. Do not plan tight fits against the raw usable length.
- Opening slot (first) and closing slot (last): different assets from their
  immediate neighbor; max usage + reuse distance apply with no exemption.
- narrative_function for first/last may be chapter_open / chapter_close.

{CUT_ASSET_SELECTION_GUIDANCE}
PAUSE RULES (DISABLED):

- Always return \"pause_directives\": [].
- Do not invent pulled pauses; natural TTS silence in the audio is enough.
- Chapter spacing uses Vorlauf/Nachlauf envelopes, not pause_directives.

RETURN STRICT JSON ONLY. No Markdown. No comments. No trailing commas.

OUTPUT SCHEMA:

{{
  "voiceover_preroll_sec": null,
  "voiceover_postroll_sec": null,
  "closing_fallback_asset_id": "existing_asset_id_not_equal_last_slot",
  "pause_directives": [],
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
Always set closing_fallback_asset_id (reserve closer for Python Timing).

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
- closing_fallback_asset_id exists in LOCAL ASSETS and differs from the last
  slot local_asset_id (prefer photo/still or long atmosphere)
- Boundaries chronological; first=VO start; last=VO end
- Every motion local_asset_id has planning_usable
  (duration_seconds - usable_in_s - 1.0s) >= intended slot span
  (last slot: include Nachlauf/postroll)
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
LOCAL ASSETS (slim; description/tags + duration/usable_in_s + motion/framing/shot_scale + quality/look when present):
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

1. a continuous cut-boundary chain across the VO (voice-over) time carpet,
2. one slot between every consecutive pair of boundaries,
3. honest local asset_fit ratings; for weak/none include inline gap specs.
   (pause_directives are disabled — always return an empty array.)

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
{VIDEO_OVER_PHOTO_ASSET_RULES}- Prefer assets whose planning usable length
  (duration_seconds - usable_in_s - 1.0s safety) covers the intended span
  when possible (closing slot: include Nachlauf/postroll). Never assume
  freeze/tpad video-hold padding. Obey shot_min/shot_max from
  SHOT / ASSET CONSTRAINTS.
- Opening slot (first) and closing slot (last): different assets from their
  immediate neighbor when both assigned; max usage + reuse distance apply.
- narrative_function for first/last may be chapter_open / chapter_close.
- The last slot must span from its start boundary through the full remaining
  VO to the last boundary (VO end).

{CUT_ASSET_SELECTION_GUIDANCE}
PAUSE RULES (DISABLED):

- Always return \"pause_directives\": [].
- Do not invent pulled pauses; natural TTS silence in the audio is enough.
- Chapter spacing uses Vorlauf/Nachlauf envelopes, not pause_directives.

RETURN STRICT JSON ONLY. No Markdown. No comments. No trailing commas.

OUTPUT SCHEMA:

{{
  "voiceover_preroll_sec": null,
  "voiceover_postroll_sec": null,
  "closing_fallback_asset_id": "existing_asset_id_not_equal_last_slot",
  "pause_directives": [],
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
Always set closing_fallback_asset_id (reserve closer for Python Timing).

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
- closing_fallback_asset_id exists in LOCAL ASSETS and differs from the last
  slot local_asset_id (prefer photo/still or long atmosphere)
- Boundaries chronological; first=VO start; last=VO end
- Last boundary is sentence end (not a keyword mid_sentence)
- Keyword picture cuts use mid_sentence + explicit offset_seconds at onset
- Prefer words[].offset_seconds when words[] is present
- No keyword picture starts before its spoken keyword
- Every motion local_asset_id has planning_usable
  (duration_seconds - usable_in_s - 1.0s) >= intended slot span
  (last slot: include Nachlauf/postroll)
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
LOCAL ASSETS (slim; description/tags + duration/usable_in_s + motion/framing/shot_scale + quality/look when present):
{local_assets_json}

STYLE PROFILE:
{style_profile_text}

DRAMATURGY:
{dramaturgy_text}
"""


def build_keyword_flow_unified_cut_prompt(
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
    """Kapitel-Unified-Cut: Keyword Flow (context-first, echte Onsets, sichere Pausen).

    Output-Schema bleibt unified-cut-v1. Keine DEFAULT_CUT_RHYTHM_TARGETS.
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
- Use only segment_ids / sentence_ids / local_asset_id values from this chapter.
- Prefix every cut_id, slot_id and coverage_gap_id with "{slug}_".
- Do not invent material for previous or next chapters.
- Previous chapter: {prev}
- Next chapter: {nxt}
- If a next chapter exists, prefer pause_function "chapter_transition" after
  this chapter's last VO sentence when editorially justified.
- Do NOT plan the Maps folder opener — Python inserts a technical 9s map
  opener before VO when a single valid map exists.
"""

    vision_rules = ""
    if include_middle_frames:
        vision_rules = """
MIDDLE-FRAME VISION (OPTIONAL INPUT):

- After the text prompt you may receive JPEG stills labeled
  "IMAGE for local_asset_id=<id>".
- Use images together with LOCAL ASSETS metadata to choose assets.
- Never invent an asset ID that is not listed in LOCAL ASSETS.
"""

    sentence_block = ""
    if sentence_timings_json.strip():
        sentence_block = f"""
SENTENCE TIMINGS (authoritative; cleaned spoken words from ElevenLabs).
Times are relative to each segment's audio. Each sentence includes words[]
with text / start_seconds / end_seconds / offset_seconds / original_word_index
(and optional word_ref). Direction tags and punctuation-only tokens are
already removed. Prefer words[].offset_seconds for keyword onset cuts.
NEVER invent or estimate word onsets from character position or sentence length.
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
You are the KEYWORD FLOW cut planner for a documentary pipeline (unified-cut-v1).

KEYWORD FLOW MARKER: context-first, keyword-anchored, honest gaps, no pause extensions.

Your task is to create ONE complete chapter plan:

1. a continuous cut-boundary chain across the VO time carpet,
2. one slot between every consecutive pair of boundaries,
3. honest local asset_fit ratings; for weak/none include inline gap specs,
4. always return \"pause_directives\": [] — never request or extend pauses.

MODE RULES (CRITICAL — KEYWORD FLOW):

- Keyword Flow is context-first and keyword-anchored.
- Before assigning an asset, understand the full meaning of the current passage,
  relevant preceding and following sentences, the concrete entity or visual
  subject, pronoun/reference relationships, whether narration requires exact
  identity, and whether the passage is factual, atmospheric, transitional or
  reflective.
- Do not assign assets from isolated word matches.
{chapter_scope}{vision_rules}{ledger_block}{shot_constraints_text}
NAMED ENTITY PRIORITY (BINDING):

1. concrete named entity (e.g. Salto Ángel)
2. concrete qualified motif phrase
3. general motif class (e.g. waterfall)
4. atmospheric correspondence

- When a concrete entity is named, do NOT use an arbitrary asset of the same
  general category. Concrete waterfall ≠ any waterfall; concrete church ≠ any
  church; concrete monument ≠ any monument.
- CHAPTER-LOCAL IDENTITY (overrides English proper-name pedantry):
  Inside THIS chapter folder, a local VIDEO whose description clearly depicts the
  spoken motif counts as exact identity present — even in German and even if the
  English proper name is missing. Treat as matches, for example:
  - \"verlassenes Dorf\" / abandoned stone cottages / village ruins ≡ Deserted Village
  - \"Schafe\" / marked sheep on Achill ≡ sheep (do not require \"blanket bog\" wording)
  - coastal bay / curved beach between headlands ≡ the chapter's named bay (e.g. Keem)
  - cliff-edge coastal road under Atlantic weather ≡ Atlantic Drive motif
  Prefer that VIDEO over any PHOTO that only wins by spelling the English name.
- If NO local video or photo shows the motif at all: asset_fit weak or none,
  local_asset_id MUST be null, and create a Coverage Gap. Never invent identity
  from a wrong place (e.g. a different island's ruins).
- Resolve pronouns across sentences when unambiguous. If ambiguous: do not invent
  identity; rate fit cautiously; gap when a concrete unproven identity is required.

ATMOSPHERIC PASSAGES WITHOUT KEYWORD:

- Not every slot needs a spoken keyword.
- For mood / atmosphere / culture / reflection / change / place character /
  transitions: derive a semantic visual_intent from the full context.
- Prefer alignment sentence_boundary or in_pause for non-keyword shots.
- mid_sentence ONLY when offset_seconds is exactly a delivered cleaned word onset.
- Do not invent artificial keywords.

KEYWORD BOUNDARIES:

- For a keyword mid-sentence cut: use sentence_id from SENTENCE TIMINGS,
  set offset_seconds EXACTLY to the delivered onset of the first keyword word,
  set alignment=\"mid_sentence\".
- Always supply the exact real keyword onset first. Python may shift the PICTURE
  cut within ±1.5 seconds for shot_min/max / valid chain — never invent offsets
  yourself. Python never inserts silence and never shifts narration/word times.
- Never estimate onsets from text proportion or character position.

LONG THEME BLOCKS:

- A shot need not run until the next keyword.
- If narration stays on the same motif longer than shot_max, insert additional
  semantically fitting shots (even / uneven splits, detail changes, scale changes,
  or sentence boundaries).
- Forbidden: one overlong shot, silently exceeding shot_max, random filler,
  mechanical one-asset-per-sentence or one-asset-per-keyword.
- Never repair shot_min/shot_max by requesting or extending a pause.

PAUSE RULES (DISABLED):

- Always return \"pause_directives\": [].
- Do not invent pulled pauses; do not request ADDITIONAL silence.
- Do not extend any existing natural pause.
- Natural TTS silence may be used only as a visual cut window via
  alignment=\"in_pause\" — and only inside the real pause, with 5 timeline frames
  after the previous word end and 5 timeline frames before the next word start.
- If that natural window is too small: do not force an in_pause cut, do not
  invent silence, choose another valid boundary, or leave a gap / fail closed.
- shot_min/shot_max violations must NOT be repaired via pause extension.
  Allowed instead: shift a boundary within ±1.5s, add fitting slots, reassign
  assets, or fail closed for a new cut.

ASSET FIT (KEYWORD FLOW — BINDING):

- strong / acceptable: asset may be used; coverage_gap_id null.
- weak / none: local_asset_id MUST be null; coverage_gap_id + gap fields REQUIRED.
- Never keep a weak asset just to fill a slot.
- REUSE DISTANCE / MAX USAGE: never place the same non-intro asset on
  consecutive shots; leave the configured reuse gap between reuses; never
  exceed max_asset_usage. When the inventory is too thin for a beat under
  those rules, prefer an honest coverage gap (asset_fit none + gap fields)
  over early reuse. Python will demote illegal early reuses to coverage gaps.
- Gap search_concepts: 2–4 English phrases, 2–5 words each, concrete to the missing motif.
{VIDEO_OVER_PHOTO_ASSET_RULES}
{CUT_ASSET_SELECTION_GUIDANCE}
CLOSING:

- The last slot is the primary Closing Shot (strong/acceptable), able to carry
  Settings postroll/Nachlauf (Python applies the duration).
- Always set closing_fallback_asset_id to a DIFFERENT chapter-local
  strong/acceptable asset with the same closing intent.
- ALWAYS also set (Keyword Flow binding; schema stays unified-cut-v1):
  closing_fallback_asset_fit = strong|acceptable (never weak/none),
  closing_fallback_asset_fit_reason (non-empty),
  closing_fallback_visual_intent (same closing intent as primary).
- Python uses fallback only if primary is technically/rule-unusable.

FORMAT PRINCIPLE (CRITICAL):

- Output N slots and exactly N+1 boundaries (unified-cut-v1).
- Boundary i ends slot i and starts slot i+1.
- First boundary = VO start; last boundary = VO end.
- Do NOT invent absolute timeline seconds, frames, or timecodes.
- TWO FIELDS: position=start|early|middle|late|end;
  alignment=mid_sentence|sentence_boundary|in_pause — never mix them.
- For in_pause cuts, Python keeps the cut inside the real natural pause window
  (5 timeline frames safety); it never inserts or extends silence.

RETURN STRICT JSON ONLY. No Markdown. No comments. No trailing commas.

OUTPUT SCHEMA:

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
      "covered_sentence_ids": ["segment_001__s001"]
    }}
  ]
}}

FINAL VALIDATION BEFORE RETURNING JSON:

- len(slots) == len(boundaries) - 1
- All IDs unique / chapter-prefixed where required
- All sentence_ids exist in SENTENCE TIMINGS
- All local_asset_id values exist in LOCAL ASSETS (or null)
- closing_fallback_asset_id differs from last slot local_asset_id
- closing_fallback_asset_fit is strong or acceptable (never weak/none)
- closing_fallback_asset_fit_reason and closing_fallback_visual_intent non-empty
- Keyword mid_sentence offsets match delivered cleaned word onsets
- weak/none → local_asset_id null + gap fields
- strong/acceptable → coverage_gap_id null
- Respect shot_min / shot_max / max_asset_usage / reuse distance from SETTINGS
- No Maps opener planning; no absolute timeline seconds

LOCKED SCRIPT:
{locked_script_json}

SEGMENT TIMINGS:
{segment_timings_json}
{sentence_block}
LOCAL ASSETS (slim; description/tags + duration/usable_in_s + motion/framing/shot_scale + quality/look when present):
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
    intro_preroll_sec: float = 4.0,
    intro_postroll_sec: float = 6.5,
    intro_postroll_min_sec: float = 5.0,
    intro_postroll_max_sec: float = 8.0,
) -> str:
    """Unified-Schema, aber Intro-Sonderregeln (strong-only, bundeltes Inventar)."""
    slug = folder_slug or folder_name or "Intro"
    duration = max(0.1, float(intro_audio_duration_seconds or 0.0))
    preroll = max(0.0, float(intro_preroll_sec))
    post_min = max(0.0, float(intro_postroll_min_sec))
    post_max = max(post_min, float(intro_postroll_max_sec))
    post_default = max(post_min, min(post_max, float(intro_postroll_sec)))
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
- Opening envelope: YOU choose intro_opener_asset_id — the semantically best
  series/tease opener from BUNDLED INVENTORY (establishing / wide / iconic).
  Python inserts that asset as a SEPARATE preroll shot for {preroll:.1f}s
  BEFORE Intro VO (independent clip — not an extension of slot 1, and NEVER
  a copy of slot 1's asset). When VO starts, your first slot begins as a NEW
  shot with a DIFFERENT local_asset_id. Set voiceover_preroll_sec to
  {preroll:.1f}.
- Closing envelope: YOU choose intro_closing_asset_id — the semantically best
  landing/outro hold from BUNDLED INVENTORY (wide / reflective / closing mood).
  Python inserts that asset as a SEPARATE postroll shot for {post_min:.1f}–{post_max:.1f}s
  AFTER Intro VO ends (independent clip — not an extension of the last VO
  slot, and NEVER a copy of the last slot's asset).
  Set voiceover_postroll_sec between {post_min:.1f} and {post_max:.1f} (prefer ~{post_default:.1f} unless justified).
- CRITICAL: preroll/postroll do NOT fill gaps inside the VO. Your boundaries
  must already cover the full VO carpet (start→end). Python places the LLM-
  chosen envelope assets before first VO and after last VO — it will NOT
  extend a last shot that ends early during narration (use
  closing_fallback_asset_id for that reserve closer).
- Asset uniqueness (CRITICAL): intro_opener_asset_id, intro_closing_asset_id,
  closing_fallback_asset_id, and every VO slot local_asset_id with
  asset_fit \"strong\" must ALL be pairwise distinct. Never reuse the same
  asset for opener, VO content, reserve closer, or closing hold.
- Always set closing_fallback_asset_id to a DIFFERENT strong asset from
  BUNDLED INVENTORY than the last slot's local_asset_id AND different from
  intro_opener_asset_id / intro_closing_asset_id. Python inserts it only if
  the last picture still ends early during VO (reserve closer). Prefer
  establishing / landscape / wide. Do NOT emit closing_fallback_asset_fit /
  _reason / _visual_intent (Intro-only id).
- Opening and closing VO slots must use different local_asset_id values when
  both have strong assets.
- Prefix every cut_id, slot_id and coverage_gap_id with \"{slug}_\".
- Use only segment_ids / sentence_ids from the Intro inputs.
- Use only local_asset_id values that exist in BUNDLED INVENTORY.

KEYWORD / CONTEXT CUTS (CRITICAL — understand the whole Intro first):

- Read the FULL Intro VO (all sentences) before placing any cut. Cuts must
  follow meaning and dramaturgy, not keyword spotting alone.
- NOT every named place / landmark / topic / list word requires its own cut.
  Only cut when a new visual subject is editorially meaningful — a real
  reveal, contrast, or list beat that deserves its own picture.
- When you DO cut to a named subject, that picture MUST start at the spoken
  keyword onset — never earlier in the same sentence or list.
- Bad: show Antelope Canyon while the VO is still on a previous place / still
  leading into the list.
- Also bad: fire a new cut on every capitalized place name even when the VO
  is still building one continuous thought (keeps Intro restless / too short).
- Good: cut to Antelope Canyon exactly as \"Antelope\" begins — IF that item
  warrants a distinct picture in context.
- Lists / enumerations: prefer one picture per list item ONLY when items are
  distinct visual beats. If several names are connective / too dense for
  readable pictures, keep one hold across them (or cut only on the strongest
  items) and use pauses (below) so pictures are not cramped.
- Do NOT pre-roll a next-subject picture during filler / connective speech
  before its keyword.
- After the LAST justified keyword/list cut: that closing picture continues
  through any remaining VO (outro line, tag, breath) until the true VO end.
  Do NOT place the final boundary at the last keyword — place it at the last
  sentence end.
- Opening envelope is separate: Python places your intro_opener_asset_id for
  {preroll:.1f}s before VO; your slot 1 starts at VO start with a different
  asset. From VO start onward, keyword-onset sync applies to justified
  place/list cuts only.
- Prefer WORD TIMINGS: for justified keyword/list cuts, set alignment
  \"mid_sentence\" and offset_seconds from words[].offset_seconds of the spoken
  keyword (or the first word of a multi-word place name). Fall back to text
  proportion only when words[] is missing for that sentence.
- Example (mid-list cut only — NOT the final boundary):
  words[] contains {{\"text\":\"Antelope\",\"offset_seconds\":1.4}} →
  boundary at that sentence_id with offset_seconds=1.4, position=\"middle\",
  alignment=\"mid_sentence\".

ENUMERATION PACING (no pulled pauses):

- Pulled pause_directives are DISABLED for Intro. Do not invent pauses.
- If many place names sit inside ONE long comma-list sentence, prefer fewer
  justified keyword cuts + holds rather than a machine-gun of sub-second
  pictures. Still sync any cut you do make to keyword onset.

PAUSE RULES (DISABLED):

- Always return \"pause_directives\": [].
- Natural TTS silence in the locked audio is enough; Python will not insert
  pulled gaps from directives.

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
- For justified keyword/list cuts (interior boundaries only): set
  offset_seconds explicitly AND alignment=\"mid_sentence\". Example interior
  cut: {{\"sentence_id\":\"…\",\"position\":\"middle\",\"offset_seconds\":1.4,
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

{CUT_ASSET_SELECTION_GUIDANCE}
RETURN STRICT JSON ONLY. No Markdown. No comments. No trailing commas.

OUTPUT SCHEMA:

{{
  "voiceover_preroll_sec": {preroll:.1f},
  "voiceover_postroll_sec": {post_default:.1f},
  "intro_opener_asset_id": "strong_opener_asset_not_used_in_vo_slots",
  "intro_closing_asset_id": "strong_closing_hold_asset_not_used_in_vo_slots",
  "closing_fallback_asset_id": "different_strong_asset_than_last_slot",
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
- Last slot covers through remaining VO after the last justified keyword cut
- closing_fallback_asset_id set, exists in BUNDLED INVENTORY, differs from
  last slot local_asset_id
- Every motion local_asset_id has planning_usable
  (duration_seconds - usable_in_s - 1.0s) >= intended slot span
  (first/last: include Vorlauf/Nachlauf)
- asset_fit is only strong or none
- Opening/closing assets differ when both assigned
- All local_asset_id values exist in BUNDLED INVENTORY (or null)
- Justified keyword/list picture cuts use mid_sentence + words[].offset_seconds
  when words[] is present (else text-proportion offset_seconds)
- No place/list picture starts before its spoken keyword (except slot-1 preroll)
- Not every keyword becomes a cut — cuts follow full-text context
- pause_directives must be []
- Preroll/postroll are outside the VO window — do not leave narration uncovered

LOCKED SCRIPT (Intro only):
{locked_script_json}

SEGMENT TIMINGS:
{segment_timings_json}
{sentence_block}
BUNDLED INVENTORY (compact rows; description/tags + motion/framing/shot_scale + subset quality/look):
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

{CUT_ASSET_SELECTION_GUIDANCE}
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
- Asset planning usable length covers each shot
  (duration_seconds - usable_in_s - 1.0s safety), including closing + postroll.
  Never assume freeze/tpad video-hold padding for short motion video.
- Every chapter has opening coverage at narration start and closing coverage at
  narration end, including the configured preroll/postroll intent.
- No two consecutive shots share the same non-intro asset_id.

LOCKED SCRIPT:
{locked_script_json}

NARRATION TIMELINE:
{narration_timeline_json}
{sentence_block}
PAUSE DIRECTIVES (DISABLED — expect empty / ignore):
{pause_directives_json}

ROUGH CUT:
{rough_cut_json}

LOCAL ASSETS (slim; description/tags + duration/usable_in_s + motion/framing/shot_scale + quality/look when present):
{local_assets_json}

ACCEPTED SUPPLEMENTS ONLY:
{accepted_supplements_json}

STYLE:
{style_profile_text}
"""
