"""On-demand place lookup for map cards.

Geocoding never runs on project open. Tests inject ``geocode_fn`` so the
default Nominatim client is not required in CI.
"""

from __future__ import annotations

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

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_USER_AGENT = "OTIO-Enhanced-Maps/1.0 (local research tool)"
NOMINATIM_TIMEOUT_SEC = 20.0


class GeocodeError(RuntimeError):
    """Raised when a single place lookup fails."""


def nominatim_geocode(place: str, country_hint: str = "") -> GeocodeHit:
    """Resolve ``place`` via Nominatim. Raises ``GeocodeError`` on failure."""
    query = str(place or "").strip()
    if not query:
        raise GeocodeError("empty place name")
    hint = str(country_hint or "").strip()
    if hint:
        query = f"{query}, {hint}"
    try:
        response = requests.get(
            NOMINATIM_URL,
            params={"q": query, "format": "json", "limit": 2},
            headers={"User-Agent": NOMINATIM_USER_AGENT, "Accept-Language": "en"},
            timeout=NOMINATIM_TIMEOUT_SEC,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        raise GeocodeError(f"Nominatim request failed for {place!r}: {exc}") from exc
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
        "country_hint": hint,
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


def lookup_missing_coordinates(
    project: Project,
    *,
    settings: MapRenderSettings | None = None,
    plan: MapPlanDocument | None = None,
    coordinates: MapCoordinatesDocument | None = None,
    geocode_fn: GeocodeFn | None = None,
) -> tuple[MapCoordinatesDocument, MapPlanDocument, list[str]]:
    """Resolve chapters that still lack coordinates. Does not render.

    Returns ``(coordinates, rebuilt_plan, errors)``. Successful hits are saved
    to the project coordinate store. Uncertain hits stay stored but keep the
    affected maps blocked until confidence is high enough or the user confirms.
    """
    resolved_settings = settings or load_map_settings(project)
    current_plan = plan or load_map_plan(project)
    if current_plan is None:
        current_plan = build_map_plan(project, settings=resolved_settings, coordinates=coordinates)
    coords = coordinates or load_map_coordinates(project)
    fn = geocode_fn or nominatim_geocode
    errors: list[str] = []
    hits: dict[str, GeocodeHit] = {}
    for chapter_id, original, display in unique_chapter_places(current_plan):
        existing = coords.places.get(chapter_id)
        if existing is not None and existing.has_coordinates:
            continue
        try:
            raw = fn(original, current_plan.country)
            hit = _normalize_hit(raw, original_label=original, display_label=display)
        except Exception as exc:
            errors.append(f"{original}: {exc}")
            continue
        if hit is None:
            errors.append(f"{original}: no geocoder hit")
            continue
        hits[chapter_id] = hit
    if hits:
        coords = apply_geocode_hits(project, hits)
    rebuilt = build_map_plan(
        project,
        settings=resolved_settings,
        coordinates=coords,
        previous=current_plan,
    )
    return coords, rebuilt, errors
