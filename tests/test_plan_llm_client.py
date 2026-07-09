"""Tests für Multi-Provider Schnittplan-LLMs."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from otio_app.services.plan_llm_client import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    PlanLlmNotConfiguredError,
    PlanLlmTruncatedResponseError,
    format_plan_model_label,
    is_plan_model_configured,
    plan_model_provider,
    resolve_plan_model,
    generate_plan_text,
)


def _bad_request_error(error_cls, message: str):
    request = httpx.Request("POST", "https://example.invalid/v1/messages")
    response = httpx.Response(
        400,
        request=request,
        json={"type": "error", "error": {"type": "invalid_request_error", "message": message}},
    )
    return error_cls(message, response=response, body=None)


def test_plan_model_provider_routes_by_prefix() -> None:
    assert plan_model_provider("gemini-3.1-pro-preview") == "gemini"
    assert plan_model_provider("openai:gpt-5.5") == "openai"
    assert plan_model_provider("anthropic:claude-opus-4-8") == "anthropic"


def test_resolve_plan_model_accepts_openai_and_anthropic() -> None:
    assert resolve_plan_model("openai:gpt-5.4-mini") == "openai:gpt-5.4-mini"
    assert resolve_plan_model("anthropic:claude-sonnet-5") == "anthropic:claude-sonnet-5"


def test_format_plan_model_label_includes_provider_names() -> None:
    assert "GPT-5.5" in format_plan_model_label("openai:gpt-5.5")
    assert "Claude Opus" in format_plan_model_label("anthropic:claude-opus-4-8")


def test_is_plan_model_configured_checks_matching_provider_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert is_plan_model_configured("openai:gpt-5.5") is True
    assert is_plan_model_configured("gemini-3.1-pro-preview") is False


def test_generate_plan_text_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content='{"beats":[]}'))]

    with patch("openai.OpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create.return_value = mock_response
        text = generate_plan_text(prompt="Plan this folder", model="openai:gpt-5.5")

    assert text == '{"beats":[]}'
    mock_openai.return_value.chat.completions.create.assert_called_once()
    call_kwargs = mock_openai.return_value.chat.completions.create.call_args.kwargs
    assert call_kwargs["model"] == "gpt-5.5"


def test_generate_plan_text_anthropic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    block = MagicMock(type="text", text='{"beats":[{"beat_id":"beat_001"}]}')
    mock_response = MagicMock(content=[block])

    with patch("anthropic.Anthropic") as mock_anthropic:
        mock_anthropic.return_value.messages.create.return_value = mock_response
        text = generate_plan_text(
            prompt="Plan this folder",
            model="anthropic:claude-sonnet-5",
        )

    assert "beat_001" in text
    call_kwargs = mock_anthropic.return_value.messages.create.call_args.kwargs
    assert call_kwargs["model"] == "claude-sonnet-5"


def test_generate_plan_text_raises_when_openai_key_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(PlanLlmNotConfiguredError, match="OPENAI_API_KEY"):
        generate_plan_text(prompt="x", model="openai:gpt-5.4-mini")


def test_generate_plan_text_gives_actionable_message_when_anthropic_sdk_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reproduziert "No module named 'anthropic'" (Paket in requirements.txt
    gelistet, aber im lokalen venv nicht installiert) — statt des kryptischen
    Python-ImportError muss eine klare, handlungsleitende Fehlermeldung mit
    Installationsbefehl kommen."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    with patch(
        "otio_app.services.plan_llm_client._require_sdk_module",
        side_effect=PlanLlmNotConfiguredError(
            "Das Python-Paket „anthropic“ ist in diesem Python-Environment "
            "nicht installiert. Bitte im Terminal (im aktivierten venv) "
            "ausführen: pip install -r requirements.txt"
        ),
    ):
        with pytest.raises(PlanLlmNotConfiguredError, match="pip install"):
            generate_plan_text(prompt="x", model="anthropic:claude-sonnet-5")


def test_require_sdk_module_raises_actionable_error_for_missing_package() -> None:
    from otio_app.services.plan_llm_client import _require_sdk_module

    with pytest.raises(PlanLlmNotConfiguredError) as exc_info:
        _require_sdk_module("this_package_does_not_exist_12345", pip_name="some-pip-name")

    message = str(exc_info.value)
    assert "some-pip-name" in message
    assert "pip install" in message


def test_require_sdk_module_returns_module_when_installed() -> None:
    from otio_app.services.plan_llm_client import _require_sdk_module

    module = _require_sdk_module("json")
    assert module.__name__ == "json"


def test_generate_plan_text_anthropic_retries_without_temperature_when_deprecated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reproduziert 'Error code: 400 [...] temperature is deprecated for this
    model.' — neuere Modelle lehnen eine explizite temperature ab. Statt
    fehlzuschlagen, muss die Erzeugung automatisch ohne temperature
    wiederholt werden und erfolgreich zurückkommen."""
    import anthropic

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    block = MagicMock(type="text", text='{"beats":[]}')
    success_response = MagicMock(content=[block])
    error = _bad_request_error(anthropic.BadRequestError, "temperature is deprecated for this model.")

    with patch("anthropic.Anthropic") as mock_anthropic:
        mock_anthropic.return_value.messages.create.side_effect = [error, success_response]
        text = generate_plan_text(prompt="Plan this folder", model="anthropic:claude-sonnet-5")

    assert text == '{"beats":[]}'
    assert mock_anthropic.return_value.messages.create.call_count == 2
    first_call_kwargs = mock_anthropic.return_value.messages.create.call_args_list[0].kwargs
    retry_call_kwargs = mock_anthropic.return_value.messages.create.call_args_list[1].kwargs
    assert first_call_kwargs["temperature"] == 0.2
    assert "temperature" not in retry_call_kwargs


def test_generate_plan_text_anthropic_reraises_unrelated_bad_request_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Andere 400-Fehler (z. B. ein tatsächlich ungültiges Modell) dürfen NICHT
    stillschweigend als 'temperature'-Problem behandelt werden."""
    import anthropic

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    error = _bad_request_error(anthropic.BadRequestError, "model: not-a-real-model is not a valid model ID.")

    with patch("anthropic.Anthropic") as mock_anthropic:
        mock_anthropic.return_value.messages.create.side_effect = error
        with pytest.raises(anthropic.BadRequestError):
            generate_plan_text(prompt="x", model="anthropic:claude-sonnet-5")

    assert mock_anthropic.return_value.messages.create.call_count == 1


def test_generate_plan_text_openai_retries_without_temperature_when_deprecated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import openai

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    success_response = MagicMock()
    success_response.choices = [MagicMock(message=MagicMock(content='{"beats":[]}'))]
    error = _bad_request_error(openai.BadRequestError, "temperature is deprecated for this model.")

    with patch("openai.OpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create.side_effect = [error, success_response]
        text = generate_plan_text(prompt="Plan this folder", model="openai:gpt-5.5")

    assert text == '{"beats":[]}'
    assert mock_openai.return_value.chat.completions.create.call_count == 2
    retry_call_kwargs = mock_openai.return_value.chat.completions.create.call_args_list[1].kwargs
    assert "temperature" not in retry_call_kwargs


def test_generate_plan_text_anthropic_uses_higher_max_tokens_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    block = MagicMock(type="text", text='{"beats":[]}')
    mock_response = MagicMock(content=[block], stop_reason="end_turn")

    with patch("anthropic.Anthropic") as mock_anthropic:
        mock_anthropic.return_value.messages.create.return_value = mock_response
        generate_plan_text(prompt="x", model="anthropic:claude-sonnet-5")

    call_kwargs = mock_anthropic.return_value.messages.create.call_args.kwargs
    assert call_kwargs["max_tokens"] == DEFAULT_MAX_OUTPUT_TOKENS
    assert DEFAULT_MAX_OUTPUT_TOKENS > 8192


def test_generate_plan_text_openai_uses_higher_max_tokens_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    mock_response = MagicMock()
    mock_response.choices = [
        MagicMock(message=MagicMock(content='{"beats":[]}'), finish_reason="stop")
    ]

    with patch("openai.OpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create.return_value = mock_response
        generate_plan_text(prompt="x", model="openai:gpt-5.5")

    call_kwargs = mock_openai.return_value.chat.completions.create.call_args.kwargs
    assert call_kwargs["max_tokens"] == DEFAULT_MAX_OUTPUT_TOKENS


def test_generate_plan_text_anthropic_raises_when_truncated_at_max_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reproduziert den gemeldeten Bug: die Dramaturgie-Antwort kam bei einem
    sehr großen Prompt (viele Ordner) exakt bei max_tokens=8192 abgeschnitten
    zurück (stop_reason='max_tokens', output_tokens==max_tokens) — vorher gab
    der Code stillschweigend '{}' zurück, was als 'erfolgreicher, aber leerer
    Plan' durchging. Jetzt muss das als expliziter Fehler erkannt werden."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    block = MagicMock(type="text", text='{"project_title": "Incomplete')
    mock_response = MagicMock(
        content=[block],
        stop_reason="max_tokens",
        usage=MagicMock(input_tokens=60389, output_tokens=DEFAULT_MAX_OUTPUT_TOKENS),
    )

    with patch("anthropic.Anthropic") as mock_anthropic:
        mock_anthropic.return_value.messages.create.return_value = mock_response
        with pytest.raises(PlanLlmTruncatedResponseError, match="max_tokens"):
            generate_plan_text(prompt="x", model="anthropic:claude-sonnet-5")


def test_generate_plan_text_anthropic_raises_when_no_text_block_returned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deckt auch den Fall ab, in dem das Modell seinen gesamten Output-Token-
    Budget für internes 'Thinking' verbraucht hat und GAR KEINEN finalen
    Text-Block liefert, selbst wenn stop_reason nicht exakt 'max_tokens' ist."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    thinking_block = MagicMock(type="thinking", text="")
    mock_response = MagicMock(content=[thinking_block], stop_reason="end_turn")

    with patch("anthropic.Anthropic") as mock_anthropic:
        mock_anthropic.return_value.messages.create.return_value = mock_response
        with pytest.raises(PlanLlmTruncatedResponseError):
            generate_plan_text(prompt="x", model="anthropic:claude-sonnet-5")


def test_generate_plan_text_openai_raises_when_truncated_at_max_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    mock_response = MagicMock()
    mock_response.choices = [
        MagicMock(message=MagicMock(content='{"project_title": "Incomplete'), finish_reason="length")
    ]

    with patch("openai.OpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create.return_value = mock_response
        with pytest.raises(PlanLlmTruncatedResponseError, match="length"):
            generate_plan_text(prompt="x", model="openai:gpt-5.5")


def test_generate_plan_text_openai_raises_when_no_message_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content=None), finish_reason="stop")]

    with patch("openai.OpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create.return_value = mock_response
        with pytest.raises(PlanLlmTruncatedResponseError):
            generate_plan_text(prompt="x", model="openai:gpt-5.5")


def test_generate_plan_text_gemini_raises_when_truncated_at_max_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    from google.genai import types

    mock_response = MagicMock(text="")
    mock_response.candidates = [MagicMock(finish_reason=types.FinishReason.MAX_TOKENS)]

    with patch("google.genai.Client") as mock_client_cls:
        mock_client_cls.return_value.models.generate_content.return_value = mock_response
        with pytest.raises(PlanLlmTruncatedResponseError, match="MAX_TOKENS"):
            generate_plan_text(prompt="x", model="gemini-3.1-pro-preview")


def test_generate_plan_text_gemini_raises_when_no_text_returned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    from google.genai import types

    mock_response = MagicMock(text="")
    mock_response.candidates = [MagicMock(finish_reason=types.FinishReason.STOP)]

    with patch("google.genai.Client") as mock_client_cls:
        mock_client_cls.return_value.models.generate_content.return_value = mock_response
        with pytest.raises(PlanLlmTruncatedResponseError):
            generate_plan_text(prompt="x", model="gemini-3.1-pro-preview")
