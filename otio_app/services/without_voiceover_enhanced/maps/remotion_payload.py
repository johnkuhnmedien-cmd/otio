"""Remotion input props from an OTIO map plan item.

No Thomas imports or absolute Thomas paths. Animation modes map
``opening`` → Remotion ``intro`` (ramp zoom) and ``transition`` → quadratic pan.
Visible labels are localized; ``exportLabel`` keeps the dramaturgy original name.

``item.country`` stays the shared family ``video_place`` (geocode / numeric id).
The on-map ``countryLabel`` is translated to the project language here, right
before render — there is no extra LLM step.
"""

from __future__ import annotations

import unicodedata
import uuid
from typing import NamedTuple

from otio_app.services.voiceover_generation.project_brief_defaults_service import (
    normalize_brief_language,
)
from otio_app.services.without_voiceover_enhanced.maps.models import (
    ENGINE_STYLE_VERSION,
    MAP_ANIMATION_OPENING,
    MAP_RESOLUTION_4K,
    MapPlanItem,
)

_LABEL_LANGS = ("EN", "DE", "FR", "IT", "ES", "PT")


class _Country(NamedTuple):
    numeric: str
    labels: dict[str, str]
    aliases: tuple[str, ...] = ()


# Canonical English key → ISO numeric id + display names in brief languages.
_COUNTRIES: dict[str, _Country] = {
    "albania": _Country(
        "008",
        {
            "EN": "Albania",
            "DE": "Albanien",
            "FR": "Albanie",
            "IT": "Albania",
            "ES": "Albania",
            "PT": "Albânia",
        },
    ),
    "austria": _Country(
        "040",
        {
            "EN": "Austria",
            "DE": "Österreich",
            "FR": "Autriche",
            "IT": "Austria",
            "ES": "Austria",
            "PT": "Áustria",
        },
        ("oesterreich",),
    ),
    "belgium": _Country(
        "056",
        {
            "EN": "Belgium",
            "DE": "Belgien",
            "FR": "Belgique",
            "IT": "Belgio",
            "ES": "Bélgica",
            "PT": "Bélgica",
        },
    ),
    "bulgaria": _Country(
        "100",
        {
            "EN": "Bulgaria",
            "DE": "Bulgarien",
            "FR": "Bulgarie",
            "IT": "Bulgaria",
            "ES": "Bulgaria",
            "PT": "Bulgária",
        },
    ),
    "croatia": _Country(
        "191",
        {
            "EN": "Croatia",
            "DE": "Kroatien",
            "FR": "Croatie",
            "IT": "Croazia",
            "ES": "Croacia",
            "PT": "Croácia",
        },
    ),
    "cyprus": _Country(
        "196",
        {
            "EN": "Cyprus",
            "DE": "Zypern",
            "FR": "Chypre",
            "IT": "Cipro",
            "ES": "Chipre",
            "PT": "Chipre",
        },
    ),
    "czechia": _Country(
        "203",
        {
            "EN": "Czechia",
            "DE": "Tschechien",
            "FR": "Tchéquie",
            "IT": "Cechia",
            "ES": "Chequia",
            "PT": "Chéquia",
        },
        ("czech republic", "cesko", "tschechische republik"),
    ),
    "denmark": _Country(
        "208",
        {
            "EN": "Denmark",
            "DE": "Dänemark",
            "FR": "Danemark",
            "IT": "Danimarca",
            "ES": "Dinamarca",
            "PT": "Dinamarca",
        },
    ),
    "egypt": _Country(
        "818",
        {
            "EN": "Egypt",
            "DE": "Ägypten",
            "FR": "Égypte",
            "IT": "Egitto",
            "ES": "Egipto",
            "PT": "Egito",
        },
        ("aegypten",),
    ),
    "finland": _Country(
        "246",
        {
            "EN": "Finland",
            "DE": "Finnland",
            "FR": "Finlande",
            "IT": "Finlandia",
            "ES": "Finlandia",
            "PT": "Finlândia",
        },
    ),
    "france": _Country(
        "250",
        {
            "EN": "France",
            "DE": "Frankreich",
            "FR": "France",
            "IT": "Francia",
            "ES": "Francia",
            "PT": "França",
        },
    ),
    "germany": _Country(
        "276",
        {
            "EN": "Germany",
            "DE": "Deutschland",
            "FR": "Allemagne",
            "IT": "Germania",
            "ES": "Alemania",
            "PT": "Alemanha",
        },
    ),
    "greece": _Country(
        "300",
        {
            "EN": "Greece",
            "DE": "Griechenland",
            "FR": "Grèce",
            "IT": "Grecia",
            "ES": "Grecia",
            "PT": "Grécia",
        },
        ("hellas", "ellada"),
    ),
    "hungary": _Country(
        "348",
        {
            "EN": "Hungary",
            "DE": "Ungarn",
            "FR": "Hongrie",
            "IT": "Ungheria",
            "ES": "Hungría",
            "PT": "Hungria",
        },
    ),
    "iceland": _Country(
        "352",
        {
            "EN": "Iceland",
            "DE": "Island",
            "FR": "Islande",
            "IT": "Islanda",
            "ES": "Islandia",
            "PT": "Islândia",
        },
    ),
    "ireland": _Country(
        "372",
        {
            "EN": "Ireland",
            "DE": "Irland",
            "FR": "Irlande",
            "IT": "Irlanda",
            "ES": "Irlanda",
            "PT": "Irlanda",
        },
        ("eire",),
    ),
    "italy": _Country(
        "380",
        {
            "EN": "Italy",
            "DE": "Italien",
            "FR": "Italie",
            "IT": "Italia",
            "ES": "Italia",
            "PT": "Itália",
        },
    ),
    "malta": _Country(
        "470",
        {
            "EN": "Malta",
            "DE": "Malta",
            "FR": "Malte",
            "IT": "Malta",
            "ES": "Malta",
            "PT": "Malta",
        },
    ),
    "montenegro": _Country(
        "499",
        {
            "EN": "Montenegro",
            "DE": "Montenegro",
            "FR": "Monténégro",
            "IT": "Montenegro",
            "ES": "Montenegro",
            "PT": "Montenegro",
        },
        ("crna gora",),
    ),
    "morocco": _Country(
        "504",
        {
            "EN": "Morocco",
            "DE": "Marokko",
            "FR": "Maroc",
            "IT": "Marocco",
            "ES": "Marruecos",
            "PT": "Marrocos",
        },
    ),
    "netherlands": _Country(
        "528",
        {
            "EN": "Netherlands",
            "DE": "Niederlande",
            "FR": "Pays-Bas",
            "IT": "Paesi Bassi",
            "ES": "Países Bajos",
            "PT": "Países Baixos",
        },
        ("holland",),
    ),
    "norway": _Country(
        "578",
        {
            "EN": "Norway",
            "DE": "Norwegen",
            "FR": "Norvège",
            "IT": "Norvegia",
            "ES": "Noruega",
            "PT": "Noruega",
        },
    ),
    "poland": _Country(
        "616",
        {
            "EN": "Poland",
            "DE": "Polen",
            "FR": "Pologne",
            "IT": "Polonia",
            "ES": "Polonia",
            "PT": "Polónia",
        },
    ),
    "portugal": _Country(
        "620",
        {
            "EN": "Portugal",
            "DE": "Portugal",
            "FR": "Portugal",
            "IT": "Portogallo",
            "ES": "Portugal",
            "PT": "Portugal",
        },
    ),
    "romania": _Country(
        "642",
        {
            "EN": "Romania",
            "DE": "Rumänien",
            "FR": "Roumanie",
            "IT": "Romania",
            "ES": "Rumanía",
            "PT": "Roménia",
        },
    ),
    "slovenia": _Country(
        "705",
        {
            "EN": "Slovenia",
            "DE": "Slowenien",
            "FR": "Slovénie",
            "IT": "Slovenia",
            "ES": "Eslovenia",
            "PT": "Eslovénia",
        },
    ),
    "spain": _Country(
        "724",
        {
            "EN": "Spain",
            "DE": "Spanien",
            "FR": "Espagne",
            "IT": "Spagna",
            "ES": "España",
            "PT": "Espanha",
        },
        ("espana",),
    ),
    "sweden": _Country(
        "752",
        {
            "EN": "Sweden",
            "DE": "Schweden",
            "FR": "Suède",
            "IT": "Svezia",
            "ES": "Suecia",
            "PT": "Suécia",
        },
    ),
    "switzerland": _Country(
        "756",
        {
            "EN": "Switzerland",
            "DE": "Schweiz",
            "FR": "Suisse",
            "IT": "Svizzera",
            "ES": "Suiza",
            "PT": "Suíça",
        },
    ),
    "turkey": _Country(
        "792",
        {
            "EN": "Turkey",
            "DE": "Türkei",
            "FR": "Turquie",
            "IT": "Turchia",
            "ES": "Turquía",
            "PT": "Turquia",
        },
        ("turkiye",),
    ),
    "united kingdom": _Country(
        "826",
        {
            "EN": "United Kingdom",
            "DE": "Vereinigtes Königreich",
            "FR": "Royaume-Uni",
            "IT": "Regno Unito",
            "ES": "Reino Unido",
            "PT": "Reino Unido",
        },
        ("uk", "great britain", "britain", "england"),
    ),
    "united states": _Country(
        "840",
        {
            "EN": "USA",
            "DE": "USA",
            "FR": "États-Unis",
            "IT": "Stati Uniti",
            "ES": "Estados Unidos",
            "PT": "Estados Unidos",
        },
        ("usa", "us", "america", "united states of america"),
    ),
}


def _norm_country_key(value: str) -> str:
    stripped = unicodedata.normalize("NFKD", str(value or ""))
    ascii_only = stripped.encode("ascii", "ignore").decode("ascii")
    return " ".join(ascii_only.casefold().split())


def _build_alias_index() -> dict[str, str]:
    index: dict[str, str] = {}
    for canonical, meta in _COUNTRIES.items():
        index[_norm_country_key(canonical)] = canonical
        for label in meta.labels.values():
            index[_norm_country_key(label)] = canonical
        for alias in meta.aliases:
            index[_norm_country_key(alias)] = canonical
    return index


_COUNTRY_ALIASES = _build_alias_index()


def canonical_country_key(country: str) -> str | None:
    return _COUNTRY_ALIASES.get(_norm_country_key(country))


def remotion_uuid(value: str, *, namespace: str) -> str:
    text = str(value or "").strip()
    try:
        return str(uuid.UUID(text))
    except ValueError:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{namespace}:{text}"))


def country_numeric_id(country: str) -> str:
    canonical = canonical_country_key(country)
    if canonical is not None:
        return _COUNTRIES[canonical].numeric
    key = _norm_country_key(country)
    if not key:
        return "000"
    for canonical, meta in _COUNTRIES.items():
        needles = [canonical, *meta.labels.values(), *meta.aliases]
        for needle in needles:
            normalized = _norm_country_key(needle)
            if len(normalized) >= 5 and normalized in key:
                return meta.numeric
    return "000"


def country_label(country: str, language: str = "DE") -> str:
    raw = str(country or "").strip() or "Map"
    canonical = canonical_country_key(raw)
    if canonical is None:
        return raw[:100]
    labels = _COUNTRIES[canonical].labels
    lang = normalize_brief_language(language)
    if lang not in _LABEL_LANGS:
        lang = "EN"
    return (labels.get(lang) or labels.get("EN") or raw)[:100]


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
        "countryLabel": country_label(item.country, item.language),
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
