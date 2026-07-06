"""Systemstatus-Seite inkl. Hintergrund-Diagnose."""

from __future__ import annotations

import streamlit as st

from otio_app.build_info import expected_feature_markers, format_build_label
from otio_app.services.gemini_client import format_gemini_model_label, get_default_gemini_model
from otio_app.system_checks import run_all_checks
from otio_app.ui.activity import render_activity_panel
from otio_app.ui.api_keys_settings import render_api_keys_settings


def render_system_status_page() -> None:
    st.header("Systemstatus")
    st.caption(f"App-Build: **{format_build_label()}**")
    with st.expander("Erwartete Merkmale dieses Stands", expanded=False):
        for marker in expected_feature_markers():
            st.caption(f"• {marker}")
    render_activity_panel(expanded=True)
    st.divider()
    render_api_keys_settings()
    st.divider()
    for result in run_all_checks():
        icon = "✅" if result.ok else "❌"
        st.subheader(f"{icon} {result.name}")
        st.write(result.message)
        if result.version:
            st.caption(f"Version: {result.version}")

    default_model = get_default_gemini_model()
    st.subheader("🤖 Gemini")
    st.write(
        f"Standardmodell (aus `.env` oder App-Default): "
        f"**{format_gemini_model_label(default_model)}** (`{default_model}`)"
    )
    st.caption(
        "Unter „Projekt bearbeiten“ kann pro Sitzung ein anderes Modell gewählt werden."
    )
