"""Gemeinsame ElevenLabs-Settings-UI (Classic + Enhanced).

Enthält nie den API-Key — nur Voice-ID, Modell und Stimm-Parameter.
"""

from __future__ import annotations

import streamlit as st

from otio_app.defaults import ELEVENLABS_MODEL_PRESETS
from otio_app.models import Project
from otio_app.services.voiceover_generation.elevenlabs_client import (
    ElevenLabsTtsError,
    is_elevenlabs_configured,
)
from otio_app.services.voiceover_generation.elevenlabs_settings_service import (
    load_elevenlabs_settings,
    save_elevenlabs_settings,
)
from otio_app.services.voiceover_generation.models import ElevenLabsSettings
from otio_app.services.voiceover_generation.tts_orchestration_service import (
    synthesize_test_voice,
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
}


def voice_id_is_set(project: Project) -> bool:
    return bool(load_elevenlabs_settings(project).voice_id.strip())


def render_elevenlabs_settings_form(
    project: Project,
    *,
    key_prefix: str,
    sample_language: str | None = None,
) -> ElevenLabsSettings:
    """Rendert Voice-ID/Modell/Parameter + Speichern / Neu laden / Test Voice."""
    settings = load_elevenlabs_settings(project)
    st.subheader("ElevenLabs Settings")
    st.caption(
        "Voice-ID aus dem ElevenLabs-Dashboard (Voice Library). "
        "API-Key bleibt unter 🔑 API-Schlüssel."
    )

    voice_id = st.text_input(
        "Voice-ID",
        value=settings.voice_id,
        key=f"{key_prefix}_voice_id_{project.id}",
        help="z. B. 21m00Tcm4TlvDq8ikWAM — aus ElevenLabs → Voices kopieren.",
    )

    model_options = list(ELEVENLABS_MODEL_PRESETS)
    if settings.model_id not in model_options:
        model_options = [settings.model_id, *model_options]
    model_id = st.selectbox(
        "Modell",
        options=model_options,
        index=model_options.index(settings.model_id),
        key=f"{key_prefix}_model_{project.id}",
    )

    col1, col2 = st.columns(2)
    with col1:
        output_format = st.text_input(
            "Output-Format",
            value=settings.output_format,
            key=f"{key_prefix}_format_{project.id}",
            help="z. B. mp3_44100_128, pcm_16000, wav_44100",
        )
        stability = st.slider(
            "Stability",
            0.0,
            1.0,
            value=settings.stability,
            key=f"{key_prefix}_stability_{project.id}",
        )
        similarity_boost = st.slider(
            "Similarity Boost",
            0.0,
            1.0,
            value=settings.similarity_boost,
            key=f"{key_prefix}_similarity_{project.id}",
        )
    with col2:
        style = st.slider(
            "Style",
            0.0,
            1.0,
            value=settings.style,
            key=f"{key_prefix}_style_{project.id}",
        )
        use_speaker_boost = st.checkbox(
            "Speaker Boost",
            value=settings.use_speaker_boost,
            key=f"{key_prefix}_speaker_boost_{project.id}",
        )
        speed = st.slider(
            "Speed",
            0.25,
            4.0,
            value=settings.speed,
            key=f"{key_prefix}_speed_{project.id}",
        )

    with st.expander("Erweitert", expanded=False):
        language_code = st.text_input(
            "Language Code (optional — nur senden, wenn ausgefüllt)",
            value=settings.language_code,
            key=f"{key_prefix}_lang_code_{project.id}",
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

    col_save, col_reload, col_test = st.columns(3)
    with col_save:
        if st.button("Settings speichern", key=f"{key_prefix}_settings_save_{project.id}"):
            save_elevenlabs_settings(project, updated)
            st.success("ElevenLabs-Settings gespeichert.")
            st.rerun()
    with col_reload:
        if st.button("Neu laden", key=f"{key_prefix}_settings_reload_{project.id}"):
            st.rerun()
    with col_test:
        test_disabled = not (is_elevenlabs_configured() and voice_id.strip())
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
