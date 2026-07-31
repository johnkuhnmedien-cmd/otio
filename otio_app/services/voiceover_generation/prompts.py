"""Prompt-Builder für die Voice-over-Generierungs-Pipeline.

Phase 2: build_style_profile_prompt()
Phase 3: build_dramaturgy_prompt()
Phase 4: build_folder_voiceover_prompt(), build_voiceover_review_prompt(),
         build_voiceover_correction_prompt()
"""

from __future__ import annotations

from otio_app.defaults import (
    BRIEF_NEGATIVE_RULE_INSTRUCTIONS,
    DRAMATURGY_PLANNING_MODE_GEOGRAPHY,
    DRAMATURGY_PLANNING_MODE_VARIETY,
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


_LANGUAGE_DISPLAY_NAMES: dict[str, str] = {
    "DE": "German",
    "EN": "English",
    "FR": "French",
    "ES": "Spanish",
    "PT": "Portuguese",
    "IT": "Italian",
    "de": "German",
    "en": "English",
    "fr": "French",
    "es": "Spanish",
    "pt": "Portuguese",
    "it": "Italian",
}


def _language_display_name(language: str) -> str:
    raw = (language or "").strip()
    if not raw:
        return "German"
    return _LANGUAGE_DISPLAY_NAMES.get(raw) or _LANGUAGE_DISPLAY_NAMES.get(raw.lower()) or raw


def native_speaker_language_block(language: str) -> str:
    """Gemeinsame Zielsprachen- + Native-Speaker-Regel für alle Text-Prompts.

    Inventar-Beschreibungen dürfen SHARED und in einer anderen Sprache sein —
    das LLM darf daraus nur Inhalt entnehmen, keine Formulierungen übernehmen.
    """
    code = (language or "").strip() or "DE"
    display = _language_display_name(code)
    return f"""## Target language & native-speaker rule (MANDATORY)
- Target language code: {code}
- Write ALL narration / reasons / transitions / hook text in {display}, \
as a NATIVE SPEAKER of {display} would write for a polished documentary.
- Do NOT translate word-for-word from inventory or asset descriptions.
- Inventory / asset descriptions are CONTENT SOURCE ONLY (what is visible, \
places, motifs). They may be written in another language or in dry analysis \
style — NEVER copy their phrasing, sentence rhythm, or wording into the \
voice-over. Re-express the meaning freshly in natural {display}.
- Do NOT mix languages. Do NOT leave untranslated fragments from the inventory."""


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

{native_speaker_language_block(project_brief.language)}

## Project
- Video title: {project_brief.video_title or "(untitled)"}
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
    """Kapitel-/Ordner-Kurzinfo für die Dramaturgie — ohne Asset-Beschreibungen.

    Pro Kapitel nur Name, Themen und grobe Kapazitäts-Signale. Volle
    Asset-Beschreibungen blähen den Prompt bei vielen Ordnern unnötig auf und
    gehören in spätere Voice-over-/Cut-Plan-Schritte, nicht in die
    Reihenfolge-Planung.
    """
    themes = ", ".join(summary.dominant_visual_themes) or "-"
    risks = ", ".join(summary.risks) or "-"
    return (
        f"[{summary.folder_name}]\n"
        f"- chapter_themes: {themes}\n"
        f"- asset_count: {summary.asset_count} "
        f"(video: {summary.video_count}, image: {summary.image_count})\n"
        f"- visual_strength_score: {summary.visual_strength_score}\n"
        f"- asset_diversity_score: {summary.asset_diversity_score}\n"
        f"- risks: {risks}"
    )


def _dramaturgy_planning_mode_task_block(planning_mode: str) -> str:
    mode = (planning_mode or DRAMATURGY_PLANNING_MODE_VARIETY).strip().lower()
    if mode == DRAMATURGY_PLANNING_MODE_GEOGRAPHY:
        return """## Planning mode: GEOGRAPHY FIRST
Order the chapters primarily by GEOGRAPHY and a coherent travel journey:
- Infer regions, routes, coasts, interiors, north→south / west→east from chapter \
names and themes when possible.
- Prefer a logical travel progression; minimize jarring geographic jumps.
- Still assign dramaturgy roles (opener/contrast/climax/resolution) and \
transitions, but geographic coherence outweighs pure visual-strength sorting.
- A strong visual chapter may open the film only if it also fits the journey, \
or after a short geographic setup.

Do NOT simply sort alphabetically. Do NOT simply sort by asset count."""

    return """## Planning mode: MAXIMUM VARIETY
Order the chapters for MAXIMUM VARIETY and narrative interest:
- Actively alternate mood, scale, landscape type, and visual character between \
neighboring chapters.
- Prioritize hook potential and overall tension arc over a strict \
geographic travel sequence.
- Geographic adjacency is allowed only when it also keeps the film interesting.
- Avoid long runs of similar chapters (e.g. several deserts or cities in a row).
- Variety in ORDER does NOT mean labeling most chapters as role "contrast".

Do NOT simply sort alphabetically. Do NOT simply sort by asset count. Do NOT \
default to a pure map route if that makes the film monotonous."""


def build_dramaturgy_prompt(
    *,
    project_brief: ProjectBrief,
    style_profile: VoiceoverStyleProfile | None,
    folder_summaries: list[FolderInventorySummary],
    model_settings: VoiceoverGenerationModelSettings | None = None,
    planning_mode: str | None = None,
    style_context_text: str | None = None,
) -> str:
    """Baut den Prompt zur Dramaturgieplanung über alle Ordner/Kapitel.

    `model_settings` ist Teil der Signatur für API-Symmetrie mit den anderen
    Rollen, wird aber aktuell nicht in den Prompt-Text eingebettet — welches
    Modell aufgerufen wird, ist eine Aufrufer-Entscheidung, kein Prompt-Inhalt.

    `planning_mode`: "geography" (Reise/Geographie zuerst) oder "variety"
    (Abwechslung/Kontrast zuerst). Default: variety.

    `style_context_text`: optional vorformatierter Stilblock (z. B. Raw Text).
    """
    del model_settings

    resolved_mode = (planning_mode or DRAMATURGY_PLANNING_MODE_VARIETY).strip().lower()

    tone_tags = ", ".join(project_brief.tone_tags) or "(keine Angabe)"
    active_negative_rules = _active_negative_rules_block(project_brief)

    if style_context_text is not None:
        style_block = style_context_text
    elif style_profile is not None:
        style_block = (
            f"- overall_tone: {style_profile.overall_tone or '-'}\n"
            f"- narration_style: {style_profile.narration_style or '-'}\n"
            f"- pacing: {style_profile.pacing or '-'}\n"
            f"- intro_hook_style: {style_profile.intro_hook_style or '-'}\n"
            f"- style_summary_for_prompts: {style_profile.style_summary_for_prompts or '-'}"
        )
    else:
        style_block = "(kein Style Profile vorhanden — neutraler Standardstil)"

    folders_block = (
        "\n\n".join(_folder_summary_block(summary) for summary in folder_summaries)
        or "(keine Kapitel verfügbar)"
    )
    planning_mode_block = _dramaturgy_planning_mode_task_block(resolved_mode)

    return f"""You are a documentary story editor. Plan the DRAMATURGY (narrative \
structure) of a travel/nature documentary across multiple CHAPTERS (folders / \
locations). This is NOT about describing individual media assets — it is about \
ORDER, TENSION ARC, and the ROLE each chapter plays in the overall video.

{native_speaker_language_block(project_brief.language)}

## Project
- Project title: {project_brief.video_title or "(untitled)"}
- Desired tone tags: {tone_tags}
- Additional editor instructions: {project_brief.global_extra_prompt or "(none)"}

## Active negative rules (MUST be respected)
{active_negative_rules}

## Style Profile (already extracted — do not re-derive, just respect it)
{style_block}

## Chapters / locations (chapter-level only — NO per-asset descriptions)
Each block is one chapter (folder name). Use these chapter names as the only \
location identifiers. Do not invent assets or quote asset descriptions.
{folders_block}

{planning_mode_block}

## Task
Decide, for the whole set of chapters above:
- Which chapter works best as the OPENER (hooks attention immediately)?
- Which chapter works as the CLIMAX / escalation point?
- Which chapter works as a calm RESOLUTION / closer?
- What is the most compelling overall narrative arc connecting them?
- For EACH chapter: its role, a short reason, and a recommended voice-over \
word count.

### Role distribution (IMPORTANT — avoid overusing "contrast")
Allowed roles: opener | setup | contrast | escalation | climax | resolution
- Prefer setup / escalation for most middle chapters.
- Use role "contrast" SPARINGLY — only for a few chapters where the place \
truly flips mood/scale vs. its neighbors (rough guide: at most ~1 in 6 chapters, \
never a long run of contrast roles).
- Do NOT assign "contrast" just because neighboring chapters differ; order \
already creates variety.
- Exactly one opener when possible; climax/resolution reserved for true peaks/ends.

### Voice-over length per chapter (IMPORTANT — decide freely, do not copy a grid)
Baseline orientation: about 150 words per chapter, with a hard band of \
120–180 words (±30 around the baseline).
YOU choose the exact target for each chapter based on narrative need:
- richer story / higher interest / climax / opener with more to say → toward 160–180
- thinner material / calmer bridge / less to report → toward 120–140
- typical middle chapters → near 150
Do NOT use rigid fixed pairs like only 115 or only 165. Vary targets across \
chapters when the story justifies it. Then set:
- recommended_word_count = your chosen target (integer in 120–180)
- recommended_min_words = target − 30 (not below 120)
- recommended_max_words = target + 30 (not above 180)
Asset counts are only weak capacity signals — they must NOT dictate word count.

Do NOT output per-chapter transition/callback/contrast checkboxes, hint strings, \
or craft flags. Those optional editor controls are handled outside this prompt and \
must not appear in the JSON (saves tokens).

Write core_promise, narrative_arc, reasons, and risks in the target language \
(native-speaker quality). Folder/chapter names stay exactly as given.

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
      "recommended_word_count": 150,
      "recommended_min_words": 120,
      "recommended_max_words": 180,
      "risks": []
    }}
  ],
  "risks": []
}}

Include exactly one entry per chapter listed above, using the EXACT folder_name \
values given. order_index must be unique and start at 1.
"""


def _style_summary_block(
    style_profile: VoiceoverStyleProfile | None,
    *,
    style_context_text: str | None = None,
) -> str:
    if style_context_text is not None:
        return style_context_text
    from otio_app.services.voiceover_generation.style_reference_service import (
        format_style_profile_summary_for_prompts,
    )

    return format_style_profile_summary_for_prompts(style_profile)


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


def _folder_craft_flags_active(setting: FolderVoiceoverSetting) -> bool:
    return any(
        (
            setting.transition_from_previous,
            setting.transition_to_next,
            setting.callback_to_previous,
            setting.use_contrast_with_previous,
            setting.use_commonality_with_previous,
        )
    )


def _folder_location_craft_block(
    *,
    dramaturgy_entry: DramaturgyFolderEntry,
    setting: FolderVoiceoverSetting,
    previous_folder_name: str | None,
    next_folder_name: str | None,
) -> str:
    """Übergangs-/Craft-Instruktionen — nur wenn mindestens ein Flag aktiv ist."""
    if not _folder_craft_flags_active(setting):
        return (
            "- voice-over craft flags: none active for this location — write a "
            "self-contained section without forced bridges, teasers, callbacks, "
            "contrast, or commonality toward neighboring chapters"
        )

    lines = [
        f"- transition goal toward the NEXT location: "
        f"{dramaturgy_entry.transition_goal_to_next or '-'}",
        f"- transition-from-previous hint: "
        f"{dramaturgy_entry.transition_from_previous_hint or '-'}",
        f"- contrast/commonality hint: "
        f"{dramaturgy_entry.contrast_or_commonality_hint or '-'}",
        (
            "- use a transition from the previous location (as a segue near the START of "
            f"this section): {setting.transition_from_previous}"
        ),
        (
            f'- end this section with a brief teaser toward "{next_folder_name or "-"}", which is '
            "the VERY NEXT section of the video (immediately after this one, not later, not "
            "eventually — the viewer will see it right after this): "
            f"{setting.transition_to_next}. "
            "Use the transition goal above. Do NOT reveal details about it, but ALSO do not use "
            "deferral language that implies it is far away or will be covered \"later\"/\"eventually\" "
            "in the video (e.g. avoid phrasing like \"von der später noch die Rede sein wird\", "
            '"later in this video", "eventually", "in due time") — it comes right after this section.'
        ),
        f"- callback to the previous location later in the text: {setting.callback_to_previous}",
        f"- use a contrast with the previous location: {setting.use_contrast_with_previous}",
        f"- use a commonality with the previous location: {setting.use_commonality_with_previous}",
    ]
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
    style_context_text: str | None = None,
) -> str:
    """Baut den Prompt für die Erzeugung des Voice-over-Textes EINES Ordners.

    Fordert echte Doku-Prosa (kein Assetlisten-Stil) UND eine vollständige,
    strukturierte Satz-/Beat-zu-Asset-Zuordnung im selben JSON-Response."""
    tone_tags = ", ".join(project_brief.tone_tags) or "(keine Angabe)"
    active_negative_rules = _active_negative_rules_block(project_brief)
    forbidden_phrases = _combined_forbidden_phrases(project_brief, style_profile, setting)
    forbidden_block = "\n".join(f"- {phrase}" for phrase in forbidden_phrases) or "(keine)"
    must_include_block = ", ".join(setting.must_include) or "(keine Angabe)"
    craft_block = _folder_location_craft_block(
        dramaturgy_entry=dramaturgy_entry,
        setting=setting,
        previous_folder_name=previous_folder_name,
        next_folder_name=next_folder_name,
    )

    return f"""You are a documentary narration writer. Write the voice-over section \
for ONE location in a multi-location travel/nature documentary.

Do not merely describe the assets. Write polished documentary narration. Use the \
assets only as visual grounding for each sentence or beat.

WRONG (asset description): "You see a canyon with red rocks."
RIGHT (documentary prose): "Between the red rock walls, the light seems to make \
the stone glow from within."

{native_speaker_language_block(project_brief.language)}

## Project
- Project title: {project_brief.video_title or "(untitled)"}
- Desired tone tags: {tone_tags}

## Active global negative rules (MUST be respected)
{active_negative_rules}

## Global negative rules (free text)
{project_brief.negative_rules_freetext or "(none)"}

## Forbidden phrases (global + style + folder must_avoid)
{forbidden_block}

## Style Profile (respect it — do not copy any reference text)
{_style_summary_block(style_profile, style_context_text=style_context_text)}

## This location
- folder_name: {dramaturgy_entry.folder_name}
- dramaturgy_role: {dramaturgy_entry.dramaturgy_role}
- reason for this role: {dramaturgy_entry.reason or "-"}
- previous location in the video: {previous_folder_name or "(none — this is the first location)"}
- next location in the video: {next_folder_name or "(none — this is the last location)"}
{craft_block}
- factuality mode: {setting.factuality_mode} (strict_inventory_only = only claim what \
is visible in the assets below; normal_safe_general_knowledge = safe, well-known \
general facts allowed; atmospheric_no_hard_facts = avoid factual claims entirely, \
stay purely atmospheric/sensory)
- energy: {setting.energy}
- must include (topics/ideas, not literal phrases): {must_include_block}
- editor's extra instructions for this location: {setting.folder_extra_prompt or "(none)"}

## Target length
- target_words: {setting.target_words} (min {setting.min_words}, max {setting.max_words})

## Inventory for this location (CONTENT SOURCE ONLY — asset_id values are EXACT)
## Do not copy inventory wording; re-express meaning in the target language.
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
    style_context_text: str | None = None,
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

{native_speaker_language_block(project_brief.language)}

## Style Profile (the text should match this style)
{_style_summary_block(style_profile, style_context_text=style_context_text)}

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
    style_context_text: str | None = None,
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

{native_speaker_language_block(project_brief.language)}

## Style Profile
{_style_summary_block(style_profile, style_context_text=style_context_text)}

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
    style_context_text: str | None = None,
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

{native_speaker_language_block(project_brief.language)}

## Style Profile
{_style_summary_block(style_profile, style_context_text=style_context_text)}

## Inventory for this location (CONTENT SOURCE ONLY — asset_id values are EXACT)
## Do not copy inventory wording; re-express meaning in the target language.
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


def _intro_chapter_signal_block(entry: DramaturgyFolderEntry) -> str:
    """Kurzes Kapitel-Signal für den Intro-Prompt — ohne Fließtext/Skript."""
    return (
        f"- folder_name: {entry.folder_name}\n"
        f"  order_index: {entry.order_index}\n"
        f"  dramaturgy_role: {entry.dramaturgy_role or '-'}\n"
        f"  reason: {entry.reason or '-'}\n"
        f"  hook_potential_score: {entry.hook_potential_score}\n"
        f"  visual_strength_score: {entry.visual_strength_score}"
    )


def _folder_voiceover_block(
    entry: DramaturgyFolderEntry | None,
    draft: FolderVoiceoverDraft,
    inventory_assets: list[dict] | None = None,
) -> str:
    """Kompatibilitätshülle — Intro nutzt nur noch Dramaturgie-Signale."""
    del inventory_assets
    if entry is not None:
        return _intro_chapter_signal_block(entry)
    return (
        f"- folder_name: {draft.folder_name}\n"
        f"  order_index: {draft.order_index}\n"
        f"  dramaturgy_role: -\n"
        f"  reason: -\n"
        f"  hook_potential_score: 0\n"
        f"  visual_strength_score: 0"
    )


def build_intro_hook_prompt(
    *,
    project_brief: ProjectBrief,
    style_profile: VoiceoverStyleProfile | None,
    dramaturgy_plan: DramaturgyPlan,
    confirmed_folder_voiceovers: list[FolderVoiceoverDraft],
    settings: IntroHookSettings,
    inventory_by_folder: dict[str, list[dict]] | None = None,
    style_context_text: str | None = None,
) -> str:
    """Baut den Prompt für genau 5 Intro-Inhaltsvarianten.

    Eine gemeinsame STRUCTURE (aus Raw-Intro-Referenz / Style) — fünf
    unterschiedliche Inhaltswahlen. Quelle: nur kurze Kapitel-Signale aus der
    Dramaturgie (Name, Rolle, Reason, Scores) — **kein** Fließtext, keine
    Folder-VO-Sätze, kein Inventory.
    """
    del inventory_by_folder
    entries_by_folder = {
        entry.folder_name: entry for entry in dramaturgy_plan.recommended_folder_order
    }
    ready_names = [draft.folder_name for draft in confirmed_folder_voiceovers]

    tone_tags = ", ".join(project_brief.tone_tags) or "(keine Angabe)"
    active_negative_rules = _active_negative_rules_block(project_brief)
    forbidden_phrases = list(project_brief.forbidden_phrases)
    if style_profile is not None:
        forbidden_phrases.extend(style_profile.forbidden_phrases)
    forbidden_phrases.extend(settings.forbidden_phrases)
    forbidden_phrases.extend(settings.must_avoid)
    forbidden_block = "\n".join(f"- {phrase}" for phrase in forbidden_phrases) or "(keine)"

    chapter_blocks = "\n".join(
        _folder_voiceover_block(entries_by_folder.get(draft.folder_name), draft)
        for draft in confirmed_folder_voiceovers
    ) or "(keine freigegebenen Kapitel verfügbar)"

    must_include_block = ", ".join(settings.must_include) or "(keine Angabe)"
    ready_list = ", ".join(ready_names) or "(none)"

    return f"""You are a documentary editor writing the OPENING INTRO for a \
multi-location travel/nature documentary. You receive SHORT chapter signals \
only (location name, dramaturgy role, reason, scores) — NOT the spoken \
chapter scripts / voice-over body text.

Do not invent plot details that are not implied by the chapter signals, \
project brief, or structural style reference. Write a strong documentary Intro.

Do not invent asset IDs. No scripts, sentence_items, or inventory are provided. \
For visual_beats, set primary_asset_id to "" and needs_supplement_asset=true \
with a concrete supplement_reason.

{native_speaker_language_block(settings.language or project_brief.language)}

## Project
- Project title: {project_brief.video_title or "(untitled)"}
- Desired tone tags: {tone_tags}
- Intro tone (from settings): {settings.tone}
- Core narrative arc: {dramaturgy_plan.narrative_arc or "-"}
- Core promise: {dramaturgy_plan.core_promise or "-"}

## Active global negative rules (MUST be respected)
{active_negative_rules}

## Forbidden phrases (global + style + intro settings)
{forbidden_block}

## Intro structural / style reference (STRUCTURE first — do not copy wording)
{_style_summary_block(style_profile, style_context_text=style_context_text)}

## Intro rules for this project
- allow_questions: {settings.allow_questions}
- allow_strong_claim: {settings.allow_strong_claim}
- allow_direct_place_name: {settings.allow_direct_place_name}
- allow_tease_multiple_places: {settings.allow_tease_multiple_places}
- must include (topics/ideas, not literal phrases): {must_include_block}
- editor's extra instructions: {settings.freeform_rule_for_llm or "(none)"}
- target_words: {settings.target_words} (min {settings.min_words}, max {settings.max_words})
- Soft guidance only when a structural raw Intro reference is present: prefer \
matching that reference's beat count and pacing; stay near the word window \
when possible without breaking the structure.

## Ready chapters (use ONLY these folder_name values in used_folders / source_folder_name)
{ready_list}

## Chapter signals (NO spoken narration text)
{chapter_blocks}

## Task
ONE shared Intro STRUCTURE. FIVE different CONTENT variants.

1. Infer the structural template from the Intro structural / style reference \
above (beat order, vignette rhythm, pauses/pacing, naming beat, tension/history \
beat, open questions, host/promise close). If no structural reference is \
available, use a clean documentary Intro with the same beat roles.
2. Keep that SAME structure for every candidate.
3. Vary only the CONTENT across the 5 candidates: different place selections, \
facts, contrasts, and question angles drawn from the chapter signals.
4. Do NOT produce 5 different hook strategies (mystery vs contrast vs question, \
etc.). Strategy/structure is fixed; content changes.

Produce EXACTLY 5 intro candidates (exactly 5, no more, no fewer). Each must \
read like real documentary prose — never like a list of assets or a plot \
summary. Preserve structural markers from the reference when present \
(e.g. [cinematic], [pause …], [serious], [intense]) adapted to this project's \
language and content — do not invent a different macro-structure.

For each candidate, also provide visual_beats: a beat breakdown of the intro \
text. Set source_folder_name to a ready chapter name from above when relevant. \
Leave source_sentence_id and primary_asset_id empty (""), set \
needs_supplement_asset=true, and give a concrete supplement_reason describing \
the needed visual.

Respond with JSON ONLY, no markdown code fences, no commentary, matching exactly \
this shape:

{{
  "candidates": [
    {{
      "hook_id": "hook_001",
      "hook_text": "...",
      "hook_type": "short_slug_for_this_content_focus",
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
      "reason": "what content choices differ vs other variants; structure is shared",
      "risks": []
    }}
  ]
}}
"""

def build_youtube_publish_prompt(
    *,
    language: str,
    title: str,
    total_duration_sec: float,
    chapters_block: str,
    description_max_chars: int,
    hashtags_max_chars: int,
    quiz_count: int = 0,
    intro_text: str = "",
    folder_scripts_block: str = "",
    option_count: int = 3,
) -> str:
    """Prompt für YouTube-Titel/Beschreibung/Hashtags — nur Kapitelüberschriften.

    Keine vollständigen Folder-Skripte. Quiz wird separat generiert.
    Legacy-Parameter ``quiz_count`` / ``intro_text`` / ``folder_scripts_block`` /
    ``option_count`` werden ignoriert (API-Kompatibilität).
    """
    del quiz_count, intro_text, folder_scripts_block, option_count
    display = _language_display_name(language)
    return f"""You prepare YouTube publish metadata for a travel/documentary video.

{native_speaker_language_block(language)}

## Hard limits
- `description_body`: engaging YouTube description in {display}, max ~{description_max_chars} characters.
  Do NOT include chapter timestamps in description_body — chapters are appended later by the system.
- `hashtags`: comma-separated keywords WITHOUT leading `#` (e.g. USA, Travel, Nature),
  max ~{hashtags_max_chars} characters total. No newlines. No `#` symbols.
- Do NOT invent quizzes here — quizzes are generated in a separate step.

## Video
- Working title: {title or "(untitled)"}
- Total duration seconds: {total_duration_sec:.1f}
- Target language: {language} ({display})

## Chapters (titles + timestamps only — authoritative; do not invent different ones)
Use ONLY these chapter headings as content signal. There are no full voice-over scripts.
{chapters_block}

## Output rules
- Derive title, description and hashtags from the chapter titles / locations and working title.
- Keep description SEO-friendly but natural — no keyword stuffing.
- Return JSON ONLY, no markdown fences.

## JSON schema
{{
  "title": "optional refined YouTube title in {display}",
  "description_body": "description without chapter list",
  "hashtags": "tag1, tag2, tag3"
}}
"""


def build_youtube_quiz_prompt(
    *,
    language: str,
    title: str,
    total_duration_sec: float,
    quiz_count: int,
    chapters_block: str,
    option_count: int = 3,
) -> str:
    """Prompt nur für YouTube-Quiz — Kapitelüberschriften, keine Folder-Skripte."""
    display = _language_display_name(language)
    return f"""You create YouTube in-video quizzes for a travel/documentary video.

{native_speaker_language_block(language)}

## Hard limits
- Return EXACTLY {quiz_count} quiz items (one per ~10 minutes of video).
- Each quiz has EXACTLY {option_count} answer options (A/B/C), exactly one correct.
- Suggest `insert_at_sec` as a good moment to show the quiz (not during the very first seconds,
  preferably near chapter transitions when possible).
- `insert_at_sec` must be within 0 and {total_duration_sec:.1f}.

## Video
- Working title: {title or "(untitled)"}
- Total duration seconds: {total_duration_sec:.1f}
- Target language: {language} ({display})

## Chapters (titles + timestamps only — authoritative)
Use ONLY these chapter headings. There are no full voice-over scripts.
{chapters_block}

## Output rules
- Questions must fit the chapter locations / themes from the list (plausible travel/doc quiz).
- Do not invent chapters or timestamps outside the list.
- Return JSON ONLY, no markdown fences.

## JSON schema
{{
  "quizzes": [
    {{
      "order_index": 1,
      "question": "...",
      "options": [
        {{"label": "A", "text": "...", "is_correct": false}},
        {{"label": "B", "text": "...", "is_correct": true}},
        {{"label": "C", "text": "...", "is_correct": false}}
      ],
      "correct_option_label": "B",
      "insert_at_sec": 0.0,
      "reason": "why this moment works"
    }}
  ]
}}
"""

