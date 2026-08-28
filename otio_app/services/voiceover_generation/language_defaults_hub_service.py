"""Lesen, Kopieren und Status der globalen Sprach-Standards unter ``data/``.

Die Hub-Seite bearbeitet dieselben Dateien wie „Als Standard für {Sprache}
speichern“ auf den einzelnen Tabs — ohne dass ein Projekt offen sein muss.
"""

from __future__ import annotations

from otio_app.defaults import BRIEF_LANGUAGE_CHOICES
from otio_app.services.voiceover_generation.dramaturgy_defaults_service import (
    delete_language_dramaturgy_word_defaults,
    load_language_dramaturgy_word_defaults,
    save_language_dramaturgy_word_defaults,
)
from otio_app.services.voiceover_generation.elevenlabs_voice_defaults_service import (
    delete_language_voice_defaults,
    load_language_voice_defaults,
    save_language_voice_defaults,
)
from otio_app.services.voiceover_generation.intro_hook_defaults_service import (
    delete_language_intro_defaults,
    load_language_intro_defaults,
    save_language_intro_defaults,
)
from otio_app.services.voiceover_generation.language_defaults_catalog import (
    LanguageStandardFile,
    list_language_standard_files,
)
from otio_app.services.voiceover_generation.project_brief_defaults_service import (
    delete_language_brief_defaults,
    load_language_brief_defaults,
    normalize_brief_language,
    save_language_brief_defaults,
)
from otio_app.services.voiceover_generation.style_reference_defaults_service import (
    delete_language_style_defaults,
    load_language_style_defaults,
    save_language_style_defaults,
)
from otio_app.services.without_voiceover_enhanced.cut_plan_options_defaults_service import (
    delete_language_cut_plan_defaults,
    load_language_cut_plan_defaults,
    save_language_cut_plan_defaults,
)

__all__ = [
    "copy_language_defaults",
    "delete_language_standard",
    "language_defaults_overview",
    "language_has_standard",
]


_LOADERS = {
    "project_brief": load_language_brief_defaults,
    "style_references": load_language_style_defaults,
    "dramaturgy": load_language_dramaturgy_word_defaults,
    "intro": load_language_intro_defaults,
    "elevenlabs_voice": load_language_voice_defaults,
    "cut_plan_options": load_language_cut_plan_defaults,
}

_SAVERS = {
    "project_brief": save_language_brief_defaults,
    "style_references": save_language_style_defaults,
    "dramaturgy": save_language_dramaturgy_word_defaults,
    "intro": save_language_intro_defaults,
    "elevenlabs_voice": save_language_voice_defaults,
    "cut_plan_options": save_language_cut_plan_defaults,
}

_DELETERS = {
    "project_brief": delete_language_brief_defaults,
    "style_references": delete_language_style_defaults,
    "dramaturgy": delete_language_dramaturgy_word_defaults,
    "intro": delete_language_intro_defaults,
    "elevenlabs_voice": delete_language_voice_defaults,
    "cut_plan_options": delete_language_cut_plan_defaults,
}


def language_has_standard(key: str, language: str) -> bool:
    loader = _LOADERS.get(key)
    if loader is None:
        return False
    return loader(normalize_brief_language(language)) is not None


def language_defaults_overview() -> dict[str, dict[str, bool]]:
    """``{language: {catalog_key: is_set}}`` für alle Katalog-Sprachen."""
    keys = [item.key for item in list_language_standard_files()]
    overview: dict[str, dict[str, bool]] = {}
    for language in BRIEF_LANGUAGE_CHOICES:
        overview[language] = {
            key: language_has_standard(key, language) for key in keys
        }
    return overview


def copy_language_defaults(source_language: str, target_language: str) -> list[str]:
    """Kopiert gesetzte Standards von einer Sprache auf eine andere.

    Der Dramaturgie-Planungsmodus bleibt global und wird nicht kopiert.
    Fehlt ein Quell-Eintrag, bleibt das Ziel unverändert.
    """
    source = normalize_brief_language(source_language)
    target = normalize_brief_language(target_language)
    if source == target:
        return []
    copied: list[str] = []
    for item in list_language_standard_files():
        loader = _LOADERS.get(item.key)
        saver = _SAVERS.get(item.key)
        if loader is None or saver is None:
            continue
        entry = loader(source)
        if entry is None:
            continue
        saver(target, entry)
        copied.append(item.key)
    return copied


def delete_language_standard(key: str, language: str) -> None:
    deleter = _DELETERS.get(key)
    if deleter is None:
        raise KeyError(f"Unbekannter Sprachstandard: {key}")
    deleter(normalize_brief_language(language))


def catalog_item(key: str) -> LanguageStandardFile:
    for item in list_language_standard_files():
        if item.key == key:
            return item
    raise KeyError(f"Unbekannter Sprachstandard: {key}")
