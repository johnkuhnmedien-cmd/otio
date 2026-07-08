"""Prompt-Builder für die Voice-over-Generierungs-Pipeline.

Phase 2: build_style_profile_prompt()
Phase 3: build_dramaturgy_prompt()
"""

from __future__ import annotations

from otio_app.services.voiceover_generation.models import (
    FolderInventorySummary,
    ProjectBrief,
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
