"""Text-LLM-Aufrufe für Schnittplan-Vorschläge (Gemini, OpenAI, Anthropic, xAI, OpenRouter)."""

from __future__ import annotations

import base64
import mimetypes
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Optional, Sequence

from otio_app.config import get_gemini_model_from_env
from otio_app.defaults import EDIT_PLAN_MODEL_CHOICES, EDIT_PLAN_MODEL_LABELS, GEMINI_MODEL_CHOICES
from otio_app.services.api_keys import get_api_key, is_api_key_set

PROVIDER_GEMINI = "gemini"
PROVIDER_OPENAI = "openai"
PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_XAI = "xai"
PROVIDER_OPENROUTER = "openrouter"

# OpenAI-kompatible Chat-Completions-APIs (xAI / OpenRouter).
XAI_API_BASE_URL = "https://api.x.ai/v1"
OPENROUTER_API_BASE_URL = "https://openrouter.ai/api/v1"

_PROVIDER_ENV_KEYS = {
    PROVIDER_GEMINI: "GEMINI_API_KEY",
    PROVIDER_OPENAI: "OPENAI_API_KEY",
    PROVIDER_ANTHROPIC: "ANTHROPIC_API_KEY",
    PROVIDER_XAI: "XAI_API_KEY",
    PROVIDER_OPENROUTER: "OPENROUTER_API_KEY",
}


class PlanLlmNotConfiguredError(RuntimeError):
    """API-Schlüssel für das gewählte Planungsmodell fehlt."""


class PlanLlmTruncatedResponseError(RuntimeError):
    """Die Modellantwort wurde abgeschnitten (max_tokens erreicht) oder enthielt
    keinen verwertbaren Text (z. B. nur interne 'Thinking'-Tokens ohne finale
    Antwort). Wird bewusst als Fehler behandelt statt — wie zuvor — stillschweigend
    als leeres JSON-Objekt "{}" durchgereicht zu werden: eine leere, aber
    "erfolgreich" geparste Antwort sah für den Aufrufer wie ein normales, nur
    inhaltlich dürftiges Ergebnis aus (z. B. ein Dramaturgie-Plan ohne jeden
    Ordner), obwohl in Wahrheit gar keine brauchbare Antwort vorlag."""


class PlanLlmConnectionError(RuntimeError):
    """Transport-/Netzwerkfehler zum LLM-Provider (kein HTTP-Status vom Modell)."""


class PlanLlmCancelledError(RuntimeError):
    """Nutzer hat Stop gedrückt — der laufende LLM-HTTP-Call wurde abgebrochen."""


_llm_cancel_check: ContextVar[Callable[[], bool] | None] = ContextVar(
    "otio_llm_cancel_check", default=None
)
_active_http_lock = threading.Lock()
_active_http_closers: list[Callable[[], None]] = []


@contextmanager
def bind_llm_cancel(should_cancel: Callable[[], bool] | None) -> Iterator[None]:
    """Bindet den Auto-Lauf-Stop an LLM-HTTP-Calls in diesem Thread."""
    if should_cancel is None:
        yield
        return
    token = _llm_cancel_check.set(should_cancel)
    try:
        yield
    finally:
        _llm_cancel_check.reset(token)


def llm_cancel_requested() -> bool:
    checker = _llm_cancel_check.get()
    return bool(checker and checker())


def raise_if_llm_cancelled() -> None:
    if llm_cancel_requested():
        raise PlanLlmCancelledError("LLM-Aufruf abgebrochen.")


def reraise_if_llm_cancelled(exc: BaseException | None = None) -> None:
    """Wenn Stop aktiv ist, Cancel weiterwerfen statt als normalen LLM-Fehler."""
    if isinstance(exc, PlanLlmCancelledError):
        raise exc
    if llm_cancel_requested():
        raise PlanLlmCancelledError("LLM-Aufruf abgebrochen.") from exc


def abort_registered_llm_http() -> None:
    """Schließt alle registrierten LLM-HTTP-Clients — auch aus einem anderen Thread."""
    with _active_http_lock:
        closers = list(_active_http_closers)
    for close in closers:
        try:
            close()
        except Exception:
            pass


@contextmanager
def cancellable_httpx_client(**client_kwargs):
    """Eigenes httpx.Client, das Stop schließen kann.

    Ohne gebundenen Cancel-Checker: kein Extra-Client (SDK-Default bleibt).
    """
    if _llm_cancel_check.get() is None:
        yield None
        return
    import httpx

    factory = client_kwargs.pop("factory", None)
    timeout = client_kwargs.pop("timeout", _LLM_REQUEST_TIMEOUT_SEC)
    if factory is None:
        client = httpx.Client(timeout=timeout, **client_kwargs)
    else:
        client = factory(timeout=timeout, **client_kwargs)
    closed = False
    close_lock = threading.Lock()

    def close() -> None:
        nonlocal closed
        with close_lock:
            if closed:
                return
            closed = True
        try:
            client.close()
        except Exception:
            pass

    stop_watch = threading.Event()

    def watch() -> None:
        while not stop_watch.wait(0.2):
            if llm_cancel_requested():
                close()
                return

    with _active_http_lock:
        _active_http_closers.append(close)
    watcher = threading.Thread(
        target=watch, daemon=True, name="llm-cancel-watch"
    )
    watcher.start()
    try:
        raise_if_llm_cancelled()
        yield client
    except PlanLlmCancelledError:
        raise
    except Exception as exc:
        if llm_cancel_requested():
            raise PlanLlmCancelledError("LLM-Aufruf abgebrochen.") from exc
        raise
    finally:
        stop_watch.set()
        close()
        with _active_http_lock:
            try:
                _active_http_closers.remove(close)
            except ValueError:
                pass


def _iter_checking_cancel(iterable):
    raise_if_llm_cancelled()
    for item in iterable:
        raise_if_llm_cancelled()
        yield item


def format_truncated_plan_response_error(
    *,
    stop_reason: str,
    max_output_tokens: int,
    output_tokens: int | None = None,
    provider_label: str = "",
) -> str:
    """Nutzertext, wenn die LLM-Antwort am Output-Token-Limit endet.

    Bleibt bewusst unabhängig vom Aufrufer (Dramaturgie, Kapitel-Cut, …):
    welcher Prozess gemeint ist, sagt der Auto-Lauf-Schritt davor.
    """
    reason = (stop_reason or "max_tokens").strip()
    used = output_tokens if output_tokens is not None else max_output_tokens
    who = f"Die {provider_label}-Antwort" if provider_label.strip() else "Die Antwort"
    return (
        f"{who} wurde nach {used} von max_tokens={max_output_tokens} Output-Tokens "
        f"abgeschnitten (stop_reason={reason}). "
        "Das Output-Token-Limit dieses einen LLM-Aufrufs war voll, bevor der Plan "
        "vollständig war — nicht der Auto-Lauf insgesamt. "
        "Bitte den Prompt kürzen; bei der Dramaturgie hilft z. B., weniger Ordner "
        "gleichzeitig zu planen."
    )


# Ceiling, kein Target: ungenutzte Tokens werden nicht abgerechnet.
# 16k/50k reichten bei Intro und umfangreicher Dramaturgie nicht.
DEFAULT_MAX_OUTPUT_TOKENS = 100_000

# Anthropic SDK: non-streaming wird abgelehnt, wenn expected_time > 10 Min
# (Formel: 3600 * max_tokens / 128000). Ab ~21334 Tokens ist Streaming nötig.
# Der 50k-Default liegt darüber und läuft deshalb immer per Stream.
_ANTHROPIC_NONSTREAMING_MAX_TOKENS = 20_000

# Lange Dramaturgie-/Plan-Calls: genug Spielraum gegen Idle-Timeouts, ohne
# die Antwortqualität zu ändern (Timeout betrifft nur die HTTP-Schicht).
# 50k Output braucht mehr Wandzeit als der frühere 16k-Default.
_LLM_REQUEST_TIMEOUT_SEC = 1_200.0
_GEMINI_HTTP_TIMEOUT_MS = 1_200_000


@dataclass(frozen=True)
class PlanImageAttachment:
    """Lokales Bild für multimodale Plan-Calls (z. B. Mittel-Frame pro Asset)."""

    path: Path
    label: str = ""
    mime_type: str = "image/jpeg"

    def resolve_mime(self) -> str:
        if self.mime_type:
            return self.mime_type
        guessed, _ = mimetypes.guess_type(str(self.path))
        return guessed or "image/jpeg"


@dataclass
class PlanLlmResponse:
    provider: str
    model: str
    raw_text: str
    latency_ms: int = 0
    token_usage: dict[str, int] = field(default_factory=dict)
    resolved_model_id: str = ""


def plan_model_provider(model_id: str | None) -> str:
    value = (model_id or "").strip()
    if value.startswith("openai:"):
        return PROVIDER_OPENAI
    if value.startswith("anthropic:"):
        return PROVIDER_ANTHROPIC
    if value.startswith("xai:"):
        return PROVIDER_XAI
    if value.startswith("openrouter:"):
        return PROVIDER_OPENROUTER
    return PROVIDER_GEMINI


def _provider_api_model(model_id: str) -> str:
    if ":" in model_id:
        return model_id.split(":", 1)[1]
    return model_id


def resolve_plan_model(model: Optional[str] = None) -> str:
    """Ermittelt das Planungsmodell (UI-Auswahl > .env > Standard)."""
    if model and model.strip():
        value = model.strip()
        if value in EDIT_PLAN_MODEL_CHOICES:
            return value
        if (
            value.startswith("openai:")
            or value.startswith("anthropic:")
            or value.startswith("xai:")
            or value.startswith("openrouter:")
        ):
            return value
        if value in GEMINI_MODEL_CHOICES:
            return value
        return value
    return get_gemini_model_from_env()


def get_default_plan_model() -> str:
    return get_gemini_model_from_env()


def format_plan_model_label(model_id: str) -> str:
    return EDIT_PLAN_MODEL_LABELS.get(model_id, model_id)


def is_plan_model_configured(model_id: str | None) -> bool:
    provider = plan_model_provider(model_id)
    env_key = _PROVIDER_ENV_KEYS[provider]
    return is_api_key_set(env_key)


def is_any_plan_llm_configured() -> bool:
    return any(is_api_key_set(env_key) for env_key in _PROVIDER_ENV_KEYS.values())


def generate_plan_text(
    *,
    prompt: str,
    model: Optional[str] = None,
    max_output_tokens: int | None = None,
    disable_thinking: bool = False,
    images: Sequence[PlanImageAttachment] | None = None,
    project: Any = None,
    stage: str = "",
    folder_name: str = "",
) -> str:
    """Sendet den Schnittplan-Prompt an das gewählte Text-LLM."""
    return generate_plan_text_with_metadata(
        prompt=prompt,
        model=model,
        max_output_tokens=max_output_tokens,
        disable_thinking=disable_thinking,
        images=images,
        project=project,
        stage=stage,
        folder_name=folder_name,
    ).raw_text


def generate_plan_text_with_metadata(
    *,
    prompt: str,
    model: Optional[str] = None,
    max_output_tokens: int | None = None,
    disable_thinking: bool = False,
    images: Sequence[PlanImageAttachment] | None = None,
    project: Any = None,
    stage: str = "",
    folder_name: str = "",
) -> PlanLlmResponse:
    """Wie generate_plan_text, inkl. Latenz und Token-Nutzung für Diagnose-Runs.

    max_output_tokens überschreibt DEFAULT_MAX_OUTPUT_TOKENS für diesen Call
    (z. B. für sehr umfangreiche Prompts wie eine Dramaturgie-Planung über
    viele Ordner). disable_thinking schaltet, sofern vom Provider unterstützt
    (Anthropic, Gemini), das interne "Thinking" des Modells für diesen Call
    aus — damit steht das gesamte max_output_tokens-Budget der sichtbaren
    Antwort zur Verfügung, statt (teilweise) für internes Reasoning verbraucht
    zu werden. Für OpenAI-Modelle über die Chat-Completions-API hat
    disable_thinking aktuell keine Wirkung (kein äquivalenter Parameter).

    images: optionale lokale Mittel-Frames (Gemini + OpenAI). Andere Provider
    lehnen Bilder mit PlanLlmNotConfiguredError ab.
    """
    raise_if_llm_cancelled()
    resolved = resolve_plan_model(model)
    provider = plan_model_provider(resolved)
    api_model = _provider_api_model(resolved)
    started = time.perf_counter()
    image_list = list(images or [])

    try:
        response = _dispatch_plan_text(
            provider=provider,
            api_model=api_model,
            resolved=resolved,
            prompt=prompt,
            max_output_tokens=max_output_tokens,
            disable_thinking=disable_thinking,
            image_list=image_list,
            started=started,
        )
    except PlanLlmCancelledError:
        raise
    except Exception as exc:
        if llm_cancel_requested():
            raise PlanLlmCancelledError("LLM-Aufruf abgebrochen.") from exc
        raise
    _record_plan_llm_cost_safe(
        project=project,
        stage=stage,
        folder_name=folder_name,
        response=response,
    )
    return response


def _record_plan_llm_cost_safe(
    *,
    project: Any,
    stage: str,
    folder_name: str,
    response: PlanLlmResponse,
) -> None:
    try:
        from otio_app.services.voiceover_generation.llm_cost_ledger import (
            record_plan_llm_cost,
        )

        record_plan_llm_cost(
            project=project,
            stage=stage,
            folder_name=folder_name,
            provider=response.provider,
            model=response.resolved_model_id or response.model,
            token_usage=response.token_usage,
            status="ok",
        )
    except Exception:
        return


def _dispatch_plan_text(
    *,
    provider: str,
    api_model: str,
    resolved: str,
    prompt: str,
    max_output_tokens: int | None,
    disable_thinking: bool,
    image_list: list[PlanImageAttachment],
    started: float,
) -> PlanLlmResponse:
    if provider == PROVIDER_GEMINI:
        raw_text, token_usage = _generate_gemini_text_with_usage(
            prompt=prompt,
            model=api_model,
            max_output_tokens=max_output_tokens,
            disable_thinking=disable_thinking,
            images=image_list,
        )
    elif provider == PROVIDER_OPENAI:
        raw_text, token_usage = _generate_openai_text_with_usage(
            prompt=prompt,
            model=api_model,
            max_output_tokens=max_output_tokens,
            disable_thinking=disable_thinking,
            images=image_list,
        )
    elif provider == PROVIDER_ANTHROPIC:
        if image_list:
            raise PlanLlmNotConfiguredError(
                "Mittel-Frames für LLM-Lauf 2 sind mit Anthropic hier noch "
                "nicht verdrahtet. Bitte Gemini oder OpenAI (Terra/Sol) wählen "
                "oder die Option deaktivieren."
            )
        raw_text, token_usage = _generate_anthropic_text_with_usage(
            prompt=prompt,
            model=api_model,
            max_output_tokens=max_output_tokens,
            disable_thinking=disable_thinking,
        )
    elif provider == PROVIDER_XAI:
        if image_list:
            raise PlanLlmNotConfiguredError(
                "Mittel-Frames für LLM-Lauf 2 sind mit xAI hier noch nicht "
                "verdrahtet. Bitte Gemini oder OpenAI wählen oder die Option "
                "deaktivieren."
            )
        raw_text, token_usage = _generate_xai_text_with_usage(
            prompt=prompt,
            model=api_model,
            max_output_tokens=max_output_tokens,
            disable_thinking=disable_thinking,
        )
    elif provider == PROVIDER_OPENROUTER:
        if image_list:
            raise PlanLlmNotConfiguredError(
                "Mittel-Frames für LLM-Lauf 2 sind mit OpenRouter hier noch "
                "nicht verdrahtet. Bitte Gemini oder OpenAI wählen oder die "
                "Option deaktivieren."
            )
        raw_text, token_usage = _generate_openrouter_text_with_usage(
            prompt=prompt,
            model=api_model,
            max_output_tokens=max_output_tokens,
            disable_thinking=disable_thinking,
        )
    else:
        raise PlanLlmNotConfiguredError(f"Unbekannter Planungs-Provider für Modell `{resolved}`.")

    latency_ms = int((time.perf_counter() - started) * 1000)
    return PlanLlmResponse(
        provider=provider,
        model=api_model,
        raw_text=raw_text,
        latency_ms=latency_ms,
        token_usage=token_usage,
        resolved_model_id=resolved,
    )


def _token_usage_dict(
    *,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    total_tokens: int | None = None,
) -> dict[str, int]:
    usage: dict[str, int] = {}
    if input_tokens is not None:
        usage["input_tokens"] = int(input_tokens)
    if output_tokens is not None:
        usage["output_tokens"] = int(output_tokens)
    if total_tokens is not None:
        usage["total_tokens"] = int(total_tokens)
    elif input_tokens is not None and output_tokens is not None:
        usage["total_tokens"] = int(input_tokens) + int(output_tokens)
    return usage


def _require_sdk_module(package_name: str, pip_name: str | None = None):
    """Importiert ein LLM-SDK-Paket mit einer klaren, handlungsleitenden
    Fehlermeldung statt eines kryptischen "No module named ..." — z. B. wenn
    requirements.txt zwar das Paket listet, es aber im lokalen Python-
    Environment (noch) nicht installiert wurde (`pip install -r
    requirements.txt` nicht/erneut ausgeführt)."""
    import importlib

    try:
        return importlib.import_module(package_name)
    except ModuleNotFoundError as exc:
        raise PlanLlmNotConfiguredError(
            f"Das Python-Paket „{pip_name or package_name}“ ist in diesem "
            "Python-Environment nicht installiert. Bitte im Terminal (im "
            "aktivierten venv) ausführen: "
            f"pip install -r requirements.txt   (oder: pip install {pip_name or package_name})"
        ) from exc


def _is_temperature_rejected_error(exc: Exception) -> bool:
    """Erkennt API-Fehler wie "temperature is deprecated for this model." —
    manche (v. a. neuere Reasoning-)Modelle akzeptieren nur noch den API-
    Standardwert und lehnen eine explizit gesetzte temperature mit HTTP 400
    ab. Wird genutzt, um genau in diesem Fall (und nur in diesem Fall) ohne
    temperature erneut zu versuchen."""
    message = str(exc).lower()
    return "temperature" in message


def _gemini_user_parts(
    prompt: str,
    images: Sequence[PlanImageAttachment],
    types_module,
) -> list:
    parts = [types_module.Part.from_text(text=prompt)]
    for image in images:
        path = Path(image.path)
        if not path.is_file():
            continue
        label = (image.label or path.name).strip()
        if label:
            parts.append(
                types_module.Part.from_text(
                    text=f"IMAGE for local_asset_id={label}"
                )
            )
        parts.append(
            types_module.Part.from_bytes(
                data=path.read_bytes(),
                mime_type=image.resolve_mime(),
            )
        )
    return parts


def _openai_user_content(
    prompt: str,
    images: Sequence[PlanImageAttachment],
) -> str | list[dict]:
    if not images:
        return prompt
    content: list[dict] = [{"type": "text", "text": prompt}]
    for image in images:
        path = Path(image.path)
        if not path.is_file():
            continue
        label = (image.label or path.name).strip()
        if label:
            content.append(
                {
                    "type": "text",
                    "text": f"IMAGE for local_asset_id={label}",
                }
            )
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        mime = image.resolve_mime()
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{encoded}"},
            }
        )
    return content


def _generate_gemini_text_with_usage(
    *,
    prompt: str,
    model: str,
    max_output_tokens: int | None = None,
    disable_thinking: bool = False,
    images: Sequence[PlanImageAttachment] | None = None,
) -> tuple[str, dict[str, int]]:
    api_key = get_api_key("GEMINI_API_KEY")
    if not api_key:
        raise PlanLlmNotConfiguredError(
            "GEMINI_API_KEY ist nicht gesetzt. "
            "Bitte unter 🔑 API-Schlüssel oder in .env eintragen."
        )
    _require_sdk_module("google.genai", pip_name="google-genai")
    from google import genai
    from google.genai import types

    if model not in GEMINI_MODEL_CHOICES:
        model = get_gemini_model_from_env()

    effective_max_tokens = max_output_tokens or DEFAULT_MAX_OUTPUT_TOKENS
    config_kwargs: dict = {"max_output_tokens": effective_max_tokens}
    if disable_thinking:
        # thinking_budget=0 schaltet das interne "Thinking" ab — das gesamte
        # max_output_tokens-Budget steht dann der sichtbaren Antwort zur
        # Verfügung (siehe generate_plan_text_with_metadata()-Docstring).
        config_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)

    http_options_kwargs: dict = {"timeout": _GEMINI_HTTP_TIMEOUT_MS}
    with cancellable_httpx_client() as http:
        if http is not None:
            http_options_kwargs["httpx_client"] = http
        client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(**http_options_kwargs),
        )
        user_parts = _gemini_user_parts(prompt, list(images or []), types)
        # Streaming hält die Verbindung bei langen Antworten offen; am Ende
        # aggregieren wir denselben Text wie bei einem Non-Stream-Call.
        stream = client.models.generate_content_stream(
            model=model,
            contents=[types.Content(role="user", parts=user_parts)],
            config=types.GenerateContentConfig(**config_kwargs),
        )
        return _consume_gemini_stream(
            stream,
            effective_max_tokens=effective_max_tokens,
        )


def _consume_gemini_stream(stream, *, effective_max_tokens: int) -> tuple[str, dict[str, int]]:
    text_parts: list[str] = []
    token_usage: dict[str, int] = {}
    finish_reason = ""
    for chunk in _iter_checking_cancel(stream):
        chunk_text = getattr(chunk, "text", None)
        if chunk_text:
            text_parts.append(chunk_text)
        usage_meta = getattr(chunk, "usage_metadata", None)
        if usage_meta is not None:
            token_usage = _token_usage_dict(
                input_tokens=getattr(usage_meta, "prompt_token_count", None),
                output_tokens=getattr(usage_meta, "candidates_token_count", None),
                total_tokens=getattr(usage_meta, "total_token_count", None),
            )
        candidates = getattr(chunk, "candidates", None) or []
        if candidates:
            finish_reason = str(getattr(candidates[0], "finish_reason", "") or finish_reason)
    if "MAX_TOKENS" in finish_reason:
        raise PlanLlmTruncatedResponseError(
            format_truncated_plan_response_error(
                stop_reason="MAX_TOKENS",
                max_output_tokens=effective_max_tokens,
                output_tokens=token_usage.get("output_tokens"),
                provider_label="Gemini",
            )
        )
    text = "".join(text_parts).strip()
    if not text:
        raise PlanLlmTruncatedResponseError(
            "Gemini hat keinen verwertbaren Text zurückgegeben. Bitte erneut versuchen."
        )
    return text, token_usage


def _is_max_tokens_param_rejected_error(exc: Exception) -> bool:
    """Erkennt OpenAI-Fehler wie: max_tokens is not supported … Use
    max_completion_tokens instead (GPT-5.x u. a.)."""
    message = str(exc).lower()
    return "max_completion_tokens" in message and (
        "max_tokens" in message or "unsupported" in message
    )


def _generate_openai_text_with_usage(
    *,
    prompt: str,
    model: str,
    max_output_tokens: int | None = None,
    disable_thinking: bool = False,
    images: Sequence[PlanImageAttachment] | None = None,
) -> tuple[str, dict[str, int]]:
    # disable_thinking hat für Standard-Chat-Completions-Modelle (GPT-5.x über
    # diese API) aktuell keine Wirkung — es gibt hier keinen äquivalenten
    # Parameter wie thinking/thinking_config bei Anthropic/Gemini.
    del disable_thinking
    api_key = get_api_key("OPENAI_API_KEY")
    if not api_key:
        raise PlanLlmNotConfiguredError(
            "OPENAI_API_KEY ist nicht gesetzt. "
            "Bitte unter 🔑 API-Schlüssel oder in .env eintragen."
        )
    _require_sdk_module("openai")
    from openai import OpenAI

    effective_max_tokens = max_output_tokens or DEFAULT_MAX_OUTPUT_TOKENS
    messages = [
        {
            "role": "user",
            "content": _openai_user_content(prompt, list(images or [])),
        }
    ]
    with cancellable_httpx_client() as http:
        client_kwargs: dict = {
            "api_key": api_key,
            "timeout": _LLM_REQUEST_TIMEOUT_SEC,
        }
        if http is not None:
            client_kwargs["http_client"] = http
        client = OpenAI(**client_kwargs)
        return _openai_complete_stream(
            client,
            messages=messages,
            model=model,
            effective_max_tokens=effective_max_tokens,
        )


def _openai_complete_stream(
    client,
    *,
    messages: list,
    model: str,
    effective_max_tokens: int,
) -> tuple[str, dict[str, int]]:
    from openai import BadRequestError

    def _stream(*, use_temperature: bool, use_max_completion_tokens: bool) -> tuple[str, str | None, dict[str, int]]:
        kwargs: dict = {
            "model": model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if use_temperature:
            kwargs["temperature"] = 0.2
        if use_max_completion_tokens:
            kwargs["max_completion_tokens"] = effective_max_tokens
        else:
            kwargs["max_tokens"] = effective_max_tokens
        stream = client.chat.completions.create(**kwargs)
        text_parts: list[str] = []
        finish_reason: str | None = None
        token_usage: dict[str, int] = {}
        for event in _iter_checking_cancel(stream):
            usage = getattr(event, "usage", None)
            if usage is not None:
                token_usage = _token_usage_dict(
                    input_tokens=getattr(usage, "prompt_tokens", None),
                    output_tokens=getattr(usage, "completion_tokens", None),
                    total_tokens=getattr(usage, "total_tokens", None),
                )
            if not event.choices:
                continue
            choice = event.choices[0]
            if choice.finish_reason:
                finish_reason = choice.finish_reason
            delta = getattr(choice.delta, "content", None)
            if delta:
                text_parts.append(delta)
        return "".join(text_parts).strip(), finish_reason, token_usage

    try:
        text, finish_reason, token_usage = _stream(
            use_temperature=True, use_max_completion_tokens=False
        )
    except BadRequestError as exc:
        if _is_max_tokens_param_rejected_error(exc):
            try:
                text, finish_reason, token_usage = _stream(
                    use_temperature=True, use_max_completion_tokens=True
                )
            except BadRequestError as retry_exc:
                if not _is_temperature_rejected_error(retry_exc):
                    raise
                text, finish_reason, token_usage = _stream(
                    use_temperature=False, use_max_completion_tokens=True
                )
        elif _is_temperature_rejected_error(exc):
            try:
                text, finish_reason, token_usage = _stream(
                    use_temperature=False, use_max_completion_tokens=False
                )
            except BadRequestError as retry_exc:
                if not _is_max_tokens_param_rejected_error(retry_exc):
                    raise
                text, finish_reason, token_usage = _stream(
                    use_temperature=False, use_max_completion_tokens=True
                )
        else:
            raise

    if finish_reason == "length":
        raise PlanLlmTruncatedResponseError(
            format_truncated_plan_response_error(
                stop_reason="length",
                max_output_tokens=effective_max_tokens,
                output_tokens=token_usage.get("output_tokens"),
            )
        )
    if not text:
        raise PlanLlmTruncatedResponseError(
            "Das Modell hat keinen verwertbaren Text zurückgegeben. Bitte erneut versuchen."
        )
    return text, token_usage


def _generate_openai_compatible_text_with_usage(
    *,
    prompt: str,
    model: str,
    api_key_env: str,
    base_url: str,
    max_output_tokens: int | None = None,
    disable_thinking: bool = False,
    default_headers: dict[str, str] | None = None,
) -> tuple[str, dict[str, int]]:
    """OpenAI-kompatible Chat-Completions (xAI, OpenRouter) mit Streaming."""
    del disable_thinking
    api_key = get_api_key(api_key_env)
    if not api_key:
        raise PlanLlmNotConfiguredError(
            f"{api_key_env} ist nicht gesetzt. "
            "Bitte unter 🔑 API-Schlüssel oder in .env eintragen."
        )
    _require_sdk_module("openai")
    from openai import BadRequestError, OpenAI

    effective_max_tokens = max_output_tokens or DEFAULT_MAX_OUTPUT_TOKENS
    client_kwargs: dict = {
        "api_key": api_key,
        "base_url": base_url,
        "timeout": _LLM_REQUEST_TIMEOUT_SEC,
    }
    if default_headers:
        client_kwargs["default_headers"] = default_headers
    messages = [{"role": "user", "content": prompt}]
    with cancellable_httpx_client() as http:
        if http is not None:
            client_kwargs["http_client"] = http
        client = OpenAI(**client_kwargs)

        def _stream(*, use_temperature: bool) -> tuple[str, str | None, dict[str, int]]:
            kwargs: dict = {
                "model": model,
                "messages": messages,
                "max_tokens": effective_max_tokens,
                "stream": True,
                "stream_options": {"include_usage": True},
            }
            if use_temperature:
                kwargs["temperature"] = 0.2
            stream = client.chat.completions.create(**kwargs)
            text_parts: list[str] = []
            finish_reason: str | None = None
            token_usage: dict[str, int] = {}
            for event in _iter_checking_cancel(stream):
                usage = getattr(event, "usage", None)
                if usage is not None:
                    token_usage = _token_usage_dict(
                        input_tokens=getattr(usage, "prompt_tokens", None),
                        output_tokens=getattr(usage, "completion_tokens", None),
                        total_tokens=getattr(usage, "total_tokens", None),
                    )
                if not event.choices:
                    continue
                choice = event.choices[0]
                if choice.finish_reason:
                    finish_reason = choice.finish_reason
                delta = getattr(choice.delta, "content", None)
                if delta:
                    text_parts.append(delta)
            return "".join(text_parts).strip(), finish_reason, token_usage

        try:
            text, finish_reason, token_usage = _stream(use_temperature=True)
        except BadRequestError as exc:
            if not _is_temperature_rejected_error(exc):
                raise
            text, finish_reason, token_usage = _stream(use_temperature=False)

        if finish_reason == "length":
            raise PlanLlmTruncatedResponseError(
                format_truncated_plan_response_error(
                    stop_reason="length",
                    max_output_tokens=effective_max_tokens,
                    output_tokens=token_usage.get("output_tokens"),
                )
            )
        if not text:
            raise PlanLlmTruncatedResponseError(
                "Das Modell hat keinen verwertbaren Text zurückgegeben. Bitte erneut versuchen."
            )
        return text, token_usage


def _generate_xai_text_with_usage(
    *,
    prompt: str,
    model: str,
    max_output_tokens: int | None = None,
    disable_thinking: bool = False,
) -> tuple[str, dict[str, int]]:
    """xAI/Grok über die OpenAI-kompatible Chat-Completions-API."""
    return _generate_openai_compatible_text_with_usage(
        prompt=prompt,
        model=model,
        api_key_env="XAI_API_KEY",
        base_url=XAI_API_BASE_URL,
        max_output_tokens=max_output_tokens,
        disable_thinking=disable_thinking,
    )


def _generate_openrouter_text_with_usage(
    *,
    prompt: str,
    model: str,
    max_output_tokens: int | None = None,
    disable_thinking: bool = False,
) -> tuple[str, dict[str, int]]:
    """OpenRouter über die OpenAI-kompatible Chat-Completions-API.

    Modell-IDs folgen der OpenRouter-Konvention (z. B. ``x-ai/grok-4.5``).
    """
    return _generate_openai_compatible_text_with_usage(
        prompt=prompt,
        model=model,
        api_key_env="OPENROUTER_API_KEY",
        base_url=OPENROUTER_API_BASE_URL,
        max_output_tokens=max_output_tokens,
        disable_thinking=disable_thinking,
        default_headers={
            "HTTP-Referer": "https://github.com/johnkuhnmedien-cmd/otio",
            "X-Title": "OTIO Voiceover Generation",
        },
    )


def _anthropic_final_message(client, create_kwargs: dict, *, use_temperature: bool):
    """Holt die finale Anthropic-Message — per Stream wenn nötig, sonst create.

    Streaming ändert die Antwortqualität nicht; es aggregiert denselben finalen
    Message-Body. Ab ~21k max_tokens verlangt das Anthropic-SDK Streaming
    (10-Minuten-Regel), sonst ValueError.
    """
    kwargs = dict(create_kwargs)
    if use_temperature:
        kwargs["temperature"] = 0.2
    max_tokens = int(kwargs.get("max_tokens") or DEFAULT_MAX_OUTPUT_TOKENS)
    if max_tokens > _ANTHROPIC_NONSTREAMING_MAX_TOKENS:
        with client.messages.stream(**kwargs) as stream:
            return stream.get_final_message()
    return client.messages.create(**kwargs)


def _build_anthropic_client(api_key: str, *, trust_env: bool, http_client=None):
    """Anthropic-Client mit explizitem httpx-Transport.

    ``trust_env=False`` ignoriert kaputte System-/Env-Proxys (häufige Ursache
    für bloßes „Connection error.“ nur bei Claude, während OpenAI/Gemini noch
    gehen).
    """
    import httpx
    from anthropic import Anthropic

    try:
        from anthropic import DefaultHttpxClient as HttpClient
    except ImportError:  # pragma: no cover - ältere SDKs
        HttpClient = httpx.Client

    if http_client is None:
        http_client = HttpClient(
            timeout=_LLM_REQUEST_TIMEOUT_SEC,
            trust_env=trust_env,
        )
    return Anthropic(
        api_key=api_key,
        http_client=http_client,
        timeout=_LLM_REQUEST_TIMEOUT_SEC,
    )


def _anthropic_error_text(exc: BaseException) -> str:
    parts = [str(exc)]
    cause = exc.__cause__ or exc.__context__
    if cause is not None:
        parts.append(f"{type(cause).__name__}: {cause}")
    return " ".join(parts).lower()


def _is_anthropic_billed_disconnect(exc: BaseException) -> bool:
    """Server hat Request angenommen und dann die Verbindung gekappt.

    Typisch nach großen Prompts. Input-Tokens können bereits berechnet sein —
    automatische Retries würden die Kosten multiplizieren.
    """
    text = _anthropic_error_text(exc)
    return (
        "disconnected without sending" in text
        or "server disconnected" in text
        or "remoteprotocolerror" in text
    )


def _format_anthropic_connection_error(exc: BaseException) -> str:
    cause = exc.__cause__ or exc.__context__
    detail = ""
    if cause is not None and str(cause).strip():
        detail = f" Ursache: {type(cause).__name__}: {cause}."
    elif str(exc).strip() and str(exc).strip().lower() != "connection error.":
        detail = f" Details: {exc}."
    size_hint = ""
    if _is_anthropic_billed_disconnect(exc):
        size_hint = (
            " Der Request wurde sehr wahrscheinlich schon auf Anthropic-Seite "
            "angenommen — Input-Tokens können trotzdem berechnet werden, auch "
            "ohne Antwort. Deshalb kein automatischer Retry (vermeidet "
            "Mehrfach-Kosten). Intro-Prompt ggf. verkleinern / erneut mit "
            "kompakterem Inventar versuchen."
        )
    return (
        "Anthropic-Verbindung fehlgeschlagen (api.anthropic.com)."
        f"{detail}{size_hint} "
        "Bitte prüfen: ANTHROPIC_API_KEY unter 🔑 API-Schlüssel, VPN/Proxy/"
        "Firewall, und ob https://api.anthropic.com vom gleichen Python "
        "erreichbar ist."
    )


def _call_anthropic_messages(client, create_kwargs: dict):
    """Ein Anthropic-Call inkl. temperature-Retry für Reasoning-Modelle."""
    from anthropic import BadRequestError

    try:
        return _anthropic_final_message(client, create_kwargs, use_temperature=True)
    except BadRequestError as exc:
        if not _is_temperature_rejected_error(exc):
            raise
        # Neuere/Reasoning-Modelle lehnen eine explizite temperature ab und
        # verlangen den API-Standardwert — ohne temperature erneut versuchen,
        # statt die Erzeugung komplett fehlschlagen zu lassen (siehe z. B.
        # "temperature is deprecated for this model.").
        return _anthropic_final_message(client, create_kwargs, use_temperature=False)


def _generate_anthropic_text_with_usage(
    *,
    prompt: str,
    model: str,
    max_output_tokens: int | None = None,
    disable_thinking: bool = False,
) -> tuple[str, dict[str, int]]:
    api_key = get_api_key("ANTHROPIC_API_KEY")
    if not api_key:
        raise PlanLlmNotConfiguredError(
            "ANTHROPIC_API_KEY ist nicht gesetzt. "
            "Bitte unter 🔑 API-Schlüssel oder in .env eintragen."
        )
    _require_sdk_module("anthropic")
    from anthropic import APIConnectionError

    effective_max_tokens = max_output_tokens or DEFAULT_MAX_OUTPUT_TOKENS
    create_kwargs: dict = {
        "model": model,
        "max_tokens": effective_max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if disable_thinking:
        # thinking={"type": "disabled"} schaltet das interne "Thinking" ab —
        # das gesamte max_tokens-Budget steht dann der sichtbaren Antwort zur
        # Verfügung (siehe generate_plan_text_with_metadata()-Docstring).
        create_kwargs["thinking"] = {"type": "disabled"}

    # Connection-Handling:
    # 1) Normal mit Env-Proxy
    # 2) Nur bei frühem Proxy-/Connect-Fail: einmal ohne System-Proxy
    # 3) Bei "Server disconnected without sending a response" KEIN Retry —
    #    Request war oft schon angenommen; Retry = erneute Input-Rechnung.
    last_connection_error: BaseException | None = None
    response = None
    import httpx

    try:
        from anthropic import DefaultHttpxClient as HttpClient
    except ImportError:  # pragma: no cover - ältere SDKs
        HttpClient = httpx.Client

    for trust_env in (True, False):
        with cancellable_httpx_client(
            factory=HttpClient, trust_env=trust_env
        ) as http:
            client = _build_anthropic_client(
                api_key, trust_env=trust_env, http_client=http
            )
            try:
                response = _call_anthropic_messages(client, create_kwargs)
                break
            except PlanLlmCancelledError:
                raise
            except APIConnectionError as exc:
                if llm_cancel_requested():
                    raise PlanLlmCancelledError("LLM-Aufruf abgebrochen.") from exc
                last_connection_error = exc
                if _is_anthropic_billed_disconnect(exc):
                    break
                # Früher Connect-/Proxy-Fehler → ein zweiter Versuch ohne Env-Proxy.
                if trust_env is False:
                    break
                continue
    if response is None:
        assert last_connection_error is not None
        raise PlanLlmConnectionError(
            _format_anthropic_connection_error(last_connection_error)
        ) from last_connection_error

    usage = getattr(response, "usage", None)
    token_usage = _token_usage_dict(
        input_tokens=getattr(usage, "input_tokens", None),
        output_tokens=getattr(usage, "output_tokens", None),
    )
    # Kernbug (behoben): Bei sehr umfangreichen Prompts (z. B. Dramaturgie über
    # viele Ordner) wurde die Antwort exakt bei max_tokens abgeschnitten
    # (stop_reason="max_tokens", output_tokens == max_tokens) — z. B. weil das
    # Modell seinen gesamten Output-Token-Budget für internes "Thinking" ohne
    # finalen Text verbraucht hat. Der alte Code gab in diesem Fall
    # stillschweigend "{}" zurück, was downstream als "erfolgreich, aber leerer
    # Plan" durchging. Jetzt wird das explizit als Fehler gemeldet.
    if getattr(response, "stop_reason", None) == "max_tokens":
        raise PlanLlmTruncatedResponseError(
            format_truncated_plan_response_error(
                stop_reason="max_tokens",
                max_output_tokens=effective_max_tokens,
                output_tokens=token_usage.get("output_tokens", effective_max_tokens),
            )
        )
    parts = [block.text for block in response.content if getattr(block, "type", "") == "text"]
    text = "\n".join(parts).strip()
    if not text:
        raise PlanLlmTruncatedResponseError(
            "Das Modell hat keinen verwertbaren Text zurückgegeben (z. B. nur interne "
            "'Thinking'-Tokens ohne finale Antwort). Bitte erneut versuchen."
        )
    return text, token_usage
