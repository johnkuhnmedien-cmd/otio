"""Gemeinsame Anzeige für Analyse-Berichte."""

from __future__ import annotations

import streamlit as st

from otio_app.services.analysis_progress import AnalysisRunReport


def render_analysis_report(report: AnalysisRunReport) -> None:
    st.markdown("**Analyse-Bericht**")
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Neu analysiert", report.media_analyzed)
    with col2:
        st.metric("Aus Cache", report.media_cached)
    with col3:
        st.metric("Fehler", report.media_failed)
    with col4:
        st.metric("Ordner übersprungen", len(report.folders_skipped))
    if report.cancelled:
        st.warning("Analyse wurde vorzeitig gestoppt.")
    if report.failures:
        st.warning("Fehlerhafte oder unvollständige Assets:")
        for line in report.failures[:20]:
            st.caption(f"• {line}")
        if len(report.failures) > 20:
            st.caption(f"… und {len(report.failures) - 20} weitere")
