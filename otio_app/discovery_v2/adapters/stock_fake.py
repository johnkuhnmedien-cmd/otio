"""Deterministic fake stock adapter for local Phase 10 E2E tests."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable
from uuid import uuid5, NAMESPACE_URL

from otio_app.discovery_v2.domain.supplementation import (
    FAKE_STOCK_ADAPTER_VERSION,
    MAX_STOCK_CANDIDATES_PER_ATTEMPT,
    STOCK_PROVIDER_FAKE,
    StockCandidate,
    StockSearchRequest,
    StockSearchResponse,
    metadata_fingerprint,
)
from otio_app.discovery_v2.supplementation_paths import supplementation_preview_relative_path

FakeStockHook = Callable[[StockSearchRequest], StockSearchResponse | Exception]

_FAKE_STOCK_TEST_HOOK: FakeStockHook | None = None


def set_fake_stock_test_hook(hook: FakeStockHook | None) -> None:
    global _FAKE_STOCK_TEST_HOOK
    _FAKE_STOCK_TEST_HOOK = hook


def reset_fake_stock_test_hook() -> None:
    set_fake_stock_test_hook(None)


class FakeStockSearchAdapter:
    """No-network provider returning deterministic metadata-only candidates."""

    provider = STOCK_PROVIDER_FAKE
    adapter_version = FAKE_STOCK_ADAPTER_VERSION

    def search(self, request: StockSearchRequest) -> StockSearchResponse:
        if _FAKE_STOCK_TEST_HOOK is not None:
            hooked = _FAKE_STOCK_TEST_HOOK(request)
            if isinstance(hooked, Exception):
                raise hooked
            return hooked

        count = min(request.max_results, 3)
        candidates: list[StockCandidate] = []
        for index in range(count):
            provider_candidate_id = _stable_id(request, index)
            preview_ref = supplementation_preview_relative_path(
                request.request_id,
                provider_candidate_id,
            )
            candidate = StockCandidate(
                candidate_id=str(uuid5(NAMESPACE_URL, f"{request.request_id}:{index}")),
                project_id=request.project_id,
                request_id=request.request_id,
                gap_id=request.gap_id,
                attempt_id="__pending__",
                provider=STOCK_PROVIDER_FAKE,
                provider_candidate_id=provider_candidate_id,
                preview_ref=preview_ref,
                description=f"Fake stock candidate {index + 1}: {request.query_text}",
                media_kind="image" if index % 2 else "video",
                visible_metadata={
                    "source": "fake",
                    "query": request.query_text,
                    "rank": index + 1,
                    "phase": "10-local-manual",
                },
                geographic_hint=None,
                created_at=datetime.now(timezone.utc),
            )
            candidates.append(
                candidate.model_copy(
                    update={"metadata_fingerprint": metadata_fingerprint(candidate)}
                )
            )
        return StockSearchResponse(
            request_id=request.request_id,
            provider=STOCK_PROVIDER_FAKE,
            adapter_version=FAKE_STOCK_ADAPTER_VERSION,
            candidates=candidates,
        )


def _stable_id(request: StockSearchRequest, index: int) -> str:
    return str(uuid5(NAMESPACE_URL, f"fake-stock:{request.gap_id}:{request.query_text}:{index}"))


__all__ = [
    "FakeStockSearchAdapter",
    "reset_fake_stock_test_hook",
    "set_fake_stock_test_hook",
]
