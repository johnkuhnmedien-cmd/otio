"""Supplement-Quellen-Registry."""

from __future__ import annotations

from otio_app.defaults import (
    SUPPLEMENT_SOURCE_ADOBE,
    SUPPLEMENT_SOURCE_GOOGLE,
    SUPPLEMENT_SOURCE_MANUAL,
    SUPPLEMENT_SOURCE_NANO_BANANA,
    SUPPLEMENT_SOURCE_PEXELS,
)
from otio_app.services.supplement_sources.adobe_stock import AdobeStockAdapter
from otio_app.services.supplement_sources.base import ProviderReadiness, SupplementSourceAdapter
from otio_app.services.supplement_sources.google_search import GoogleSearchAdapter
from otio_app.services.supplement_sources.manual import ManualAdapter
from otio_app.services.supplement_sources.nano_banana import NanoBananaAdapter
from otio_app.services.supplement_sources.pexels import PexelsAdapter

_ADAPTERS: dict[str, SupplementSourceAdapter] = {
    SUPPLEMENT_SOURCE_ADOBE: AdobeStockAdapter(),
    SUPPLEMENT_SOURCE_PEXELS: PexelsAdapter(),
    SUPPLEMENT_SOURCE_GOOGLE: GoogleSearchAdapter(),
    SUPPLEMENT_SOURCE_NANO_BANANA: NanoBananaAdapter(),
    SUPPLEMENT_SOURCE_MANUAL: ManualAdapter(),
}


def get_supplement_adapter(provider: str) -> SupplementSourceAdapter:
    adapter = _ADAPTERS.get(provider)
    if adapter is None:
        raise ValueError(f"Unbekannte Supplement-Quelle: {provider}")
    return adapter


def get_provider_readiness(provider: str) -> ProviderReadiness:
    return get_supplement_adapter(provider).readiness()


def list_provider_readiness() -> dict[str, ProviderReadiness]:
    return {provider: adapter.readiness() for provider, adapter in _ADAPTERS.items()}
