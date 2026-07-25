"""DEMO-/Fixture-UI — KEIN Produktions-Smoke und KEIN Abnahmenachweis.

Diese Datei enthält fest codierte Demo-Daten und importiert keinen
Produktionsrenderer. Für DIAG-002-R1 bitte ausschließlich
`scripts/adobe_diag002_r1_production_smoke_app.py` verwenden
(ruft `render_adobe_research_import_page` auf — dieselbe Funktion wie
Route `adobe-stock-import`).
"""

from __future__ import annotations

import streamlit as st

st.set_page_config(page_title="Adobe DIAG-002 DEMO (nicht Abnahme)", layout="wide")
st.error(
    "DEMO ONLY — nicht als Produktions-Smoke / Abnahmenachweis verwenden. "
    "Bitte scripts/adobe_diag002_r1_production_smoke_app.py starten."
)
st.caption("Fest codierte Demo-Daten, kein otio_app-Produktionsrenderer.")
