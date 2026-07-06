"""Adobe Stock — Suche/Preview; Lizenz nur nach Freigabe."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from otio_app.analysis_models import SupplementAssetSidecar, SupplementCandidate, SupplementRequest
from otio_app.defaults import (
    CANDIDATE_STATUS_MOCK_ONLY,
    PROVIDER_STATUS_MOCK,
    RIGHTS_STATUS_NEEDS_LICENSE_REVIEW,
    SUPPLEMENT_SOURCE_ADOBE,
)
from otio_app.services.supplement_search import preferred_search_query
from otio_app.services.supplement_sources.base import ProviderReadiness, SupplementAsset, SupplementSourceAdapter


class AdobeStockAdapter(SupplementSourceAdapter):
    provider = SUPPLEMENT_SOURCE_ADOBE

    def readiness(self) -> ProviderReadiness:
        return ProviderReadiness(
            provider=self.provider,
            status=PROVIDER_STATUS_MOCK,
            message="Adobe Stock API/Lizenzierung ist noch nicht angebunden.",
            search_enabled=True,
            acquire_enabled=False,
            is_mock=True,
        )

    def search(self, request: SupplementRequest) -> list[SupplementCandidate]:
        query = preferred_search_query(request)
        return [
            SupplementCandidate(
                candidate_id=f"cand_{uuid.uuid4().hex[:8]}",
                supplement_request_id=request.supplement_request_id,
                provider=self.provider,
                provider_asset_id=f"adobe_{uuid.uuid4().hex[:6]}",
                title=f"Adobe Stock: {query[:40]}",
                description=request.visual_requirement,
                preview_url=f"https://stock.adobe.com/preview/{uuid.uuid4().hex[:8]}",
                media_type="video",
                width=3840,
                height=2160,
                duration_sec=request.duration_needed_sec,
                estimated_cost=29.99,
                requires_purchase=True,
                requires_user_approval=True,
                match_score=0.72,
                match_reason="Adobe Stock Vorschau",
                status=CANDIDATE_STATUS_MOCK_ONLY,
                provider_status=PROVIDER_STATUS_MOCK,
                is_mock=True,
                download_enabled=False,
            )
        ]

    def acquire(
        self,
        candidate: SupplementCandidate,
        destination_folder: Path,
    ) -> SupplementAsset:
        raise PermissionError("Adobe Stock ist noch nicht produktiv angebunden.")
