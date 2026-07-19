"""Schritt 8 Enhanced: Final Output — OTIO nur aus aufgelöster Timeline."""

from __future__ import annotations

import streamlit as st

from otio_app.services.without_voiceover_enhanced.io_utils import load_model
from otio_app.services.without_voiceover_enhanced.models import ResolvedTimelineDocument
from otio_app.services.without_voiceover_enhanced.otio_export_service import (
    EnhancedOtioExportError,
    export_otio_from_resolved_timeline,
)
from otio_app.services.without_voiceover_enhanced.paths import resolved_timeline_path
from otio_app.ui.without_voiceover_enhanced._shared import get_enhanced_project


def render_enhanced_final_output_page() -> None:
    st.header("⑧ Final Output (Enhanced)")
    st.caption(
        "Keine neue redaktionelle Planung. Lädt freigegebenen finalen Cut Plan / "
        "technisch aufgelöste Timeline und erzeugt OTIO."
    )
    project = get_enhanced_project()
    if project is None:
        return

    resolved = load_model(resolved_timeline_path(project), ResolvedTimelineDocument)
    if resolved is None:
        st.error(
            "Keine aufgelöste Timeline vorhanden — zuerst Schritt 7 "
            "(Finalen Cut Plan erzeugen und technisch auflösen)."
        )
        return

    if resolved.errors:
        st.error("Timeline enthält Fehler und darf nicht exportiert werden:")
        for err in resolved.errors:
            st.write(f"- {err}")
        return

    st.success(
        f"Skript `{resolved.script_version}` · "
        f"{resolved.total_duration_seconds:.2f}s · "
        f"{len(resolved.shots)} Shots · {len(resolved.audio_segments)} Audio-Segmente"
    )
    for segment in resolved.audio_segments:
        st.caption(
            f"Audio {segment.segment_id}: "
            f"{segment.timeline_start_seconds:.2f}–{segment.timeline_end_seconds:.2f} "
            f"(pause {segment.pause_after_seconds:.2f}s)"
        )
    for shot in resolved.shots:
        st.caption(
            f"Video {shot.shot_id}: "
            f"{shot.timeline_start_seconds:.2f}–{shot.timeline_end_seconds:.2f} · "
            f"{shot.asset_id}"
        )

    basename = st.text_input(
        "OTIO-Dateiname",
        value=f"{project.name}_enhanced",
        key=f"enh_otio_name_{project.id}",
    )
    if st.button("OTIO erzeugen", type="primary"):
        try:
            path = export_otio_from_resolved_timeline(project, basename=basename.strip() or "enhanced")
            st.success(f"OTIO geschrieben: `{path}`")
        except EnhancedOtioExportError as exc:
            st.error(str(exc))
