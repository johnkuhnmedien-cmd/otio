"""Keyword-Suchqueries für Supplement-Assets."""

from __future__ import annotations

import re

from otio_app.analysis_models import SupplementRequest

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


def preferred_search_query(request: SupplementRequest) -> str:
    for language in ("en", "de"):
        queries = request.search_queries.get(language, [])
        if queries:
            query = str(queries[0]).strip()
            if query:
                return query
    return build_keyword_query(
        folder_name=request.folder_name,
        visual_requirement=request.visual_requirement,
        passage_text=request.passage_text,
    )


def request_with_keyword_query(request: SupplementRequest, query: str | None = None) -> SupplementRequest:
    selected = (query or preferred_search_query(request)).strip()
    return request.model_copy(
        update={
            "search_queries": {
                **request.search_queries,
                "en": [selected],
            }
        }
    )
