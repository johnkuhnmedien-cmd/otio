"""Schritt 6 Enhanced: ElevenLabs + gemessene Segment-Timestamps — pro Kapitel."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from otio_app.defaults import ELEVENLABS_MODEL_ID_V3
from otio_app.services.voiceover_generation.elevenlabs_client import is_elevenlabs_configured
from otio_app.services.voiceover_generation.elevenlabs_settings_service import (
    load_elevenlabs_settings,
)
from otio_app.services.without_voiceover_enhanced.audio_timing_service import (
    AudioTimingError,
    load_segment_timings,
    synthesize_folder_script_audio,
    synthesize_intro_script_audio,
    synthesize_locked_script_audio,
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
from otio_app.services.without_voiceover_enhanced.io_utils import load_model
from otio_app.services.without_voiceover_enhanced.models import SegmentAlignment
from otio_app.services.without_voiceover_enhanced.paths import (
    segment_sentence_alignment_path,
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


def _stored_tts_text(project, segment_id: str) -> str | None:
    path = segment_sentence_alignment_path(project, segment_id)
    if not Path(path).is_file():
        return None
    alignment = load_model(path, SegmentAlignment)
    if alignment is None:
        return None
    text = (alignment.tts_text or "").strip()
    return text or None


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


def render_enhanced_audio_page() -> None:
    st.header("⑥ Audio / ElevenLabs (Enhanced)")
    st.caption(
        "Nur gesperrte Skripte. Vertonung **pro Kapitel in einem ElevenLabs-Call** "
        "(Intro zuerst, dann Dramaturgie-Kapitel) — niemals Segment für Segment. "
        "Pause-Tags stehen im Kapitel-TTS-Text; Segment-Slices und Satzzeiten "
        "werden aus dem Chapter-Alignment abgeleitet (`audio/alignments/`)."
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

    # Bestätigtes Intro aus Schritt ⑤ in Locked-Script spiegeln (falls vorhanden).
    locked = ensure_confirmed_intro_in_locked_script(project) or locked

    st.info(f"Skriptversion: `{locked.script_version}`")
    intro_text = confirmed_intro_text(project)
    if intro_text:
        st.success(
            f"Bestätigtes Intro vorhanden → Kapitel „{ENHANCED_INTRO_FOLDER_NAME}“ "
            "wird standardmäßig mit an ElevenLabs gesendet (zuerst)."
        )
    else:
        st.caption(
            "Kein bestätigter Intro-Hook (`intro_hook.confirmed.json`). "
            "Optional in Schritt ⑤ bestätigen — sonst starten Kapitel ohne Intro."
        )

    entries = list_enabled_dramaturgy_folders(project)
    folder_order = [entry.folder_name for entry in entries]
    groups = group_segments_by_folder(locked, folder_order=folder_order)
    timings = load_segment_timings(project)
    timing_by_id = {
        item.segment_id: item for item in (timings.segments if timings else [])
    }

    col_all, col_intro = st.columns(2)
    with col_all:
        run_all = st.button(
            "Alle Kapitel sequenziell vertonen",
            type="primary",
            key="enh_audio_all",
            disabled=not can_tts,
        )
    with col_intro:
        run_intro = st.button(
            "Intro vertonen",
            key="enh_audio_intro",
            disabled=not can_tts or intro_text is None,
            help="Bestätigtes Intro in einem ElevenLabs-Call.",
        )

    if run_all:
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
            with st.spinner(
                "Alle Kapitel werden nacheinander an ElevenLabs gesendet…"
            ):
                timings = synthesize_locked_script_audio(
                    project,
                    progress_callback=_progress,
                )
            progress.empty()
            st.success(
                f"{len(timings.segments)} Segmente in {len(groups)} Kapitel(n) vertont."
            )
            st.rerun()
        except (AudioTimingError, ScriptLockError) as exc:
            progress.empty()
            st.error(str(exc))

    if run_intro:
        progress = st.empty()

        def _intro_progress(
            folder_name: str,
            chapter_index: int,
            chapter_total: int,
            segment_index: int,
            segment_total: int,
        ) -> None:
            progress.info(
                _format_tts_progress(
                    folder_name or ENHANCED_INTRO_FOLDER_NAME,
                    chapter_index,
                    chapter_total,
                    segment_index,
                    segment_total,
                )
            )

        try:
            with st.spinner("Intro wird an ElevenLabs gesendet…"):
                synthesize_intro_script_audio(
                    project,
                    progress_callback=_intro_progress,
                )
            progress.empty()
            st.success("Intro vertont.")
            st.rerun()
        except (AudioTimingError, ScriptLockError) as exc:
            progress.empty()
            st.error(str(exc))

    settings = load_elevenlabs_settings(project)
    st.subheader("Kapitel")
    st.caption(
        f"An ElevenLabs geht **ein TTS-Text pro Kapitel** "
        f"(bei `{ELEVENLABS_MODEL_ID_V3}` inkl. `[short pause]` / `[pause]` / "
        "`[long pause]`). Darunter: abgeleitete Segment-Slices."
    )
    for folder_name, segments in groups:
        label = folder_name or "(ohne Kapitelzuordnung)"
        if is_intro_folder_name(folder_name):
            label = f"{ENHANCED_INTRO_FOLDER_NAME} (Hook)"
        ready = sum(
            1
            for seg in segments
            if timing_by_id.get(seg.segment_id)
            and timing_by_id[seg.segment_id].audio_status == "valid"
            and timing_by_id[seg.segment_id].script_version == locked.script_version
        )
        chapter_tts, _align_parts = build_chapter_tts_text(
            list(segments), model_id=settings.model_id
        )
        with st.expander(
            f"{label} · {ready}/{len(segments)} Segmente · 1 ElevenLabs-Call",
            expanded=True,
        ):
            st.caption("Kapitel-TTS-Text an ElevenLabs:")
            st.code(chapter_tts, language=None)
            if folder_name:
                button_label = (
                    "Intro vertonen"
                    if is_intro_folder_name(folder_name)
                    else f"Kapitel „{folder_name}“ vertonen"
                )
                if st.button(
                    button_label,
                    key=f"enh_audio_folder_{project.id}_{folder_name}",
                    disabled=not can_tts,
                ):
                    progress = st.empty()

                    def _folder_progress(
                        name: str,
                        chapter_index: int,
                        chapter_total: int,
                        segment_index: int,
                        segment_total: int,
                        *,
                        _folder: str = folder_name,
                    ) -> None:
                        progress.info(
                            _format_tts_progress(
                                _folder or name,
                                chapter_index,
                                chapter_total,
                                segment_index,
                                segment_total,
                            )
                        )

                    try:
                        with st.spinner(
                            f"„{folder_name}“ wird in einem Call an ElevenLabs gesendet…"
                        ):
                            if is_intro_folder_name(folder_name):
                                synthesize_intro_script_audio(
                                    project,
                                    progress_callback=_folder_progress,
                                )
                            else:
                                synthesize_folder_script_audio(
                                    project,
                                    folder_name,
                                    progress_callback=_folder_progress,
                                )
                        progress.empty()
                        st.success(f"„{folder_name}“ vertont.")
                        st.rerun()
                    except (AudioTimingError, ScriptLockError) as exc:
                        progress.empty()
                        st.error(str(exc))
            for seg in segments:
                pause = float(getattr(seg, "author_pause_after_seconds", 0.0) or 0.0)
                pause_note = f" · author_pause={pause:g}s" if pause > 0 else ""
                item = timing_by_id.get(seg.segment_id)
                if item is None:
                    st.markdown(f"**`{seg.segment_id}`** · noch kein Audio{pause_note}")
                else:
                    st.markdown(
                        f"**`{seg.segment_id}`** · {item.duration_seconds:.2f}s · "
                        f"{item.audio_status} · v={item.script_version}{pause_note}"
                    )
                    st.caption(item.audio_path)
                    if item.timestamps_path:
                        st.caption(f"Timestamps: `{item.timestamps_path}`")
                spoken = (seg.text or "").strip()
                stored = _stored_tts_text(project, seg.segment_id)
                if stored:
                    st.caption("Segment-Slice (Spoken Body):")
                    st.code(stored, language=None)
                elif spoken:
                    st.caption("Segment-Spoken:")
                    st.code(spoken, language=None)

    errors: list[str] = []
    try:
        errors = validate_timings_against_script(project, timings)
    except ScriptLockError as exc:
        errors = [str(exc)]

    if errors:
        st.subheader("Validierung")
        for err in errors:
            st.error(err)
    elif timings is not None:
        with_ts = sum(1 for item in timings.segments if item.timestamps_path)
        st.success(
            "Segment-Timings gültig und zur Skriptversion passend "
            f"({with_ts}/{len(timings.segments)} mit ElevenLabs-Timestamps)."
        )
