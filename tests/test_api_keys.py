"""Tests für API-Schlüssel-Verwaltung."""

from __future__ import annotations

import os

import pytest

from otio_app.services.api_keys import (
    get_api_key,
    is_api_key_set,
    mask_api_key,
    parse_env_file,
    set_runtime_api_key,
    write_user_secrets,
)


def test_get_api_key_prefers_runtime_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "from-env")
    set_runtime_api_key("GEMINI_API_KEY", "from-session")
    assert get_api_key("GEMINI_API_KEY") == "from-session"
    set_runtime_api_key("GEMINI_API_KEY", None)


def test_write_user_secrets_persists(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("otio_app.services.api_keys.ensure_data_dir", lambda: tmp_path)
    write_user_secrets({"GEMINI_API_KEY": "stored-key", "OPENAI_API_KEY": "sk-test"})
    secrets_file = tmp_path / "user_secrets.env"
    assert secrets_file.is_file()
    parsed = parse_env_file(secrets_file)
    assert parsed["GEMINI_API_KEY"] == "stored-key"
    assert parsed["OPENAI_API_KEY"] == "sk-test"
    assert os.environ.get("GEMINI_API_KEY") == "stored-key"


def test_mask_api_key_hides_middle() -> None:
    assert mask_api_key("AIzaSyABCDEF123456789") == "AIza…6789"
    assert mask_api_key(None) == "—"


def test_is_api_key_set_false_when_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    set_runtime_api_key("PEXELS_API_KEY", None)
    assert is_api_key_set("PEXELS_API_KEY") is False
