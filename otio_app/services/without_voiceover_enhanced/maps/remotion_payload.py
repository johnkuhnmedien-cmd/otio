"""Remotion input props from an OTIO map plan item.

No Thomas imports or absolute Thomas paths. Animation modes map
``opening`` → Remotion ``intro`` (ramp zoom) and ``transition`` → quadratic pan.
Visible labels are localized; ``exportLabel`` keeps the dramaturgy original name.
"""

from __future__ import annotations

import re
import uuid

from otio_app.project_layout import language_folder_name
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

_EAST_ASIAN_COUNTRY_LABELS: dict[str, dict[str, str]] = {
    "JP": {
        "albania": "アルバニア",
        "austria": "オーストリア",
        "belgium": "ベルギー",
        "bulgaria": "ブルガリア",
        "croatia": "クロアチア",
        "cyprus": "キプロス",
        "czechia": "チェコ",
        "denmark": "デンマーク",
        "egypt": "エジプト",
        "finland": "フィンランド",
        "france": "フランス",
        "germany": "ドイツ",
        "greece": "ギリシャ",
        "hungary": "ハンガリー",
        "iceland": "アイスランド",
        "ireland": "アイルランド",
        "italy": "イタリア",
        "malta": "マルタ",
        "montenegro": "モンテネグロ",
        "morocco": "モロッコ",
        "netherlands": "オランダ",
        "norway": "ノルウェー",
        "poland": "ポーランド",
        "portugal": "ポルトガル",
        "romania": "ルーマニア",
        "slovenia": "スロベニア",
        "spain": "スペイン",
        "sweden": "スウェーデン",
        "switzerland": "スイス",
        "turkey": "トルコ",
        "united kingdom": "イギリス",
        "united states": "アメリカ",
    },
    "KR": {
        "albania": "알바니아",
        "austria": "오스트리아",
        "belgium": "벨기에",
        "bulgaria": "불가리아",
        "croatia": "크로아티아",
        "cyprus": "키프로스",
        "czechia": "체코",
        "denmark": "덴마크",
        "egypt": "이집트",
        "finland": "핀란드",
        "france": "프랑스",
        "germany": "독일",
        "greece": "그리스",
        "hungary": "헝가리",
        "iceland": "아이슬란드",
        "ireland": "아일랜드",
        "italy": "이탈리아",
        "malta": "몰타",
        "montenegro": "몬테네그로",
        "morocco": "모로코",
        "netherlands": "네덜란드",
        "norway": "노르웨이",
        "poland": "폴란드",
        "portugal": "포르투갈",
        "romania": "루마니아",
        "slovenia": "슬로베니아",
        "spain": "스페인",
        "sweden": "스웨덴",
        "switzerland": "스위스",
        "turkey": "터키",
        "united kingdom": "영국",
        "united states": "미국",
    },
}

_COUNTRY_OVERLAY_LABELS: dict[str, dict[str, str]] = {
    "DE": {
        "albania": "Albanien",
        "austria": "Österreich",
        "belgium": "Belgien",
        "bulgaria": "Bulgarien",
        "croatia": "Kroatien",
        "cyprus": "Zypern",
        "czechia": "Tschechien",
        "denmark": "Dänemark",
        "egypt": "Ägypten",
        "finland": "Finnland",
        "france": "Frankreich",
        "germany": "Deutschland",
        "greece": "Griechenland",
        "hungary": "Ungarn",
        "iceland": "Island",
        "ireland": "Irland",
        "italy": "Italien",
        "malta": "Malta",
        "montenegro": "Montenegro",
        "morocco": "Marokko",
        "netherlands": "Niederlande",
        "norway": "Norwegen",
        "poland": "Polen",
        "portugal": "Portugal",
        "romania": "Rumänien",
        "slovenia": "Slowenien",
        "spain": "Spanien",
        "sweden": "Schweden",
        "switzerland": "Schweiz",
        "turkey": "Türkei",
        "united kingdom": "Vereinigtes Königreich",
        "united states": "Vereinigte Staaten",
    },
    "FR": {
        "albania": "Albanie",
        "austria": "Autriche",
        "belgium": "Belgique",
        "bulgaria": "Bulgarie",
        "croatia": "Croatie",
        "cyprus": "Chypre",
        "czechia": "Tchéquie",
        "denmark": "Danemark",
        "egypt": "Égypte",
        "finland": "Finlande",
        "france": "France",
        "germany": "Allemagne",
        "greece": "Grèce",
        "hungary": "Hongrie",
        "iceland": "Islande",
        "ireland": "Irlande",
        "italy": "Italie",
        "malta": "Malte",
        "montenegro": "Monténégro",
        "morocco": "Maroc",
        "netherlands": "Pays-Bas",
        "norway": "Norvège",
        "poland": "Pologne",
        "portugal": "Portugal",
        "romania": "Roumanie",
        "slovenia": "Slovénie",
        "spain": "Espagne",
        "sweden": "Suède",
        "switzerland": "Suisse",
        "turkey": "Turquie",
        "united kingdom": "Royaume-Uni",
        "united states": "États-Unis",
    },
    "IT": {
        "albania": "Albania",
        "austria": "Austria",
        "belgium": "Belgio",
        "bulgaria": "Bulgaria",
        "croatia": "Croazia",
        "cyprus": "Cipro",
        "czechia": "Cechia",
        "denmark": "Danimarca",
        "egypt": "Egitto",
        "finland": "Finlandia",
        "france": "Francia",
        "germany": "Germania",
        "greece": "Grecia",
        "hungary": "Ungheria",
        "iceland": "Islanda",
        "ireland": "Irlanda",
        "italy": "Italia",
        "malta": "Malta",
        "montenegro": "Montenegro",
        "morocco": "Marocco",
        "netherlands": "Paesi Bassi",
        "norway": "Norvegia",
        "poland": "Polonia",
        "portugal": "Portogallo",
        "romania": "Romania",
        "slovenia": "Slovenia",
        "spain": "Spagna",
        "sweden": "Svezia",
        "switzerland": "Svizzera",
        "turkey": "Turchia",
        "united kingdom": "Regno Unito",
        "united states": "Stati Uniti",
    },
    "ES": {
        "albania": "Albania",
        "austria": "Austria",
        "belgium": "Bélgica",
        "bulgaria": "Bulgaria",
        "croatia": "Croacia",
        "cyprus": "Chipre",
        "czechia": "Chequia",
        "denmark": "Dinamarca",
        "egypt": "Egipto",
        "finland": "Finlandia",
        "france": "Francia",
        "germany": "Alemania",
        "greece": "Grecia",
        "hungary": "Hungría",
        "iceland": "Islandia",
        "ireland": "Irlanda",
        "italy": "Italia",
        "malta": "Malta",
        "montenegro": "Montenegro",
        "morocco": "Marruecos",
        "netherlands": "Países Bajos",
        "norway": "Noruega",
        "poland": "Polonia",
        "portugal": "Portugal",
        "romania": "Rumanía",
        "slovenia": "Eslovenia",
        "spain": "España",
        "sweden": "Suecia",
        "switzerland": "Suiza",
        "turkey": "Turquía",
        "united kingdom": "Reino Unido",
        "united states": "Estados Unidos",
    },
    "PT": {
        "albania": "Albânia",
        "austria": "Áustria",
        "belgium": "Bélgica",
        "bulgaria": "Bulgária",
        "croatia": "Croácia",
        "cyprus": "Chipre",
        "czechia": "Chéquia",
        "denmark": "Dinamarca",
        "egypt": "Egito",
        "finland": "Finlândia",
        "france": "França",
        "germany": "Alemanha",
        "greece": "Grécia",
        "hungary": "Hungria",
        "iceland": "Islândia",
        "ireland": "Irlanda",
        "italy": "Itália",
        "malta": "Malta",
        "montenegro": "Montenegro",
        "morocco": "Marrocos",
        "netherlands": "Países Baixos",
        "norway": "Noruega",
        "poland": "Polónia",
        "portugal": "Portugal",
        "romania": "Roménia",
        "slovenia": "Eslovénia",
        "spain": "Espanha",
        "sweden": "Suécia",
        "switzerland": "Suíça",
        "turkey": "Turquia",
        "united kingdom": "Reino Unido",
        "united states": "Estados Unidos",
    },
    "NL": {
        "albania": "Albanië",
        "austria": "Oostenrijk",
        "belgium": "België",
        "bulgaria": "Bulgarije",
        "croatia": "Kroatië",
        "cyprus": "Cyprus",
        "czechia": "Tsjechië",
        "denmark": "Denemarken",
        "egypt": "Egypte",
        "finland": "Finland",
        "france": "Frankrijk",
        "germany": "Duitsland",
        "greece": "Griekenland",
        "hungary": "Hongarije",
        "iceland": "IJsland",
        "ireland": "Ierland",
        "italy": "Italië",
        "malta": "Malta",
        "montenegro": "Montenegro",
        "morocco": "Marokko",
        "netherlands": "Nederland",
        "norway": "Noorwegen",
        "poland": "Polen",
        "portugal": "Portugal",
        "romania": "Roemenië",
        "slovenia": "Slovenië",
        "spain": "Spanje",
        "sweden": "Zweden",
        "switzerland": "Zwitserland",
        "turkey": "Turkije",
        "united kingdom": "Verenigd Koninkrijk",
        "united states": "Verenigde Staten",
    },
    "PL": {
        "albania": "Albania",
        "austria": "Austria",
        "belgium": "Belgia",
        "bulgaria": "Bułgaria",
        "croatia": "Chorwacja",
        "cyprus": "Cypr",
        "czechia": "Czechy",
        "denmark": "Dania",
        "egypt": "Egipt",
        "finland": "Finlandia",
        "france": "Francja",
        "germany": "Niemcy",
        "greece": "Grecja",
        "hungary": "Węgry",
        "iceland": "Islandia",
        "ireland": "Irlandia",
        "italy": "Włochy",
        "malta": "Malta",
        "montenegro": "Czarnogóra",
        "morocco": "Maroko",
        "netherlands": "Holandia",
        "norway": "Norwegia",
        "poland": "Polska",
        "portugal": "Portugalia",
        "romania": "Rumunia",
        "slovenia": "Słowenia",
        "spain": "Hiszpania",
        "sweden": "Szwecja",
        "switzerland": "Szwajcaria",
        "turkey": "Turcja",
        "united kingdom": "Wielka Brytania",
        "united states": "Stany Zjednoczone",
    },
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
    """Karten-Overlay: Ländername in der Projektsprache, sonst Rohname."""
    raw = str(country or "").strip() or "Map"
    if language is None:
        return raw[:100]
    lang = language_folder_name(str(language).strip() or "DE")
    english = _COUNTRY_ENGLISH.get(raw.casefold(), "")
    key = (english or raw).casefold()
    if lang == "EN":
        return (english or raw)[:100]
    if lang in _EAST_ASIAN_COUNTRY_LABELS:
        mapped = _EAST_ASIAN_COUNTRY_LABELS[lang].get(key)
        return (mapped or raw)[:100]
    overlay = _COUNTRY_OVERLAY_LABELS.get(lang, {})
    mapped = overlay.get(key)
    return (mapped or english or raw)[:100]


_DE_ATTRIBUTIVE_RE = re.compile(r"^(.+?)er(?:\s+|-)(.+)$", re.IGNORECASE)

_LANDMARK_KIND_BY_TOKEN: dict[str, str] = {
    "wasserfall": "falls",
    "waterfall": "falls",
    "waterfalls": "falls",
    "falls": "falls",
    "waterval": "falls",
    "wodospad": "falls",
    "klammen": "gorges",
    "gorges": "gorges",
    "klamm": "gorge",
    "schlucht": "gorge",
    "gorge": "gorge",
    "kloof": "gorge",
    "see": "lake",
    "lake": "lake",
    "meer": "lake",
    "jezero": "lake",
    "tal": "valley",
    "valley": "valley",
    "dal": "valley",
    "dolina": "valley",
    "höhlen": "caves",
    "hoehlen": "caves",
    "caves": "caves",
    "höhle": "cave",
    "hoehle": "cave",
    "cave": "cave",
    "grot": "cave",
    "castle": "castle",
    "burg": "castle",
    "pass": "pass",
    "nationalpark": "park",
    "national park": "park",
}

_LANDMARK_PREFIXES: tuple[tuple[str, str], ...] = (
    ("falls", "cascata di "),
    ("falls", "cascade de "),
    ("falls", "cascada de "),
    ("falls", "cascata de "),
    ("falls", "chute de "),
    ("falls", "wodospad "),
    ("gorges", "gole di "),
    ("gorges", "gorges de "),
    ("gorges", "gargantas de "),
    ("gorge", "gola di "),
    ("gorge", "garganta de "),
    ("gorge", "gorge de "),
    ("gorge", "wąwóz "),
    ("lake", "lago di "),
    ("lake", "lac de "),
    ("lake", "jezioro "),
    ("lake", "lago "),
    ("lake", "lake "),
    ("valley", "valle di "),
    ("valley", "vallée de "),
    ("valley", "vallee de "),
    ("valley", "valle de "),
    ("valley", "vale de "),
    ("valley", "dolina "),
    ("caves", "grotte di "),
    ("caves", "grottes de "),
    ("caves", "cuevas de "),
    ("caves", "caves of "),
    ("caves", "höhlen von "),
    ("caves", "hoehlen von "),
    ("cave", "grotta di "),
    ("cave", "grotte de "),
    ("cave", "cueva de "),
    ("cave", "gruta de "),
    ("cave", "jaskinia "),
    ("cave", "cave of "),
    ("cave", "höhle von "),
    ("cave", "hoehle von "),
    ("castle", "castello di "),
    ("castle", "château de "),
    ("castle", "chateau de "),
    ("castle", "castillo de "),
    ("castle", "castelo de "),
    ("castle", "kasteel "),
    ("castle", "zamek "),
    ("castle", "burg "),
    ("pass", "passo del "),
    ("pass", "passo di "),
    ("pass", "passo "),
    ("pass", "col de "),
    ("park", "parco nazionale del "),
    ("park", "parco nazionale di "),
    ("park", "parc national de "),
    ("park", "parque nacional de "),
)

_LANDMARK_SUFFIXES: tuple[tuple[str, str], ...] = (
    ("falls", "-wasserfall"),
    ("falls", " wasserfall"),
    ("falls", " waterfall"),
    ("falls", " waterfalls"),
    ("falls", "-waterval"),
    ("falls", " waterval"),
    ("falls", " falls"),
    ("falls", "-falls"),
    ("gorges", "-klammen"),
    ("gorges", " klammen"),
    ("gorges", " gorges"),
    ("gorge", "-schlucht"),
    ("gorge", " schlucht"),
    ("gorge", "-klamm"),
    ("gorge", " klamm"),
    ("gorge", "-kloof"),
    ("gorge", " kloof"),
    ("gorge", " gorge"),
    ("gorge", "-gorge"),
    ("lake", "-see"),
    ("lake", " see"),
    ("lake", " lake"),
    ("lake", "-meer"),
    ("lake", " meer"),
    ("valley", "-tal"),
    ("valley", " tal"),
    ("valley", " valley"),
    ("valley", "-dal"),
    ("valley", " dal"),
    ("valley", " dolina"),
    ("valley", "-dolina"),
    ("caves", "-höhlen"),
    ("caves", " höhlen"),
    ("caves", "-hoehlen"),
    ("caves", " hoehlen"),
    ("caves", " caves"),
    ("cave", "-höhle"),
    ("cave", " höhle"),
    ("cave", "-hoehle"),
    ("cave", " hoehle"),
    ("cave", " cave"),
    ("castle", " castle"),
    ("castle", "-castle"),
    ("park", "-nationalpark"),
    ("park", " nationalpark"),
    ("park", " national park"),
    ("pass", "-pass"),
    ("pass", " pass"),
)

_LANDMARK_TEMPLATES: dict[str, dict[str, str]] = {
    "DE": {
        "falls": "{stem}-Wasserfall",
        "gorge": "{stem}-Klamm",
        "gorges": "{stem}-Klammen",
        "lake": "{stem}-See",
        "valley": "{stem}-Tal",
        "caves": "{stem}-Höhlen",
        "cave": "{stem}-Höhle",
        "castle": "Burg {stem}",
        "pass": "{stem}-Pass",
        "park": "{stem}-Nationalpark",
    },
    "EN": {
        "falls": "{stem} Falls",
        "gorge": "{stem} Gorge",
        "gorges": "{stem} Gorges",
        "lake": "{stem} Lake",
        "valley": "{stem} Valley",
        "caves": "{stem} Caves",
        "cave": "{stem} Cave",
        "castle": "{stem} Castle",
        "pass": "{stem} Pass",
        "park": "{stem} National Park",
    },
    "FR": {
        "falls": "Cascade de {stem}",
        "gorge": "Gorges de {stem}",
        "gorges": "Gorges de {stem}",
        "lake": "Lac de {stem}",
        "valley": "Vallée de {stem}",
        "caves": "Grottes de {stem}",
        "cave": "Grotte de {stem}",
        "castle": "Château de {stem}",
        "pass": "Col de {stem}",
        "park": "Parc national de {stem}",
    },
    "IT": {
        "falls": "Cascata di {stem}",
        "gorge": "Gola di {stem}",
        "gorges": "Gole di {stem}",
        "lake": "Lago di {stem}",
        "valley": "Valle di {stem}",
        "caves": "Grotte di {stem}",
        "cave": "Grotta di {stem}",
        "castle": "Castello di {stem}",
        "pass": "Passo {stem}",
        "park": "Parco nazionale del {stem}",
    },
    "ES": {
        "falls": "Cascada de {stem}",
        "gorge": "Garganta de {stem}",
        "gorges": "Gargantas de {stem}",
        "lake": "Lago {stem}",
        "valley": "Valle de {stem}",
        "caves": "Cuevas de {stem}",
        "cave": "Cueva de {stem}",
        "castle": "Castillo de {stem}",
        "pass": "Puerto de {stem}",
        "park": "Parque nacional de {stem}",
    },
    "PT": {
        "falls": "Cascata de {stem}",
        "gorge": "Garganta de {stem}",
        "gorges": "Gargantas de {stem}",
        "lake": "Lago {stem}",
        "valley": "Vale de {stem}",
        "caves": "Grutas de {stem}",
        "cave": "Gruta de {stem}",
        "castle": "Castelo de {stem}",
        "pass": "Passo de {stem}",
        "park": "Parque nacional de {stem}",
    },
    "NL": {
        "falls": "{stem}-waterval",
        "gorge": "{stem}-kloof",
        "gorges": "{stem}-kloof",
        "lake": "{stem}-meer",
        "valley": "{stem}-dal",
        "caves": "{stem}-grotten",
        "cave": "{stem}-grot",
        "castle": "Kasteel {stem}",
        "pass": "{stem}-pas",
        "park": "{stem} Nationaal Park",
    },
    "PL": {
        "falls": "Wodospad {stem}",
        "gorge": "Wąwóz {stem}",
        "gorges": "Wąwóz {stem}",
        "lake": "Jezioro {stem}",
        "valley": "Dolina {stem}",
        "caves": "Jaskinie {stem}",
        "cave": "Jaskinia {stem}",
        "castle": "Zamek {stem}",
        "pass": "Przełęcz {stem}",
        "park": "Park Narodowy {stem}",
    },
}


def _clip_label(value: str, limit: int) -> str:
    text = str(value or "").strip() or "Kapitel"
    return text[:limit]


def _parse_map_landmark(place: str) -> tuple[str, str] | None:
    """``(kind, stem)`` for German/English/localized geographic labels."""
    text = " ".join(str(place or "").replace("_", " ").split())
    if not text:
        return None
    lower = text.casefold()
    prefixes = sorted(_LANDMARK_PREFIXES, key=lambda item: len(item[1]), reverse=True)
    for kind, prefix in prefixes:
        if lower.startswith(prefix):
            stem = text[len(prefix) :].strip(" -")
            if stem:
                return kind, stem
    match = _DE_ATTRIBUTIVE_RE.match(text)
    if match:
        stem = match.group(1).strip(" -")
        rest = match.group(2).strip(" -")
        kind = _LANDMARK_KIND_BY_TOKEN.get(rest.casefold())
        if kind and len(stem) >= 4:
            return kind, stem
    suffixes = sorted(_LANDMARK_SUFFIXES, key=lambda item: len(item[1]), reverse=True)
    for kind, suffix in suffixes:
        if lower.endswith(suffix):
            stem = text[: len(text) - len(suffix)].strip(" -")
            if stem:
                return kind, stem
    return None


def localize_map_place_label(place: str, language: str | None) -> str:
    """Sichtbarer Kartenort: deutsche/englische Landschaftswörter in die Projektsprache."""
    text = " ".join(str(place or "").replace("_", " ").split())
    if not text:
        return text
    parsed = _parse_map_landmark(text)
    if parsed is None:
        return text
    kind, stem = parsed
    lang = language_folder_name(str(language or "").strip() or "DE")
    templates = _LANDMARK_TEMPLATES.get(lang) or _LANDMARK_TEMPLATES["EN"]
    template = templates.get(kind)
    if not template:
        return text
    return template.format(stem=stem)


def map_overlay_place_label(
    original: str, display: str | None, language: str | None
) -> str:
    """Kartenzeile: deutschen Ordnernamen übersetzen, OSM-Namen nicht übernehmen.

    Ein manueller Anzeigename gilt nur, wenn der Ordner selbst kein
    übersetzbares Landschaftswort enthält (z. B. Monte Athos).
    """
    original_text = " ".join(str(original or "").replace("_", " ").split())
    display_text = " ".join(str(display or "").replace("_", " ").split())
    if original_text and _parse_map_landmark(original_text) is not None:
        return localize_map_place_label(original_text, language)
    if display_text:
        return localize_map_place_label(display_text, language)
    return localize_map_place_label(original_text, language)


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
        from_original = item.original_chapter_label
    else:
        from_raw = item.from_localized_display_label or item.localized_display_label
        from_original = item.from_original_chapter_label or item.original_chapter_label
    from_label = _clip_label(
        map_overlay_place_label(from_original, from_raw, item.language), 100
    )
    to_label = _clip_label(
        map_overlay_place_label(
            item.original_chapter_label, item.localized_display_label, item.language
        ),
        100,
    )
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
