"""text_segment_matching.py — aus audio_alignment_service.py extrahierte,
gemeinsam genutzte Such-Logik (jetzt auch von tts_text_builder.py verwendet)."""

from __future__ import annotations

from otio_app.services.voiceover_generation.text_segment_matching import (
    build_normalized_index_map,
    find_segment_span,
)


def test_build_normalized_index_map_strips_punctuation_and_lowercases() -> None:
    normalized, index_map = build_normalized_index_map("Hello, World!")
    assert normalized == "hello world"
    assert len(normalized) == len(index_map)


def test_build_normalized_index_map_collapses_whitespace() -> None:
    normalized, _ = build_normalized_index_map("Hello   World")
    assert normalized == "hello world"


def test_build_normalized_index_map_trims_leading_and_trailing_space() -> None:
    normalized, _ = build_normalized_index_map("  Hello World  ")
    assert normalized == "hello world"


def test_find_segment_span_locates_exact_match() -> None:
    full_text = "Hello world. This is a test."
    normalized_full, index_map = build_normalized_index_map(full_text)
    normalized_segment, _ = build_normalized_index_map("This is a test.")
    span = find_segment_span(normalized_full, index_map, normalized_segment, search_from=0)
    assert span is not None
    start, end, _ = span
    assert full_text[start:end] == "This is a test"  # Satzzeichen am Ende ausgeschlossen


def test_find_segment_span_returns_none_when_not_found() -> None:
    full_text = "Hello world."
    normalized_full, index_map = build_normalized_index_map(full_text)
    normalized_segment, _ = build_normalized_index_map("Not present at all")
    span = find_segment_span(normalized_full, index_map, normalized_segment, search_from=0)
    assert span is None


def test_find_segment_span_returns_none_for_empty_segment() -> None:
    normalized_full, index_map = build_normalized_index_map("Hello world.")
    span = find_segment_span(normalized_full, index_map, "", search_from=0)
    assert span is None


def test_find_segment_span_respects_search_from() -> None:
    full_text = "Test one. Test two."
    normalized_full, index_map = build_normalized_index_map(full_text)
    normalized_segment, _ = build_normalized_index_map("Test")
    first_span = find_segment_span(normalized_full, index_map, normalized_segment, search_from=0)
    assert first_span is not None
    second_span = find_segment_span(
        normalized_full, index_map, normalized_segment, search_from=first_span[2]
    )
    assert second_span is not None
    assert second_span[0] > first_span[0]


def test_build_normalized_index_map_turkish_dotted_i_stays_one_to_one() -> None:
    """Türkisches İ: str.lower() wird zu zwei Codepoints — Map muss 1:1 bleiben."""
    text = "Climb toward İshak."
    normalized, index_map = build_normalized_index_map(text)
    assert len(normalized) == len(index_map)
    assert "ishak" in normalized
    assert "i\u0307" not in normalized


def test_find_segment_span_with_trailing_turkish_dotted_i() -> None:
    """Letzter Satz mit İ — früher IndexError list index out of range."""
    full_text = "Climb toward İshak."
    normalized_full, index_map = build_normalized_index_map(full_text)
    normalized_segment, _ = build_normalized_index_map(full_text)
    span = find_segment_span(
        normalized_full, index_map, normalized_segment, search_from=0
    )
    assert span is not None
    start, end, _ = span
    assert "İshak" in full_text[start:end]


def test_find_segment_span_returns_none_when_index_map_too_short() -> None:
    span = find_segment_span("hello world", [0, 1, 2], "hello world", search_from=0)
    assert span is None
