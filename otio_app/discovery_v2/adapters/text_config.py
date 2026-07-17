"""Fake-only Discovery V2 text configuration for Phase 9 editorial."""

from __future__ import annotations

from otio_app.discovery_v2.domain.editorial import (
    GATEWAY_VERSION,
    PROMPT_VERSION_COVERAGE,
    PROMPT_VERSION_NARRATIVE,
    PROMPT_VERSION_PAUSE_DIRECTION,
    PROMPT_VERSION_SCRIPT,
    PROMPT_VERSION_STRUCTURE,
    RESPONSE_SCHEMA_COVERAGE,
    RESPONSE_SCHEMA_NARRATIVE,
    RESPONSE_SCHEMA_PAUSE_DIRECTION,
    RESPONSE_SCHEMA_SCRIPT,
    RESPONSE_SCHEMA_STRUCTURE,
    TEXT_MODEL_IDENTIFIER,
    TEXT_PROVIDER,
    TextConfig,
)

MAX_RETRIES = 2
TEXT_TIMEOUT_SECONDS = 30

PROMPTS = {
    "narrative": PROMPT_VERSION_NARRATIVE,
    "script": PROMPT_VERSION_SCRIPT,
    "structure": PROMPT_VERSION_STRUCTURE,
    "coverage": PROMPT_VERSION_COVERAGE,
    "pause_direction": PROMPT_VERSION_PAUSE_DIRECTION,
}

RESPONSE_SCHEMAS = {
    "narrative": RESPONSE_SCHEMA_NARRATIVE,
    "script": RESPONSE_SCHEMA_SCRIPT,
    "structure": RESPONSE_SCHEMA_STRUCTURE,
    "coverage": RESPONSE_SCHEMA_COVERAGE,
    "pause_direction": RESPONSE_SCHEMA_PAUSE_DIRECTION,
}


def load_text_config() -> TextConfig:
    """Return the only supported Phase 9 text configuration."""

    return TextConfig(
        provider=TEXT_PROVIDER,
        enabled=True,
        model_identifier=TEXT_MODEL_IDENTIFIER,
        gateway_version=GATEWAY_VERSION,
        max_retries=MAX_RETRIES,
        timeout_seconds=TEXT_TIMEOUT_SECONDS,
        prompts=dict(PROMPTS),
        response_schemas=dict(RESPONSE_SCHEMAS),
    )


def reset_text_config_for_tests() -> None:
    """Phase 9 config has no mutable runtime state."""

    return None


__all__ = [
    "MAX_RETRIES",
    "PROMPTS",
    "RESPONSE_SCHEMAS",
    "TEXT_MODEL_IDENTIFIER",
    "TEXT_PROVIDER",
    "TEXT_TIMEOUT_SECONDS",
    "load_text_config",
    "reset_text_config_for_tests",
]
