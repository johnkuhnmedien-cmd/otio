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
    CANDIDATE_STATUS_NOT_DOWNLOADABLE_16_9,
    CANDIDATE_STATUS_REJECTED_ASPECT_RATIO,
    PROVIDER_STATUS_CONFIG_MISSING,
    PROVIDER_STATUS_READY,
    RIGHTS_STATUS_APPROVED,
    SUPPLEMENT_SOURCE_PEXELS,
)
from otio_app.services.api_keys import get_api_key
from otio_app.services.media_utils import probe_duration_seconds
from otio_app.services.supplement_search import (
    base_location_for_request,
    build_pexels_photo_query_variants,
    build_pexels_query_variants,
    location_match_for_text,
)
from otio_app.services.supplement_sources.base import ProviderReadiness, SupplementAsset, SupplementSourceAdapter

PEXELS_VIDEO_SEARCH_ENDPOINT = "https://api.pexels.com/v1/videos/search"
PEXELS_PHOTO_SEARCH_ENDPOINT = "https://api.pexels.com/v1/search"
MIN_DOWNLOAD_BYTES = 100 * 1024
TARGET_VIDEO_ASPECT_RATIO = 16 / 9


class PexelsAdapter(SupplementSourceAdapter):
    provider = SUPPLEMENT_SOURCE_PEXELS
    last_debug_report: dict = {}

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
        mode = (request.required_asset_type or "video_preferred").strip()
        self.last_debug_report = self._empty_debug(request, mode)
        candidates: list[SupplementCandidate] = []
        if mode in {"video", "video_preferred", "any"}:
            video_candidates = self._search_videos(request, api_key)
            candidates.extend(video_candidates)
            productive_videos = [
                c for c in video_candidates if c.media_type == "video" and c.download_enabled
            ]
            if mode == "video":
                self._finalize_debug(candidates)
                return candidates
            if mode == "video_preferred" and productive_videos:
                self._finalize_debug(candidates)
                return candidates

        if mode in {"image", "image_preferred", "video_preferred", "any"}:
            candidates.extend(self._search_photos(request, api_key))

        if mode == "image_preferred" and not [c for c in candidates if c.media_type == "image"]:
            candidates.extend(self._search_videos(request, api_key))

        self._finalize_debug(candidates)
        return candidates

    def _empty_debug(self, request: SupplementRequest, mode: str) -> dict:
        return {
            "request_id": request.supplement_request_id,
            "provider": "pexels",
            "media_search_mode": mode,
            "queries_attempted": [],
            "video_endpoint": PEXELS_VIDEO_SEARCH_ENDPOINT,
            "photo_endpoint": PEXELS_PHOTO_SEARCH_ENDPOINT,
            "params_without_api_key": {},
            "http_status_by_query": {},
            "raw_video_result_count": 0,
            "mapped_video_candidate_count": 0,
            "rejected_video_count": 0,
            "raw_photo_result_count": 0,
            "mapped_photo_candidate_count": 0,
            "rejected_photo_count": 0,
            "filter_rejections": [],
            "final_video_candidate_count": 0,
            "final_photo_candidate_count": 0,
        }

    def _request_json(self, endpoint: str, params: dict, api_key: str) -> tuple[int, dict]:
        url = f"{endpoint}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"Authorization": api_key})
        with urllib.request.urlopen(req, timeout=20) as response:
            status = int(getattr(response, "status", 200) or 200)
            return status, json.loads(response.read().decode("utf-8"))

    def _search_videos(self, request: SupplementRequest, api_key: str) -> list[SupplementCandidate]:
        candidates: list[SupplementCandidate] = []
        location = base_location_for_request(request)
        for query in build_pexels_query_variants(request):
            params = {"query": query, "per_page": 15, "orientation": "landscape"}
            self.last_debug_report["queries_attempted"].append(query)
            self.last_debug_report["params_without_api_key"] = params
            status, payload = self._request_json(PEXELS_VIDEO_SEARCH_ENDPOINT, params, api_key)
            self.last_debug_report["http_status_by_query"][query] = status
            videos = payload.get("videos", []) or []
            self.last_debug_report["raw_video_result_count"] += len(videos)
            for video in videos:
                candidate = self._candidate_from_video(
                    request=request,
                    video=video,
                    query_used=query,
                    location_name=location,
                )
                candidates.append(candidate)
                self.last_debug_report["mapped_video_candidate_count"] += 1
                if candidate.status in {CANDIDATE_STATUS_REJECTED_ASPECT_RATIO, CANDIDATE_STATUS_NOT_DOWNLOADABLE_16_9}:
                    self.last_debug_report["rejected_video_count"] += 1
                    self.last_debug_report["filter_rejections"].append(
                        {
                            "provider_asset_id": candidate.provider_asset_id,
                            "reason": candidate.status,
                            "width": candidate.width,
                            "height": candidate.height,
                            "aspect_ratio": candidate.aspect_ratio,
                        }
                    )
            if [candidate for candidate in candidates if candidate.download_enabled]:
                break
        return candidates

    def _search_photos(self, request: SupplementRequest, api_key: str) -> list[SupplementCandidate]:
        candidates: list[SupplementCandidate] = []
        location = base_location_for_request(request)
        for query in build_pexels_photo_query_variants(request):
            params = {"query": query, "per_page": 15, "orientation": "landscape"}
            self.last_debug_report["queries_attempted"].append(query)
            self.last_debug_report["params_without_api_key"] = params
            status, payload = self._request_json(PEXELS_PHOTO_SEARCH_ENDPOINT, params, api_key)
            self.last_debug_report["http_status_by_query"][query] = status
            photos = payload.get("photos", []) or []
            self.last_debug_report["raw_photo_result_count"] += len(photos)
            for photo in photos:
                candidate = self._candidate_from_photo(
                    request=request,
                    photo=photo,
                    query_used=query,
                    location_name=location,
                )
                candidates.append(candidate)
                self.last_debug_report["mapped_photo_candidate_count"] += 1
            if candidates:
                break
        return candidates

    def _finalize_debug(self, candidates: list[SupplementCandidate]) -> None:
        self.last_debug_report["final_video_candidate_count"] = len(
            [c for c in candidates if c.media_type == "video" and c.download_enabled]
        )
        self.last_debug_report["final_photo_candidate_count"] = len(
            [c for c in candidates if c.media_type == "image" and c.download_enabled]
        )

    def _aspect_ratio(self, width: int, height: int) -> float:
        return round(width / height, 6) if width and height else 0.0

    def _is_16_9(self, width: int, height: int, tolerance: float) -> bool:
        aspect = self._aspect_ratio(width, height)
        return bool(aspect and abs(aspect - TARGET_VIDEO_ASPECT_RATIO) <= tolerance)

    def _candidate_from_photo(
        self,
        *,
        request: SupplementRequest,
        photo: dict,
        query_used: str,
        location_name: str,
    ) -> SupplementCandidate:
        width = int(photo.get("width") or 0)
        height = int(photo.get("height") or 0)
        aspect = self._aspect_ratio(width, height)
        is_16_9 = self._is_16_9(width, height, request.video_aspect_ratio_tolerance)
        title = photo.get("alt") or photo.get("url") or f"Pexels Photo {photo.get('id', '')}"
        location_match, required_terms, present_terms = location_match_for_text(
            f"{title} {request.visual_requirement} {query_used}",
            location_name,
            broadened=request.allow_broader_search and location_name.casefold() not in query_used.casefold(),
        )
        src = photo.get("src", {}) or {}
        download_url = src.get("original") or src.get("large2x") or src.get("large") or ""
        return SupplementCandidate(
            candidate_id=f"cand_{uuid.uuid4().hex[:8]}",
            supplement_request_id=request.supplement_request_id,
            provider=self.provider,
            provider_asset_id=str(photo.get("id", "")),
            title=title[:120],
            description=request.visual_requirement,
            preview_url=src.get("medium", "") or src.get("small", ""),
            download_url=download_url,
            creator=photo.get("photographer", ""),
            creator_url=photo.get("photographer_url", ""),
            license="Pexels License",
            license_url="https://www.pexels.com/license/",
            rights_status=RIGHTS_STATUS_APPROVED,
            source_page_url=photo.get("url", ""),
            media_type="image",
            width=width,
            height=height,
            duration_sec=0.0,
            requires_purchase=False,
            requires_user_approval=not is_16_9 or location_match in {"broadened", "missing"},
            match_score=0.7 if location_match != "missing" else 0.35,
            match_reason="Pexels Photo API Treffer",
            status=CANDIDATE_STATUS_FOUND,
            provider_status=PROVIDER_STATUS_READY,
            is_mock=False,
            download_enabled=bool(download_url),
            query_used=query_used,
            folder_name=request.folder_name,
            location_name=location_name,
            location_terms_required=required_terms,
            location_terms_present=present_terms,
            location_match=location_match,
            aspect_ratio=aspect,
            aspect_ratio_policy=request.photo_aspect_policy,
            is_16_9=is_16_9,
            approved_for_cut_plan=location_match != "missing",
            supplement_validation_status="NEEDS_USER_REVIEW" if location_match == "missing" else "WEAK_PASS",
            supplement_validation_score=0.7 if location_match != "missing" else 0.35,
        )

    def _select_video_file(
        self,
        files: list[dict],
        *,
        timeline_width: int = 0,
        tolerance: float = 0.03,
    ) -> dict | None:
        downloadable = [
            entry
            for entry in files
            if entry.get("link")
            and self._is_16_9(
                int(entry.get("width") or 0),
                int(entry.get("height") or 0),
                tolerance,
            )
        ]
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
        width = int(video.get("width") or 0)
        height = int(video.get("height") or 0)
        video_aspect = self._aspect_ratio(width, height)
        video_is_16_9 = self._is_16_9(width, height, request.video_aspect_ratio_tolerance)
        selected_file = self._select_video_file(
            files,
            tolerance=request.video_aspect_ratio_tolerance,
        )
        if not video_is_16_9:
            status = CANDIDATE_STATUS_REJECTED_ASPECT_RATIO
        elif not selected_file:
            status = CANDIDATE_STATUS_NOT_DOWNLOADABLE_16_9
        else:
            status = CANDIDATE_STATUS_FOUND
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
            width=width,
            height=height,
            duration_sec=float(video.get("duration") or request.duration_needed_sec),
            requires_purchase=False,
            requires_user_approval=location_match in {"broadened", "missing"},
            match_score=0.75 if location_match != "missing" else 0.35,
            match_reason="Pexels API Treffer",
            status=status,
            provider_status=PROVIDER_STATUS_READY,
            is_mock=False,
            download_enabled=bool(selected_file and video_is_16_9),
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
            selected_video_file_aspect_ratio=self._aspect_ratio(
                int((selected_file or {}).get("width") or 0),
                int((selected_file or {}).get("height") or 0),
            ),
            aspect_ratio=video_aspect,
            aspect_ratio_policy="video_16_9",
            is_16_9=video_is_16_9,
            approved_for_cut_plan=video_is_16_9 and location_match != "missing",
            supplement_validation_status="PASS" if video_is_16_9 and location_match != "missing" else "NEEDS_USER_REVIEW",
            supplement_validation_score=0.8 if video_is_16_9 and location_match != "missing" else 0.4,
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
        parsed_suffix = Path(urllib.parse.urlparse(candidate.download_url).path).suffix.lower()
        extension = parsed_suffix or (".jpg" if candidate.media_type == "image" else ".mp4")
        filename = (
            f"{folder_slug}_{candidate.supplement_request_id}_pexels_{candidate.provider_asset_id}{extension}"
        )
        local_path = destination_folder / filename
        try:
            with urllib.request.urlopen(candidate.download_url, timeout=60) as response:
                status = int(getattr(response, "status", 200) or 200)
                if status != 200:
                    raise RuntimeError(f"HTTP Status {status}")
                content_type = response.headers.get("Content-Type", "")
                is_octet_mp4 = content_type.startswith("application/octet-stream") and candidate.download_url.lower().endswith(".mp4")
                valid_image = candidate.media_type == "image" and content_type.startswith("image/")
                valid_video = candidate.media_type == "video" and (content_type.startswith("video/") or is_octet_mp4)
                if not (valid_video or valid_image):
                    raise ValueError(f"Unerwarteter Content-Type: {content_type or 'unbekannt'}")
                data = response.read()
        except (urllib.error.URLError, OSError) as exc:
            raise RuntimeError(f"Pexels-Download fehlgeschlagen: {exc}") from exc
        if len(data) < MIN_DOWNLOAD_BYTES:
            raise RuntimeError("Pexels-Download zu klein — vermutlich kein gültiges Asset.")
        local_path.write_bytes(data)
        duration = probe_duration_seconds(local_path) if candidate.media_type == "video" else 0.0
        if candidate.media_type == "video" and duration is None:
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
            media_type=candidate.media_type,
            aspect_ratio=candidate.aspect_ratio,
            aspect_ratio_policy=candidate.aspect_ratio_policy,
            is_16_9=candidate.is_16_9,
            supplement_validation_status=candidate.supplement_validation_status,
            supplement_validation_score=candidate.supplement_validation_score,
            approved_for_cut_plan=candidate.approved_for_cut_plan,
            downloaded_at=datetime.now(timezone.utc),
            original_filename=filename,
            local_path=str(local_path),
            rights_status=RIGHTS_STATUS_APPROVED,
            requires_attribution=True,
            approval_status="APPROVED",
        )
        return SupplementAsset(local_path=local_path, sidecar=sidecar)
