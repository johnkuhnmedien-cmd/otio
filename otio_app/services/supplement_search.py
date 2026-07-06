"""Keyword-Suchqueries für Supplement-Assets."""

from __future__ import annotations

import re

from otio_app.analysis_models import SupplementCandidate, SupplementRequest

_STOPWORDS = {
    "aber",
    "alle",
    "auf",
    "aus",
    "bei",
    "bis",
    "das",
    "dem",
    "den",
    "der",
    "des",
    "die",
    "durch",
    "ein",
    "eine",
    "einem",
    "einen",
    "einer",
    "er",
    "für",
    "hat",
    "hier",
    "ist",
    "mit",
    "nicht",
    "oder",
    "sich",
    "und",
    "von",
    "vor",
    "wegen",
    "zu",
}

_VISUAL_TRANSLATIONS = {
    "eng": "narrow",
    "enge": "narrow",
    "engen": "narrow",
    "fels": "rock",
    "felsen": "rock",
    "felsspalte": "slot canyon",
    "felsspalten": "slot canyon",
    "licht": "light",
    "lichtstrahl": "light beam",
    "lichtstrahlen": "light beams",
    "mensch": "person",
    "person": "person",
    "schmal": "narrow",
    "schmale": "narrow",
    "schmalen": "narrow",
    "sonne": "sunlight",
    "wasser": "water",
    "gewasser": "water",
    "gewitter": "storm",
    "wuste": "desert",
    "wüstenlandschaft": "desert landscape",
}


def _tokens(text: str) -> list[str]:
    normalized = (
        text.lower()
        .replace("ä", "ae")
        .replace("ö", "oe")
        .replace("ü", "ue")
        .replace("ß", "ss")
    )
    return re.findall(r"[a-z0-9]{3,}", normalized)


def _dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        clean = value.strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        result.append(clean)
    return result


def build_keyword_query(
    *,
    folder_name: str,
    visual_requirement: str,
    passage_text: str = "",
    max_terms: int = 7,
) -> str:
    """Erstellt eine kurze Suchquery statt eines ganzen Satzes."""
    terms: list[str] = [folder_name.strip()]
    text = f"{visual_requirement} {passage_text}"
    for token in _tokens(text):
        if token in _STOPWORDS:
            continue
        translated = _VISUAL_TRANSLATIONS.get(token)
        if translated:
            terms.append(translated)
        elif len(token) >= 5:
            terms.append(token)
    return " ".join(_dedupe_keep_order(terms)[:max_terms])


def base_location_for_request(request: SupplementRequest) -> str:
    return (request.location_name or request.folder_name).strip()


def ensure_location_in_query(query: str, location_name: str) -> str:
    location = location_name.strip()
    cleaned = " ".join(query.split())
    if not location:
        return cleaned
    if location.casefold() in cleaned.casefold():
        return cleaned
    return f"{location} {cleaned}".strip()


def build_pexels_query_variants(request: SupplementRequest) -> list[str]:
    location = base_location_for_request(request)
    preferred = ensure_location_in_query(preferred_search_query(request), location)
    variants = [
        location,
        f"{location} narrow",
        f"{location} slot canyon",
        f"{location} sandstone",
        f"{location} canyon",
        f"{location} walking",
        f"{location} person",
        f"{location} tour",
        preferred,
    ]
    if request.allow_broader_search:
        variants.extend(
            [
                "slot canyon",
                "narrow canyon",
                "person in canyon",
            ]
        )
    return _dedupe_keep_order([variant for variant in variants if variant.strip()])


def build_pexels_photo_query_variants(request: SupplementRequest) -> list[str]:
    location = base_location_for_request(request)
    variants = [
        location,
        f"{location} sandstone",
        f"{location} slot canyon",
        f"{location} narrow",
        f"{location} walking",
        f"{location} tour",
        ensure_location_in_query(preferred_search_query(request), location),
    ]
    return _dedupe_keep_order([variant for variant in variants if variant.strip()])


def preferred_search_query(request: SupplementRequest) -> str:
    for language in ("en", "de"):
        queries = request.search_queries.get(language, [])
        if queries:
            query = str(queries[0]).strip()
            if query:
                return ensure_location_in_query(query, base_location_for_request(request))
    return build_keyword_query(
        folder_name=base_location_for_request(request),
        visual_requirement=request.visual_requirement,
        passage_text=request.passage_text,
    )


def request_with_keyword_query(request: SupplementRequest, query: str | None = None) -> SupplementRequest:
    selected = (query or preferred_search_query(request)).strip()
    return request.model_copy(
        update={
            "search_queries": {
                **request.search_queries,
                "en": [ensure_location_in_query(selected, base_location_for_request(request))],
            }
        }
    )


def location_terms(location_name: str) -> list[str]:
    return _tokens(location_name)


def location_match_for_text(text: str, location_name: str, *, broadened: bool = False) -> tuple[str, list[str], list[str]]:
    required = location_terms(location_name)
    present_tokens = set(_tokens(text))
    present = [token for token in required if token in present_tokens]
    if broadened:
        return "broadened", required, present
    if required and len(present) == len(required):
        return "exact", required, present
    if present:
        return "likely", required, present
    return "missing", required, present
