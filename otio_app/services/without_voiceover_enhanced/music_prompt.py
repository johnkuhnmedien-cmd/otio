"""ElevenLabs Music prompts — short, hierarchical, no second LLM."""

from __future__ import annotations

MUSIC_PROMPT_MAX_CHARS = 4100

_PAUSE_NOTE = (
    "Any [pause …] markers describe narration pacing only; "
    "they must not be spoken or sung."
)

_START_IMMEDIATELY_NOTE = (
    "The music must start immediately at 0.00 seconds. "
    "It must be clearly audible from the first instant. "
    "Do not delay the entrance. Do not begin with silence. "
    "Do not use an 8–10 second fade-in. "
    "Do not wait for the narrator to start speaking."
)

_PLAY_UNTIL_END_NOTE = (
    "Keep the music playing until the last moment of the requested duration. "
    "Do not stop, fade out, or resolve 8–10 seconds before the end. "
    "Fill the complete duration wall-to-wall, from 0.00 seconds to the final second."
)


def build_chapter_music_prompt(
    *,
    narration_text: str,
    total_duration_seconds: float,
    narration_end_seconds: float,
) -> str:
    body = (narration_text or "").strip()
    total = float(total_duration_seconds)
    narration_end = float(narration_end_seconds)
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

Total track duration: {total:.2f} seconds.
Narration ends at: {narration_end:.2f} seconds.

{_START_IMMEDIATELY_NOTE}

Maintain a continuous, fully developed musical underscore throughout the complete narration and the time after it.

Do not begin the musical outro, fade-out, final cadence, or final resolution while the narrator is still speaking.

Keep the music musically active and supportive until approximately {narration_end:.2f} seconds.

Only after the narration has finished, transition into a very short and concise closing cadence using the remaining time.

Do not create a long outro.
Do not fade down early.
Do not musically resolve early.

If very little time remains after the narration, make the final cadence extremely short rather than starting the outro during the voice-over.

{_PLAY_UNTIL_END_NOTE}

The short closing cadence must still reach the exact end of the requested duration.

Let the music develop naturally and end cleanly within the requested duration.

NARRATION:

{body}
"""


def build_intro_music_prompt(
    *,
    narration_text: str,
    total_duration_seconds: float | None = None,
) -> str:
    body = (narration_text or "").strip()
    duration_block = ""
    if total_duration_seconds is not None:
        duration_block = (
            f"\nTotal track duration: {float(total_duration_seconds):.2f} seconds.\n"
        )
    return f"""\
Create instrumental documentary opening music for the following intro narration.

Match the location, atmosphere, cultural or historical character, and emotional tone of the narration.

Musical direction: classical orchestral (symphony, strings, brass, woodwinds).
The intro may feel epic and grand — a classical, cinematic opening, not a quiet pad.
Do not use modern trailer music, hybrid-epic percussion, electronic drops, or exaggerated Hollywood trailer drama.
Key: begin in E minor. Later make a shift into major (parallel E major or a bright major cadence) so the ending opens and lifts.

{_START_IMMEDIATELY_NOTE}

Start already present from 0.00 seconds, with an epic classical character.
Gradually build energy and forward momentum.
The ending should feel more open and anticipatory so it leads naturally into the first chapter.

The music may be more elevated than a chapter underscore, but must still leave space for spoken narration.

No vocals.
No spoken words.
No lyrics.

{_PAUSE_NOTE}
{duration_block}
{_PLAY_UNTIL_END_NOTE}

End cleanly at the requested duration, not earlier.

INTRO NARRATION:

{body}
"""


def music_prompt_within_limit(prompt: str) -> bool:
    return len(prompt or "") <= MUSIC_PROMPT_MAX_CHARS
