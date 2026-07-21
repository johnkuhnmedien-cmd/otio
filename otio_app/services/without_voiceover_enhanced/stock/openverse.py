"""Openverse StockProvider (no API key required for basic search).

Openverse bietet nur ``/v1/images/`` und ``/v1/audio/`` — kein Video-Katalog.
Bei preferred_media_type=video fallen wir auf Bilder zurück (Stills als
Supplement), statt den nicht existierenden Endpoint ``/v1/videos/`` (404).
"""

from __future__ import annotations

from otio_app.services.without_voiceover_enhanced.models import StockCandidate
from otio_app.services.without_voiceover_enhanced.stock.base import (
    ProviderStatus,
    StockProvider,
    optional_text,
    unknown_or_null,
)
from otio_app.services.without_voiceover_enhanced.stock.http_utils import stock_get


class OpenverseStockProvider(StockProvider):
    provider_name = "openverse"

    def readiness(self) -> ProviderStatus:
        return ProviderStatus(self.provider_name, "ready")

    def search(self, query: str, media_type: str | None = None) -> list[StockCandidate]:
        requested = (media_type or "").lower().strip()
        if requested == "audio":
            endpoint = "audio"
            resolved_type = "audio"
        else:
            # photo | video | map | archive | illustration | either | …
            # → images (Openverse has no video search endpoint)
            endpoint = "images"
            resolved_type = "photo"
        response = stock_get(
            f"https://api.openverse.org/v1/{endpoint}/",
            params={"q": query, "page_size": 8},
        )
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
                    creator=optional_text(item.get("creator")),
                    source_page=unknown_or_null(item.get("foreign_landing_url")) or "",
                    preview_url=unknown_or_null(
                        item.get("url") or item.get("thumbnail")
                    )
                    or "",
                    width=item.get("width"),
                    height=item.get("height"),
                    duration_seconds=None,
                    license=unknown_or_null(item.get("license")),
                    attribution=unknown_or_null(
                        item.get("attribution") or item.get("creator")
                    ),
                )
            )
        return candidates
