"""Manuell — lokales Asset akzeptieren oder überspringen."""

from __future__ import annotations

import uuid

from otio_app.analysis_models import SupplementCandidate, SupplementRequest
from otio_app.defaults import SUPPLEMENT_SOURCE_MANUAL
from otio_app.services.supplement_sources.base import SupplementSourceAdapter


class ManualAdapter(SupplementSourceAdapter):
    provider = SUPPLEMENT_SOURCE_MANUAL

    def search(self, request: SupplementRequest) -> list[SupplementCandidate]:
        if not request.local_best_asset_id:
            return []
        return [
            SupplementCandidate(
                candidate_id=f"cand_{uuid.uuid4().hex[:8]}",
                supplement_request_id=request.supplement_request_id,
                provider=self.provider,
                provider_asset_id=request.local_best_asset_id,
                title="Lokalen Kandidaten akzeptieren",
                description=request.reason,
                media_type="video",
                match_score=request.local_best_match_score,
                match_reason="Manuell: lokales Asset akzeptieren",
                requires_user_approval=True,
                status="MANUAL_LOCAL",
            )
        ]
