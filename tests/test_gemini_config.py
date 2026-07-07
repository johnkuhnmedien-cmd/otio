"""Tests für Gemini-Modell-Konfiguration."""

from __future__ import annotations

import pytest

from otio_app.config import get_gemini_model_from_env
from otio_app.defaults import DEFAULT_GEMINI_MODEL
from otio_app.services.gemini_client import resolve_gemini_model


def test_resolve_gemini_model_prefers_ui_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    assert resolve_gemini_model("gemini-3.1-pro-preview") == "gemini-3.1-pro-preview"


def test_resolve_gemini_model_falls_back_to_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3-flash-preview")
    assert resolve_gemini_model(None) == "gemini-3-flash-preview"
    assert resolve_gemini_model("") == "gemini-3-flash-preview"


def test_resolve_gemini_model_ignores_invalid_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_MODEL", "not-a-real-model")
    assert resolve_gemini_model("also-invalid") == DEFAULT_GEMINI_MODEL
    assert get_gemini_model_from_env() == DEFAULT_GEMINI_MODEL
