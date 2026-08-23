"""Land/Region → Suchraum für Karten-Koordinaten.

``video_place`` kann ein Land (Spanien) oder ein Kontinent (Europa) sein.
Nominatim darf den Kontinentnamen nicht als Orts-Suffix verwenden — sonst wird
aus „Granadilla, Europa“ die Urbanización La Europa in Costa Rica.
"""

from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass

from otio_app.services.without_voiceover_enhanced.maps.remotion_payload import (
    canonical_country_key,
    country_iso2,
    country_label,
)

# south, west, north, east
BBox = tuple[float, float, float, float]

# Osten bis Kaukasus (Dagestan ~47°E). Finnland bleibt im Kasten — LLM prüft Namesakes.
EUROPE_BBOX: BBox = (34.0, -25.0, 72.0, 62.0)
ASIA_BBOX: BBox = (-10.0, 26.0, 77.0, 180.0)
AFRICA_BBOX: BBox = (-35.0, -18.0, 38.0, 52.0)
NORTH_AMERICA_BBOX: BBox = (14.0, -170.0, 72.0, -50.0)
SOUTH_AMERICA_BBOX: BBox = (-56.0, -82.0, 13.0, -34.0)

# Nominatim countrycodes — kommagetrennt, nur Europa (inkl. Reiseziele TR/CY/IS).
EUROPE_COUNTRYCODES = (
    "al,ad,am,at,az,ba,be,bg,by,ch,cy,cz,de,dk,ee,es,fi,fr,gb,ge,gr,hr,hu,"
    "ie,is,it,li,lt,lu,lv,mc,md,me,mk,mt,nl,no,pl,pt,ro,rs,ru,se,si,sk,sm,"
    "tr,ua,xk"
)
EUROPE_ISO2 = frozenset(
    code.strip() for code in EUROPE_COUNTRYCODES.split(",") if code.strip()
)

_CONTINENT_ALIASES: dict[str, str] = {
    "europa": "europe",
    "europe": "europe",
    "europaische union": "europe",
    "european union": "europe",
    "asien": "asia",
    "asia": "asia",
    "afrika": "africa",
    "africa": "africa",
    "nordamerika": "north_america",
    "north america": "north_america",
    "sudamerika": "south_america",
    "south america": "south_america",
}

_CONTINENT_META: dict[str, tuple[BBox, str, str]] = {
    "europe": (EUROPE_BBOX, "Europe", EUROPE_COUNTRYCODES),
    "asia": (ASIA_BBOX, "Asia", ""),
    "africa": (AFRICA_BBOX, "Africa", ""),
    "north_america": (NORTH_AMERICA_BBOX, "North America", ""),
    "south_america": (SOUTH_AMERICA_BBOX, "South America", ""),
}

_REGION_TO_COUNTRY: dict[str, str] = {
    "extremadura": "spain",
    "andalucia": "spain",
    "andalusia": "spain",
    "andalusien": "spain",
    "kastilien": "spain",
    "castile": "spain",
    "castilla": "spain",
    "aragon": "spain",
    "catalonia": "spain",
    "katalonien": "spain",
    "valencia": "spain",
    "galicia": "spain",
    "galicien": "spain",
    "tuscany": "italy",
    "toskana": "italy",
    "sicily": "italy",
    "sizilien": "italy",
    "bavaria": "germany",
    "bayern": "germany",
    "provence": "france",
    "bretagne": "france",
    "brittany": "france",
}

# Geocode-only (nicht Remotion-Länderkatalog): Kaukasus / Russland.
_EXTRA_ISO2: dict[str, str] = {
    "russia": "ru",
    "russland": "ru",
    "russian federation": "ru",
    "dagestan": "ru",
    "nordkaukasus": "ru",
    "north caucasus": "ru",
    "caucasus": "ru",
    "kaukasus": "ru",
    "georgia": "ge",
    "georgien": "ge",
    "armenia": "am",
    "armenien": "am",
    "azerbaijan": "az",
    "aserbaidschan": "az",
}
_EXTRA_LABEL_EN: dict[str, str] = {
    "ru": "Russia",
    "ge": "Georgia",
    "am": "Armenia",
    "az": "Azerbaijan",
}

LLM_COORD_AGREE_KM = 150.0


def _norm(value: str) -> str:
    stripped = unicodedata.normalize("NFKD", str(value or ""))
    ascii_only = stripped.encode("ascii", "ignore").decode("ascii")
    return " ".join(ascii_only.casefold().split())


@dataclass(frozen=True)
class GeocodeScope:
    raw: str
    kind: str
    canonical: str
    iso2: str
    countrycodes: str
    query_suffix: str
    bbox: BBox | None
    label_en: str

    @property
    def has_constraint(self) -> bool:
        return bool(self.countrycodes or self.bbox)


def resolve_geocode_scope(video_place: str) -> GeocodeScope:
    """Macht aus Land/Region einen Suchraum (Land oder Kontinent)."""
    raw = str(video_place or "").strip()
    key = _norm(raw)
    continent = _CONTINENT_ALIASES.get(key)
    if continent and continent in _CONTINENT_META:
        bbox, label_en, codes = _CONTINENT_META[continent]
        return GeocodeScope(
            raw=raw,
            kind="continent",
            canonical=continent,
            iso2="",
            countrycodes=codes,
            query_suffix="",
            bbox=bbox,
            label_en=label_en,
        )

    region_country = _REGION_TO_COUNTRY.get(key)
    if region_country:
        iso2 = country_iso2(region_country)
        label = country_label(region_country, "EN")
        return GeocodeScope(
            raw=raw,
            kind="country",
            canonical=region_country,
            iso2=iso2,
            countrycodes=iso2,
            query_suffix=label,
            bbox=EUROPE_BBOX if iso2 in EUROPE_ISO2 else None,
            label_en=label,
        )

    extra_iso = _EXTRA_ISO2.get(key)
    if extra_iso:
        label = _EXTRA_LABEL_EN.get(extra_iso, raw)
        return GeocodeScope(
            raw=raw,
            kind="country",
            canonical=extra_iso,
            iso2=extra_iso,
            countrycodes=extra_iso,
            query_suffix=label,
            bbox=EUROPE_BBOX,
            label_en=label,
        )

    iso2 = country_iso2(raw)
    if iso2:
        canonical = canonical_country_key(raw) or ""
        label = country_label(raw, "EN")
        if label.casefold() == "map":
            label = raw
        bbox = EUROPE_BBOX if iso2 in EUROPE_ISO2 else None
        if iso2 == "us":
            bbox = NORTH_AMERICA_BBOX
        return GeocodeScope(
            raw=raw,
            kind="country",
            canonical=canonical,
            iso2=iso2,
            countrycodes=iso2,
            query_suffix=label,
            bbox=bbox,
            label_en=label,
        )

    return GeocodeScope(
        raw=raw,
        kind="unknown",
        canonical="",
        iso2="",
        countrycodes="",
        query_suffix="",
        bbox=None,
        label_en=raw or "unknown",
    )


def coordinates_in_scope(
    latitude: float | None,
    longitude: float | None,
    scope: GeocodeScope,
) -> bool:
    if scope.bbox is None:
        return True
    if latitude is None or longitude is None:
        return False
    try:
        lat = float(latitude)
        lon = float(longitude)
    except (TypeError, ValueError):
        return False
    south, west, north, east = scope.bbox
    if lat < south or lat > north:
        return False
    if west <= east:
        return west <= lon <= east
    return lon >= west or lon <= east


def country_code_in_scope(country_code: str, scope: GeocodeScope) -> bool:
    code = str(country_code or "").strip().lower()
    if not code:
        return True
    if scope.iso2:
        return code == scope.iso2
    if scope.countrycodes:
        allowed = {
            item.strip() for item in scope.countrycodes.split(",") if item.strip()
        }
        return code in allowed
    return True


def hit_in_scope(
    *,
    latitude: float | None,
    longitude: float | None,
    country_code: str = "",
    scope: GeocodeScope,
) -> bool:
    if not scope.has_constraint:
        return True
    if not country_code_in_scope(country_code, scope):
        return False
    return coordinates_in_scope(latitude, longitude, scope)


def haversine_km(
    lat1: float | None,
    lon1: float | None,
    lat2: float | None,
    lon2: float | None,
) -> float | None:
    """Großkreis-Abstand in km, oder None wenn eine Koordinate fehlt."""
    try:
        a = float(lat1)
        b = float(lon1)
        c = float(lat2)
        d = float(lon2)
    except (TypeError, ValueError):
        return None
    if any(math.isnan(value) for value in (a, b, c, d)):
        return None
    phi1, phi2 = math.radians(a), math.radians(c)
    d_phi = math.radians(c - a)
    d_lambda = math.radians(d - b)
    chord = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return 6371.0 * 2 * math.atan2(math.sqrt(chord), math.sqrt(max(0.0, 1.0 - chord)))


def coordinates_disagree(
    lat1: float | None,
    lon1: float | None,
    lat2: float | None,
    lon2: float | None,
    *,
    max_km: float = LLM_COORD_AGREE_KM,
) -> bool:
    distance = haversine_km(lat1, lon1, lat2, lon2)
    return distance is not None and distance > max_km


def pin_display_label(chapter_label: str, osm_name: str) -> str:
    """Kartenpin: Kapitelname, außer der OSM-Name teilt ein sinnvolles Token."""
    chapter = " ".join(str(chapter_label or "").split())
    osm = " ".join(str(osm_name or "").split())
    if not chapter:
        return osm
    if not osm:
        return chapter
    stop = {
        "the",
        "el",
        "la",
        "le",
        "les",
        "de",
        "del",
        "von",
        "der",
        "die",
        "das",
        "urb",
        "urbanizacion",
        "urbanización",
    }
    chapter_tokens = {
        token
        for token in chapter.casefold().replace(":", " ").replace(".", " ").split()
        if token not in stop
    }
    osm_tokens = {
        token
        for token in osm.casefold().replace(":", " ").replace(".", " ").split()
        if token not in stop
    }
    if chapter_tokens and osm_tokens and chapter_tokens & osm_tokens:
        return osm if len(osm) <= 48 else chapter
    return chapter
