"""Streamlit-Seite: Discovery V2 Technische Prüfung."""

from __future__ import annotations

from collections import defaultdict

import streamlit as st

from otio_app.discovery_v2.application.technical_validation_service import (
    TechnicalValidationServiceError,
    can_start_technical_validation,
    get_latest_report,
    get_validation_status,
    get_validation_summary,
    report_path_for_run,
    start_technical_validation,
)
from otio_app.discovery_v2.domain.technical_validation import (
    ACTIVE_RUN_STATUSES,
    AssetValidationStatus,
    ValidationRunStatus,
)
from otio_app.discovery_v2.paths import get_discovery_v2_root
from otio_app.discovery_v2.ui.overview import active_discovery_project


_SESSION_ERROR_KEY = "discovery_v2_validation_error"
_SESSION_INFO_KEY = "discovery_v2_validation_info"


def render_discovery_technical_validation_page() -> None:
    """Technische Prüfung — liest Status; startet nur über expliziten Button."""
    st.title("Technische Prüfung")
    project = active_discovery_project()
    if project is None:
        return

    st.caption(
        "Die Quelldateien werden gelesen und technisch geprüft. Es werden "
        "keine Medien kopiert oder verändert."
    )

    error = st.session_state.pop(_SESSION_ERROR_KEY, None)
    info = st.session_state.pop(_SESSION_INFO_KEY, None)
    if error:
        st.error(error)
    if info:
        st.info(info)

    ok, block_msg, ctx = can_start_technical_validation(project)
    run, validations, status_err = get_validation_status(project)
    if status_err:
        st.warning(status_err)

    # Vorbedingungen / Kontext
    st.subheader("Registry-Basis")
    if ctx is not None:
        st.write(f"**Registry-Import-ID:** `{ctx['import_id']}`")
        st.write(f"**Registrierte Quellen:** {ctx['asset_count']}")
        st.write(f"**Auswahl-ID:** `{ctx['selection_id']}`")
        st.write(f"**Scan-ID:** `{ctx['scan_id']}`")
    else:
        st.write("Kein gültiger Registry-Import für die aktuelle Auswahl.")
        if block_msg:
            st.warning(block_msg)

    active = run is not None and run.status in ACTIVE_RUN_STATUSES
    can_click = ok and not active

    if st.button(
        "Technische Prüfung starten",
        type="primary",
        disabled=not can_click,
        key="discovery_v2_validation_start_btn",
    ):
        try:
            result = start_technical_validation(project, sync=False)
        except TechnicalValidationServiceError as exc:
            st.session_state[_SESSION_ERROR_KEY] = str(exc)
        else:
            if result.started:
                st.session_state[_SESSION_INFO_KEY] = result.message
            else:
                st.session_state[_SESSION_ERROR_KEY] = result.message
        st.rerun()

    if not can_click and block_msg and not active:
        st.caption(block_msg)

    # Status neu laden nach möglichem Start
    run, validations, _ = get_validation_status(project)
    if run is None:
        st.info("Noch keine technische Prüfung durchgeführt.")
        return

    st.subheader("Prüfauftrag")
    st.write(f"**Status:** `{run.status.value}`")
    st.write(
        f"**Fortschritt:** {run.processed_assets} / {run.total_assets} Assets"
    )
    st.write(f"**Fehlerzahl:** {run.failed_assets}")
    st.write(f"**Run-ID:** `{run.run_id}`")

    if run.status in ACTIVE_RUN_STATUSES:
        st.info("Prüfung läuft … Seite neu laden, um den Fortschritt zu aktualisieren.")
        return

    summary = get_validation_summary(validations)
    st.subheader("Ergebnis")
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Erfolgreich geprüft", summary["successful"])
        st.metric("Quellen nicht gefunden", summary["source_missing"])
    with col2:
        st.metric("Fehlgeschlagen", summary["failed"])
        st.metric("Quellen verändert", summary["source_changed"])
    with col3:
        st.metric("Mögliche Inhaltsdubletten", summary["potential_duplicates"])

    if run.error_summary:
        st.error(run.error_summary)

    report_path = report_path_for_run(project, run.run_id)
    st.write(f"**Prüfbericht:** `{report_path}`")
    latest_report, latest_warn = get_latest_report(project)
    if latest_warn:
        st.warning(latest_warn)
    elif latest_report is not None:
        rel = latest_report.report_relative_path
        st.caption(
            f"Latest-Pointer: `_otio_v2/{rel}` · Artefaktwurzel: "
            f"`{get_discovery_v2_root(project.project_root_path)}`"
        )

    # Gruppierte Ergebnisliste
    by_group: dict[str, list] = defaultdict(list)
    for item in validations:
        group = item.source_group or "__root__"
        by_group[group].append(item)

    st.subheader("Ergebnisse nach Quellgruppe")
    if not validations:
        st.caption("Keine Asset-Ergebnisse gespeichert.")
        return

    for group_name in sorted(by_group.keys()):
        items = by_group[group_name]
        with st.expander(f"{group_name} ({len(items)})", expanded=False):
            for item in items:
                status_label = item.status.value
                dup = ""
                if item.duplicate_hint:
                    dup = f" · {item.duplicate_hint}"
                line = f"`{item.source_relative_path}` — **{status_label}**{dup}"
                if item.status == AssetValidationStatus.PROBE_SUCCEEDED:
                    st.markdown(line)
                    details = []
                    if item.sha256:
                        details.append(f"sha256=`{item.sha256[:12]}…`")
                    if item.frame_rate_numerator and item.frame_rate_denominator:
                        details.append(
                            f"fps={item.frame_rate_numerator}/"
                            f"{item.frame_rate_denominator}"
                        )
                    if item.width and item.height:
                        details.append(f"{item.width}×{item.height}")
                    if item.media_kind == "video":
                        details.append(
                            f"pix_fmt={item.pixel_format or 'null'}"
                        )
                        details.append(
                            f"bit_depth="
                            f"{item.bit_depth if item.bit_depth is not None else 'null'}"
                        )
                    if item.embedded_timecode:
                        details.append(f"tc={item.embedded_timecode}")
                    elif item.media_kind == "video":
                        details.append("tc=null")
                    if details:
                        st.caption(" · ".join(details))
                else:
                    st.markdown(line)
                    if item.error_message:
                        st.caption(item.error_message)
