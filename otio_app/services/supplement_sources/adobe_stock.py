"""Adobe Stock — Suche/Preview; Lizenz nur nach Freigabe."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from otio_app.analysis_models import SupplementAssetSidecar, SupplementCandidate, SupplementRequest
from otio_app.defaults import RIGHTS_STATUS_NEEDS_LICENSE_REVIEW, SUPPLEMENT_SOURCE_ADOBE
from otio_app.services.supplement_sources.base import SupplementAsset, SupplementSourceAdapter


class AdobeStockAdapter(SupplementSourceAdapter):
    provider = SUPPLEMENT_SOURCE_ADOBE

    def search(self, request: SupplementRequest) -> list[SupplementCandidate]:
        query = request.visual_requirement[:80] or request.passage_text[:80]
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
                status="ADOBE_FOUND",
            )
        ]

    def acquire(
        self,
        candidate: SupplementCandidate,
        destination_folder: Path,
    ) -> SupplementAsset:
        if candidate.status != "ADOBE_LICENSE_APPROVED":
            raise PermissionError(
                "Adobe Asset darf nur nach expliziter Lizenz-Freigabe heruntergeladen werden."
            )
        destination_folder.mkdir(parents=True, exist_ok=True)
        filename = f"{candidate.supplement_request_id}_adobe_{candidate.provider_asset_id}.mp4"
        local_path = destination_folder / filename
        local_path.write_bytes(b"adobe-stock-placeholder")
        sidecar = SupplementAssetSidecar(
            asset_id=f"asset_{candidate.provider_asset_id}",
            supplement_request_id=candidate.supplement_request_id,
            provider=self.provider,
            provider_asset_id=candidate.provider_asset_id,
            source_url=candidate.source_page_url or candidate.preview_url,
            download_url=candidate.download_url,
            license=candidate.license or "Adobe Stock Standard",
            license_url=candidate.license_url,
            creator=candidate.creator,
            acquisition_method="license_download",
            downloaded_at=datetime.now(timezone.utc),
            original_filename=filename,
            local_path=str(local_path),
            rights_status=RIGHTS_STATUS_NEEDS_LICENSE_REVIEW,
            cost=candidate.estimated_cost,
            approval_status="ADOBE_DOWNLOADED",
        )
        return SupplementAsset(local_path=local_path, sidecar=sidecar)
