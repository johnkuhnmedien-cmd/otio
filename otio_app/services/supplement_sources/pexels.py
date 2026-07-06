"""Pexels — Download nach Nutzerbestätigung."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

from otio_app.analysis_models import SupplementAssetSidecar, SupplementCandidate, SupplementRequest
from otio_app.defaults import (
    CANDIDATE_STATUS_DOWNLOAD_FAILED,
    CANDIDATE_STATUS_FOUND,
    CANDIDATE_STATUS_MOCK_ONLY,
    PROVIDER_STATUS_CONFIG_MISSING,
    PROVIDER_STATUS_READY,
    RIGHTS_STATUS_APPROVED,
    SUPPLEMENT_SOURCE_PEXELS,
)
from otio_app.services.api_keys import get_api_key
from otio_app.services.supplement_search import preferred_search_query
from otio_app.services.supplement_sources.base import ProviderReadiness, SupplementAsset, SupplementSourceAdapter


class PexelsAdapter(SupplementSourceAdapter):
    provider = SUPPLEMENT_SOURCE_PEXELS

    def readiness(self) -> ProviderReadiness:
        if get_api_key("PEXELS_API_KEY"):
            return ProviderReadiness(
                provider=self.provider,
                status=PROVIDER_STATUS_READY,
                message="Pexels API-Key vorhanden.",
                search_enabled=True,
                acquire_enabled=True,
            )
        return ProviderReadiness(
            provider=self.provider,
            status=PROVIDER_STATUS_CONFIG_MISSING,
            message="PEXELS_API_KEY fehlt.",
            search_enabled=True,
            acquire_enabled=False,
        )

    def search(self, request: SupplementRequest) -> list[SupplementCandidate]:
        query = preferred_search_query(request)
        api_key = get_api_key("PEXELS_API_KEY")
        if api_key:
            try:
                return self._search_api(request, query, api_key)
            except (urllib.error.URLError, json.JSONDecodeError, KeyError):
                pass
        return [
            SupplementCandidate(
                candidate_id=f"cand_{uuid.uuid4().hex[:8]}",
                supplement_request_id=request.supplement_request_id,
                provider=self.provider,
                provider_asset_id=f"pexels_{uuid.uuid4().hex[:6]}",
                title=f"Pexels: {query[:40]}",
                description=request.visual_requirement,
                preview_url="https://images.pexels.com/photos/preview.jpg",
                download_url="https://videos.pexels.com/video-files/sample.mp4",
                creator="Pexels Contributor",
                license="Pexels License",
                license_url="https://www.pexels.com/license/",
                media_type="video",
                width=3840,
                height=2160,
                duration_sec=request.duration_needed_sec,
                requires_purchase=False,
                requires_user_approval=True,
                match_score=0.68,
                match_reason="Demo-Kandidat — PEXELS_API_KEY fehlt",
                status=CANDIDATE_STATUS_MOCK_ONLY,
                provider_status=PROVIDER_STATUS_CONFIG_MISSING,
                is_mock=True,
                download_enabled=False,
            )
        ]

    def _search_api(
        self,
        request: SupplementRequest,
        query: str,
        api_key: str,
    ) -> list[SupplementCandidate]:
        url = f"https://api.pexels.com/videos/search?query={urllib.parse.quote(query)}&per_page=3"
        req = urllib.request.Request(url, headers={"Authorization": api_key})
        with urllib.request.urlopen(req, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
        candidates: list[SupplementCandidate] = []
        for video in payload.get("videos", [])[:3]:
            files = video.get("video_files", [])
            best = max(files, key=lambda entry: entry.get("width", 0), default={})
            candidates.append(
                SupplementCandidate(
                    candidate_id=f"cand_{uuid.uuid4().hex[:8]}",
                    supplement_request_id=request.supplement_request_id,
                    provider=self.provider,
                    provider_asset_id=str(video.get("id", "")),
                    title=video.get("url", query)[:80],
                    description=request.visual_requirement,
                    preview_url=video.get("image", ""),
                    download_url=best.get("link", ""),
                    creator=video.get("user", {}).get("name", ""),
                    license="Pexels License",
                    license_url="https://www.pexels.com/license/",
                    source_page_url=video.get("url", ""),
                    media_type="video",
                    width=int(best.get("width", 0)),
                    height=int(best.get("height", 0)),
                    duration_sec=float(video.get("duration", request.duration_needed_sec)),
                    requires_purchase=False,
                    requires_user_approval=True,
                    match_score=0.7,
                    match_reason="Pexels API Treffer",
                    status=CANDIDATE_STATUS_FOUND,
                    provider_status=PROVIDER_STATUS_READY,
                    is_mock=False,
                    download_enabled=True,
                )
            )
        return candidates

    def acquire(
        self,
        candidate: SupplementCandidate,
        destination_folder: Path,
    ) -> SupplementAsset:
        if not get_api_key("PEXELS_API_KEY"):
            raise PermissionError("PEXELS_API_KEY fehlt — Pexels-Download ist deaktiviert.")
        if candidate.is_mock or not candidate.download_enabled:
            raise PermissionError("Mock-/Demo-Kandidaten dürfen nicht heruntergeladen werden.")
        if not candidate.download_url:
            raise ValueError("Pexels-Kandidat hat keine download_url.")

        destination_folder.mkdir(parents=True, exist_ok=True)
        filename = (
            f"{candidate.supplement_request_id}_pexels_{candidate.provider_asset_id}.mp4"
        )
        local_path = destination_folder / filename
        try:
            with urllib.request.urlopen(candidate.download_url, timeout=60) as response:
                content_type = response.headers.get("Content-Type", "")
                if not (content_type.startswith("video/") or content_type.startswith("image/")):
                    raise ValueError(f"Unerwarteter Content-Type: {content_type or 'unbekannt'}")
                data = response.read()
        except (urllib.error.URLError, OSError) as exc:
            raise RuntimeError(f"Pexels-Download fehlgeschlagen: {exc}") from exc
        if len(data) < 1024:
            raise RuntimeError("Pexels-Download zu klein — vermutlich kein gültiges Asset.")
        local_path.write_bytes(data)
        sidecar = SupplementAssetSidecar(
            asset_id=f"asset_pexels_{candidate.provider_asset_id}",
            supplement_request_id=candidate.supplement_request_id,
            provider=self.provider,
            provider_asset_id=candidate.provider_asset_id,
            source_url=candidate.source_page_url,
            download_url=candidate.download_url,
            license=candidate.license,
            license_url=candidate.license_url,
            creator=candidate.creator,
            acquisition_method="download",
            downloaded_at=datetime.now(timezone.utc),
            original_filename=filename,
            local_path=str(local_path),
            rights_status=RIGHTS_STATUS_APPROVED,
            requires_attribution=True,
            approval_status="APPROVED",
        )
        return SupplementAsset(local_path=local_path, sidecar=sidecar)
