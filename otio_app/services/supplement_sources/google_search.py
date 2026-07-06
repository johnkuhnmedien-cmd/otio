"""Google Search — nur Discovery, keine automatische Produktionsfreigabe."""

from __future__ import annotations

import uuid

from otio_app.analysis_models import SupplementCandidate, SupplementRequest
from otio_app.defaults import SUPPLEMENT_SOURCE_GOOGLE
from otio_app.services.supplement_sources.base import SupplementSourceAdapter


class GoogleSearchAdapter(SupplementSourceAdapter):
    provider = SUPPLEMENT_SOURCE_GOOGLE

    def search(self, request: SupplementRequest) -> list[SupplementCandidate]:
        query = request.visual_requirement[:80] or request.passage_text[:80]
        return [
            SupplementCandidate(
                candidate_id=f"cand_{uuid.uuid4().hex[:8]}",
                supplement_request_id=request.supplement_request_id,
                provider=self.provider,
                provider_asset_id=f"google_{uuid.uuid4().hex[:6]}",
                title=f"Google: {query[:40]}",
                description=request.visual_requirement,
                preview_url=f"https://example.com/preview/{uuid.uuid4().hex[:8]}.jpg",
                download_url=f"https://example.com/media/{uuid.uuid4().hex[:8]}.jpg",
                source_page_url=f"https://www.google.com/search?q={query.replace(' ', '+')}",
                license="Unbekannt — manuelle Rechteprüfung erforderlich",
                media_type="image",
                width=1920,
                height=1080,
                requires_purchase=False,
                requires_user_approval=True,
                match_score=0.55,
                match_reason="Google Discovery — Rechteprüfung nötig",
                status="CANDIDATE",
            )
        ]
