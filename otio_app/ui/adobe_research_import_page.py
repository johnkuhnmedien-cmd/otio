"""UI: Research-Excel hochladen → Adobe Stock lizenzieren/herunterladen."""

from __future__ import annotations

import time
from pathlib import Path

import streamlit as st

from otio_app.paths import clean_user_path_input
from otio_app.services.adobe_research_import import (
    STATUS_CANCELLED,
    STATUS_DOWNLOADED,
    STATUS_DOWNLOADING,
    STATUS_ERROR,
    STATUS_OPEN,
    AdobeResearchImportBoard,
    AdobeResearchImportPlan,
    build_research_import_board,
    parse_research_excel,
)
from otio_app.services.adobe_research_import_job import (
    JobStatus,
    get_research_import_job_manager,
)
from otio_app.services.supplement_sources.adobe_stock import AdobeStockAdapter
from otio_app.ui.adobe_oauth_panel import render_adobe_oauth_panel

_PLAN_BYTES_KEY = "adobe_research_plan_bytes"
_PLAN_NAME_KEY = "adobe_research_plan_name"


def _status_label(status: str) -> str:
    return {
        STATUS_DOWNLOADED: "Downloaded",
        STATUS_OPEN: "Open",
        STATUS_ERROR: "Fehler",
        STATUS_DOWNLOADING: "Läuft",
        STATUS_CANCELLED: "Open (gestoppt)",
    }.get(status, status)


def _cache_plan_from_upload(uploaded) -> AdobeResearchImportPlan | None:
    if uploaded is None:
        raw = st.session_state.get(_PLAN_BYTES_KEY)
        if not raw:
            return None
        return parse_research_excel(raw)
    data = uploaded.getvalue()
    st.session_state[_PLAN_BYTES_KEY] = data
    st.session_state[_PLAN_NAME_KEY] = getattr(uploaded, "name", "") or "research.xlsx"
    return parse_research_excel(data)


def _render_board(board: AdobeResearchImportBoard) -> None:
    st.subheader("Fortschritt (Excel-Spiegel)")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Gesamt", board.total)
    c2.metric("Downloaded", board.downloaded)
    c3.metric("Open", board.open_count)
    c4.metric("Fehler", board.error_count)

    chapter_rows = [
        {
            "Kapitel": ch.title,
            "Ordner": ch.folder_name,
            "Downloaded": ch.downloaded,
            "Open": ch.open_count,
            "Fehler": ch.error_count,
            "Gesamt": ch.total,
            "Stand": f"{ch.downloaded}/{ch.total}",
        }
        for ch in board.chapters
    ]
    st.dataframe(chapter_rows, use_container_width=True, hide_index=True)

    filter_mode = st.radio(
        "Asset-Ansicht",
        options=["Alle", "Nur Open", "Nur Downloaded", "Nur Fehler"],
        horizontal=True,
        key="adobe_research_board_filter",
    )
    asset_rows = []
    for ch in board.chapters:
        for asset in ch.assets:
            if filter_mode == "Nur Open" and asset.status not in {
                STATUS_OPEN,
                STATUS_CANCELLED,
            }:
                continue
            if filter_mode == "Nur Downloaded" and asset.status != STATUS_DOWNLOADED:
                continue
            if filter_mode == "Nur Fehler" and asset.status != STATUS_ERROR:
                continue
            asset_rows.append(
                {
                    "Kapitel": asset.chapter_title,
                    "Asset ID": asset.asset_id,
                    "Status": _status_label(asset.status),
                    "Lizenz": asset.license or "—",
                    "Datei": Path(asset.local_path).name if asset.local_path else "—",
                    "Hinweis": asset.message or "—",
                    "Link": asset.link or "—",
                }
            )
    st.dataframe(asset_rows, use_container_width=True, hide_index=True, height=360)
    if board.target_root:
        st.caption(
            f"Board-Datei: `{Path(board.target_root) / 'adobe_research_import_board.json'}`"
        )


def render_adobe_research_import_page() -> None:
    st.header("Adobe Stock Import (Research-Excel)")
    st.caption(
        "Vor der Projektanlage: Research-Template hochladen, Zielordner wählen, "
        "Kapitelordner anlegen und Assets als `{Kapitel}_Asset_01` lizenzieren/herunterladen."
    )
    st.caption(
        "Videos: bevorzugt **Video_4K**; wenn die Datei über **600 MB** liegt "
        "(Content-Length oder während des Downloads gemessen), Fallback auf **Video_HD**. "
        "Gewählte Lizenz steht danach in der `.adobe.json`-Sidecar-Datei."
    )

    render_adobe_oauth_panel(key_prefix="adobe_import_oauth")
    st.divider()

    readiness = AdobeStockAdapter().readiness()
    if readiness.acquire_enabled:
        st.success(readiness.message)
    elif readiness.search_enabled:
        st.warning(readiness.message)
    else:
        st.error(readiness.message)

    uploaded = st.file_uploader(
        "Research-Excel (.xlsx)",
        type=["xlsx"],
        key="adobe_research_xlsx",
        help=(
            "Layout: Zeile 1 Kapitel-Titel alle 3 Spalten, "
            "Spalte 2 je Block = Adobe Asset ID, Spalte 3 = Link."
        ),
    )
    target_raw = st.text_input(
        "Zielordner für Kapitel",
        key="adobe_research_target",
        placeholder="/Pfad/zu/Ireland",
        help="Hier werden Unterordner je Kapitel-Überschrift erstellt.",
    )

    try:
        plan = _cache_plan_from_upload(uploaded)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Excel konnte nicht gelesen werden: {exc}")
        return

    if plan is None:
        st.info("Excel hochladen, um die Kapitelvorschau und den Fortschritts-Spiegel zu sehen.")
        return

    target = clean_user_path_input(target_raw) if target_raw.strip() else ""
    mgr = get_research_import_job_manager()
    job = mgr.get_state()
    board_root = target or (job.target_root if job.target_root else None)

    st.write(
        f"**{plan.chapter_count} Kapitel** · **{plan.asset_count} Assets** "
        f"(Sheet `{plan.sheet_name}`)"
        + (
            f" · Datei `{st.session_state.get(_PLAN_NAME_KEY, '')}`"
            if st.session_state.get(_PLAN_NAME_KEY)
            else ""
        )
    )

    board = build_research_import_board(
        plan,
        board_root,
        live_statuses=job.live_statuses if job.live_statuses else None,
    )
    _render_board(board)

    st.subheader("Import steuern")
    chapter_labels = [ch.title for ch in plan.chapters]
    selected = st.multiselect(
        "Kapitel zum Import",
        options=chapter_labels,
        default=chapter_labels,
        key="adobe_research_chapters",
    )
    skip_existing = st.checkbox(
        "Bereits heruntergeladene Asset-IDs überspringen",
        value=True,
        key="adobe_research_skip_existing",
    )

    if target:
        st.caption(f"Ziel: `{target}`")
        example = plan.chapters[0] if plan.chapters else None
        if example:
            st.caption(
                f"Beispiel: `{Path(target) / example.folder_name / (example.folder_name + '_Asset_01.mp4')}`"
            )

    running = job.status == JobStatus.RUNNING
    can_start = bool(target) and bool(selected) and readiness.acquire_enabled and not running

    col_start, col_stop, col_refresh = st.columns(3)
    with col_start:
        if st.button(
            "▶ Lizenzieren & herunterladen",
            type="primary",
            disabled=not can_start,
            key="adobe_research_run",
            use_container_width=True,
        ):
            if not target:
                st.error("Bitte Zielordner angeben.")
            else:
                started = mgr.start(
                    plan,
                    target,
                    chapter_titles=list(selected),
                    skip_existing_ids=skip_existing,
                )
                if not started:
                    st.warning("Import läuft bereits.")
                st.rerun()
    with col_stop:
        if st.button(
            "⏹ Stop",
            disabled=not running,
            key="adobe_research_stop",
            use_container_width=True,
        ):
            mgr.request_cancel()
            st.rerun()
    with col_refresh:
        if st.button("🔄 Board aktualisieren", key="adobe_research_refresh", use_container_width=True):
            st.rerun()

    job = mgr.get_state()
    if job.status == JobStatus.RUNNING:
        st.progress(
            min(1.0, max(0.0, float(job.fraction))),
            text=(job.message or "Import läuft…")[:140],
        )
        st.info(job.message or "Import läuft…")
        if job.cancel_requested:
            st.warning(
                "Stop angefordert. Das aktuelle Asset wird noch zu Ende geladen — "
                "danach bleibt der Rest auf Open."
            )
        else:
            st.caption("Stop wirkt nach dem laufenden Asset-Download, nicht mitten in der Datei.")
        if job.log_lines:
            with st.expander("Live-Log", expanded=False):
                st.caption("\n".join(job.log_lines[-20:]))
        # Auto-Refresh für Live-Fortschritt + Stop-Button
        time.sleep(1.0)
        st.rerun()
    elif job.status == JobStatus.COMPLETED:
        st.success(job.message or "Import fertig.")
        if job.result and job.result.manifest_path:
            st.caption(f"Manifest: `{job.result.manifest_path}`")
    elif job.status == JobStatus.CANCELLED:
        st.warning(job.message or "Import gestoppt.")
        if job.result and job.result.manifest_path:
            st.caption(f"Manifest: `{job.result.manifest_path}`")
    elif job.status == JobStatus.FAILED:
        st.error(job.error or job.message or "Import fehlgeschlagen.")
