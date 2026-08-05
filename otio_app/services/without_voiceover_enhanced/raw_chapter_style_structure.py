"""Deterministische Strukturmerkmale aus Raw-Kapitel-Referenzen."""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from otio_app.services.without_voiceover_enhanced.script_chapter_text import (
    AUTHOR_PAUSE_MARKER_RE,
    normalize_author_pause_seconds,
)

__all__ = [
    "PreparedRawChapterReference",
    "RawChapterStyleStructure",
    "RawReferencePause",
    "PAUSE_MARKER_RE",
    "prepare_raw_chapter_reference",
    "analyze_raw_chapter_style_structure",
    "format_raw_chapter_structure_signals",
    "detect_raw_chapter_style_violations",
]

# Alias — gleiche Marker-Erkennung wie in der Kapitelansicht.
PAUSE_MARKER_RE = AUTHOR_PAUSE_MARKER_RE
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


class RawReferencePause(BaseModel):
    after_beat_index: int
    seconds: float


@dataclass(frozen=True)
class PreparedRawChapterReference:
    cleaned_text: str
    contains_pause_markers: bool
    beat_texts: list[str]
    pauses: list[RawReferencePause] = field(default_factory=list)


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
    pause_count: int = 0
    pause_seconds_sequence: list[float] = Field(default_factory=list)
    median_pause_seconds: float = 0.0
    min_pause_seconds: float = 0.0
    max_pause_seconds: float = 0.0


def prepare_raw_chapter_reference(raw_text: str) -> PreparedRawChapterReference:
    text = (raw_text or "").replace("\r\n", "\n").replace("\r", "\n")
    contains = bool(PAUSE_MARKER_RE.search(text))

    pieces: list[tuple[str, object]] = []
    cursor = 0
    for match in PAUSE_MARKER_RE.finditer(text):
        if match.start() > cursor:
            pieces.append(("text", text[cursor : match.start()]))
        seconds = normalize_author_pause_seconds(
            match.group("seconds").replace(",", ".")
        )
        pieces.append(("pause", seconds))
        cursor = match.end()
    if cursor < len(text):
        pieces.append(("text", text[cursor:]))

    beats: list[str] = []
    pauses: list[RawReferencePause] = []
    for kind, value in pieces:
        if kind == "text":
            for part in re.split(r"\n\s*\n+", str(value)):
                cleaned = part.strip()
                if cleaned:
                    beats.append(cleaned)
            continue
        seconds = float(value)
        if not beats:
            # Leading pause in reference: ignore for beat association.
            continue
        pauses.append(
            RawReferencePause(after_beat_index=len(beats) - 1, seconds=seconds)
        )

    cleaned_lines: list[str] = []
    for index, beat in enumerate(beats):
        cleaned_lines.append(beat)
        cleaned_lines.append("")
        pause = next((p for p in pauses if p.after_beat_index == index), None)
        if pause is not None:
            cleaned_lines.append(
                f"[REFERENCE TIMED PAUSE — {pause.seconds:g}s — NOT SPOKEN TEXT]"
            )
            cleaned_lines.append("")
    cleaned = "\n".join(cleaned_lines).strip()
    if contains:
        cleaned = (
            cleaned
            + "\n\nPause markers in the reference are non-spoken pacing examples.\n"
            "Do not include pause labels, brackets, seconds or production directions "
            "in narration_full or segment text.\n"
            "Do express intended timed pauses through author_pause_after_seconds.\n"
            "Treat the observed durations as the pause-rhythm model for this chapter."
        )
    return PreparedRawChapterReference(
        cleaned_text=cleaned,
        contains_pause_markers=contains,
        beat_texts=beats,
        pauses=pauses,
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
    pause_values = [float(p.seconds) for p in prepared.pauses if float(p.seconds) > 0]
    if not lengths:
        return RawChapterStyleStructure(
            beat_count=len(prepared.beat_texts),
            contains_pause_markers=prepared.contains_pause_markers,
            pause_count=len(pause_values),
            pause_seconds_sequence=pause_values,
            median_pause_seconds=(
                round(float(statistics.median(pause_values)), 2) if pause_values else 0.0
            ),
            min_pause_seconds=round(min(pause_values), 2) if pause_values else 0.0,
            max_pause_seconds=round(max(pause_values), 2) if pause_values else 0.0,
        )
    median = float(statistics.median(lengths))
    lower = (
        float(statistics.quantiles(lengths, n=4)[0])
        if len(lengths) >= 4
        else float(min(lengths))
    )
    upper = (
        float(statistics.quantiles(lengths, n=4)[-1])
        if len(lengths) >= 4
        else float(max(lengths))
    )
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
        pause_count=len(pause_values),
        pause_seconds_sequence=pause_values,
        median_pause_seconds=(
            round(float(statistics.median(pause_values)), 2) if pause_values else 0.0
        ),
        min_pause_seconds=round(min(pause_values), 2) if pause_values else 0.0,
        max_pause_seconds=round(max(pause_values), 2) if pause_values else 0.0,
    )


def format_raw_chapter_structure_signals(structure: RawChapterStyleStructure) -> str:
    lines = [
        "REFERENCE STRUCTURE SIGNALS",
        f"- Typical sentence length: ~{structure.median_words_per_sentence:.0f} words",
        (
            "- Typical sentence range: "
            f"~{structure.lower_sentence_words:.0f}–{structure.upper_sentence_words:.0f} words"
        ),
        (
            "- Typical number of sentences per factual beat: "
            f"~{structure.median_sentences_per_beat:.1f}"
        ),
        (
            "- Reference opens directly with its subject: "
            f"{'yes' if structure.starts_directly_with_subject else 'no'}"
        ),
        (
            "- Reference uses restrained factual beats separated by pacing breaks: "
            f"{'yes' if structure.contains_pause_markers or structure.beat_count >= 2 else 'no'}"
        ),
    ]
    if structure.pause_count > 0:
        seq = ", ".join(f"{value:g}s" for value in structure.pause_seconds_sequence[:24])
        if len(structure.pause_seconds_sequence) > 24:
            seq += ", ..."
        lines.extend(
            [
                "",
                "REFERENCE PAUSE RHYTHM",
                "- Reference uses an explicit pause after most factual beats.",
                f"- Observed pause durations: {seq}",
                (
                    "- Typical pause duration: approximately "
                    f"{structure.median_pause_seconds:g} seconds."
                ),
                f"- Pause range: {structure.min_pause_seconds:g}s–{structure.max_pause_seconds:g}s.",
                "- Use shorter pauses for closely related facts.",
                (
                    "- Use longer pauses after a location change, important historical "
                    "fact or major visual beat."
                ),
                (
                    "- Reproduce this rhythm with author_pause_after_seconds "
                    "(not by writing pause labels into spoken text)."
                ),
            ]
        )
    return "\n".join(lines)


def detect_raw_chapter_style_violations(
    narration_full: str,
    *,
    structure: RawChapterStyleStructure | None,
    folder_name: str = "",
    segments: list | None = None,
) -> list[str]:
    """Erkennt grobe Abweichungen von einer klar direkten/faktischen Referenz."""
    text = (narration_full or "").strip()
    if not text and not segments:
        return []
    violations: list[str] = []
    spoken_parts = [text] if text else []
    for segment in segments or []:
        spoken_parts.append(str(getattr(segment, "text", "") or ""))
    spoken = "\n".join(spoken_parts)
    if PAUSE_MARKER_RE.search(spoken) or re.search(
        r"\[\s*(?:REFERENCE (?:BEAT BREAK|TIMED PAUSE)|pause)[^\]]*\]",
        spoken,
        re.IGNORECASE,
    ):
        violations.append("Gesprochener Text enthält Pausemarker/Produktionsanweisung.")

    sentences = _sentences(text)
    if structure is not None and structure.median_words_per_sentence > 0 and sentences:
        lengths = [_word_count(s) for s in sentences]
        median = float(statistics.median(lengths))
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

    if (
        structure is not None
        and structure.pause_count >= 3
        and segments is not None
        and len(segments) >= 4
    ):
        pauses = [
            float(getattr(seg, "author_pause_after_seconds", 0.0) or 0.0)
            for seg in segments
        ]
        if pauses and all(value <= 0 for value in pauses):
            violations.append(
                "Raw-Referenz nutzt zeitlich markierte Pausen, aber das Kapitel "
                "setzt keine author_pause_after_seconds."
            )

    return violations
