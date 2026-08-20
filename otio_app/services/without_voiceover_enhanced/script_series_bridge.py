"""Film-zu-Film-Brücke am Ende des letzten Enhanced-Kapitel-Skripts.

Kein YouTube-CTA: das letzte Kapitel darf neugierig auf ein anderes Video der
Serie machen, indem es vom hiesigen Ort aus einen belegten gedanklichen Sprung
ins andere Land baut.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from otio_app.services.voiceover_generation.models import ProjectBrief
from otio_app.services.without_voiceover_enhanced.segment_alignment_service import (
    split_segment_into_sentences,
)

__all__ = [
    "SERIES_BRIDGE_CTA_REPAIR_INSTRUCTION",
    "SERIES_BRIDGE_LINK_REPAIR_INSTRUCTION",
    "SeriesBridgeConfig",
    "build_series_bridge_prompt_block",
    "detect_series_bridge_cta_violations",
    "series_bridge_from_brief",
]


@dataclass(frozen=True)
class SeriesBridgeConfig:
    destination: str
    hook_facts: str = ""
    editorial_angle: str = ""


def series_bridge_from_brief(brief: ProjectBrief | None) -> SeriesBridgeConfig | None:
    if brief is None or not brief.series_bridge_enabled:
        return None
    destination = (brief.series_bridge_destination or "").strip()
    if not destination:
        return None
    return SeriesBridgeConfig(
        destination=destination,
        hook_facts=(brief.series_bridge_hook_facts or "").strip(),
        editorial_angle=(brief.series_bridge_angle or "").strip(),
    )


def build_series_bridge_prompt_block(
    config: SeriesBridgeConfig | None,
    *,
    this_place: str = "",
    is_last_chapter: bool = False,
) -> str:
    """Nur das letzte Kapitel bekommt den gesprochenen Film-zu-Film-Block."""
    if config is None or not is_last_chapter:
        return ""
    this_label = (this_place or "").strip() or "(unspecified — use THIS chapter's place)"
    facts = config.hook_facts or (
        "(none provided — name the destination only through contrast with THIS "
        "chapter's verified facts; invent nothing about the other place)"
    )
    angle = config.editorial_angle or (
        "(choose a factual hinge from THIS chapter + the facts below)"
    )
    dest = config.destination
    return f"""\
SERIES BRIDGE — LAST CHAPTER OF THIS FILM ONLY (spoken, at the END)

This film belongs to a documentary series. After this chapter's own closing thought,
add 1–3 sentences that turn curiosity toward another film in the series.

THIS film's country/region: {this_label}
OTHER film's country/region (name it naturally in LANGUAGE): {dest}
Editorial hinge (SILENT — do not verbalize this label): {angle}
Verified facts you MAY use about the other place (do not invent beyond these):
{facts}

HOW
- Earn the turn from HERE: geography, history, culture, climate, or a shared thread
  that makes {dest} the natural next question — not an advertisement.
- Keep the same documentary voice. Do not break into presenter/YouTuber address.
- End on an image or unanswered tension, not on an instruction to the viewer.
- This exception outranks the generic ban on mentioning other places — but ONLY
  in these closing sentences, and ONLY for {dest}.
- chapter_link_usage.to_next stays false: this is film-to-film, not a road to the
  next chapter of THIS film.
- The bridge is extra (about 40–70 words). Do not replace the chapter with it.

NEVER
- YouTube/CTA: watch now, click, subscribe, link in the description, channel,
  "schau dir das Video an", "mein letztes/nächstes Video"
- "Im nächsten Video reisen wir nach…" / journey-on-to-the-next-film formulas
- Reciting a video title like a title card
- Putting this bridge at the opening of the chapter

BAD: "Schau dir jetzt mein letztes Video über {dest} an."
BAD: "Im nächsten Video geht es weiter nach {dest}."
GOOD: a concrete hinge from this place to {dest} that makes the other country
feel inevitable — then name it.
"""


# CTA / YouTube-Formeln — nur gegen den Schluss prüfen, damit Ortsnamen wie
# „Kanal von Korinth“ nicht als Kanal-CTA zählen.
_TAIL_CTA_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:schau|sieh)(?:e|t)?\s+(?:dir|dich)\b.{0,40}\bvideo\b",
        r"\bletzte[sn]?\s+video\b",
        r"\bnächste[sn]?\s+video\b",
        r"\bwatch\s+(?:now|next|my)\b",
        r"\b(?:my\s+)?(?:last|latest|previous|next)\s+video\b",
        r"\bclick\s+(?:here|the\s+link|below)\b",
        r"\bhier\s+klicken\b",
        r"\bklick(?:e|t)?\s+(?:jetzt|hier|unten)\b",
        r"\blink\s+in\s+(?:the\s+)?description\b",
        r"\blink\s+in\s+der\s+beschreibung\b",
        r"\bsubscribe\b",
        r"\babonnier",
        r"\bcheck\s+out\s+(?:my|the)\b",
        r"\bregarde(?:z)?\s+ma\s+vid[eé]o\b",
        r"\bmira(?:\s+el|\s+mi)?\s+v[ií]deo\b",
        r"\bveja\s+o\s+v[ií]deo\b",
        r"\bguarda(?:\s+il)?\s+video\b",
        r"\bim\s+n[äa]chsten\s+video\b",
        r"\bin\s+(?:the\s+)?next\s+video\b",
        r"\bmein(?:em)?\s+(?:letzten?\s+)?video\b",
        r"\byoutube\b",
    )
)


def detect_series_bridge_cta_violations(narration: str) -> list[str]:
    """Findet YouTube-CTA-Formeln in den letzten Sätzen."""
    sentences = [
        s.strip() for s in split_segment_into_sentences(narration or "") if s.strip()
    ]
    if not sentences:
        return []
    tail = " ".join(sentences[-5:] if len(sentences) > 5 else sentences)
    violations: list[str] = []
    seen: set[str] = set()
    for pattern in _TAIL_CTA_PATTERNS:
        match = pattern.search(tail)
        if not match:
            continue
        snippet = match.group(0).strip()
        if snippet.lower() in seen:
            continue
        seen.add(snippet.lower())
        violations.append(f"Series-Bridge-CTA (Ende): «{snippet}»")
    return violations


SERIES_BRIDGE_CTA_REPAIR_INSTRUCTION = """\
SERIES BRIDGE REPAIR REQUIRED

The previous ending sounded like a YouTube call-to-action (watch now, next video,
click, subscribe, "schau dir das Video an").

Rewrite the complete chapter. Keep the documentary voice.

The LAST 1–3 sentences must still be a SERIES BRIDGE to the other film's place:
a factual hinge from THIS place that wakes curiosity — then name that place.

Do not instruct the viewer. Do not mention video, YouTube, click, or subscribe.
Do not use journey-to-the-next-film formulas.

Return the complete required JSON again.
"""


SERIES_BRIDGE_LINK_REPAIR_INSTRUCTION = """\
REPAIR REQUIRED

The previous answer used a forbidden spoken connection between chapters of THIS film.

Rewrite the complete chapter as a self-contained mini-documentary about this location.

Start directly with the location, a concrete defining feature, its historical importance, or a verified fact.

Do not describe leaving another chapter, travelling here, or the road to the next
chapter of THIS film.

KEEP the end-of-film SERIES BRIDGE: 1–3 documentary sentences that turn curiosity
toward the other film's place. That bridge is not a chapter-to-chapter road.

Do not use YouTube/CTA language.

Preserve the factual content and target length.
Return the complete required JSON again.
"""
