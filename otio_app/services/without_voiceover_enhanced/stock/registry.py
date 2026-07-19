"""Registrierung und fehlertolerante Mehranbieter-Suche (R1)."""

from __future__ import annotations

import logging
from typing import Iterable

from otio_app.models import Project
from otio_app.services.without_voiceover_enhanced.models import StockCandidate
from otio_app.services.without_voiceover_enhanced.stock.archive_org import (
    ArchiveOrgStockProvider,
)
from otio_app.services.without_voiceover_enhanced.stock.base import StockProvider
from otio_app.services.without_voiceover_enhanced.stock.openverse import (
    OpenverseStockProvider,
)
from otio_app.services.without_voiceover_enhanced.stock.pexels import PexelsStockProvider
from otio_app.services.without_voiceover_enhanced.stock.pixabay import PixabayStockProvider
from otio_app.services.without_voiceover_enhanced.stock.wikimedia import (
    WikimediaStockProvider,
)
from otio_app.services.without_voiceover_enhanced.stock_provider_config import (
    PROVIDER_STATUS_COMPLETED,
    PROVIDER_STATUS_DISABLED,
    PROVIDER_STATUS_FAILED,
    PROVIDER_STATUS_UNAVAILABLE,
    SUPPORTED_STOCK_PROVIDERS,
    UNSUPPORTED_PROVIDER_KEYS,
    enabled_provider_names,
    load_stock_providers_config,
)

logger = logging.getLogger(__name__)

# Canonical names — Adobe Stock must never appear here.
REQUIRED_PROVIDER_NAMES = SUPPORTED_STOCK_PROVIDERS


def get_stock_providers() -> list[StockProvider]:
    """Alle unterstützten Adapter (Adobe nicht enthalten)."""
    return [
        PexelsStockProvider(),
        PixabayStockProvider(),
        WikimediaStockProvider(),
        OpenverseStockProvider(),
        ArchiveOrgStockProvider(),
    ]


def get_provider_map() -> dict[str, StockProvider]:
    return {provider.provider_name: provider for provider in get_stock_providers()}


def search_all_providers(
    query: str,
    media_type: str | None = None,
    *,
    providers: list[StockProvider] | None = None,
    enabled_names: Iterable[str] | None = None,
) -> tuple[list[StockCandidate], dict[str, str]]:
    """Sucht nur bei aktivierten Anbietern.

    Statuswerte: completed | disabled | unavailable | failed
    """
    provider_map = (
        {p.provider_name: p for p in providers}
        if providers is not None
        else get_provider_map()
    )
    # Always report status for the five supported names when using the default set.
    report_names = (
        list(SUPPORTED_STOCK_PROVIDERS)
        if providers is None
        else list(provider_map.keys())
    )

    if enabled_names is None:
        enabled_set = set(report_names)
    else:
        enabled_set = set(enabled_names)

    candidates: list[StockCandidate] = []
    status_map: dict[str, str] = {}

    for name in report_names:
        if name in UNSUPPORTED_PROVIDER_KEYS:
            status_map[name] = "unsupported"
            continue
        if name not in enabled_set:
            status_map[name] = PROVIDER_STATUS_DISABLED
            continue
        provider = provider_map.get(name)
        if provider is None:
            status_map[name] = PROVIDER_STATUS_UNAVAILABLE
            continue
        readiness = provider.readiness()
        if readiness.status != "ready":
            status_map[name] = PROVIDER_STATUS_UNAVAILABLE
            continue
        try:
            found = provider.search(query, media_type=media_type)
            candidates.extend(found)
            status_map[name] = PROVIDER_STATUS_COMPLETED
        except Exception as exc:  # noqa: BLE001 — provider isolation
            logger.warning("Stock provider %s failed: %s", name, exc)
            status_map[name] = PROVIDER_STATUS_FAILED

    return candidates, status_map


def search_configured_providers(
    project: Project,
    query: str,
    media_type: str | None = None,
) -> tuple[list[StockCandidate], dict[str, str], list[str]]:
    """Projektkonfiguration anwenden; deaktivierte nie aufrufen/keine Key-Prüfung."""
    config = load_stock_providers_config(project)
    enabled = enabled_provider_names(project)
    # Ensure disabled providers are marked without readiness()/search().
    _ = config  # config loaded for side-effect documentation / future use
    candidates, status = search_all_providers(
        query,
        media_type=media_type,
        enabled_names=enabled,
    )
    return candidates, status, enabled
