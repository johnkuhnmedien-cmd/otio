"""LLM-JSON-Parser: Steuerzeichen in Strings (YouTube-Beschreibungen)."""

from __future__ import annotations

import json

import pytest

from otio_app.services.gemini_client import _extract_json


def test_extract_json_allows_literal_newline_in_string() -> None:
    raw = (
        '{\n'
        '  "title": "IT_Greece",\n'
        '  "description_body": "' + ("x" * 1100) + '\nmore text",\n'
        '  "hashtags": "Greece, Travel"\n'
        '}'
    )
    with pytest.raises(json.JSONDecodeError, match="Invalid control character"):
        json.loads(raw)
    payload = _extract_json(raw)
    assert payload["title"] == "IT_Greece"
    assert "more text" in payload["description_body"]
    assert payload["hashtags"] == "Greece, Travel"


def test_extract_json_allows_tab_in_string() -> None:
    payload = _extract_json('{"title": "A\tB"}')
    assert payload["title"] == "A\tB"


def test_extract_json_reads_fenced_json_with_trailing_prose() -> None:
    raw = 'Here you go:\n```json\n{"title": "Ok"}\n```\nThanks.'
    assert _extract_json(raw) == {"title": "Ok"}


def test_extract_json_still_rejects_broken_json() -> None:
    with pytest.raises(json.JSONDecodeError):
        _extract_json("{not json")
