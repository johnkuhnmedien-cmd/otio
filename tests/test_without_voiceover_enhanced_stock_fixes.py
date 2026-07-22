"""Stock-Suche: Openverse ohne Videos-Endpoint, Wikimedia-Rate-Limit."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from otio_app.services.without_voiceover_enhanced.stock.http_utils import stock_get
from otio_app.services.without_voiceover_enhanced.stock.openverse import (
    OpenverseStockProvider,
)
from otio_app.services.without_voiceover_enhanced.stock.registry import (
    clear_rate_limit_circuit,
    search_all_providers,
)
from otio_app.services.without_voiceover_enhanced.stock.base import (
    ProviderStatus,
    StockProvider,
)
from otio_app.services.without_voiceover_enhanced.models import StockCandidate


def test_archive_org_accepts_missing_or_list_creator() -> None:
    from otio_app.services.without_voiceover_enhanced.stock.archive_org import (
        ArchiveOrgStockProvider,
    )

    provider = ArchiveOrgStockProvider()
    fake = MagicMock()
    fake.json.return_value = {
        "response": {
            "docs": [
                {"identifier": "a1", "title": "One", "creator": None},
                {
                    "identifier": "a2",
                    "title": ["Two", "Alt"],
                    "creator": ["Alice", "Bob"],
                },
            ]
        }
    }
    with patch(
        "otio_app.services.without_voiceover_enhanced.stock.archive_org.stock_get",
        return_value=fake,
    ):
        found = provider.search("denali", media_type="video")
    assert len(found) == 2
    assert found[0].creator == ""
    assert found[1].creator == "Alice, Bob"
    assert found[1].title == "Two, Alt"


def test_openverse_video_request_uses_images_endpoint() -> None:
    provider = OpenverseStockProvider()
    fake = MagicMock()
    fake.json.return_value = {
        "results": [
            {
                "id": "img-1",
                "title": "Denali",
                "url": "https://example.com/a.jpg",
                "license": "cc0",
            }
        ]
    }
    with patch(
        "otio_app.services.without_voiceover_enhanced.stock.openverse.stock_get",
        return_value=fake,
    ) as mocked:
        found = provider.search("Denali wilderness", media_type="video")
    mocked.assert_called_once()
    url = mocked.call_args.args[0]
    assert url.endswith("/v1/images/")
    assert "/v1/videos/" not in url
    assert len(found) == 1
    assert found[0].media_type == "photo"


def test_stock_get_retries_on_429() -> None:
    limited = MagicMock()
    limited.status_code = 429
    limited.headers = {"Retry-After": "0"}
    limited.raise_for_status.side_effect = requests.HTTPError(
        "429 Too Many Requests", response=limited
    )

    ok = MagicMock()
    ok.status_code = 200
    ok.headers = {}
    ok.raise_for_status = MagicMock()
    ok.json.return_value = {}

    with patch(
        "otio_app.services.without_voiceover_enhanced.stock.http_utils.requests.get",
        side_effect=[limited, ok],
    ), patch(
        "otio_app.services.without_voiceover_enhanced.stock.http_utils.time.sleep"
    ) as slept:
        response = stock_get("https://example.com/api", max_retries_on_429=2)
    assert response is ok
    slept.assert_called()


class _RateLimitProvider(StockProvider):
    provider_name = "wikimedia"

    def __init__(self) -> None:
        self.calls = 0

    def readiness(self) -> ProviderStatus:
        return ProviderStatus(self.provider_name, "ready")

    def search(self, query: str, media_type: str | None = None) -> list[StockCandidate]:
        self.calls += 1
        response = MagicMock()
        response.status_code = 429
        raise requests.HTTPError("429 Client Error: Too Many Requests", response=response)


class _OkProvider(StockProvider):
    provider_name = "openverse"

    def readiness(self) -> ProviderStatus:
        return ProviderStatus(self.provider_name, "ready")

    def search(self, query: str, media_type: str | None = None) -> list[StockCandidate]:
        return [
            StockCandidate(
                candidate_id="ov_1",
                provider="openverse",
                title=query,
                media_type="photo",
                license="cc0",
            )
        ]


def test_rate_limit_circuit_skips_provider_for_rest_of_run() -> None:
    clear_rate_limit_circuit()
    limited = _RateLimitProvider()
    ok = _OkProvider()
    providers = [limited, ok]

    found1, status1 = search_all_providers(
        "query one",
        providers=providers,
        enabled_names=["wikimedia", "openverse"],
    )
    found2, status2 = search_all_providers(
        "query two",
        providers=providers,
        enabled_names=["wikimedia", "openverse"],
    )
    assert limited.calls == 1  # second query skipped via circuit
    assert status1["wikimedia"] == "failed"
    assert status2["wikimedia"] == "failed"
    assert status1["openverse"] == "completed"
    assert status2["openverse"] == "completed"
    assert len(found1) == 1
    assert len(found2) == 1
    clear_rate_limit_circuit()
