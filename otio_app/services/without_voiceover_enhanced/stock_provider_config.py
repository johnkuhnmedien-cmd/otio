"""Projektbezogene Stockanbieter-Konfiguration (Enhanced MVP R1).

Nur die fünf unterstützten Anbieter. Kein Adobe Stock. Keine API-Keys hier.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from otio_app.models import Project
from otio_app.services.without_voiceover_enhanced.io_utils import load_model, write_json
from otio_app.services.without_voiceover_enhanced.paths import stock_providers_config_path

SUPPORTED_STOCK_PROVIDERS: tuple[str, ...] = (
    "pexels",
    "pixabay",
    "wikimedia",
    "openverse",
    "archive_org",
)

PROVIDER_UI_LABELS: dict[str, str] = {
    "pexels": "Pexels",
    "pixabay": "Pixabay",
    "wikimedia": "Wikimedia",
    "openverse": "Openverse",
    "archive_org": "Archive.org",
}

UNSUPPORTED_PROVIDER_KEYS: frozenset[str] = frozenset(
    {
        "adobe_stock",
        "adobe",
        "shutterstock",
        "getty",
        "archive.org",  # legacy alias — not executed
    }
)

PROVIDER_STATUS_COMPLETED = "completed"
PROVIDER_STATUS_DISABLED = "disabled"
PROVIDER_STATUS_UNAVAILABLE = "unavailable"
PROVIDER_STATUS_FAILED = "failed"


class StockProviderToggle(BaseModel):
    enabled: bool = True


class StockProvidersConfig(BaseModel):
    schema_version: str = "1.0"
    providers: dict[str, StockProviderToggle] = Field(default_factory=dict)


def default_stock_providers_config() -> StockProvidersConfig:
    return StockProvidersConfig(
        schema_version="1.0",
        providers={
            name: StockProviderToggle(enabled=True)
            for name in SUPPORTED_STOCK_PROVIDERS
        },
    )


def _normalize_payload(raw: dict[str, Any]) -> StockProvidersConfig:
    defaults = default_stock_providers_config()
    providers_raw = raw.get("providers") or {}
    if not isinstance(providers_raw, dict):
        return defaults
    normalized: dict[str, StockProviderToggle] = {}
    for name in SUPPORTED_STOCK_PROVIDERS:
        entry = providers_raw.get(name)
        if isinstance(entry, dict) and "enabled" in entry:
            normalized[name] = StockProviderToggle(enabled=bool(entry["enabled"]))
        elif isinstance(entry, bool):
            normalized[name] = StockProviderToggle(enabled=entry)
        else:
            normalized[name] = StockProviderToggle(enabled=True)
    # Ignore unknown / unsupported keys (including adobe_stock) — never execute them.
    return StockProvidersConfig(
        schema_version=str(raw.get("schema_version") or "1.0"),
        providers=normalized,
    )


def load_stock_providers_config(project: Project) -> StockProvidersConfig:
    """Fehlende Datei → dokumentierte Defaults (alle fünf aktiv)."""
    path = stock_providers_config_path(project)
    if not path.is_file():
        return default_stock_providers_config()
    try:
        loaded = load_model(path, StockProvidersConfig)
        if loaded is None:
            return default_stock_providers_config()
        return _normalize_payload(loaded.model_dump(mode="json"))
    except Exception:  # noqa: BLE001 — corrupt config must not break projects
        return default_stock_providers_config()


def save_stock_providers_config(
    project: Project,
    enabled_by_provider: dict[str, bool],
) -> StockProvidersConfig:
    """Speichert die Auswahl; unbekannte Keys werden verworfen."""
    config = default_stock_providers_config()
    for name in SUPPORTED_STOCK_PROVIDERS:
        if name in enabled_by_provider:
            config.providers[name] = StockProviderToggle(
                enabled=bool(enabled_by_provider[name])
            )
    write_json(stock_providers_config_path(project), config)
    return config


def enabled_provider_names(project: Project) -> list[str]:
    config = load_stock_providers_config(project)
    return [
        name
        for name in SUPPORTED_STOCK_PROVIDERS
        if config.providers.get(name, StockProviderToggle(enabled=True)).enabled
    ]


def is_provider_enabled(project: Project, provider_name: str) -> bool:
    if provider_name in UNSUPPORTED_PROVIDER_KEYS:
        return False
    if provider_name not in SUPPORTED_STOCK_PROVIDERS:
        return False
    config = load_stock_providers_config(project)
    return bool(config.providers.get(provider_name, StockProviderToggle()).enabled)
