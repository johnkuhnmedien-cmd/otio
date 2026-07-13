"""Geografie-Hinweise für Kapitel-Karten-Pins (USA-Schwerpunkt).

Das Image-Modell platziert Pins zuverlässiger, wenn es explizite Regions-/
Bundesstaat-Hinweise bekommt — reine Ortsnamen allein reichen oft nicht.
"""

from __future__ import annotations

# Bekannte USA-Locations aus typischen YT-Reisedokus. Keys normalisiert lowercase.
_US_LOCATION_HINTS: dict[str, str] = {
    "antelope canyon": "Northern Arizona, USA (near Page, AZ) — south of Lake Powell, east of Grand Canyon",
    "niagara falls": "Far western New York / Ontario border — northeastern USA, near Buffalo NY",
    "monument valley": "Arizona–Utah border (Navajo Nation) — Four Corners region, northeast Arizona",
    "caddo lake": "East Texas / northwest Louisiana border — far eastern Texas",
    "denali national park": "Interior Alaska — south-central Alaska, well north of Anchorage",
    "havasu falls": "Northwest Arizona, Grand Canyon region (Supai / Havasupai) — western Grand Canyon",
    "badlands national park": "Southwestern South Dakota — northern Great Plains",
    "san francisco": "Central California coast — San Francisco Bay Area",
    "the wave": "Northern Arizona near Utah border (Coyote Buttes) — south of Kanab/Page corridor",
    "apostle islands": "Northern Wisconsin on Lake Superior — far north-central USA",
    "yellowstone": "Northwest Wyoming (also touches MT/ID) — northern Rockies",
    "grand canyon": "Northern Arizona — Colorado Plateau",
    "zion national park": "Southwestern Utah — near Arizona border",
    "bryce canyon": "Southern Utah — east of Zion",
    "yosemite": "Central California Sierra Nevada",
    "death valley": "Eastern California / Nevada border — Mojave Desert",
    "miami": "Southeast Florida — southern tip of Florida peninsula",
    "new york": "Southeastern New York State — Northeast USA Atlantic coast",
    "las vegas": "Southern Nevada — Mojave Desert",
    "seattle": "Western Washington State — Pacific Northwest",
    "chicago": "Northeast Illinois — southern shore of Lake Michigan",
    "hawaii": "Hawaiian Islands — Pacific Ocean, southwest of mainland USA on inset maps",
    "alaska": "Alaska — far northwest, usually shown as inset on continental USA maps",
}


def normalize_location_key(name: str) -> str:
    return " ".join((name or "").strip().lower().split())


def geography_hint_for_location(location_name: str) -> str:
    key = normalize_location_key(location_name)
    if key in _US_LOCATION_HINTS:
        return _US_LOCATION_HINTS[key]
    # Soft fallback: keep the raw name and demand real-world placement.
    return (
        f'Real-world geographic position of "{location_name}" on a continental USA map '
        "(use true latitude/longitude relative to coasts, Great Lakes, Rockies, and state borders). "
        "Do NOT place it randomly or for visual balance."
    )


def format_geography_hint(location_name: str) -> str:
    return f'"{location_name}": {geography_hint_for_location(location_name)}'
