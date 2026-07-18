"""Streamlit page: Discovery V2 Narration (fake voice, pause, timing)."""

from __future__ import annotations

import streamlit as st

from otio_app.discovery_v2.application.narration_timing_service import (
    start_narration_timing_run,
)
from otio_app.discovery_v2.application.pause_direction_service import (
    start_pause_direction_run,
)
from otio_app.discovery_v2.application.voice_generation_service import (
    get_narration_view,
    start_voice_generation_run,
)
from otio_app.discovery_v2.ui.flash import discovery_ui_flash_and_rerun
from otio_app.discovery_v2.ui.overview import active_discovery_project


def render_discovery_narration_page() -> None:
    st.title("Narration")
    project = active_discovery_project()
    if project is None:
        return
    st.info(
        "Lokaler Fake-Voice-Adapter: Es werden keine Texte an externe Dienste "
        "uebertragen. Es wird deterministisches WAV-Testaudio erzeugt, keine "
        "natuerliche Stimme."
    )
    view = get_narration_view(project)
    if not view.ok:
        st.warning(view.message or "Narration-Ansicht nicht verfuegbar.")
        return
    _render_lock(view)
    _render_voice(project, view)
    _render_pause(project, view)
    _render_timing(project, view)
    _render_review(view)


def _render_lock(view) -> None:
    st.subheader("Lock")
    lock = view.effective_lock
    if lock is None:
        st.warning("Kein wirksamer Script Lock vorhanden.")
        return
    st.write(
        {
            "lock_id": lock.lock_id,
            "script_id": lock.script_id,
            "script_version": lock.script_version,
            "fingerprint": lock.lock_fingerprint,
            "status": lock.status.value,
        }
    )


def _render_voice(project, view) -> None:
    st.subheader("Voice")
    profile = view.voice_profile
    if profile is not None:
        st.write(
            {
                "voice_profile_id": profile.voice_profile_id,
                "provider": profile.provider,
                "voice_identifier": profile.voice_identifier,
                "format": profile.output_profile.audio_format,
                "sample_rate_hz": profile.output_profile.sample_rate_hz,
                "channels": profile.output_profile.channels,
            }
        )
    if st.button(
        "Voice erzeugen",
        disabled=not view.can_start_voice,
        key="discovery_v2_narration_start_voice",
    ):
        result = start_voice_generation_run(project, sync=False)
        if result.started:
            discovery_ui_flash_and_rerun(result.message, level="info")
        else:
            st.warning(result.message)
    if view.active_run is not None:
        st.caption(
            f"Aktiver Narration-Run: `{view.active_run.run_id}` "
            f"({view.active_run.scope}/{view.active_run.status.value})"
        )
    if view.voice_runs:
        st.dataframe(
            [
                {
                    "Run": run.run_id,
                    "Scope": run.scope,
                    "Status": run.status.value,
                    "Created": run.segments_created,
                    "Reused": run.segments_reused,
                    "Failed": run.segments_failed,
                    "Error": run.error_code,
                }
                for run in view.voice_runs
            ],
            hide_index=True,
            use_container_width=True,
        )


def _render_pause(project, view) -> None:
    st.subheader("Pause")
    if st.button(
        "Pausenregie erzeugen",
        disabled=not view.can_start_pause,
        key="discovery_v2_narration_start_pause",
    ):
        result = start_pause_direction_run(project, sync=False)
        if result.started:
            discovery_ui_flash_and_rerun(result.message, level="info")
        else:
            st.warning(result.message)
    if view.pause_plans:
        st.dataframe(
            [
                {
                    "Plan": plan.pause_plan_id,
                    "Voice Run": plan.voice_run_id,
                    "Status": plan.status.value,
                    "Prompt": plan.prompt_version,
                    "Schema": plan.response_schema_version,
                }
                for plan in view.pause_plans
            ],
            hide_index=True,
            use_container_width=True,
        )


def _render_timing(project, view) -> None:
    st.subheader("Timing")
    st.caption(f"Projekt-Timebase: {project.fps:g} fps")
    if st.button(
        "Narration Timing aufloesen",
        disabled=not view.can_resolve_timing,
        key="discovery_v2_narration_start_timing",
    ):
        result = start_narration_timing_run(project, sync=True)
        if result.started:
            discovery_ui_flash_and_rerun(result.message, level="info")
        else:
            st.warning(result.message)
    if view.timelines:
        st.dataframe(
            [
                {
                    "Timeline": timeline.timeline_id,
                    "Status": timeline.status.value,
                    "Frames": timeline.total_frames,
                    "Seconds": round(timeline.total_duration_seconds, 3),
                    "Timebase": f"{timeline.timebase.fps_numerator}/{timeline.timebase.fps_denominator}",
                }
                for timeline in view.timelines
            ],
            hide_index=True,
            use_container_width=True,
        )


def _render_review(view) -> None:
    st.subheader("Review")
    if view.voice_segments:
        st.markdown("**Voice Segments**")
        st.dataframe(
            [
                {
                    "Ordinal": segment.sentence_ordinal,
                    "Sentence": segment.sentence_id,
                    "Segment": segment.segment_id,
                    "Duration": round(segment.duration_seconds, 3),
                    "Samples": segment.sample_count,
                    "Hash": segment.audio_sha256[:12],
                    "Path": segment.relative_path,
                }
                for segment in view.voice_segments
            ],
            hide_index=True,
            use_container_width=True,
        )
    if view.timelines:
        latest = view.timelines[0]
        st.markdown("**Timeline Entries**")
        st.dataframe(
            [
                {
                    "Ordinal": entry.ordinal,
                    "Type": entry.entry_type.value,
                    "Function": entry.function,
                    "Start s": round(entry.start_seconds, 3),
                    "End s": round(entry.end_seconds, 3),
                    "Start frame": entry.start_frame,
                    "End frame": entry.end_frame,
                }
                for entry in latest.entries
            ],
            hide_index=True,
            use_container_width=True,
        )


__all__ = ["render_discovery_narration_page"]
