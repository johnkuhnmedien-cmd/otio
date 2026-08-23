"""On-demand place lookup for map cards.

Geocoding never runs on project open. Tests inject ``geocode_fn`` so the
default Nominatim client is not required in CI.

Nominatim: English country name first (Ungarn → Hungary), ISO countrycodes
only for a concrete country, query variants, local cache, 1 req/s, retry
HTTP 429. Continent hints (Europa) are never a Nominatim suffix and do
not reject hits. If Nominatim has no hit: Photon, then Wikipedia.
Auto-Lauf and the maps page ask the Brief-LLM for
coordinates from each folder script — never the Cut models. Every
non-manual pin is checked against its chapter script. If the village
is missing, the next larger city from the script is used. LLM coords
override a Nominatim namesake that is far from the scripted place
(Amuzgi in Finland vs Dagestan). A failure for one place never aborts
the remaining search.
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import requests

from otio_app.models import Project
from otio_app.services.without_voiceover_enhanced.maps.models import (
    COORDINATE_STATUS_MANUAL,
    MapCoordinateRecord,
    MapCoordinatesDocument,
    MapPlanDocument,
    MapRenderSettings,
)
from otio_app.services.without_voiceover_enhanced.maps.geo_scope import (
    GeocodeScope,
    coordinates_disagree,
    pin_display_label,
    resolve_geocode_scope,
)
from otio_app.services.without_voiceover_enhanced.maps.plan_service import (
    GeocodeHit,
    apply_geocode_hits,
    build_map_plan,
    load_map_coordinates,
    load_map_plan,
    load_map_settings,
    unique_chapter_places,
)
from otio_app.services.without_voiceover_enhanced.maps.remotion_payload import (
    country_label,
)

GeocodeFn = Callable[[str, str], GeocodeHit | tuple[float, float, float] | None]
GeocodeWaitFn = Callable[[float, str], None]
GeocodeProgressFn = Callable[["GeocodeProgress"], None]

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
PHOTON_URL = "https://photon.komoot.io/api/"
WIKIPEDIA_API_URL = "https://en.wikipedia.org/w/api.php"
NOMINATIM_USER_AGENT = (
    "OTIO-Schnittplaner/0.1 "
    "(https://github.com/johnkuhnmedien-cmd/otio; maps-geocode)"
)
NOMINATIM_TIMEOUT_SEC = 20.0
NOMINATIM_MIN_INTERVAL_SEC = 1.0
NOMINATIM_MAX_429_RETRIES = 5
NOMINATIM_429_BASE_PAUSE_SEC = 1.0
NOMINATIM_429_MAX_PAUSE_SEC = 30.0
WIKIPEDIA_TIMEOUT_SEC = 20.0
PHOTON_TIMEOUT_SEC = 20.0

_LEADING_INDEX_RE = re.compile(r"^\s*\d+[\).\s:\-–—]+")
_LEADING_ARTICLE_RE = re.compile(
    r"^(the|el|la|le|les|der|die|das|il|lo|los|las)\s+",
    re.IGNORECASE,
)

_rate_lock = threading.Lock()
_cache_lock = threading.Lock()
_last_request_monotonic = 0.0
_sleep = time.sleep
_monotonic = time.monotonic


class GeocodeError(RuntimeError):
    """Raised when a single place lookup fails."""


class GeocodeRateLimited(GeocodeError):
    """Nominatim answered HTTP 429 after retries."""


class GeocodeCancelled(GeocodeError):
    """Stop during coordinate lookup."""


@dataclass(frozen=True)
class GeocodeProgress:
    place: str
    index: int
    total: int
    kind: str
    wait_sec: float = 0.0
    detail: str = ""

    @property
    def fraction(self) -> float:
        if self.total <= 0:
            return 1.0
        if self.kind in {"found", "skipped", "missing", "error"}:
            return min(1.0, max(0.0, self.index / self.total))
        return min(1.0, max(0.0, (self.index - 1) / self.total))

    @property
    def message(self) -> str:
        if self.total <= 0:
            return "Alle Orte haben schon Koordinaten."
        prefix = f"{self.index}/{self.total} · {self.place}"
        if self.kind == "waiting":
            seconds = max(1, int(round(self.wait_sec)))
            return (
                f"{prefix}: Nominatim bittet um Pause — {seconds} s warten…"
            )
        if self.kind == "found":
            return f"{prefix}: gefunden"
        if self.kind == "skipped":
            return f"{prefix}: bereits vorhanden"
        if self.kind == "missing":
            return f"{prefix}: kein Treffer"
        if self.kind == "error":
            detail = self.detail or "Suche fehlgeschlagen"
            return f"{prefix}: {detail}"
        return f"{prefix}: suche…"


@dataclass
class GeocodeLookupReport:
    found: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [
            f"{self.found} gefunden",
            f"{self.skipped} bereits vorhanden",
            f"{self.failed} ohne Ergebnis",
        ]
        return "Koordinatenprüfung: " + ", ".join(parts) + "."


@dataclass(frozen=True)
class LlmPlaceSuggestion:
    """Brief-LLM guess for one chapter: Nominatim query plus lat/lon."""

    query: str = ""
    country: str = ""
    latitude: float | None = None
    longitude: float | None = None
    fallback: str = ""
    city: str = ""

    @property
    def has_coordinates(self) -> bool:
        return self.latitude is not None and self.longitude is not None

    @property
    def is_city_fallback(self) -> bool:
        return self.fallback == "city" or bool(self.city)


def nominatim_cache_path() -> Path:
    from otio_app.config import DATA_DIR

    path = DATA_DIR / "cache" / "nominatim_geocode.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def reset_nominatim_client_for_tests() -> None:
    """Reset rate limiter and allow tests to inject sleep/clock."""
    global _last_request_monotonic, _sleep, _monotonic
    with _rate_lock:
        _last_request_monotonic = 0.0
    _sleep = time.sleep
    _monotonic = time.monotonic


def _cache_key(place: str, country_hint: str) -> str:
    return f"{str(country_hint or '').strip().lower()}|{str(place or '').strip().lower()}"


def geocode_query_variants(place: str, country: str = "") -> list[str]:
    """Nominatim-Queries: englischer Ländername zuerst, dann Original, dann ohne Land.

    Kontinent-Hinweise (Europa) werden nicht angehängt — sonst findet Nominatim
    „Urb. La Europa“ statt Granadilla in Spanien.
    """
    place = " ".join(str(place or "").split())
    if not place:
        return []
    scope = resolve_geocode_scope(country)
    original = str(country or "").strip()
    if scope.kind == "continent":
        english = ""
        original = ""
    else:
        english = scope.query_suffix or (
            country_label(original, "EN") if original else ""
        )
        if english.casefold() == "map":
            english = original
    names = [place]
    stripped = _LEADING_INDEX_RE.sub("", place).strip()
    if stripped and stripped.casefold() != place.casefold():
        names.append(stripped)
    no_article = _LEADING_ARTICLE_RE.sub("", names[-1]).strip()
    if no_article and all(no_article.casefold() != item.casefold() for item in names):
        names.append(no_article)
    if ":" in place:
        tail = place.split(":")[-1].strip()
        if tail and all(tail.casefold() != item.casefold() for item in names):
            names.append(tail)
    variants: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        cleaned = " ".join(value.split())
        key = cleaned.casefold()
        if not cleaned or key in seen:
            return
        seen.add(key)
        variants.append(cleaned)

    for name in names:
        if english and english.casefold() not in name.casefold():
            add(f"{name}, {english}")
        if (
            original
            and original.casefold() != english.casefold()
            and original.casefold() not in name.casefold()
        ):
            add(f"{name}, {original}")
        add(name)
    return variants


def _load_cache(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_cache(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _cache_get(path: Path, place: str, country_hint: str) -> GeocodeHit | None:
    key = _cache_key(place, country_hint)
    with _cache_lock:
        stored = _load_cache(path).get(key)
    if not isinstance(stored, dict):
        return None
    if stored.get("latitude") is None or stored.get("longitude") is None:
        return None
    hit = dict(stored)
    hit.setdefault("original_label", place)
    hit.setdefault("display_label", place)
    hit.setdefault("source", "nominatim-cache")
    return hit


def _cache_put(path: Path, place: str, country_hint: str, hit: GeocodeHit) -> None:
    key = _cache_key(place, country_hint)
    record = {
        "latitude": hit.get("latitude"),
        "longitude": hit.get("longitude"),
        "confidence": hit.get("confidence", 0.0),
        "ambiguous": bool(hit.get("ambiguous")),
        "original_label": hit.get("original_label") or place,
        "display_label": hit.get("display_label") or place,
        "source": "nominatim",
        "country_hint": country_hint,
        "country_code": str(hit.get("country_code") or ""),
    }
    with _cache_lock:
        payload = _load_cache(path)
        payload[key] = record
        _save_cache(path, payload)


def _wait_for_nominatim_slot(on_wait: GeocodeWaitFn | None = None) -> None:
    global _last_request_monotonic
    with _rate_lock:
        now = _monotonic()
        wait = NOMINATIM_MIN_INTERVAL_SEC - (now - _last_request_monotonic)
        if wait > 0:
            if on_wait is not None:
                on_wait(wait, "rate_limit")
            _sleep(wait)
        _last_request_monotonic = _monotonic()


def _retry_after_seconds(response: requests.Response | None, attempt: int) -> float:
    backoff = min(
        NOMINATIM_429_MAX_PAUSE_SEC,
        NOMINATIM_429_BASE_PAUSE_SEC * (2**attempt),
    )
    header = ""
    if response is not None:
        header = str(response.headers.get("Retry-After") or "").strip()
    if header:
        try:
            return max(backoff, float(header))
        except ValueError:
            pass
    return backoff


def _response_status(exc: BaseException) -> int | None:
    response = getattr(exc, "response", None)
    if response is None:
        return None
    try:
        return int(getattr(response, "status_code", None) or 0) or None
    except (TypeError, ValueError):
        return None


def friendly_geocode_error(place: str, exc: BaseException) -> str:
    """Short German reason without URLs, JSON, or HTTP dumps."""
    status = _response_status(exc)
    text = str(exc or "").lower()
    if status == 429 or isinstance(exc, GeocodeRateLimited) or "429" in text:
        return "Nominatim ist gerade ausgelastet"
    if status in {500, 502, 503, 504}:
        return "Nominatim antwortet gerade nicht"
    if isinstance(exc, (requests.Timeout, TimeoutError)) or "timeout" in text:
        return "Zeitüberschreitung"
    message = str(exc or "")
    if "no Nominatim hit" in message or "empty place" in message:
        return "kein Treffer"
    if "invalid Nominatim" in message:
        return "Antwort unbrauchbar"
    return "Suche fehlgeschlagen"


def _row_lat_lon(row: dict[str, Any]) -> tuple[float | None, float | None]:
    try:
        return float(row["lat"]), float(row["lon"])
    except (KeyError, TypeError, ValueError):
        return None, None


def _row_country_code(row: dict[str, Any]) -> str:
    address = row.get("address") if isinstance(row.get("address"), dict) else {}
    return str(address.get("country_code") or "").strip().lower()


def _parse_nominatim_payload(
    payload: Any,
    place: str,
    *,
    iso2: str = "",
    country_en: str = "",
    scope: GeocodeScope | None = None,
) -> GeocodeHit:
    rows = _preferred_nominatim_rows(
        payload, iso2=iso2, country_en=country_en, scope=scope
    )
    if not rows:
        raise GeocodeError(f"no Nominatim hit for {place!r}")
    hit = rows[0]
    try:
        lat = float(hit["lat"])
        lon = float(hit["lon"])
    except (KeyError, TypeError, ValueError) as exc:
        raise GeocodeError(f"invalid Nominatim payload for {place!r}") from exc
    try:
        confidence = float(hit.get("importance", 0.55))
    except (TypeError, ValueError):
        confidence = 0.55
    confidence = max(0.0, min(1.0, confidence))
    ambiguous = False
    if len(rows) > 1:
        try:
            second = float(rows[1].get("importance", 0.0) or 0.0)
        except (TypeError, ValueError):
            second = 0.0
        if second >= max(0.4, confidence * 0.85):
            ambiguous = True
            confidence = min(confidence, 0.6)
    display = pin_display_label(place, str(hit.get("name") or "").strip())
    return {
        "latitude": lat,
        "longitude": lon,
        "confidence": confidence,
        "ambiguous": ambiguous,
        "original_label": place,
        "display_label": display,
        "source": "nominatim",
        "country_code": _row_country_code(hit),
    }


def _preferred_nominatim_rows(
    payload: Any,
    *,
    iso2: str = "",
    country_en: str = "",
    scope: GeocodeScope | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        return []
    rows = [row for row in payload if isinstance(row, dict)]
    if not rows:
        return []
    iso = str(iso2 or "").strip().lower()
    english = str(country_en or "").strip()
    if scope is not None and scope.kind == "continent":
        iso = ""
        english = ""
    if not iso and not english:
        return rows
    preferred: list[dict[str, Any]] = []
    for row in rows:
        address = row.get("address") if isinstance(row.get("address"), dict) else {}
        code = str(address.get("country_code") or "").strip().lower()
        name = str(address.get("country") or "")
        if iso and code == iso:
            preferred.append(row)
        elif english and english.casefold() in name.casefold():
            preferred.append(row)
    return preferred or rows


def _nominatim_request(
    query: str,
    place: str,
    *,
    iso2: str = "",
    country_en: str = "",
    scope: GeocodeScope | None = None,
    on_wait: GeocodeWaitFn | None = None,
) -> GeocodeHit:
    last_error: BaseException | None = None
    countrycodes = ""
    if scope is not None and scope.kind == "country":
        countrycodes = scope.iso2 or scope.countrycodes
    elif scope is None:
        countrycodes = iso2
    for attempt in range(NOMINATIM_MAX_429_RETRIES + 1):
        _wait_for_nominatim_slot(on_wait)
        params: dict[str, Any] = {
            "q": query,
            "format": "json",
            "limit": 5,
            "addressdetails": 1,
        }
        if countrycodes:
            params["countrycodes"] = countrycodes
        try:
            response = requests.get(
                NOMINATIM_URL,
                params=params,
                headers={
                    "User-Agent": NOMINATIM_USER_AGENT,
                    "Accept-Language": "en,de;q=0.8",
                },
                timeout=NOMINATIM_TIMEOUT_SEC,
            )
        except requests.Timeout as exc:
            raise GeocodeError(f"Nominatim request failed for {place!r}: timeout") from exc
        except requests.RequestException as exc:
            raise GeocodeError(
                f"Nominatim request failed for {place!r}: {type(exc).__name__}"
            ) from exc
        if getattr(response, "status_code", 200) == 429:
            pause = _retry_after_seconds(response, attempt)
            last_error = GeocodeRateLimited(f"Nominatim rate-limited for {place!r}")
            last_error.response = response  # type: ignore[attr-defined]
            if attempt >= NOMINATIM_MAX_429_RETRIES:
                raise last_error
            if on_wait is not None:
                on_wait(pause, "http_429")
            _sleep(pause)
            continue
        try:
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise GeocodeError(
                f"Nominatim request failed for {place!r}: {type(exc).__name__}"
            ) from exc
        return _parse_nominatim_payload(
            payload, place, iso2=iso2, country_en=country_en, scope=scope
        )
    if last_error is not None:
        raise last_error
    raise GeocodeError(f"Nominatim request failed for {place!r}")


def nominatim_geocode(
    place: str,
    country_hint: str = "",
    *,
    on_wait: GeocodeWaitFn | None = None,
) -> GeocodeHit:
    """Resolve ``place`` via Nominatim. Raises ``GeocodeError`` on failure."""
    query = str(place or "").strip()
    if not query:
        raise GeocodeError("empty place name")
    hint = str(country_hint or "").strip()
    scope = resolve_geocode_scope(hint)
    iso2 = scope.iso2
    country_en = scope.query_suffix or (
        country_label(hint, "EN") if hint and scope.kind == "country" else ""
    )
    if country_en.casefold() == "map":
        country_en = hint
    last_miss: BaseException | None = None
    variants = geocode_query_variants(query, hint)
    for variant in variants:
        try:
            hit = _nominatim_request(
                variant,
                place,
                iso2=iso2,
                country_en=country_en,
                scope=scope,
                on_wait=on_wait,
            )
        except GeocodeError as exc:
            message = str(exc or "")
            if "no Nominatim hit" in message:
                last_miss = exc
                continue
            raise
        hit["country_hint"] = hint
        hit["query"] = variant
        if not str(hit.get("original_label") or "").strip():
            hit["original_label"] = place
        return hit
    if last_miss is not None:
        raise last_miss
    raise GeocodeError(f"no Nominatim hit for {place!r}")


def photon_geocode(place: str, country_hint: str = "") -> GeocodeHit | None:
    """Fuzzy OSM search via Photon. None if nothing usable."""
    variants = geocode_query_variants(place, country_hint)
    query = variants[0] if variants else str(place or "").strip()
    if not query:
        return None
    scope = resolve_geocode_scope(country_hint)
    iso2 = scope.iso2
    params: dict[str, Any] = {"q": query, "limit": 5, "lang": "en"}
    if scope.kind == "country" and scope.bbox is not None:
        south, west, north, east = scope.bbox
        params["bbox"] = f"{west},{south},{east},{north}"
    try:
        response = requests.get(
            PHOTON_URL,
            params=params,
            headers={"User-Agent": NOMINATIM_USER_AGENT},
            timeout=PHOTON_TIMEOUT_SEC,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception:
        return None
    features = payload.get("features") if isinstance(payload, dict) else None
    if not isinstance(features, list):
        return None
    preferred: list[dict[str, Any]] = []
    rest: list[dict[str, Any]] = []
    for feature in features:
        if not isinstance(feature, dict):
            continue
        properties = (
            feature.get("properties")
            if isinstance(feature.get("properties"), dict)
            else {}
        )
        geometry = (
            feature.get("geometry") if isinstance(feature.get("geometry"), dict) else {}
        )
        coords = geometry.get("coordinates")
        if not isinstance(coords, list) or len(coords) < 2:
            continue
        code = str(properties.get("countrycode") or "").strip().lower()
        try:
            lon = float(coords[0])
            lat = float(coords[1])
        except (TypeError, ValueError):
            continue
        row = {"properties": properties, "coords": coords, "code": code}
        if iso2 and code == iso2:
            preferred.append(row)
        else:
            rest.append(row)
    chosen = preferred or rest
    if not chosen:
        return None
    row = chosen[0]
    try:
        lon = float(row["coords"][0])
        lat = float(row["coords"][1])
    except (TypeError, ValueError, KeyError):
        return None
    name = pin_display_label(place, str(row["properties"].get("name") or "").strip())
    return {
        "latitude": lat,
        "longitude": lon,
        "confidence": 0.82,
        "ambiguous": len(chosen) > 1,
        "original_label": place,
        "display_label": name,
        "source": "photon",
        "country_hint": country_hint,
        "country_code": str(row.get("code") or ""),
    }


def wikipedia_geocode(place: str, country_hint: str = "") -> GeocodeHit | None:
    """Landmark coordinates from Wikipedia search. None if the page has no point."""
    variants = geocode_query_variants(place, country_hint)
    query = variants[0] if variants else str(place or "").strip()
    if not query:
        return None
    try:
        response = requests.get(
            WIKIPEDIA_API_URL,
            params={
                "action": "query",
                "generator": "search",
                "gsrsearch": query,
                "gsrlimit": 5,
                "prop": "coordinates",
                "colimit": 5,
                "format": "json",
            },
            headers={"User-Agent": NOMINATIM_USER_AGENT},
            timeout=WIKIPEDIA_TIMEOUT_SEC,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    query_block = payload.get("query")
    pages = query_block.get("pages") if isinstance(query_block, dict) else None
    if not isinstance(pages, dict):
        return None
    for page in pages.values():
        if not isinstance(page, dict):
            continue
        coords = page.get("coordinates")
        if not isinstance(coords, list) or not coords:
            continue
        point = coords[0] if isinstance(coords[0], dict) else None
        if point is None:
            continue
        try:
            lat = float(point["lat"])
            lon = float(point["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        title = str(page.get("title") or "").strip() or place
        return {
            "latitude": lat,
            "longitude": lon,
            "confidence": 0.8,
            "ambiguous": False,
            "original_label": place,
            "display_label": pin_display_label(place, title),
            "source": "wikipedia",
            "country_hint": country_hint,
        }
    return None


def resolve_place_coordinates(
    place: str,
    country_hint: str = "",
    *,
    on_wait: GeocodeWaitFn | None = None,
    geocode_fn: GeocodeFn | None = None,
) -> GeocodeHit:
    """Nominatim, then Photon, then Wikipedia. ``geocode_fn`` skips fallbacks (tests)."""
    if geocode_fn is not None:
        raw = geocode_fn(place, country_hint)
        hit = _normalize_hit(raw, original_label=place, display_label=place)
        if hit is None:
            raise GeocodeError(f"no Nominatim hit for {place!r}")
        return hit
    last_error: BaseException | None = None
    try:
        return nominatim_geocode(place, country_hint, on_wait=on_wait)
    except GeocodeCancelled:
        raise
    except GeocodeError as exc:
        last_error = exc
    photon = photon_geocode(place, country_hint)
    if photon is not None:
        return photon
    wiki = wikipedia_geocode(place, country_hint)
    if wiki is not None:
        return wiki
    if last_error is not None:
        raise last_error
    raise GeocodeError(f"no Nominatim hit for {place!r}")


def _project_video_title(project: Project) -> str:
    video_title = str(getattr(project, "name", "") or "").strip()
    try:
        from otio_app.services.voiceover_generation.project_brief_service import (
            load_project_brief,
        )

        brief = load_project_brief(project)
        if brief is not None and str(brief.video_title or "").strip():
            return str(brief.video_title).strip()
    except Exception:
        pass
    return video_title


def _load_map_script_document(project: Project) -> Any:
    try:
        from otio_app.services.without_voiceover_enhanced.script_lock_service import (
            load_locked_script,
            load_script_draft,
        )

        return load_locked_script(project) or load_script_draft(project)
    except Exception:
        return None


def _chapter_narration_excerpt(
    project: Project,
    chapter_id: str,
    document: Any = None,
) -> str:
    try:
        from otio_app.services.without_voiceover_enhanced.script_author_service import (
            chapter_narration_text,
        )

        script = document if document is not None else _load_map_script_document(project)
        if script is None:
            return ""
        return " ".join((chapter_narration_text(script, chapter_id) or "").split())[:5000]
    except Exception:
        return ""


def _parse_llm_coord(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return number


def _coerce_suggestion(value: Any) -> LlmPlaceSuggestion:
    if isinstance(value, LlmPlaceSuggestion):
        return value
    if not isinstance(value, dict):
        return LlmPlaceSuggestion()
    latitude = _parse_llm_coord(value.get("latitude"))
    longitude = _parse_llm_coord(value.get("longitude"))
    if latitude is None or longitude is None:
        latitude = None
        longitude = None
    fallback_raw = str(value.get("fallback") or "").strip().casefold()
    fallback = "city" if fallback_raw in {"city", "stadt", "larger_city"} else ""
    city = " ".join(str(value.get("city") or "").split())
    if city and not fallback:
        fallback = "city"
    return LlmPlaceSuggestion(
        query=" ".join(str(value.get("query") or "").split()),
        country=" ".join(str(value.get("country") or "").split()),
        latitude=latitude,
        longitude=longitude,
        fallback=fallback,
        city=city,
    )


def _suggestion_agrees(hit: GeocodeHit | None, suggestion: LlmPlaceSuggestion | None) -> bool:
    if hit is None or suggestion is None or not suggestion.has_coordinates:
        return True
    return not coordinates_disagree(
        hit.get("latitude"),
        hit.get("longitude"),
        suggestion.latitude,
        suggestion.longitude,
    )


def _place_needs_search(
    existing: MapCoordinateRecord | None,
    suggestion: LlmPlaceSuggestion | None,
    scope: GeocodeScope,
) -> bool:
    if existing is None or not existing.has_coordinates:
        return True
    if existing.status == COORDINATE_STATUS_MANUAL:
        return False
    if suggestion is not None and suggestion.has_coordinates:
        return coordinates_disagree(
            existing.latitude,
            existing.longitude,
            suggestion.latitude,
            suggestion.longitude,
        )
    return False


def _hit_from_suggestion(
    suggestion: LlmPlaceSuggestion,
    *,
    original: str,
    display: str,
) -> GeocodeHit | None:
    if not suggestion.has_coordinates:
        return None
    country_code = ""
    hint = str(suggestion.country or "").strip()
    if hint:
        country_code = resolve_geocode_scope(hint).iso2
    city = str(suggestion.city or "").strip()
    if suggestion.is_city_fallback:
        note = f"LLM city fallback: {city or suggestion.query}"
        confidence = 0.8
    else:
        note = "LLM from folder script"
        confidence = 0.88
    return {
        "latitude": float(suggestion.latitude),
        "longitude": float(suggestion.longitude),
        "confidence": confidence,
        "original_label": original,
        "display_label": display or original,
        "source": "llm",
        "country_code": country_code,
        "note": note,
    }


def build_geocode_rewrite_prompt(
    project: Project,
    country: str,
    rows: list[dict[str, Any]],
) -> str:
    """Brief-LLM-Prompt: Ort + Videogeografie, nie Cut-Modelle."""
    scope = resolve_geocode_scope(country)
    video_title = _project_video_title(project)
    neighbors = [str(item.get("title") or "").strip() for item in rows if item.get("title")]
    region = scope.label_en or country or "unknown"
    return (
        "Find a geocodable place name for each travel-video chapter.\n"
        f"Video title: {video_title or 'unknown'}\n"
        f"Geographic setting (Land/Region): {country or 'unknown'} "
        f"({region}).\n"
        f"Neighboring chapter places: {', '.join(neighbors) or 'none'}.\n"
        "Every query MUST be in that geographic setting. "
        "Never pick a namesake on another continent "
        "(example: Granadilla in Costa Rica is wrong when the video is about Europe; "
        "use Granadilla, Cáceres, Spain).\n"
        "If the setting is a continent, add the specific country to the query "
        "(not the continent name — do not write ', Europe' or ', Europa').\n"
        "Return JSON only of the form "
        '{"places": [{"id": "chapter id", "query": "Granadilla, Cáceres, Spain"}]}.\n'
        "query must be a real place Nominatim can find. "
        "If unknown, use an empty query. Do not invent coordinates.\n"
        f"{json.dumps(rows, ensure_ascii=False)}\n"
    )


def build_geocode_coordinate_prompt(
    project: Project,
    country: str,
    rows: list[dict[str, Any]],
) -> str:
    """Brief-LLM-Prompt: Folder-Skript → Query + Koordinaten, nie Cut-Modelle."""
    scope = resolve_geocode_scope(country)
    video_title = _project_video_title(project)
    neighbors = [str(item.get("title") or "").strip() for item in rows if item.get("title")]
    region = scope.label_en or country or "unknown"
    return (
        "Read each travel-video chapter's folder script and return map coordinates.\n"
        f"Video title: {video_title or 'unknown'}\n"
        f"Geographic setting (Land/Region): {country or 'unknown'} "
        f"({region}).\n"
        f"Neighboring chapter places: {', '.join(neighbors) or 'none'}.\n"
        "Each item has its own folder script in `narration`. "
        "Understand that chapter's context: country, region, nearby towns. "
        "If the script is empty, use the chapter title, video title, and neighbors.\n"
        "If the exact village or place can be located, return those coordinates "
        "and set fallback to \"exact\".\n"
        "If the exact place cannot be located, return the next larger city "
        "named or implied by the script (municipality or regional town), "
        "set fallback to \"city\", and put that town in `city` and `query`.\n"
        "Every real chapter MUST have latitude and longitude. "
        "Never pick a namesake in the wrong country.\n"
        "Examples:\n"
        "- Granadilla → Granadilla, Cáceres, Spain — not Costa Rica.\n"
        "- Amuzgi → Amuzgi, Dagestan, Russia (41.82, 47.58) — not Finland.\n"
        "- Ropoto → Ropoto, Trikala, Greece (39.47, 21.47) — not Germany. "
        "If Ropoto itself is missing, use the next larger city Trikala.\n"
        "If the setting is a continent, put the specific country in `country` "
        "and `query`. Do not write ', Europe' or ', Europa' in the query.\n"
        "Return JSON only of the form "
        '{"places":[{"id":"chapter id","query":"Ropoto, Trikala, Greece",'
        '"country":"Greece","fallback":"exact","city":"",'
        '"latitude":39.47,"longitude":21.47}]}.\n'
        f"{json.dumps(rows, ensure_ascii=False)}\n"
    )


def rewrite_geocode_queries_with_llm(
    project: Project,
    places: list[tuple[str, str]],
    country: str,
) -> dict[str, str]:
    """Kapitel-Titel → geocodierbarer Ortsname. Nutzt Brief-Modell, nie Cut-Modelle."""
    if not places:
        return {}
    from otio_app.services.gemini_client import _extract_json
    from otio_app.services.plan_llm_client import generate_plan_text
    from otio_app.services.voiceover_generation.model_settings_service import (
        load_model_settings,
        resolve_llm_model_id,
    )

    rows = [
        {
            "id": chapter_id,
            "title": original,
            "narration": _chapter_narration_excerpt(project, chapter_id),
        }
        for chapter_id, original in places
    ]
    prompt = build_geocode_rewrite_prompt(project, country, rows)
    try:
        settings = load_model_settings(project)
        role = settings.project_brief
        model = resolve_llm_model_id(role.provider, role.model)
        raw = generate_plan_text(
            prompt=prompt,
            model=model,
            max_output_tokens=800,
            disable_thinking=True,
            project=project,
            stage="maps_geocode",
        )
        payload = _extract_json(raw)
    except Exception:
        return {}
    items = payload.get("places") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return {}
    mapping: dict[str, str] = {}
    known = {chapter_id: original for chapter_id, original in places}
    for item in items:
        if not isinstance(item, dict):
            continue
        chapter_id = str(item.get("id") or "").strip()
        query = " ".join(str(item.get("query") or "").split())
        if chapter_id not in known or not query:
            continue
        original = known[chapter_id]
        if query.casefold() in {chapter_id.casefold(), original.casefold()}:
            continue
        mapping[chapter_id] = query
    return mapping


def _suggestions_from_payload(
    payload: Any,
    known: dict[str, str],
) -> dict[str, LlmPlaceSuggestion]:
    items = payload.get("places") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return {}
    mapping: dict[str, LlmPlaceSuggestion] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        chapter_id = str(item.get("id") or "").strip()
        if chapter_id not in known:
            continue
        suggestion = _coerce_suggestion(item)
        query = suggestion.query
        if not query and not suggestion.has_coordinates and not suggestion.city:
            continue
        mapping[chapter_id] = LlmPlaceSuggestion(
            query=query or suggestion.city or known[chapter_id],
            country=suggestion.country,
            latitude=suggestion.latitude,
            longitude=suggestion.longitude,
            fallback=suggestion.fallback,
            city=suggestion.city,
        )
    return mapping


def _call_coordinate_llm(
    project: Project,
    country: str,
    rows: list[dict[str, Any]],
    *,
    known: dict[str, str],
    max_output_tokens: int,
) -> dict[str, LlmPlaceSuggestion]:
    from otio_app.services.gemini_client import _extract_json
    from otio_app.services.plan_llm_client import generate_plan_text
    from otio_app.services.voiceover_generation.model_settings_service import (
        load_model_settings,
        resolve_llm_model_id,
    )

    prompt = build_geocode_coordinate_prompt(project, country, rows)
    try:
        settings = load_model_settings(project)
        role = settings.project_brief
        model = resolve_llm_model_id(role.provider, role.model)
        raw = generate_plan_text(
            prompt=prompt,
            model=model,
            max_output_tokens=max_output_tokens,
            disable_thinking=True,
            project=project,
            stage="maps_geocode",
        )
        payload = _extract_json(raw)
    except Exception:
        return {}
    return _suggestions_from_payload(payload, known)


def suggest_coordinates_with_llm(
    project: Project,
    places: list[tuple[str, str]],
    country: str,
) -> dict[str, LlmPlaceSuggestion]:
    """Folder-Skript → Query + lat/lon. Nutzt Brief-Modell, nie Cut-Modelle."""
    if not places:
        return {}
    document = _load_map_script_document(project)
    known = {chapter_id: original for chapter_id, original in places}

    def rows_for(subset: list[tuple[str, str]]) -> list[dict[str, Any]]:
        return [
            {
                "id": chapter_id,
                "title": original,
                "narration": _chapter_narration_excerpt(
                    project, chapter_id, document
                ),
            }
            for chapter_id, original in subset
        ]

    mapping = _call_coordinate_llm(
        project,
        country,
        rows_for(places),
        known=known,
        max_output_tokens=4000,
    )
    missing = [(chapter_id, original) for chapter_id, original in places if chapter_id not in mapping]
    for chapter_id, original in missing:
        mapping.update(
            _call_coordinate_llm(
                project,
                country,
                rows_for([(chapter_id, original)]),
                known={chapter_id: original},
                max_output_tokens=800,
            )
        )
    return mapping


def _normalize_hit(
    raw: GeocodeHit | tuple[float, float, float] | None,
    *,
    original_label: str,
    display_label: str,
) -> GeocodeHit | None:
    if raw is None:
        return None
    if isinstance(raw, tuple):
        if len(raw) < 3:
            return None
        lat, lon, confidence = raw[0], raw[1], raw[2]
        return {
            "latitude": float(lat),
            "longitude": float(lon),
            "confidence": float(confidence),
            "original_label": original_label,
            "display_label": display_label,
            "source": "geocode",
        }
    if not isinstance(raw, dict):
        return None
    hit: dict[str, Any] = dict(raw)
    hit.setdefault("original_label", original_label)
    hit.setdefault("display_label", display_label)
    hit.setdefault("source", "geocode")
    return hit


def _hit_from_existing(
    coords: MapCoordinatesDocument, original: str
) -> GeocodeHit | None:
    key = str(original or "").strip().lower()
    if not key:
        return None
    for record in coords.places.values():
        if not record.has_coordinates:
            continue
        if str(record.original_label or "").strip().lower() != key:
            continue
        return {
            "latitude": record.latitude,
            "longitude": record.longitude,
            "confidence": record.confidence,
            "original_label": original,
            "display_label": record.display_label or original,
            "source": record.source or "existing",
            "ambiguous": record.status == "needs_review",
        }
    return None


def lookup_missing_coordinates(
    project: Project,
    *,
    settings: MapRenderSettings | None = None,
    plan: MapPlanDocument | None = None,
    coordinates: MapCoordinatesDocument | None = None,
    geocode_fn: GeocodeFn | None = None,
    on_progress: GeocodeProgressFn | None = None,
    cache_path: Path | None = None,
    should_cancel: Callable[[], bool] | None = None,
    llm_rewrite: bool = False,
    rewrite_queries_fn: Callable[[list[tuple[str, str]], str], dict[str, str]] | None = None,
    suggest_coordinates_fn: (
        Callable[[list[tuple[str, str]], str], dict[str, LlmPlaceSuggestion]] | None
    ) = None,
    force_recheck: bool = False,
) -> tuple[MapCoordinatesDocument, MapPlanDocument, list[str]]:
    """Resolve chapters that still lack coordinates. Does not render.

    Returns ``(coordinates, rebuilt_plan, errors)``. Successful hits are saved
    to the project coordinate store. Uncertain hits stay stored but keep the
    affected maps blocked until confidence is high enough or the user confirms.
    One failed place never aborts the remaining search. ``llm_rewrite`` asks
    the Brief-Modell for coordinates from each folder script — never the
    Cut-Modelle. A Nominatim namesake far from those coordinates is discarded.
    ``force_recheck`` searches every non-manual chapter again, even if the
    form still shows the last run. Production checks always ask the Brief-LLM
    with each folder script; if the village is missing, the next larger city
    from the script is used.
    """
    resolved_settings = settings or load_map_settings(project)
    current_plan = plan or load_map_plan(project)
    if current_plan is None:
        current_plan = build_map_plan(project, settings=resolved_settings, coordinates=coordinates)
    coords = coordinates or load_map_coordinates(project)
    cache_file = cache_path if cache_path is not None else nominatim_cache_path()
    use_nominatim = geocode_fn is None
    report = GeocodeLookupReport()
    hits: dict[str, GeocodeHit] = {}
    query_hits: dict[str, GeocodeHit] = {}
    scope = resolve_geocode_scope(current_plan.country)
    used_coordinate_llm = False
    suggestions: dict[str, LlmPlaceSuggestion] = {}

    def emit(event: GeocodeProgress) -> None:
        if on_progress is not None:
            on_progress(event)

    def cancelled() -> bool:
        return bool(should_cancel and should_cancel())

    def store_hit(chapter_id: str, original: str, display: str, hit: GeocodeHit) -> bool:
        hit = dict(hit)
        hit["original_label"] = original
        osm_name = str(hit.get("display_label") or "").strip()
        hit["display_label"] = pin_display_label(original, osm_name)
        hits[chapter_id] = hit
        query_hits[_cache_key(original, current_plan.country)] = hit
        if use_nominatim:
            _cache_put(cache_file, original, current_plan.country, hit)
        report.found += 1
        return True

    places = unique_chapter_places(current_plan)
    non_manual = [
        (chapter_id, original)
        for chapter_id, original, _display in places
        if (
            coords.places.get(chapter_id) is None
            or coords.places[chapter_id].status != COORDINATE_STATUS_MANUAL
        )
    ]
    called_real_llm = (
        llm_rewrite and use_nominatim and rewrite_queries_fn is None
        and suggest_coordinates_fn is None
    )
    if non_manual and (suggest_coordinates_fn is not None or called_real_llm):
        if cancelled():
            raise GeocodeCancelled("Auto-Lauf gestoppt.")
        try:
            if suggest_coordinates_fn is not None:
                raw_map = suggest_coordinates_fn(non_manual, current_plan.country) or {}
            else:
                raw_map = suggest_coordinates_with_llm(
                    project, non_manual, current_plan.country
                )
        except GeocodeCancelled:
            raise
        except Exception:
            raw_map = {}
        used_coordinate_llm = True
        for chapter_id, value in dict(raw_map).items():
            suggestions[str(chapter_id)] = _coerce_suggestion(value)

    always_recheck = force_recheck or called_real_llm
    pending: list[tuple[str, str, str]] = []
    for chapter_id, original, display in places:
        existing = coords.places.get(chapter_id)
        if existing is not None and existing.status == COORDINATE_STATUS_MANUAL:
            report.skipped += 1
            continue
        if always_recheck or _place_needs_search(
            existing, suggestions.get(chapter_id), scope
        ):
            pending.append((chapter_id, original, display))
            continue
        report.skipped += 1

    total = len(pending)
    if total == 0:
        emit(GeocodeProgress(place="", index=0, total=0, kind="skipped"))
        rebuilt = build_map_plan(
            project,
            settings=resolved_settings,
            coordinates=coords,
            previous=current_plan,
        )
        return coords, rebuilt, report.errors

    missed: list[tuple[str, str, str, str]] = []
    for index, (chapter_id, original, display) in enumerate(pending, start=1):
        if cancelled():
            raise GeocodeCancelled("Auto-Lauf gestoppt.")
        emit(
            GeocodeProgress(
                place=original, index=index, total=total, kind="checking"
            )
        )
        suggestion = suggestions.get(chapter_id)
        reused = None
        if not always_recheck:
            reused = _hit_from_existing(coords, original) or query_hits.get(
                _cache_key(original, current_plan.country)
            )
            if reused is None and use_nominatim:
                reused = _cache_get(cache_file, original, current_plan.country)
        if reused is not None:
            hit = _normalize_hit(reused, original_label=original, display_label=display)
            if (
                hit is not None
                and _suggestion_agrees(hit, suggestion)
                and store_hit(chapter_id, original, display, hit)
            ):
                emit(
                    GeocodeProgress(
                        place=original, index=index, total=total, kind="skipped"
                    )
                )
                continue

        def on_wait(seconds: float, reason: str) -> None:
            emit(
                GeocodeProgress(
                    place=original,
                    index=index,
                    total=total,
                    kind="waiting",
                    wait_sec=seconds,
                    detail=reason,
                )
            )

        def lookup_one(query: str, hint: str) -> tuple[GeocodeHit | None, str]:
            try:
                raw = resolve_place_coordinates(
                    query,
                    hint,
                    on_wait=on_wait,
                    geocode_fn=geocode_fn,
                )
                hit = _normalize_hit(
                    raw, original_label=original, display_label=display
                )
            except GeocodeCancelled:
                raise
            except Exception as exc:  # noqa: BLE001 — Ort überspringen, Rest weiter
                return None, friendly_geocode_error(original, exc)
            if hit is None:
                return None, "kein Treffer"
            return hit, ""

        last_reason = "kein Treffer"
        resolved = False
        searches: list[tuple[str, str]] = []
        if suggestion is not None:
            hint = suggestion.country or current_plan.country
            if suggestion.query:
                searches.append((suggestion.query, hint))
            if suggestion.city:
                city_search = (suggestion.city, hint)
                if city_search not in searches:
                    searches.append(city_search)
        original_search = (original, current_plan.country)
        if original_search not in searches:
            searches.append(original_search)
        for query, hint in searches:
            hit, reason = lookup_one(query, hint)
            if reason:
                last_reason = reason
            if hit is not None and not _suggestion_agrees(hit, suggestion):
                hit = None
                last_reason = "Namesake weicht vom Skript ab"
            if hit is not None and store_hit(chapter_id, original, display, hit):
                emit(
                    GeocodeProgress(
                        place=original, index=index, total=total, kind="found"
                    )
                )
                resolved = True
                break
        if resolved:
            continue
        llm_hit = (
            _hit_from_suggestion(suggestion, original=original, display=display)
            if suggestion is not None
            else None
        )
        if llm_hit is not None and store_hit(chapter_id, original, display, llm_hit):
            emit(
                GeocodeProgress(
                    place=original, index=index, total=total, kind="found"
                )
            )
            continue
        missed.append((chapter_id, original, display, last_reason))

    if missed and llm_rewrite and use_nominatim and (
        rewrite_queries_fn is not None or not used_coordinate_llm
    ):
        rewriter = rewrite_queries_fn or (
            lambda rows, country: rewrite_geocode_queries_with_llm(project, rows, country)
        )
        try:
            mapping = rewriter(
                [(chapter_id, original) for chapter_id, original, _display, _reason in missed],
                current_plan.country,
            )
        except GeocodeCancelled:
            raise
        except Exception:
            mapping = {}
        still: list[tuple[str, str, str, str]] = []
        for chapter_id, original, display, reason in missed:
            if cancelled():
                raise GeocodeCancelled("Auto-Lauf gestoppt.")
            query = str((mapping or {}).get(chapter_id) or "").strip()
            if not query:
                still.append((chapter_id, original, display, reason))
                continue
            emit(
                GeocodeProgress(
                    place=original, index=total, total=total, kind="checking"
                )
            )
            try:
                raw = resolve_place_coordinates(
                    query,
                    current_plan.country,
                    geocode_fn=None,
                )
                hit = _normalize_hit(
                    raw, original_label=original, display_label=display
                )
            except GeocodeCancelled:
                raise
            except Exception as exc:  # noqa: BLE001
                still.append(
                    (
                        chapter_id,
                        original,
                        display,
                        friendly_geocode_error(original, exc),
                    )
                )
                continue
            suggestion = suggestions.get(chapter_id)
            if hit is not None and not _suggestion_agrees(hit, suggestion):
                hit = None
            if hit is None or not store_hit(chapter_id, original, display, hit):
                still.append((chapter_id, original, display, "kein Treffer"))
                continue
            emit(
                GeocodeProgress(
                    place=original, index=total, total=total, kind="found"
                )
            )
        missed = still

    for chapter_id, original, display, reason in missed:
        report.failed += 1
        report.errors.append(f"{original}: {reason}")
        emit(
            GeocodeProgress(
                place=original, index=total, total=total, kind="error", detail=reason
            )
        )

    if hits:
        coords = apply_geocode_hits(project, hits)
    rebuilt = build_map_plan(
        project,
        settings=resolved_settings,
        coordinates=coords,
        previous=current_plan,
    )
    return coords, rebuilt, report.errors
