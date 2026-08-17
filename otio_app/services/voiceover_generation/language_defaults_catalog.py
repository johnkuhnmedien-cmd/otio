"""Katalog der globalen Sprach-Standards unter ``data/``.

Alle per-Sprache-Defaults liegen im App-Datenordner (``ensure_data_dir()``),
typisch ``<Repo>/data/`` bzw. lokal ``…/otio/data/``. Die JSON-Dateien sind
nach ``by_language["PT"]`` usw. keyed (über ``normalize_brief_language``).

Projektspezifische Kopien bleiben im Language-Work-Dir
(``_otio_enhanced/<LANG>/voiceover_generation/…``). „Als Standard speichern“
schreibt die globale ``data/*.json``; der Projektordner wird zusätzlich
aktualisiert.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from otio_app.config import ensure_data_dir
from otio_app.defaults import (
    CUT_PLAN_OPTIONS_DEFAULTS_FILENAME,
    DRAMATURGY_DEFAULTS_FILENAME,
    ELEVENLABS_VOICE_DEFAULTS_FILENAME,
    INTRO_HOOK_DEFAULTS_FILENAME,
    PROJECT_BRIEF_DEFAULTS_FILENAME,
    RAW_STYLE_LIBRARY_FILENAME,
    STYLE_PROFILE_LIBRARY_FILENAME,
    STYLE_REFERENCE_DEFAULTS_FILENAME,
)
from otio_app.services.voiceover_generation.dramaturgy_defaults_service import (
    get_dramaturgy_defaults_path,
)
from otio_app.services.voiceover_generation.elevenlabs_voice_defaults_service import (
    get_elevenlabs_voice_defaults_path,
)
from otio_app.services.voiceover_generation.intro_hook_defaults_service import (
    get_intro_hook_defaults_path,
)
from otio_app.services.without_voiceover_enhanced.cut_plan_options_defaults_service import (
    get_cut_plan_options_defaults_path,
)
from otio_app.services.voiceover_generation.project_brief_defaults_service import (
    get_project_brief_defaults_path,
)
from otio_app.services.voiceover_generation.raw_style_library_service import (
    get_raw_style_library_path,
)
from otio_app.services.voiceover_generation.style_profile_library_service import (
    get_style_profile_library_path,
)
from otio_app.services.voiceover_generation.style_reference_defaults_service import (
    get_style_reference_defaults_path,
)

__all__ = [
    "LanguageStandardFile",
    "get_language_standard",
    "language_standards_dir",
    "list_language_standard_files",
    "list_shared_library_files",
]


@dataclass(frozen=True)
class LanguageStandardFile:
    """Eine globale JSON-Datei unter ``data/``."""

    key: str
    tab: str
    filename: str
    path: Path
    stores: str
    not_stored: str
    per_language: bool = True


def language_standards_dir() -> Path:
    """Ordner, in dem alle Sprach-Standards liegen."""
    return ensure_data_dir()


def list_language_standard_files() -> list[LanguageStandardFile]:
    """Alle per-Sprache-Standards (Brief, Style, Dramaturgie, Intro, Voice-ID, Cut Plan)."""
    return [
        LanguageStandardFile(
            key="project_brief",
            tab="① Project Brief",
            filename=PROJECT_BRIEF_DEFAULTS_FILENAME,
            path=get_project_brief_defaults_path(),
            stores=(
                "Ton, Negativregeln, Freitext, verbotene Phrasen, "
                "Zusatzprompt, drei Titel-Referenzen"
            ),
            not_stored="Videotitel (bleibt projektspezifisch)",
        ),
        LanguageStandardFile(
            key="style_references",
            tab="② Style References",
            filename=STYLE_REFERENCE_DEFAULTS_FILENAME,
            path=get_style_reference_defaults_path(),
            stores=(
                "Modus, Raw-Texte, Intro-/Segment-Referenzen, "
                "optional Style-Profile-Snapshot"
            ),
            not_stored="Uploads (bleiben projektspezifisch)",
        ),
        LanguageStandardFile(
            key="dramaturgy",
            tab="③ Dramaturgie",
            filename=DRAMATURGY_DEFAULTS_FILENAME,
            path=get_dramaturgy_defaults_path(),
            stores=(
                "Planungsmodus (global, Auto-Lauf) und Wortziel + Toleranz "
                "pro Sprache"
            ),
            not_stored="Bestätigter Dramaturgie-Plan",
        ),
        LanguageStandardFile(
            key="intro",
            tab="⑤ Intro",
            filename=INTRO_HOOK_DEFAULTS_FILENAME,
            path=get_intro_hook_defaults_path(),
            stores=(
                "Wortband, Tonalität, Freitext-Regel, Flags, "
                "Must-include / Must-avoid"
            ),
            not_stored="Erzeugte Varianten und Intro-Bestätigung",
        ),
        LanguageStandardFile(
            key="elevenlabs_voice",
            tab="⑥ Audio / ElevenLabs",
            filename=ELEVENLABS_VOICE_DEFAULTS_FILENAME,
            path=get_elevenlabs_voice_defaults_path(),
            stores="Voice-ID, Modell und Stimm-Parameter pro Sprache",
            not_stored="API-Key (liegt in user_secrets.env / Umgebung)",
        ),
        LanguageStandardFile(
            key="cut_plan_options",
            tab="⑦ Cut Plan",
            filename=CUT_PLAN_OPTIONS_DEFAULTS_FILENAME,
            path=get_cut_plan_options_defaults_path(),
            stores=(
                "Modus, Unified-Stil, LLM-Cut-Modell, Shot-Min/Max, Reuse, "
                "Vor-/Nachlauf, Intro-Hüllen, Still/Pan, Titel, Music-Anzahl, "
                "SFX-Planner-Modell, SFX-Maximum"
            ),
            not_stored="Erzeugte Cuts, Timing, Funnel, OTIO",
        ),
    ]


def list_shared_library_files() -> list[LanguageStandardFile]:
    """Globale Bibliotheken unter ``data/`` — nicht nach Sprache keyed."""
    return [
        LanguageStandardFile(
            key="raw_style_library",
            tab="② Style — Raw-Bibliothek",
            filename=RAW_STYLE_LIBRARY_FILENAME,
            path=get_raw_style_library_path(),
            stores="Benannte Raw-Text-Snapshots (allgemein + Intro)",
            not_stored="Projekt-Uploads",
            per_language=False,
        ),
        LanguageStandardFile(
            key="style_profile_library",
            tab="② Style — Profile-Bibliothek",
            filename=STYLE_PROFILE_LIBRARY_FILENAME,
            path=get_style_profile_library_path(),
            stores="Benannte Style-Profile zum Wiederverwenden",
            not_stored="Projekt-Uploads",
            per_language=False,
        ),
    ]


def get_language_standard(key: str) -> LanguageStandardFile:
    for item in (*list_language_standard_files(), *list_shared_library_files()):
        if item.key == key:
            return item
    raise KeyError(f"Unbekannter Sprachstandard: {key}")
