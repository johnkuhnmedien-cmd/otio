"""Menschlesbare Zusammenfassung von Python-Timing-Fehlern."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "TimingIssueGroup",
    "classify_timing_errors",
    "format_timing_error_overview",
    "split_timing_error_blob",
]


@dataclass
class TimingIssueGroup:
    category: str
    title: str
    explanation: str
    next_step: str
    items: list[str] = field(default_factory=list)


_SHORT_RE = re.compile(
    r"(?P<slot>\S+):\s*Asset\s+(?P<asset>\S+)\s+zu kurz\s+"
    r"\(nutzbar\s+(?P<usable>[\d.]+)s\s*<\s*nötig\s+(?P<need>[\d.]+)s",
    re.IGNORECASE,
)
_GAP_RE = re.compile(
    r"(?P<label>Führende|Abschließende|Visuelle)?\s*visuelle Lücke|"
    r"visuelle Lücke",
    re.IGNORECASE,
)
_BRIDGE_RE = re.compile(
    r"unterschiedlichen Kapiteln|bridge_\d+",
    re.IGNORECASE,
)
_PATH_RE = re.compile(r"(Pfad|/Users/|/home/|[A-Za-z]:\\)\S+")


def split_timing_error_blob(blob: str | Exception) -> list[str]:
    if hasattr(blob, "errors") and getattr(blob, "errors"):
        return [str(x).strip() for x in blob.errors if str(x).strip()]
    text = str(blob or "").strip()
    if not text:
        return []
    # Neu: Newline-getrennt. Alt: "; " — aber nicht innerhalb "(…; Toleranz…)".
    if "\n" in text:
        return [p.strip() for p in text.splitlines() if p.strip()]
    parts = re.split(
        r"\s*;\s*(?=(?:[A-Za-z0-9_]+_slot_\d+|bridge_\d+|Abschließende|Führende|"
        r"Visuelle|Kapitel|Asset\s))",
        text,
    )
    return [p.strip() for p in parts if p.strip()]


def _short_item(message: str) -> str:
    match = _SHORT_RE.search(message)
    if not match:
        return _strip_paths(message)
    file_hint = ""
    path_match = re.search(r"Pfad\s+(\S+)", message)
    if path_match:
        file_hint = f" · Datei `{Path(path_match.group(1)).name}`"
    return (
        f"{match.group('slot')}: "
        f"braucht {match.group('need')}s, Asset nur {match.group('usable')}s nutzbar"
        f"{file_hint}"
    )


def _strip_paths(message: str) -> str:
    cleaned = _PATH_RE.sub("", message)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" ·")
    # Lange Asset-Hashes kürzen.
    cleaned = re.sub(
        r"asset__[a-z0-9_]+__[a-z0-9_]+__[a-f0-9]{6,}",
        lambda m: m.group(0).split("__")[2] if "__" in m.group(0) else m.group(0),
        cleaned,
    )
    return cleaned


def classify_timing_errors(messages: list[str] | str | Exception) -> list[TimingIssueGroup]:
    """Gruppiert Roh-Fehler in wenige, erklärbare Blöcke."""
    if isinstance(messages, (str, Exception)):
        items = split_timing_error_blob(messages)
    else:
        items = [str(m).strip() for m in messages if str(m).strip()]

    groups: dict[str, TimingIssueGroup] = {
        "short_asset": TimingIssueGroup(
            category="short_asset",
            title="Asset zu kurz für Narrationsspanne",
            explanation=(
                "Das LLM plant redaktionell (Anker/Sätze), nicht in exakten Sekunden. "
                "Python berechnet danach die echte Dauer aus dem Voice-over — "
                "manche gewählten Clips sind dafür zu kurz. "
                "Das ist kein falsches Datei-Mapping."
            ),
            next_step=(
                "Im Funnel Ersatz suchen, kürzeren Shot planen, oder "
                "Toleranz `short_asset_tolerance_sec` erhöhen."
            ),
        ),
        "visual_gap": TimingIssueGroup(
            category="visual_gap",
            title="Visuelle Lücke in der Timeline",
            explanation=(
                "Zwischen zwei Shots oder am Kapitelende fehlt Bild, während "
                "Audio weiterläuft. Oft Folge davon, dass ein zu kurzer Shot "
                "nicht gelegt werden konnte."
            ),
            next_step=(
                "Zuerst die „zu kurz“-Fälle schließen (Funnel/anderes Asset), "
                "dann Python Timing erneut."
            ),
        ),
        "chapter_bridge": TimingIssueGroup(
            category="chapter_bridge",
            title="Kapitelübergang / Bridge",
            explanation=(
                "Zwischen zwei Kapiteln liegt ein technischer Bridge-Slot. "
                "Der spannt bewusst zwei Kapitel — kein normaler Inhalts-Shot."
            ),
            next_step="Meist ignorierbar; bei Bedarf Gap für den Übergang füllen.",
        ),
        "other": TimingIssueGroup(
            category="other",
            title="Weitere Timing-Hinweise",
            explanation="Sonstige Prüfungen der aufgelösten Timeline.",
            next_step="Details prüfen und ggf. Plan oder Medien korrigieren.",
        ),
    }

    for message in items:
        if "zu kurz" in message.lower():
            groups["short_asset"].items.append(_short_item(message))
        elif _BRIDGE_RE.search(message):
            groups["chapter_bridge"].items.append(_strip_paths(message))
        elif _GAP_RE.search(message) or "Lücke" in message:
            groups["visual_gap"].items.append(_strip_paths(message))
        else:
            groups["other"].items.append(_strip_paths(message))

    return [g for g in groups.values() if g.items]


def format_timing_error_overview(messages: list[str] | str | Exception) -> str:
    """Kompakte Textübersicht (eine Zeile pro Gruppe)."""
    groups = classify_timing_errors(messages)
    if not groups:
        return ""
    parts = [f"{g.title}: {len(g.items)}" for g in groups]
    return " · ".join(parts)
