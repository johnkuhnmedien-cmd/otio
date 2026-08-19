"""Tests für Gemini-Modell-Konfiguration."""

from __future__ import annotations

import pytest

from otio_app.config import get_gemini_model_from_env
from otio_app.defaults import (
    DEFAULT_GEMINI_MODEL,
    VOICEOVER_GEN_ENHANCED_FUNNEL_DEFAULT_MODEL,
    resolve_funnel_gemini_model,
)
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


def test_resolve_funnel_gemini_model_keeps_curated_ids() -> None:
    assert resolve_funnel_gemini_model("gemini-3.5-flash") == "gemini-3.5-flash"
    assert resolve_funnel_gemini_model("gemini-3.1-flash-lite") == "gemini-3.1-flash-lite"


def test_resolve_funnel_gemini_model_maps_retired_15_to_funnel_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
    assert (
        resolve_funnel_gemini_model("gemini-1.5-flash")
        == VOICEOVER_GEN_ENHANCED_FUNNEL_DEFAULT_MODEL
    )
    assert resolve_funnel_gemini_model("") == VOICEOVER_GEN_ENHANCED_FUNNEL_DEFAULT_MODEL
    # Analyse-Fallback (Lite) darf den Funnel nicht umbiegen.
    assert resolve_gemini_model("gemini-1.5-flash") == "gemini-3.1-flash-lite"
