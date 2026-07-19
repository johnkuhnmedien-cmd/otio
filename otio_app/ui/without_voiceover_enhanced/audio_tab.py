"""Schritt 6 Enhanced: ElevenLabs + gemessene Segment-Timestamps."""

from __future__ import annotations

import streamlit as st

from otio_app.services.voiceover_generation.elevenlabs_client import is_elevenlabs_configured
from otio_app.services.without_voiceover_enhanced.audio_timing_service import (
    AudioTimingError,
    load_segment_timings,
    synthesize_locked_script_audio,
    validate_timings_against_script,
)
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    ScriptLockError,
    load_locked_script,
)
from otio_app.ui.without_voiceover_enhanced._shared import get_enhanced_project


def render_enhanced_audio_page() -> None:
    st.header("⑥ Audio / ElevenLabs (Enhanced)")
    st.caption(
        "Nur gesperrte Skripte. Eine Audiodatei pro Segment; Dauer wird technisch gemessen."
    )
    project = get_enhanced_project()
    if project is None:
        return

    locked = load_locked_script(project)
    if locked is None:
        st.error("Kein gesperrtes Skript vorhanden — zuerst Script Lock in Schritt 4.")
        return

    st.info(f"Skriptversion: `{locked.script_version}`")
    if not is_elevenlabs_configured():
        st.warning("ELEVENLABS_API_KEY fehlt unter API-Schlüssel.")

    if st.button("Audio für alle Segmente erzeugen", type="primary"):
        try:
            with st.spinner("ElevenLabs + Dauerablesung…"):
                timings = synthesize_locked_script_audio(project)
            st.success(f"{len(timings.segments)} Segmente vertont.")
            st.rerun()
        except (AudioTimingError, ScriptLockError) as exc:
            st.error(str(exc))

    timings = load_segment_timings(project)
    errors = []
    try:
        errors = validate_timings_against_script(project, timings)
    except ScriptLockError as exc:
        errors = [str(exc)]

    if errors:
        for err in errors:
            st.error(err)
    elif timings is not None:
        st.success("Segment-Timings gültig und zur Skriptversion passend.")

    if timings is None:
        st.info("Noch keine segment_timings.json.")
        return

    for item in timings.segments:
        st.write(
            f"`{item.segment_id}` · {item.duration_seconds:.2f}s · "
            f"{item.audio_status} · v={item.script_version}"
        )
        st.caption(item.audio_path)
