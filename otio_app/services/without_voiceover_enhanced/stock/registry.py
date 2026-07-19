"""Registrierung und fehlertolerante Mehranbieter-Suche."""

from __future__ import annotations

import logging

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

logger = logging.getLogger(__name__)

REQUIRED_PROVIDER_NAMES = (
    "pexels",
    "pixabay",
    "wikimedia",
    "openverse",
    "archive.org",
)


def get_stock_providers() -> list[StockProvider]:
    return [
        PexelsStockProvider(),
        PixabayStockProvider(),
        WikimediaStockProvider(),
        OpenverseStockProvider(),
        ArchiveOrgStockProvider(),
    ]


def search_all_providers(
    query: str,
    media_type: str | None = None,
    *,
    providers: list[StockProvider] | None = None,
) -> tuple[list[StockCandidate], dict[str, str]]:
    """Sucht bei allen Anbietern; unavailable stoppt nicht die anderen."""
    active = providers if providers is not None else get_stock_providers()
    candidates: list[StockCandidate] = []
    status_map: dict[str, str] = {}
    for provider in active:
        readiness = provider.readiness()
        if readiness.status != "ready":
            status_map[provider.provider_name] = f"unavailable: {readiness.message}"
            continue
        try:
            found = provider.search(query, media_type=media_type)
            candidates.extend(found)
            status_map[provider.provider_name] = f"ok:{len(found)}"
        except Exception as exc:  # noqa: BLE001 — provider isolation
            logger.warning("Stock provider %s failed: %s", provider.provider_name, exc)
            status_map[provider.provider_name] = f"error: {exc}"
    return candidates, status_map
