"""Text-LLM-Aufrufe für Schnittplan-Vorschläge (Gemini, OpenAI, Anthropic)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from otio_app.config import get_gemini_model_from_env
from otio_app.defaults import EDIT_PLAN_MODEL_CHOICES, EDIT_PLAN_MODEL_LABELS, GEMINI_MODEL_CHOICES
from otio_app.services.api_keys import get_api_key, is_api_key_set

PROVIDER_GEMINI = "gemini"
PROVIDER_OPENAI = "openai"
PROVIDER_ANTHROPIC = "anthropic"

_PROVIDER_ENV_KEYS = {
    PROVIDER_GEMINI: "GEMINI_API_KEY",
    PROVIDER_OPENAI: "OPENAI_API_KEY",
    PROVIDER_ANTHROPIC: "ANTHROPIC_API_KEY",
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


# Höher als der ursprüngliche Default (8192) — bei umfangreichen Prompts (z. B.
# Dramaturgie-Planung über viele Ordner) reichte das nicht aus und die Antwort
# wurde exakt bei max_tokens abgeschnitten (stop_reason="max_tokens"), was durch
# den alten "leeres Ergebnis statt Fehler"-Fallback unbemerkt blieb.
DEFAULT_MAX_OUTPUT_TOKENS = 16384


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
        if value.startswith("openai:") or value.startswith("anthropic:"):
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
) -> str:
    """Sendet den Schnittplan-Prompt an das gewählte Text-LLM."""
    return generate_plan_text_with_metadata(
        prompt=prompt,
        model=model,
        max_output_tokens=max_output_tokens,
        disable_thinking=disable_thinking,
    ).raw_text


def generate_plan_text_with_metadata(
    *,
    prompt: str,
    model: Optional[str] = None,
    max_output_tokens: int | None = None,
    disable_thinking: bool = False,
) -> PlanLlmResponse:
    """Wie generate_plan_text, inkl. Latenz und Token-Nutzung für Diagnose-Runs.

    max_output_tokens überschreibt DEFAULT_MAX_OUTPUT_TOKENS für diesen Call
    (z. B. für sehr umfangreiche Prompts wie eine Dramaturgie-Planung über
    viele Ordner). disable_thinking schaltet, sofern vom Provider unterstützt
    (Anthropic, Gemini), das interne "Thinking" des Modells für diesen Call
    aus — damit steht das gesamte max_output_tokens-Budget der sichtbaren
    Antwort zur Verfügung, statt (teilweise) für internes Reasoning verbraucht
    zu werden. Für OpenAI-Modelle über die Chat-Completions-API hat
    disable_thinking aktuell keine Wirkung (kein äquivalenter Parameter)."""
    resolved = resolve_plan_model(model)
    provider = plan_model_provider(resolved)
    api_model = _provider_api_model(resolved)
    started = time.perf_counter()

    if provider == PROVIDER_GEMINI:
        raw_text, token_usage = _generate_gemini_text_with_usage(
            prompt=prompt,
            model=api_model,
            max_output_tokens=max_output_tokens,
            disable_thinking=disable_thinking,
        )
    elif provider == PROVIDER_OPENAI:
        raw_text, token_usage = _generate_openai_text_with_usage(
            prompt=prompt,
            model=api_model,
            max_output_tokens=max_output_tokens,
            disable_thinking=disable_thinking,
        )
    elif provider == PROVIDER_ANTHROPIC:
        raw_text, token_usage = _generate_anthropic_text_with_usage(
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


def _generate_gemini_text_with_usage(
    *,
    prompt: str,
    model: str,
    max_output_tokens: int | None = None,
    disable_thinking: bool = False,
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

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=model,
        contents=[types.Content(role="user", parts=[types.Part.from_text(text=prompt)])],
        config=types.GenerateContentConfig(**config_kwargs),
    )
    usage_meta = getattr(response, "usage_metadata", None)
    token_usage = _token_usage_dict(
        input_tokens=getattr(usage_meta, "prompt_token_count", None),
        output_tokens=getattr(usage_meta, "candidates_token_count", None),
        total_tokens=getattr(usage_meta, "total_token_count", None),
    )
    candidates = getattr(response, "candidates", None) or []
    finish_reason = str(getattr(candidates[0], "finish_reason", "")) if candidates else ""
    if "MAX_TOKENS" in finish_reason:
        raise PlanLlmTruncatedResponseError(
            f"Die Gemini-Antwort wurde bei max_output_tokens={effective_max_tokens} "
            "abgeschnitten (finish_reason=MAX_TOKENS). Der Prompt ist wahrscheinlich zu "
            "umfangreich (z. B. sehr viele Ordner) für eine vollständige Antwort in diesem "
            "Limit. Bitte weniger Ordner gleichzeitig planen oder den Prompt kürzen."
        )
    text = (response.text or "").strip()
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
    from openai import BadRequestError, OpenAI

    effective_max_tokens = max_output_tokens or DEFAULT_MAX_OUTPUT_TOKENS
    client = OpenAI(api_key=api_key)
    messages = [{"role": "user", "content": prompt}]

    def _create(*, use_temperature: bool, use_max_completion_tokens: bool):
        kwargs: dict = {
            "model": model,
            "messages": messages,
        }
        if use_temperature:
            kwargs["temperature"] = 0.2
        if use_max_completion_tokens:
            kwargs["max_completion_tokens"] = effective_max_tokens
        else:
            kwargs["max_tokens"] = effective_max_tokens
        return client.chat.completions.create(**kwargs)

    use_max_completion_tokens = False
    try:
        response = _create(use_temperature=True, use_max_completion_tokens=False)
    except BadRequestError as exc:
        if _is_max_tokens_param_rejected_error(exc):
            use_max_completion_tokens = True
            try:
                response = _create(use_temperature=True, use_max_completion_tokens=True)
            except BadRequestError as retry_exc:
                if not _is_temperature_rejected_error(retry_exc):
                    raise
                response = _create(use_temperature=False, use_max_completion_tokens=True)
        elif _is_temperature_rejected_error(exc):
            try:
                response = _create(use_temperature=False, use_max_completion_tokens=False)
            except BadRequestError as retry_exc:
                if not _is_max_tokens_param_rejected_error(retry_exc):
                    raise
                use_max_completion_tokens = True
                response = _create(use_temperature=False, use_max_completion_tokens=True)
        else:
            raise
    del use_max_completion_tokens  # nur für Lesbarkeit der Retry-Zweige

    usage = getattr(response, "usage", None)
    token_usage = _token_usage_dict(
        input_tokens=getattr(usage, "prompt_tokens", None),
        output_tokens=getattr(usage, "completion_tokens", None),
        total_tokens=getattr(usage, "total_tokens", None),
    )
    choice = response.choices[0] if response.choices else None
    finish_reason = getattr(choice, "finish_reason", None) if choice is not None else None
    if finish_reason == "length":
        raise PlanLlmTruncatedResponseError(
            f"Die Antwort wurde bei max_tokens={effective_max_tokens} abgeschnitten "
            "(finish_reason=length). Der Prompt ist wahrscheinlich zu umfangreich (z. B. "
            "sehr viele Ordner) für eine vollständige Antwort in diesem Limit. Bitte "
            "weniger Ordner gleichzeitig planen oder den Prompt kürzen."
        )
    message = choice.message.content if choice is not None else None
    text = (message or "").strip()
    if not text:
        raise PlanLlmTruncatedResponseError(
            "Das Modell hat keinen verwertbaren Text zurückgegeben. Bitte erneut versuchen."
        )
    return text, token_usage


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
    from anthropic import Anthropic, BadRequestError

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

    client = Anthropic(api_key=api_key)
    try:
        response = client.messages.create(temperature=0.2, **create_kwargs)
    except BadRequestError as exc:
        if not _is_temperature_rejected_error(exc):
            raise
        # Neuere/Reasoning-Modelle lehnen eine explizite temperature ab und
        # verlangen den API-Standardwert — ohne temperature erneut versuchen,
        # statt die Erzeugung komplett fehlschlagen zu lassen (siehe z. B.
        # "temperature is deprecated for this model.").
        response = client.messages.create(**create_kwargs)
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
            f"Die Antwort wurde nach {token_usage.get('output_tokens', effective_max_tokens)} "
            f"von max_tokens={effective_max_tokens} Output-Tokens abgeschnitten "
            "(stop_reason=max_tokens). Der Prompt ist wahrscheinlich zu umfangreich (z. B. "
            "sehr viele Ordner) für eine vollständige Antwort in diesem Limit. Bitte weniger "
            "Ordner gleichzeitig planen oder den Prompt kürzen."
        )
    parts = [block.text for block in response.content if getattr(block, "type", "") == "text"]
    text = "\n".join(parts).strip()
    if not text:
        raise PlanLlmTruncatedResponseError(
            "Das Modell hat keinen verwertbaren Text zurückgegeben (z. B. nur interne "
            "'Thinking'-Tokens ohne finale Antwort). Bitte erneut versuchen."
        )
    return text, token_usage
