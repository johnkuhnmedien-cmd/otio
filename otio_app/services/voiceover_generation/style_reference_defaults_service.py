"""Globale Style-Reference-Defaults pro Sprache (unter ``data/``).

Projektspezifisch bleiben ``voiceover_style_references.json`` und Uploads.
Modus, Raw-Texte, Beispiel-Referenzen und optional das Style Profile können
pro Sprache als Standard liegen — analog zu Brief- und Voice-Defaults.
"""

from __future__ import annotations

import json
from pathlib import Path

from otio_app.config import ensure_data_dir
from otio_app.defaults import (
    STYLE_REFERENCE_DEFAULT_SLOTS,
    STYLE_REFERENCE_DEFAULTS_FILENAME,
)
from otio_app.services.voiceover_generation.models import (
    STYLE_MODE_CHOICES,
    STYLE_MODE_PROFILE,
    STYLE_MODE_RAW_TEXT,
    StyleReferenceDefaultsDocument,
    StyleReferenceLanguageDefaults,
    VoiceoverStyleProfile,
    VoiceoverStyleReferences,
)
from otio_app.services.voiceover_generation.project_brief_defaults_service import (
    normalize_brief_language,
)


def _normalize_mode(style_mode: str | None) -> str:
    mode = (style_mode or STYLE_MODE_PROFILE).strip().lower()
    if mode in STYLE_MODE_CHOICES:
        return mode
    return STYLE_MODE_PROFILE

__all__ = [
    "get_style_reference_defaults_path",
    "normalize_style_reference_texts",
    "load_style_reference_defaults_document",
    "save_style_reference_defaults_document",
    "load_language_style_defaults",
    "save_language_style_defaults",
    "delete_language_style_defaults",
    "language_defaults_from_refs",
    "apply_language_defaults_to_refs",
]


def get_style_reference_defaults_path() -> Path:
    return ensure_data_dir() / STYLE_REFERENCE_DEFAULTS_FILENAME


def normalize_style_reference_texts(values: list[str] | None) -> list[str]:
    cleaned = [str(item).strip() for item in (values or []) if str(item).strip()]
    return cleaned[:STYLE_REFERENCE_DEFAULT_SLOTS]


def load_style_reference_defaults_document() -> StyleReferenceDefaultsDocument:
    path = get_style_reference_defaults_path()
    if not path.is_file():
        return StyleReferenceDefaultsDocument()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return StyleReferenceDefaultsDocument.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return StyleReferenceDefaultsDocument()


def save_style_reference_defaults_document(
    document: StyleReferenceDefaultsDocument,
) -> StyleReferenceDefaultsDocument:
    path = get_style_reference_defaults_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document.model_dump_json(indent=2), encoding="utf-8")
    return document


def load_language_style_defaults(language: str) -> StyleReferenceLanguageDefaults | None:
    key = normalize_brief_language(language)
    return load_style_reference_defaults_document().by_language.get(key)


def save_language_style_defaults(
    language: str,
    defaults: StyleReferenceLanguageDefaults | VoiceoverStyleReferences,
    *,
    style_profile: VoiceoverStyleProfile | None = None,
) -> StyleReferenceLanguageDefaults:
    key = normalize_brief_language(language)
    entry = language_defaults_from_refs(defaults, style_profile=style_profile)
    document = load_style_reference_defaults_document()
    updated = dict(document.by_language)
    updated[key] = entry
    save_style_reference_defaults_document(
        StyleReferenceDefaultsDocument(by_language=updated)
    )
    return entry


def delete_language_style_defaults(language: str) -> None:
    key = normalize_brief_language(language)
    document = load_style_reference_defaults_document()
    if key not in document.by_language:
        return
    updated = dict(document.by_language)
    del updated[key]
    save_style_reference_defaults_document(
        StyleReferenceDefaultsDocument(by_language=updated)
    )


def language_defaults_from_refs(
    refs: StyleReferenceLanguageDefaults | VoiceoverStyleReferences,
    *,
    style_profile: VoiceoverStyleProfile | None = None,
) -> StyleReferenceLanguageDefaults:
    mode = _normalize_mode(getattr(refs, "style_mode", None))
    profile = style_profile
    if profile is None and isinstance(refs, StyleReferenceLanguageDefaults):
        profile = refs.style_profile
    if mode == STYLE_MODE_RAW_TEXT:
        profile = None
    return StyleReferenceLanguageDefaults(
        style_mode=mode,
        raw_reference_text=(refs.raw_reference_text or "").strip(),
        raw_intro_reference_text=(refs.raw_intro_reference_text or "").strip(),
        raw_library_name=(refs.raw_library_name or "").strip(),
        intro_reference_texts=normalize_style_reference_texts(refs.intro_reference_texts),
        segment_reference_texts=normalize_style_reference_texts(
            refs.segment_reference_texts
        ),
        style_profile=profile,
    )


def apply_language_defaults_to_refs(
    refs: VoiceoverStyleReferences,
    defaults: StyleReferenceLanguageDefaults,
) -> VoiceoverStyleReferences:
    return refs.model_copy(
        update={
            "style_mode": _normalize_mode(defaults.style_mode),
            "raw_reference_text": defaults.raw_reference_text,
            "raw_intro_reference_text": defaults.raw_intro_reference_text,
            "raw_library_name": defaults.raw_library_name,
            "intro_reference_texts": list(defaults.intro_reference_texts),
            "segment_reference_texts": list(defaults.segment_reference_texts),
            "uploaded_file_names": [],
            "uploaded_file_texts": [],
        }
    )
