"""On-demand place lookup for map cards.

Geocoding never runs on project open. Tests inject ``geocode_fn`` so the
default Nominatim client is not required in CI.

Nominatim: English country name first (Ungarn → Hungary), ISO countrycodes,
query variants, local cache, 1 req/s, retry HTTP 429. If Nominatim has no
hit: Photon, then Wikipedia. Auto-Lauf and the maps page can ask the
Brief-LLM for a better place name after that — never the Cut models.
A failure for one place never aborts the remaining search.
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
    MapCoordinatesDocument,
    MapPlanDocument,
    MapRenderSettings,
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
    country_iso2,
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
_DE_LANDMARK_SUFFIXES: tuple[tuple[str, str], ...] = (
    ("-klammen", " Gorges"),
    (" klammen", " Gorges"),
    ("-klamm", " Gorge"),
    (" klamm", " Gorge"),
    ("-schlucht", " Gorge"),
    ("-tal", " Valley"),
    (" tal", " Valley"),
    ("-see", " Lake"),
    (" see", " Lake"),
    ("-wasserfall", " Falls"),
    (" wasserfall", " Falls"),
    ("-höhle", " Cave"),
    ("-hoehle", " Cave"),
    (" höhle", " Cave"),
)
_DE_ATTRIBUTIVE_RE = re.compile(r"^(.+?)er(?:\s+|-)(.+)$", re.IGNORECASE)
_GENERIC_GEO_TOKENS = {
    "lake",
    "see",
    "gorge",
    "gorges",
    "klamm",
    "klammen",
    "valley",
    "tal",
    "castle",
    "burg",
    "cave",
    "höhle",
    "hoehle",
    "falls",
    "wasserfall",
    "dolina",
    "jezero",
    "korita",
    "soteska",
    "slovenia",
    "slowenien",
    "hungary",
    "ungarn",
    "greece",
    "griechenland",
    "germany",
    "deutschland",
    "austria",
    "österreich",
    "italy",
    "italien",
    "france",
    "frankreich",
    "croatia",
    "kroatien",
}

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


def _german_landmark_english(place: str) -> str | None:
    """Vintgar-Klamm → Vintgar Gorge, Burg Predjama → Predjama Castle."""
    text = " ".join(str(place or "").split())
    if not text:
        return None
    lower = text.casefold()
    if lower.startswith("burg "):
        rest = text[5:].strip()
        return f"{rest} Castle" if rest else None
    for german, english in _DE_LANDMARK_SUFFIXES:
        needle = german.casefold()
        if lower.endswith(needle):
            stem = text[: len(text) - len(german)].rstrip("- ").strip()
            if stem:
                return f"{stem}{english}"
    return None


def _dolina_name(place: str) -> str | None:
    """Logar-Tal → Logarska dolina (OSM-Name, den Nominatim findet)."""
    text = " ".join(str(place or "").split())
    lower = text.casefold()
    if lower.endswith("-tal"):
        stem = text[:-4].rstrip("- ").strip()
    elif lower.endswith(" tal"):
        stem = text[:-4].strip()
    else:
        return None
    if not stem:
        return None
    return f"{stem}ska dolina"


def _de_attributive_place(place: str) -> str | None:
    """Bohinjer See → Bohinj Lake, Tolminer Klammen → Tolmin Gorges."""
    text = " ".join(str(place or "").split())
    match = _DE_ATTRIBUTIVE_RE.match(text)
    if not match:
        return None
    stem = match.group(1).strip("- ")
    rest = match.group(2).strip()
    if len(stem) < 4 or not rest:
        return None
    composed = f"{stem} {rest}"
    return _german_landmark_english(composed) or composed


def _korita_name(place: str) -> str | None:
    """Tolminer Klammen → Tolminska korita (OSM-Name der Tolmin Gorge)."""
    english = _de_attributive_place(place)
    if not english:
        return None
    if not (english.endswith(" Gorges") or english.endswith(" Gorge")):
        return None
    stem = english.replace(" Gorges", "").replace(" Gorge", "").strip()
    if len(stem) < 4:
        return None
    return f"{stem}ska korita"


def geocode_query_variants(place: str, country: str = "") -> list[str]:
    """Nominatim-Queries: englischer Ländername zuerst, dann Original, dann ohne Land."""
    place = " ".join(str(place or "").split())
    if not place:
        return []
    original = str(country or "").strip()
    english = country_label(original, "EN") if original else ""
    if english.casefold() == "map":
        english = original
    names: list[str] = []
    expanded: list[str] = []
    for extra in (
        _dolina_name(place),
        _korita_name(place),
        _de_attributive_place(place),
        _german_landmark_english(place),
        place,
    ):
        if extra:
            expanded.append(extra)
            if extra.endswith(" Gorges"):
                expanded.append(extra[:-1])
    for extra in expanded:
        if extra and all(extra.casefold() != item.casefold() for item in names):
            names.append(extra)
    stripped = _LEADING_INDEX_RE.sub("", place).strip()
    if stripped and all(stripped.casefold() != item.casefold() for item in names):
        names.append(stripped)
    no_article = _LEADING_ARTICLE_RE.sub("", names[-1]).strip() if names else ""
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
        "source": str(hit.get("source") or "nominatim"),
        "country_hint": country_hint,
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


def _parse_nominatim_payload(
    payload: Any, place: str, *, iso2: str = "", country_en: str = ""
) -> GeocodeHit:
    rows = _preferred_nominatim_rows(payload, iso2=iso2, country_en=country_en)
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
    display = str(hit.get("name") or "").strip() or place
    return {
        "latitude": lat,
        "longitude": lon,
        "confidence": confidence,
        "ambiguous": ambiguous,
        "original_label": place,
        "display_label": display,
        "source": "nominatim",
    }


def _preferred_nominatim_rows(
    payload: Any, *, iso2: str = "", country_en: str = ""
) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        return []
    rows = [row for row in payload if isinstance(row, dict)]
    if not rows:
        return []
    iso = str(iso2 or "").strip().lower()
    english = str(country_en or "").strip()
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
    on_wait: GeocodeWaitFn | None = None,
) -> GeocodeHit:
    last_error: BaseException | None = None
    for attempt in range(NOMINATIM_MAX_429_RETRIES + 1):
        _wait_for_nominatim_slot(on_wait)
        params: dict[str, Any] = {
            "q": query,
            "format": "json",
            "limit": 5,
            "addressdetails": 1,
        }
        if iso2:
            params["countrycodes"] = iso2
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
            payload, place, iso2=iso2, country_en=country_en
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
    iso2 = country_iso2(hint)
    country_en = country_label(hint, "EN") if hint else ""
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
    if iso2:
        try:
            hit = _nominatim_request(
                variants[0] if variants else query,
                place,
                iso2="",
                country_en=country_en,
                on_wait=on_wait,
            )
        except GeocodeError as exc:
            last_miss = exc
        else:
            hit["country_hint"] = hint
            hit["query"] = variants[0] if variants else query
            return hit
    if last_miss is not None:
        raise last_miss
    raise GeocodeError(f"no Nominatim hit for {place!r}")


# south, north, west, east — grobe Länderbox gegen Photon/Wikipedia-Fremdtreffer
_ISO2_BBOX: dict[str, tuple[float, float, float, float]] = {
    "si": (45.2, 46.98, 13.35, 16.65),
    "hu": (45.7, 48.62, 16.05, 22.95),
    "gr": (34.7, 41.85, 19.2, 29.75),
    "at": (46.32, 49.05, 9.45, 17.22),
    "de": (47.2, 55.15, 5.8, 15.1),
    "it": (35.4, 47.15, 6.55, 18.6),
    "hr": (42.3, 46.6, 13.4, 19.5),
    "fr": (41.25, 51.2, -5.25, 9.75),
    "es": (35.9, 43.85, -9.4, 4.4),
    "pt": (36.9, 42.2, -9.6, -6.1),
    "pl": (49.0, 54.9, 14.05, 24.2),
    "cz": (48.5, 51.1, 12.05, 18.9),
    "ch": (45.8, 47.85, 5.9, 10.55),
}


def _point_in_iso2(lat: float, lon: float, iso2: str) -> bool:
    box = _ISO2_BBOX.get(str(iso2 or "").strip().lower())
    if box is None:
        return True
    south, north, west, east = box
    return south <= lat <= north and west <= lon <= east


def _place_tokens(*parts: str) -> set[str]:
    tokens: set[str] = set()
    for part in parts:
        for raw in re.split(r"[^0-9a-zäöüß]+", str(part or "").casefold()):
            if len(raw) >= 4:
                tokens.add(raw)
    return tokens


def _distinctive_place_tokens(*parts: str) -> set[str]:
    return {token for token in _place_tokens(*parts) if token not in _GENERIC_GEO_TOKENS}


def photon_geocode(place: str, country_hint: str = "") -> GeocodeHit | None:
    """Fuzzy OSM search via Photon. None if nothing usable."""
    variants = geocode_query_variants(place, country_hint)[:6]
    if not variants:
        return None
    iso2 = country_iso2(country_hint)
    wanted = _distinctive_place_tokens(
        place,
        _german_landmark_english(place) or "",
        _dolina_name(place) or "",
        _de_attributive_place(place) or "",
        _korita_name(place) or "",
    )
    best: tuple[int, dict[str, Any]] | None = None
    for query in variants:
        try:
            response = requests.get(
                PHOTON_URL,
                params={"q": query, "limit": 5, "lang": "en"},
                headers={"User-Agent": NOMINATIM_USER_AGENT},
                timeout=PHOTON_TIMEOUT_SEC,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:
            continue
        features = payload.get("features") if isinstance(payload, dict) else None
        if not isinstance(features, list):
            continue
        for feature in features:
            if not isinstance(feature, dict):
                continue
            properties = (
                feature.get("properties")
                if isinstance(feature.get("properties"), dict)
                else {}
            )
            geometry = (
                feature.get("geometry")
                if isinstance(feature.get("geometry"), dict)
                else {}
            )
            coords = geometry.get("coordinates")
            if not isinstance(coords, list) or len(coords) < 2:
                continue
            try:
                lon = float(coords[0])
                lat = float(coords[1])
            except (TypeError, ValueError):
                continue
            if not _point_in_iso2(lat, lon, iso2):
                continue
            code = str(properties.get("countrycode") or "").strip().lower()
            name = str(properties.get("name") or "").strip()
            city = str(properties.get("city") or "").strip()
            name_tokens = _distinctive_place_tokens(name, city)
            overlap = wanted & name_tokens
            if not overlap:
                continue
            score = 3 * len(overlap)
            if iso2 and code == iso2:
                score += 10
            row = {
                "lat": lat,
                "lon": lon,
                "name": name or city or place,
                "query": query,
            }
            if best is None or score > best[0]:
                best = (score, row)
        if best is not None and best[0] >= 16:
            break
    if best is None:
        return None
    row = best[1]
    return {
        "latitude": float(row["lat"]),
        "longitude": float(row["lon"]),
        "confidence": 0.82,
        "ambiguous": False,
        "original_label": place,
        "display_label": str(row["name"]),
        "source": "photon",
        "country_hint": country_hint,
        "query": str(row.get("query") or ""),
    }


def wikipedia_geocode(place: str, country_hint: str = "") -> GeocodeHit | None:
    """Landmark coordinates from Wikipedia search. None if the page has no point."""
    variants = geocode_query_variants(place, country_hint)[:6]
    if not variants:
        return None
    iso2 = country_iso2(country_hint)
    country_en = country_label(country_hint, "EN") if country_hint else ""
    for query in variants:
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
            continue
        if not isinstance(payload, dict):
            continue
        query_block = payload.get("query")
        pages = query_block.get("pages") if isinstance(query_block, dict) else None
        if not isinstance(pages, dict):
            continue
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
            if not _point_in_iso2(lat, lon, iso2):
                continue
            title = str(page.get("title") or "").strip() or place
            if (
                country_en
                and country_en.casefold() != "map"
                and country_en.casefold() not in title.casefold()
                and iso2
                and not _point_in_iso2(lat, lon, iso2)
            ):
                continue
            return {
                "latitude": lat,
                "longitude": lon,
                "confidence": 0.8,
                "ambiguous": False,
                "original_label": place,
                "display_label": title,
                "source": "wikipedia",
                "country_hint": country_hint,
                "query": query,
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


def _chapter_narration_excerpt(project: Project, chapter_id: str) -> str:
    try:
        from otio_app.services.without_voiceover_enhanced.script_author_service import (
            chapter_narration_text,
        )
        from otio_app.services.without_voiceover_enhanced.script_lock_service import (
            load_locked_script,
            load_script_draft,
        )

        document = load_locked_script(project) or load_script_draft(project)
        if document is None:
            return ""
        return " ".join((chapter_narration_text(document, chapter_id) or "").split())[:700]
    except Exception:
        return ""


def _parse_llm_coord(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number


@dataclass
class LlmPlaceSuggestion:
    query: str = ""
    latitude: float | None = None
    longitude: float | None = None

    @property
    def has_coordinates(self) -> bool:
        return self.latitude is not None and self.longitude is not None


def rewrite_geocode_places_with_llm(
    project: Project,
    places: list[tuple[str, str]],
    country: str,
) -> dict[str, LlmPlaceSuggestion]:
    """Kapitel-Titel → OSM-Query plus Koordinaten der nächsten Stadt. Brief-Modell."""
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
    country_en = country_label(country, "EN") if country else country
    prompt = (
        "Find a geocodable place for each travel-video chapter.\n"
        f"Country/region: {country_en or country or 'unknown'}\n"
        "Return JSON only of the form "
        '{"places":[{"id":"chapter id","query":"Vintgar Gorge, Slovenia",'
        '"nearest_city":"Bled","latitude":46.386,"longitude":14.09}]}.\n'
        "query must be an English OSM/Nominatim name in that country. "
        "If the landmark is uncertain, query MUST be the nearest well-known "
        "city in that country. Always include latitude and longitude as "
        "decimal degrees for that query. Never leave a place empty. "
        "Do not invent a country outside the given region.\n"
        f"{json.dumps(rows, ensure_ascii=False)}\n"
    )
    try:
        settings = load_model_settings(project)
        role = settings.project_brief
        model = resolve_llm_model_id(role.provider, role.model)
        raw = generate_plan_text(
            prompt=prompt,
            model=model,
            max_output_tokens=1200,
            disable_thinking=True,
        )
        payload = _extract_json(raw)
    except Exception:
        return {}
    items = payload.get("places") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return {}
    known = {chapter_id: original for chapter_id, original in places}
    mapping: dict[str, LlmPlaceSuggestion] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        chapter_id = str(item.get("id") or "").strip()
        if chapter_id not in known:
            continue
        query = " ".join(
            str(item.get("query") or item.get("nearest_city") or "").split()
        )
        lat = _parse_llm_coord(item.get("latitude"))
        lon = _parse_llm_coord(item.get("longitude"))
        if lat is not None and not -90.0 <= lat <= 90.0:
            lat = None
        if lon is not None and not -180.0 <= lon <= 180.0:
            lon = None
        iso2 = country_iso2(country)
        if (
            lat is not None
            and lon is not None
            and iso2
            and not _point_in_iso2(lat, lon, iso2)
        ):
            lat = None
            lon = None
        if not query and lat is None:
            continue
        mapping[chapter_id] = LlmPlaceSuggestion(
            query=query,
            latitude=lat,
            longitude=lon,
        )
    return mapping


def rewrite_geocode_queries_with_llm(
    project: Project,
    places: list[tuple[str, str]],
    country: str,
) -> dict[str, str]:
    """Kapitel-Titel → geocodierbarer Ortsname. Nutzt Brief-Modell, nie Cut-Modelle."""
    return {
        chapter_id: suggestion.query
        for chapter_id, suggestion in rewrite_geocode_places_with_llm(
            project, places, country
        ).items()
        if suggestion.query
    }


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
) -> tuple[MapCoordinatesDocument, MapPlanDocument, list[str]]:
    """Resolve chapters that still lack coordinates. Does not render.

    Returns ``(coordinates, rebuilt_plan, errors)``. Successful hits are saved
    to the project coordinate store. Uncertain hits stay stored but keep the
    affected maps blocked until confidence is high enough or the user confirms.
    One failed place never aborts the remaining search. ``llm_rewrite`` asks
    the Brief-Modell for a better place name after Nominatim/Photon/Wikipedia
    miss — never the Cut-Modelle.
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
    pending: list[tuple[str, str, str]] = []
    for chapter_id, original, display in unique_chapter_places(current_plan):
        existing = coords.places.get(chapter_id)
        if existing is not None and existing.has_coordinates:
            report.skipped += 1
            continue
        pending.append((chapter_id, original, display))

    total = len(pending)

    def emit(event: GeocodeProgress) -> None:
        if on_progress is not None:
            on_progress(event)

    def cancelled() -> bool:
        return bool(should_cancel and should_cancel())

    def store_hit(chapter_id: str, original: str, display: str, hit: GeocodeHit) -> None:
        hit = dict(hit)
        hit["original_label"] = original
        if display and not str(hit.get("display_label") or "").strip():
            hit["display_label"] = display
        hits[chapter_id] = hit
        query_hits[_cache_key(original, current_plan.country)] = hit
        if use_nominatim:
            _cache_put(cache_file, original, current_plan.country, hit)
        report.found += 1

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
        reused = _hit_from_existing(coords, original) or query_hits.get(
            _cache_key(original, current_plan.country)
        )
        if reused is None and use_nominatim:
            reused = _cache_get(cache_file, original, current_plan.country)
        if reused is not None:
            hit = _normalize_hit(reused, original_label=original, display_label=display)
            if hit is not None:
                store_hit(chapter_id, original, display, hit)
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

        try:
            raw = resolve_place_coordinates(
                original,
                current_plan.country,
                on_wait=on_wait,
                geocode_fn=geocode_fn,
            )
            hit = _normalize_hit(raw, original_label=original, display_label=display)
        except GeocodeCancelled:
            raise
        except Exception as exc:  # noqa: BLE001 — Ort überspringen, Rest weiter
            reason = friendly_geocode_error(original, exc)
            missed.append((chapter_id, original, display, reason))
            continue
        if hit is None:
            missed.append((chapter_id, original, display, "kein Treffer"))
            continue
        store_hit(chapter_id, original, display, hit)
        emit(
            GeocodeProgress(
                place=original, index=index, total=total, kind="found"
            )
        )

    if missed and llm_rewrite and use_nominatim:
        rows = [
            (chapter_id, original)
            for chapter_id, original, _display, _reason in missed
        ]
        suggestions: dict[str, LlmPlaceSuggestion] = {}
        mapping: dict[str, str] = {}
        try:
            if rewrite_queries_fn is not None:
                mapping = dict(rewrite_queries_fn(rows, current_plan.country) or {})
            else:
                suggestions = rewrite_geocode_places_with_llm(
                    project, rows, current_plan.country
                )
                mapping = {
                    chapter_id: suggestion.query
                    for chapter_id, suggestion in suggestions.items()
                    if suggestion.query
                }
        except GeocodeCancelled:
            raise
        except Exception:
            mapping = {}
        still: list[tuple[str, str, str, str]] = []
        for chapter_id, original, display, reason in missed:
            if cancelled():
                raise GeocodeCancelled("Auto-Lauf gestoppt.")
            query = str(mapping.get(chapter_id) or "").strip()
            hit = None
            if query:
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
                except Exception:
                    hit = None
            if hit is None:
                suggestion = suggestions.get(chapter_id)
                if suggestion is not None and suggestion.has_coordinates:
                    hit = {
                        "latitude": float(suggestion.latitude),
                        "longitude": float(suggestion.longitude),
                        "confidence": 0.72,
                        "ambiguous": True,
                        "original_label": original,
                        "display_label": suggestion.query or display or original,
                        "source": "llm-nearest-city",
                        "country_hint": current_plan.country,
                    }
            if hit is None:
                still.append((chapter_id, original, display, reason))
                continue
            store_hit(chapter_id, original, display, hit)
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
