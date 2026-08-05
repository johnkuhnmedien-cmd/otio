"""Schritt 6 Enhanced: ElevenLabs — Vertonung pro Kapitel (1 Call)."""

from __future__ import annotations

import streamlit as st

from otio_app.defaults import ELEVENLABS_MODEL_ID_V3
from otio_app.services.voiceover_generation.elevenlabs_client import is_elevenlabs_configured
from otio_app.services.voiceover_generation.elevenlabs_settings_service import (
    load_elevenlabs_settings,
)
from otio_app.services.without_voiceover_enhanced.audio_timing_service import (
    CHAPTER_AUDIO_READY,
    AudioTimingError,
    ChapterAudioStatus,
    list_chapter_audio_statuses,
    load_segment_timings,
    synthesize_folder_script_audio,
    synthesize_intro_script_audio,
    synthesize_locked_script_audio,
    synthesize_open_chapters_audio,
    validate_timings_against_script,
)
from otio_app.services.without_voiceover_enhanced.enhanced_tts_text import (
    build_chapter_tts_text,
)
from otio_app.services.without_voiceover_enhanced.intro_script_bridge import (
    ENHANCED_INTRO_FOLDER_NAME,
    confirmed_intro_text,
    ensure_confirmed_intro_in_locked_script,
    is_intro_folder_name,
)
from otio_app.services.without_voiceover_enhanced.script_author_service import (
    group_segments_by_folder,
    list_enabled_dramaturgy_folders,
)
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    ScriptLockError,
    load_locked_script,
)
from otio_app.ui.voiceover_generation.elevenlabs_settings_ui import (
    render_elevenlabs_settings_form,
    voice_id_is_set,
)
from otio_app.ui.without_voiceover_enhanced._shared import get_enhanced_project


def _format_tts_progress(
    folder_name: str,
    chapter_index: int,
    chapter_total: int,
    segment_index: int,
    segment_total: int,
) -> str:
    label = folder_name or "(ohne Kapitel)"
    return (
        f"Kapitel {chapter_index}/{chapter_total}: „{label}“ "
        "→ ElevenLabs (1 Call)…"
    )


def _status_badge(row: ChapterAudioStatus) -> str:
    return row.status_label.upper()


def render_enhanced_audio_page() -> None:
    st.header("⑥ Audio / ElevenLabs (Enhanced)")
    st.caption(
        "Nur gesperrte Skripte. Jedes Kapitel wird in **einem** ElevenLabs-Call "
        "vertont (Intro zuerst). Pause-Tags stehen im Kapitel-TTS-Text."
    )
    project = get_enhanced_project()
    if project is None:
        return

    api_ready = is_elevenlabs_configured()
    if not api_ready:
        st.warning("ELEVENLABS_API_KEY fehlt unter API-Schlüssel.")

    render_elevenlabs_settings_form(
        project,
        key_prefix="enh_audio",
        sample_language=project.language,
    )
    voice_ready = voice_id_is_set(project)
    can_tts = api_ready and voice_ready
    if api_ready and not voice_ready:
        st.warning(
            "Keine ElevenLabs Voice-ID konfiguriert. "
            "Bitte oben eintragen und „Settings speichern“."
        )

    locked = load_locked_script(project)
    if locked is None:
        st.error("Kein gesperrtes Skript vorhanden — zuerst Script Lock in Schritt 4.")
        return

    locked = ensure_confirmed_intro_in_locked_script(project) or locked
    st.info(f"Skriptversion: `{locked.script_version}`")

    intro_text = confirmed_intro_text(project)
    if not intro_text:
        st.caption(
            "Kein bestätigter Intro-Hook (`intro_hook.confirmed.json`). "
            "Optional in Schritt ⑤ bestätigen — sonst starten Kapitel ohne Intro."
        )

    try:
        chapter_rows = list_chapter_audio_statuses(project)
    except ScriptLockError as exc:
        st.error(str(exc))
        return

    open_rows = [row for row in chapter_rows if row.is_open]
    ready_rows = [row for row in chapter_rows if row.status == CHAPTER_AUDIO_READY]
    st.caption(
        f"{len(ready_rows)}/{len(chapter_rows)} Kapitel vertont · "
        f"{len(open_rows)} offen/veraltet/unvollständig"
    )

    col_open, col_all, col_intro = st.columns(3)
    with col_open:
        run_open = st.button(
            f"Alle offenen vertonen ({len(open_rows)})",
            type="primary",
            key="enh_audio_open",
            disabled=not can_tts or not open_rows,
            help="Nur Kapitel mit Status offen, veraltet oder unvollständig.",
        )
    with col_all:
        run_all = st.button(
            "Alle Kapitel neu vertonen",
            key="enh_audio_all",
            disabled=not can_tts or not chapter_rows,
            help="Erzeugt jedes Kapitel erneut (auch bereits vertonte).",
        )
    with col_intro:
        run_intro = st.button(
            "Intro vertonen",
            key="enh_audio_intro",
            disabled=not can_tts or intro_text is None,
            help="Nur das bestätigte Intro (1 Call).",
        )

    def _run_with_progress(action, *, spinner_text: str, success_text: str) -> None:
        progress = st.empty()

        def _progress(
            folder_name: str,
            chapter_index: int,
            chapter_total: int,
            segment_index: int,
            segment_total: int,
        ) -> None:
            progress.info(
                _format_tts_progress(
                    folder_name,
                    chapter_index,
                    chapter_total,
                    segment_index,
                    segment_total,
                )
            )

        try:
            with st.spinner(spinner_text):
                action(progress_callback=_progress)
            progress.empty()
            st.success(success_text)
            st.rerun()
        except (AudioTimingError, ScriptLockError) as exc:
            progress.empty()
            st.error(str(exc))

    if run_open:
        _run_with_progress(
            lambda progress_callback: synthesize_open_chapters_audio(
                project, progress_callback=progress_callback
            ),
            spinner_text="Offene Kapitel werden an ElevenLabs gesendet…",
            success_text=f"{len(open_rows)} offene Kapitel vertont.",
        )

    if run_all:
        _run_with_progress(
            lambda progress_callback: synthesize_locked_script_audio(
                project, progress_callback=progress_callback
            ),
            spinner_text="Alle Kapitel werden neu an ElevenLabs gesendet…",
            success_text=f"{len(chapter_rows)} Kapitel neu vertont.",
        )

    if run_intro:
        _run_with_progress(
            lambda progress_callback: synthesize_intro_script_audio(
                project, progress_callback=progress_callback
            ),
            spinner_text="Intro wird an ElevenLabs gesendet…",
            success_text="Intro vertont.",
        )

    settings = load_elevenlabs_settings(project)
    folder_order = [
        entry.folder_name for entry in list_enabled_dramaturgy_folders(project)
    ]
    groups = dict(group_segments_by_folder(locked, folder_order=folder_order))

    st.subheader("Kapitel")
    st.caption(
        f"Ein TTS-Call pro Zeile"
        f"{' · Pause-Tags für ' + ELEVENLABS_MODEL_ID_V3 if settings.model_id == ELEVENLABS_MODEL_ID_V3 else ''}."
    )

    for row in chapter_rows:
        duration_note = (
            f" · {row.duration_seconds:.1f}s" if row.duration_seconds > 0 else ""
        )
        header = f"{_status_badge(row)} · {row.label}{duration_note}"
        with st.expander(header, expanded=row.is_open):
            segments = groups.get(row.folder_name) or []
            chapter_tts, _ = build_chapter_tts_text(
                list(segments), model_id=settings.model_id
            )
            if chapter_tts:
                st.caption("Kapitel-TTS-Text:")
                preview = chapter_tts if len(chapter_tts) <= 700 else chapter_tts[:700] + "…"
                st.code(preview, language=None)
            if row.chapter_audio_path:
                st.caption(f"Kapitel-Audio: `{row.chapter_audio_path}`")

            folder_name = row.folder_name
            if not folder_name:
                continue
            button_label = (
                "Intro vertonen"
                if is_intro_folder_name(folder_name)
                else (
                    f"„{row.label}“ neu vertonen"
                    if row.status == CHAPTER_AUDIO_READY
                    else f"„{row.label}“ vertonen"
                )
            )
            if st.button(
                button_label,
                key=f"enh_audio_folder_{project.id}_{folder_name}",
                disabled=not can_tts,
            ):
                def _one(progress_callback, *, _folder=folder_name):
                    if is_intro_folder_name(_folder):
                        return synthesize_intro_script_audio(
                            project, progress_callback=progress_callback
                        )
                    return synthesize_folder_script_audio(
                        project, _folder, progress_callback=progress_callback
                    )

                _run_with_progress(
                    _one,
                    spinner_text=f"„{row.label}“ → ElevenLabs (1 Call)…",
                    success_text=f"„{row.label}“ vertont.",
                )

    timings = load_segment_timings(project)
    errors: list[str] = []
    try:
        errors = validate_timings_against_script(project, timings)
    except ScriptLockError as exc:
        errors = [str(exc)]

    if errors:
        st.subheader("Validierung")
        for err in errors:
            st.error(err)
    elif timings is not None and ready_rows and not open_rows:
        st.success(
            f"Alle {len(ready_rows)} Kapitel sind zur Skriptversion "
            f"`{locked.script_version}` vertont."
        )
