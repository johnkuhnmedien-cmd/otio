"""Gemeinsame ElevenLabs-Settings-UI (Classic + Enhanced).

Enthält nie den API-Key — nur Voice-ID, Modell und Stimm-Parameter.
Unterstützt globale Voice-Defaults pro Sprache plus Projekt-Override.
"""

from __future__ import annotations

import streamlit as st

from otio_app.defaults import (
    ELEVENLABS_DEFAULT_OUTPUT_FORMAT,
    ELEVENLABS_MODEL_PRESETS,
    ELEVENLABS_OUTPUT_FORMAT_PRESETS,
    normalize_elevenlabs_output_format,
)
from otio_app.models import Project
from otio_app.project_layout import language_folder_name
from otio_app.services.voiceover_generation.elevenlabs_client import (
    ElevenLabsTtsError,
    is_elevenlabs_configured,
)
from otio_app.services.voiceover_generation.elevenlabs_settings_service import (
    clear_project_elevenlabs_settings,
    describe_settings_source,
    has_project_elevenlabs_settings,
    load_elevenlabs_settings,
    save_elevenlabs_settings,
)
from otio_app.services.voiceover_generation.elevenlabs_voice_defaults_service import (
    load_language_voice_defaults,
    save_language_voice_defaults,
)
from otio_app.services.voiceover_generation.models import ElevenLabsSettings
from otio_app.services.voiceover_generation.tts_orchestration_service import (
    synthesize_test_voice,
)
from otio_app.ui.voiceover_generation.language_standards_ui import (
    render_language_standard_path_caption,
)

__all__ = [
    "render_elevenlabs_settings_form",
    "voice_id_is_set",
]

_SAMPLE_TEXTS = {
    "DE": "Dies ist ein kurzer Test der Stimme.",
    "EN": "This is a short voice test.",
    "FR": "Ceci est un court test de voix.",
    "ES": "Esta es una breve prueba de voz.",
    "PT": "Este é um breve teste de voz.",
    "IT": "Questo è un breve test vocale.",
    "JP": "これは短い音声テストです。",
    "KR": "이것은 짧은 음성 테스트입니다.",
}


def voice_id_is_set(project: Project) -> bool:
    return bool(load_elevenlabs_settings(project).voice_id.strip())


def _widget_keys(project: Project, key_prefix: str) -> dict[str, str]:
    pid = project.id
    return {
        "voice_id": f"{key_prefix}_voice_id_{pid}",
        "model": f"{key_prefix}_model_{pid}",
        "format": f"{key_prefix}_format_{pid}",
        "stability": f"{key_prefix}_stability_{pid}",
        "similarity": f"{key_prefix}_similarity_{pid}",
        "style": f"{key_prefix}_style_{pid}",
        "speaker_boost": f"{key_prefix}_speaker_boost_{pid}",
        "speed": f"{key_prefix}_speed_{pid}",
        "lang_code": f"{key_prefix}_lang_code_{pid}",
    }


def _sync_form_widgets_from_settings(
    project: Project,
    *,
    key_prefix: str,
    settings: ElevenLabsSettings,
) -> None:
    """Setzt Streamlit-Widget-Keys, damit Reload/Sprach-Standard sichtbar greift."""
    keys = _widget_keys(project, key_prefix)
    st.session_state[keys["voice_id"]] = settings.voice_id
    st.session_state[keys["model"]] = settings.model_id
    st.session_state[keys["format"]] = normalize_elevenlabs_output_format(
        settings.output_format,
        migrate_legacy_default=True,
    )
    st.session_state[keys["stability"]] = settings.stability
    st.session_state[keys["similarity"]] = settings.similarity_boost
    st.session_state[keys["style"]] = settings.style
    st.session_state[keys["speaker_boost"]] = settings.use_speaker_boost
    st.session_state[keys["speed"]] = settings.speed
    st.session_state[keys["lang_code"]] = settings.language_code


def render_elevenlabs_settings_form(
    project: Project,
    *,
    key_prefix: str,
    sample_language: str | None = None,
) -> ElevenLabsSettings:
    """Rendert Voice-ID/Modell/Parameter + Speichern / Sprach-Standard / Test Voice."""
    settings = load_elevenlabs_settings(project)
    lang_key = language_folder_name(project.language or "DE")
    has_language_default = load_language_voice_defaults(project.language) is not None
    has_project_override = has_project_elevenlabs_settings(project)

    st.subheader("ElevenLabs Settings")
    st.caption(
        "Voice-ID aus dem ElevenLabs-Dashboard (Voice Library). "
        "API-Key bleibt unter 🔑 API-Schlüssel."
    )
    st.caption(describe_settings_source(project))

    keys = _widget_keys(project, key_prefix)
    # Erstes Befüllen der Session-Keys aus geladenen Settings (Projekt oder Sprach-Standard).
    if keys["voice_id"] not in st.session_state:
        _sync_form_widgets_from_settings(project, key_prefix=key_prefix, settings=settings)

    voice_id = st.text_input(
        "Voice-ID",
        key=keys["voice_id"],
        help="z. B. 21m00Tcm4TlvDq8ikWAM — aus ElevenLabs → Voices kopieren.",
    )

    model_options = list(ELEVENLABS_MODEL_PRESETS)
    current_model = st.session_state.get(keys["model"], settings.model_id)
    if current_model not in model_options:
        model_options = [current_model, *model_options]
    model_id = st.selectbox(
        "Modell",
        options=model_options,
        key=keys["model"],
    )

    col1, col2 = st.columns(2)
    with col1:
        format_options = list(ELEVENLABS_OUTPUT_FORMAT_PRESETS)
        current_format = normalize_elevenlabs_output_format(
            st.session_state.get(keys["format"], settings.output_format)
        )
        if current_format not in format_options:
            format_options = [current_format, *format_options]
        output_format = st.selectbox(
            "Output-Format",
            options=format_options,
            key=keys["format"],
            help=(
                f"ElevenLabs output_format. Standard: {ELEVENLABS_DEFAULT_OUTPUT_FORMAT} "
                "(Resolve-tauglich, echter WAV-Container). "
                "Nach Formatwechsel TTS neu erzeugen."
            ),
        )
        if str(output_format or "").strip().lower().startswith("mp3"):
            st.caption(
                "Hinweis: Für DaVinci Resolve besser **wav_48000** — MP3 kann "
                "Waveform zeigen aber stumm abspielen. Nach Formatwechsel TTS neu erzeugen."
            )
        stability = st.slider(
            "Stability",
            0.0,
            1.0,
            key=keys["stability"],
        )
        similarity_boost = st.slider(
            "Similarity Boost",
            0.0,
            1.0,
            key=keys["similarity"],
        )
    with col2:
        style = st.slider(
            "Style",
            0.0,
            1.0,
            key=keys["style"],
        )
        use_speaker_boost = st.checkbox(
            "Speaker Boost",
            key=keys["speaker_boost"],
        )
        speed = st.slider(
            "Speed",
            0.25,
            4.0,
            key=keys["speed"],
        )

    with st.expander("Erweitert", expanded=False):
        language_code = st.text_input(
            "Language Code (optional — nur senden, wenn ausgefüllt)",
            key=keys["lang_code"],
            help="Wird nur an ElevenLabs gesendet, wenn nicht leer.",
        )

    updated = settings.model_copy(
        update={
            "voice_id": voice_id,
            "model_id": model_id,
            "output_format": output_format,
            "stability": stability,
            "similarity_boost": similarity_boost,
            "style": style,
            "use_speaker_boost": use_speaker_boost,
            "speed": speed,
            "language_code": language_code,
        }
    )

    col_save, col_lang, col_reload = st.columns(3)
    with col_save:
        if st.button(
            "Settings speichern",
            key=f"{key_prefix}_settings_save_{project.id}",
            help="Nur für dieses Projekt (Override).",
        ):
            save_elevenlabs_settings(project, updated)
            st.success("ElevenLabs-Settings für dieses Projekt gespeichert.")
            st.rerun()
    with col_lang:
        if st.button(
            f"Als Standard für {lang_key} speichern",
            key=f"{key_prefix}_settings_save_lang_{project.id}",
            type="primary",
            help=(
                f"Global unter data/ speichern. Andere {lang_key}-Projekte "
                "ohne eigenen Override laden diese Settings automatisch."
            ),
        ):
            save_language_voice_defaults(project.language, updated)
            # Auch im aktuellen Projekt speichern, damit der Override konsistent ist.
            save_elevenlabs_settings(project, updated)
            st.success(
                f"Als globaler Standard für **{lang_key}** gespeichert "
                "(und für dieses Projekt übernommen)."
            )
            st.rerun()
    with col_reload:
        if st.button("Neu laden", key=f"{key_prefix}_settings_reload_{project.id}"):
            fresh = load_elevenlabs_settings(project)
            _sync_form_widgets_from_settings(
                project, key_prefix=key_prefix, settings=fresh
            )
            st.rerun()

    render_language_standard_path_caption("elevenlabs_voice")

    col_reset, col_test = st.columns(2)
    with col_reset:
        reset_disabled = not (has_project_override and has_language_default)
        if st.button(
            f"Auf {lang_key}-Standard zurück",
            key=f"{key_prefix}_settings_reset_lang_{project.id}",
            disabled=reset_disabled,
            help=(
                "Löscht den Projekt-Override und lädt den globalen "
                f"Sprach-Standard für {lang_key}."
            ),
        ):
            clear_project_elevenlabs_settings(project)
            fresh = load_elevenlabs_settings(project)
            _sync_form_widgets_from_settings(
                project, key_prefix=key_prefix, settings=fresh
            )
            st.success(f"Projekt-Override entfernt — {lang_key}-Standard aktiv.")
            st.rerun()
    with col_test:
        test_disabled = not (is_elevenlabs_configured() and str(voice_id).strip())
        if st.button(
            "Test Voice",
            key=f"{key_prefix}_test_voice_{project.id}",
            disabled=test_disabled,
        ):
            lang = (sample_language or project.language or "EN").strip().upper()
            sample_text = _SAMPLE_TEXTS.get(lang, _SAMPLE_TEXTS["EN"])
            try:
                save_elevenlabs_settings(project, updated)
                with st.spinner("Test-Audio wird erzeugt…"):
                    path = synthesize_test_voice(project, sample_text)
                st.success(f"Test-Audio erzeugt: `{path}`")
                st.audio(str(path))
            except ElevenLabsTtsError as exc:
                st.error(f"Test Voice fehlgeschlagen: {exc}")

    return updated
