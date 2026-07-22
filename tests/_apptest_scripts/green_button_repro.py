"""Isoliertes AppTest-Skript für render_new_feature_button (_shared.py) —
prüft ausschließlich das Widget-Verhalten (Label, CSS-Markup, Klick), ohne
irgendein Projekt/Pipeline-Setup."""

from __future__ import annotations

import streamlit as st

from otio_app.ui.voiceover_generation._shared import render_new_feature_button

if "green_button_clicks" not in st.session_state:
    st.session_state["green_button_clicks"] = 0

clicked = render_new_feature_button(
    "🟢 Testfunktion ausführen",
    key="green_button_repro_button",
    help="Testhilfe",
)
if clicked:
    st.session_state["green_button_clicks"] += 1

st.text(f"clicks={st.session_state['green_button_clicks']}")
