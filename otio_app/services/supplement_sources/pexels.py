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
    PROVIDER_STATUS_CONFIG_MISSING,
    PROVIDER_STATUS_READY,
    RIGHTS_STATUS_APPROVED,
    SUPPLEMENT_SOURCE_PEXELS,
)
from otio_app.services.api_keys import get_api_key
from otio_app.services.media_utils import probe_duration_seconds
from otio_app.services.supplement_search import (
    base_location_for_request,
    build_pexels_query_variants,
    location_match_for_text,
)
from otio_app.services.supplement_sources.base import ProviderReadiness, SupplementAsset, SupplementSourceAdapter

PEXELS_VIDEO_SEARCH_ENDPOINT = "https://api.pexels.com/v1/videos/search"
MIN_DOWNLOAD_BYTES = 100 * 1024


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
        api_key = get_api_key("PEXELS_API_KEY")
        if not api_key:
            return []
        return self._search_api(request, api_key)

    def _search_api(
        self,
        request: SupplementRequest,
        api_key: str,
    ) -> list[SupplementCandidate]:
        payload: dict = {}
        query_used = ""
        for query in build_pexels_query_variants(request):
            query_used = query
            params = urllib.parse.urlencode({"query": query, "per_page": 8})
            req = urllib.request.Request(
                f"{PEXELS_VIDEO_SEARCH_ENDPOINT}?{params}",
                headers={"Authorization": api_key},
            )
            with urllib.request.urlopen(req, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if payload.get("videos"):
                break
        if not payload.get("videos"):
            return []
        candidates: list[SupplementCandidate] = []
        location = base_location_for_request(request)
        for video in payload.get("videos", [])[:8]:
            candidate = self._candidate_from_video(
                request=request,
                video=video,
                query_used=query_used,
                location_name=location,
            )
            candidates.append(candidate)
        return candidates

    def _select_video_file(
        self,
        files: list[dict],
        *,
        timeline_width: int = 0,
    ) -> dict | None:
        downloadable = [entry for entry in files if entry.get("link")]
        if not downloadable:
            return None

        def score(entry: dict) -> tuple[int, int, int, int]:
            file_type = str(entry.get("file_type", "")).lower()
            quality = str(entry.get("quality", "")).lower()
            width = int(entry.get("width") or 0)
            progressive_mp4 = 1 if "mp4" in file_type else 0
            hd_or_better = 1 if quality in {"hd", "uhd"} or width >= 1280 else 0
            timeline_fit = 1 if timeline_width and width >= timeline_width else 0
            return progressive_mp4, hd_or_better, timeline_fit, width

        return max(downloadable, key=score)

    def _candidate_from_video(
        self,
        *,
        request: SupplementRequest,
        video: dict,
        query_used: str,
        location_name: str,
    ) -> SupplementCandidate:
        files = video.get("video_files", []) or []
        selected_file = self._select_video_file(files)
        status = CANDIDATE_STATUS_FOUND if selected_file else "NOT_DOWNLOADABLE"
        title = video.get("url") or f"Pexels Video {video.get('id', '')}"
        location_match, required_terms, present_terms = location_match_for_text(
            f"{title} {request.visual_requirement} {query_used}",
            location_name,
            broadened=request.allow_broader_search and location_name.casefold() not in query_used.casefold(),
        )
        return SupplementCandidate(
            candidate_id=f"cand_{uuid.uuid4().hex[:8]}",
            supplement_request_id=request.supplement_request_id,
            provider=self.provider,
            provider_asset_id=str(video.get("id", "")),
            title=title[:120],
            description=request.visual_requirement,
            preview_url=video.get("image", ""),
            download_url=(selected_file or {}).get("link", ""),
            creator=video.get("user", {}).get("name", ""),
            creator_url=video.get("user", {}).get("url", ""),
            license="Pexels License",
            license_url="https://www.pexels.com/license/",
            rights_status=RIGHTS_STATUS_APPROVED,
            source_page_url=video.get("url", ""),
            media_type="video",
            width=int(video.get("width") or (selected_file or {}).get("width") or 0),
            height=int(video.get("height") or (selected_file or {}).get("height") or 0),
            duration_sec=float(video.get("duration") or request.duration_needed_sec),
            requires_purchase=False,
            requires_user_approval=location_match in {"broadened", "missing"},
            match_score=0.75 if location_match != "missing" else 0.35,
            match_reason="Pexels API Treffer",
            status=status,
            provider_status=PROVIDER_STATUS_READY,
            is_mock=False,
            download_enabled=bool(selected_file),
            query_used=query_used,
            folder_name=request.folder_name,
            location_name=location_name,
            location_terms_required=required_terms,
            location_terms_present=present_terms,
            location_match=location_match,
            pexels_video_file_id=str((selected_file or {}).get("id", "")),
            pexels_quality=str((selected_file or {}).get("quality", "")),
            pexels_file_type=str((selected_file or {}).get("file_type", "")),
            pexels_fps=float((selected_file or {}).get("fps") or 0.0),
            selected_video_file_width=int((selected_file or {}).get("width") or 0),
            selected_video_file_height=int((selected_file or {}).get("height") or 0),
        )

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
        if "example.com" in candidate.download_url:
            raise ValueError("Pexels download_url ist keine echte Medien-URL.")

        destination_folder.mkdir(parents=True, exist_ok=True)
        folder_slug = destination_folder.parent.parent.name.replace(" ", "_")
        filename = (
            f"{folder_slug}_{candidate.supplement_request_id}_pexels_{candidate.provider_asset_id}.mp4"
        )
        local_path = destination_folder / filename
        try:
            with urllib.request.urlopen(candidate.download_url, timeout=60) as response:
                status = int(getattr(response, "status", 200) or 200)
                if status != 200:
                    raise RuntimeError(f"HTTP Status {status}")
                content_type = response.headers.get("Content-Type", "")
                is_octet_mp4 = content_type.startswith("application/octet-stream") and candidate.download_url.lower().endswith(".mp4")
                if not (content_type.startswith("video/") or is_octet_mp4):
                    raise ValueError(f"Unerwarteter Content-Type: {content_type or 'unbekannt'}")
                data = response.read()
        except (urllib.error.URLError, OSError) as exc:
            raise RuntimeError(f"Pexels-Download fehlgeschlagen: {exc}") from exc
        if len(data) < MIN_DOWNLOAD_BYTES:
            raise RuntimeError("Pexels-Download zu klein — vermutlich kein gültiges Asset.")
        local_path.write_bytes(data)
        duration = probe_duration_seconds(local_path)
        if duration is None:
            try:
                local_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise RuntimeError("ffprobe konnte Pexels-Download nicht lesen.")
        sidecar = SupplementAssetSidecar(
            asset_id=f"asset_pexels_{candidate.provider_asset_id}",
            supplement_request_id=candidate.supplement_request_id,
            provider=self.provider,
            provider_asset_id=candidate.provider_asset_id,
            source_url=candidate.source_page_url,
            download_url=candidate.download_url,
            query_used=candidate.query_used,
            location_name=candidate.location_name,
            location_match=candidate.location_match,
            license=candidate.license,
            license_url=candidate.license_url,
            creator=candidate.creator,
            creator_url=candidate.creator_url,
            acquisition_method="api_download",
            downloaded_at=datetime.now(timezone.utc),
            original_filename=filename,
            local_path=str(local_path),
            rights_status=RIGHTS_STATUS_APPROVED,
            requires_attribution=True,
            approval_status="APPROVED",
        )
        return SupplementAsset(local_path=local_path, sidecar=sidecar)
