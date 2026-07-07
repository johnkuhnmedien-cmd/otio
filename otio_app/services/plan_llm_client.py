"""Text-LLM-Aufrufe für Schnittplan-Vorschläge (Gemini, OpenAI, Anthropic)."""

from __future__ import annotations

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
    if model and model.strip() in EDIT_PLAN_MODEL_CHOICES:
        return model.strip()
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
    resolved = resolve_plan_model(model)
    provider = plan_model_provider(resolved)
    api_model = _provider_api_model(resolved)

    if provider == PROVIDER_GEMINI:
        return _generate_gemini_text(prompt=prompt, model=api_model)
    if provider == PROVIDER_OPENAI:
        return _generate_openai_text(prompt=prompt, model=api_model)
    if provider == PROVIDER_ANTHROPIC:
        return _generate_anthropic_text(prompt=prompt, model=api_model)
    raise PlanLlmNotConfiguredError(f"Unbekannter Planungs-Provider für Modell `{resolved}`.")


def _generate_gemini_text(*, prompt: str, model: str) -> str:
    api_key = get_api_key("GEMINI_API_KEY")
    if not api_key:
        raise PlanLlmNotConfiguredError(
            "GEMINI_API_KEY ist nicht gesetzt. "
            "Bitte unter 🔑 API-Schlüssel oder in .env eintragen."
        )
    from google import genai
    from google.genai import types

    if model not in GEMINI_MODEL_CHOICES:
        model = get_gemini_model_from_env()

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=model,
        contents=[types.Content(role="user", parts=[types.Part.from_text(text=prompt)])],
    )
    return response.text or "{}"


def _generate_openai_text(*, prompt: str, model: str) -> str:
    api_key = get_api_key("OPENAI_API_KEY")
    if not api_key:
        raise PlanLlmNotConfiguredError(
            "OPENAI_API_KEY ist nicht gesetzt. "
            "Bitte unter 🔑 API-Schlüssel oder in .env eintragen."
        )
    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
    )
    message = response.choices[0].message.content if response.choices else None
    return (message or "").strip() or "{}"


def _generate_anthropic_text(*, prompt: str, model: str) -> str:
    api_key = get_api_key("ANTHROPIC_API_KEY")
    if not api_key:
        raise PlanLlmNotConfiguredError(
            "ANTHROPIC_API_KEY ist nicht gesetzt. "
            "Bitte unter 🔑 API-Schlüssel oder in .env eintragen."
        )
    from anthropic import Anthropic

    client = Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model,
        max_tokens=8192,
        temperature=0.2,
        messages=[{"role": "user", "content": prompt}],
    )
    parts = [block.text for block in response.content if getattr(block, "type", "") == "text"]
    return "\n".join(parts).strip() or "{}"
