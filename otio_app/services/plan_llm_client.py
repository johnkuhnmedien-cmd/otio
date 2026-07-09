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


def generate_plan_text(*, prompt: str, model: Optional[str] = None) -> str:
    """Sendet den Schnittplan-Prompt an das gewählte Text-LLM."""
    return generate_plan_text_with_metadata(prompt=prompt, model=model).raw_text


def generate_plan_text_with_metadata(*, prompt: str, model: Optional[str] = None) -> PlanLlmResponse:
    """Wie generate_plan_text, inkl. Latenz und Token-Nutzung für Diagnose-Runs."""
    resolved = resolve_plan_model(model)
    provider = plan_model_provider(resolved)
    api_model = _provider_api_model(resolved)
    started = time.perf_counter()

    if provider == PROVIDER_GEMINI:
        raw_text, token_usage = _generate_gemini_text_with_usage(prompt=prompt, model=api_model)
    elif provider == PROVIDER_OPENAI:
        raw_text, token_usage = _generate_openai_text_with_usage(prompt=prompt, model=api_model)
    elif provider == PROVIDER_ANTHROPIC:
        raw_text, token_usage = _generate_anthropic_text_with_usage(prompt=prompt, model=api_model)
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


def _generate_gemini_text_with_usage(*, prompt: str, model: str) -> tuple[str, dict[str, int]]:
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

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=model,
        contents=[types.Content(role="user", parts=[types.Part.from_text(text=prompt)])],
    )
    usage_meta = getattr(response, "usage_metadata", None)
    token_usage = _token_usage_dict(
        input_tokens=getattr(usage_meta, "prompt_token_count", None),
        output_tokens=getattr(usage_meta, "candidates_token_count", None),
        total_tokens=getattr(usage_meta, "total_token_count", None),
    )
    return response.text or "{}", token_usage


def _generate_openai_text_with_usage(*, prompt: str, model: str) -> tuple[str, dict[str, int]]:
    api_key = get_api_key("OPENAI_API_KEY")
    if not api_key:
        raise PlanLlmNotConfiguredError(
            "OPENAI_API_KEY ist nicht gesetzt. "
            "Bitte unter 🔑 API-Schlüssel oder in .env eintragen."
        )
    _require_sdk_module("openai")
    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
    )
    message = response.choices[0].message.content if response.choices else None
    usage = getattr(response, "usage", None)
    token_usage = _token_usage_dict(
        input_tokens=getattr(usage, "prompt_tokens", None),
        output_tokens=getattr(usage, "completion_tokens", None),
        total_tokens=getattr(usage, "total_tokens", None),
    )
    return (message or "").strip() or "{}", token_usage


def _generate_anthropic_text_with_usage(*, prompt: str, model: str) -> tuple[str, dict[str, int]]:
    api_key = get_api_key("ANTHROPIC_API_KEY")
    if not api_key:
        raise PlanLlmNotConfiguredError(
            "ANTHROPIC_API_KEY ist nicht gesetzt. "
            "Bitte unter 🔑 API-Schlüssel oder in .env eintragen."
        )
    _require_sdk_module("anthropic")
    from anthropic import Anthropic

    client = Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model,
        max_tokens=8192,
        temperature=0.2,
        messages=[{"role": "user", "content": prompt}],
    )
    parts = [block.text for block in response.content if getattr(block, "type", "") == "text"]
    usage = getattr(response, "usage", None)
    token_usage = _token_usage_dict(
        input_tokens=getattr(usage, "input_tokens", None),
        output_tokens=getattr(usage, "output_tokens", None),
    )
    return "\n".join(parts).strip() or "{}", token_usage
