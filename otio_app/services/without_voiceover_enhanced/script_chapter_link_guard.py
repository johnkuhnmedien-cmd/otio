"""Deterministischer Guard gegen unerlaubte gesprochene Kapitelübergänge."""

from __future__ import annotations

import re

from otio_app.services.without_voiceover_enhanced.segment_alignment_service import (
    split_segment_into_sentences,
)

__all__ = [
    "detect_chapter_link_violations",
]

# (kind, pattern) — kind: from_previous | to_next | either
# Keine Einzelwort-Verbote; „Across the water…“ / „At the center…“ bleiben erlaubt.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (kind, re.compile(pattern, re.IGNORECASE))
    for kind, pattern in (
        (
            "from_previous",
            r"\bleav(?:e|ing)\s+\w[\w'’\-]*(?:\s+\w[\w'’\-]*){0,5}\s+behind\b",
        ),
        ("from_previous", r"\bthe\s+road\s+out\s+of\b"),
        ("from_previous", r"\bwe\s+leave\b"),
        ("from_previous", r"\bwir\s+verlassen\b"),
        ("from_previous", r"\bfahren\s+weiter\b"),
        ("from_previous", r"\ben\s+quittant\b"),
        ("from_previous", r"\bsaliendo\s+de\b"),
        ("from_previous", r"\bdeixando\s+.+\s+para\s+trás\b"),
        ("from_previous", r"\blasciando\s+.+\s+alle\s+spalle\b"),
        ("to_next", r"\bfrom\s+here[, ]+(?:the\s+)?journey\b"),
        ("to_next", r"\bjourney\s+continues\b"),
        ("to_next", r"\bjourney\s+moves?\s+on\b"),
        ("to_next", r"\b(?:our\s+)?next\s+stop\b"),
        ("to_next", r"\bheading\s+toward(?:s)?\b"),
        ("to_next", r"\bmoving\s+on(?:\s+toward|\s+to|\s+into|\b)"),
        ("to_next", r"\bbefore\s+long[,]?\s+\w[\w'’\-]*\s+appears\b"),
        ("to_next", r"\bthe\s+landscape\s+changes\s+again\b"),
        ("to_next", r"\bvon\s+hier\s+aus\b.{0,40}\breise\b"),
        ("to_next", r"\bdie\s+reise\s+geht\s+weiter\b"),
        ("to_next", r"\b(?:unser\s+)?nächster\s+halt\b"),
        ("to_next", r"\bla\s+route\s+(?:mène|continue)\b"),
        ("to_next", r"\bnotre\s+prochain\s+arrêt\b"),
        ("to_next", r"\bel\s+camino\s+(?:sale|conduce|continúa)\b"),
        ("to_next", r"\bnuestra\s+próxima\s+parada\b"),
        ("to_next", r"\ba\s+estrada\s+(?:sai|leva|continua)\b"),
        ("to_next", r"\bla\s+strada\s+(?:esce|porta|continua)\b"),
        ("to_next", r"\bla\s+nostra\s+prossima\s+tappa\b"),
        ("either", r"\bthe\s+road\s+leads?\b"),
        ("either", r"\bthe\s+road\s+starts?\s+climbing\b"),
    )
)


def _edge_windows(narration: str, *, n: int = 2) -> tuple[str, str]:
    sentences = [s.strip() for s in split_segment_into_sentences(narration) if s.strip()]
    if not sentences:
        return "", ""
    head = " ".join(sentences[:n])
    tail = " ".join(sentences[-n:] if len(sentences) > n else sentences)
    return head, tail


def detect_chapter_link_violations(
    narration_full: str,
    *,
    language: str = "de",
    allow_from_previous: bool = False,
    allow_to_next: bool = False,
    allow_callback: bool = False,
) -> list[str]:
    """Prüft erste/letzte Sätze auf Reise- und Kapitelverbindungsformeln.

    ``allow_callback`` erlaubt kurze Rückbezüge, aber keine Abfahrts-/Reiseformel.
    Geografische Einstiege ohne Reiseverknüpfung bleiben erlaubt.
    """
    del language
    del allow_callback  # Rückbezüge ohne Reiseformel werden hier nicht geblockt.
    text = (narration_full or "").strip()
    if not text:
        return []

    head, tail = _edge_windows(text, n=2)
    violations: list[str] = []

    for where, sample in (("Anfang", head), ("Ende", tail)):
        if not sample:
            continue
        for kind, pattern in _PATTERNS:
            match = pattern.search(sample)
            if not match:
                continue
            snippet = match.group(0)
            blocked = False
            if kind == "from_previous" and not allow_from_previous:
                blocked = True
            elif kind == "to_next" and not allow_to_next:
                blocked = True
            elif kind == "either" and not (allow_from_previous or allow_to_next):
                blocked = True
            if blocked:
                violations.append(
                    f"Unerlaubte Kapitelverbindung ({where}): «{snippet}»"
                )
                break

    seen: set[str] = set()
    ordered: list[str] = []
    for item in violations:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered
