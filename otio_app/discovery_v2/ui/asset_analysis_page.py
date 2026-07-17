"""Streamlit-Seite: Discovery V2 Assetanalyse (Phase 8B — lokale Vorbereitung)."""

from __future__ import annotations

import streamlit as st

from otio_app.discovery_v2.application.analysis_prepare_service import (
    get_analysis_prepare_view,
    start_analysis_prepare,
)
from otio_app.discovery_v2.domain.asset_analysis import (
    ANALYSIS_PREPARE_PROFILE_VERSION,
    FRAME_SAMPLE_PROFILE_VERSION,
    SHOT_DETECT_PROFILE_VERSION,
)
from otio_app.discovery_v2.persistence.asset_analysis_repository import (
    list_representative_frames_for_project,
    list_technical_shots_for_project,
    open_analysis_registry,
)
from otio_app.discovery_v2.persistence.asset_registry_database import (
    RegistryDatabaseError,
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
    """Assetanalyse — lokale Prepare-UI, nur persistierte Daten beim Rendering."""
    st.title("Assetanalyse")
    project = active_discovery_project()
    if project is None:
        return

    view = get_analysis_prepare_view(project)

    st.subheader("Lokale Vorbereitung")
    st.info(
        "Die lokale Vorbereitung erkennt technische Videoszenen und erzeugt "
        "ausgewählte Analyseframes. Es werden keine Medien an externe Dienste "
        "gesendet."
    )

    if not view.ok:
        st.warning(view.message or "Eligibility nicht verfügbar.")
        if view.chain_error_code:
            st.caption(f"Grund: `{view.chain_error_code}`")
        return

    st.caption(
        f"Prepare-Profil: `{ANALYSIS_PREPARE_PROFILE_VERSION}` · "
        f"Shot: `{SHOT_DETECT_PROFILE_VERSION}` · "
        f"Frame: `{FRAME_SAMPLE_PROFILE_VERSION}` · "
        f"Plan: `{view.plan_id or '—'}`"
    )
    if view.active_run is not None:
        st.caption(
            f"Aktiver Analysis-Run: `{view.active_run.run_id}` "
            f"({view.active_run.scope}/{view.active_run.status.value})"
        )
    elif view.latest_run is not None:
        st.caption(
            f"Letzter Prepare-Run: `{view.latest_run.run_id}` "
            f"({view.latest_run.status.value})"
        )

    if not view.items:
        st.write("Keine Plan-Assets vorhanden.")
    else:
        rows = []
        for item in view.items:
            el = item.eligibility
            rows.append(
                {
                    "Anzeige": el.display_name or el.asset_id,
                    "Medienart": el.media_kind,
                    "Working-Media-Profil": el.actual_processing_profile_version
                    or "—",
                    "Analysis Identity": item.analysis_identity_id or "—",
                    "eligible": "ja" if el.eligible else "nein",
                    "Prepare-Status": (
                        item.prepare_status.value if item.prepare_status else "—"
                    ),
                    "Shots": item.shot_count,
                    "Frames": item.frame_count,
                    "Fehlergrund": item.error_code or el.reason_code or "—",
                }
            )
        st.dataframe(rows, use_container_width=True, hide_index=True)

    eligible_count = sum(1 for i in view.items if i.eligibility.eligible)
    st.caption(
        f"{eligible_count} von {len(view.items)} Assets eligible für visuelle Analyse."
    )

    start_clicked = st.button(
        "Lokale Analyse vorbereiten",
        disabled=not view.can_start,
        key="discovery_v2_analysis_prepare_start",
    )
    if start_clicked and view.can_start:
        result = start_analysis_prepare(project, sync=False)
        if result.started:
            st.success(result.message)
        else:
            st.warning(result.message)

    _render_prepare_review(project.id, project.project_root_path)

    st.subheader("Noch nicht implementiert")
    st.markdown(
        "- externe Modellanalyse folgt in Phase 8C\n"
        "- visuelle Beobachtungen / Freigaben folgen später\n"
        "- Visual Beats / Dramaturgie folgen später"
    )


def _render_prepare_review(project_id: str, project_root) -> None:
    st.subheader("Review: Technical Shots und Frames")
    try:
        conn = open_analysis_registry(project_root)
    except RegistryDatabaseError as exc:
        st.caption(f"Registry nicht lesbar: {exc}")
        return
    try:
        shots = list_technical_shots_for_project(conn, project_id=project_id)
        frames = list_representative_frames_for_project(conn, project_id=project_id)
    finally:
        conn.close()

    if not shots and not frames:
        st.write("Noch keine persistierten Shots oder Frames vorhanden.")
        return

    if shots:
        st.markdown("**Technical Shots**")
        st.dataframe(
            [
                {
                    "Asset": shot.asset_id,
                    "Ordinal": shot.ordinal,
                    "Start": round(shot.start_seconds, 3),
                    "Ende": round(shot.end_seconds, 3),
                    "Dauer": round(shot.duration_seconds, 3),
                    "Profil": shot.detection_profile_version,
                }
                for shot in shots
            ],
            use_container_width=True,
            hide_index=True,
        )

    if frames:
        st.markdown("**Representative Frames**")
        st.dataframe(
            [
                {
                    "Asset": frame.asset_id,
                    "Ordinal": frame.ordinal,
                    "Timestamp": (
                        "—"
                        if frame.timestamp_seconds is None
                        else round(frame.timestamp_seconds, 3)
                    ),
                    "Breite": frame.width,
                    "Höhe": frame.height,
                    "Helligkeit": round(frame.brightness_mean, 4),
                    "Schwarzanteil": round(frame.black_fraction, 4),
                    "Schärfehinweis": round(frame.sharpness_score, 2),
                    "Profil": frame.sampling_profile_version,
                    "Pfad": frame.relative_path,
                    "Hash": _short_hash(frame.frame_sha256),
                }
                for frame in frames
            ],
            use_container_width=True,
            hide_index=True,
        )
