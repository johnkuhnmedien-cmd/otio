"""Provider-neutral stock search gateway for Phase 10."""

from __future__ import annotations

from pydantic import ValidationError

from otio_app.discovery_v2.adapters.stock_fake import FakeStockSearchAdapter
from otio_app.discovery_v2.domain.supplementation import (
    MAX_STOCK_CANDIDATES_PER_ATTEMPT,
    STOCK_PROVIDER_FAKE,
    SUPPLEMENTATION_ERROR_GATEWAY_UNCONFIGURED,
    SUPPLEMENTATION_ERROR_PROVIDER_UNAVAILABLE,
    SUPPLEMENTATION_ERROR_RESPONSE_INVALID,
    StockConfig,
    StockSearchRequest,
    StockSearchResponse,
)


class StockGatewayError(Exception):
    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message or code


class StockSearchGateway:
    """Gateway with no fallback: only the explicitly configured provider is used."""

    def __init__(self, *, config: StockConfig) -> None:
        self.config = config

    def search(self, request: StockSearchRequest) -> StockSearchResponse:
        if not self.config.enabled:
            raise StockGatewayError(
                SUPPLEMENTATION_ERROR_GATEWAY_UNCONFIGURED,
                "Stock gateway is disabled.",
            )
        if self.config.provider != STOCK_PROVIDER_FAKE or request.provider != STOCK_PROVIDER_FAKE:
            raise StockGatewayError(
                SUPPLEMENTATION_ERROR_PROVIDER_UNAVAILABLE,
                f"Stock provider unavailable: {self.config.provider}",
            )
        try:
            response = FakeStockSearchAdapter().search(request)
        except ValidationError as exc:
            raise StockGatewayError(
                SUPPLEMENTATION_ERROR_RESPONSE_INVALID,
                _sanitize(str(exc)),
            ) from exc
        if len(response.candidates) > MAX_STOCK_CANDIDATES_PER_ATTEMPT:
            raise StockGatewayError(
                SUPPLEMENTATION_ERROR_RESPONSE_INVALID,
                "Fake stock response exceeded 10 candidates.",
            )
        for candidate in response.candidates:
            if candidate.provider != STOCK_PROVIDER_FAKE:
                raise StockGatewayError(
                    SUPPLEMENTATION_ERROR_RESPONSE_INVALID,
                    "Fake stock response included an unexpected provider.",
                )
            if candidate.license_status.value != "unknown":
                raise StockGatewayError(
                    SUPPLEMENTATION_ERROR_RESPONSE_INVALID,
                    "Fake stock response must not claim license status.",
                )
        return StockSearchResponse.model_validate(response.model_dump(mode="json"))


def _sanitize(message: str) -> str:
    return " ".join(message.split())[:500]


__all__ = ["StockGatewayError", "StockSearchGateway"]
