"""Feste Nachbarländer- und Meer-Punkte für Vintage-Kartenbeschriftung.

Keine OSM-Namen. Koordinaten sind Innenpunkte zum Beschriften.
Kosovo wird beschriftet, aber nie als hervorgehobene Fläche benutzt.
"""

from __future__ import annotations

from dataclasses import dataclass

_MAX_COUNTRIES = 8
_MAX_SEAS = 2
_MIN_SEP_DEG = 0.8
_PIN_CLEAR_DEG = 0.55


@dataclass(frozen=True)
class GeographyEntry:
    id: str
    kind: str
    english: str
    longitude: float
    latitude: float
    numeric_id: str = ""
    atlas_name: str = ""


# UN M49 / world-atlas numeric ids where the fill exists.
# atlas_name matches world-atlas properties.name. Kosovo has no numeric id.
_COUNTRIES: tuple[GeographyEntry, ...] = (
    GeographyEntry("country:albania", "country", "Albania", 20.07, 41.14, "008", "Albania"),
    GeographyEntry("country:austria", "country", "Austria", 14.55, 47.52, "040", "Austria"),
    GeographyEntry("country:belgium", "country", "Belgium", 4.47, 50.50, "056", "Belgium"),
    GeographyEntry("country:bosnia", "country", "Bosnia and Herzegovina", 17.78, 44.17, "070", "Bosnia and Herz."),
    GeographyEntry("country:bulgaria", "country", "Bulgaria", 25.23, 42.73, "100", "Bulgaria"),
    GeographyEntry("country:croatia", "country", "Croatia", 16.55, 45.55, "191", "Croatia"),
    GeographyEntry("country:czechia", "country", "Czechia", 15.47, 49.82, "203", "Czechia"),
    GeographyEntry("country:denmark", "country", "Denmark", 9.50, 56.00, "208", "Denmark"),
    GeographyEntry("country:egypt", "country", "Egypt", 30.80, 26.80, "818", "Egypt"),
    GeographyEntry("country:finland", "country", "Finland", 26.00, 64.00, "246", "Finland"),
    GeographyEntry("country:france", "country", "France", 2.50, 46.50, "250", "France"),
    GeographyEntry("country:germany", "country", "Germany", 10.45, 51.16, "276", "Germany"),
    GeographyEntry("country:greece", "country", "Greece", 21.80, 39.10, "300", "Greece"),
    GeographyEntry("country:hungary", "country", "Hungary", 19.50, 47.00, "348", "Hungary"),
    GeographyEntry("country:iceland", "country", "Iceland", -19.00, 65.00, "352", "Iceland"),
    GeographyEntry("country:ireland", "country", "Ireland", -8.00, 53.40, "372", "Ireland"),
    GeographyEntry("country:italy", "country", "Italy", 14.90, 41.30, "380", "Italy"),
    GeographyEntry("country:kosovo", "country", "Kosovo", 20.90, 42.56, "", "Kosovo"),
    GeographyEntry("country:malta", "country", "Malta", 14.40, 35.90, "470", "Malta"),
    GeographyEntry("country:montenegro", "country", "Montenegro", 19.30, 42.75, "499", "Montenegro"),
    GeographyEntry("country:morocco", "country", "Morocco", -7.09, 31.79, "504", "Morocco"),
    GeographyEntry("country:netherlands", "country", "Netherlands", 5.29, 52.13, "528", "Netherlands"),
    GeographyEntry("country:north-macedonia", "country", "North Macedonia", 21.70, 41.60, "807", "Macedonia"),
    GeographyEntry("country:norway", "country", "Norway", 8.50, 60.50, "578", "Norway"),
    GeographyEntry("country:poland", "country", "Poland", 19.40, 52.10, "616", "Poland"),
    GeographyEntry("country:portugal", "country", "Portugal", -8.22, 39.40, "620", "Portugal"),
    GeographyEntry("country:romania", "country", "Romania", 24.97, 45.94, "642", "Romania"),
    GeographyEntry("country:serbia", "country", "Serbia", 20.80, 44.15, "688", "Serbia"),
    GeographyEntry("country:slovenia", "country", "Slovenia", 14.82, 46.12, "705", "Slovenia"),
    GeographyEntry("country:spain", "country", "Spain", -3.70, 40.40, "724", "Spain"),
    GeographyEntry("country:sweden", "country", "Sweden", 15.00, 62.00, "752", "Sweden"),
    GeographyEntry("country:switzerland", "country", "Switzerland", 8.23, 46.82, "756", "Switzerland"),
    GeographyEntry("country:turkey", "country", "Turkey", 32.86, 39.06, "792", "Turkey"),
    GeographyEntry("country:united-kingdom", "country", "United Kingdom", -1.50, 52.50, "826", "United Kingdom"),
    GeographyEntry("country:united-states", "country", "United States", -98.35, 39.50, "840", "United States of America"),
    GeographyEntry("country:canada", "country", "Canada", -96.00, 51.00, "124", "Canada"),
    GeographyEntry("country:mexico", "country", "Mexico", -102.55, 23.63, "484", "Mexico"),
)

_SEAS: tuple[GeographyEntry, ...] = (
    GeographyEntry("sea:adriatic", "sea", "Adriatic Sea", 18.15, 42.15),
    GeographyEntry("sea:ionian", "sea", "Ionian Sea", 19.70, 38.70),
    GeographyEntry("sea:aegean", "sea", "Aegean Sea", 25.20, 37.80),
    GeographyEntry("sea:mediterranean", "sea", "Mediterranean Sea", 18.00, 36.40),
    GeographyEntry("sea:tyrrhenian", "sea", "Tyrrhenian Sea", 12.20, 40.60),
    GeographyEntry("sea:black-sea", "sea", "Black Sea", 34.50, 43.40),
    GeographyEntry("sea:north-sea", "sea", "North Sea", 3.50, 56.00),
    GeographyEntry("sea:baltic", "sea", "Baltic Sea", 19.50, 58.50),
    GeographyEntry("sea:atlantic", "sea", "Atlantic Ocean", -20.00, 40.00),
    GeographyEntry("sea:english-channel", "sea", "English Channel", -1.50, 50.20),
    GeographyEntry("sea:caribbean", "sea", "Caribbean Sea", -75.00, 15.00),
    GeographyEntry("sea:aegean-north", "sea", "Sea of Marmara", 27.70, 40.70),
)

_SEA_FALLBACKS: dict[str, dict[str, str]] = {
    "DE": {
        "Adriatic Sea": "Adriatisches Meer",
        "Ionian Sea": "Ionisches Meer",
        "Aegean Sea": "Ägäisches Meer",
        "Mediterranean Sea": "Mittelmeer",
        "Tyrrhenian Sea": "Tyrrhenisches Meer",
        "Black Sea": "Schwarzes Meer",
        "North Sea": "Nordsee",
        "Baltic Sea": "Ostsee",
        "Atlantic Ocean": "Atlantik",
        "English Channel": "Ärmelkanal",
        "Caribbean Sea": "Karibisches Meer",
        "Sea of Marmara": "Marmarameer",
    },
    "EN": {},
    "FR": {
        "Adriatic Sea": "mer Adriatique",
        "Ionian Sea": "mer Ionienne",
        "Aegean Sea": "mer Égée",
        "Mediterranean Sea": "Méditerranée",
        "Tyrrhenian Sea": "mer Tyrrhénienne",
        "Black Sea": "mer Noire",
        "North Sea": "mer du Nord",
        "Baltic Sea": "mer Baltique",
        "Atlantic Ocean": "océan Atlantique",
        "English Channel": "Manche",
        "Caribbean Sea": "mer des Caraïbes",
        "Sea of Marmara": "mer de Marmara",
    },
    "IT": {
        "Adriatic Sea": "mare Adriatico",
        "Ionian Sea": "mare Ionio",
        "Aegean Sea": "mar Egeo",
        "Mediterranean Sea": "Mediterraneo",
        "Tyrrhenian Sea": "mar Tirreno",
        "Black Sea": "mar Nero",
        "North Sea": "mare del Nord",
        "Baltic Sea": "mar Baltico",
        "Atlantic Ocean": "oceano Atlantico",
        "English Channel": "Canale della Manica",
        "Caribbean Sea": "mar dei Caraibi",
        "Sea of Marmara": "mar di Marmara",
    },
    "ES": {
        "Adriatic Sea": "mar Adriático",
        "Ionian Sea": "mar Jónico",
        "Aegean Sea": "mar Egeo",
        "Mediterranean Sea": "Mediterráneo",
        "Tyrrhenian Sea": "mar Tirreno",
        "Black Sea": "mar Negro",
        "North Sea": "mar del Norte",
        "Baltic Sea": "mar Báltico",
        "Atlantic Ocean": "océano Atlántico",
        "English Channel": "canal de la Mancha",
        "Caribbean Sea": "mar Caribe",
        "Sea of Marmara": "mar de Mármara",
    },
    "PT": {
        "Adriatic Sea": "mar Adriático",
        "Ionian Sea": "mar Jónico",
        "Aegean Sea": "mar Egeu",
        "Mediterranean Sea": "Mediterrâneo",
        "Tyrrhenian Sea": "mar Tirreno",
        "Black Sea": "mar Negro",
        "North Sea": "mar do Norte",
        "Baltic Sea": "mar Báltico",
        "Atlantic Ocean": "oceano Atlântico",
        "English Channel": "canal da Mancha",
        "Caribbean Sea": "mar das Caraíbas",
        "Sea of Marmara": "mar de Mármara",
    },
    "NL": {
        "Adriatic Sea": "Adriatische Zee",
        "Ionian Sea": "Ionische Zee",
        "Aegean Sea": "Egeïsche Zee",
        "Mediterranean Sea": "Middellandse Zee",
        "Tyrrhenian Sea": "Tyrreense Zee",
        "Black Sea": "Zwarte Zee",
        "North Sea": "Noordzee",
        "Baltic Sea": "Oostzee",
        "Atlantic Ocean": "Atlantische Oceaan",
        "English Channel": "Het Kanaal",
        "Caribbean Sea": "Caraïbische Zee",
        "Sea of Marmara": "Zee van Marmara",
    },
    "PL": {
        "Adriatic Sea": "Morze Adriatyckie",
        "Ionian Sea": "Morze Jońskie",
        "Aegean Sea": "Morze Egejskie",
        "Mediterranean Sea": "Morze Śródziemne",
        "Tyrrhenian Sea": "Morze Tyrreńskie",
        "Black Sea": "Morze Czarne",
        "North Sea": "Morze Północne",
        "Baltic Sea": "Morze Bałtyckie",
        "Atlantic Ocean": "Ocean Atlantycki",
        "English Channel": "kanał La Manche",
        "Caribbean Sea": "Morze Karaibskie",
        "Sea of Marmara": "Morze Marmara",
    },
    "JP": {
        "Adriatic Sea": "アドリア海",
        "Ionian Sea": "イオニア海",
        "Aegean Sea": "エーゲ海",
        "Mediterranean Sea": "地中海",
        "Tyrrhenian Sea": "ティレニア海",
        "Black Sea": "黒海",
        "North Sea": "北海",
        "Baltic Sea": "バルト海",
        "Atlantic Ocean": "大西洋",
        "English Channel": "イギリス海峡",
        "Caribbean Sea": "カリブ海",
        "Sea of Marmara": "マルマラ海",
    },
    "KR": {
        "Adriatic Sea": "아드리아해",
        "Ionian Sea": "이오니아해",
        "Aegean Sea": "에게해",
        "Mediterranean Sea": "지중해",
        "Tyrrhenian Sea": "티레니아해",
        "Black Sea": "흑해",
        "North Sea": "북해",
        "Baltic Sea": "발트해",
        "Atlantic Ocean": "대서양",
        "English Channel": "영국 해협",
        "Caribbean Sea": "카리브해",
        "Sea of Marmara": "마르마라해",
    },
}

_COUNTRY_FALLBACKS: dict[str, dict[str, str]] = {
    "DE": {
        "Kosovo": "Kosovo",
        "North Macedonia": "Nordmazedonien",
        "Bosnia and Herzegovina": "Bosnien-Herzegowina",
        "Canada": "Kanada",
        "Mexico": "Mexiko",
    },
    "FR": {
        "Kosovo": "Kosovo",
        "North Macedonia": "Macédoine du Nord",
        "Bosnia and Herzegovina": "Bosnie-Herzégovine",
        "Canada": "Canada",
        "Mexico": "Mexique",
    },
    "IT": {
        "Kosovo": "Kosovo",
        "North Macedonia": "Macedonia del Nord",
        "Bosnia and Herzegovina": "Bosnia ed Erzegovina",
        "Canada": "Canada",
        "Mexico": "Messico",
    },
    "ES": {
        "Kosovo": "Kosovo",
        "North Macedonia": "Macedonia del Norte",
        "Bosnia and Herzegovina": "Bosnia y Herzegovina",
        "Canada": "Canadá",
        "Mexico": "México",
    },
    "PT": {
        "Kosovo": "Kosovo",
        "North Macedonia": "Macedónia do Norte",
        "Bosnia and Herzegovina": "Bósnia e Herzegovina",
        "Canada": "Canadá",
        "Mexico": "México",
    },
    "NL": {
        "Kosovo": "Kosovo",
        "North Macedonia": "Noord-Macedonië",
        "Bosnia and Herzegovina": "Bosnië en Herzegovina",
        "Canada": "Canada",
        "Mexico": "Mexico",
    },
    "PL": {
        "Kosovo": "Kosowo",
        "North Macedonia": "Macedonia Północna",
        "Bosnia and Herzegovina": "Bośnia i Hercegowina",
        "Canada": "Kanada",
        "Mexico": "Meksyk",
    },
    "JP": {
        "Kosovo": "コソボ",
        "North Macedonia": "北マケドニア",
        "Bosnia and Herzegovina": "ボスニア・ヘルツェゴビナ",
        "Canada": "カナダ",
        "Mexico": "メキシコ",
    },
    "KR": {
        "Kosovo": "코소보",
        "North Macedonia": "북마케도니아",
        "Bosnia and Herzegovina": "보스니아 헤르체고비나",
        "Canada": "캐나다",
        "Mexico": "멕시코",
    },
}


def point_in_view_bounds(
    longitude: float,
    latitude: float,
    view_bounds: list[list[float]],
) -> bool:
    west, south = view_bounds[0]
    east, north = view_bounds[1]
    return west <= longitude <= east and south <= latitude <= north


def _distance_deg(
    longitude: float,
    latitude: float,
    other_lon: float,
    other_lat: float,
) -> float:
    return ((longitude - other_lon) ** 2 + (latitude - other_lat) ** 2) ** 0.5


def visible_geography(
    view_bounds: list[list[float]],
    *,
    exclude_numeric: str = "",
    pin_longitude: float | None = None,
    pin_latitude: float | None = None,
) -> list[GeographyEntry]:
    """Länder und Meere, deren Beschriftungspunkt im Kartenausschnitt liegt."""
    wanted = str(exclude_numeric or "").zfill(3)[:3]
    west, south = view_bounds[0]
    east, north = view_bounds[1]
    center_lon = (west + east) / 2.0
    center_lat = (south + north) / 2.0

    def _keep(entry: GeographyEntry) -> bool:
        if entry.numeric_id and entry.numeric_id.zfill(3) == wanted:
            return False
        if not point_in_view_bounds(entry.longitude, entry.latitude, view_bounds):
            return False
        if pin_longitude is None or pin_latitude is None:
            return True
        return (
            _distance_deg(
                entry.longitude, entry.latitude, pin_longitude, pin_latitude
            )
            >= _PIN_CLEAR_DEG
        )

    countries = [entry for entry in _COUNTRIES if _keep(entry)]
    seas = [entry for entry in _SEAS if _keep(entry)]
    countries.sort(
        key=lambda entry: _distance_deg(
            entry.longitude, entry.latitude, center_lon, center_lat
        )
    )
    seas.sort(
        key=lambda entry: _distance_deg(
            entry.longitude, entry.latitude, center_lon, center_lat
        )
    )

    picked: list[GeographyEntry] = []

    def _fits(entry: GeographyEntry) -> bool:
        return all(
            _distance_deg(entry.longitude, entry.latitude, other.longitude, other.latitude)
            >= _MIN_SEP_DEG
            for other in picked
        )

    for entry in countries:
        if len([item for item in picked if item.kind == "country"]) >= _MAX_COUNTRIES:
            break
        if _fits(entry):
            picked.append(entry)
    for entry in seas:
        if len([item for item in picked if item.kind == "sea"]) >= _MAX_SEAS:
            break
        if _fits(entry):
            picked.append(entry)
    return picked


def sea_fallback_label(english: str, language: str) -> str:
    from otio_app.project_layout import language_folder_name

    lang = language_folder_name(language)
    name = str(english or "").strip()
    mapped = _SEA_FALLBACKS.get(lang, {}).get(name)
    if mapped:
        return mapped
    if lang == "EN":
        return name
    return _SEA_FALLBACKS.get("DE", {}).get(name) or name


def country_fallback_label(english: str, language: str) -> str:
    from otio_app.project_layout import language_folder_name

    lang = language_folder_name(language)
    name = str(english or "").strip()
    mapped = _COUNTRY_FALLBACKS.get(lang, {}).get(name)
    if mapped:
        return mapped
    if lang == "EN":
        return name
    return _COUNTRY_FALLBACKS.get("DE", {}).get(name) or ""


def geography_label_is_plausible(localized: str) -> bool:
    label = " ".join(str(localized or "").split())
    if not label or len(label) > 40 or len(label.split()) > 5:
        return False
    if any(mark in label for mark in (".", "?", "!", ":")):
        return False
    lowered = label.casefold()
    banned = ("http", "www", "socket", "payment", "help", "card")
    return not any(token in lowered for token in banned)
