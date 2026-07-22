"""MockStockProvider für Tests."""

from __future__ import annotations

from otio_app.services.without_voiceover_enhanced.models import StockCandidate
from otio_app.services.without_voiceover_enhanced.stock.base import (
    ProviderStatus,
    StockProvider,
)


class MockStockProvider(StockProvider):
    provider_name = "mock"

    def __init__(self, *, available: bool = True, results: list[StockCandidate] | None = None):
        self._available = available
        self._results = results or []

    def readiness(self) -> ProviderStatus:
        if not self._available:
            return ProviderStatus(self.provider_name, "unavailable", "mock unavailable")
        return ProviderStatus(self.provider_name, "ready")

    def search(self, query: str, media_type: str | None = None) -> list[StockCandidate]:
        status = self.readiness()
        if status.status != "ready":
            raise RuntimeError(status.message)
        if self._results:
            return list(self._results)
        return [
            StockCandidate(
                candidate_id="stock_001",
                provider=self.provider_name,
                provider_asset_id="mock-1",
                title=f"Mock result for {query}",
                media_type=media_type or "photo",
                creator="Mock Creator",
                source_page="https://example.com/mock",
                preview_url="https://example.com/mock.jpg",
                width=4000,
                height=2600,
                duration_seconds=None,
                license="CC0",
                attribution="Mock Creator",
                selected=False,
            )
        ]
