"""Phase 10 fake stock gateway tests."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from otio_app.discovery_v2.adapters.stock_fake import (
    reset_fake_stock_test_hook,
    set_fake_stock_test_hook,
)
from otio_app.discovery_v2.adapters.stock_gateway import StockGatewayError, StockSearchGateway
from otio_app.discovery_v2.domain.supplementation import (
    FAKE_STOCK_ADAPTER_VERSION,
    MAX_STOCK_CANDIDATES_PER_ATTEMPT,
    STOCK_PROVIDER_FAKE,
    SUPPLEMENTATION_ERROR_GATEWAY_UNCONFIGURED,
    SUPPLEMENTATION_ERROR_RESPONSE_INVALID,
    StockCandidate,
    StockConfig,
    StockSearchRequest,
    StockSearchResponse,
)


@pytest.fixture(autouse=True)
def _reset_hook() -> None:
    reset_fake_stock_test_hook()
    yield
    reset_fake_stock_test_hook()


def _config(enabled: bool = True) -> StockConfig:
    return StockConfig(
        provider=STOCK_PROVIDER_FAKE,
        enabled=enabled,
        adapter_version=FAKE_STOCK_ADAPTER_VERSION,
        gateway_version="discovery-stock-gateway-v1",
        max_retries=1,
        timeout_seconds=5,
    )


def _request() -> StockSearchRequest:
    return StockSearchRequest(
        project_id="project-1",
        request_id="request-1",
        gap_id="gap-1",
        query_text="lokale suche",
        search_strategy="fake",
    )


def test_fake_stock_gateway_is_deterministic_local_and_license_unknown() -> None:
    response = StockSearchGateway(config=_config()).search(_request())
    second = StockSearchGateway(config=_config()).search(_request())
    assert [c.provider_candidate_id for c in response.candidates] == [
        c.provider_candidate_id for c in second.candidates
    ]
    assert 0 < len(response.candidates) <= MAX_STOCK_CANDIDATES_PER_ATTEMPT
    assert {candidate.license_status.value for candidate in response.candidates} == {"unknown"}
    assert all(
        (candidate.preview_ref or "").startswith("editorial/supplementation/previews/")
        for candidate in response.candidates
    )


def test_gateway_has_no_silent_fallback_when_disabled() -> None:
    with pytest.raises(StockGatewayError) as exc:
        StockSearchGateway(config=_config(enabled=False)).search(_request())
    assert exc.value.code == SUPPLEMENTATION_ERROR_GATEWAY_UNCONFIGURED


def test_adapter_response_over_ten_is_invalid() -> None:
    def too_many(request: StockSearchRequest) -> StockSearchResponse:
        candidates = [
            StockCandidate(
                candidate_id=f"candidate-{index}",
                project_id=request.project_id,
                request_id=request.request_id,
                gap_id=request.gap_id,
                attempt_id="attempt-1",
                provider="fake",
                provider_candidate_id=f"provider-{index}",
                preview_ref=f"editorial/supplementation/previews/attempt-1/{index}.preview",
                description="too many",
                media_kind="image",
                created_at=datetime.now(timezone.utc),
            )
            for index in range(11)
        ]
        return StockSearchResponse.model_construct(
            request_id=request.request_id,
            provider="fake",
            adapter_version=FAKE_STOCK_ADAPTER_VERSION,
            candidates=candidates,
        )

    set_fake_stock_test_hook(too_many)
    with pytest.raises(StockGatewayError) as exc:
        StockSearchGateway(config=_config()).search(_request())
    assert exc.value.code == SUPPLEMENTATION_ERROR_RESPONSE_INVALID
