"""Globale Project-Brief-Defaults pro Sprache (unter ``data/``).

Projektspezifisch bleiben Titel und die Datei ``project_brief.json``.
Ton, Negativregeln, Zusatzprompt und Titel-Referenzen können pro Sprache
als Standard liegen — analog zu den ElevenLabs-Voice-Defaults.
"""

from __future__ import annotations

import json
from pathlib import Path

from otio_app.config import ensure_data_dir
from otio_app.defaults import (
    BRIEF_LANGUAGE_CHOICES,
    PROJECT_BRIEF_DEFAULTS_FILENAME,
    PROJECT_BRIEF_TITLE_REFERENCE_SLOTS,
)
from otio_app.project_layout import language_folder_name
from otio_app.services.voiceover_generation.models import (
    ProjectBrief,
    ProjectBriefDefaultsDocument,
    ProjectBriefLanguageDefaults,
)

__all__ = [
    "get_project_brief_defaults_path",
    "normalize_brief_language",
    "normalize_title_references",
    "title_references_for_ui",
    "load_brief_defaults_document",
    "save_brief_defaults_document",
    "load_language_brief_defaults",
    "save_language_brief_defaults",
    "delete_language_brief_defaults",
    "language_defaults_from_brief",
    "apply_language_defaults_to_brief",
]


def get_project_brief_defaults_path() -> Path:
    return ensure_data_dir() / PROJECT_BRIEF_DEFAULTS_FILENAME


def normalize_brief_language(language: str) -> str:
    key = language_folder_name(language or "DE")
    if key in BRIEF_LANGUAGE_CHOICES:
        return key
    return "DE"


def normalize_title_references(values: list[str] | None) -> list[str]:
    cleaned = [str(item).strip() for item in (values or []) if str(item).strip()]
    return cleaned[:PROJECT_BRIEF_TITLE_REFERENCE_SLOTS]


def title_references_for_ui(values: list[str] | None) -> list[str]:
    padded = list(normalize_title_references(values))
    while len(padded) < PROJECT_BRIEF_TITLE_REFERENCE_SLOTS:
        padded.append("")
    return padded


def load_brief_defaults_document() -> ProjectBriefDefaultsDocument:
    path = get_project_brief_defaults_path()
    if not path.is_file():
        return ProjectBriefDefaultsDocument()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return ProjectBriefDefaultsDocument.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return ProjectBriefDefaultsDocument()


def save_brief_defaults_document(
    document: ProjectBriefDefaultsDocument,
) -> ProjectBriefDefaultsDocument:
    path = get_project_brief_defaults_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document.model_dump_json(indent=2), encoding="utf-8")
    return document


def load_language_brief_defaults(language: str) -> ProjectBriefLanguageDefaults | None:
    key = normalize_brief_language(language)
    return load_brief_defaults_document().by_language.get(key)


def save_language_brief_defaults(
    language: str,
    defaults: ProjectBriefLanguageDefaults | ProjectBrief,
) -> ProjectBriefLanguageDefaults:
    key = normalize_brief_language(language)
    entry = language_defaults_from_brief(defaults)
    document = load_brief_defaults_document()
    updated = dict(document.by_language)
    updated[key] = entry
    save_brief_defaults_document(ProjectBriefDefaultsDocument(by_language=updated))
    return entry


def delete_language_brief_defaults(language: str) -> None:
    key = normalize_brief_language(language)
    document = load_brief_defaults_document()
    if key not in document.by_language:
        return
    updated = dict(document.by_language)
    del updated[key]
    save_brief_defaults_document(ProjectBriefDefaultsDocument(by_language=updated))


def language_defaults_from_brief(
    brief: ProjectBriefLanguageDefaults | ProjectBrief,
) -> ProjectBriefLanguageDefaults:
    return ProjectBriefLanguageDefaults(
        tone_tags=list(brief.tone_tags),
        negative_rule_flags=dict(brief.negative_rule_flags),
        negative_rules_freetext=brief.negative_rules_freetext,
        forbidden_phrases=list(brief.forbidden_phrases),
        global_extra_prompt=brief.global_extra_prompt,
        title_references=normalize_title_references(brief.title_references),
    )


def apply_language_defaults_to_brief(
    brief: ProjectBrief,
    defaults: ProjectBriefLanguageDefaults,
    *,
    keep_title: bool = True,
) -> ProjectBrief:
    return brief.model_copy(
        update={
            "tone_tags": list(defaults.tone_tags),
            "negative_rule_flags": dict(defaults.negative_rule_flags),
            "negative_rules_freetext": defaults.negative_rules_freetext,
            "forbidden_phrases": list(defaults.forbidden_phrases),
            "global_extra_prompt": defaults.global_extra_prompt,
            "title_references": normalize_title_references(defaults.title_references),
            **({} if keep_title else {"video_title": ""}),
        }
    )
