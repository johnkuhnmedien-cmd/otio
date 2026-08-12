"""ElevenLabs Music prompts — short, hierarchical, no second LLM."""

from __future__ import annotations

MUSIC_PROMPT_MAX_CHARS = 4100

_PAUSE_NOTE = (
    "Any [pause …] markers describe narration pacing only; "
    "they must not be spoken or sung."
)


def build_chapter_music_prompt(*, narration_text: str) -> str:
    body = (narration_text or "").strip()
    return f"""\
Create an instrumental documentary underscore for the following narration.

Match the location, atmosphere, cultural or historical character, and emotional tone of the narration.

The music must support spoken narration without dominating it.
Use restrained dynamics and a clear but unobtrusive musical identity.

No vocals.
No spoken words.
No lyrics.
Avoid exaggerated trailer-style drama.

{_PAUSE_NOTE}

Let the music develop naturally and end cleanly within the requested duration.

NARRATION:

{body}
"""


def build_intro_music_prompt(*, narration_text: str) -> str:
    body = (narration_text or "").strip()
    return f"""\
Create instrumental documentary opening music for the following intro narration.

Match the location, atmosphere, cultural or historical character, and emotional tone of the narration.

Begin atmospheric and restrained.
Gradually build energy and forward momentum.
The ending should feel more open and anticipatory so it leads naturally into the first chapter.

The music must support spoken narration without dominating it.

No vocals.
No spoken words.
No lyrics.
Avoid exaggerated trailer-style drama.

{_PAUSE_NOTE}

End cleanly within the requested duration.

INTRO NARRATION:

{body}
"""


def music_prompt_within_limit(prompt: str) -> bool:
    return len(prompt or "") <= MUSIC_PROMPT_MAX_CHARS
