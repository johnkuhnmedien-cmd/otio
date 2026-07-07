"""Tests für Multi-Provider Schnittplan-LLMs."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from otio_app.services.plan_llm_client import (
    PlanLlmNotConfiguredError,
    format_plan_model_label,
    is_plan_model_configured,
    plan_model_provider,
    resolve_plan_model,
    generate_plan_text,
)


def test_plan_model_provider_routes_by_prefix() -> None:
    assert plan_model_provider("gemini-3.1-pro-preview") == "gemini"
    assert plan_model_provider("openai:gpt-4.1") == "openai"
    assert plan_model_provider("anthropic:claude-opus-4-6") == "anthropic"


def test_resolve_plan_model_accepts_openai_and_anthropic() -> None:
    assert resolve_plan_model("openai:gpt-4.1-mini") == "openai:gpt-4.1-mini"
    assert resolve_plan_model("anthropic:claude-haiku-4-5") == "anthropic:claude-haiku-4-5"


def test_format_plan_model_label_includes_provider_names() -> None:
    assert "GPT-4.1" in format_plan_model_label("openai:gpt-4.1")
    assert "Claude Opus" in format_plan_model_label("anthropic:claude-opus-4-6")


def test_is_plan_model_configured_checks_matching_provider_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert is_plan_model_configured("openai:gpt-4.1") is True
    assert is_plan_model_configured("gemini-3.1-pro-preview") is False


def test_generate_plan_text_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content='{"beats":[]}'))]

    with patch("openai.OpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create.return_value = mock_response
        text = generate_plan_text(prompt="Plan this folder", model="openai:gpt-4.1")

    assert text == '{"beats":[]}'
    mock_openai.return_value.chat.completions.create.assert_called_once()
    call_kwargs = mock_openai.return_value.chat.completions.create.call_args.kwargs
    assert call_kwargs["model"] == "gpt-4.1"


def test_generate_plan_text_anthropic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    block = MagicMock(type="text", text='{"beats":[{"beat_id":"beat_001"}]}')
    mock_response = MagicMock(content=[block])

    with patch("anthropic.Anthropic") as mock_anthropic:
        mock_anthropic.return_value.messages.create.return_value = mock_response
        text = generate_plan_text(
            prompt="Plan this folder",
            model="anthropic:claude-haiku-4-5",
        )

    assert "beat_001" in text
    call_kwargs = mock_anthropic.return_value.messages.create.call_args.kwargs
    assert call_kwargs["model"] == "claude-haiku-4-5"


def test_generate_plan_text_raises_when_openai_key_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(PlanLlmNotConfiguredError, match="OPENAI_API_KEY"):
        generate_plan_text(prompt="x", model="openai:gpt-4.1-mini")
