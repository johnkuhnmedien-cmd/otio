"""JP/KR-Schnittpunkte für Satz- und Wort-Timings (ElevenLabs Character-Times).

Westliche Sprachen bleiben unverändert: Wörter nur an Whitespace, Sätze an
``.!?…`` plus Leerzeichen. Japanisch und Koreanisch haben oft keine
Leerzeichen — Keyword Flow braucht trotzdem echte Onsets.

Zusätzliche Grenzen (nur CJK/Hangul-Kontext):
- Satzende ``。！？`` (Leerzeichen optional)
- lateinisches ``.!?…`` direkt vor Kana/Kanji/Hangul (KR ohne Space nach Punkt)
- Phrase: ``、。！？`` und Schriftwechsel (Kata|Kanji, Hangul|Latin, …)
"""

from __future__ import annotations

import re

# Phrase-Schnitt: bleibt am vorausgehenden Token (Onset = nächste Gruppe).
CJK_PHRASE_PUNCT = frozenset("、。！？｡､‥・･…·")

_CJK_SCRIPTS = frozenset({"hira", "kata", "han", "hangul"})

# Western terminator + space (bestehendes Verhalten).
# CJK-Terminator, Space optional.
# Western terminator unmittelbar vor Kana/Kanji/Hangul (KR: "다.다음").
_SENTENCE_SPLIT_RE = re.compile(
    r"(?<=[.!?…])\s+"
    r"|(?<=[。！？])\s*"
    r"|(?<=[.!?…])(?=[\u3040-\u30FF\u3400-\u9FFF\uF900-\uFAFF\uAC00-\uD7A3])"
)

_PAUSE_TAG_RE = re.compile(
    r"\[(?:pause|Pause)(?:\s+[^\]]*)?\]",
    re.IGNORECASE,
)
_PAUSE_ONLY_RE = re.compile(
    rf"^{_PAUSE_TAG_RE.pattern}$",
    re.IGNORECASE,
)
_LEADING_PAUSE_RE = re.compile(
    rf"^(?:{_PAUSE_TAG_RE.pattern}\s*)+",
    re.IGNORECASE,
)


def char_timing_run_class(ch: str) -> str:
    """Schriftklasse für Token-Läufe; ``space`` / ``cjk_punct`` sind Grenzen."""
    if not ch:
        return "other"
    if ch.isspace():
        return "space"
    if ch in CJK_PHRASE_PUNCT:
        return "cjk_punct"
    code = ord(ch)
    if 0x3040 <= code <= 0x309F:
        return "hira"
    if (
        0x30A0 <= code <= 0x30FF
        or code == 0x30FC
        or 0x31F0 <= code <= 0x31FF
        or 0xFF66 <= code <= 0xFF9D
    ):
        return "kata"
    if (
        0xAC00 <= code <= 0xD7AF
        or 0x1100 <= code <= 0x11FF
        or 0x3130 <= code <= 0x318F
        or 0xA960 <= code <= 0xA97F
        or 0xD7B0 <= code <= 0xD7FF
    ):
        return "hangul"
    if (
        0x3400 <= code <= 0x9FFF
        or 0xF900 <= code <= 0xFAFF
        or 0x20000 <= code <= 0x2FAFF
        or code == 0x3005  # 々
    ):
        return "han"
    return "other"


def should_split_timing_run(prev_class: str, new_class: str) -> bool:
    """True wenn JP/KR-Schrift wechselt (nicht bei reinem Latin/Other)."""
    if not prev_class or not new_class or prev_class == new_class:
        return False
    return prev_class in _CJK_SCRIPTS or new_class in _CJK_SCRIPTS


def _is_pause_or_non_speech_part(text: str) -> bool:
    cleaned = (text or "").strip()
    if not cleaned:
        return True
    if _PAUSE_ONLY_RE.match(cleaned):
        return True
    return False


def split_cjk_aware_sentences(text: str) -> list[str]:
    """Satz-/Beat-Chunks; Pause-Tags ohne eigenen Satz an den Vorgänger."""
    cleaned = (text or "").strip()
    if not cleaned:
        return []
    raw_parts = [part.strip() for part in _SENTENCE_SPLIT_RE.split(cleaned) if part.strip()]
    if not raw_parts:
        return [cleaned]
    merged: list[str] = []
    for part in raw_parts:
        if merged and _is_pause_or_non_speech_part(part):
            merged[-1] = f"{merged[-1]} {part}".strip()
            continue
        leading = _LEADING_PAUSE_RE.match(part) if merged else None
        if leading:
            merged[-1] = f"{merged[-1]} {leading.group(0)}".strip()
            rest = part[leading.end() :].strip()
            if rest:
                merged.append(rest)
            continue
        merged.append(part)
    return merged or [cleaned]
