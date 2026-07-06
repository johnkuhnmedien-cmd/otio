"""Google Search — nur Discovery, keine automatische Produktionsfreigabe."""

from __future__ import annotations

import uuid

from otio_app.analysis_models import SupplementCandidate, SupplementRequest
from otio_app.defaults import (
    CANDIDATE_STATUS_MOCK_ONLY,
    PROVIDER_STATUS_MOCK,
    SUPPLEMENT_SOURCE_GOOGLE,
)
from otio_app.services.supplement_search import preferred_search_query
from otio_app.services.supplement_sources.base import ProviderReadiness, SupplementSourceAdapter


class GoogleSearchAdapter(SupplementSourceAdapter):
    provider = SUPPLEMENT_SOURCE_GOOGLE

    def readiness(self) -> ProviderReadiness:
        return ProviderReadiness(
            provider=self.provider,
            status=PROVIDER_STATUS_MOCK,
            message="Google ist aktuell nur Browser-Discovery; keine echte Search-API.",
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
                provider_asset_id=f"google_{uuid.uuid4().hex[:6]}",
                title=f"Google: {query[:40]}",
                description=request.visual_requirement,
                preview_url="",
                download_url="",
                source_page_url=f"https://www.google.com/search?q={query.replace(' ', '+')}",
                license="Unbekannt — manuelle Rechteprüfung erforderlich",
                media_type="image",
                width=1920,
                height=1080,
                requires_purchase=False,
                requires_user_approval=True,
                match_score=0.55,
                match_reason=f"Google Discovery für Query: {query} — Rechteprüfung nötig",
                status=CANDIDATE_STATUS_MOCK_ONLY,
                provider_status=PROVIDER_STATUS_MOCK,
                is_mock=True,
                download_enabled=False,
            )
        ]
