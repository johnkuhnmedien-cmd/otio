"""Streamlit-UI: Projekt analysieren (Voice-over + Assets)."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from otio_app.config import get_voice_backend_from_env
from otio_app.defaults import (
    GEMINI_MODEL_CHOICES,
    VOICE_BACKEND_CHOICES,
    VOICE_BACKEND_GEMINI,
    VOICE_BACKEND_LABELS,
    VOICE_BACKEND_WHISPER,
    WHISPER_MODEL_CHOICES,
    WHISPER_MODEL_LABELS,
)
from otio_app.models import ProjectStatus
from otio_app.project_repository import (
    update_project_selection,
    update_project_status,
)
from otio_app.services.asset_analysis_job import get_asset_analysis_job_manager
from otio_app.services.voice_analysis_job import get_voice_analysis_job_manager
from otio_app.services.gemini_client import (
    format_gemini_model_label,
    get_default_gemini_model,
    is_gemini_configured,
)
from otio_app.services.inventory_loader import (
    probe_folder_inventory_statuses,
    sync_folder_inventories_from_cache,
)
from otio_app.services.media_inventory_cache import (
    discover_folder_media_paths,
    list_assets_missing_successful_cache,
)
from otio_app.services.whisper_transcriber import (
    get_default_whisper_model,
    is_whisper_available,
)
from otio_app.services.folder_asset_status import (
    AssetAnalysisState,
    list_missing_or_failed_assets,
)
from otio_app.services.folder_analysis_status import (
    FolderAnalysisState,
    count_folder_states,
    format_folder_with_status,
    get_folder_analysis_state,
)
from otio_app.services.manual_folder_completion import (
    is_manually_complete,
    list_manually_complete_folders,
    set_manually_complete,
    set_manually_complete_many,
)
from otio_app.ui.analysis_jobs_ui import render_analysis_jobs_monitor
from otio_app.ui.project_context import (
    get_workflow_status,
    render_file_paths,
    render_output_status,
    render_project_selector,
    render_workflow_progress,
)
from otio_app.ui.voiceover_generation._shared import (
    LLM_INPUT_INFO,
    render_llm_input_info,
)


def _folder_status_cache_keys(project_id: str) -> tuple[str, str]:
    return (
        f"wb_folder_status_cache_{project_id}",
        f"wb_folder_status_fp_{project_id}",
    )


def _invalidate_folder_status_cache(project_id: str) -> None:
    cache_key, fp_key = _folder_status_cache_keys(project_id)
    st.session_state.pop(cache_key, None)
    st.session_state.pop(fp_key, None)


def _folder_status_fingerprint(project) -> str:
    asset_job = get_asset_analysis_job_manager().get_state(project.id)
    if asset_job is None:
        asset_fp = "none"
    else:
        asset_fp = (
            f"{asset_job.status.value}:{asset_job.done_media}:"
            f"{asset_job.phase}:{asset_job.phase_data.get('folder', '')}"
        )
    return (
        f"{asset_fp}|{len(project.asset_subdir_names)}|"
        f"{project.inventory_dir.is_dir()}"
    )


def _get_folder_status_cache(project) -> dict[str, FolderAnalysisState]:
    """Session-Cache für Ordner-Status — vermeidet Media/Cache-Scans bei jedem Klick."""
    cache_key, fp_key = _folder_status_cache_keys(project.id)
    fingerprint = _folder_status_fingerprint(project)
    cached = st.session_state.get(cache_key)
    if cached is not None and st.session_state.get(fp_key) == fingerprint:
        # Neue Ordner nachziehen, ohne alles neu zu scannen.
        missing = [name for name in project.asset_subdir_names if name not in cached]
        if not missing:
            return cached
        for name in missing:
            cached[name] = get_folder_analysis_state(project, name)
        st.session_state[cache_key] = cached
        return cached

    states = {
        name: get_folder_analysis_state(project, name)
        for name in project.asset_subdir_names
    }
    st.session_state[cache_key] = states
    st.session_state[fp_key] = fingerprint
    return states


def _start_voice_analysis_background(
    project,
    *,
    backend: str,
    whisper_model: str,
    gemini_model: str,
    chain_asset_folders: list[str] | None = None,
    chain_asset_model: str = "",
) -> bool:
    manager = get_voice_analysis_job_manager()
    if manager.is_running(project.id):
        st.warning("Voice-over-Analyse läuft bereits — bitte warten oder stoppen.")
        return False
    if not manager.start(
        project,
        backend=backend,
        whisper_model=whisper_model,
        gemini_model=gemini_model,
        chain_asset_folders=chain_asset_folders,
        chain_asset_model=chain_asset_model,
    ):
        st.warning("Voice-over-Analyse konnte nicht gestartet werden.")
        return False
    update_project_status(project.id, ProjectStatus.ANALYZING)
    return True


def _start_asset_analysis_background(project, folders: list[str], model: str) -> bool:
    """Startet Asset-Analyse im Hintergrund — UI bleibt bedienbar."""
    manager = get_asset_analysis_job_manager()
    if manager.is_running(project.id):
        st.warning("Asset-Analyse läuft bereits — bitte warten oder stoppen.")
        return False
    if not manager.start(project, folders, model):
        st.warning("Asset-Analyse konnte nicht gestartet werden.")
        return False
    update_project_status(project.id, ProjectStatus.ANALYZING)
    return True


def _render_folder_picker(project) -> list[str]:
    """Multiselect und Schnellauswahl — oben in der Analyse-Ansicht."""
    folder_state_key = f"workbench_folders_{project.id}"
    pending_key = f"workbench_folders_pending_{project.id}"
    # Apply button-driven selection before the multiselect is instantiated.
    # Streamlit forbids writing a widget key after that widget exists in the run.
    if pending_key in st.session_state:
        st.session_state[folder_state_key] = st.session_state.pop(pending_key)
    elif folder_state_key not in st.session_state:
        st.session_state[folder_state_key] = list(project.selected_asset_subdirs)

    status_cache = _get_folder_status_cache(project)
    label_cache = {
        name: format_folder_with_status(project, name, state=state)
        for name, state in status_cache.items()
    }

    selected_folders = st.multiselect(
        "Zu bearbeitende Asset-Ordner",
        options=project.asset_subdir_names,
        format_func=lambda name: label_cache.get(name, name),
        key=folder_state_key,
    )
    st.caption(
        f"{len(selected_folders)} von {len(project.asset_subdir_names)} Ordnern ausgewählt"
    )

    btn_col1, btn_col2, btn_col3, btn_col4 = st.columns(4)
    with btn_col1:
        if st.button("Alle Ordner auswählen", key=f"all_{project.id}"):
            st.session_state[pending_key] = list(project.asset_subdir_names)
            st.rerun()
    with btn_col2:
        if st.button("Nur offene Ordner", key=f"open_{project.id}"):
            open_names = [
                name
                for name in project.asset_subdir_names
                if status_cache.get(name)
                in {FolderAnalysisState.PENDING, FolderAnalysisState.PARTIAL}
            ]
            st.session_state[pending_key] = open_names
            st.rerun()
    with btn_col3:
        if st.button("Gespeicherte Auswahl", key=f"reload_{project.id}"):
            st.session_state[pending_key] = list(project.selected_asset_subdirs)
            st.rerun()
    with btn_col4:
        if st.button("Auswahl speichern", key=f"save_sel_{project.id}"):
            update_project_selection(project.id, selected_folders)
            st.success("Ordnerauswahl gespeichert.")
            st.rerun()

    return selected_folders


def _render_folder_status_overview(project) -> None:
    """Statusübersicht aller Asset-Ordner (Tab „Ordner“)."""
    status_cache = _get_folder_status_cache(project)
    counts = count_folder_states(
        project, project.asset_subdir_names, states=status_cache
    )
    st.caption(
        f"🟢 {counts[FolderAnalysisState.COMPLETE]} fertig · "
        f"🟡 {counts[FolderAnalysisState.PARTIAL]} teilweise · "
        f"⚪ {counts[FolderAnalysisState.PENDING]} offen · "
        f"➖ {counts[FolderAnalysisState.EMPTY]} leer"
    )
    incomplete_names = [
        name
        for name in project.asset_subdir_names
        if status_cache.get(name, get_folder_analysis_state(project, name))
        in {FolderAnalysisState.PARTIAL, FolderAnalysisState.PENDING}
    ]
    manual_names = [
        name
        for name in list_manually_complete_folders(project)
        if name in set(project.asset_subdir_names)
    ]
    col_refresh, col_mark_all, col_clear_all = st.columns(3)
    with col_refresh:
        if st.button(
            "Status aktualisieren",
            key=f"refresh_folder_status_{project.id}",
            use_container_width=True,
        ):
            _invalidate_folder_status_cache(project.id)
            st.rerun()
    with col_mark_all:
        if st.button(
            f"Alle unfertigen manuell fertig ({len(incomplete_names)})",
            key=f"manual_complete_all_{project.id}",
            use_container_width=True,
            disabled=not incomplete_names,
            type="primary",
            help=(
                "Markiert alle teilweise/offenen Ordner auf einmal als manuell fertig "
                "und baut Inventory aus dem Cache (ohne Analyse nachzuholen)."
            ),
        ):
            changed = set_manually_complete_many(
                project, incomplete_names, complete=True
            )
            _invalidate_folder_status_cache(project.id)
            if changed:
                st.success(f"{len(changed)} Ordner manuell als fertig markiert.")
            else:
                st.info("Keine Ordner geändert.")
            st.rerun()
    with col_clear_all:
        if st.button(
            f"Alle manuellen Markierungen aufheben ({len(manual_names)})",
            key=f"manual_clear_all_{project.id}",
            use_container_width=True,
            disabled=not manual_names,
            help=(
                "Hebt für alle Kapitel die manuelle Fertig-Markierung auf einmal auf. "
                "Automatisch voll analysierte Ordner bleiben grün."
            ),
        ):
            changed = set_manually_complete_many(
                project, manual_names, complete=False
            )
            _invalidate_folder_status_cache(project.id)
            if changed:
                st.success(
                    f"{len(changed)} manuelle Markierung(en) aufgehoben."
                )
            else:
                st.info("Keine manuellen Markierungen vorhanden.")
            st.rerun()

    for folder_name in project.asset_subdir_names:
        state = status_cache.get(
            folder_name, get_folder_analysis_state(project, folder_name)
        )
        label = format_folder_with_status(project, folder_name, state=state)
        col_status, col_action = st.columns([5, 1])
        with col_status:
            if state == FolderAnalysisState.COMPLETE:
                st.success(label)
            elif state == FolderAnalysisState.PARTIAL:
                st.warning(label)
                # Checkbox statt Expander: Inhalt nur bei aktivem Haken ausführen.
                if st.checkbox(
                    f"Details · {folder_name}",
                    key=f"gaps_{project.id}_{folder_name}",
                    value=False,
                ):
                    gaps = list_missing_or_failed_assets(project, folder_name)
                    if not gaps:
                        st.caption("Keine offenen Assets.")
                    for gap in gaps:
                        if gap.state == AssetAnalysisState.MISSING:
                            st.caption(f"⚪ `{gap.path.name}` — noch nicht analysiert")
                        else:
                            st.caption(
                                f"❌ `{gap.path.name}` — {gap.error or 'Fehler ohne Details'}"
                            )
            elif state == FolderAnalysisState.EMPTY:
                st.caption(label)
            else:
                st.info(label)
        with col_action:
            if state == FolderAnalysisState.COMPLETE and not is_manually_complete(
                project, folder_name
            ):
                st.caption("✓")
            elif is_manually_complete(project, folder_name):
                if st.button(
                    "↩",
                    key=f"unmanual_{project.id}_{folder_name}",
                    help="Manuelle Markierung aufheben",
                ):
                    set_manually_complete(project, folder_name, complete=False)
                    _invalidate_folder_status_cache(project.id)
                    st.rerun()
            elif state in {FolderAnalysisState.PARTIAL, FolderAnalysisState.PENDING}:
                if st.button(
                    "✓",
                    key=f"manual_{project.id}_{folder_name}",
                    help="Manuell als fertig markieren",
                ):
                    set_manually_complete(project, folder_name, complete=True)
                    _invalidate_folder_status_cache(project.id)
                    st.rerun()

    st.caption(
        "Rechts: ✓ = manuell als fertig markieren · ↩ = Markierung aufheben · "
        "Oben: alle unfertigen fertig markieren bzw. alle manuellen Markierungen aufheben."
    )


def _init_model_settings() -> tuple[str, str, str]:
    default_voice_backend = get_voice_backend_from_env()
    if "voice_backend" not in st.session_state:
        st.session_state["voice_backend"] = default_voice_backend
    if st.session_state["voice_backend"] not in VOICE_BACKEND_CHOICES:
        st.session_state["voice_backend"] = default_voice_backend

    default_whisper_model = get_default_whisper_model()
    if "whisper_model" not in st.session_state:
        st.session_state["whisper_model"] = default_whisper_model
    if st.session_state["whisper_model"] not in WHISPER_MODEL_CHOICES:
        st.session_state["whisper_model"] = default_whisper_model

    default_model = get_default_gemini_model()
    if "gemini_model" not in st.session_state:
        st.session_state["gemini_model"] = default_model
    if st.session_state["gemini_model"] not in GEMINI_MODEL_CHOICES:
        st.session_state["gemini_model"] = default_model

    return (
        st.session_state["voice_backend"],
        st.session_state["whisper_model"],
        st.session_state["gemini_model"],
    )


def _render_analysis_actions(
    project,
    selected_folders: list[str],
    selected_voice_backend: str,
    selected_whisper_model: str,
    selected_model: str,
) -> None:
    folder_state_key = f"workbench_folders_{project.id}"
    asset_job_running = get_asset_analysis_job_manager().is_running(project.id)
    voice_job_running = get_voice_analysis_job_manager().is_running(project.id)
    any_job_running = asset_job_running or voice_job_running
    without_voiceover = bool(
        getattr(project, "is_without_voiceover_pipeline", False)
        or getattr(project, "is_without_voiceover", False)
    )

    if not without_voiceover:
        st.markdown("**Voice-over** — lokal mit Whisper (Standard) oder optional Gemini.")
        if voice_job_running:
            st.caption("Voice-over-Analyse läuft im Hintergrund — Fortschritt siehe oben.")
        if st.button(
            "🎙️ Voice-over analysieren",
            key=f"voice_{project.id}",
            type="primary",
            disabled=any_job_running,
        ):
            if selected_voice_backend == VOICE_BACKEND_GEMINI and not is_gemini_configured():
                st.error("GEMINI_API_KEY fehlt — unter **🔑 API-Schlüssel** oder in `.env`.")
            elif selected_voice_backend == VOICE_BACKEND_WHISPER and not is_whisper_available():
                st.error("Whisper nicht installiert — `pip install -r requirements.txt`.")
            else:
                if _start_voice_analysis_background(
                    project,
                    backend=selected_voice_backend,
                    whisper_model=selected_whisper_model,
                    gemini_model=selected_model,
                ):
                    st.rerun()

        st.divider()

    st.markdown("**Asset-Ordner** — Gemini analysiert nur Frame-Bilder (kostenpflichtig).")
    if without_voiceover:
        st.caption(
            "Ohne Voice-Over: Hauptassets hier analysieren. Fehlende Cut-Plan-/"
            "`_supplemental/`-Supplements über den Button darunter ins Inventory holen."
        )
    if asset_job_running:
        st.caption(
            "Asset-Analyse läuft im Hintergrund — Fortschritt siehe oben. "
            "Du kannst zu **③ Schnittplan** wechseln."
        )
    if selected_folders and st.checkbox(
        "Fehlende Analysen je Ordner anzeigen",
        key=f"show_missing_assets_{project.id}",
        value=False,
    ):
        from otio_app.services.supplement_inventory import (
            count_supplements_needing_analysis,
        )

        for folder_name in selected_folders:
            missing = list_assets_missing_successful_cache(project, folder_name)
            total = len(discover_folder_media_paths(project, folder_name))
            open_supplements = count_supplements_needing_analysis(
                project, [folder_name]
            )
            # Beschaffte Assets laufen im selben Lauf mit — sie gehören in die
            # Vorschau, sonst überrascht die Kostenschätzung.
            supplement_note = (
                f" · zusätzlich {open_supplements} beschaffte(s) Asset(s)"
                if open_supplements
                else ""
            )
            if missing:
                labels = ", ".join(f"`{path.name}`" for path in missing[:8])
                suffix = " …" if len(missing) > 8 else ""
                st.warning(
                    f"**{folder_name}:** {len(missing)} von {total} Assets ohne Analyse-JSON "
                    f"({labels}{suffix}){supplement_note}"
                )
            elif open_supplements:
                st.warning(
                    f"**{folder_name}:** alle {total} Originale analysiert,"
                    f"{supplement_note}"
                )
            else:
                st.caption(f"**{folder_name}:** alle Assets analysiert ({total})")
    if st.button(
        "📁 Ausgewählte Ordner analysieren",
        key=f"assets_{project.id}",
        disabled=any_job_running,
    ):
        if not selected_folders:
            st.warning("Bitte mindestens einen Ordner unter „Ordner“ auswählen.")
        elif not is_gemini_configured():
            st.error("GEMINI_API_KEY fehlt — unter **🔑 API-Schlüssel** oder in `.env`.")
        else:
            folders = list(selected_folders)
            update_project_selection(project.id, folders)
            if _start_asset_analysis_background(project, folders, selected_model):
                st.rerun()

    from otio_app.services.cut_plan_inventory_bridge import (
        analyze_and_import_missing_supplement_assets,
        list_supplement_assets_missing_from_inventory,
    )

    missing_supplements = list_supplement_assets_missing_from_inventory(project)
    if selected_folders:
        missing_supplements = [
            entry
            for entry in missing_supplements
            if entry["folder_name"] in set(selected_folders)
        ]
    st.divider()
    st.markdown("**Supplement-Assets** — noch nicht im Inventory.")
    if missing_supplements:
        st.info(
            f"{len(missing_supplements)} Supplement-Asset(s) fehlen im Inventory "
            "(Cut-Plan und/oder `_supplemental/`)."
        )
        preview = ", ".join(
            f"`{Path(entry['asset_path']).name}`" for entry in missing_supplements[:8]
        )
        suffix = " …" if len(missing_supplements) > 8 else ""
        st.caption(preview + suffix)
    else:
        st.caption("Keine fehlenden Supplement-Assets für die aktuelle Ordnerauswahl.")
    if st.button(
        "🧩 Fehlende Supplement-Assets analysieren & ins Inventory",
        key=f"analyze_missing_supplements_{project.id}",
        disabled=any_job_running or not missing_supplements,
        help=(
            "Analysiert alle Supplement-Dateien, die noch nicht im Folder-Inventory "
            "stehen, und übernimmt sie. Vorhandene LLM-Validierung wird wiederverwendet."
        ),
    ):
        if not missing_supplements:
            st.warning("Keine fehlenden Supplements.")
        else:
            with st.spinner("Analysiere fehlende Supplement-Assets …"):
                report = analyze_and_import_missing_supplement_assets(
                    project,
                    folder_names=list(selected_folders) if selected_folders else None,
                    gemini_model=selected_model,
                )
            if report.imported:
                details = ", ".join(
                    f"{folder}: {count}"
                    for folder, count in sorted(report.imported_by_folder.items())
                )
                st.success(f"{report.imported} Supplement(s) analysiert und übernommen ({details}).")
            else:
                st.warning("Keine Supplements übernommen.")
            for skip in report.skipped[:20]:
                st.caption(f"⚠️ {skip}")
            st.rerun()

    _render_supplement_analysis_status(
        project,
        selected_folders=selected_folders,
        selected_model=selected_model,
        any_job_running=any_job_running,
    )

    if without_voiceover:
        return

    st.divider()
    if st.button(
        "⚡ Voice-over + alle Ordner",
        key=f"all_run_{project.id}",
        disabled=any_job_running,
    ):
        if not is_gemini_configured():
            st.error("GEMINI_API_KEY fehlt — unter **🔑 API-Schlüssel** oder in `.env`.")
        elif (
            selected_voice_backend == VOICE_BACKEND_WHISPER
            and not is_whisper_available()
        ):
            st.error("Whisper nicht installiert.")
        else:
            all_folders = list(project.asset_subdir_names)
            st.session_state[folder_state_key] = all_folders
            update_project_selection(project.id, all_folders)
            if _start_voice_analysis_background(
                project,
                backend=selected_voice_backend,
                whisper_model=selected_whisper_model,
                gemini_model=selected_model,
                chain_asset_folders=all_folders,
                chain_asset_model=selected_model,
            ):
                st.rerun()


def _render_supplement_recovery(
    project,
    *,
    selected_folders: list[str],
    selected_model: str | None,
    any_job_running: bool,
) -> None:
    """Bestandsaufnahme für Projekte aus der Zeit vor dem gemeinsamen Eingangstor.

    Damals entfernte ein Ordner-Sync die Inventarzeilen beschaffter Assets. Die
    Dateien liegen noch da — Acceptance-Listen aller Sprachen, Clean-Manifeste
    und Stock-Downloads sind die Quellen, aus denen sie zurückkommen.
    """
    from otio_app.services.supplement_recovery import (
        recover_supplements_into_inventory,
        scan_recoverable_supplements,
    )

    scan_key = f"supplement_recovery_scan_{project.id}"
    with st.expander("Bestand beschaffter Assets prüfen", expanded=False):
        st.caption(
            "Sucht bereits beschaffte Assets, die nicht im geteilten Inventar "
            "stehen — etwa weil ein früherer Ordner-Sync sie entfernt hat. "
            "Liest die Acceptance-Listen aller Sprachen dieses Medienordners."
        )
        if st.button(
            "🔍 Bestand prüfen (ohne Änderung)",
            key=f"scan_recovery_{project.id}",
            disabled=any_job_running,
        ):
            with st.spinner("Prüfe Bestand …"):
                st.session_state[scan_key] = scan_recoverable_supplements(
                    project, folder_names=selected_folders or None
                )

        scanned = st.session_state.get(scan_key)
        if scanned is None:
            return

        items, report = scanned
        missing = [item for item in items if not item.in_inventory]
        if not items and not report.unresolved:
            st.success("Kein beschafftes Asset gefunden, das im Inventar fehlt.")
            return

        if missing:
            st.warning(
                f"{len(missing)} beschaffte(s) Asset(s) fehlen im Inventar. "
                "Nachtragen analysiert sie wie Originale."
            )
            for item in missing[:15]:
                st.caption(
                    f"`{item.media_path.name}` → **{item.folder_name}** "
                    f"(Quelle: {item.source})"
                )
            if len(missing) > 15:
                st.caption(f"… und {len(missing) - 15} weitere")
        else:
            st.success(
                f"Alle {len(items)} gefundenen beschafften Assets stehen im Inventar."
            )

        for note in report.unresolved[:10]:
            st.caption(f"⚠️ {note}")
        if report.unresolved:
            st.caption(
                "Nicht zuordenbare Dateien werden bewusst nicht geraten — sie "
                "lassen sich im Schnittplan-Tab manuell einem Gap zuweisen."
            )

        if st.button(
            "📥 Fehlende Assets nachtragen & analysieren",
            key=f"run_recovery_{project.id}",
            disabled=any_job_running or not missing,
        ):
            if not is_gemini_configured():
                st.error(
                    "GEMINI_API_KEY fehlt — unter **🔑 API-Schlüssel** oder in `.env`."
                )
            else:
                with st.spinner("Trage beschaffte Assets nach …"):
                    result = recover_supplements_into_inventory(
                        project,
                        folder_names=selected_folders or None,
                        model=selected_model,
                    )
                if result.recovered:
                    details = ", ".join(
                        f"{folder}: {count}"
                        for folder, count in sorted(result.recovered_by_folder.items())
                    )
                    st.success(
                        f"{result.recovered} Asset(s) nachgetragen "
                        f"({result.analyzed} neu analysiert) — {details}."
                    )
                for failure in result.failures[:20]:
                    st.caption(f"⚠️ {failure}")
                st.session_state.pop(scan_key, None)
                st.rerun()


def _render_supplement_analysis_status(
    project,
    *,
    selected_folders: list[str],
    selected_model: str | None,
    any_job_running: bool,
) -> None:
    """Beschaffte Assets im Inventar, denen die reguläre Analyse fehlt.

    Betrifft Material aus Supplement-Funnel und Coverage-Gap-Inbox, das vor der
    Vereinheitlichung importiert wurde oder ohne API-Schlüssel ankam. Erst mit
    der Analyse trägt es dieselben Parameter wie ein Original — und ist damit
    für ein zweites Sprachprojekt im selben Ordner voll nutzbar.
    """
    from otio_app.services.supplement_inventory import (
        analyze_supplements_for_folder,
        list_supplement_assets,
    )

    folders = list(selected_folders or project.asset_subdir_names)
    if not folders:
        return

    open_by_folder: dict[str, list] = {}
    total = 0
    for folder_name in folders:
        statuses = list_supplement_assets(project, folder_name, model=selected_model)
        total += len(statuses)
        open_items = [status for status in statuses if status.needs_analysis]
        if open_items:
            open_by_folder[folder_name] = open_items

    st.divider()
    st.markdown("**Beschaffte Assets** — Analyse wie bei Originalen.")
    _render_supplement_recovery(
        project,
        selected_folders=folders,
        selected_model=selected_model,
        any_job_running=any_job_running,
    )
    if not total:
        st.caption("Keine beschafften Assets im Inventar dieser Ordnerauswahl.")
        return

    open_count = sum(len(items) for items in open_by_folder.values())
    if not open_count:
        st.caption(
            f"{total} beschaffte(s) Asset(s) vollständig analysiert — im geteilten "
            "Inventar für jede Sprache dieses Medienordners nutzbar."
        )
        return

    st.warning(
        f"{open_count} von {total} beschafften Assets ohne aktuelle Analyse. "
        "Ohne sie fehlen Dauer, Tags, Motion, Framing und Qualitätsprofil — "
        "der Cut-LLM wählt sie dann kaum aus."
    )
    for folder_name, items in open_by_folder.items():
        names = ", ".join(f"`{status.media_path.name}`" for status in items[:8])
        suffix = " …" if len(items) > 8 else ""
        reasons = sorted({status.reason for status in items if status.reason})
        reason_text = f" — {', '.join(reasons)}" if reasons else ""
        st.caption(f"**{folder_name}:** {names}{suffix}{reason_text}")

    if st.button(
        "🧠 Beschaffte Assets regulär analysieren",
        key=f"analyze_supplements_{project.id}",
        disabled=any_job_running,
        help=(
            "Nutzt denselben Prompt wie die Erstanalyse. Herkunft, Lizenz und "
            "Beschaffungsbegründung bleiben erhalten."
        ),
    ):
        if not is_gemini_configured():
            st.error("GEMINI_API_KEY fehlt — unter **🔑 API-Schlüssel** oder in `.env`.")
        else:
            analyzed = 0
            failures: list[str] = []
            with st.spinner("Analysiere beschaffte Assets …"):
                for folder_name in open_by_folder:
                    report = analyze_supplements_for_folder(
                        project, folder_name, model=selected_model
                    )
                    analyzed += report.analyzed + report.cached
                    failures.extend(report.failures)
            if analyzed:
                st.success(f"{analyzed} beschaffte(s) Asset(s) analysiert.")
            for failure in failures[:20]:
                st.caption(f"⚠️ {failure}")
            st.rerun()


def render_project_workbench() -> None:
    st.header("① Analysen")

    project = render_project_selector()
    if project is None:
        return

    without_voiceover = bool(
        getattr(project, "is_without_voiceover_pipeline", False)
        or getattr(project, "is_without_voiceover", False)
    )

    render_workflow_progress(project, current_step="analysis", lightweight=True)
    if not get_workflow_status(project, lightweight=True).clean_media_done:
        st.warning(
            "**Clean Media noch nicht abgeschlossen** — unter **⓪ Clean Media** Medien "
            "prüfen und ggf. transcodieren, bevor du analysierst."
        )
    render_analysis_jobs_monitor(project)
    diag_key = f"inv_sync_diag_{project.id}"
    with st.expander(
        "Inventar-Sync (Diagnose)",
        expanded=not project.inventory_dir.is_dir(),
    ):
        st.caption(f"Zielordner: `{project.inventory_dir}`")
        st.caption(f"Cache: `{project.work_dir_path / 'cache' / 'inventory'}`")
        if project.inventory_path.is_file():
            st.caption(f"Legacy: `{project.inventory_path}` wird beim Sync aufgeteilt.")
        st.caption(
            "Inventar-Sync läuft nicht mehr bei jedem Klick — nur auf Button-Druck."
        )
        sync_statuses = st.session_state.get(diag_key)
        if sync_statuses is None:
            st.write(
                "Noch nicht geprüft. „Status prüfen“ oder „aus Cache aufbauen“ klicken."
            )
        elif not sync_statuses:
            st.write("Noch keine Asset-Ordner gescannt.")
        else:
            for status in sync_statuses:
                label = (
                    f"**{status.folder}** — {status.detail} "
                    f"({status.cache_files} Cache / {status.media_files} Medien)"
                )
                if status.state == "created":
                    st.success(label)
                elif status.state == "exists":
                    st.info(label)
                else:
                    st.warning(label)
        btn_probe, btn_sync = st.columns(2)
        with btn_probe:
            if st.button("Status prüfen", key=f"probe_inv_{project.id}"):
                st.session_state[diag_key] = probe_folder_inventory_statuses(project)
                st.rerun()
        with btn_sync:
            if st.button(
                "Inventar jetzt aus Cache aufbauen",
                key=f"sync_inv_{project.id}",
            ):
                created, refreshed = sync_folder_inventories_from_cache(project)
                st.session_state[diag_key] = refreshed
                _invalidate_folder_status_cache(project.id)
                if created:
                    st.success("Erstellt: " + ", ".join(created))
                else:
                    st.warning(
                        "Keine neuen Ordner-Inventare erstellt — siehe Status oben."
                    )
                st.rerun()
    render_output_status(project)
    st.caption(
        f"Status: {project.status.value} · "
        f"{len(project.selected_asset_subdirs)} Ordner gespeichert"
    )

    selected_folders = _render_folder_picker(project)
    st.divider()

    tab_folders, tab_run, tab_results = st.tabs(
        ["📁 Ordner", "▶️ Analysen starten", "📄 Ergebnisse"]
    )

    with tab_folders:
        st.markdown("Status aller Asset-Ordner")
        _render_folder_status_overview(project)

    with tab_run:
        folder_state_key = f"workbench_folders_{project.id}"
        selected_folders = st.session_state.get(
            folder_state_key,
            list(project.selected_asset_subdirs),
        )

        with st.expander("⚙️ Einstellungen (Modelle & API)", expanded=False):
            if not is_gemini_configured():
                st.warning("GEMINI_API_KEY fehlt — unter **🔑 API-Schlüssel** eintragen.")
            if not without_voiceover and not is_whisper_available():
                st.caption("Whisper fehlt — nur Voice-over via Gemini möglich.")

            if not without_voiceover:
                selected_voice_backend = st.selectbox(
                    "Voice-over-Engine",
                    options=list(VOICE_BACKEND_CHOICES),
                    format_func=lambda value: VOICE_BACKEND_LABELS[value],
                    key="voice_backend",
                )
                st.selectbox(
                    "Whisper-Modell",
                    options=list(WHISPER_MODEL_CHOICES),
                    format_func=lambda value: WHISPER_MODEL_LABELS[value],
                    key="whisper_model",
                    disabled=selected_voice_backend != VOICE_BACKEND_WHISPER,
                )
            st.selectbox(
                "Gemini-Modell",
                options=list(GEMINI_MODEL_CHOICES),
                format_func=format_gemini_model_label,
                key="gemini_model",
            )
            render_llm_input_info(
                LLM_INPUT_INFO["analysis_assets"],
                title="Assets (Gemini)",
            )
            if not without_voiceover:
                render_llm_input_info(
                    LLM_INPUT_INFO["analysis_voice_gemini"],
                    title="Voice-over (nur Gemini-Engine)",
                )
            st.caption("API-Schlüssel wechseln: **🔑 API-Schlüssel**")

        voice_backend, whisper_model, gemini_model = _init_model_settings()

        _render_analysis_actions(
            project,
            selected_folders,
            voice_backend,
            whisper_model,
            gemini_model,
        )

        st.caption(f"Aktuell {len(selected_folders)} Ordner für Asset-Analyse ausgewählt.")

    with tab_results:
        if getattr(project, "is_without_voiceover_pipeline", project.is_without_voiceover):
            st.caption(
                "Ohne Voice-Over: keine Voice-over-Analyse. "
                "Fehlende Supplements analysierst du unter **▶️ Analysen starten**."
            )
        elif project.voice_analysis_path.is_file():
            st.markdown("**voice_over_analysis.json**")
            st.code(project.voice_analysis_path.read_text(encoding="utf-8")[:4000])
        else:
            st.caption("Voice-over-Analyse noch nicht erstellt.")
        if project.inventory_dir.is_dir():
            from otio_app.services.inventory_loader import (
                is_canonical_folder_inventory_path,
            )

            inventory_files = sorted(
                path
                for path in project.inventory_dir.glob("*.json")
                if is_canonical_folder_inventory_path(path)
            )
            if inventory_files:
                st.markdown("**Inventar (pro Ordner)**")
                for inv_file in inventory_files:
                    st.caption(str(inv_file.name))
                    st.code(inv_file.read_text(encoding="utf-8")[:2000])
                    slim = inv_file.with_name(f"{inv_file.stem}.slim.json")
                    if slim.is_file():
                        st.caption(f"Slim: {slim.name}")
            else:
                st.caption("Inventar noch nicht erstellt.")
        else:
            st.caption("Inventar noch nicht erstellt.")

    render_file_paths(project)
