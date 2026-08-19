"""Globale Intro-Settings-Defaults pro Sprache (unter ``data/``).

Projektspezifisch bleiben ``intro_hook_settings.json`` sowie erzeugte
Varianten und die Intro-Bestätigung. Wortziel, Tonalität, Freitext-Regel
und die übrigen Intro-Flags können pro Sprache als Standard liegen —
analog zu Brief-, Style- und Dramaturgie-Defaults.
"""

from __future__ import annotations

import json
from pathlib import Path

from otio_app.config import ensure_data_dir
from otio_app.defaults import (
    INTRO_HOOK_DEFAULTS_FILENAME,
    INTRO_HOOK_DEFAULT_TARGET_WORDS,
    VOICEOVER_GEN_DEFAULT_WORD_TOLERANCE_PERCENT,
)
from otio_app.services.voiceover_generation.models import (
    IntroHookDefaultsDocument,
    IntroHookLanguageDefaults,
    IntroHookSettings,
)
from otio_app.services.voiceover_generation.project_brief_defaults_service import (
    normalize_brief_language,
)

_INTRO_TARGET_WORDS_INPUT_MAX = 500

__all__ = [
    "apply_language_defaults_to_settings",
    "clamp_intro_target_words",
    "clamp_intro_tolerance_percent",
    "delete_language_intro_defaults",
    "get_intro_hook_defaults_path",
    "language_defaults_from_settings",
    "load_intro_hook_defaults_document",
    "load_language_intro_defaults",
    "save_intro_hook_defaults_document",
    "save_language_intro_defaults",
]


def get_intro_hook_defaults_path() -> Path:
    return ensure_data_dir() / INTRO_HOOK_DEFAULTS_FILENAME


def clamp_intro_target_words(value: int | None) -> int:
    try:
        raw = int(value or 0)
    except (TypeError, ValueError):
        raw = INTRO_HOOK_DEFAULT_TARGET_WORDS
    if raw <= 0:
        raw = INTRO_HOOK_DEFAULT_TARGET_WORDS
    return max(1, min(_INTRO_TARGET_WORDS_INPUT_MAX, raw))


def clamp_intro_tolerance_percent(value: int | None) -> int:
    try:
        raw = int(
            value if value is not None else VOICEOVER_GEN_DEFAULT_WORD_TOLERANCE_PERCENT
        )
    except (TypeError, ValueError):
        raw = VOICEOVER_GEN_DEFAULT_WORD_TOLERANCE_PERCENT
    return max(0, min(100, raw))


def _clean_phrases(values: list[str] | None) -> list[str]:
    return [str(item).strip() for item in (values or []) if str(item).strip()]


def load_intro_hook_defaults_document() -> IntroHookDefaultsDocument:
    path = get_intro_hook_defaults_path()
    if not path.is_file():
        return IntroHookDefaultsDocument()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        loaded = IntroHookDefaultsDocument.model_validate(payload)
        return loaded.model_copy(
            update={
                "by_language": {
                    key: language_defaults_from_settings(entry)
                    for key, entry in loaded.by_language.items()
                }
            }
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return IntroHookDefaultsDocument()


def save_intro_hook_defaults_document(
    document: IntroHookDefaultsDocument,
) -> IntroHookDefaultsDocument:
    path = get_intro_hook_defaults_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document.model_dump_json(indent=2), encoding="utf-8")
    return document


def load_language_intro_defaults(language: str) -> IntroHookLanguageDefaults | None:
    key = normalize_brief_language(language)
    return load_intro_hook_defaults_document().by_language.get(key)


def save_language_intro_defaults(
    language: str,
    defaults: IntroHookLanguageDefaults | IntroHookSettings,
) -> IntroHookLanguageDefaults:
    key = normalize_brief_language(language)
    entry = language_defaults_from_settings(defaults)
    document = load_intro_hook_defaults_document()
    updated = dict(document.by_language)
    updated[key] = entry
    save_intro_hook_defaults_document(IntroHookDefaultsDocument(by_language=updated))
    return entry


def delete_language_intro_defaults(language: str) -> None:
    key = normalize_brief_language(language)
    document = load_intro_hook_defaults_document()
    if key not in document.by_language:
        return
    updated = dict(document.by_language)
    del updated[key]
    save_intro_hook_defaults_document(IntroHookDefaultsDocument(by_language=updated))


def language_defaults_from_settings(
    settings: IntroHookLanguageDefaults | IntroHookSettings,
) -> IntroHookLanguageDefaults:
    return IntroHookLanguageDefaults(
        target_words=clamp_intro_target_words(settings.target_words),
        word_tolerance_percent=clamp_intro_tolerance_percent(
            settings.word_tolerance_percent
        ),
        tone=(settings.tone or "").strip() or "cinematic",
        freeform_rule_for_llm=(settings.freeform_rule_for_llm or "").strip(),
        forbidden_phrases=_clean_phrases(settings.forbidden_phrases),
        allow_questions=bool(settings.allow_questions),
        allow_strong_claim=bool(settings.allow_strong_claim),
        allow_direct_place_name=bool(settings.allow_direct_place_name),
        allow_tease_multiple_places=bool(settings.allow_tease_multiple_places),
        must_include=_clean_phrases(settings.must_include),
        must_avoid=_clean_phrases(settings.must_avoid),
    )


def apply_language_defaults_to_settings(
    settings: IntroHookSettings,
    defaults: IntroHookLanguageDefaults,
) -> IntroHookSettings:
    entry = language_defaults_from_settings(defaults)
    return IntroHookSettings(
        project_id=settings.project_id,
        language=settings.language,
        target_words=entry.target_words,
        word_tolerance_percent=entry.word_tolerance_percent,
        tone=entry.tone,
        freeform_rule_for_llm=entry.freeform_rule_for_llm,
        forbidden_phrases=list(entry.forbidden_phrases),
        allow_questions=entry.allow_questions,
        allow_strong_claim=entry.allow_strong_claim,
        allow_direct_place_name=entry.allow_direct_place_name,
        allow_tease_multiple_places=entry.allow_tease_multiple_places,
        must_include=list(entry.must_include),
        must_avoid=list(entry.must_avoid),
    )
