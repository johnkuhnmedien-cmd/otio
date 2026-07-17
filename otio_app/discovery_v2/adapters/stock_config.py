"""Configuration for Phase 10 stock search adapters."""

from __future__ import annotations

from otio_app.discovery_v2.domain.supplementation import (
    FAKE_STOCK_ADAPTER_VERSION,
    STOCK_GATEWAY_VERSION,
    STOCK_PROVIDER_FAKE,
    StockConfig,
)


def load_stock_config() -> StockConfig:
    """Return the local fake-only stock configuration.

    Phase 10 intentionally has no network provider and no silent fallback path.
    """

    return StockConfig(
        provider=STOCK_PROVIDER_FAKE,
        enabled=True,
        adapter_version=FAKE_STOCK_ADAPTER_VERSION,
        gateway_version=STOCK_GATEWAY_VERSION,
        max_retries=1,
        timeout_seconds=5,
    )


__all__ = ["load_stock_config"]
