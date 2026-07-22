"""Schritt 8 Enhanced: Final Output — OTIO nur aus aufgelöster Timeline."""

from __future__ import annotations

import streamlit as st

from otio_app.services.without_voiceover_enhanced.io_utils import load_model
from otio_app.services.without_voiceover_enhanced.models import ResolvedTimelineDocument
from otio_app.services.without_voiceover_enhanced.otio_export_service import (
    EnhancedOtioExportError,
    export_otio_from_resolved_timeline,
    validate_resolved_timeline_for_production,
)
from otio_app.services.without_voiceover_enhanced.paths import resolved_timeline_path
from otio_app.ui.without_voiceover_enhanced._shared import get_enhanced_project


def render_enhanced_final_output_page() -> None:
    st.header("⑧ Final Output (Enhanced)")
    st.caption(
        "Keine neue redaktionelle Planung. Lädt freigegebenen finalen Cut Plan / "
        "technisch aufgelöste Timeline und erzeugt OTIO. "
        "Produktions-Export prüft reale Medien und Source-Ranges fail-closed."
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

    gate_errors = validate_resolved_timeline_for_production(project, resolved)
    has_errors = bool(resolved.errors) or bool(gate_errors)
    st.success(
        f"Skript `{resolved.script_version}` · "
        f"{resolved.total_duration_seconds:.2f}s · "
        f"{len(resolved.shots)} Shots · {len(resolved.audio_segments)} Audio-Segmente · "
        f"Vorlauf {resolved.voiceover_preroll_sec:.2f}s · "
        f"Nachlauf {resolved.voiceover_postroll_sec:.2f}s"
    )
    if has_errors:
        st.error(
            f"Export-Gate: {len(resolved.errors) + len(gate_errors)} Fehler — "
            "Produktions-OTIO ist gesperrt. Der Test-Export unten ist nur zur "
            "Diagnose (Lücken/Gaps) und nicht für Resolve-Produktion."
        )
        with st.expander("Fehlerliste anzeigen", expanded=True):
            for err in list(resolved.errors) + list(gate_errors):
                st.write(f"- {err}")

    show_detail_key = f"enh_otio_show_detail_{project.id}"
    st.checkbox("Shot-/Audio-Details laden", key=show_detail_key)
    if st.session_state.get(show_detail_key):
        for segment in resolved.audio_segments:
            st.caption(
                f"Audio {segment.segment_id}: "
                f"{segment.timeline_start_seconds:.2f}–"
                f"{segment.timeline_end_seconds:.2f} "
                f"(pause {segment.pause_after_seconds:.2f}s)"
            )
        for shot in resolved.shots:
            st.caption(
                f"Video {shot.shot_id}: "
                f"{shot.timeline_start_seconds:.2f}–"
                f"{shot.timeline_end_seconds:.2f} · "
                f"{shot.asset_id} · `{shot.resolved_media_path}` · "
                f"src {shot.source_start_seconds:.3f}–{shot.source_end_seconds:.3f}"
            )

    basename = st.text_input(
        "OTIO-Dateiname",
        value=f"{project.name}_enhanced",
        key=f"enh_otio_name_{project.id}",
    )
    if st.button(
        "Produktions-OTIO erzeugen",
        type="primary",
        key=f"enh_otio_export_{project.id}",
        disabled=has_errors,
        help=(
            "Fail-closed: blockiert bei Resolve-/Medienfehlern."
            if has_errors
            else "Produktions-Export (fail-closed, allow_errors=False)."
        ),
    ):
        try:
            path = export_otio_from_resolved_timeline(
                project, basename=basename.strip() or "enhanced", allow_errors=False
            )
            st.success(f"Produktions-OTIO geschrieben: `{path}`")
        except EnhancedOtioExportError as exc:
            st.error(str(exc))

    st.markdown("**Test-/Diagnose-Export (mit Lücken)**")
    st.caption(
        "Nicht für DaVinci-Resolve-Produktion. Schreibt eine OTIO mit Gaps für "
        "fehlende/ungültige Shots (`allow_errors=True`). Nur zum Anschauen."
    )
    test_name = (basename.strip() or "enhanced") + "_preview_gaps"
    if st.button(
        "Test-OTIO mit Lücken erzeugen",
        key=f"enh_otio_export_gaps_{project.id}",
    ):
        try:
            path = export_otio_from_resolved_timeline(
                project,
                basename=test_name,
                allow_errors=True,
            )
            st.warning(
                f"Diagnose-OTIO geschrieben: `{path}` · "
                f"{len(resolved.shots)} Shots · "
                f"nicht für Produktion verwenden"
            )
        except EnhancedOtioExportError as exc:
            st.error(str(exc))
