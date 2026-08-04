"""Deterministische Strukturmerkmale aus Raw-Kapitel-Referenzen."""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass

from pydantic import BaseModel, Field

__all__ = [
    "PreparedRawChapterReference",
    "RawChapterStyleStructure",
    "PAUSE_MARKER_RE",
    "prepare_raw_chapter_reference",
    "analyze_raw_chapter_style_structure",
    "format_raw_chapter_structure_signals",
    "detect_raw_chapter_style_violations",
]

PAUSE_MARKER_RE = re.compile(
    r"\[\s*pause\s+\d+(?:[.,]\d+)?\s*(?:seconds?|sekunden?|secs?|s)?\s*\]",
    re.IGNORECASE,
)
_BEAT_BREAK_TOKEN = "[REFERENCE BEAT BREAK — NOT SPOKEN]"
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_WORD_RE = re.compile(r"[A-Za-zÀ-ÿ0-9']+")
_PERSONIFICATION_RE = re.compile(
    r"\b(?:greets|had\s+run\s+out\s+of\s+patience|seems\s+to\s+linger|"
    r"history\s+seems|landscape\s+had|city\s+whispers|stones?\s+remember)\b",
    re.IGNORECASE,
)
_LITERARY_TRAVEL_OPEN_RE = re.compile(
    r"^\s*(?:.{0,40}\bgreets\s+the\s+journey\b|"
    r".{0,40}\brun\s+out\s+of\s+patience\b|"
    r".{0,40}\bhistory\s+seems\s+to\s+linger\b)",
    re.IGNORECASE,
)
_DIRECT_SUBJECT_RE = re.compile(
    r"^\s*(?:The\s+|Der\s+|Die\s+|Das\s+|Le\s+|La\s+|El\s+|Il\s+)?"
    r"[A-ZÀ-Ü][\w'’\-]*(?:\s+[A-ZÀ-Ü][\w'’\-]*){0,5}\s+"
    r"(?:lies|lie|is\s+located|stands|stand|sits|sit|extends|extend|"
    r"liegt|befindet|erstreckt|se\s+trouve|se\s+halla)\b"
)


@dataclass(frozen=True)
class PreparedRawChapterReference:
    cleaned_text: str
    contains_pause_markers: bool
    beat_texts: list[str]


class RawChapterStyleStructure(BaseModel):
    sentence_count: int = 0
    median_words_per_sentence: float = 0.0
    lower_sentence_words: float = 0.0
    upper_sentence_words: float = 0.0
    max_sentence_words: int = 0
    beat_count: int = 0
    median_sentences_per_beat: float = 0.0
    starts_directly_with_subject: bool = False
    contains_pause_markers: bool = False


def prepare_raw_chapter_reference(raw_text: str) -> PreparedRawChapterReference:
    text = (raw_text or "").replace("\r\n", "\n").replace("\r", "\n")
    contains = bool(PAUSE_MARKER_RE.search(text))
    normalized = PAUSE_MARKER_RE.sub(_BEAT_BREAK_TOKEN, text)
    # Beats: blank lines, beat-break tokens, paragraphs
    parts = re.split(
        rf"(?:\n\s*\n+|{re.escape(_BEAT_BREAK_TOKEN)})",
        normalized,
    )
    beats = [p.strip() for p in parts if p.strip()]
    cleaned_lines: list[str] = []
    for beat in beats:
        cleaned_lines.append(beat)
        cleaned_lines.append("")
    cleaned = "\n".join(cleaned_lines).strip()
    if contains:
        cleaned = (
            cleaned
            + "\n\nPause markers in the reference are non-spoken pacing examples.\n"
            "Do not include pause labels, brackets, seconds or production directions "
            "in narration_full or segment text.\n"
            "Treat them only as indications that the reference often separates one "
            "factual idea from the next."
        )
    return PreparedRawChapterReference(
        cleaned_text=cleaned,
        contains_pause_markers=contains,
        beat_texts=beats,
    )


def _sentences(text: str) -> list[str]:
    chunks = [c.strip() for c in _SENTENCE_SPLIT_RE.split(text or "") if c.strip()]
    return chunks


def _word_count(sentence: str) -> int:
    return len(_WORD_RE.findall(sentence))


def analyze_raw_chapter_style_structure(
    prepared: PreparedRawChapterReference | str,
) -> RawChapterStyleStructure:
    if isinstance(prepared, str):
        prepared = prepare_raw_chapter_reference(prepared)
    sentences: list[str] = []
    sentences_per_beat: list[int] = []
    for beat in prepared.beat_texts:
        beat_sentences = _sentences(beat)
        sentences.extend(beat_sentences)
        if beat_sentences:
            sentences_per_beat.append(len(beat_sentences))
    lengths = [_word_count(s) for s in sentences if _word_count(s) > 0]
    if not lengths:
        return RawChapterStyleStructure(
            beat_count=len(prepared.beat_texts),
            contains_pause_markers=prepared.contains_pause_markers,
        )
    median = float(statistics.median(lengths))
    lower = float(statistics.quantiles(lengths, n=4)[0]) if len(lengths) >= 4 else float(min(lengths))
    upper = float(statistics.quantiles(lengths, n=4)[-1]) if len(lengths) >= 4 else float(max(lengths))
    first = sentences[0] if sentences else ""
    return RawChapterStyleStructure(
        sentence_count=len(sentences),
        median_words_per_sentence=round(median, 2),
        lower_sentence_words=round(lower, 2),
        upper_sentence_words=round(upper, 2),
        max_sentence_words=max(lengths),
        beat_count=len(prepared.beat_texts),
        median_sentences_per_beat=(
            round(float(statistics.median(sentences_per_beat)), 2)
            if sentences_per_beat
            else 0.0
        ),
        starts_directly_with_subject=bool(_DIRECT_SUBJECT_RE.search(first)),
        contains_pause_markers=prepared.contains_pause_markers,
    )


def format_raw_chapter_structure_signals(structure: RawChapterStyleStructure) -> str:
    return (
        "REFERENCE STRUCTURE SIGNALS\n"
        f"- Typical sentence length: ~{structure.median_words_per_sentence:.0f} words\n"
        f"- Typical sentence range: "
        f"~{structure.lower_sentence_words:.0f}–{structure.upper_sentence_words:.0f} words\n"
        f"- Typical number of sentences per factual beat: "
        f"~{structure.median_sentences_per_beat:.1f}\n"
        f"- Reference opens directly with its subject: "
        f"{'yes' if structure.starts_directly_with_subject else 'no'}\n"
        f"- Reference uses restrained factual beats separated by pacing breaks: "
        f"{'yes' if structure.contains_pause_markers or structure.beat_count >= 2 else 'no'}"
    )


def detect_raw_chapter_style_violations(
    narration_full: str,
    *,
    structure: RawChapterStyleStructure | None,
    folder_name: str = "",
) -> list[str]:
    """Erkennt grobe Abweichungen von einer klar direkten/faktischen Referenz."""
    text = (narration_full or "").strip()
    if not text:
        return []
    violations: list[str] = []
    if PAUSE_MARKER_RE.search(text) or re.search(
        r"\[\s*(?:REFERENCE BEAT BREAK|pause)[^\]]*\]", text, re.IGNORECASE
    ):
        violations.append("Gesprochener Text enthält Pausemarker/Produktionsanweisung.")

    sentences = _sentences(text)
    if structure is not None and structure.median_words_per_sentence > 0 and sentences:
        lengths = [_word_count(s) for s in sentences]
        median = float(statistics.median(lengths))
        # Deutlich zu lange Sätze gegenüber klar kurzer Referenz
        if (
            structure.median_words_per_sentence <= 18
            and median >= structure.median_words_per_sentence * 2.2
            and median >= 28
        ):
            violations.append(
                "Satzstruktur deutlich länger als die Raw-Kapitelreferenz."
            )

    first = sentences[0] if sentences else text
    if structure is not None and structure.starts_directly_with_subject:
        if _LITERARY_TRAVEL_OPEN_RE.search(first):
            violations.append(
                "Literarische Reiseeröffnung trotz direkter Referenzeröffnung."
            )
        place = (folder_name or "").strip()
        if place:
            place_token = place.split("&")[0].split("–")[0].split("-")[0].strip()
            head = first.lower()
            token = place_token.lower()
            if token and token not in head and not _DIRECT_SUBJECT_RE.search(first):
                # soft: only flag when clearly literary/personifying without place
                if _PERSONIFICATION_RE.search(first):
                    violations.append(
                        "Einstieg ohne direkten Ortsbezug trotz direkter Referenz."
                    )

    personifications = _PERSONIFICATION_RE.findall(text)
    if len(personifications) >= 2:
        violations.append(
            f"Mehrere abstrakte Personifikationen erkannt ({len(personifications)})."
        )
    elif personifications and _LITERARY_TRAVEL_OPEN_RE.search(first or ""):
        violations.append("Literarische Personifikation im Kapiteleinstieg.")

    return violations
