"""Prompt-Builder für die Voice-over-Generierungs-Pipeline (Phase 2: Style Profile)."""

from __future__ import annotations

from otio_app.services.voiceover_generation.models import (
    ProjectBrief,
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
