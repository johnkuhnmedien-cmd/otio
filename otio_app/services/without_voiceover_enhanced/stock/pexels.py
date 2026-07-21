"""Pexels StockProvider."""

from __future__ import annotations

import os

import requests

from otio_app.services.api_keys import get_api_key
from otio_app.services.without_voiceover_enhanced.models import StockCandidate
from otio_app.services.without_voiceover_enhanced.stock.base import (
    ProviderStatus,
    StockProvider,
    optional_text,
    unknown_or_null,
)


class PexelsStockProvider(StockProvider):
    provider_name = "pexels"

    def readiness(self) -> ProviderStatus:
        key = get_api_key("PEXELS_API_KEY") or os.environ.get("PEXELS_API_KEY")
        if not key:
            return ProviderStatus(self.provider_name, "unavailable", "PEXELS_API_KEY fehlt")
        return ProviderStatus(self.provider_name, "ready")

    def search(self, query: str, media_type: str | None = None) -> list[StockCandidate]:
        status = self.readiness()
        if status.status != "ready":
            raise RuntimeError(status.message)
        key = get_api_key("PEXELS_API_KEY") or os.environ.get("PEXELS_API_KEY")
        headers = {"Authorization": key}
        want_video = (media_type or "video").lower() == "video"
        url = "https://api.pexels.com/videos/search" if want_video else "https://api.pexels.com/v1/search"
        response = requests.get(
            url,
            headers=headers,
            params={"query": query, "per_page": 8},
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        candidates: list[StockCandidate] = []
        if want_video:
            for index, item in enumerate(payload.get("videos") or [], start=1):
                user = item.get("user") or {}
                download_url = _best_pexels_video_url(item.get("video_files") or [])
                candidates.append(
                    StockCandidate(
                        candidate_id=f"pexels_video_{item.get('id', index)}",
                        provider=self.provider_name,
                        provider_asset_id=str(item.get("id") or ""),
                        title=str(item.get("url") or query),
                        media_type="video",
                        creator=optional_text(user.get("name")),
                        source_page=unknown_or_null(item.get("url")) or "",
                        preview_url=unknown_or_null(
                            (item.get("image") or "")
                        ) or "",
                        download_url=download_url,
                        width=item.get("width"),
                        height=item.get("height"),
                        duration_seconds=item.get("duration"),
                        license="Pexels License",
                        attribution=unknown_or_null(user.get("name")),
                    )
                )
        else:
            for index, item in enumerate(payload.get("photos") or [], start=1):
                src = item.get("src") or {}
                photographer = item.get("photographer")
                download_url = str(
                    src.get("original")
                    or src.get("large2x")
                    or src.get("large")
                    or src.get("medium")
                    or ""
                )
                candidates.append(
                    StockCandidate(
                        candidate_id=f"pexels_photo_{item.get('id', index)}",
                        provider=self.provider_name,
                        provider_asset_id=str(item.get("id") or ""),
                        title=str(item.get("alt") or query),
                        media_type="photo",
                        creator=optional_text(photographer),
                        source_page=unknown_or_null(item.get("url")) or "",
                        preview_url=unknown_or_null(src.get("medium")) or "",
                        download_url=download_url,
                        width=item.get("width"),
                        height=item.get("height"),
                        duration_seconds=None,
                        license="Pexels License",
                        attribution=unknown_or_null(photographer),
                    )
                )
        return candidates


def _best_pexels_video_url(files: list) -> str:
    """Wählt eine möglichst große HD-Datei ohne unnötig riesige 4K-Last."""
    scored: list[tuple[int, str]] = []
    for item in files:
        if not isinstance(item, dict):
            continue
        link = str(item.get("link") or "").strip()
        if not link:
            continue
        width = int(item.get("width") or 0)
        height = int(item.get("height") or 0)
        # Bevorzuge ~1080p; bestrafe sehr kleine und sehr große Dateien leicht.
        score = width * height
        if 720 <= height <= 1080:
            score += 10_000_000
        scored.append((score, link))
    if not scored:
        return ""
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return scored[0][1]
