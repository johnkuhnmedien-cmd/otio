"""UI: Research-Excel hochladen → Adobe Stock lizenzieren/herunterladen."""

from __future__ import annotations

import time
from pathlib import Path

import streamlit as st

from otio_app.paths import clean_user_path_input
from otio_app.services.adobe_download_projects import (
    create_download_project,
    delete_download_project,
    get_download_project,
    list_download_projects,
    load_project_plan,
    project_dir,
    update_download_project,
)
from otio_app.services.adobe_research_import import (
    STATUS_CANCELLED,
    STATUS_DOWNLOADED,
    STATUS_DOWNLOADING,
    STATUS_ERROR,
    STATUS_OPEN,
    STATUS_UNAVAILABLE,
    AdobeResearchImportBoard,
    build_research_import_board,
    cleanup_media_folder_json,
)
from otio_app.services.adobe_research_import_job import (
    JobStatus,
    get_research_import_job_manager,
)
from otio_app.services.supplement_sources.adobe_stock import AdobeStockAdapter
from otio_app.ui.adobe_oauth_panel import render_adobe_oauth_panel

_ACTIVE_PROJECT_KEY = "adobe_download_active_project_id"


def _status_label(status: str) -> str:
    return {
        STATUS_DOWNLOADED: "Downloaded",
        STATUS_OPEN: "Open",
        STATUS_ERROR: "Fehler",
        STATUS_DOWNLOADING: "Läuft",
        STATUS_CANCELLED: "Open (gestoppt)",
        STATUS_UNAVAILABLE: "Nicht verfügbar",
    }.get(status, status)


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
        options=["Alle", "Nur Open", "Nur Downloaded", "Nur Fehler", "Nicht verfügbar"],
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
            if filter_mode == "Nicht verfügbar" and asset.status != STATUS_UNAVAILABLE:
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


def _render_project_switcher() -> str | None:
    projects = list_download_projects()
    st.subheader("Download-Projekte")
    st.caption(
        "Eigenständige Download-Aufträge (unabhängig von OTIO-Projekten). "
        "Excel + Zielordner werden gespeichert — du kannst zwischen Aufträgen wechseln."
    )

    label_to_id = {
        f"{p.name} ({p.chapter_count} Kap. / {p.asset_count} Assets)": p.id for p in projects
    }
    labels = list(label_to_id.keys())
    active_id = st.session_state.get(_ACTIVE_PROJECT_KEY)
    if active_id and active_id not in {p.id for p in projects}:
        active_id = None
        st.session_state.pop(_ACTIVE_PROJECT_KEY, None)

    col_sel, col_new = st.columns([2, 1])
    with col_sel:
        if labels:
            default_index = 0
            if active_id:
                for i, label in enumerate(labels):
                    if label_to_id[label] == active_id:
                        default_index = i
                        break
            chosen = st.selectbox(
                "Aktives Download-Projekt",
                options=labels,
                index=default_index,
                key="adobe_download_project_select",
            )
            active_id = label_to_id[chosen]
            st.session_state[_ACTIVE_PROJECT_KEY] = active_id
        else:
            st.info("Noch kein Download-Projekt — unten eines anlegen.")
            active_id = None

    with col_new:
        st.write("")
        st.write("")
        if st.button("＋ Neues anlegen", key="adobe_download_show_create", use_container_width=True):
            st.session_state["adobe_download_show_create_form"] = True

    if st.session_state.get("adobe_download_show_create_form") or not projects:
        with st.expander("Neues Download-Projekt", expanded=True):
            name = st.text_input("Name", key="adobe_dl_new_name", placeholder="Irland Research")
            target_raw = st.text_input(
                "Zielordner für Kapitel",
                key="adobe_dl_new_target",
                placeholder="/Pfad/zu/Ireland",
            )
            uploaded = st.file_uploader(
                "Research-Excel (.xlsx)",
                type=["xlsx"],
                key="adobe_dl_new_xlsx",
            )
            if st.button("Download-Projekt speichern", type="primary", key="adobe_dl_create"):
                try:
                    if not uploaded:
                        raise ValueError("Bitte Excel hochladen.")
                    target = clean_user_path_input(target_raw) if target_raw.strip() else ""
                    project = create_download_project(
                        name=name,
                        target_root=target,
                        excel_bytes=uploaded.getvalue(),
                        excel_filename=getattr(uploaded, "name", "") or "research.xlsx",
                    )
                    st.session_state[_ACTIVE_PROJECT_KEY] = project.id
                    st.session_state["adobe_download_show_create_form"] = False
                    st.success(f"Projekt „{project.name}“ gespeichert.")
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Anlegen fehlgeschlagen: {exc}")

    return active_id


def render_adobe_research_import_page() -> None:
    st.header("Adobe Stock Import (Research-Excel)")
    st.caption(
        "Vor der Projektanlage: Research-Template als Download-Projekt speichern, "
        "zwischen Aufträgen wechseln, Kapitelordner füllen (`{Kapitel}_Asset_01`)."
    )
    st.caption(
        "Videos: bevorzugt **Video_4K**; wenn die Datei über **600 MB** liegt, "
        "Fallback auf **Video_HD**. Ablauf strikt nacheinander: Pause → ein Asset "
        "lizenzieren/downloaden (`.part`) → umbenennen → Pause. Kein Parallel-Download."
    )

    render_adobe_oauth_panel(key_prefix="adobe_import_oauth")
    st.divider()

    adapter = AdobeStockAdapter()
    readiness = adapter.readiness()
    if readiness.acquire_enabled:
        st.success(readiness.message)
        # Video-Entitlement prüfen — sonst endet jeder Video-Download als Comp/cancelled.
        try:
            from otio_app.services.adobe_stock_oauth import get_adobe_access_token
            from otio_app.services.api_keys import get_api_key

            api_key = get_api_key("ADOBE_STOCK_API_KEY") or ""
            token = get_adobe_access_token() or ""
            if api_key and token:
                probe = adapter.probe_video_entitlement(api_key, token)
                ent = (probe.get("available_entitlement") or {}).get(
                    "full_entitlement_quota"
                ) or {}
                quota = (probe.get("available_entitlement") or {}).get("quota")
                if ent or quota is not None:
                    st.caption(f"API-Entitlements (Video_HD): `{ent}` · quota={quota}")
                if probe.get("lacks_video"):
                    st.warning(
                        "Die **Stock-API** sieht für dieses OAuth-Token **kein Video-Unlimited** "
                        "(nur `cct_pro_unlimited_images`, Video-quota=0) — obwohl im Browser "
                        "Video Unlimited greifen kann. Creative Cloud Pro/Plus ist für die API "
                        "oft gesperrt (Adobe: Freigabe nötig, `stockapis@adobe.com`). "
                        "Import versucht trotzdem zuerst **LicenseHistory** (bereits im Browser "
                        "lizenzierte Clips). Neue Video-Lizenzen über die API schlagen sonst "
                        "mit `cancelled`/`Comp` fehl. Prüfe auch: richtige Adobe-ID / Teamkonto?"
                    )
        except Exception as exc:  # noqa: BLE001
            st.caption(f"Entitlement-Check übersprungen: {exc}")
    elif readiness.search_enabled:
        st.warning(readiness.message)
    else:
        st.error(readiness.message)

    active_id = _render_project_switcher()
    if not active_id:
        return

    project = get_download_project(active_id)
    if project is None:
        st.error("Download-Projekt nicht gefunden.")
        st.session_state.pop(_ACTIVE_PROJECT_KEY, None)
        return

    try:
        plan = load_project_plan(project.id)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Excel des Projekts konnte nicht gelesen werden: {exc}")
        return

    mgr = get_research_import_job_manager()
    job = mgr.get_state(project.id)
    running_other = mgr.any_running()
    if running_other and running_other != project.id:
        other = get_download_project(running_other)
        st.warning(
            "Ein anderer Download läuft gerade: "
            f"**{(other.name if other else running_other)}**. "
            "Stoppe ihn oder warte, bevor du hier startest."
        )

    st.divider()
    st.markdown(f"### {project.name}")
    st.caption(
        f"Ziel: `{project.target_root}` · Excel: `{project.excel_filename}` · "
        f"{project.chapter_count} Kapitel / {project.asset_count} Assets"
    )

    with st.expander("Projekt bearbeiten", expanded=False):
        new_name = st.text_input("Name", value=project.name, key=f"adobe_dl_edit_name_{project.id}")
        new_target = st.text_input(
            "Zielordner",
            value=project.target_root,
            key=f"adobe_dl_edit_target_{project.id}",
        )
        replace_xlsx = st.file_uploader(
            "Excel ersetzen (optional)",
            type=["xlsx"],
            key=f"adobe_dl_replace_xlsx_{project.id}",
        )
        col_save, col_del = st.columns(2)
        with col_save:
            if st.button("Änderungen speichern", key=f"adobe_dl_save_{project.id}"):
                try:
                    update_download_project(
                        project.id,
                        name=new_name,
                        target_root=clean_user_path_input(new_target) if new_target.strip() else project.target_root,
                        excel_bytes=replace_xlsx.getvalue() if replace_xlsx else None,
                        excel_filename=(
                            getattr(replace_xlsx, "name", None) if replace_xlsx else None
                        ),
                    )
                    st.success("Gespeichert.")
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    st.error(str(exc))
        with col_del:
            if st.button("Projekt löschen", key=f"adobe_dl_delete_{project.id}"):
                if mgr.is_running(project.id):
                    st.error("Laufenden Download zuerst stoppen.")
                else:
                    delete_download_project(project.id)
                    st.session_state.pop(_ACTIVE_PROJECT_KEY, None)
                    st.success("Download-Projekt gelöscht (Medien im Zielordner bleiben).")
                    st.rerun()

    state_dir = project_dir(project.id)
    board = build_research_import_board(
        plan,
        project.target_root,
        state_dir=state_dir,
        live_statuses=job.live_statuses if job.live_statuses else None,
    )
    _render_board(board)
    st.caption(f"Fortschritt gespeichert unter: `{state_dir}` (nicht im Medienordner)")

    with st.expander("JSON aus Medienordnern entfernen", expanded=False):
        st.caption(
            "Löscht `*.adobe.json`, `adobe_research_import_board.json` und "
            "`adobe_research_import_manifest.json` im Zielordner. "
            "Fortschritt bleibt im Download-Projekt unter data/."
        )
        if st.button("JSON jetzt löschen", key=f"adobe_dl_cleanup_json_{project.id}"):
            removed = cleanup_media_folder_json(project.target_root)
            st.success(
                f"Entfernt: {removed['sidecar']} Sidecars · "
                f"{removed['board']} Board · {removed['manifest']} Manifest"
            )
            st.rerun()

    st.subheader("Import steuern")
    chapter_labels = [ch.title for ch in plan.chapters]
    default_selected = project.selected_chapters or chapter_labels
    default_selected = [c for c in default_selected if c in chapter_labels] or chapter_labels
    selected = st.multiselect(
        "Kapitel zum Import",
        options=chapter_labels,
        default=default_selected,
        key=f"adobe_research_chapters_{project.id}",
    )
    skip_existing = st.checkbox(
        "Bereits heruntergeladene Asset-IDs überspringen",
        value=project.skip_existing_ids,
        key=f"adobe_research_skip_existing_{project.id}",
    )

    running = job.status == JobStatus.RUNNING
    can_start = (
        bool(project.target_root)
        and bool(selected)
        and readiness.acquire_enabled
        and not running
        and (running_other is None or running_other == project.id)
    )

    col_start, col_stop, col_refresh = st.columns(3)
    with col_start:
        if st.button(
            "▶ Lizenzieren & herunterladen",
            type="primary",
            disabled=not can_start,
            key=f"adobe_research_run_{project.id}",
            use_container_width=True,
        ):
            update_download_project(
                project.id,
                selected_chapters=list(selected),
                skip_existing_ids=skip_existing,
            )
            started = mgr.start(
                project.id,
                plan,
                project.target_root,
                chapter_titles=list(selected),
                skip_existing_ids=skip_existing,
            )
            if not started:
                st.warning("Import läuft bereits (dieses oder ein anderes Projekt).")
            st.rerun()
    with col_stop:
        if st.button(
            "⏹ Stop",
            disabled=not running,
            key=f"adobe_research_stop_{project.id}",
            use_container_width=True,
        ):
            mgr.request_cancel(project.id)
            st.rerun()
    with col_refresh:
        if st.button(
            "🔄 Board aktualisieren",
            key=f"adobe_research_refresh_{project.id}",
            use_container_width=True,
        ):
            st.rerun()

    job = mgr.get_state(project.id)
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
            st.caption("Stop wirkt nach dem laufenden Asset-Download.")
        if job.log_lines:
            with st.expander("Live-Log", expanded=False):
                st.caption("\n".join(job.log_lines[-20:]))
        time.sleep(1.0)
        st.rerun()
    elif job.status == JobStatus.COMPLETED:
        if job.result and job.result.errors:
            st.warning(job.message or "Import fertig (mit Fehlern).")
        else:
            st.success(job.message or "Import fertig.")
        if job.result and job.result.manifest_path:
            st.caption(f"Manifest: `{job.result.manifest_path}`")
        if job.result and (job.result.errors or job.result.unavailable):
            st.subheader("Fehler / Nicht verfügbar")
            st.caption(
                "Typische Ursachen laut Adobe-Antwort: "
                "`Content is no longer available` (Asset entfernt), "
                "`state=cancelled` + nur `cct_pro_unlimited_images` (Videos nicht im "
                "aktuellen API-Entitlement — OAuth mit dem Unlimited-/Video-Konto), "
                "Fotos brauchen `Standard`, Videos `Video_4K`/`Video_HD`. "
                "Bereits Lizenzierte werden über Content/Info + LicenseHistory geladen."
            )
            err_rows = [
                {
                    "Kapitel": item.chapter_title,
                    "Asset ID": item.asset_id,
                    "Status": _status_label(item.status),
                    "Fehler": item.message,
                }
                for item in job.result.items
                if item.status in {STATUS_ERROR, STATUS_UNAVAILABLE}
            ]
            st.dataframe(err_rows, use_container_width=True, hide_index=True)
    elif job.status == JobStatus.CANCELLED:
        st.warning(job.message or "Import gestoppt.")
        if job.result and job.result.manifest_path:
            st.caption(f"Manifest: `{job.result.manifest_path}`")
    elif job.status == JobStatus.FAILED:
        st.error(job.error or job.message or "Import fehlgeschlagen.")
