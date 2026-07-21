"""Wikimedia Commons StockProvider (no API key)."""

from __future__ import annotations

from otio_app.services.without_voiceover_enhanced.models import StockCandidate
from otio_app.services.without_voiceover_enhanced.stock.base import (
    ProviderStatus,
    StockProvider,
    unknown_or_null,
)
from otio_app.services.without_voiceover_enhanced.stock.http_utils import stock_get


class WikimediaStockProvider(StockProvider):
    provider_name = "wikimedia"

    def readiness(self) -> ProviderStatus:
        return ProviderStatus(self.provider_name, "ready")

    def search(self, query: str, media_type: str | None = None) -> list[StockCandidate]:
        # filetype-Hints reduzieren irrelevante Treffer und API-Last.
        search_query = query.strip()
        requested = (media_type or "").lower().strip()
        if requested == "video" and "filetype:" not in search_query.lower():
            search_query = f"{search_query} filetype:video"
        elif requested in {"photo", "image", "illustration", "map"} and (
            "filetype:" not in search_query.lower()
        ):
            search_query = f"{search_query} filetype:bitmap|filetype:drawing"

        response = stock_get(
            "https://commons.wikimedia.org/w/api.php",
            params={
                "action": "query",
                "format": "json",
                "generator": "search",
                "gsrsearch": search_query,
                "gsrlimit": 8,
                "gsrnamespace": 6,
                "prop": "imageinfo",
                "iiprop": "url|size|extmetadata|mime",
            },
        )
        pages = (response.json().get("query") or {}).get("pages") or {}
        candidates: list[StockCandidate] = []
        for index, page in enumerate(pages.values(), start=1):
            info = (page.get("imageinfo") or [{}])[0]
            meta = info.get("extmetadata") or {}
            license_name = unknown_or_null(
                (meta.get("LicenseShortName") or {}).get("value")
            )
            artist = unknown_or_null((meta.get("Artist") or {}).get("value")) or ""
            mime = str(info.get("mime") or "")
            media = "video" if mime.startswith("video/") else "photo"
            if requested == "video" and media != "video":
                continue
            if requested in {"photo", "image", "illustration", "map"} and media != "photo":
                continue
            candidates.append(
                StockCandidate(
                    candidate_id=f"wikimedia_{page.get('pageid', index)}",
                    provider=self.provider_name,
                    provider_asset_id=str(page.get("pageid") or ""),
                    title=str(page.get("title") or query),
                    media_type=media,
                    creator=artist,
                    source_page=unknown_or_null(info.get("descriptionurl")) or "",
                    preview_url=unknown_or_null(info.get("url")) or "",
                    width=info.get("width"),
                    height=info.get("height"),
                    duration_seconds=None,
                    license=license_name,
                    attribution=artist,
                )
            )
        return candidates
