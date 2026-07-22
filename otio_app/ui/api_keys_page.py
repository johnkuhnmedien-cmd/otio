"""Eigene Seite für API-Schlüssel."""

from __future__ import annotations

import streamlit as st

from otio_app.ui.api_keys_settings import render_api_keys_settings


def render_api_keys_page() -> None:
    st.header("🔑 API-Schlüssel")
    st.caption(
        "Schlüssel für Gemini (Asset-Analysen, Voice-over), ChatGPT und Claude "
        "(Schnittplan-Vorschlag), Pexels und weitere Dienste. "
        "**Speichern** legt Keys in `data/user_secrets.env` ab (nicht in Git)."
    )
    render_api_keys_settings()
