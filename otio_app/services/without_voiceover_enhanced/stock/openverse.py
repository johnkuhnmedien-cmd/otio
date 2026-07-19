"""Openverse StockProvider (no API key required for basic search)."""

from __future__ import annotations

import requests

from otio_app.services.without_voiceover_enhanced.models import StockCandidate
from otio_app.services.without_voiceover_enhanced.stock.base import (
    ProviderStatus,
    StockProvider,
    unknown_or_null,
)


class OpenverseStockProvider(StockProvider):
    provider_name = "openverse"

    def readiness(self) -> ProviderStatus:
        return ProviderStatus(self.provider_name, "ready")

    def search(self, query: str, media_type: str | None = None) -> list[StockCandidate]:
        endpoint = "images"
        resolved_type = "photo"
        if (media_type or "").lower() == "video":
            endpoint = "videos"
            resolved_type = "video"
        response = requests.get(
            f"https://api.openverse.org/v1/{endpoint}/",
            params={"q": query, "page_size": 8},
            timeout=30,
            headers={"User-Agent": "otio-without-vo-enhanced-mvp/1.0"},
        )
        response.raise_for_status()
        results = response.json().get("results") or []
        candidates: list[StockCandidate] = []
        for index, item in enumerate(results, start=1):
            candidates.append(
                StockCandidate(
                    candidate_id=f"openverse_{item.get('id', index)}",
                    provider=self.provider_name,
                    provider_asset_id=str(item.get("id") or ""),
                    title=str(item.get("title") or query),
                    media_type=resolved_type,
                    creator=unknown_or_null(item.get("creator")),
                    source_page=unknown_or_null(item.get("foreign_landing_url")) or "",
                    preview_url=unknown_or_null(item.get("url") or item.get("thumbnail")) or "",
                    width=item.get("width"),
                    height=item.get("height"),
                    duration_seconds=None,
                    license=unknown_or_null(item.get("license")),
                    attribution=unknown_or_null(item.get("attribution") or item.get("creator")),
                )
            )
        return candidates
