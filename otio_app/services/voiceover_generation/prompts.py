"""Prompt-Builder für die Voice-over-Generierungs-Pipeline.

Phase 2: build_style_profile_prompt()
Phase 3: build_dramaturgy_prompt()
Phase 4: build_folder_voiceover_prompt(), build_voiceover_review_prompt(),
         build_voiceover_correction_prompt()
"""

from __future__ import annotations

from otio_app.defaults import (
    BRIEF_NEGATIVE_RULE_INSTRUCTIONS,
    PAUSE_AFTER_CHOICES,
    SEGMENT_ASSET_PLANNING_MODE_LLM_DISCRETION,
    SEGMENT_ASSET_PLANNING_MODE_PER_SEGMENT,
    SEGMENT_ASSET_PLANNING_MODE_PER_SENTENCE,
)
from otio_app.services.voiceover_generation.folder_asset_readiness import SentenceAssetReadinessIssue
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


_SEGMENT_ASSET_PLANNING_BLOCKS: dict[str, str] = {
    SEGMENT_ASSET_PLANNING_MODE_PER_SENTENCE: """\
## Shot planning for this location: ONE asset per sentence
For this location, plan visuals per SENTENCE, not per shot. Assign exactly one \
primary_asset_id (plus optional backup_asset_ids/second_backup_asset_ids) per \
sentence/beat, and leave visual_asset_plan.preferred_cut_count at 1 and \
planned_segments empty — even for longer sentences. Do not propose your own \
multi-shot breakdown; if a sentence turns out too long for a single shot, the \
editing system will handle the technical split automatically using your \
primary/backup/second_backup choices.""",
    SEGMENT_ASSET_PLANNING_MODE_PER_SEGMENT: """\
## Shot planning for this location: split into multiple shots where it helps
For this location, actively look for sentences/beats that describe more than \
one distinct visual idea, or that will run long when spoken. For those, set \
visual_asset_plan.preferred_cut_count to the number of shots it should \
become, and fill planned_segments with one entry per shot (segment_order \
starting at 1), each with its own genuinely fitting asset — prefer a \
DIFFERENT asset per shot wherever the location's material allows it. Use \
this multi-shot planning generously: more variety across the section is \
preferred over holding a single shot for the entire sentence, as long as \
each individual shot's asset still genuinely matches that portion of the \
sentence.""",
    SEGMENT_ASSET_PLANNING_MODE_LLM_DISCRETION: """\
## Shot planning for this location: your judgment — varied, but never restless
For this location, decide sentence by sentence whether it plays best as ONE \
calm, steady shot or as a multi-shot split (preferred_cut_count > 1 with \
planned_segments). Balance two goals:
- Variety across the section: don't hold the exact same single-shot pacing \
for every sentence in a row — some visual rhythm change over the section \
reads better than a flat, uniform rhythm.
- Calm, not restless: within a single sentence, do NOT cut between shots \
just to add movement. Reserve a multi-shot split for a sentence that \
genuinely describes multiple distinct visual beats, or that clearly runs \
long when spoken — a short, simple, atmospheric sentence should almost \
always stay one calm shot, even if you have several genuinely fitting \
assets available for it.
When in doubt, prefer the calmer option (single shot).""",
}


def _segment_asset_planning_block(mode: str) -> str:
    """Phase 7.1 (Asset-bewusste Cut-Plan-Vorbereitung): liefert den zum
    gewählten Segment-Planungsmodus (FolderVoiceoverSetting.
    segment_asset_planning_mode) passenden Prompt-Baustein. Ein unbekannter/
    leerer Wert fällt sicher auf PER_SENTENCE zurück (heutiges Verhalten) —
    schützt davor, dass ein ungültiger Wert versehentlich aktives
    Multi-Shot-Planen auslöst."""
    return _SEGMENT_ASSET_PLANNING_BLOCKS.get(
        mode, _SEGMENT_ASSET_PLANNING_BLOCKS[SEGMENT_ASSET_PLANNING_MODE_PER_SENTENCE]
    )


def _active_negative_rules_block(project_brief: ProjectBrief) -> str:
    """Rendert die aktiven Negativregel-Flags als selbsterklärenden Block —
    jede Zeile beginnt mit dem kompakten Regel-Key (Rückwärtskompatibilität
    für Code/Tests, die gezielt nach dem Key suchen), gefolgt von der
    ausführlichen, an das LLM gerichteten Formulierung. Macht die Regeln für
    Mensch UND LLM nachvollziehbar, statt nur kryptische Keys aneinanderzureihen."""
    active_flags = [flag for flag, enabled in project_brief.negative_rule_flags.items() if enabled]
    if not active_flags:
        return "(keine Angabe)"
    lines = []
    for flag in active_flags:
        instruction = BRIEF_NEGATIVE_RULE_INSTRUCTIONS.get(flag)
        lines.append(f"- {flag}: {instruction}" if instruction else f"- {flag}")
    return "\n".join(lines)


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
    active_negative_rules = _active_negative_rules_block(project_brief)
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
- Additional editor instructions: {project_brief.global_extra_prompt or "(none)"}

## Active negative rules (MUST be respected)
{active_negative_rules}

## Global negative rules (free text)
{project_brief.negative_rules_freetext or "(none)"}

## Forbidden phrases
{forbidden_phrases}

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
    active_negative_rules = _active_negative_rules_block(project_brief)

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
- Additional editor instructions: {project_brief.global_extra_prompt or "(none)"}

## Active negative rules (MUST be respected)
{active_negative_rules}

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
      "recommended_word_count": 135,
      "recommended_min_words": 120,
      "recommended_max_words": 150,
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
    active_negative_rules = _active_negative_rules_block(project_brief)
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

## Active global negative rules (MUST be respected)
{active_negative_rules}

## Global negative rules (free text)
{project_brief.negative_rules_freetext or "(none)"}

## Forbidden phrases (global + style + folder must_avoid)
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
- use a transition from the previous location (as a segue near the START of \
this section): {setting.transition_from_previous}
- end this section with a brief teaser toward "{next_folder_name or "-"}", which is \
the VERY NEXT section of the video (immediately after this one, not later, not \
eventually — the viewer will see it right after this): {setting.transition_to_next}. \
Use the transition goal above. Do NOT reveal details about it, but ALSO do not use \
deferral language that implies it is far away or will be covered "later"/"eventually" \
in the video (e.g. avoid phrasing like "von der später noch die Rede sein wird", \
"later in this video", "eventually", "in due time") — it comes right after this section.
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

## Visual editing awareness (read this before writing)
This voice-over will later be CUT together with the local video/photo assets listed \
above, one shot per sentence/beat (long sentences get split into multiple shots). \
Write with that edit in mind:
- Develop sentences FROM the available local assets where it makes sense — use them \
as visual grounding, not as a checklist to describe one by one.
- Do NOT omit an important narrative detail just because no local asset shows it. If \
a detail matters to the story but nothing here covers it, still write it — mark that \
sentence/beat needs_supplement_asset=true with a concrete supplement_reason instead \
of forcing a weak or wrong local asset onto it.
- Avoid assigning the SAME asset as primary_asset_id to two sentences/beats in a row. \
If the same subject naturally continues across sentences, vary which asset carries \
it (e.g. swap primary/backup, or use a different asset) instead of repeating the \
identical shot back-to-back — that reads as a frozen/stuck image on screen.
- Where more than one asset genuinely fits a sentence/beat, put the best match in \
primary_asset_id and add one or two further plausible, DIFFERENT assets to \
backup_asset_ids — this gives the later edit real alternatives instead of a single \
point of failure (especially important if that same subject also needs to avoid the \
back-to-back repetition rule above).
- A sentence/beat long enough to need splitting into multiple shots later needs \
enough distinct, usable local coverage for that split (a primary AND at least one \
different, usable backup). If this location's material can't support that, prefer \
several SHORTER sentences/beats — each cleanly covered by one asset — over one long \
sentence that outruns the available visual material.
- visual_intent must state a concrete visual purpose (what should be visible, the \
mood/movement/shot type it implies) — not a generic restatement of the sentence text.
- If, beyond primary_asset_id and backup_asset_ids, there is a FURTHER local asset \
that still genuinely fits this sentence/beat (even if broader/more atmospheric), add \
it to second_backup_asset_ids. This must still be a real, plausible visual match for \
this specific sentence/beat — NEVER add an asset there just to fill the list. If \
nothing else genuinely fits, leave second_backup_asset_ids empty and rely on \
needs_supplement_asset instead — a weak filler asset is worse than an honest gap.
- Optionally fill visual_asset_plan to make your own editing reasoning explicit: \
preferred_cut_count (how many distinct shots this sentence/beat should ideally become, \
1 if it's a single shot), reuse_risk ("low"/"medium"/"high" — how risky reusing \
primary_asset_id nearby would be, given how few local alternatives exist for this \
subject), needs_visual_variety (true if this sentence/beat would look monotonous \
without switching visuals), asset_strategy_reason (why you assigned assets this way), \
and — ONLY if needs_supplement_asset is true — supplement_search_hint: a concrete, \
location-prefixed search phrase for finding this missing visual externally (e.g. \
"Havasu Falls waterfall woman", not just "waterfall").

## Asset allocation across this whole location (read this carefully)
Treat the local assets listed above as a SCARCE shared resource for the ENTIRE \
location, not something to decide sentence by sentence. Before finalizing your \
assignments, think about ALL sentences/beats (and the closing shot below) together:
- Keep each asset_id's TOTAL number of occurrences in your entire response at or \
below 3. Count EVERY occurrence anywhere — primary_asset_id, backup_asset_ids, \
second_backup_asset_ids, planned_segments, AND closing_visual_plan all count toward \
this same total. A backup slot is not a safe dumping ground: only put an asset in \
backup_asset_ids/second_backup_asset_ids if you would genuinely be fine with it \
actually appearing on screen there.
- Keep at least 4 shot positions between two occurrences of the same asset_id \
(counting sentence/beat order, including planned_segments as their own positions, \
with the closing shot as the last position). Do not place the same asset again \
within that distance.
- Resolve competition for the same asset by scarcity, not by which sentence you \
happen to be writing first: if one sentence/beat has only ONE genuinely fitting \
local asset while another sentence/beat could use several different plausible \
assets, the sentence with only one option KEEPS that asset. The sentence/beat with \
multiple options must use a different one of its alternatives instead — and if its \
remaining alternatives are only weakly fitting, set needs_supplement_asset=true for \
that MORE FLEXIBLE sentence/beat rather than taking the scarce asset away from the \
one that has no alternative.
- Never resolve scarcity by silently violating the total-occurrence or shot-distance \
rules above, and never resolve it by forcing a weak/wrong asset onto a sentence — in \
both cases, prefer needs_supplement_asset=true with a concrete supplement_reason.

{_segment_asset_planning_block(setting.segment_asset_planning_mode)}

## Closing shot for this location (required)
After the LAST sentence/beat, this location needs exactly one additional, purely \
visual closing shot — no spoken text, no TTS, no sentence_id — that will visually \
hold the screen while the voice-over of the NEXT location has not started yet \
(covering the trailing silence after the last sentence AND the pause before the \
next location). Plan this as closing_visual_plan:
- primary_asset_id/backup_asset_ids/second_backup_asset_ids follow the exact same \
rules as for a sentence/beat above (EXACT asset_id values only, or empty) and count \
toward the same total-occurrence/shot-distance budget above, as the LAST position.
- The closing shot's primary_asset_id (and ideally its backups too) MUST NOT be the \
SAME asset_id as the primary_asset_id of the LAST sentence/beat, NOR the SAME \
asset_id as the primary_asset_id of the SECOND-TO-LAST sentence/beat — it must \
visually read as a distinct beat, not a continuation of the same shot.
- Prefer a VIDEO over a photo for the closing shot wherever a genuinely fitting \
video exists — a closing shot benefits from a few extra seconds of real motion \
rather than a static hold.
- Prefer a calm aerial/drone shot, a wide establishing shot, or another calm, \
scenic/atmospheric shot for the closing — avoid a tight detail shot or anything with \
busy/hectic motion, which reads poorly when held slightly longer than a normal shot.
- If nothing in the local inventory genuinely fits as a distinct, calm closing shot \
(respecting the last-two-sentences exclusion above), set needs_supplement_asset=true \
with a concrete supplement_reason and a location-prefixed supplement_search_hint \
(e.g. "Antelope Canyon aerial wide shot") — do not force a weak or repeated asset \
just to fill this field.

## Task
Write ONE flowing documentary voice-over text for this location (target_words above), \
then break it into sentence_items — one entry per sentence or narrative beat — each \
with a visual assignment.

Rules for sentence_items:
- Every sentence/beat needs an asset assignment OR needs_supplement_asset=true.
- primary_asset_id MUST be one of the EXACT asset_id values listed above, or empty.
- Never invent asset IDs that are not in the list above.
- backup_asset_ids MUST also only contain asset_id values from the list above.
- second_backup_asset_ids MUST also only contain asset_id values from the list above \
(or stay empty — see "Visual editing awareness" above for when to use it).
- planned_segments (if used) MUST also only reference asset_id values from the list \
above, and segment_order MUST start at 1 and be unique within that sentence/beat.
- If no asset fits a sentence, set primary_asset_id to "", needs_supplement_asset=true, \
and give a concrete supplement_reason (what visual is missing).
- Do not assign the same primary_asset_id to two consecutive sentence_items unless \
there is genuinely no other usable local asset for the second one — see "Visual \
editing awareness" above.
- Respect the folder-wide asset allocation rules above: at or below 3 total \
occurrences per asset_id across the ENTIRE response, at least 4 shot positions \
between repeated occurrences, and scarce assets reserved for the sentence/beat that \
has no real alternative.
- asset_confidence: 0.0-1.0, honestly reflecting how well the asset matches the sentence.
- Not every asset in the inventory needs to be used.
- Optionally set pause_after on a sentence_item to mark a deliberate narrative \
pause AFTER that sentence — one of {list(PAUSE_AFTER_CHOICES)} ("" = no pause, \
"short"/"medium"/"long" = increasingly longer pause). Use pauses sparingly, only \
at genuine dramatic beats (e.g. after a striking statement, before a topic shift) \
— not after every sentence. Never set pause_after on the LAST sentence_item.

Rules for closing_visual_plan (see "Closing shot for this location" above):
- primary_asset_id/backup_asset_ids/second_backup_asset_ids MUST also only reference \
EXACT asset_id values from the inventory above, or stay empty.
- primary_asset_id MUST NOT equal the primary_asset_id of the last or second-to-last \
sentence_items.
- If no genuinely fitting, distinct, calm local asset exists, set primary_asset_id \
to "", needs_supplement_asset=true, and give a concrete supplement_reason.

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
      "second_backup_asset_ids": [],
      "asset_match_reason": "...",
      "asset_confidence": 0.0,
      "estimated_duration_sec": 0.0,
      "must_show": [],
      "avoid_showing": [],
      "needs_supplement_asset": false,
      "supplement_reason": "",
      "source_inventory_asset_ids_considered": [],
      "pause_after": "",
      "visual_asset_plan": {{
        "preferred_cut_count": 1,
        "reuse_risk": "",
        "needs_visual_variety": false,
        "asset_strategy_reason": "...",
        "supplement_search_hint": ""
      }},
      "planned_segments": []
    }}
  ],
  "closing_visual_plan": {{
    "visual_intent": "...",
    "primary_asset_id": "...",
    "backup_asset_ids": [],
    "second_backup_asset_ids": [],
    "needs_supplement_asset": false,
    "supplement_reason": "",
    "supplement_search_hint": "",
    "asset_strategy_reason": "..."
  }},
  "transition_from_previous_used": false,
  "transition_to_next_used": false,
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

Keep respecting the visual editing awareness from the original assignment: don't \
assign the same primary_asset_id to two consecutive sentence_items unless there is \
genuinely no other usable local asset, keep needs_supplement_asset=true (with a \
concrete supplement_reason) for beats that need a visual not covered here rather \
than omitting the detail, and only keep a long sentence/beat if it still has enough \
distinct, usable local coverage for a later split. Only add an asset to \
second_backup_asset_ids if it still genuinely fits that specific sentence/beat — \
never as filler.

{_segment_asset_planning_block(setting.segment_asset_planning_mode)}

If a sentence/beat has planned_segments (per-shot asset planning), keep it \
consistent with any rewritten text — remove/adjust segments that no longer apply, \
but never invent a segment's asset just to fill it.

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
      "second_backup_asset_ids": [],
      "asset_match_reason": "...",
      "asset_confidence": 0.0,
      "estimated_duration_sec": 0.0,
      "must_show": [],
      "avoid_showing": [],
      "needs_supplement_asset": false,
      "supplement_reason": "",
      "source_inventory_asset_ids_considered": [],
      "pause_after": "",
      "visual_asset_plan": {{
        "preferred_cut_count": 1,
        "reuse_risk": "",
        "needs_visual_variety": false,
        "asset_strategy_reason": "...",
        "supplement_search_hint": ""
      }},
      "planned_segments": []
    }}
  ],
  "transition_from_previous_used": false,
  "transition_to_next_used": false,
  "callback_to_previous_used": false,
  "contrast_or_commonality_used": false,
  "risks": []
}}
"""


def build_asset_allocation_correction_prompt(
    *,
    project_brief: ProjectBrief,
    style_profile: VoiceoverStyleProfile | None,
    setting: FolderVoiceoverSetting,
    draft: FolderVoiceoverDraft,
    inventory_assets: list[dict],
    issues: list[SentenceAssetReadinessIssue],
) -> str:
    """Nutzervorgabe (Juli 2026): eigenständiger Correction-Prompt für die
    Asset-READINESS-Diagnose (siehe folder_asset_readiness.py) — bewusst
    GETRENNT von build_voiceover_correction_prompt (Text-/Stil-Review), da
    hier NUR die Asset-Zuordnung (inkl. Closing Shot) repariert werden
    soll, der redaktionelle Text möglichst unverändert bleibt.

    Erwartet dieselbe Response-Shape wie der Autor-Prompt (voiceover_text_full
    + sentence_items + closing_visual_plan + ...) — der Aufrufer nutzt
    denselben Parser/Sanitizer wie generate_folder_voiceover."""
    issues_block = "\n".join(
        f"- [{issue.issue_type}] (sentence_id={issue.sentence_id or '(folder-level)'}) {issue.message}"
        for issue in issues
    ) or "(keine Issues übergeben)"

    sentence_lines = "\n".join(
        f"- {item.sentence_id}: text=\"{item.text}\" primary_asset_id={item.primary_asset_id!r} "
        f"backup_asset_ids={item.backup_asset_ids!r} second_backup_asset_ids={item.second_backup_asset_ids!r} "
        f"source_inventory_asset_ids_considered={item.source_inventory_asset_ids_considered!r} "
        f"needs_supplement_asset={item.needs_supplement_asset}"
        for item in draft.sentence_items
    ) or "(keine sentence_items)"

    closing = draft.closing_visual_plan
    closing_line = (
        f"primary_asset_id={closing.primary_asset_id!r} backup_asset_ids={closing.backup_asset_ids!r} "
        f"second_backup_asset_ids={closing.second_backup_asset_ids!r} "
        f"needs_supplement_asset={closing.needs_supplement_asset}"
    )

    return f"""You previously assigned local assets to the sentences and closing shot of \
ONE location below, but a deterministic allocation check found problems — this is a \
DEDICATED asset-allocation repair pass, not a general rewrite.

## Target language
{project_brief.language}

## Style Profile
{_style_summary_block(style_profile)}

## Inventory for this location (asset_id values are EXACT — never invent new ones)
{_inventory_asset_block(inventory_assets)}

## Original voice-over text (keep as unchanged as possible)
{draft.voiceover_text_full}

## Original sentence/beat breakdown (current asset assignment)
{sentence_lines}

## Original closing shot (current asset assignment)
{closing_line}

## Asset allocation problems that MUST be fixed
{issues_block}

## Task
Fix ONLY the asset allocation — do NOT rewrite the voice-over text or restructure \
sentences/beats unless an issue explicitly requires it (e.g. a sentence/beat that \
must now request supplement instead of using a scarce asset). Apply the SAME rules \
as the original assignment:
- Keep each asset_id's TOTAL occurrences (primary/backup/second_backup/
planned_segments/closing_visual_plan combined) at or below 3.
- Keep at least 4 shot positions between two occurrences of the same asset_id.
- When two sentences/beats compete for the same asset, the one with fewer genuinely \
fitting local alternatives keeps it; the one with more alternatives must switch to a \
different alternative or set needs_supplement_asset=true instead of taking the \
scarce asset away from the sentence/beat that has no alternative.
- The closing shot's primary_asset_id must NOT equal the primary_asset_id of the \
last or second-to-last sentence/beat, and should prefer a video over a photo and a \
calm aerial/wide/establishing shot over a tight/busy one; if nothing local fits, set \
closing_visual_plan.needs_supplement_asset=true with a concrete supplement_reason and \
supplement_search_hint.
- Never invent asset IDs that are not in the inventory list above.
- Never force a weak/wrong asset onto a sentence/beat or the closing shot just to \
avoid needs_supplement_asset — an honest supplement request is always preferable.

{_segment_asset_planning_block(setting.segment_asset_planning_mode)}

Respond with JSON ONLY, no markdown code fences, no commentary, using the EXACT \
same shape as the original assignment:

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
      "second_backup_asset_ids": [],
      "asset_match_reason": "...",
      "asset_confidence": 0.0,
      "estimated_duration_sec": 0.0,
      "must_show": [],
      "avoid_showing": [],
      "needs_supplement_asset": false,
      "supplement_reason": "",
      "source_inventory_asset_ids_considered": [],
      "pause_after": "",
      "visual_asset_plan": {{
        "preferred_cut_count": 1,
        "reuse_risk": "",
        "needs_visual_variety": false,
        "asset_strategy_reason": "...",
        "supplement_search_hint": ""
      }},
      "planned_segments": []
    }}
  ],
  "closing_visual_plan": {{
    "visual_intent": "...",
    "primary_asset_id": "...",
    "backup_asset_ids": [],
    "second_backup_asset_ids": [],
    "needs_supplement_asset": false,
    "supplement_reason": "",
    "supplement_search_hint": "",
    "asset_strategy_reason": "..."
  }},
  "transition_from_previous_used": {str(draft.transition_from_previous_used).lower()},
  "transition_to_next_used": {str(draft.transition_to_next_used).lower()},
  "callback_to_previous_used": {str(draft.callback_to_previous_used).lower()},
  "contrast_or_commonality_used": {str(draft.contrast_or_commonality_used).lower()},
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
    active_negative_rules = _active_negative_rules_block(project_brief)
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
- Core narrative arc: {dramaturgy_plan.narrative_arc or "-"}
- Core promise: {dramaturgy_plan.core_promise or "-"}

## Active global negative rules (MUST be respected)
{active_negative_rules}

## Forbidden phrases (global + style + hook settings)
{forbidden_block}

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
