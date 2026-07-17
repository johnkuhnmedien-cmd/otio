"""Streamlit-Seite: Discovery V2 Assetanalyse (Phase 8A — read-only Shell)."""

from __future__ import annotations

import streamlit as st

from otio_app.discovery_v2.application.asset_analysis_eligibility_service import (
    get_analysis_eligibility_view,
)
from otio_app.discovery_v2.ui.overview import active_discovery_project


def _short_hash(value: str | None) -> str:
    if not value:
        return "—"
    text = value.strip()
    if len(text) <= 12:
        return text
    return f"{text[:8]}…{text[-4:]}"


def render_discovery_asset_analysis_page() -> None:
    """Assetanalyse — nur persistierte Eligibility, kein Jobstart."""
    st.title("Assetanalyse")
    project = active_discovery_project()
    if project is None:
        return

    st.subheader("Status")
    st.info(
        "Die lokale Assetanalyse ist noch nicht aktiviert. "
        "Diese Seite zeigt zunächst nur die analysierbaren Working-Media-Ausgaben."
    )

    view = get_analysis_eligibility_view(project)
    if not view.ok:
        st.warning(view.message or "Eligibility nicht verfügbar.")
        if view.chain_error_code:
            st.caption(f"Grund: `{view.chain_error_code}`")
        _render_not_implemented()
        return

    st.caption(
        f"Analyseprofil-Vertrag: `{view.analysis_profile_version}` · "
        f"Plan: `{view.plan_id or '—'}` · "
        f"gespeicherte Analysis-Runs: {view.analysis_run_count}"
    )

    st.subheader("Analysierbare Assets")
    if not view.items:
        st.write("Keine Plan-Assets vorhanden.")
    else:
        rows = []
        for item in view.items:
            rows.append(
                {
                    "Anzeige": item.display_name or item.asset_id,
                    "Asset-ID": item.asset_id,
                    "Source Group": item.source_group,
                    "Medienart": item.media_kind,
                    "erwartetes Profil": item.expected_processing_profile_version
                    or "—",
                    "tatsächliches Profil": item.actual_processing_profile_version
                    or "—",
                    "Working-Media-ID": item.working_media_id or "—",
                    "Output-Hash": _short_hash(item.output_sha256),
                    "eligible": "ja" if item.eligible else "nein",
                    "Blockierungsgrund": item.reason_code or "—",
                }
            )
        st.dataframe(rows, use_container_width=True, hide_index=True)

    eligible_count = sum(1 for i in view.items if i.eligible)
    st.caption(
        f"{eligible_count} von {len(view.items)} Assets eligible für visuelle Analyse."
    )

    _render_not_implemented()


def _render_not_implemented() -> None:
    st.subheader("Noch nicht implementiert")
    st.markdown(
        "- Shot-Erkennung folgt in Phase 8B\n"
        "- Frame-Extraktion folgt in Phase 8B\n"
        "- externe Modellanalyse folgt in Phase 8C"
    )
