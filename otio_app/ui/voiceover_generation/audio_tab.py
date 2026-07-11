"""ElevenLabs-Vertonung pro Ordner, Manifest, Alignment, Re-TTS-Versionierung (Phase 6)."""

from __future__ import annotations

from otio_app.defaults import (
    AUDIO_SCOPE_FOLDER,
    AUDIO_SCOPE_INTRO,
    AUDIO_STATUS_READY,
    AUDIO_STATUS_READY_WITH_WARNINGS,
    AUDIO_STATUS_STALE,
    ELEVENLABS_MODEL_PRESETS,
)
from otio_app.models import Project
from otio_app.services.voiceover_generation.audio_alignment_service import load_alignment
from otio_app.services.voiceover_generation.dramaturgy_service import load_confirmed_dramaturgy
from otio_app.services.voiceover_generation.elevenlabs_client import (
    ElevenLabsTtsError,
    is_elevenlabs_configured,
)
from otio_app.services.voiceover_generation.elevenlabs_settings_service import (
    load_elevenlabs_settings,
    save_elevenlabs_settings,
)
from otio_app.services.voiceover_generation.intro_hook_service import load_confirmed_intro_hook
from otio_app.services.voiceover_generation.models import ElevenLabsSettings
from otio_app.services.voiceover_generation.project_brief_service import load_project_brief
from otio_app.services.voiceover_generation.tts_orchestration_service import (
    load_audio_manifest,
    mark_stale_audio_if_needed,
    synthesize_all_confirmed_voiceovers,
    synthesize_folder_voiceover,
    synthesize_intro,
    synthesize_test_voice,
)
from otio_app.services.voiceover_generation.voiceover_author_service import (
    load_folder_voiceovers_confirmed,
)
from otio_app.ui.project_context import render_project_selector
from otio_app.ui.voiceover_generation._shared import require_without_voiceover_mode

import streamlit as st


def _render_prerequisites(project: Project) -> tuple[bool, bool]:
    """Gibt (api_key_ready, voice_id_set) zurück."""
    brief = load_project_brief(project)
    confirmed_folders = load_folder_voiceovers_confirmed(project)
    confirmed_hook = load_confirmed_intro_hook(project)
    api_ready = is_elevenlabs_configured()
    settings = load_elevenlabs_settings(project)
    voice_id_set = bool(settings.voice_id.strip())
    manifest = load_audio_manifest(project)

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Project Brief", "✓" if (brief.video_title or brief.tone_tags) else "—")
    with col2:
        st.metric("Bestätigte Ordner", len(confirmed_folders.items))
    with col3:
        st.metric("Intro-Hook bestätigt", "✓" if confirmed_hook is not None else "—")
    with col4:
        st.metric("ElevenLabs API-Key", "READY" if api_ready else "MISSING")

    st.caption(f"Voice-ID: {'gesetzt' if voice_id_set else 'fehlt'} · Audio-Manifest-Einträge: {len(manifest.items)}")

    if not api_ready:
        st.warning(
            "ELEVENLABS_API_KEY fehlt. Bitte unter 🔑 API-Schlüssel eintragen — "
            "ohne Key ist keine Vertonung möglich."
        )
    if not voice_id_set:
        st.warning("Keine ElevenLabs Voice-ID konfiguriert. Bitte unten in den Settings eintragen.")

    return api_ready, voice_id_set


def _render_elevenlabs_settings(project: Project) -> ElevenLabsSettings:
    settings = load_elevenlabs_settings(project)
    st.subheader("ElevenLabs Settings")

    voice_id = st.text_input("Voice-ID", value=settings.voice_id, key=f"vo_audio_voice_id_{project.id}")

    model_options = list(ELEVENLABS_MODEL_PRESETS)
    if settings.model_id not in model_options:
        model_options = [settings.model_id, *model_options]
    model_id = st.selectbox(
        "Modell", options=model_options, index=model_options.index(settings.model_id),
        key=f"vo_audio_model_{project.id}",
    )

    col1, col2 = st.columns(2)
    with col1:
        output_format = st.text_input(
            "Output-Format", value=settings.output_format, key=f"vo_audio_format_{project.id}",
            help="z. B. mp3_44100_128, pcm_16000, wav_44100",
        )
        stability = st.slider(
            "Stability", 0.0, 1.0, value=settings.stability, key=f"vo_audio_stability_{project.id}"
        )
        similarity_boost = st.slider(
            "Similarity Boost", 0.0, 1.0, value=settings.similarity_boost,
            key=f"vo_audio_similarity_{project.id}",
        )
    with col2:
        style = st.slider("Style", 0.0, 1.0, value=settings.style, key=f"vo_audio_style_{project.id}")
        use_speaker_boost = st.checkbox(
            "Speaker Boost", value=settings.use_speaker_boost, key=f"vo_audio_speaker_boost_{project.id}"
        )
        speed = st.slider(
            "Speed", 0.25, 4.0, value=settings.speed, key=f"vo_audio_speed_{project.id}"
        )

    with st.expander("Erweitert", expanded=False):
        language_code = st.text_input(
            "Language Code (optional — nur senden, wenn ausgefüllt)",
            value=settings.language_code, key=f"vo_audio_lang_code_{project.id}",
            help="Wird nur an ElevenLabs gesendet, wenn nicht leer. Nicht an bestimmte "
            "Modelle gekoppelt — kein Modell wird deswegen blockiert.",
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
        if st.button("Settings speichern", key=f"vo_audio_settings_save_{project.id}"):
            save_elevenlabs_settings(project, updated)
            st.success("ElevenLabs-Settings gespeichert.")
            st.rerun()
    with col_reload:
        if st.button("Neu laden", key=f"vo_audio_settings_reload_{project.id}"):
            st.rerun()
    with col_test:
        test_disabled = not (is_elevenlabs_configured() and voice_id.strip())
        if st.button("Test Voice", key=f"vo_audio_test_voice_{project.id}", disabled=test_disabled):
            brief = load_project_brief(project)
            sample_texts = {
                "DE": "Dies ist ein kurzer Test der Stimme.",
                "EN": "This is a short voice test.",
                "FR": "Ceci est un court test de voix.",
                "ES": "Esta es una breve prueba de voz.",
                "PT": "Este é um breve teste de voz.",
                "IT": "Questo è un breve test vocale.",
            }
            sample_text = sample_texts.get(brief.language, sample_texts["EN"])
            try:
                with st.spinner("Test-Audio wird erzeugt…"):
                    path = synthesize_test_voice(project, sample_text)
                st.success(f"Test-Audio erzeugt: `{path}`")
                st.audio(str(path))
            except ElevenLabsTtsError as exc:
                st.error(f"Test Voice fehlgeschlagen: {exc}")

    return updated


def _character_counts(project: Project) -> tuple[int, dict[str, int], int]:
    """Gibt (intro_chars, {folder_name: chars}, total_chars) zurück."""
    confirmed_folders = load_folder_voiceovers_confirmed(project)
    confirmed_hook = load_confirmed_intro_hook(project)
    intro_chars = len(confirmed_hook.hook_text) if confirmed_hook is not None else 0
    folder_chars = {item.folder_name: len(item.voiceover_text_full) for item in confirmed_folders.items}
    total = intro_chars + sum(folder_chars.values())
    return intro_chars, folder_chars, total


def _render_cost_transparency(project: Project) -> bool:
    """Zeigt Zeichenanzahl-Übersicht, gibt zurück ob die Kostenbestätigung gesetzt ist."""
    intro_chars, folder_chars, total_chars = _character_counts(project)
    st.subheader("Kosten-/Zeichen-Transparenz")
    st.write(f"**Intro:** {intro_chars} Zeichen")
    for folder_name, chars in folder_chars.items():
        st.write(f"**{folder_name}:** {chars} Zeichen")
    st.write(f"**Gesamt:** {total_chars} Zeichen über {len(folder_chars) + (1 if intro_chars else 0)} Einheiten")
    st.info("TTS kann Kosten beim Provider verursachen.")
    return st.checkbox(
        "Ich bestätige, dass TTS-Kosten entstehen können.",
        key=f"vo_audio_cost_confirm_{project.id}",
    )


def _render_actions(project: Project, *, can_tts: bool, cost_confirmed: bool) -> None:
    st.subheader("Aktionen")
    confirmed_hook = load_confirmed_intro_hook(project)
    confirmed_folders = load_folder_voiceovers_confirmed(project)

    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button(
            "Intro vertonen", key=f"vo_audio_synth_intro_{project.id}",
            disabled=not can_tts or confirmed_hook is None,
        ):
            with st.spinner("Intro wird vertont…"):
                try:
                    synthesize_intro(project)
                    st.success("Intro vertont.")
                except ElevenLabsTtsError as exc:
                    st.error(f"Fehlgeschlagen: {exc}")
            st.rerun()
    with col2:
        if st.button(
            "Alle bestätigten Folder Voice-overs vertonen",
            key=f"vo_audio_synth_all_folders_{project.id}",
            disabled=not can_tts or not cost_confirmed or not confirmed_folders.items,
        ):
            progress_placeholder = st.empty()

            def _progress(label: str, index: int, total: int) -> None:
                progress_placeholder.info(f"Ordner {index}/{total} wird vertont: „{label}“")

            with st.spinner("Ordner werden vertont…"):
                _synthesize_all_folders_sequential(project, progress_placeholder, _progress)
            progress_placeholder.empty()
            st.success("Alle bestätigten Ordner verarbeitet.")
            st.rerun()
    with col3:
        if st.button(
            "Intro + alle Ordner vertonen", key=f"vo_audio_synth_everything_{project.id}",
            disabled=not can_tts or not cost_confirmed,
        ):
            progress_placeholder = st.empty()

            def _progress_all(label: str, index: int, total: int) -> None:
                progress_placeholder.info(f"{index}/{total} wird vertont: „{label}“")

            with st.spinner("Intro und Ordner werden vertont…"):
                synthesize_all_confirmed_voiceovers(project, progress_callback=_progress_all)
            progress_placeholder.empty()
            st.success("Intro und alle bestätigten Ordner verarbeitet.")
            st.rerun()


def _synthesize_all_folders_sequential(project: Project, placeholder, progress_callback) -> None:
    confirmed_folders = load_folder_voiceovers_confirmed(project)
    items = sorted(confirmed_folders.items, key=lambda item: item.order_index)
    total = len(items)
    for index, item in enumerate(items, start=1):
        progress_callback(item.folder_name, index, total)
        try:
            synthesize_folder_voiceover(project, item.folder_name)
        except ElevenLabsTtsError:
            continue


def _render_audio_preview(project: Project, *, scope: str, folder_name: str, manifest) -> None:
    item = next(
        (
            entry
            for entry in manifest.items
            if entry.scope == scope and (scope == AUDIO_SCOPE_INTRO or entry.folder_name == folder_name)
        ),
        None,
    )
    if item is None or not item.audio_path:
        st.caption("Noch kein Audio vorhanden.")
        return

    if item.status == AUDIO_STATUS_STALE:
        st.warning("Audio veraltet — bitte neu vertonen.")
    if item.error_message:
        st.warning(item.error_message)

    st.audio(item.audio_path)
    st.caption(
        f"Dauer: {item.audio_duration_sec:.1f}s · Version: v{item.audio_version:03d} · "
        f"TTS-Run: `{item.tts_run_id}` · Status: {item.status}"
    )

    with st.expander("Alignment anzeigen"):
        alignment = load_alignment(project, scope, folder_name)
        if alignment is None:
            st.caption("Kein Alignment vorhanden.")
        else:
            if alignment.alignment_warnings:
                st.warning("Warnungen: " + ", ".join(alignment.alignment_warnings))
            rows = [
                {
                    "sentence_id": alignment_item.sentence_id,
                    "text": alignment_item.text,
                    "audio_start_sec": alignment_item.audio_start_sec,
                    "audio_end_sec": alignment_item.audio_end_sec,
                    "primary_asset_id": alignment_item.primary_asset_id,
                    "needs_supplement_asset": alignment_item.needs_supplement_asset,
                }
                for alignment_item in alignment.items
            ]
            st.dataframe(rows, use_container_width=True, hide_index=True)


def _render_folder_table(project: Project, *, can_tts: bool) -> None:
    st.subheader("Ordner-Vertonung")
    confirmed_folders = load_folder_voiceovers_confirmed(project)
    manifest = load_audio_manifest(project)

    if not confirmed_folders.items:
        st.info("Noch keine bestätigten Folder Voice-overs vorhanden.")
        return

    for draft in sorted(confirmed_folders.items, key=lambda item: item.order_index):
        item = next(
            (entry for entry in manifest.items if entry.scope == AUDIO_SCOPE_FOLDER and entry.folder_name == draft.folder_name),
            None,
        )
        status = item.status if item is not None else "MISSING"
        with st.expander(f"{draft.order_index}. {draft.folder_name} — {status}"):
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("Wortanzahl", draft.word_count)
            with col2:
                st.metric("Zeichen", len(draft.voiceover_text_full))
            with col3:
                st.metric("Audio-Status", status)
            with col4:
                st.metric("Version", item.audio_version if item is not None else 0)

            label = "Neu vertonen" if item is not None and item.status in (AUDIO_STATUS_READY, AUDIO_STATUS_READY_WITH_WARNINGS, AUDIO_STATUS_STALE) else "Vertonen"
            if st.button(label, key=f"vo_audio_synth_folder_{draft.folder_name}_{project.id}", disabled=not can_tts):
                with st.spinner(f"„{draft.folder_name}“ wird vertont…"):
                    try:
                        synthesize_folder_voiceover(project, draft.folder_name)
                        st.success("Vertont.")
                    except ElevenLabsTtsError as exc:
                        st.error(f"Fehlgeschlagen: {exc}")
                st.rerun()

            _render_audio_preview(project, scope=AUDIO_SCOPE_FOLDER, folder_name=draft.folder_name, manifest=manifest)


def _render_intro_section(project: Project, *, can_tts: bool) -> None:
    st.subheader("Intro-Vertonung")
    confirmed_hook = load_confirmed_intro_hook(project)
    manifest = load_audio_manifest(project)

    if confirmed_hook is None:
        st.info("Noch kein bestätigter Intro-Hook vorhanden.")
        return

    item = next((entry for entry in manifest.items if entry.scope == AUDIO_SCOPE_INTRO), None)
    status = item.status if item is not None else "MISSING"
    col1, col2 = st.columns(2)
    with col1:
        st.metric("Zeichen", len(confirmed_hook.hook_text))
    with col2:
        st.metric("Audio-Status", status)

    label = "Neu vertonen" if item is not None and item.status in (AUDIO_STATUS_READY, AUDIO_STATUS_READY_WITH_WARNINGS, AUDIO_STATUS_STALE) else "Vertonen"
    if st.button(label, key=f"vo_audio_synth_intro_row_{project.id}", disabled=not can_tts):
        with st.spinner("Intro wird vertont…"):
            try:
                synthesize_intro(project)
                st.success("Vertont.")
            except ElevenLabsTtsError as exc:
                st.error(f"Fehlgeschlagen: {exc}")
        st.rerun()

    _render_audio_preview(project, scope=AUDIO_SCOPE_INTRO, folder_name="", manifest=manifest)


def render_audio_page() -> None:
    st.header("⑥ Audio / ElevenLabs")

    project = render_project_selector("Projekt")
    if project is None:
        return
    if not require_without_voiceover_mode(project):
        return

    if load_confirmed_dramaturgy(project) is None:
        st.warning("Bitte zuerst die Dramaturgie bestätigen.")
        return

    mark_stale_audio_if_needed(project)

    st.subheader("Voraussetzungen")
    api_ready, voice_id_set = _render_prerequisites(project)
    can_tts = api_ready and voice_id_set

    _render_elevenlabs_settings(project)

    cost_confirmed = _render_cost_transparency(project)
    _render_actions(project, can_tts=can_tts, cost_confirmed=cost_confirmed)

    _render_intro_section(project, can_tts=can_tts)
    _render_folder_table(project, can_tts=can_tts)

    st.caption(
        "Audio-QA mit Whisper/STT folgt in späterer Phase — kein Pflichtpfad in Phase 6."
    )
