"""Schritt 6 Enhanced: ElevenLabs + gemessene Segment-Timestamps — pro Kapitel."""

from __future__ import annotations

import streamlit as st

from otio_app.services.voiceover_generation.elevenlabs_client import is_elevenlabs_configured
from otio_app.services.without_voiceover_enhanced.audio_timing_service import (
    AudioTimingError,
    load_segment_timings,
    synthesize_folder_script_audio,
    synthesize_locked_script_audio,
    validate_timings_against_script,
)
from otio_app.services.without_voiceover_enhanced.script_author_service import (
    chapter_narration_text,
    group_segments_by_folder,
    list_enabled_dramaturgy_folders,
)
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    ScriptLockError,
    load_locked_script,
)
from otio_app.ui.without_voiceover_enhanced._shared import get_enhanced_project


def render_enhanced_audio_page() -> None:
    st.header("⑥ Audio / ElevenLabs (Enhanced)")
    st.caption(
        "Nur gesperrte Skripte. Vertonung **pro Dramaturgie-Kapitel** "
        "(wie klassische Folder-VOs) — intern eine Audiodatei pro Segment, "
        "gruppiert nach Kapitel."
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

    entries = list_enabled_dramaturgy_folders(project)
    folder_order = [entry.folder_name for entry in entries]
    groups = group_segments_by_folder(locked, folder_order=folder_order)
    timings = load_segment_timings(project)
    timing_by_id = {
        item.segment_id: item for item in (timings.segments if timings else [])
    }

    if st.button("Alle Kapitel vertonen", type="primary", key="enh_audio_all"):
        try:
            with st.spinner("ElevenLabs — alle Kapitel…"):
                timings = synthesize_locked_script_audio(project)
            st.success(f"{len(timings.segments)} Segmente vertont.")
            st.rerun()
        except (AudioTimingError, ScriptLockError) as exc:
            st.error(str(exc))

    st.subheader("Kapitel")
    for folder_name, segments in groups:
        label = folder_name or "(ohne Kapitelzuordnung)"
        narration = (
            chapter_narration_text(locked, folder_name)
            if folder_name
            else " ".join(seg.text for seg in segments)
        )
        ready = sum(
            1
            for seg in segments
            if timing_by_id.get(seg.segment_id)
            and timing_by_id[seg.segment_id].audio_status == "valid"
            and timing_by_id[seg.segment_id].script_version == locked.script_version
        )
        with st.expander(
            f"{label} · {ready}/{len(segments)} Segmente vertont",
            expanded=True,
        ):
            st.write(narration[:500] + ("…" if len(narration) > 500 else ""))
            if folder_name:
                if st.button(
                    f"Kapitel „{folder_name}“ vertonen",
                    key=f"enh_audio_folder_{project.id}_{folder_name}",
                    disabled=not is_elevenlabs_configured(),
                ):
                    try:
                        with st.spinner(f"ElevenLabs — „{folder_name}“…"):
                            synthesize_folder_script_audio(project, folder_name)
                        st.success(f"„{folder_name}“ vertont.")
                        st.rerun()
                    except (AudioTimingError, ScriptLockError) as exc:
                        st.error(str(exc))
            for seg in segments:
                item = timing_by_id.get(seg.segment_id)
                if item is None:
                    st.caption(f"`{seg.segment_id}` · noch kein Audio")
                else:
                    st.caption(
                        f"`{seg.segment_id}` · {item.duration_seconds:.2f}s · "
                        f"{item.audio_status} · v={item.script_version}"
                    )
                    st.caption(item.audio_path)

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
        st.success("Segment-Timings gültig und zur Skriptversion passend.")
