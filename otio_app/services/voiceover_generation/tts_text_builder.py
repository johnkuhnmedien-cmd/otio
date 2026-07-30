"""Baut den tatsächlich an ElevenLabs gesendeten Text aus einem bestätigten
Folder-Voice-over-Draft — inkl. Pause-Tags zwischen Sätzen/Beats, sofern
`sentence_items[].pause_after` gesetzt ist (Nutzerfeedback Juli 2026:
Pausen wie in den Style-References-Beispielen, z. B. "[pause 4 seconds]").

Wichtige Einschränkung (siehe PAUSE_AFTER_CHOICES/ELEVENLABS_V3_PAUSE_TAGS in
defaults.py): NUR das ElevenLabs-Modell eleven_v3 unterstützt die
Bracket-Pause-Tags ("[pause]", "[short pause]", "[long pause]") — mit exakter
Sekundenzahl (wie in den Nutzer-Referenztexten) kann KEIN ElevenLabs-Modell
über die API angesteuert werden. Für alle anderen Modelle würden dieselben
Tags als Text VORGELESEN statt eine Pause zu erzeugen — deshalb werden sie
dort NICHT eingefügt und voiceover_text_full bleibt unverändert.

Die tatsächlich erreichte Pausenlänge bei eleven_v3 ist NICHT exakt steuerbar
(nur qualitativ kurz/mittel/lang) — für die Cut-Plan-/Timeline-Berechnung
sollte daher die GEMESSENE Lücke aus dem Alignment verwendet werden, nicht
die angeforderte Kategorie.
"""

from __future__ import annotations

from otio_app.defaults import ELEVENLABS_MODEL_ID_V3, ELEVENLABS_V3_PAUSE_TAGS
from otio_app.services.voiceover_generation.models import FolderVoiceoverDraft
from otio_app.services.voiceover_generation.text_segment_matching import (
    build_normalized_index_map,
    find_segment_span,
)

__all__ = ["build_tts_ready_text"]


def build_tts_ready_text(draft: FolderVoiceoverDraft, model_id: str) -> str:
    """Liefert den Text, der tatsächlich an ElevenLabs gesendet werden soll.

    Sucht für jedes sentence_item mit gesetztem pause_after dessen Textspanne
    innerhalb von voiceover_text_full (dieselbe Such-Logik wie das spätere
    Alignment, siehe text_segment_matching.py) und fügt direkt danach den
    passenden eleven_v3-Pause-Tag ein. Wird eine Spanne nicht gefunden (z. B.
    weil sentence_items vom Fließtext abweichen), wird diese eine Pause
    stillschweigend übersprungen, statt den gesamten Text zu verwerfen —
    robuster als ein harter Fehler."""
    full_text = draft.voiceover_text_full
    if model_id != ELEVENLABS_MODEL_ID_V3:
        return full_text

    if not any(item.pause_after for item in draft.sentence_items):
        return full_text

    normalized_full, index_map = build_normalized_index_map(full_text)
    insertions: list[tuple[int, str]] = []
    search_from = 0

    for item in draft.sentence_items:
        if not item.text.strip():
            continue
        normalized_segment, _ = build_normalized_index_map(item.text)
        span = find_segment_span(normalized_full, index_map, normalized_segment, search_from=search_from)
        if span is None:
            continue
        _, orig_end, new_cursor = span
        search_from = new_cursor
        tag = ELEVENLABS_V3_PAUSE_TAGS.get(item.pause_after, "")
        if tag:
            # find_segment_span() endet direkt nach dem letzten alphanumerischen
            # Zeichen des Satzes — Satzzeichen (z. B. der Punkt) werden von der
            # Normalisierung ignoriert. Ohne diese Erweiterung würde der Tag
            # VOR dem Satzzeichen landen ("Satz [pause]." statt "Satz. [pause]").
            insertion_index = orig_end
            while insertion_index < len(full_text) and not (
                full_text[insertion_index].isalnum() or full_text[insertion_index].isspace()
            ):
                insertion_index += 1
            insertions.append((insertion_index, f" {tag}"))

    if not insertions:
        return full_text

    parts: list[str] = []
    cursor = 0
    for index, tag_text in sorted(insertions, key=lambda pair: pair[0]):
        parts.append(full_text[cursor:index])
        parts.append(tag_text)
        cursor = index
    parts.append(full_text[cursor:])
    return "".join(parts)
