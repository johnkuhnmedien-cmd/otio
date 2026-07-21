"""Internet Archive (Archive.org) StockProvider."""

from __future__ import annotations

from otio_app.services.without_voiceover_enhanced.models import StockCandidate
from otio_app.services.without_voiceover_enhanced.stock.base import (
    ProviderStatus,
    StockProvider,
    optional_text,
    unknown_or_null,
)
from otio_app.services.without_voiceover_enhanced.stock.http_utils import stock_get


class ArchiveOrgStockProvider(StockProvider):
    provider_name = "archive_org"

    def readiness(self) -> ProviderStatus:
        return ProviderStatus(self.provider_name, "ready")

    def search(self, query: str, media_type: str | None = None) -> list[StockCandidate]:
        mediatype = "movies" if (media_type or "").lower() == "video" else "image"
        response = stock_get(
            "https://archive.org/advancedsearch.php",
            params={
                "q": f"{query} AND mediatype:({mediatype})",
                "fl[]": ["identifier", "title", "creator", "licenseurl"],
                "rows": 8,
                "page": 1,
                "output": "json",
            },
        )
        docs = ((response.json().get("response") or {}).get("docs")) or []
        candidates: list[StockCandidate] = []
        for index, item in enumerate(docs, start=1):
            identifier = str(item.get("identifier") or index)
            source = f"https://archive.org/details/{identifier}"
            candidates.append(
                StockCandidate(
                    candidate_id=f"archive_{identifier}",
                    provider=self.provider_name,
                    provider_asset_id=identifier,
                    title=optional_text(item.get("title"), default=query),
                    media_type="video" if mediatype == "movies" else "photo",
                    creator=optional_text(item.get("creator")),
                    source_page=source,
                    preview_url=source,
                    width=None,
                    height=None,
                    duration_seconds=None,
                    license=unknown_or_null(item.get("licenseurl")),
                    attribution=unknown_or_null(item.get("creator")),
                )
            )
        return candidates
