"""SFX planner prompt builders and ElevenLabs prompt helpers."""

from __future__ import annotations

import json
from typing import Any

from otio_app.services.without_voiceover_enhanced.elevenlabs_sfx_client import (
    SFX_PROMPT_MAX_CHARS,
)

__all__ = [
    "SFX_PLAN_SCHEMA_VERSION",
    "SFX_PROMPT_PREFERRED_MAX_CHARS",
    "build_sfx_planner_system_rules",
    "build_sfx_planner_prompt",
    "sfx_prompt_within_limit",
]

SFX_PLAN_SCHEMA_VERSION = "sfx-plan-v1"
SFX_PROMPT_PREFERRED_MAX_CHARS = 350


def build_sfx_planner_system_rules(*, max_sfx_per_chapter: int) -> str:
    max_n = int(max_sfx_per_chapter)
    return f"""Sound effects are optional.

Prefer silence over unnecessary sound design.

Use at most {max_n} sound effects for the complete chapter.
This is a hard maximum, not a target.

Use fewer whenever possible.

Prefer environmental ambience and natural location sound over detailed Foley.

Do not add Foley simply because an action is visible.

Do not add a sound effect simply because a related word appears in the narration.

Choose only sound effects with HIGH editorial value.

Sound design must remain subtle and documentary in character.

Avoid trailer-style sound design, excessive whooshes, impacts and dramatic cinematic hits.

Hierarchy:
- Resolved Visual Timeline = primary truth
- Narration Timeline = semantic + temporal context

A sentence, keyword, shot, or visible action does NOT automatically imply an SFX.
Silence is a valid and preferred decision.
0, 1, 2, or at most {max_n} effects are allowed — never more than {max_n}.

Allowed evidence_basis values only:
- visible
- environmental_plausible
- editorial_non_diegetic

Allowed sfx_type values only:
- natural_ambience
- location_ambience
- diegetic_foley (exceptional only)
- editorial_transition (very rare)

editorial_value must be "high" for every proposed effect.
Do not invent event timestamps. Do not invent free-form seconds.
Allowed anchor_type values only:
- shot_start
- shot_center
- shot_end
- span_shot
- narration_word

For narration_word you MUST copy an existing word_ref from the provided word flow.
For other anchors set word_ref to null.

duration_class must be one of: short | medium | long
(Python resolves durations; do not output final seconds.)

Each ElevenLabs prompt must be a clear single sound description, preferably
<= {SFX_PROMPT_PREFERRED_MAX_CHARS} characters, never over {SFX_PROMPT_MAX_CHARS}.
Prefer phrases like: subtle, realistic, documentary ambience, no music, no speech, no vocals.
"""


def build_sfx_planner_prompt(
    *,
    max_sfx_per_chapter: int,
    scope: str,
    chapter_id: str,
    locked_script_text: str,
    narration_start: float,
    narration_end: float,
    scope_total_duration: float,
    resolved_shots: list[dict[str, Any]],
    word_flow: list[dict[str, Any]],
) -> str:
    rules = build_sfx_planner_system_rules(max_sfx_per_chapter=max_sfx_per_chapter)
    payload = {
        "scope": scope,
        "chapter_id": chapter_id,
        "scope_total_duration_seconds": round(float(scope_total_duration), 3),
        "narration_start_seconds": round(float(narration_start), 3),
        "narration_end_seconds": round(float(narration_end), 3),
        "locked_script": locked_script_text,
        "resolved_visual_timeline_shots": resolved_shots,
        "narration_word_flow": word_flow,
    }
    return (
        f"{rules}\n\n"
        "Return ONLY valid JSON matching this schema (no markdown):\n"
        "{\n"
        f'  "schema_version": "{SFX_PLAN_SCHEMA_VERSION}",\n'
        '  "scope": "chapter" | "intro",\n'
        '  "sfx": [\n'
        "    {\n"
        '      "sfx_id": "sfx_001",\n'
        '      "sfx_type": "natural_ambience",\n'
        '      "prompt": "...",\n'
        '      "evidence_basis": "environmental_plausible",\n'
        '      "editorial_value": "high",\n'
        '      "shot_id": "slot_011",\n'
        '      "anchor_type": "span_shot",\n'
        '      "word_ref": null,\n'
        '      "duration_class": "medium",\n'
        '      "reason": "..."\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "An empty sfx array is a valid successful plan.\n\n"
        "PLANNER INPUT:\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n"
    )


def sfx_prompt_within_limit(prompt: str, *, max_chars: int = SFX_PROMPT_MAX_CHARS) -> bool:
    return 0 < len(str(prompt or "").strip()) <= int(max_chars)
