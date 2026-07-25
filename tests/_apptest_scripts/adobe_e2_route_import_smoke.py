"""E2 AppTest-Smoke: echte Import-Seite (Mocks kommen aus dem Pytest-Harness).

Der Test `test_adobe_e2_route_apptest.py` setzt per monkeypatch:
  - Adobe HTTP-Grenze (urlopen)
  - OAuth/API-Keys
  - Download-Projekte / Plan / Job project_dir
  - Readiness / Entitlement / OAuth-Panel

Dieses Script setzt nur Session-State und rendert die echte Seite.
Kein Seed fertiger AdobeResearchImportResult-Objekte.
"""

from __future__ import annotations

import os

import streamlit as st

st.set_page_config(page_title="OTIO Adobe E2 Route Smoke", layout="wide")

PROJECT_ID = os.environ.get("ADOBE_E2_SMOKE_PROJECT_ID", "e2-route-smoke")

from otio_app.ui import adobe_research_import_page as page_mod

st.session_state[page_mod._ACTIVE_PROJECT_KEY] = PROJECT_ID

# st.rerun-Limit kommt aus dem Pytest-Harness (monkeypatch), nicht hier —
# sonst leakt der Patch in spätere AppTests derselben Prozess-Session.

st.caption(
    "E2 Route Smoke · echte `render_adobe_research_import_page` · "
    "Start → JobManager.start → download_research_import (gemocktes Adobe-Netz)"
)

from otio_app.ui.adobe_research_import_page import render_adobe_research_import_page

render_adobe_research_import_page()
