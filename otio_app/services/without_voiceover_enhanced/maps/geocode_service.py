"""On-demand place lookup for map cards.

Geocoding never runs on project open. Tests inject ``geocode_fn`` so the
default Nominatim client is not required in CI.

Nominatim usage: one request per second, identifying User-Agent, local cache,
skip places that already have coordinates, retry HTTP 429 with increasing
pauses. A failure for one place never aborts the remaining search.
"""

from __future__ import annotations

import json
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

GeocodeFn = Callable[[str, str], GeocodeHit | tuple[float, float, float] | None]
GeocodeWaitFn = Callable[[float, str], None]
GeocodeProgressFn = Callable[["GeocodeProgress"], None]

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_USER_AGENT = (
    "OTIO-Schnittplaner/0.1 "
    "(https://github.com/johnkuhnmedien-cmd/otio; maps-geocode)"
)
NOMINATIM_TIMEOUT_SEC = 20.0
NOMINATIM_MIN_INTERVAL_SEC = 1.0
NOMINATIM_MAX_429_RETRIES = 5
NOMINATIM_429_BASE_PAUSE_SEC = 1.0
NOMINATIM_429_MAX_PAUSE_SEC = 30.0

_rate_lock = threading.Lock()
_cache_lock = threading.Lock()
_last_request_monotonic = 0.0
_sleep = time.sleep
_monotonic = time.monotonic


class GeocodeError(RuntimeError):
    """Raised when a single place lookup fails."""


class GeocodeRateLimited(GeocodeError):
    """Nominatim answered HTTP 429 after retries."""


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


def _parse_nominatim_payload(payload: Any, place: str) -> GeocodeHit:
    if not isinstance(payload, list) or not payload:
        raise GeocodeError(f"no Nominatim hit for {place!r}")
    hit = payload[0]
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
    if len(payload) > 1:
        try:
            second = float(payload[1].get("importance", 0.0) or 0.0)
        except (TypeError, ValueError):
            second = 0.0
        if second >= max(0.4, confidence * 0.85):
            ambiguous = True
            confidence = min(confidence, 0.6)
    return {
        "latitude": lat,
        "longitude": lon,
        "confidence": confidence,
        "ambiguous": ambiguous,
        "original_label": place,
        "display_label": place,
        "source": "nominatim",
    }


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
    if hint:
        query = f"{query}, {hint}"
    last_error: BaseException | None = None
    for attempt in range(NOMINATIM_MAX_429_RETRIES + 1):
        _wait_for_nominatim_slot(on_wait)
        try:
            response = requests.get(
                NOMINATIM_URL,
                params={"q": query, "format": "json", "limit": 2},
                headers={
                    "User-Agent": NOMINATIM_USER_AGENT,
                    "Accept-Language": "en",
                },
                timeout=NOMINATIM_TIMEOUT_SEC,
            )
        except requests.Timeout as exc:
            raise GeocodeError(f"Nominatim request failed for {place!r}: timeout") from exc
        except requests.RequestException as exc:
            raise GeocodeError(f"Nominatim request failed for {place!r}: {type(exc).__name__}") from exc
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
        hit = _parse_nominatim_payload(payload, place)
        hit["country_hint"] = hint
        return hit
    if last_error is not None:
        raise last_error
    raise GeocodeError(f"Nominatim request failed for {place!r}")


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
) -> tuple[MapCoordinatesDocument, MapPlanDocument, list[str]]:
    """Resolve chapters that still lack coordinates. Does not render.

    Returns ``(coordinates, rebuilt_plan, errors)``. Successful hits are saved
    to the project coordinate store. Uncertain hits stay stored but keep the
    affected maps blocked until confidence is high enough or the user confirms.
    One failed place never aborts the rest.
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

    if total == 0:
        emit(GeocodeProgress(place="", index=0, total=0, kind="skipped"))
        rebuilt = build_map_plan(
            project,
            settings=resolved_settings,
            coordinates=coords,
            previous=current_plan,
        )
        return coords, rebuilt, report.errors

    for index, (chapter_id, original, display) in enumerate(pending, start=1):
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
                hits[chapter_id] = hit
                query_hits[_cache_key(original, current_plan.country)] = hit
                report.found += 1
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
            if use_nominatim:
                raw = nominatim_geocode(
                    original, current_plan.country, on_wait=on_wait
                )
            else:
                assert geocode_fn is not None
                raw = geocode_fn(original, current_plan.country)
            hit = _normalize_hit(raw, original_label=original, display_label=display)
        except Exception as exc:  # noqa: BLE001 — Ort überspringen, Rest weiter
            reason = friendly_geocode_error(original, exc)
            report.failed += 1
            report.errors.append(f"{original}: {reason}")
            emit(
                GeocodeProgress(
                    place=original,
                    index=index,
                    total=total,
                    kind="error",
                    detail=reason,
                )
            )
            continue
        if hit is None:
            reason = "kein Treffer"
            report.failed += 1
            report.errors.append(f"{original}: {reason}")
            emit(
                GeocodeProgress(
                    place=original, index=index, total=total, kind="missing"
                )
            )
            continue
        hits[chapter_id] = hit
        query_hits[_cache_key(original, current_plan.country)] = hit
        if use_nominatim:
            _cache_put(cache_file, original, current_plan.country, hit)
        report.found += 1
        emit(
            GeocodeProgress(
                place=original, index=index, total=total, kind="found"
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
