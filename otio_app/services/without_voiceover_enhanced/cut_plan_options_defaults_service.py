"""Globale Enhanced-Cut-Plan-Settings pro Sprache (unter ``data/``).

Projektspezifisch bleibt ``cut_plan_options.json`` plus erzeugte Cuts,
Timing, Funnel und OTIO. Die Cut Plan Settings (Modus, Stil, LLM-Cut-Modell,
Shot-Längen, Reuse, Vor-/Nachlauf, Still, Music/SFX-Anzahl, …) können pro
Sprache als Standard liegen — analog zu Brief, Style, Dramaturgie und Intro.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

from otio_app.config import ensure_data_dir
from otio_app.defaults import CUT_PLAN_OPTIONS_DEFAULTS_FILENAME
from otio_app.models import Project
from otio_app.services.voiceover_generation.project_brief_defaults_service import (
    normalize_brief_language,
)
from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
    CutPlanOptions,
    _normalize_payload,
    default_cut_plan_options,
)

__all__ = [
    "CutPlanOptionsDefaultsDocument",
    "apply_language_defaults_to_options",
    "default_cut_plan_options_for_project",
    "delete_language_cut_plan_defaults",
    "get_cut_plan_options_defaults_path",
    "load_cut_plan_options_defaults_document",
    "load_language_cut_plan_defaults",
    "save_cut_plan_options_defaults_document",
    "save_language_cut_plan_defaults",
]


class CutPlanOptionsDefaultsDocument(BaseModel):
    by_language: dict[str, CutPlanOptions] = Field(default_factory=dict)


def get_cut_plan_options_defaults_path() -> Path:
    return ensure_data_dir() / CUT_PLAN_OPTIONS_DEFAULTS_FILENAME


def load_cut_plan_options_defaults_document() -> CutPlanOptionsDefaultsDocument:
    path = get_cut_plan_options_defaults_path()
    if not path.is_file():
        return CutPlanOptionsDefaultsDocument()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        raw_by_language = payload.get("by_language") if isinstance(payload, dict) else {}
        if not isinstance(raw_by_language, dict):
            return CutPlanOptionsDefaultsDocument()
        by_language: dict[str, CutPlanOptions] = {}
        for key, entry in raw_by_language.items():
            if not isinstance(entry, dict):
                continue
            language = normalize_brief_language(str(key))
            by_language[language] = _normalize_payload(entry)
        return CutPlanOptionsDefaultsDocument(by_language=by_language)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return CutPlanOptionsDefaultsDocument()


def save_cut_plan_options_defaults_document(
    document: CutPlanOptionsDefaultsDocument,
) -> CutPlanOptionsDefaultsDocument:
    path = get_cut_plan_options_defaults_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document.model_dump_json(indent=2), encoding="utf-8")
    return document


def load_language_cut_plan_defaults(language: str) -> CutPlanOptions | None:
    key = normalize_brief_language(language)
    return load_cut_plan_options_defaults_document().by_language.get(key)


def save_language_cut_plan_defaults(
    language: str,
    options: CutPlanOptions,
) -> CutPlanOptions:
    key = normalize_brief_language(language)
    entry = apply_language_defaults_to_options(default_cut_plan_options(), options)
    document = load_cut_plan_options_defaults_document()
    updated = dict(document.by_language)
    updated[key] = entry
    save_cut_plan_options_defaults_document(
        CutPlanOptionsDefaultsDocument(by_language=updated)
    )
    return entry


def delete_language_cut_plan_defaults(language: str) -> None:
    key = normalize_brief_language(language)
    document = load_cut_plan_options_defaults_document()
    if key not in document.by_language:
        return
    updated = dict(document.by_language)
    del updated[key]
    save_cut_plan_options_defaults_document(
        CutPlanOptionsDefaultsDocument(by_language=updated)
    )


def apply_language_defaults_to_options(
    options: CutPlanOptions,
    defaults: CutPlanOptions,
) -> CutPlanOptions:
    del options
    return _normalize_payload(defaults.model_dump())


def default_cut_plan_options_for_project(project: Project) -> CutPlanOptions:
    base = default_cut_plan_options()
    language = normalize_brief_language(project.language)
    defaults = load_language_cut_plan_defaults(language)
    if defaults is None:
        return base
    return apply_language_defaults_to_options(base, defaults)
