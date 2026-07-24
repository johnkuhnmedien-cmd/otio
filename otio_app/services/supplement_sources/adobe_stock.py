"""Adobe Stock — Suche (Phase 12.1/12.2a) + Lizenzierung/Download (Phase
12.3).

Nutzerentscheidung (Juli 2026): generative-AI-Assets werden bei Adobe Stock
IMMER ausgeschlossen — sowohl über den Suchfilter
(`search_parameters[filters][gentech]=false`) als auch als zusätzliches
Code-seitiges Sicherheitsnetz (`is_gentech`-Prüfung pro Treffer), falls Adobe
trotz Filter einen generativen Treffer zurückgeben sollte.

Nutzerentscheidung (Juli 2026, Phase 12.3): `acquire()` lizenziert und lädt
SOFORT herunter — es wird NICHT zuerst eine Wasserzeichen-Vorschau geprüft
und erst danach lizenziert. Grund: unlimited Adobe-Stock-Plan des Nutzers,
eine versehentlich falsch lizenzierte Datei verursacht keine zusätzlichen
Kosten. Die Gemini-Qualitätsprüfung läuft danach auf dem bereits lizenziert
heruntergeladenen Original (siehe cut_plan_supplement_auto_resolve_service.py
— unverändert, da bereits provider-unabhängig)."""

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
    ADOBE_STOCK_CONTENT_INFO_ENDPOINT,
    ADOBE_STOCK_DEFAULT_PRODUCT_NAME,
    ADOBE_STOCK_FILES_ENDPOINT,
    ADOBE_STOCK_LICENSE_ENDPOINT,
    ADOBE_STOCK_LICENSE_HISTORY_ENDPOINT,
    ADOBE_STOCK_MEMBER_PROFILE_ENDPOINT,
    ADOBE_STOCK_LICENSE_TYPE_STANDARD,
    ADOBE_STOCK_LICENSE_TYPE_VIDEO_4K,
    ADOBE_STOCK_LICENSE_TYPE_VIDEO_HD,
    ADOBE_STOCK_MEDIA_TYPE_ID_PHOTO,
    ADOBE_STOCK_MEDIA_TYPE_ID_VIDEO,
    ADOBE_STOCK_MIN_DOWNLOAD_BYTES,
    ADOBE_STOCK_REJECTED_REASON_GENTECH,
    ADOBE_STOCK_SEARCH_ENDPOINT,
    ADOBE_STOCK_VIDEO_4K_MAX_BYTES,
    CANDIDATE_STATUS_FOUND,
    CANDIDATE_STATUS_REJECTED_ASPECT_RATIO,
    PROVIDER_STATUS_CONFIG_MISSING,
    PROVIDER_STATUS_READY,
    RIGHTS_STATUS_APPROVED,
    RIGHTS_STATUS_NEEDS_LICENSE_REVIEW,
    SUPPLEMENT_SOURCE_ADOBE,
)
from otio_app.services.adobe_stock_oauth import get_adobe_access_token
from otio_app.services.api_keys import get_api_key
from otio_app.services.media_utils import probe_duration_seconds
from otio_app.services.supplement_search import (
    base_location_for_request,
    llm_generated_query_variants,
    location_match_for_text,
    preferred_search_query,
)
from otio_app.services.supplement_sources.base import ProviderReadiness, SupplementAsset, SupplementSourceAdapter


class AdobeAssetTooLargeError(Exception):
    """Intern: signalisiert, dass ein Video-Download die Größengrenze für
    die aktuell lizenzierte Qualität (Content-Length-Header ODER während
    des Streamings gemessen) überschritten hat — löst in acquire() den
    Wechsel auf die nächstkleinere Lizenzvariante (Video_HD) aus."""


class AdobeContentUnavailableError(Exception):
    """Asset ist bei Adobe nicht mehr verfügbar (HTTP 404 / code 300)."""


class AdobeVideoEntitlementError(Exception):
    """OAuth-/Stock-Konto hat keine Video-Lizenzrechte (nur Bild-Unlimited o. ä.)."""


VIDEO_ENTITLEMENT_HINT = (
    "Hinweis: Dein Browser-Unlimited funktioniert — die Stock-API meldet "
    "für dieses OAuth-Token bei Video aber nur cct_pro_unlimited_images "
    "(Video-quota=0) und Content/License antwortet mit state=cancelled + Comp. "
    "Das ist ein API-/Token-Problem, kein fehlendes Abo. "
    "Prüfen: OAuth mit derselben Adobe-ID wie stock.adobe.com? "
    "stock_id im Diagnose-Panel vergleichen. "
    "Im Browser frisch lizenzierte Clips erneut importieren "
    "(LicenseHistory/Content/Info). "
    "Bei CC Pro/Plus ggf. API-Freigabe: stockapis@adobe.com."
)


def is_full_adobe_download_url(url: str) -> bool:
    """Echte Lizenz-Download-URL — keine Wasserzeichen-/Comp-URL."""
    text = (url or "").strip()
    if not text:
        return False
    if "/Watermarked/" in text:
        return False
    return (
        "/Rest/Libraries/Download/" in text
        or "/Download/DownloadFileDirectly/" in text
        or "/DownloadFileDirectly/" in text
    )

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


def _adobe_media_type_filter(required_asset_type: str) -> str:
    """Phase 12.7: bewusst NUR die EXAKTEN Werte "video"/"image" schränken
    die Adobe-Suche auf einen Medientyp ein — jeder andere Wert (u. a. die
    Produktions-Pipeline-Standardvorgabe "video_preferred", der Cut-Plan-
    Standard "any" für die manuelle Suche, sowie jeder unbekannte/leere
    Wert) ergibt "any" und behält das bisherige Verhalten (Video UND Foto
    gemeinsam) unverändert bei. Das schützt insbesondere die produktions-
    seitige Supplement-Pipeline (supplement_pipeline.py), die
    required_asset_type="video_preferred" nutzt, aber von Adobe bislang
    IMMER beide Medientypen gemischt zurückbekommt — nur der neue Cut-Plan-
    Auto-Resolver (siehe cut_plan_supplement_auto_resolve_service.py)
    übergibt hier je Suchstufe explizit "video" oder "image"."""
    normalized = (required_asset_type or "").strip().lower()
    if normalized == "video":
        return "video"
    if normalized == "image":
        return "photo"
    return "any"


def _compact_json(value: object, *, max_len: int = 700) -> str:
    """Serialisiert Diagnoseobjekte lesbar und begrenzt die Länge für Trace/UI."""
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        text = str(value)
    if len(text) <= max_len:
        return text
    return f"{text[: max_len - 3]}..."


def _license_content_entry(payload: dict, content_id: str) -> dict:
    contents = payload.get("contents") or {}
    return contents.get(str(content_id)) or (next(iter(contents.values()), {}) if contents else {})


class AdobeStockAdapter(SupplementSourceAdapter):
    provider = SUPPLEMENT_SOURCE_ADOBE
    last_debug_report: dict = {}
    last_license_diagnostic: dict = {}

    def readiness(self) -> ProviderReadiness:
        if not get_api_key("ADOBE_STOCK_API_KEY"):
            return ProviderReadiness(
                provider=self.provider,
                status=PROVIDER_STATUS_CONFIG_MISSING,
                message="ADOBE_STOCK_API_KEY fehlt.",
                search_enabled=True,
                acquire_enabled=False,
            )
        if not get_adobe_access_token():
            return ProviderReadiness(
                provider=self.provider,
                status=PROVIDER_STATUS_READY,
                message=(
                    "Adobe Stock API-Key vorhanden — Suche ist aktiv. "
                    "Für Lizenzierung/Download bitte OAuth-Login (Adobe Stock Import / "
                    "API-Schlüssel) oder manuelles ADOBE_STOCK_ACCESS_TOKEN."
                ),
                search_enabled=True,
                acquire_enabled=False,
            )
        return ProviderReadiness(
            provider=self.provider,
            status=PROVIDER_STATUS_READY,
            message="Adobe Stock vollständig konfiguriert (Suche + Lizenzierung/Download).",
            search_enabled=True,
            acquire_enabled=True,
        )

    @staticmethod
    def entitlement_lacks_video(entitlement_summary: dict) -> bool:
        """True, wenn Member/Profile bzw. License-Antwort nur Bild-Unlimited zeigt."""
        entitlement = entitlement_summary.get("available_entitlement") or entitlement_summary
        if not isinstance(entitlement, dict):
            return False
        full = entitlement.get("full_entitlement_quota") or {}
        if not isinstance(full, dict) or not full:
            return False
        keys = {str(k).lower() for k in full}
        has_image_unlimited = any("image" in k and "unlimited" in k for k in keys)
        has_video_key = any("video" in k for k in keys)
        quota = entitlement.get("quota")
        try:
            quota_i = int(quota) if quota is not None else None
        except (TypeError, ValueError):
            quota_i = None
        return has_image_unlimited and not has_video_key and quota_i == 0

    def probe_video_entitlement(
        self,
        api_key: str,
        access_token: str,
        *,
        content_id: str | None = None,
    ) -> dict:
        """Member/Profile mit Video_HD — zeigt, ob das Konto Videos lizenzieren kann."""
        params: dict = {"license": ADOBE_STOCK_LICENSE_TYPE_VIDEO_HD, "locale": "en_US"}
        if content_id:
            params["content_id"] = str(content_id)
        payload = self._request_licensing_json_safe(
            ADOBE_STOCK_MEMBER_PROFILE_ENDPOINT,
            params,
            api_key,
            access_token,
        )
        summary = self._summarize_member_profile(payload)
        summary["lacks_video"] = self.entitlement_lacks_video(summary)
        return summary

    def _product_name(self) -> str:
        return get_api_key("ADOBE_STOCK_PRODUCT_NAME") or ADOBE_STOCK_DEFAULT_PRODUCT_NAME

    def _headers(self, api_key: str) -> dict:
        headers = {
            "x-api-key": api_key,
            "x-product": self._product_name(),
            "Accept": "application/json",
            "User-Agent": ADOBE_STOCK_REQUEST_USER_AGENT,
        }
        access_token = get_adobe_access_token()
        if access_token:
            # Optional für die reine Suche — nur mit gültigem Token liefert
            # Adobe zusätzlich den Lizenzstatus (is_licensed) mit.
            headers["Authorization"] = f"Bearer {access_token}"
        return headers

    def _download_headers(self, api_key: str) -> dict:
        """Header für Libraries/Download — bewusst OHNE Accept: application/json
        und OHNE Authorization: Bearer. Laut Adobe Stock Licensing API wird der
        Access-Token für Downloads als URL-Parameter ?token=… erwartet; die
        JSON-Such-/Lizenz-Header (_headers) können auf dem Binär-Download zu
        HTTP 400 führen, obwohl Content/License bereits erfolgreich war."""
        return {
            "x-api-key": api_key,
            "x-product": self._product_name(),
            "User-Agent": ADOBE_STOCK_REQUEST_USER_AGENT,
        }

    @staticmethod
    def _prepare_download_url(url: str, access_token: str, *, size: int | None = None) -> str:
        """Hängt ?token=… an die von Content/License gelieferte Download-URL
        an, falls noch nicht vorhanden — Adobe Stock Libraries/Download.

        Für Videos wird zusätzlich die gewünschte Rendition explizit gesetzt
        (2160 für Video_4K, 1080 für Video_HD). Ohne size kann Adobe je nach
        Antwort/Entitlement eine Comp-/Preview-Datei liefern, die zwar
        downloadbar ist, aber weiterhin ein Wasserzeichen trägt."""
        parsed = urllib.parse.urlparse(url)
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        if not (query.get("token") and query["token"][0]):
            query["token"] = [access_token]
        if size is not None:
            query["size"] = [str(size)]
        new_query = urllib.parse.urlencode(query, doseq=True)
        return urllib.parse.urlunparse(parsed._replace(query=new_query))

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

    def _build_params(self, query: str, media_type_filter: str) -> dict:
        params = {
            "locale": "en_US",
            "search_parameters[words]": query,
            "search_parameters[limit]": ADOBE_STOCK_SEARCH_LIMIT,
            "search_parameters[order]": "relevance",
            "search_parameters[filters][orientation]": "horizontal",
            # Nutzervorgabe: generative-AI-Assets ausschließen.
            "search_parameters[filters][gentech]": "false",
            "result_columns[]": list(_ADOBE_RESULT_COLUMNS),
        }
        # Phase 12.7: media_type_filter=="any" (Standard für alle bisherigen
        # Aufrufer) fragt wie bisher BEIDE Medientypen gleichzeitig an —
        # "video"/"image" (nur vom Cut-Plan-Auto-Resolver verwendet)
        # schränkt die Adobe-Suche selbst schon auf einen Typ ein, statt nur
        # nachträglich zu filtern (spart API-seitig irrelevante Treffer).
        if media_type_filter != "photo":
            params["search_parameters[filters][content_type:video]"] = 1
        if media_type_filter != "video":
            params["search_parameters[filters][content_type:photo]"] = 1
        return params

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
            # Phase 12.7: "any" (Standard) fragt Video+Foto gemeinsam an wie
            # bisher — "video"/"image" (nur Cut-Plan-Auto-Resolver) schränkt
            # die Adobe-Anfrage selbst auf einen Medientyp ein.
            "media_type_filter": _adobe_media_type_filter(request.required_asset_type),
            "queries_attempted": [],
            "http_status_by_query": {},
            "raw_result_count": 0,
            "mapped_candidate_count": 0,
            "gentech_rejected_count": 0,
            "skipped_unsupported_media_type_count": 0,
            "skipped_wrong_media_type_count": 0,
            "final_candidate_count": 0,
            "rejected_reasons": [],
            "errors": [],
        }

    def _finalize_debug(self, candidates: list[SupplementCandidate]) -> None:
        self.last_debug_report["final_candidate_count"] = len(candidates)

    def _search_api(self, request: SupplementRequest, api_key: str) -> list[SupplementCandidate]:
        self.last_debug_report = self._empty_debug(request)
        media_type_filter = self.last_debug_report["media_type_filter"]
        location = base_location_for_request(request)
        candidates: list[SupplementCandidate] = []

        for query in self._query_variants(request):
            self.last_debug_report["queries_attempted"].append(query)
            params = self._build_params(query, media_type_filter)
            result = self._request_json_safe(params, api_key, query=query)
            if result is None:
                continue
            status, payload = result
            self.last_debug_report["http_status_by_query"][query] = status
            files = payload.get("files", []) or []
            self.last_debug_report["raw_result_count"] += len(files)
            for file_entry in files:
                candidate = self._candidate_from_file(
                    request=request,
                    file_entry=file_entry,
                    query_used=query,
                    location_name=location,
                    media_type_filter=media_type_filter,
                )
                if candidate is None:
                    continue
                candidates.append(candidate)
                self.last_debug_report["mapped_candidate_count"] += 1
            # Erst abbrechen, wenn mindestens ein tatsächlich verwendbarer
            # Kandidat gefunden wurde — ein Video mit falschem Seitenverhältnis
            # (download_enabled=False, Phase 12.2b) allein reicht nicht, sonst
            # würden bessere Treffer aus einer späteren Query nie versucht.
            if any(candidate.download_enabled for candidate in candidates):
                break

        self._finalize_debug(candidates)
        max_count = request.max_candidates if request.max_candidates > 0 else MAX_CANDIDATES_PER_REQUEST
        return candidates[:max_count]

    def _select_preview(self, *, media_type: str, comps: dict, file_entry: dict) -> tuple[str, int, int]:
        """Gibt (preview_url, width, height) zurück.

        Video: `comps.Video_4K`/`comps.Video_HD` liefern hilfreiche Maße für
        die spätere Lizenzvariante, die URL selbst ist aber nur Preview/Comp
        und darf niemals als finaler Download verwendet werden. Der echte
        Download kommt ausschließlich aus Content/License.purchase_details.url.

        Foto: `comps.Standard` ist dagegen NUR eine kleine Wasserzeichen-
        Vorschau (z. B. 1000x600) — für Breite/Höhe wird deshalb IMMER das
        native Basismaß aus der Files-API (file_entry.width/height, die
        tatsächliche Auflösung des lizenzierbaren Originals) verwendet; die
        comps-URL wird nur als Vorschaubild-Link genutzt."""
        base_width = int(file_entry.get("width") or 0)
        base_height = int(file_entry.get("height") or 0)
        if media_type == "video":
            video_4k = comps.get("Video_4K") or {}
            video_hd = comps.get("Video_HD") or {}
            for entry in (video_4k, video_hd):
                if entry.get("url"):
                    return (
                        str(entry.get("url")),
                        int(entry.get("width") or base_width),
                        int(entry.get("height") or base_height),
                    )
            return str(file_entry.get("video_preview_url") or ""), base_width, base_height

        standard = comps.get("Standard") or {}
        preview_url = str(standard.get("url") or file_entry.get("thumbnail_1000_url") or "")
        return preview_url, base_width, base_height

    @staticmethod
    def _is_16_9(width: int, height: int, tolerance: float) -> bool:
        if not width or not height:
            return False
        return abs((width / height) - (16 / 9)) <= tolerance

    def _candidate_from_file(
        self,
        *,
        request: SupplementRequest,
        file_entry: dict,
        query_used: str,
        location_name: str,
        media_type_filter: str = "any",
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

        # Phase 12.7: defensives Sicherheitsnetz zusätzlich zum Suchfilter in
        # _build_params — falls Adobe trotz content_type-Filter (oder bei
        # einem unveränderten Aufrufer mit media_type_filter="any", der
        # ohnehin nie hier ausschlägt) den "falschen" Medientyp zurückgibt,
        # wird der Treffer verworfen statt in eine falsche Suchstufe
        # (z. B. den "nur Video"-Schritt des Cut-Plan-Auto-Resolvers) zu
        # rutschen.
        if (media_type_filter == "video" and media_type != "video") or (
            media_type_filter == "photo" and media_type != "image"
        ):
            self.last_debug_report["skipped_wrong_media_type_count"] += 1
            return None

        comps = file_entry.get("comps") or {}
        preview_url, width, height = self._select_preview(
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

        # Phase 12.2b, Nutzerentscheidung Juli 2026: Video MUSS 16:9 sein
        # (harte Ablehnung, analog zu Pexels) — Fotos MÜSSEN es NICHT sein,
        # is_16_9 wird für Fotos nur informativ mitgeführt, nie erzwungen.
        is_16_9 = self._is_16_9(width, height, request.video_aspect_ratio_tolerance)
        if media_type == "video" and not is_16_9:
            status = CANDIDATE_STATUS_REJECTED_ASPECT_RATIO
            download_enabled = False
        else:
            status = CANDIDATE_STATUS_FOUND
            # download_enabled bezieht sich hier bewusst nur auf "ist der
            # Kandidat selbst technisch verwendbar" (echter Treffer, kein
            # generative-AI-/Aspect-Ratio-Ausschluss) — die eigentliche
            # Lizenzierung/Download-Fähigkeit prüft acquire() unten separat
            # und erneut (z. B. fehlender Access-Token).
            download_enabled = True

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
            width=width,
            height=height,
            duration_sec=duration_sec,
            requires_purchase=True,
            requires_user_approval=True,
            match_score=0.75 if location_match != "missing" else 0.35,
            match_reason="Adobe Stock API Treffer",
            status=status,
            provider_status=PROVIDER_STATUS_READY,
            is_mock=False,
            download_enabled=download_enabled,
            query_used=query_used,
            folder_name=request.folder_name,
            location_name=location_name,
            location_terms_required=required_terms,
            location_terms_present=present_terms,
            location_match=location_match,
            aspect_ratio=round(width / height, 6) if width and height else 0.0,
            aspect_ratio_policy="video_16_9" if media_type == "video" else request.photo_aspect_policy,
            is_16_9=is_16_9,
            approved_for_cut_plan=download_enabled and location_match != "missing",
            supplement_validation_status="NEEDS_USER_REVIEW",
            supplement_validation_score=0.7 if location_match != "missing" else 0.35,
            adobe_media_type_id=media_type_id,
            adobe_is_gentech=is_gentech,
            adobe_comps=comps,
            adobe_content_type=str(file_entry.get("content_type") or ""),
        )

    def _request_licensing_json(
        self,
        endpoint: str,
        params: dict,
        api_key: str,
        access_token: str,
        *,
        timeout: int = 20,
    ) -> dict:
        url = f"{endpoint}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers=self._headers(api_key), method="GET")
        req.add_header("Authorization", f"Bearer {access_token}")
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def _request_licensing_json_safe(
        self,
        endpoint: str,
        params: dict,
        api_key: str,
        access_token: str,
    ) -> dict:
        """Diagnose-Aufrufe dürfen Content/License nicht abbrechen."""
        try:
            return self._request_licensing_json(endpoint, params, api_key, access_token)
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                body = ""
            return {
                "_error": f"HTTP {exc.code}",
                "_message": body or exc.reason or str(exc),
            }
        except (urllib.error.URLError, json.JSONDecodeError, OSError) as exc:
            return {"_error": type(exc).__name__, "_message": str(exc)}

    @staticmethod
    def _summarize_member_profile(payload: dict) -> dict:
        entitlement = payload.get("available_entitlement") or {}
        purchase_options = payload.get("purchase_options") or {}
        member = payload.get("member") or {}
        return {
            "available_entitlement": {
                "quota": entitlement.get("quota"),
                "license_type_id": entitlement.get("license_type_id"),
                "has_credit_model": entitlement.get("has_credit_model"),
                "has_agency_model": entitlement.get("has_agency_model"),
                "is_cce": entitlement.get("is_cce"),
                "full_entitlement_quota": entitlement.get("full_entitlement_quota"),
            },
            "purchase_options": {
                "state": purchase_options.get("state"),
                "requires_checkout": purchase_options.get("requires_checkout"),
                "message": purchase_options.get("message"),
                "url": purchase_options.get("url"),
            },
            "member": member,
            "possible_licenses": payload.get("possible_licenses"),
            "license_references": payload.get("license_references"),
            "_error": payload.get("_error"),
            "_message": payload.get("_message"),
        }

    @staticmethod
    def _summarize_content_info(payload: dict, content_id: str) -> dict:
        entry = _license_content_entry(payload, content_id)
        purchase_details = entry.get("purchase_details") or {}
        return {
            "content_id": entry.get("content_id", content_id),
            "size": entry.get("size"),
            "purchase_details": {
                "state": purchase_details.get("state"),
                "license": purchase_details.get("license"),
                "date": purchase_details.get("date"),
                "member_id": purchase_details.get("member_id"),
                "stock_id": purchase_details.get("stock_id"),
            },
            "member": payload.get("member"),
            "_error": payload.get("_error"),
            "_message": payload.get("_message"),
        }

    @staticmethod
    def _summarize_license_response(payload: dict, content_id: str) -> dict:
        entry = _license_content_entry(payload, content_id)
        purchase_details = entry.get("purchase_details") or {}
        entitlement = payload.get("available_entitlement") or {}
        purchase_options = payload.get("purchase_options") or {}
        return {
            "content_id": entry.get("content_id", content_id),
            "size": entry.get("size"),
            "purchase_details": {
                "state": purchase_details.get("state"),
                "license": purchase_details.get("license"),
                "date": purchase_details.get("date"),
                "url": purchase_details.get("url"),
                "content_type": purchase_details.get("content_type"),
                "width": purchase_details.get("width"),
                "height": purchase_details.get("height"),
            },
            "available_entitlement": {
                "quota": entitlement.get("quota"),
                "license_type_id": entitlement.get("license_type_id"),
                "has_credit_model": entitlement.get("has_credit_model"),
                "has_agency_model": entitlement.get("has_agency_model"),
                "is_cce": entitlement.get("is_cce"),
                "full_entitlement_quota": entitlement.get("full_entitlement_quota"),
            },
            "purchase_options": {
                "state": purchase_options.get("state"),
                "requires_checkout": purchase_options.get("requires_checkout"),
                "message": purchase_options.get("message"),
                "url": purchase_options.get("url"),
            },
            "possible_licenses": payload.get("possible_licenses"),
        }

    def _format_license_diagnostic(self, diagnostic: dict) -> str:
        parts: list[str] = []
        member_profile = diagnostic.get("member_profile") or {}
        content_info = diagnostic.get("content_info") or {}
        license_response = diagnostic.get("license_response") or {}
        if member_profile:
            parts.append(f"Member/Profile={_compact_json(member_profile)}")
        if content_info:
            parts.append(f"Content/Info={_compact_json(content_info)}")
        if license_response:
            parts.append(f"Content/License={_compact_json(license_response)}")
        return " | ".join(parts)

    def lookup_file_metadata(self, content_id: str, api_key: str) -> dict:
        """Files-API: media_type_id / content_type für eine Content-ID."""
        params = {
            "ids": str(content_id),
            "locale": "en_US",
            "result_columns[]": [
                "id",
                "title",
                "content_type",
                "media_type_id",
                "width",
                "height",
                "duration",
            ],
        }
        url = f"{ADOBE_STOCK_FILES_ENDPOINT}?{urllib.parse.urlencode(params, doseq=True)}"
        req = urllib.request.Request(url, headers=self._headers(api_key), method="GET")
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise AdobeContentUnavailableError(
                    f"Content-ID {content_id} ist bei Adobe nicht (mehr) verfügbar."
                ) from exc
            raise
        files = payload.get("files") or []
        if not files:
            raise AdobeContentUnavailableError(
                f"Content-ID {content_id} liefert keine Files-Metadaten (entfernt/ungültig)."
            )
        return files[0] if isinstance(files[0], dict) else {}

    def find_license_history_download(
        self,
        content_id: str,
        api_key: str,
        access_token: str,
        *,
        pages: int = 5,
    ) -> dict | None:
        """Sucht in Member/LicenseHistory nach einer bestehenden Lizenz + Download-URL."""
        target = str(content_id)
        for page in range(pages):
            params = {
                "locale": "en_US",
                "all": "true",
                "search_parameters[limit]": 100,
                "search_parameters[offset]": page * 100,
            }
            payload = self._request_licensing_json_safe(
                ADOBE_STOCK_LICENSE_HISTORY_ENDPOINT,
                params,
                api_key,
                access_token,
            )
            if payload.get("_error"):
                return None
            for entry in payload.get("files") or []:
                if str(entry.get("id") or "") != target:
                    continue
                url = str(entry.get("download_url") or "")
                if not is_full_adobe_download_url(url):
                    continue
                return {
                    "url": url,
                    "license": str(entry.get("license") or ""),
                    "content_type": str(entry.get("content_type") or ""),
                    "width": entry.get("width"),
                    "height": entry.get("height"),
                    "state": "purchased",
                }
            nb = int(payload.get("nb_results") or 0)
            if (page + 1) * 100 >= nb:
                break
        return None

    def content_info_purchase(
        self,
        content_id: str,
        license_type: str,
        api_key: str,
        access_token: str,
    ) -> dict:
        """Content/Info → purchase_details (oder leer bei Fehler)."""
        payload = self._request_licensing_json_safe(
            ADOBE_STOCK_CONTENT_INFO_ENDPOINT,
            {
                "content_id": content_id,
                "license": license_type,
                "locale": "en_US",
            },
            api_key,
            access_token,
        )
        if payload.get("_error"):
            return {"_error": payload.get("_error"), "_message": payload.get("_message")}
        entry = _license_content_entry(payload, content_id)
        details = dict(entry.get("purchase_details") or {})
        details["size"] = entry.get("size")
        return details

    def _license_asset(
        self,
        content_id: str,
        license_type: str,
        api_key: str,
        access_token: str,
        *,
        diagnose: bool = True,
    ) -> dict:
        """Ruft Content/License auf und liefert das purchase_details-Objekt
        der Antwort (enthält u. a. url/content_type/width/height). Löst bei
        HTTP- oder Netzwerkfehlern sowie bei fehlender Download-URL in der
        Antwort ein RuntimeError aus — acquire() darf hier nie stillschweigend
        weitermachen.

        Phase 12.12: Optional vor Content/License Member/Profile und Content/Info
        abfragen (`diagnose=True`). Für Bulk-Downloads (Research-Import) bitte
        `diagnose=False` — sonst 3 API-Calls pro Lizenzversuch und Rate-Limits.
        """
        self.last_license_diagnostic = {
            "content_id": content_id,
            "requested_license": license_type,
            "member_profile": {},
            "content_info": {},
            "license_response": {},
        }
        if diagnose:
            diag_params = {
                "content_id": content_id,
                "license": license_type,
                "locale": "en_US",
            }
            member_payload = self._request_licensing_json_safe(
                ADOBE_STOCK_MEMBER_PROFILE_ENDPOINT,
                diag_params,
                api_key,
                access_token,
            )
            self.last_license_diagnostic["member_profile"] = self._summarize_member_profile(
                member_payload
            )
            content_info_payload = self._request_licensing_json_safe(
                ADOBE_STOCK_CONTENT_INFO_ENDPOINT,
                diag_params,
                api_key,
                access_token,
            )
            self.last_license_diagnostic["content_info"] = self._summarize_content_info(
                content_info_payload, content_id
            )

        params = {"content_id": content_id, "license": license_type, "locale": "en_US"}
        try:
            payload = self._request_licensing_json(
                ADOBE_STOCK_LICENSE_ENDPOINT, params, api_key, access_token, timeout=30
            )
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                body = ""
            if exc.code == 404 or "no longer available" in body.lower() or '"code":"300"' in body:
                raise AdobeContentUnavailableError(
                    f"Content-ID {content_id} ist bei Adobe nicht mehr verfügbar ({body[:180]})."
                ) from exc
            diagnostic_suffix = self._format_license_diagnostic(self.last_license_diagnostic)
            detail = f"{body or exc.reason}"
            if diagnostic_suffix:
                detail = f"{detail} | Diagnose: {diagnostic_suffix}"
            raise RuntimeError(
                f"Adobe-Lizenzierung fehlgeschlagen (HTTP {exc.code}, license={license_type}): {detail}"
            ) from exc
        except (urllib.error.URLError, json.JSONDecodeError, OSError) as exc:
            diagnostic_suffix = self._format_license_diagnostic(self.last_license_diagnostic)
            detail = str(exc)
            if diagnostic_suffix:
                detail = f"{detail} | Diagnose: {diagnostic_suffix}"
            raise RuntimeError(f"Adobe-Lizenzierung fehlgeschlagen (license={license_type}): {detail}") from exc

        self.last_license_diagnostic["license_response"] = self._summarize_license_response(
            payload, content_id
        )
        diagnostic_suffix = self._format_license_diagnostic(self.last_license_diagnostic)

        entry = _license_content_entry(payload, content_id)
        purchase_details = entry.get("purchase_details") or {}
        state = str(purchase_details.get("state") or "unbekannt")
        response_license = str(purchase_details.get("license") or "")
        response_url = str(purchase_details.get("url") or "")
        response_size = str(entry.get("size") or "")
        if state not in {"just_purchased", "purchased"}:
            license_summary = self.last_license_diagnostic.get("license_response") or {}
            if (
                license_type in {ADOBE_STOCK_LICENSE_TYPE_VIDEO_4K, ADOBE_STOCK_LICENSE_TYPE_VIDEO_HD}
                and state == "cancelled"
                and self.entitlement_lacks_video(license_summary)
            ):
                raise AdobeVideoEntitlementError(VIDEO_ENTITLEMENT_HINT)
            raise RuntimeError(
                "Adobe-Lizenzierung nicht bestätigt: "
                f"Content-ID {content_id}, angefragt license={license_type}, "
                f"state={state}, response_license={response_license or '—'}, size={response_size or '—'}. "
                f"Diagnose: {diagnostic_suffix}"
            )
        if response_license and response_license != license_type:
            raise RuntimeError(
                "Adobe-Lizenzierung lieferte unerwarteten Lizenztyp: "
                f"Content-ID {content_id}, angefragt license={license_type}, "
                f"response_license={response_license or '—'}, state={state}, size={response_size or '—'}. "
                f"Diagnose: {diagnostic_suffix}"
            )
        if not response_url:
            raise RuntimeError(
                f"Adobe-Lizenzierung lieferte keine Download-URL für Content-ID {content_id} "
                f"(license={license_type}, state={state}, size={response_size or '—'}). "
                f"Diagnose: {diagnostic_suffix}"
            )
        if not is_full_adobe_download_url(response_url):
            raise RuntimeError(
                "Adobe-Lizenzierung lieferte keine Voll-Download-URL "
                f"(Comp/Wasserzeichen verworfen): Content-ID {content_id}, "
                f"license={license_type}, state={state}, size={response_size or '—'}, "
                f"url={response_url[:180]}. Diagnose: {diagnostic_suffix}"
            )
        return purchase_details

    def _stream_download_to_file(
        self,
        url: str,
        local_path: Path,
        *,
        api_key: str,
        access_token: str,
        size: int | None,
        max_bytes: int | None,
    ) -> None:
        """Lädt url chunked auf local_path herunter. Bricht ab (löscht die
        Teildatei) und wirft AdobeAssetTooLargeError, sobald entweder der
        Content-Length-Header ODER die Summe der bereits gestreamten Bytes
        max_bytes überschreitet — max_bytes=None bedeutet keine Grenze
        (Fotos, oder ein Video, für das ohnehin bereits die kleinste
        Lizenzvariante läuft)."""
        download_url = self._prepare_download_url(url, access_token, size=size)
        req = urllib.request.Request(download_url, headers=self._download_headers(api_key))
        try:
            with urllib.request.urlopen(req, timeout=180) as response:
                status = int(getattr(response, "status", 200) or 200)
                if status != 200:
                    raise RuntimeError(f"Adobe-Download HTTP-Status {status}.")
                content_length = response.headers.get("Content-Length")
                if max_bytes is not None and content_length:
                    try:
                        if int(content_length) > max_bytes:
                            raise AdobeAssetTooLargeError()
                    except ValueError:
                        pass
                total = 0
                with open(local_path, "wb") as handle:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if max_bytes is not None and total > max_bytes:
                            raise AdobeAssetTooLargeError()
                        handle.write(chunk)
        except AdobeAssetTooLargeError:
            local_path.unlink(missing_ok=True)
            raise
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                body = ""
            local_path.unlink(missing_ok=True)
            raise RuntimeError(
                f"Adobe-Download fehlgeschlagen (HTTP {exc.code}): {body or exc.reason}"
            ) from exc
        except (urllib.error.URLError, OSError) as exc:
            local_path.unlink(missing_ok=True)
            raise RuntimeError(f"Adobe-Download fehlgeschlagen: {exc}") from exc

    def _extension_for(self, purchase_details: dict, candidate: SupplementCandidate) -> str:
        content_type = str(purchase_details.get("content_type") or "").lower()
        mapping = {
            "image/jpeg": ".jpg",
            "image/jpg": ".jpg",
            "image/png": ".png",
            "video/mp4": ".mp4",
            "video/quicktime": ".mov",
        }
        if content_type in mapping:
            return mapping[content_type]
        return ".jpg" if candidate.media_type == "image" else ".mp4"

    def acquire(
        self,
        candidate: SupplementCandidate,
        destination_folder: Path,
    ) -> SupplementAsset:
        api_key = get_api_key("ADOBE_STOCK_API_KEY")
        if not api_key:
            raise PermissionError("ADOBE_STOCK_API_KEY fehlt — Adobe-Stock-Download ist deaktiviert.")
        access_token = get_adobe_access_token()
        if not access_token:
            raise PermissionError(
                "Kein Adobe Access-Token — bitte OAuth-Login nutzen oder "
                "ADOBE_STOCK_ACCESS_TOKEN setzen (die reine Suche funktioniert auch ohne)."
            )
        if candidate.is_mock or not candidate.download_enabled:
            raise PermissionError("Mock-/Demo-Kandidaten dürfen nicht heruntergeladen werden.")
        if not candidate.provider_asset_id:
            raise ValueError("Adobe-Kandidat hat keine provider_asset_id.")

        destination_folder.mkdir(parents=True, exist_ok=True)
        folder_slug = destination_folder.parent.parent.name.replace(" ", "_")
        filename_base = (
            f"{folder_slug}_{candidate.supplement_request_id}_adobe_{candidate.provider_asset_id}"
        )

        if candidate.media_type == "video":
            # Nutzervorgabe (4K/HD-Regel): 4K bevorzugen, wenn verfügbar —
            # sonst gleich HD. Die 600-MB-Grenze gilt nur, solange 4K
            # versucht wird; ist HD bereits die einzige Option, gibt es
            # keine kleinere Lizenzvariante mehr, auf die man ausweichen
            # könnte, also keine Größengrenze mehr durchsetzen. Fallback wird
            # NUR angeboten, wenn Video_HD auch tatsächlich in den zur
            # Such-Zeit gespeicherten comps vorhanden war (nicht bloß aus
            # has_4k abgeleitet) — defensiv, falls Adobe für ein Asset
            # ausnahmsweise nur 4K ohne HD liefern sollte.
            has_4k = ADOBE_STOCK_LICENSE_TYPE_VIDEO_4K in candidate.adobe_comps
            has_hd = ADOBE_STOCK_LICENSE_TYPE_VIDEO_HD in candidate.adobe_comps
            if has_4k:
                primary_license = ADOBE_STOCK_LICENSE_TYPE_VIDEO_4K
                fallback_license = ADOBE_STOCK_LICENSE_TYPE_VIDEO_HD if has_hd else ""
                primary_size = 2160
                fallback_size = 1080 if has_hd else None
                size_limit = ADOBE_STOCK_VIDEO_4K_MAX_BYTES
            else:
                primary_license = ADOBE_STOCK_LICENSE_TYPE_VIDEO_HD
                fallback_license = ""
                primary_size = 1080
                fallback_size = None
                size_limit = None
        else:
            primary_license = ADOBE_STOCK_LICENSE_TYPE_STANDARD
            fallback_license = ""
            primary_size = None
            fallback_size = None
            size_limit = None

        purchase_details = self._license_asset(
            candidate.provider_asset_id, primary_license, api_key, access_token
        )
        used_license = primary_license
        extension = self._extension_for(purchase_details, candidate)
        local_path = destination_folder / f"{filename_base}{extension}"

        try:
            self._stream_download_to_file(
                str(purchase_details.get("url") or ""),
                local_path,
                api_key=api_key,
                access_token=access_token,
                size=primary_size,
                max_bytes=size_limit,
            )
        except AdobeAssetTooLargeError:
            if not fallback_license:
                raise RuntimeError(
                    f"Adobe-Video (Content-ID {candidate.provider_asset_id}, {primary_license}) "
                    "überschreitet die 600-MB-Grenze und es ist keine kleinere Lizenzvariante "
                    "verfügbar."
                ) from None
            purchase_details = self._license_asset(
                candidate.provider_asset_id, fallback_license, api_key, access_token
            )
            used_license = fallback_license
            extension = self._extension_for(purchase_details, candidate)
            local_path = destination_folder / f"{filename_base}{extension}"
            self._stream_download_to_file(
                str(purchase_details.get("url") or ""),
                local_path,
                api_key=api_key,
                access_token=access_token,
                size=fallback_size,
                max_bytes=None,
            )

        if not local_path.is_file() or local_path.stat().st_size < ADOBE_STOCK_MIN_DOWNLOAD_BYTES:
            local_path.unlink(missing_ok=True)
            raise RuntimeError("Adobe-Download zu klein — vermutlich kein gültiges Asset.")

        if candidate.media_type == "video":
            duration = probe_duration_seconds(local_path)
            if duration is None:
                local_path.unlink(missing_ok=True)
                raise RuntimeError("ffprobe konnte Adobe-Download nicht lesen.")

        sidecar = SupplementAssetSidecar(
            asset_id=f"asset_adobe_{candidate.provider_asset_id}",
            supplement_request_id=candidate.supplement_request_id,
            provider=self.provider,
            provider_asset_id=candidate.provider_asset_id,
            source_url=candidate.source_page_url,
            download_url=str(purchase_details.get("url") or ""),
            query_used=candidate.query_used,
            location_name=candidate.location_name,
            location_match=candidate.location_match,
            license=used_license,
            creator=candidate.creator,
            acquisition_method="adobe_stock_license_api",
            media_type=candidate.media_type,
            aspect_ratio=candidate.aspect_ratio,
            aspect_ratio_policy=candidate.aspect_ratio_policy,
            is_16_9=candidate.is_16_9,
            supplement_validation_status=candidate.supplement_validation_status,
            supplement_validation_score=candidate.supplement_validation_score,
            approved_for_cut_plan=candidate.approved_for_cut_plan,
            downloaded_at=datetime.now(timezone.utc),
            original_filename=local_path.name,
            local_path=str(local_path),
            rights_status=RIGHTS_STATUS_APPROVED,
            requires_attribution=False,
            approval_status="APPROVED",
        )
        return SupplementAsset(local_path=local_path, sidecar=sidecar)
