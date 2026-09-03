"""Kartennamen per Brief-LLM in die Videosprache — mit Ortskontext.

Quelle ist immer der Dramaturgie-Ordnername, nie OSM/Wikipedia-Titel.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from otio_app.models import Project
from otio_app.services.voiceover_generation.project_brief_defaults_service import (
    normalize_brief_language,
)
from otio_app.services.without_voiceover_enhanced.io_utils import write_json
from otio_app.services.without_voiceover_enhanced.maps.models import (
    RENDER_STATUS_DONE,
    RENDER_STATUS_IDLE,
    MapPlanDocument,
)
from otio_app.services.without_voiceover_enhanced.maps.remotion_payload import (
    country_label,
    localize_map_place_label,
    overlay_label_is_plausible,
)
from otio_app.services.without_voiceover_enhanced.paths import map_overlay_labels_path

TranslateFn = Callable[[str], str]

_LANGUAGE_DISPLAY_NAMES: dict[str, str] = {
    "DE": "German",
    "EN": "English",
    "FR": "French",
    "ES": "Spanish",
    "PT": "Portuguese",
    "IT": "Italian",
    "JP": "Japanese",
    "KR": "Korean",
}


def _language_display_name(language: str) -> str:
    code = normalize_brief_language(language)
    return _LANGUAGE_DISPLAY_NAMES.get(code) or code or "German"


def _cache_key(language: str, original: str) -> str:
    return f"{normalize_brief_language(language)}|{original.strip()}"


def load_overlay_label_cache(project: Project) -> dict[str, str]:
    path = map_overlay_labels_path(project)
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in payload.items():
        label = " ".join(str(value or "").split())
        if str(key).strip() and label:
            out[str(key)] = label
    return out


def save_overlay_label_cache(project: Project, cache: dict[str, str]) -> None:
    write_json(map_overlay_labels_path(project), cache)


def fallback_overlay_label(original: str, language: str) -> str:
    return localize_map_place_label(original, language) or original


def build_map_label_translate_prompt(
    *,
    language: str,
    country: str,
    rows: list[dict[str, str]],
) -> str:
    lang_name = _language_display_name(language)
    country_en = country_label(country, "EN") if country else ""
    region = country_en or country or "unknown"
    order = [
        f"{index}. {row['name']}"
        for index, row in enumerate(rows, start=1)
        if row.get("name")
    ]
    return (
        "Translate travel-video map labels into the target language.\n"
        f"Target language: {lang_name} ({normalize_brief_language(language)})\n"
        f"Country/region: {region}\n"
        "These names appear as short FROM → TO labels on a vintage route map.\n"
        "Context — chapter order:\n"
        + ("\n".join(order) if order else "(none)")
        + "\n"
        "Rules:\n"
        "- Translate each folder name into a natural place label in the target language.\n"
        "- Keep real place names; translate landscape words (gorge, lake, falls, "
        "valley, castle, pass, caves, national park, island).\n"
        "- Two places joined by & stay two places, joined as a native speaker would "
        "(e, et, y, und, and, e).\n"
        "- Use previous/next only to disambiguate the same name, never copy them.\n"
        "- Output MUST be a place name derived from the folder name. "
        "Never a sentence, never payment/help/socket/card text, never OSM/Wikipedia titles.\n"
        "- Keep it short (few words). No quotes, no trailing period.\n"
        "Return JSON only: "
        '{"places":[{"id":"folder name","label":"translated label"}]}.\n'
        f"{json.dumps({'places': rows}, ensure_ascii=False)}\n"
    )


def _parse_translated_places(payload: Any) -> dict[str, str]:
    items = payload.get("places") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return {}
    mapping: dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        chapter_id = str(item.get("id") or "").strip()
        label = " ".join(str(item.get("label") or "").split())
        if chapter_id and label:
            mapping[chapter_id] = label
    return mapping


def translate_map_overlay_labels_with_llm(
    project: Project,
    rows: list[dict[str, str]],
    *,
    language: str,
    country: str,
    translate_fn: TranslateFn | None = None,
) -> dict[str, str]:
    """Ein Brief-LLM-Call für alle Kartennamen. Leer bei Fehler."""
    if not rows:
        return {}
    prompt = build_map_label_translate_prompt(
        language=language, country=country, rows=rows
    )
    try:
        if translate_fn is not None:
            raw = translate_fn(prompt)
        else:
            from otio_app.services.gemini_client import _extract_json
            from otio_app.services.plan_llm_client import generate_plan_text
            from otio_app.services.voiceover_generation.model_settings_service import (
                load_model_settings,
                resolve_llm_model_id,
            )

            settings = load_model_settings(project)
            role = settings.project_brief
            model = resolve_llm_model_id(role.provider, role.model)
            raw = generate_plan_text(
                prompt=prompt,
                model=model,
                max_output_tokens=1600,
                disable_thinking=True,
            )
            payload = _extract_json(raw)
            return _parse_translated_places(payload)
        from otio_app.services.gemini_client import _extract_json

        payload = _extract_json(raw) if isinstance(raw, str) else raw
        if isinstance(payload, str):
            payload = _extract_json(payload)
        return _parse_translated_places(payload)
    except Exception:
        return {}


def _chapter_rows(plan: MapPlanDocument) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    maps = list(plan.maps or [])
    for index, item in enumerate(maps):
        original = str(item.original_chapter_label or item.chapter_id or "").strip()
        if not original:
            continue
        previous = ""
        nxt = ""
        if index > 0:
            previous = str(
                maps[index - 1].original_chapter_label or maps[index - 1].chapter_id
            ).strip()
        if index + 1 < len(maps):
            nxt = str(
                maps[index + 1].original_chapter_label or maps[index + 1].chapter_id
            ).strip()
        rows.append(
            {
                "id": str(item.chapter_id or original),
                "name": original,
                "previous": previous,
                "next": nxt,
            }
        )
    return rows


def apply_overlay_labels(
    plan: MapPlanDocument, labels_by_id: dict[str, str]
) -> MapPlanDocument:
    """Schreibt übersetzte Namen auf den Plan und invalidiert veraltete Renders."""
    from otio_app.services.without_voiceover_enhanced.maps.plan_service import (
        compute_plan_hash,
    )

    resolved: dict[str, str] = {}
    for item in plan.maps:
        chapter_id = str(item.chapter_id or "")
        original = str(item.original_chapter_label or chapter_id)
        candidate = labels_by_id.get(chapter_id) or labels_by_id.get(original) or ""
        candidate = " ".join(str(candidate).split())
        if candidate and overlay_label_is_plausible(original, candidate):
            resolved[chapter_id] = candidate
        else:
            resolved[chapter_id] = item.localized_display_label or fallback_overlay_label(
                original, plan.language
            )

    for item in plan.maps:
        new_label = resolved.get(item.chapter_id) or item.localized_display_label
        from_label = ""
        if item.from_chapter_id:
            from_label = (
                resolved.get(item.from_chapter_id)
                or item.from_localized_display_label
                or ""
            )
        changed = (
            new_label != item.localized_display_label
            or from_label != (item.from_localized_display_label or "")
        )
        item.localized_display_label = new_label
        item.from_localized_display_label = from_label
        item.plan_hash = compute_plan_hash(item)
        if changed and item.render_status == RENDER_STATUS_DONE:
            item.render_status = RENDER_STATUS_IDLE
            item.output_path = ""
            item.media_hash = ""
            item.progress = 0.0
            item.error_detail = ""
    return plan


def localize_map_plan_with_llm(
    project: Project,
    plan: MapPlanDocument,
    *,
    translate_fn: TranslateFn | None = None,
) -> MapPlanDocument:
    """Übersetzt sichtbare Kartennamen; Cache + Fallback ohne Netz."""
    rows = _chapter_rows(plan)
    if not rows:
        return plan
    language = plan.language or normalize_brief_language(project.language)
    country = plan.country or (project.video_place or "")
    cache = load_overlay_label_cache(project)
    labels: dict[str, str] = {}
    missing_rows: list[dict[str, str]] = []
    for row in rows:
        original = row["name"]
        chapter_id = row["id"]
        cached = cache.get(_cache_key(language, original), "")
        if cached and overlay_label_is_plausible(original, cached):
            labels[chapter_id] = cached
        else:
            missing_rows.append(row)

    if missing_rows:
        context_rows = rows
        translated = translate_map_overlay_labels_with_llm(
            project,
            context_rows,
            language=language,
            country=country,
            translate_fn=translate_fn,
        )
        for row in missing_rows:
            chapter_id = row["id"]
            original = row["name"]
            candidate = translated.get(chapter_id) or translated.get(original) or ""
            if candidate and overlay_label_is_plausible(original, candidate):
                labels[chapter_id] = candidate
                cache[_cache_key(language, original)] = candidate
            else:
                labels[chapter_id] = fallback_overlay_label(original, language)
        save_overlay_label_cache(project, cache)

    return apply_overlay_labels(plan, labels)
