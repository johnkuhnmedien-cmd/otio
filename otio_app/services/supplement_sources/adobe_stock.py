"""Adobe Stock — Suche (Phase 12.1/12.2a); Lizenzierung/Download folgen erst
in einer späteren Phase (12.4). Bis dahin liefert `acquire()` weiterhin einen
klaren Fehler statt eine Wasserzeichen-Vorschau fälschlich als finales Asset
herunterzuladen.

Nutzerentscheidung (Juli 2026): generative-AI-Assets werden bei Adobe Stock
IMMER ausgeschlossen — sowohl über den Suchfilter
(`search_parameters[filters][gentech]=false`) als auch als zusätzliches
Code-seitiges Sicherheitsnetz (`is_gentech`-Prüfung pro Treffer), falls Adobe
trotz Filter einen generativen Treffer zurückgeben sollte."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from otio_app.analysis_models import SupplementCandidate, SupplementRequest
from otio_app.defaults import (
    ADOBE_STOCK_DEFAULT_PRODUCT_NAME,
    ADOBE_STOCK_MEDIA_TYPE_ID_PHOTO,
    ADOBE_STOCK_MEDIA_TYPE_ID_VIDEO,
    ADOBE_STOCK_REJECTED_REASON_GENTECH,
    ADOBE_STOCK_SEARCH_ENDPOINT,
    CANDIDATE_STATUS_FOUND,
    PROVIDER_STATUS_CONFIG_MISSING,
    PROVIDER_STATUS_READY,
    RIGHTS_STATUS_NEEDS_LICENSE_REVIEW,
    SUPPLEMENT_SOURCE_ADOBE,
)
from otio_app.services.api_keys import get_api_key
from otio_app.services.supplement_search import (
    base_location_for_request,
    llm_generated_query_variants,
    location_match_for_text,
    preferred_search_query,
)
from otio_app.services.supplement_sources.base import ProviderReadiness, SupplementAsset, SupplementSourceAdapter

# Pro Szene/Request soll die UI höchstens diese Anzahl an Kandidaten zur
# Auswahl anbieten, falls request.max_candidates nicht explizit gesetzt ist
# (der Cut-Plan-Workflow überschreibt dies auf 5, siehe
# CUT_PLAN_SUPPLEMENT_MAX_CANDIDATES) — analog zu MAX_CANDIDATES_PER_REQUEST
# in supplement_sources/pexels.py.
MAX_CANDIDATES_PER_REQUEST = 3
ADOBE_STOCK_SEARCH_LIMIT = 15
# Adobe erlaubt in EINER Suche gleichzeitig nach Foto UND Video zu filtern
# (anders als Pexels mit zwei getrennten Endpunkten) — media_type_id in der
# Response unterscheidet danach, was ein Treffer tatsächlich ist.
_ADOBE_RESULT_COLUMNS = (
    "id",
    "title",
    "description",
    "creator_name",
    "width",
    "height",
    "duration",
    "thumbnail_1000_url",
    "details_url",
    "content_type",
    "media_type_id",
    "comps",
    "licenses",
    "video_preview_url",
    "is_gentech",
)
ADOBE_STOCK_REQUEST_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class AdobeStockAdapter(SupplementSourceAdapter):
    provider = SUPPLEMENT_SOURCE_ADOBE
    last_debug_report: dict = {}

    def readiness(self) -> ProviderReadiness:
        if get_api_key("ADOBE_STOCK_API_KEY"):
            return ProviderReadiness(
                provider=self.provider,
                status=PROVIDER_STATUS_READY,
                message=(
                    "Adobe Stock API-Key vorhanden — Suche ist aktiv. Automatische "
                    "Lizenzierung/Download folgen erst in einer späteren Phase."
                ),
                search_enabled=True,
                acquire_enabled=False,
            )
        return ProviderReadiness(
            provider=self.provider,
            status=PROVIDER_STATUS_CONFIG_MISSING,
            message="ADOBE_STOCK_API_KEY fehlt.",
            search_enabled=True,
            acquire_enabled=False,
        )

    def _product_name(self) -> str:
        return get_api_key("ADOBE_STOCK_PRODUCT_NAME") or ADOBE_STOCK_DEFAULT_PRODUCT_NAME

    def _headers(self, api_key: str) -> dict:
        headers = {
            "x-api-key": api_key,
            "x-product": self._product_name(),
            "Accept": "application/json",
            "User-Agent": ADOBE_STOCK_REQUEST_USER_AGENT,
        }
        access_token = get_api_key("ADOBE_STOCK_ACCESS_TOKEN")
        if access_token:
            # Optional für die reine Suche — nur mit gültigem Token liefert
            # Adobe zusätzlich den Lizenzstatus (is_licensed) mit.
            headers["Authorization"] = f"Bearer {access_token}"
        return headers

    def search(self, request: SupplementRequest) -> list[SupplementCandidate]:
        api_key = get_api_key("ADOBE_STOCK_API_KEY")
        if not api_key:
            return []
        return self._search_api(request, api_key)

    def _query_variants(self, request: SupplementRequest) -> list[str]:
        """Nutzt bevorzugt bereits vorbereitete Queries (LLM-Suchqueries aus
        der Cut-Plan-Query-Generierung bzw. der Phase-9-supplement_search_hint
        — beide landen in request.llm_generated_queries, siehe
        supplement_search.llm_generated_query_variants), sonst EINE
        deterministische Ersatzquery. Bewusst KEINE eigene, hartkodierte
        Fallback-Liste analog zu build_pexels_query_variants — jene Liste
        enthält historisch canyon-spezifische Literale, die für Adobe nicht
        sinnvoll wären."""
        llm_queries = llm_generated_query_variants(request)
        if llm_queries:
            return llm_queries
        location = base_location_for_request(request)
        fallback = preferred_search_query(request).strip()
        return [fallback] if fallback else [location]

    def _build_params(self, query: str) -> dict:
        return {
            "locale": "en_US",
            "search_parameters[words]": query,
            "search_parameters[limit]": ADOBE_STOCK_SEARCH_LIMIT,
            "search_parameters[order]": "relevance",
            "search_parameters[filters][orientation]": "horizontal",
            "search_parameters[filters][content_type:video]": 1,
            "search_parameters[filters][content_type:photo]": 1,
            # Nutzervorgabe: generative-AI-Assets ausschließen.
            "search_parameters[filters][gentech]": "false",
            "result_columns[]": list(_ADOBE_RESULT_COLUMNS),
        }

    def _request_json(self, params: dict, api_key: str) -> tuple[int, dict]:
        url = f"{ADOBE_STOCK_SEARCH_ENDPOINT}?{urllib.parse.urlencode(params, doseq=True)}"
        req = urllib.request.Request(url, headers=self._headers(api_key))
        with urllib.request.urlopen(req, timeout=20) as response:
            status = int(getattr(response, "status", 200) or 200)
            return status, json.loads(response.read().decode("utf-8"))

    def _request_json_safe(self, params: dict, api_key: str, *, query: str) -> tuple[int, dict] | None:
        """Führt die Adobe-Anfrage aus; Fehler werden im Debug-Report
        festgehalten statt die ganze Suche stillschweigend abzubrechen."""
        try:
            return self._request_json(params, api_key)
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read().decode("utf-8", errors="replace")[:300]
            except Exception:
                body = ""
            message = body or exc.reason or str(exc)
            self.last_debug_report["http_status_by_query"][query] = exc.code
            self.last_debug_report["errors"].append(
                {"query": query, "status": exc.code, "message": message}
            )
            return None
        except (urllib.error.URLError, json.JSONDecodeError, OSError) as exc:
            self.last_debug_report["http_status_by_query"][query] = 0
            self.last_debug_report["errors"].append({"query": query, "status": 0, "message": str(exc)})
            return None

    def _empty_debug(self, request: SupplementRequest) -> dict:
        return {
            "request_id": request.supplement_request_id,
            "provider": "adobe_stock",
            "endpoint": ADOBE_STOCK_SEARCH_ENDPOINT,
            "queries_attempted": [],
            "http_status_by_query": {},
            "raw_result_count": 0,
            "mapped_candidate_count": 0,
            "gentech_rejected_count": 0,
            "skipped_unsupported_media_type_count": 0,
            "final_candidate_count": 0,
            "rejected_reasons": [],
            "errors": [],
        }

    def _finalize_debug(self, candidates: list[SupplementCandidate]) -> None:
        self.last_debug_report["final_candidate_count"] = len(candidates)

    def _search_api(self, request: SupplementRequest, api_key: str) -> list[SupplementCandidate]:
        self.last_debug_report = self._empty_debug(request)
        location = base_location_for_request(request)
        candidates: list[SupplementCandidate] = []

        for query in self._query_variants(request):
            self.last_debug_report["queries_attempted"].append(query)
            params = self._build_params(query)
            result = self._request_json_safe(params, api_key, query=query)
            if result is None:
                continue
            status, payload = result
            self.last_debug_report["http_status_by_query"][query] = status
            files = payload.get("files", []) or []
            self.last_debug_report["raw_result_count"] += len(files)
            for file_entry in files:
                candidate = self._candidate_from_file(
                    request=request, file_entry=file_entry, query_used=query, location_name=location
                )
                if candidate is None:
                    continue
                candidates.append(candidate)
                self.last_debug_report["mapped_candidate_count"] += 1
            if candidates:
                break

        self._finalize_debug(candidates)
        max_count = request.max_candidates if request.max_candidates > 0 else MAX_CANDIDATES_PER_REQUEST
        return candidates[:max_count]

    def _select_preview_url(self, *, media_type: str, comps: dict, file_entry: dict) -> tuple[str, bool, bool]:
        """Gibt (preview_url, has_hd, has_4k) zurück. Bevorzugt bei Videos
        Video_4K, dann Video_HD, dann video_preview_url; bei Fotos
        comps.Standard, dann thumbnail_1000_url. Alle URLs sind zu diesem
        Zeitpunkt Wasserzeichen-Vorschauen — keine lizenzierten Originale
        (siehe Moduldocstring/Phase 12.3/12.4)."""
        if media_type == "video":
            video_hd = comps.get("Video_HD") or {}
            video_4k = comps.get("Video_4K") or {}
            has_hd = bool(video_hd.get("url"))
            has_4k = bool(video_4k.get("url"))
            preview = (
                video_4k.get("url")
                or video_hd.get("url")
                or str(file_entry.get("video_preview_url") or "")
            )
            return str(preview or ""), has_hd, has_4k

        standard = comps.get("Standard") or {}
        preview = standard.get("url") or str(file_entry.get("thumbnail_1000_url") or "")
        return str(preview or ""), False, False

    def _candidate_from_file(
        self,
        *,
        request: SupplementRequest,
        file_entry: dict,
        query_used: str,
        location_name: str,
    ) -> SupplementCandidate | None:
        is_gentech = bool(file_entry.get("is_gentech", False))
        if is_gentech:
            # Sicherheitsnetz zusätzlich zum Suchfilter (gentech=false) —
            # Nutzervorgabe: niemals generative-AI-Assets von Adobe Stock.
            self.last_debug_report["gentech_rejected_count"] += 1
            self.last_debug_report["rejected_reasons"].append(
                {
                    "provider_asset_id": str(file_entry.get("id", "")),
                    "reason": ADOBE_STOCK_REJECTED_REASON_GENTECH,
                }
            )
            return None

        media_type_id = int(file_entry.get("media_type_id") or 0)
        if media_type_id == ADOBE_STOCK_MEDIA_TYPE_ID_VIDEO:
            media_type = "video"
        elif media_type_id == ADOBE_STOCK_MEDIA_TYPE_ID_PHOTO:
            media_type = "image"
        else:
            # Illustration/Vektor/3D/Template/Premium/Audio — für die
            # Supplement-Suche (Video/Foto) nicht relevant.
            self.last_debug_report["skipped_unsupported_media_type_count"] += 1
            return None

        width = int(file_entry.get("width") or 0)
        height = int(file_entry.get("height") or 0)
        comps = file_entry.get("comps") or {}
        preview_url, has_hd, has_4k = self._select_preview_url(
            media_type=media_type, comps=comps, file_entry=file_entry
        )
        title = str(file_entry.get("title") or f"Adobe Stock {file_entry.get('id', '')}")
        # Adobe liefert Video-Dauer in Millisekunden.
        duration_ms = file_entry.get("duration")
        duration_sec = (float(duration_ms) / 1000.0) if media_type == "video" and duration_ms else 0.0

        location_match, required_terms, present_terms = location_match_for_text(
            f"{title} {request.visual_requirement} {query_used}",
            location_name,
            broadened=request.allow_broader_search and location_name.casefold() not in query_used.casefold(),
        )

        return SupplementCandidate(
            candidate_id=f"cand_{uuid.uuid4().hex[:8]}",
            supplement_request_id=request.supplement_request_id,
            provider=self.provider,
            provider_asset_id=str(file_entry.get("id", "")),
            title=title[:120],
            description=str(file_entry.get("description") or request.visual_requirement),
            preview_url=preview_url,
            download_url=preview_url,
            creator=str(file_entry.get("creator_name") or ""),
            license="",
            rights_status=RIGHTS_STATUS_NEEDS_LICENSE_REVIEW,
            source_page_url=str(file_entry.get("details_url") or ""),
            media_type=media_type,
            width=(3840 if has_4k else (1920 if has_hd else width)) if media_type == "video" else width,
            height=(2160 if has_4k else (1080 if has_hd else height)) if media_type == "video" else height,
            duration_sec=duration_sec,
            requires_purchase=True,
            requires_user_approval=True,
            match_score=0.75 if location_match != "missing" else 0.35,
            match_reason="Adobe Stock API Treffer",
            status=CANDIDATE_STATUS_FOUND,
            provider_status=PROVIDER_STATUS_READY,
            is_mock=False,
            # download_enabled bezieht sich hier bewusst nur auf "ist der
            # Kandidat selbst technisch verwendbar" (echter Treffer, keine
            # generative-AI-Ablehnung) — NICHT auf "automatische Lizenzierung
            # ist bereits produktiv". Letzteres prüft acquire() unten
            # weiterhin explizit und liefert bis Phase 12.4 einen klaren,
            # phasengerechten Fehler. Würde man hier False setzen, würde die
            # Produktions-Guard-Logik (acquire_supplement_candidate) fälschlich
            # "Mock-/Demo-Kandidat" melden, obwohl es ein echter Adobe-Treffer
            # ist, der lediglich noch nicht automatisch lizenzierbar ist.
            download_enabled=True,
            query_used=query_used,
            folder_name=request.folder_name,
            location_name=location_name,
            location_terms_required=required_terms,
            location_terms_present=present_terms,
            location_match=location_match,
            aspect_ratio=round(width / height, 6) if width and height else 0.0,
            aspect_ratio_policy="video_16_9" if media_type == "video" else request.photo_aspect_policy,
            is_16_9=False,
            approved_for_cut_plan=False,
            supplement_validation_status="NEEDS_USER_REVIEW",
            supplement_validation_score=0.7 if location_match != "missing" else 0.35,
            adobe_media_type_id=media_type_id,
            adobe_is_gentech=is_gentech,
            adobe_comps=comps,
            adobe_content_type=str(file_entry.get("content_type") or ""),
        )

    def acquire(
        self,
        candidate: SupplementCandidate,
        destination_folder: Path,
    ) -> SupplementAsset:
        raise PermissionError(
            "Adobe Stock: automatische Lizenzierung/Download sind noch nicht produktiv "
            "angebunden (folgt in einer späteren Phase)."
        )
