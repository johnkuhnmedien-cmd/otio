"""Tests für Multi-Provider Schnittplan-LLMs."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from otio_app.services.plan_llm_client import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    PlanLlmConnectionError,
    PlanLlmNotConfiguredError,
    PlanLlmTruncatedResponseError,
    format_plan_model_label,
    format_truncated_plan_response_error,
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


def _openai_stream_events(text: str, *, finish_reason: str = "stop"):
    first = MagicMock()
    first.usage = None
    first.choices = [MagicMock(delta=MagicMock(content=text), finish_reason=None)]
    last = MagicMock()
    last.usage = MagicMock(prompt_tokens=10, completion_tokens=20, total_tokens=30)
    last.choices = [MagicMock(delta=MagicMock(content=None), finish_reason=finish_reason)]
    return [first, last]


def _anthropic_stream_cm(final_message):
    stream = MagicMock()
    stream.get_final_message.return_value = final_message
    cm = MagicMock()
    cm.__enter__.return_value = stream
    cm.__exit__.return_value = False
    return cm


def _configure_anthropic_stream(client, response=None, *, side_effect=None):
    """Default 50k max_tokens läuft bei Anthropic immer per Stream."""
    if side_effect is not None:
        items = list(side_effect) if isinstance(side_effect, (list, tuple)) else [side_effect]
        client.messages.stream.side_effect = [
            item if isinstance(item, BaseException) else _anthropic_stream_cm(item)
            for item in items
        ]
        return
    client.messages.stream.return_value = _anthropic_stream_cm(response)


def _gemini_stream_chunks(text: str, *, finish_reason: str = "STOP"):
    chunk = MagicMock(text=text)
    chunk.usage_metadata = MagicMock(
        prompt_token_count=5, candidates_token_count=7, total_token_count=12
    )
    chunk.candidates = [MagicMock(finish_reason=finish_reason)]
    return [chunk]


def test_plan_model_provider_routes_by_prefix() -> None:
    assert plan_model_provider("gemini-3.1-pro-preview") == "gemini"
    assert plan_model_provider("openai:gpt-5.5") == "openai"
    assert plan_model_provider("anthropic:claude-opus-4-8") == "anthropic"
    assert plan_model_provider("xai:grok-4.5") == "xai"
    assert plan_model_provider("openrouter:x-ai/grok-4.5") == "openrouter"


def test_resolve_plan_model_accepts_openai_and_anthropic() -> None:
    assert resolve_plan_model("openai:gpt-5.4-mini") == "openai:gpt-5.4-mini"
    assert resolve_plan_model("anthropic:claude-sonnet-5") == "anthropic:claude-sonnet-5"
    assert resolve_plan_model("xai:grok-4.5") == "xai:grok-4.5"
    assert resolve_plan_model("openrouter:x-ai/grok-4.5") == "openrouter:x-ai/grok-4.5"


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

    with patch("openai.OpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create.return_value = _openai_stream_events(
            '{"beats":[]}'
        )
        text = generate_plan_text(prompt="Plan this folder", model="openai:gpt-5.5")

    assert text == '{"beats":[]}'
    mock_openai.return_value.chat.completions.create.assert_called_once()
    call_kwargs = mock_openai.return_value.chat.completions.create.call_args.kwargs
    assert call_kwargs["model"] == "gpt-5.5"
    assert call_kwargs["stream"] is True


def test_generate_plan_text_anthropic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    block = MagicMock(type="text", text='{"beats":[{"beat_id":"beat_001"}]}')
    mock_response = MagicMock(content=[block])

    with patch("anthropic.Anthropic") as mock_anthropic:
        _configure_anthropic_stream(mock_anthropic.return_value, mock_response)
        text = generate_plan_text(
            prompt="Plan this folder",
            model="anthropic:claude-sonnet-5",
        )

    assert "beat_001" in text
    call_kwargs = mock_anthropic.return_value.messages.stream.call_args.kwargs
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


def test_generate_plan_text_anthropic_retries_without_proxy_on_connection_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Früher Connect-/Proxy-Fail → zweiter Versuch mit trust_env=False."""
    import anthropic

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    block = MagicMock(type="text", text='{"beats":[]}')
    success = MagicMock(content=[block], stop_reason="end_turn")
    connection_error = anthropic.APIConnectionError(request=None)
    connection_error.__cause__ = OSError("Network is unreachable")

    clients: list[MagicMock] = []

    def _factory(*_args, **_kwargs):
        client = MagicMock()
        clients.append(client)
        if len(clients) == 1:
            client.messages.stream.side_effect = connection_error
        else:
            client.messages.stream.return_value = _anthropic_stream_cm(success)
        return client

    with patch("anthropic.Anthropic", side_effect=_factory):
        text = generate_plan_text(
            prompt="Plan this folder",
            model="anthropic:claude-sonnet-5",
        )

    assert text == '{"beats":[]}'
    assert len(clients) == 2


def test_generate_plan_text_anthropic_does_not_retry_billed_disconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Server disconnected after accept → kein Retry (sonst Mehrfach-Input-Kosten)."""
    import anthropic
    import httpx

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    connection_error = anthropic.APIConnectionError(request=None)
    connection_error.__cause__ = httpx.RemoteProtocolError(
        "Server disconnected without sending a response."
    )

    with patch("anthropic.Anthropic") as mock_anthropic:
        mock_anthropic.return_value.messages.stream.side_effect = connection_error
        with pytest.raises(PlanLlmConnectionError, match="kein automatischer Retry") as exc_info:
            generate_plan_text(prompt="x", model="anthropic:claude-sonnet-5")

    assert "disconnected" in str(exc_info.value).lower()
    assert mock_anthropic.call_count == 1


def test_generate_plan_text_anthropic_connection_error_is_actionable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import anthropic

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    connection_error = anthropic.APIConnectionError(request=None)
    connection_error.__cause__ = OSError("Network is unreachable")

    with patch("anthropic.Anthropic") as mock_anthropic:
        mock_anthropic.return_value.messages.stream.side_effect = connection_error
        with pytest.raises(PlanLlmConnectionError, match="Anthropic-Verbindung") as exc_info:
            generate_plan_text(prompt="x", model="anthropic:claude-sonnet-5")

    assert "Network is unreachable" in str(exc_info.value)
    assert mock_anthropic.call_count == 2  # trust_env True, then False


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
        _configure_anthropic_stream(
            mock_anthropic.return_value, side_effect=[error, success_response]
        )
        text = generate_plan_text(prompt="Plan this folder", model="anthropic:claude-sonnet-5")

    assert text == '{"beats":[]}'
    assert mock_anthropic.return_value.messages.stream.call_count == 2
    first_call_kwargs = mock_anthropic.return_value.messages.stream.call_args_list[0].kwargs
    retry_call_kwargs = mock_anthropic.return_value.messages.stream.call_args_list[1].kwargs
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
        mock_anthropic.return_value.messages.stream.side_effect = error
        with pytest.raises(anthropic.BadRequestError):
            generate_plan_text(prompt="x", model="anthropic:claude-sonnet-5")

    assert mock_anthropic.return_value.messages.stream.call_count == 1


def test_generate_plan_text_openai_retries_without_temperature_when_deprecated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import openai

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    error = _bad_request_error(openai.BadRequestError, "temperature is deprecated for this model.")

    with patch("openai.OpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create.side_effect = [
            error,
            _openai_stream_events('{"beats":[]}'),
        ]
        text = generate_plan_text(prompt="Plan this folder", model="openai:gpt-5.5")

    assert text == '{"beats":[]}'
    assert mock_openai.return_value.chat.completions.create.call_count == 2
    retry_call_kwargs = mock_openai.return_value.chat.completions.create.call_args_list[1].kwargs
    assert "temperature" not in retry_call_kwargs
    assert retry_call_kwargs["stream"] is True


def test_generate_plan_text_anthropic_uses_higher_max_tokens_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    block = MagicMock(type="text", text='{"beats":[]}')
    mock_response = MagicMock(content=[block], stop_reason="end_turn")

    with patch("anthropic.Anthropic") as mock_anthropic:
        _configure_anthropic_stream(mock_anthropic.return_value, mock_response)
        generate_plan_text(prompt="x", model="anthropic:claude-sonnet-5")

    call_kwargs = mock_anthropic.return_value.messages.stream.call_args.kwargs
    assert call_kwargs["max_tokens"] == DEFAULT_MAX_OUTPUT_TOKENS
    assert DEFAULT_MAX_OUTPUT_TOKENS == 100_000


def test_generate_plan_text_openai_uses_higher_max_tokens_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    with patch("openai.OpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create.return_value = _openai_stream_events(
            '{"beats":[]}'
        )
        generate_plan_text(prompt="x", model="openai:gpt-5.5")

    call_kwargs = mock_openai.return_value.chat.completions.create.call_args.kwargs
    assert call_kwargs["max_tokens"] == DEFAULT_MAX_OUTPUT_TOKENS
    assert call_kwargs["stream"] is True
    assert DEFAULT_MAX_OUTPUT_TOKENS == 100_000


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
        _configure_anthropic_stream(mock_anthropic.return_value, mock_response)
        with pytest.raises(PlanLlmTruncatedResponseError, match="max_tokens"):
            generate_plan_text(prompt="x", model="anthropic:claude-sonnet-5")


def test_format_truncated_plan_response_error_names_limit_not_auto_run() -> None:
    text = format_truncated_plan_response_error(
        stop_reason="max_tokens",
        max_output_tokens=16384,
        output_tokens=16384,
    )
    assert "nach 16384 von max_tokens=16384" in text
    assert "stop_reason=max_tokens" in text
    assert "dieses einen LLM-Aufrufs" in text
    assert "Auto-Lauf insgesamt" in text
    assert "weniger Ordner" in text


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
        _configure_anthropic_stream(mock_anthropic.return_value, mock_response)
        with pytest.raises(PlanLlmTruncatedResponseError):
            generate_plan_text(prompt="x", model="anthropic:claude-sonnet-5")


def test_generate_plan_text_openai_raises_when_truncated_at_max_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    with patch("openai.OpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create.return_value = _openai_stream_events(
            '{"project_title": "Incomplete', finish_reason="length"
        )
        with pytest.raises(PlanLlmTruncatedResponseError, match="length"):
            generate_plan_text(prompt="x", model="openai:gpt-5.5")


def test_generate_plan_text_openai_raises_when_no_message_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    with patch("openai.OpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create.return_value = _openai_stream_events("")
        with pytest.raises(PlanLlmTruncatedResponseError):
            generate_plan_text(prompt="x", model="openai:gpt-5.5")


def test_generate_plan_text_gemini_raises_when_truncated_at_max_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    from google.genai import types

    with patch("google.genai.Client") as mock_client_cls:
        mock_client_cls.return_value.models.generate_content_stream.return_value = (
            _gemini_stream_chunks("", finish_reason=types.FinishReason.MAX_TOKENS)
        )
        with pytest.raises(PlanLlmTruncatedResponseError, match="MAX_TOKENS"):
            generate_plan_text(prompt="x", model="gemini-3.1-pro-preview")


def test_generate_plan_text_gemini_raises_when_no_text_returned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    from google.genai import types

    with patch("google.genai.Client") as mock_client_cls:
        mock_client_cls.return_value.models.generate_content_stream.return_value = (
            _gemini_stream_chunks("", finish_reason=types.FinishReason.STOP)
        )
        with pytest.raises(PlanLlmTruncatedResponseError):
            generate_plan_text(prompt="x", model="gemini-3.1-pro-preview")


# --- Nutzerfeedback: Option A (max_output_tokens pro Call erhöhbar) und
# Option B (disable_thinking pro Call) für sehr umfangreiche Dramaturgie-
# Prompts (z. B. 37 Ordner) ---


def test_generate_plan_text_anthropic_accepts_custom_max_output_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hohe max_tokens (> ~21k) müssen per Streaming laufen (Anthropic-SDK)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    block = MagicMock(type="text", text='{"beats":[]}')
    mock_response = MagicMock(content=[block], stop_reason="end_turn")

    with patch("anthropic.Anthropic") as mock_anthropic:
        mock_anthropic.return_value.messages.stream.return_value = _anthropic_stream_cm(
            mock_response
        )
        generate_plan_text(prompt="x", model="anthropic:claude-sonnet-5", max_output_tokens=32768)

    mock_anthropic.return_value.messages.create.assert_not_called()
    call_kwargs = mock_anthropic.return_value.messages.stream.call_args.kwargs
    assert call_kwargs["max_tokens"] == 32768
    assert call_kwargs["temperature"] == 0.2


def test_generate_plan_text_anthropic_without_override_still_uses_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    block = MagicMock(type="text", text='{"beats":[]}')
    mock_response = MagicMock(content=[block], stop_reason="end_turn")

    with patch("anthropic.Anthropic") as mock_anthropic:
        _configure_anthropic_stream(mock_anthropic.return_value, mock_response)
        generate_plan_text(prompt="x", model="anthropic:claude-sonnet-5")

    call_kwargs = mock_anthropic.return_value.messages.stream.call_args.kwargs
    assert call_kwargs["max_tokens"] == DEFAULT_MAX_OUTPUT_TOKENS
    mock_anthropic.return_value.messages.create.assert_not_called()


def test_generate_plan_text_anthropic_disable_thinking_sets_thinking_param(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    block = MagicMock(type="text", text='{"beats":[]}')
    mock_response = MagicMock(content=[block], stop_reason="end_turn")

    with patch("anthropic.Anthropic") as mock_anthropic:
        _configure_anthropic_stream(mock_anthropic.return_value, mock_response)
        generate_plan_text(prompt="x", model="anthropic:claude-sonnet-5", disable_thinking=True)

    call_kwargs = mock_anthropic.return_value.messages.stream.call_args.kwargs
    assert call_kwargs["thinking"] == {"type": "disabled"}


def test_generate_plan_text_anthropic_thinking_not_set_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    block = MagicMock(type="text", text='{"beats":[]}')
    mock_response = MagicMock(content=[block], stop_reason="end_turn")

    with patch("anthropic.Anthropic") as mock_anthropic:
        _configure_anthropic_stream(mock_anthropic.return_value, mock_response)
        generate_plan_text(prompt="x", model="anthropic:claude-sonnet-5")

    call_kwargs = mock_anthropic.return_value.messages.stream.call_args.kwargs
    assert "thinking" not in call_kwargs


def test_generate_plan_text_anthropic_disable_thinking_survives_temperature_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """disable_thinking und max_output_tokens müssen auch im Streaming-Retry
    (ohne temperature) erhalten bleiben."""
    import anthropic

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    block = MagicMock(type="text", text='{"beats":[]}')
    success_response = MagicMock(content=[block], stop_reason="end_turn")
    error = _bad_request_error(anthropic.BadRequestError, "temperature is deprecated for this model.")

    with patch("anthropic.Anthropic") as mock_anthropic:
        mock_anthropic.return_value.messages.stream.side_effect = [
            error,
            _anthropic_stream_cm(success_response),
        ]
        generate_plan_text(
            prompt="x",
            model="anthropic:claude-sonnet-5",
            max_output_tokens=32768,
            disable_thinking=True,
        )

    retry_call_kwargs = mock_anthropic.return_value.messages.stream.call_args_list[1].kwargs
    assert retry_call_kwargs["max_tokens"] == 32768
    assert retry_call_kwargs["thinking"] == {"type": "disabled"}
    assert "temperature" not in retry_call_kwargs


def test_generate_plan_text_gemini_accepts_custom_max_output_tokens_and_disable_thinking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    with patch("google.genai.Client") as mock_client_cls:
        mock_client_cls.return_value.models.generate_content_stream.return_value = (
            _gemini_stream_chunks('{"beats":[]}')
        )
        generate_plan_text(
            prompt="x",
            model="gemini-3.1-pro-preview",
            max_output_tokens=32768,
            disable_thinking=True,
        )

    call_kwargs = mock_client_cls.return_value.models.generate_content_stream.call_args.kwargs
    config = call_kwargs["config"]
    assert config.max_output_tokens == 32768
    assert config.thinking_config.thinking_budget == 0


def test_generate_plan_text_openai_accepts_custom_max_output_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    with patch("openai.OpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create.return_value = _openai_stream_events(
            '{"beats":[]}'
        )
        generate_plan_text(prompt="x", model="openai:gpt-5.5", max_output_tokens=32768)

    call_kwargs = mock_openai.return_value.chat.completions.create.call_args.kwargs
    assert call_kwargs["max_tokens"] == 32768
    assert call_kwargs["stream"] is True


def test_generate_plan_text_openai_retries_with_max_completion_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GPT-5.x lehnt max_tokens ab und verlangt max_completion_tokens."""
    from openai import BadRequestError

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    with patch("openai.OpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create.side_effect = [
            _bad_request_error(
                BadRequestError,
                "Unsupported parameter: 'max_tokens' is not supported with this model. "
                "Use 'max_completion_tokens' instead.",
            ),
            _openai_stream_events('{"beats":[]}'),
        ]
        text = generate_plan_text(prompt="x", model="openai:gpt-5.5", max_output_tokens=32768)

    assert text == '{"beats":[]}'
    assert mock_openai.return_value.chat.completions.create.call_count == 2
    retry_kwargs = mock_openai.return_value.chat.completions.create.call_args.kwargs
    assert retry_kwargs["max_completion_tokens"] == 32768
    assert "max_tokens" not in retry_kwargs
    assert retry_kwargs["stream"] is True


def test_generate_plan_text_xai_grok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XAI_API_KEY", "xai-test")

    with patch("openai.OpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create.return_value = _openai_stream_events(
            '{"beats":[]}'
        )
        text = generate_plan_text(prompt="Plan this folder", model="xai:grok-4.5")

    assert text == '{"beats":[]}'
    mock_openai.assert_called_once()
    assert mock_openai.call_args.kwargs["base_url"] == "https://api.x.ai/v1"
    assert mock_openai.call_args.kwargs["api_key"] == "xai-test"
    call_kwargs = mock_openai.return_value.chat.completions.create.call_args.kwargs
    assert call_kwargs["model"] == "grok-4.5"


def test_generate_plan_text_openrouter_grok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")

    with patch("openai.OpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create.return_value = _openai_stream_events(
            '{"beats":[]}'
        )
        text = generate_plan_text(prompt="Plan this folder", model="openrouter:x-ai/grok-4.5")

    assert text == '{"beats":[]}'
    mock_openai.assert_called_once()
    assert mock_openai.call_args.kwargs["base_url"] == "https://openrouter.ai/api/v1"
    assert mock_openai.call_args.kwargs["api_key"] == "sk-or-test"
    assert mock_openai.call_args.kwargs["default_headers"]["X-Title"] == "OTIO Voiceover Generation"
    call_kwargs = mock_openai.return_value.chat.completions.create.call_args.kwargs
    assert call_kwargs["model"] == "x-ai/grok-4.5"


def test_generate_plan_text_raises_when_xai_key_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    with pytest.raises(PlanLlmNotConfiguredError, match="XAI_API_KEY"):
        generate_plan_text(prompt="x", model="xai:grok-4.5")


def test_generate_plan_text_raises_when_openrouter_key_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(PlanLlmNotConfiguredError, match="OPENROUTER_API_KEY"):
        generate_plan_text(prompt="x", model="openrouter:x-ai/grok-4.5")


def test_is_plan_model_configured_checks_xai_and_openrouter_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XAI_API_KEY", "xai-test")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert is_plan_model_configured("xai:grok-4.5") is True
    assert is_plan_model_configured("openrouter:x-ai/grok-4.5") is False

    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    assert is_plan_model_configured("openrouter:x-ai/grok-4.5") is True
