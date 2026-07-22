"""Zentrale Pausendauer-Klassen für without_voiceover_enhanced.

Einzige Stelle, an der short/medium/long in Sekunden übersetzt werden.

Hinweis: Voice-over-Pausen sind vorübergehend deaktiviert
(``ENHANCED_VOICEOVER_PAUSES_ENABLED = False``), weil sie in Resolve als
Lücken auf der Video-Spur landeten statt als Pausen in der Narration.
"""

from __future__ import annotations

# Temporär aus: keine aufgezogenen VO-Pausen in Timeline/OTIO.
# Auf True setzen, wenn Audio-Gaps korrekt und Video per Hold gefüllt wird.
ENHANCED_VOICEOVER_PAUSES_ENABLED = False

PAUSE_DURATION_SECONDS: dict[str, float] = {
    "short": 0.35,
    "medium": 0.80,
    "long": 1.50,
}

PAUSE_FUNCTIONS = (
    "breath",
    "emphasis",
    "anticipation",
    "reveal",
    "chapter_transition",
    "reflection",
    "no_pause",
)

DURATION_CLASSES = tuple(PAUSE_DURATION_SECONDS.keys())

VISUAL_BEHAVIORS = (
    "hold_current_shot",
    "next_shot_may_start_during_pause",
    "cut_at_pause_start",
    "cut_at_pause_end",
    "editorial_choice",
)


def voiceover_pauses_enabled() -> bool:
    return bool(ENHANCED_VOICEOVER_PAUSES_ENABLED)


def resolve_pause_duration_seconds(duration_class: str) -> float:
    """Deterministische Auflösung einer Dauerklasse in Sekunden."""
    if not ENHANCED_VOICEOVER_PAUSES_ENABLED:
        return 0.0
    key = (duration_class or "").strip().lower()
    if key == "no_pause":
        return 0.0
    if key not in PAUSE_DURATION_SECONDS:
        raise ValueError(
            f"Ungültige Pause-Dauerklasse: {duration_class!r}. "
            f"Erlaubt: {', '.join(DURATION_CLASSES)}"
        )
    return PAUSE_DURATION_SECONDS[key]
