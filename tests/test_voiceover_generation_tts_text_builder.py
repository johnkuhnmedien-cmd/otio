"""Pausen im Folder-Voice-over (Nutzerfeedback Juli 2026): 'Ich will dass die
voice over in Abschnitte unterteilt werden mit pausen dazwischen.'

build_tts_ready_text() fügt eleven_v3-Pause-Tags NUR für das eleven_v3-Modell
ein — bei allen anderen Modellen würden dieselben Tags als Text vorgelesen
statt eine Pause zu erzeugen."""

from __future__ import annotations

from otio_app.services.voiceover_generation.models import FolderVoiceoverDraft, SentenceItem
from otio_app.services.voiceover_generation.tts_text_builder import build_tts_ready_text


def _draft(text: str, sentence_items: list[SentenceItem]) -> FolderVoiceoverDraft:
    return FolderVoiceoverDraft(
        project_id="p1",
        folder_name="Antelope Canyon",
        voiceover_text_full=text,
        sentence_items=sentence_items,
    )


def test_no_pause_tags_for_non_v3_models() -> None:
    draft = _draft(
        "Erster Satz. Zweiter Satz.",
        [
            SentenceItem(sentence_id="s1", text="Erster Satz.", pause_after="long"),
            SentenceItem(sentence_id="s2", text="Zweiter Satz."),
        ],
    )
    result = build_tts_ready_text(draft, "eleven_multilingual_v2")
    assert result == draft.voiceover_text_full
    assert "[" not in result


def test_no_pause_tags_when_none_requested() -> None:
    draft = _draft(
        "Erster Satz. Zweiter Satz.",
        [
            SentenceItem(sentence_id="s1", text="Erster Satz."),
            SentenceItem(sentence_id="s2", text="Zweiter Satz."),
        ],
    )
    result = build_tts_ready_text(draft, "eleven_v3")
    assert result == draft.voiceover_text_full


def test_inserts_long_pause_tag_after_requested_sentence_for_v3() -> None:
    draft = _draft(
        "Erster Satz. Zweiter Satz.",
        [
            SentenceItem(sentence_id="s1", text="Erster Satz.", pause_after="long"),
            SentenceItem(sentence_id="s2", text="Zweiter Satz."),
        ],
    )
    result = build_tts_ready_text(draft, "eleven_v3")
    assert result == "Erster Satz. [long pause] Zweiter Satz."


def test_inserts_short_and_medium_pause_tags_correctly() -> None:
    draft = _draft(
        "Eins. Zwei. Drei.",
        [
            SentenceItem(sentence_id="s1", text="Eins.", pause_after="short"),
            SentenceItem(sentence_id="s2", text="Zwei.", pause_after="medium"),
            SentenceItem(sentence_id="s3", text="Drei."),
        ],
    )
    result = build_tts_ready_text(draft, "eleven_v3")
    assert result == "Eins. [short pause] Zwei. [pause] Drei."


def test_multiple_pauses_inserted_in_correct_order() -> None:
    draft = _draft(
        "Eins. Zwei. Drei. Vier.",
        [
            SentenceItem(sentence_id="s1", text="Eins.", pause_after="long"),
            SentenceItem(sentence_id="s2", text="Zwei."),
            SentenceItem(sentence_id="s3", text="Drei.", pause_after="short"),
            SentenceItem(sentence_id="s4", text="Vier."),
        ],
    )
    result = build_tts_ready_text(draft, "eleven_v3")
    assert result == "Eins. [long pause] Zwei. Drei. [short pause] Vier."


def test_gracefully_skips_pause_when_sentence_text_not_found_in_full_text() -> None:
    """Robustheit: sentence_items.text weicht (in Tests konstruierbar) vom
    Fließtext ab — die Pause an dieser Stelle wird einfach übersprungen,
    statt den kompletten Text zu verwerfen oder einen Fehler zu werfen."""
    draft = _draft(
        "Ein völlig anderer Fließtext ohne Übereinstimmung.",
        [
            SentenceItem(sentence_id="s1", text="Dieser Satz kommt nicht vor.", pause_after="long"),
        ],
    )
    result = build_tts_ready_text(draft, "eleven_v3")
    assert result == draft.voiceover_text_full
    assert "[" not in result


def test_empty_sentence_text_is_skipped_without_error() -> None:
    draft = _draft(
        "Nur ein Satz.",
        [
            SentenceItem(sentence_id="s1", text=""),
            SentenceItem(sentence_id="s2", text="Nur ein Satz.", pause_after="short"),
        ],
    )
    result = build_tts_ready_text(draft, "eleven_v3")
    assert result == "Nur ein Satz. [short pause]"


def test_invalid_pause_after_value_produces_no_tag() -> None:
    """Falls irgendwo ein nicht-erlaubter Wert durchrutscht (sollte durch
    _valid_pause_after in voiceover_author_service bereits verhindert
    werden), darf build_tts_ready_text trotzdem keinen Tag einfügen."""
    draft = _draft(
        "Erster Satz. Zweiter Satz.",
        [
            SentenceItem(sentence_id="s1", text="Erster Satz.", pause_after="extremely_long"),
            SentenceItem(sentence_id="s2", text="Zweiter Satz."),
        ],
    )
    result = build_tts_ready_text(draft, "eleven_v3")
    assert result == draft.voiceover_text_full
