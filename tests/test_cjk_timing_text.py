"""JP/KR Satz- und Wort-Onsets für Keyword Flow (ohne Leerzeichen)."""

from __future__ import annotations

from otio_app.services.without_voiceover_enhanced.cjk_timing_text import (
    split_cjk_aware_sentences,
)
from otio_app.services.without_voiceover_enhanced.segment_alignment_service import (
    split_segment_into_sentences,
)
from otio_app.services.without_voiceover_enhanced.sentence_timing_prompt import (
    clean_words_for_keyword_flow_prompt,
    words_from_elevenlabs_alignment,
)


def _alignment_for(text: str, *, step: float = 0.05) -> dict:
    chars = list(text)
    starts = [index * step for index in range(len(chars))]
    ends = [start + step for start in starts]
    return {
        "characters": chars,
        "character_start_times_seconds": starts,
        "character_end_times_seconds": ends,
    }


def test_western_sentence_split_unchanged() -> None:
    assert split_segment_into_sentences("One. Two? Three!") == [
        "One.",
        "Two?",
        "Three!",
    ]
    assert split_segment_into_sentences("  Alone  ") == ["Alone"]


def test_japanese_sentence_split_on_ideographic_period() -> None:
    text = (
        "光の届かない静寂の奥には、ここだけの生態系が息づいている。"
        "洞窟の環境に適応した生き物たちが、ひっそりと暮らしているのだ。"
        " [pause 4 seconds]"
    )
    parts = split_segment_into_sentences(text)
    assert len(parts) == 2
    assert parts[0].endswith("息づいている。")
    assert "暮らしているのだ。" in parts[1]
    assert "[pause 4 seconds]" in parts[1]


def test_japanese_pause_between_sentences_stays_on_previous() -> None:
    text = (
        "石灰岩の洞窟だ。 [pause 4 seconds] "
        "洞内には石筍が連なる。"
    )
    parts = split_segment_into_sentences(text)
    assert len(parts) == 2
    assert parts[0].endswith("[pause 4 seconds]")
    assert parts[1].startswith("洞内には")


def test_korean_sentence_split_period_before_hangul() -> None:
    text = "첫 문장이다.두 번째 문장이다."
    parts = split_cjk_aware_sentences(text)
    assert parts == ["첫 문장이다.", "두 번째 문장이다."]


def test_korean_sentence_split_ideographic_period() -> None:
    parts = split_segment_into_sentences("첫 문장이다。다음 문장이다。")
    assert parts == ["첫 문장이다。", "다음 문장이다。"]


def test_english_words_still_split_only_on_whitespace() -> None:
    words = words_from_elevenlabs_alignment(_alignment_for("Hi waterfall now"))
    assert [word["text"] for word in words] == ["Hi", "waterfall", "now"]


def test_japanese_words_split_on_punctuation_and_script_runs() -> None:
    text = "バラドラ鍾乳洞は、ハンガリー北東部、地下深くに広がる石灰岩の洞窟だ。"
    words = words_from_elevenlabs_alignment(_alignment_for(text))
    texts = [word["text"] for word in words]
    assert len(texts) >= 8
    assert "ハンガリー" in texts
    assert any(token.startswith("北東部") for token in texts)
    assert any(token.startswith("鍾乳洞") for token in texts)
    # Ein Token für den ganzen Satz — der bisherige Bug — darf nicht mehr entstehen.
    assert text not in texts
    starts = [word["start_seconds"] for word in words]
    assert starts == sorted(starts)
    assert words[0]["start_seconds"] == 0.0


def test_japanese_pause_tags_are_separate_tokens_then_cleaned() -> None:
    text = "洞窟だ。 [pause 4 seconds] 洞内には"
    raw = words_from_elevenlabs_alignment(_alignment_for(text))
    cleaned = clean_words_for_keyword_flow_prompt(raw, sentence_id="seg__s001")
    cleaned_texts = [word["text"] for word in cleaned]
    assert "[pause" not in cleaned_texts
    assert "4" not in cleaned_texts
    assert "seconds]" not in cleaned_texts
    assert any("洞窟" in token or token.endswith("だ。") for token in cleaned_texts)
    assert any("洞内" in token for token in cleaned_texts)


def test_korean_words_keep_spaces_and_split_latin_mix() -> None:
    text = "헝가리 북동부 UNESCO유산이다."
    words = words_from_elevenlabs_alignment(_alignment_for(text))
    texts = [word["text"] for word in words]
    assert "헝가리" in texts
    assert "북동부" in texts
    assert "UNESCO" in texts
    assert any("유산" in token for token in texts)
    assert text not in texts
