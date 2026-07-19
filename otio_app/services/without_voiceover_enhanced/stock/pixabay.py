"""Pixabay StockProvider."""

from __future__ import annotations

import os

import requests

from otio_app.services.api_keys import get_api_key
from otio_app.services.without_voiceover_enhanced.models import StockCandidate
from otio_app.services.without_voiceover_enhanced.stock.base import (
    ProviderStatus,
    StockProvider,
    unknown_or_null,
)


class PixabayStockProvider(StockProvider):
    provider_name = "pixabay"

    def readiness(self) -> ProviderStatus:
        key = get_api_key("PIXABAY_API_KEY") or os.environ.get("PIXABAY_API_KEY")
        if not key:
            return ProviderStatus(self.provider_name, "unavailable", "PIXABAY_API_KEY fehlt")
        return ProviderStatus(self.provider_name, "ready")

    def search(self, query: str, media_type: str | None = None) -> list[StockCandidate]:
        status = self.readiness()
        if status.status != "ready":
            raise RuntimeError(status.message)
        key = get_api_key("PIXABAY_API_KEY") or os.environ.get("PIXABAY_API_KEY")
        want_video = (media_type or "photo").lower() == "video"
        if want_video:
            url = "https://pixabay.com/api/videos/"
        else:
            url = "https://pixabay.com/api/"
        response = requests.get(
            url,
            params={"key": key, "q": query, "per_page": 8},
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        candidates: list[StockCandidate] = []
        for index, item in enumerate(payload.get("hits") or [], start=1):
            media = "video" if want_video else "photo"
            candidates.append(
                StockCandidate(
                    candidate_id=f"pixabay_{media}_{item.get('id', index)}",
                    provider=self.provider_name,
                    provider_asset_id=str(item.get("id") or ""),
                    title=str(item.get("tags") or query),
                    media_type=media,
                    creator=unknown_or_null(item.get("user")),
                    source_page=unknown_or_null(item.get("pageURL")) or "",
                    preview_url=unknown_or_null(
                        item.get("previewURL") or item.get("userImageURL")
                    ) or "",
                    width=item.get("imageWidth") or item.get("videos", {}).get("medium", {}).get("width"),
                    height=item.get("imageHeight") or item.get("videos", {}).get("medium", {}).get("height"),
                    duration_seconds=item.get("duration"),
                    license="Pixabay License",
                    attribution=unknown_or_null(item.get("user")),
                )
            )
        return candidates
