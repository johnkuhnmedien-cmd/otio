"""Zentrale Pausendauer-Klassen für without_voiceover_enhanced.

Einzige Stelle, an der short/medium/long in Sekunden übersetzt werden.
"""

from __future__ import annotations

# Stilziel (Projekt): short ≈ Original-Stille, medium/long aufgezogen,
# chapter_transition etwas länger. Werte = deterministische Mittelpunkte.
PAUSE_DURATION_SECONDS: dict[str, float] = {
    "short": 0.50,   # Band 0.3–0.8s
    "medium": 2.50,  # Band 2–3s
    "long": 4.00,    # Band 3–5s
}

# Keyword Flow: zusätzliche Stille (nicht Gesamtdauer).
KEYWORD_FLOW_PAUSE_DURATION_SECONDS: dict[str, float] = {
    "short": 0.35,
    "medium": 0.80,
    "long": 1.50,
}

# Wenn pause_function=chapter_transition: Band 3–8s (Mittelpunkt).
CHAPTER_TRANSITION_SECONDS: float = 5.00

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


def resolve_pause_duration_seconds(
    duration_class: str,
    *,
    pause_function: str = "",
) -> float:
    """Deterministische Auflösung einer Dauerklasse in Sekunden."""
    function = (pause_function or "").strip().lower()
    if function == "no_pause":
        return 0.0
    if function == "chapter_transition":
        return float(CHAPTER_TRANSITION_SECONDS)
    key = (duration_class or "").strip().lower()
    if key == "no_pause":
        return 0.0
    if key not in PAUSE_DURATION_SECONDS:
        raise ValueError(
            f"Ungültige Pause-Dauerklasse: {duration_class!r}. "
            f"Erlaubt: {', '.join(DURATION_CLASSES)}"
        )
    return PAUSE_DURATION_SECONDS[key]


def resolve_keyword_flow_pause_duration_seconds(
    duration_class: str,
    *,
    pause_function: str = "",
) -> float:
    """Keyword-Flow: zusätzliche Stille short/medium/long (fail-closed)."""
    function = (pause_function or "").strip().lower()
    if function == "no_pause":
        return 0.0
    key = (duration_class or "").strip().lower()
    if key == "no_pause" or function == "no_pause":
        return 0.0
    if key not in KEYWORD_FLOW_PAUSE_DURATION_SECONDS:
        raise ValueError(
            f"Ungültige Keyword-Flow-Pause-Dauerklasse: {duration_class!r}. "
            f"Erlaubt: {', '.join(KEYWORD_FLOW_PAUSE_DURATION_SECONDS)}"
        )
    return KEYWORD_FLOW_PAUSE_DURATION_SECONDS[key]
