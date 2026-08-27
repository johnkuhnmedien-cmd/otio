"""Remotion input props from an OTIO map plan item.

No Thomas imports or absolute Thomas paths. Animation modes map
``opening`` → Remotion ``intro`` (ramp zoom) and ``transition`` → quadratic pan.
Visible labels are localized; ``exportLabel`` keeps the dramaturgy original name.
"""

from __future__ import annotations

import uuid

from otio_app.services.without_voiceover_enhanced.maps.models import (
    ENGINE_STYLE_VERSION,
    MAP_ANIMATION_OPENING,
    MAP_RESOLUTION_4K,
    MapPlanItem,
)

_COUNTRY_NUMERIC: dict[str, str] = {
    "usa": "840",
    "united states": "840",
    "united states of america": "840",
    "america": "840",
    "ireland": "372",
    "eire": "372",
    "éire": "372",
    "greece": "300",
    "griechenland": "300",
    "hellas": "300",
    "germany": "276",
    "deutschland": "276",
    "france": "250",
    "frankreich": "250",
    "italy": "380",
    "italien": "380",
    "italia": "380",
    "spain": "724",
    "spanien": "724",
    "espana": "724",
    "españa": "724",
    "portugal": "620",
    "united kingdom": "826",
    "uk": "826",
    "great britain": "826",
    "austria": "040",
    "österreich": "040",
    "oesterreich": "040",
    "switzerland": "756",
    "schweiz": "756",
    "slovenia": "705",
    "slowenien": "705",
    "hungary": "348",
    "ungarn": "348",
}

# English label + ISO2 for Nominatim/Photon. Keys are lowercase aliases.
_COUNTRY_ENGLISH: dict[str, str] = {
    "albania": "Albania",
    "albanien": "Albania",
    "austria": "Austria",
    "österreich": "Austria",
    "oesterreich": "Austria",
    "belgium": "Belgium",
    "belgien": "Belgium",
    "bulgaria": "Bulgaria",
    "bulgarien": "Bulgaria",
    "croatia": "Croatia",
    "kroatien": "Croatia",
    "cyprus": "Cyprus",
    "zypern": "Cyprus",
    "czechia": "Czechia",
    "czech republic": "Czechia",
    "tschechien": "Czechia",
    "denmark": "Denmark",
    "dänemark": "Denmark",
    "egypt": "Egypt",
    "ägypten": "Egypt",
    "finland": "Finland",
    "finnland": "Finland",
    "france": "France",
    "frankreich": "France",
    "germany": "Germany",
    "deutschland": "Germany",
    "greece": "Greece",
    "griechenland": "Greece",
    "hellas": "Greece",
    "hungary": "Hungary",
    "ungarn": "Hungary",
    "iceland": "Iceland",
    "island": "Iceland",
    "ireland": "Ireland",
    "irland": "Ireland",
    "eire": "Ireland",
    "italy": "Italy",
    "italien": "Italy",
    "italia": "Italy",
    "malta": "Malta",
    "montenegro": "Montenegro",
    "morocco": "Morocco",
    "marokko": "Morocco",
    "netherlands": "Netherlands",
    "niederlande": "Netherlands",
    "holland": "Netherlands",
    "norway": "Norway",
    "norwegen": "Norway",
    "poland": "Poland",
    "polen": "Poland",
    "portugal": "Portugal",
    "romania": "Romania",
    "rumänien": "Romania",
    "slovenia": "Slovenia",
    "slowenien": "Slovenia",
    "spain": "Spain",
    "spanien": "Spain",
    "sweden": "Sweden",
    "schweden": "Sweden",
    "switzerland": "Switzerland",
    "schweiz": "Switzerland",
    "turkey": "Turkey",
    "türkei": "Turkey",
    "united kingdom": "United Kingdom",
    "uk": "United Kingdom",
    "great britain": "United Kingdom",
    "united states": "United States",
    "usa": "United States",
}

_ISO2_BY_ENGLISH: dict[str, str] = {
    "albania": "al",
    "austria": "at",
    "belgium": "be",
    "bulgaria": "bg",
    "croatia": "hr",
    "cyprus": "cy",
    "czechia": "cz",
    "denmark": "dk",
    "egypt": "eg",
    "finland": "fi",
    "france": "fr",
    "germany": "de",
    "greece": "gr",
    "hungary": "hu",
    "iceland": "is",
    "ireland": "ie",
    "italy": "it",
    "malta": "mt",
    "montenegro": "me",
    "morocco": "ma",
    "netherlands": "nl",
    "norway": "no",
    "poland": "pl",
    "portugal": "pt",
    "romania": "ro",
    "slovenia": "si",
    "spain": "es",
    "sweden": "se",
    "switzerland": "ch",
    "turkey": "tr",
    "united kingdom": "gb",
    "united states": "us",
}


def remotion_uuid(value: str, *, namespace: str) -> str:
    text = str(value or "").strip()
    try:
        return str(uuid.UUID(text))
    except ValueError:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{namespace}:{text}"))


def country_numeric_id(country: str) -> str:
    key = str(country or "").strip().lower()
    if key in _COUNTRY_NUMERIC:
        return _COUNTRY_NUMERIC[key]
    for name, code in _COUNTRY_NUMERIC.items():
        if name and name in key:
            return code
    return "000"


def country_iso2(country: str) -> str:
    """ISO 3166-1 alpha-2 for Nominatim/Photon, or empty if unknown."""
    raw = str(country or "").strip().lower()
    if not raw:
        return ""
    english = _COUNTRY_ENGLISH.get(raw, "")
    canonical = (english or raw).strip().lower()
    return _ISO2_BY_ENGLISH.get(canonical, "")


def country_label(country: str, language: str | None = None) -> str:
    """Karten-Overlay: Rohname. Mit ``language='EN'``: englischer Nominatim-Name."""
    raw = str(country or "").strip() or "Map"
    if language is None:
        return raw[:100]
    if str(language).strip().upper() == "EN":
        mapped = _COUNTRY_ENGLISH.get(raw.casefold())
        return (mapped or raw)[:100]
    return raw[:100]


def _clip_label(value: str, limit: int) -> str:
    text = str(value or "").strip() or "Kapitel"
    return text[:limit]


def view_bounds(
    numeric_id: str,
    start_longitude: float,
    start_latitude: float,
    end_longitude: float,
    end_latitude: float,
) -> list[list[float]]:
    if numeric_id == "840":
        return [[-125.0, 24.0], [-66.0, 50.0]]
    if numeric_id == "372":
        return [[-11.2, 51.15], [-5.05, 55.85]]
    west = min(start_longitude, end_longitude)
    east = max(start_longitude, end_longitude)
    south = min(start_latitude, end_latitude)
    north = max(start_latitude, end_latitude)
    longitude_padding = max(6.0, (east - west) * 0.65)
    latitude_padding = max(4.0, (north - south) * 0.8)
    return [
        [max(-180.0, west - longitude_padding), max(-85.0, south - latitude_padding)],
        [min(180.0, east + longitude_padding), min(85.0, north + latitude_padding)],
    ]


def remotion_payload(item: MapPlanItem) -> dict:
    if item.start_latitude is None or item.start_longitude is None:
        raise ValueError(f"Startkoordinaten fehlen für {item.chapter_id}")
    if item.end_latitude is None or item.end_longitude is None:
        raise ValueError(f"Zielkoordinaten fehlen für {item.chapter_id}")
    is_opening = item.animation_mode == MAP_ANIMATION_OPENING
    if is_opening:
        from_raw = item.localized_display_label
    else:
        from_raw = item.from_localized_display_label or item.localized_display_label
    from_label = _clip_label(from_raw, 100)
    to_label = _clip_label(item.localized_display_label, 100)
    numeric_id = country_numeric_id(item.country).zfill(3)[:3]
    resolution = "4k" if item.resolution == MAP_RESOLUTION_4K else "hd"
    return {
        "mapSequenceId": remotion_uuid(item.map_sequence_id, namespace="otio-map-seq"),
        "projectId": remotion_uuid(item.project_id, namespace="otio-map-project"),
        "chapterId": _clip_label(item.chapter_id, 200),
        "from": {
            "label": from_label,
            "longitude": float(item.start_longitude),
            "latitude": float(item.start_latitude),
        },
        "to": {
            "label": to_label,
            "longitude": float(item.end_longitude),
            "latitude": float(item.end_latitude),
        },
        "exportLabel": _clip_label(item.original_chapter_label, 200),
        "countryNumericId": numeric_id,
        "countryLabel": country_label(item.country),
        "language": (str(item.language or "DE").strip().upper()[:20] or "DE"),
        "chapterOrdinal": int(item.chapter_ordinal),
        "chapterCount": int(item.chapter_count),
        "animationMode": "intro" if is_opening else "transition",
        "transportMode": "car",
        "showVehicle": bool(item.show_vehicle) and not is_opening,
        "routeKind": (
            "deterministic_ramp_zoom" if is_opening else "deterministic_quadratic_curve"
        ),
        "durationInFrames": int(item.duration_in_frames),
        "fps": int(item.fps),
        "outputResolution": resolution,
        "outputWidth": int(item.width),
        "outputHeight": int(item.height),
        "seed": remotion_uuid(item.map_sequence_id, namespace="otio-map-seq"),
        "styleVersion": ENGINE_STYLE_VERSION,
        "viewBounds": view_bounds(
            numeric_id,
            float(item.start_longitude),
            float(item.start_latitude),
            float(item.end_longitude),
            float(item.end_latitude),
        ),
    }
