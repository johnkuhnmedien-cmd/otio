"""Streamlit-Seite: Discovery V2 Medienbestand + Auswahl/Bestätigung."""

from __future__ import annotations

import streamlit as st

from otio_app.discovery_v2.application.asset_registry_service import (
    AssetRegistryServiceError,
    can_import_selection,
    get_registry_summary,
    import_confirmed_selection,
)
from otio_app.discovery_v2.application.inventory_service import (
    InventoryServiceError,
    get_latest_inventory,
    run_inventory_scan,
)
from otio_app.discovery_v2.application.selection_service import (
    build_default_draft,
    confirm_selection,
    get_latest_confirmed_selection,
    is_selectable_media,
    other_files,
    set_file_excluded,
    set_group_selected,
    summarize_selection,
)
from otio_app.discovery_v2.domain.asset_registry import RegistryImportStatus
from otio_app.discovery_v2.domain.inventory import InventorySnapshot, MediaKind
from otio_app.discovery_v2.domain.selection import (
    InventorySelection,
    SelectionDraft,
    SelectionStatus,
)
from otio_app.discovery_v2.paths import get_discovery_v2_root
from otio_app.discovery_v2.ui.flash import discovery_ui_flash_and_rerun
from otio_app.discovery_v2.ui.overview import active_discovery_project


_SESSION_SNAPSHOT_KEY = "discovery_v2_inventory_snapshot"
_SESSION_WARNING_KEY = "discovery_v2_inventory_warning"
_SESSION_ERROR_KEY = "discovery_v2_inventory_error"
_SESSION_DRAFT_KEY = "discovery_v2_selection_draft"
_SESSION_SELECTION_KEY = "discovery_v2_confirmed_selection"
_SESSION_SELECTION_STATUS_KEY = "discovery_v2_selection_status"
_SESSION_SELECTION_WARNING_KEY = "discovery_v2_selection_warning"
_SESSION_SELECTION_ERROR_KEY = "discovery_v2_selection_error"
_SESSION_REGISTRY_RESULT_KEY = "discovery_v2_registry_import_result"
_SESSION_REGISTRY_ERROR_KEY = "discovery_v2_registry_error"


def _format_dt(value) -> str:
    if value is None:
        return "—"
    if value.tzinfo is not None:
        return value.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _ensure_draft(snapshot: InventorySnapshot) -> SelectionDraft:
    draft = st.session_state.get(_SESSION_DRAFT_KEY)
    if (
        isinstance(draft, SelectionDraft)
        and draft.scan_id == snapshot.scan_id
    ):
        return draft
    draft = build_default_draft(snapshot)
    st.session_state[_SESSION_DRAFT_KEY] = draft
    return draft


def _load_confirmed_into_session(project, snapshot: InventorySnapshot | None) -> None:
    if _SESSION_SELECTION_KEY in st.session_state:
        # Status gegen aktuellen Scan aktualisieren
        selection = st.session_state.get(_SESSION_SELECTION_KEY)
        if isinstance(selection, InventorySelection) and snapshot is not None:
            from otio_app.discovery_v2.application.selection_service import (
                effective_selection_status,
            )

            st.session_state[_SESSION_SELECTION_STATUS_KEY] = effective_selection_status(
                selection, snapshot.scan_id
            )
        return
    try:
        selection, status, warning = get_latest_confirmed_selection(
            project,
            current_scan_id=snapshot.scan_id if snapshot else None,
        )
    except InventoryServiceError as exc:
        st.session_state[_SESSION_SELECTION_ERROR_KEY] = str(exc)
        selection, status, warning = None, None, None
    st.session_state[_SESSION_SELECTION_KEY] = selection
    st.session_state[_SESSION_SELECTION_STATUS_KEY] = status
    st.session_state[_SESSION_SELECTION_WARNING_KEY] = warning


def _render_summary(snapshot: InventorySnapshot) -> None:
    st.subheader("Bestandsaufnahme")
    cols = st.columns(4)
    cols[0].metric("Quellgruppen", snapshot.source_group_count)
    cols[1].metric("Dateien", snapshot.file_count)
    cols[2].metric("Videos", snapshot.video_count)
    cols[3].metric("Bilder", snapshot.image_count)
    cols2 = st.columns(4)
    cols2[0].metric("Audio", snapshot.audio_count)
    cols2[1].metric("Sonstige", snapshot.other_count)
    cols2[2].metric("Ausgeschlossen", snapshot.excluded_count)
    cols2[3].metric("Scan-ID", snapshot.scan_id[:8] + "…")
    st.caption(f"Erstellt: {_format_dt(snapshot.created_at)}")
    st.caption(
        "Quellgruppen sind die obersten Ordner der Projektstruktur — "
        "noch keine Kapitel. Kapitel entstehen erst später in der Dramaturgie."
    )


def _render_groups_readonly(snapshot: InventorySnapshot) -> None:
    st.subheader("Gefundene Quellgruppen")
    files_by_group: dict[str, list] = {}
    for entry in snapshot.files:
        if entry.scan_status.value != "found":
            continue
        files_by_group.setdefault(entry.source_group, []).append(entry)

    for group in snapshot.source_groups:
        with st.expander(
            f"{group.label} — {group.file_count} Dateien "
            f"(Video {group.video_count} · Bild {group.image_count} · "
            f"Audio {group.audio_count} · Sonstige {group.other_count})",
            expanded=False,
        ):
            rows = [
                {
                    "Relativer Pfad": f.relative_path,
                    "Typ": f.media_kind.value,
                    "Größe (Bytes)": f.size_bytes,
                }
                for f in files_by_group.get(group.source_group, [])
            ]
            if rows:
                st.dataframe(rows, hide_index=True, use_container_width=True)
            else:
                st.caption("Keine Dateien in dieser Quellgruppe.")


def _render_scan_excluded(snapshot: InventorySnapshot) -> None:
    if not snapshot.excluded:
        return
    with st.expander(
        f"Beim Scan ausgeschlossene Einträge ({snapshot.excluded_count})",
        expanded=False,
    ):
        rows = [
            {"Relativer Pfad": e.relative_path, "Grund": e.reason}
            for e in snapshot.excluded
        ]
        st.dataframe(rows, hide_index=True, use_container_width=True)


def _render_confirmed_banner(
    selection: InventorySelection | None,
    status: SelectionStatus | None,
) -> None:
    if selection is None or status is None:
        st.caption("Noch keine bestätigte Medienauswahl.")
        return
    if status == SelectionStatus.CONFIRMED:
        st.success(
            f"Bestätigte Auswahl für Scan `{selection.scan_id[:8]}…` — "
            f"{selection.selected_media_count} Medien "
            f"(bestätigt {_format_dt(selection.confirmed_at)})."
        )
    elif status == SelectionStatus.STALE:
        st.warning(
            f"Die letzte Bestätigung (`{selection.selection_id[:8]}…`) gehört zu "
            f"Scan `{selection.scan_id[:8]}…` und ist **veraltet (stale)**, "
            "weil eine neuere Bestandsaufnahme vorliegt. "
            "Bitte die aktuelle Auswahl erneut bestätigen. "
            "Media Intake ist mit dieser Auswahl nicht erlaubt."
        )
        with st.expander("Veraltete Bestätigung (nur Information)", expanded=False):
            st.write(f"**selection_id:** `{selection.selection_id}`")
            st.write(f"**scan_id:** `{selection.scan_id}`")
            st.write(f"**Medien:** {selection.selected_media_count}")
            st.write(f"**Quellgruppen:** {', '.join(selection.selected_source_groups)}")
    else:
        st.info(f"Auswahlstatus: `{status.value}`")


def _render_selection_editor(
    snapshot: InventorySnapshot,
    draft: SelectionDraft,
) -> SelectionDraft:
    st.subheader("Auswahl und Bestätigung")
    st.caption(
        "Standard: Videos, Bilder und Audio sind ausgewählt; "
        "sonstige Dateien (`other`) gehören nicht zur Medienauswahl. "
        "Es werden noch keine Dateien kopiert oder technisch geprüft."
    )

    active = set(draft.selected_source_groups)
    files_by_group: dict[str, list] = {}
    for entry in snapshot.files:
        if entry.scan_status.value != "found":
            continue
        files_by_group.setdefault(entry.source_group, []).append(entry)

    for group in snapshot.source_groups:
        group_files = files_by_group.get(group.source_group, [])
        selectable = [f for f in group_files if is_selectable_media(f)]
        others = [f for f in group_files if f.media_kind == MediaKind.OTHER]
        group_on = group.source_group in active
        with st.expander(
            f"{group.label} — "
            f"{'übernehmen' if group_on else 'ausgeschlossen'} · "
            f"{len(selectable)} Medien · {len(others)} sonstige",
            expanded=False,
        ):
            new_group_on = st.checkbox(
                f"Quellgruppe „{group.label}“ übernehmen",
                value=group_on,
                key=f"discovery_v2_group_{snapshot.scan_id}_{group.source_group}",
            )
            if new_group_on != group_on:
                draft = set_group_selected(draft, group.source_group, new_group_on)
                st.session_state[_SESSION_DRAFT_KEY] = draft
                group_on = new_group_on

            if not selectable and not others:
                st.caption("Keine Dateien.")
                continue

            if selectable:
                st.markdown("**Unterstützte Medien**")
                filter_text = st.text_input(
                    "Filter (Dateiname/Pfad)",
                    value="",
                    key=f"discovery_v2_filter_{snapshot.scan_id}_{group.source_group}",
                ).strip().casefold()
                for entry in selectable:
                    if filter_text and filter_text not in entry.relative_path.casefold():
                        continue
                    excluded = entry.relative_path in set(draft.excluded_relative_paths)
                    # Aktiv nur sinnvoll, wenn Gruppe übernommen wird.
                    checked = group_on and not excluded
                    label = f"{entry.relative_path} ({entry.media_kind.value})"
                    new_checked = st.checkbox(
                        label,
                        value=checked,
                        disabled=not group_on,
                        key=(
                            f"discovery_v2_file_{snapshot.scan_id}_"
                            f"{entry.relative_path}"
                        ),
                    )
                    if group_on:
                        want_excluded = not new_checked
                        if want_excluded != excluded:
                            draft = set_file_excluded(
                                draft, entry.relative_path, want_excluded
                            )
                            st.session_state[_SESSION_DRAFT_KEY] = draft

            if others:
                st.markdown("**Sonstige Dateien (nicht auswählbar)**")
                for entry in others:
                    st.caption(f"• {entry.relative_path} — other")

    return st.session_state.get(_SESSION_DRAFT_KEY, draft)


def _render_registry_section(
    project,
    snapshot: InventorySnapshot,
    selection: InventorySelection | None,
    status: SelectionStatus | None,
) -> None:
    st.divider()
    st.subheader("Asset Registry")
    st.caption(
        "Die Registry speichert Metadaten der bestätigten Quellmedien. "
        "Es werden keine Medien kopiert, gehasht oder technisch geprüft. "
        "Es wird noch kein Working Media erzeugt."
    )

    reg_error = st.session_state.get(_SESSION_REGISTRY_ERROR_KEY)
    if reg_error:
        st.error(reg_error)

    if selection is None or status is None:
        st.info("Bestätige zuerst deine Medienauswahl.")
        return

    if status == SelectionStatus.STALE:
        st.warning(
            "Die bestätigte Auswahl gehört zu einer älteren Bestandsaufnahme. "
            "Bitte prüfe und bestätige den aktuellen Bestand erneut."
        )
        return

    ok, reason, blocked = can_import_selection(
        project, snapshot=snapshot, selection=selection, status=status
    )
    if not ok:
        if blocked == RegistryImportStatus.STALE_SELECTION:
            st.warning(reason or "Auswahl ist veraltet.")
        else:
            st.info(reason or "Import derzeit nicht möglich.")
        return

    st.write(f"**Selection-ID:** `{selection.selection_id}`")
    st.write(f"**Scan-ID:** `{selection.scan_id}`")
    st.write(f"**Ausgewählte Medien:** {selection.selected_media_count}")
    st.write(
        "**Quellgruppen:** "
        + (", ".join(selection.selected_source_groups) or "—")
    )
    st.write("**Registry-Pfad:** `_otio_v2/registry/assets.sqlite3`")

    summary = get_registry_summary(project)
    if summary.get("exists"):
        st.caption(
            f"Bereits registrierte Assets in dieser Registry: {summary['asset_count']}"
        )

    st.info(
        "Beim Übernehmen werden **nur Metadaten** registriert. "
        "Keine Kopie, keine Transkodierung, keine technische Medienprüfung, "
        "kein Working Media."
    )

    if st.button(
        "Auswahl in Asset Registry übernehmen",
        type="primary",
        key="discovery_v2_registry_import_btn",
    ):
        try:
            result = import_confirmed_selection(project)
            st.session_state[_SESSION_REGISTRY_RESULT_KEY] = result
            st.session_state[_SESSION_REGISTRY_ERROR_KEY] = None
        except (AssetRegistryServiceError, InventoryServiceError) as exc:
            st.session_state[_SESSION_REGISTRY_ERROR_KEY] = str(exc)
            st.error(str(exc))
            return

    result = st.session_state.get(_SESSION_REGISTRY_RESULT_KEY)
    if result is None:
        return

    if result.status == RegistryImportStatus.IMPORTED:
        st.success(
            f"Import erfolgreich. Import-ID `{result.import_id}` · "
            f"{result.asset_count} Assets "
            f"(neu {result.new_asset_count}, wiederverwendet {result.reused_asset_count}). "
            "Die Medien wurden noch nicht technisch geprüft oder kopiert."
        )
    elif result.status == RegistryImportStatus.ALREADY_IMPORTED:
        st.info(result.message)
    elif result.status == RegistryImportStatus.STALE_SELECTION:
        st.warning(result.message)
    else:
        st.error(result.message)

    if result.import_id:
        st.write(f"**Import-ID:** `{result.import_id}`")
        st.write(f"**Registry:** `_otio_v2/{result.registry_sqlite_relative_path}`")
        st.write(
            f"**Importbericht:** `_otio_v2/registry/imports/{result.import_id}.json`"
        )
    if result.report is not None:
        with st.expander("Importbericht (Details)", expanded=False):
            st.json(result.report.model_dump(mode="json"))


def _render_confirm_panel(
    project,
    snapshot: InventorySnapshot,
    draft: SelectionDraft,
) -> None:
    summary = summarize_selection(snapshot, draft)
    st.markdown("#### Prüfübersicht vor Bestätigung")
    st.write(f"**Scan-ID:** `{snapshot.scan_id}`")
    st.write(
        "**Ausgewählte Quellgruppen:** "
        + (", ".join(summary["selected_source_groups"]) or "—")
    )
    cols = st.columns(4)
    cols[0].metric("Videos", summary["selected_video_count"])
    cols[1].metric("Bilder", summary["selected_image_count"])
    cols[2].metric("Audio", summary["selected_audio_count"])
    cols[3].metric("Medien gesamt", summary["selected_media_count"])
    st.write(
        f"**Ausgeschlossene unterstützte Medien:** "
        f"{len(summary['excluded_relative_paths'])}"
    )
    st.write(
        f"**Nicht auswählbare sonstige Dateien:** {summary['other_file_count']}"
    )
    if summary["excluded_relative_paths"]:
        with st.expander("Ausgeschlossene Medienpfade", expanded=False):
            for path in summary["excluded_relative_paths"]:
                st.caption(f"• {path}")
    if other_files(snapshot):
        with st.expander("Sonstige Dateien (nicht in Medienauswahl)", expanded=False):
            for entry in other_files(snapshot):
                st.caption(f"• {entry.relative_path}")

    acknowledged = st.checkbox(
        "Ich habe die Bestandsaufnahme und die Auswahl geprüft.",
        value=False,
        key=f"discovery_v2_ack_{snapshot.scan_id}",
    )
    if st.button(
        "Medienauswahl bestätigen",
        type="primary",
        key="discovery_v2_confirm_selection_btn",
    ):
        try:
            selection = confirm_selection(
                project,
                snapshot,
                draft,
                acknowledged=acknowledged,
            )
            st.session_state[_SESSION_SELECTION_KEY] = selection
            st.session_state[_SESSION_SELECTION_STATUS_KEY] = SelectionStatus.CONFIRMED
            st.session_state[_SESSION_SELECTION_ERROR_KEY] = None
            st.session_state[_SESSION_SELECTION_WARNING_KEY] = None
            st.success(
                "Medienauswahl bestätigt. "
                f"Auswahl-ID `{selection.selection_id}` · "
                f"Scan `{selection.scan_id}` · "
                f"{selection.selected_media_count} Medien · "
                f"{_format_dt(selection.confirmed_at)}. "
                "Es wurde noch kein Working Media erzeugt."
            )
        except InventoryServiceError as exc:
            st.session_state[_SESSION_SELECTION_ERROR_KEY] = str(exc)
            st.error(str(exc))


def render_discovery_inventory_page() -> None:
    """Medienbestand — Scan und bewusste Auswahl/Bestätigung."""
    st.title("Medienbestand")
    project = active_discovery_project()
    if project is None:
        return

    discovery_root = get_discovery_v2_root(project.project_root_path)

    st.info(
        "Diese Bestandsaufnahme **liest nur** den Projektordner. "
        "Originaldateien werden nicht verändert. "
        "Es wird **kein Working Media** erzeugt und nichts kopiert oder transkodiert. "
        "Oberste Ordner werden als **Quellgruppen** erfasst — noch keine Kapitel. "
        "Die Bestätigung speichert nur die Auswahl als JSON-Artefakt."
    )

    st.write(f"**Projekt:** {project.name}")
    st.write(f"**Projektordner:** `{project.project_root}`")
    st.write(f"**Discovery-Ausgabewurzel:** `{discovery_root}`")

    if _SESSION_SNAPSHOT_KEY not in st.session_state:
        try:
            loaded, warning = get_latest_inventory(project)
        except InventoryServiceError as exc:
            st.session_state[_SESSION_ERROR_KEY] = str(exc)
            loaded, warning = None, None
        st.session_state[_SESSION_SNAPSHOT_KEY] = loaded
        st.session_state[_SESSION_WARNING_KEY] = warning

    error = st.session_state.get(_SESSION_ERROR_KEY)
    warning = st.session_state.get(_SESSION_WARNING_KEY)
    snapshot: InventorySnapshot | None = st.session_state.get(_SESSION_SNAPSHOT_KEY)

    if error:
        st.error(error)
    if warning:
        st.warning(warning)

    has_snapshot = snapshot is not None
    button_label = (
        "Bestandsaufnahme aktualisieren" if has_snapshot else "Bestandsaufnahme starten"
    )

    if st.button(button_label, type="primary", key="discovery_v2_inventory_scan_btn"):
        try:
            snapshot = run_inventory_scan(project)
            st.session_state[_SESSION_SNAPSHOT_KEY] = snapshot
            st.session_state[_SESSION_WARNING_KEY] = None
            st.session_state[_SESSION_ERROR_KEY] = None
            # Neuer Scan → frische Draft-Auswahl; alte Bestätigung wird stale.
            st.session_state[_SESSION_DRAFT_KEY] = build_default_draft(snapshot)
            for key in (
                _SESSION_SELECTION_KEY,
                _SESSION_SELECTION_STATUS_KEY,
                _SESSION_SELECTION_WARNING_KEY,
                _SESSION_SELECTION_ERROR_KEY,
                _SESSION_REGISTRY_RESULT_KEY,
                _SESSION_REGISTRY_ERROR_KEY,
            ):
                st.session_state.pop(key, None)
            discovery_ui_flash_and_rerun("Bestandsaufnahme abgeschlossen.")
        except InventoryServiceError as exc:
            st.session_state[_SESSION_ERROR_KEY] = str(exc)
            discovery_ui_flash_and_rerun(str(exc), level="error")

    snapshot = st.session_state.get(_SESSION_SNAPSHOT_KEY)
    if snapshot is None:
        st.caption("Noch keine Bestandsaufnahme vorhanden.")
        return

    _load_confirmed_into_session(project, snapshot)

    sel_error = st.session_state.get(_SESSION_SELECTION_ERROR_KEY)
    sel_warning = st.session_state.get(_SESSION_SELECTION_WARNING_KEY)
    if sel_error:
        st.error(sel_error)
    if sel_warning:
        st.warning(sel_warning)

    _render_summary(snapshot)
    _render_groups_readonly(snapshot)
    _render_scan_excluded(snapshot)

    st.divider()
    _render_confirmed_banner(
        st.session_state.get(_SESSION_SELECTION_KEY),
        st.session_state.get(_SESSION_SELECTION_STATUS_KEY),
    )

    draft = _ensure_draft(snapshot)
    draft = _render_selection_editor(snapshot, draft)
    st.session_state[_SESSION_DRAFT_KEY] = draft
    _render_confirm_panel(project, snapshot, draft)

    _render_registry_section(
        project,
        snapshot,
        st.session_state.get(_SESSION_SELECTION_KEY),
        st.session_state.get(_SESSION_SELECTION_STATUS_KEY),
    )

    with st.expander("Technische Details", expanded=False):
        st.write(f"**schema_version:** `{snapshot.schema_version}`")
        st.write(f"**scan_id:** `{snapshot.scan_id}`")
        st.write(f"**project_id:** `{snapshot.project_id}`")
        st.write(f"**project_root (absolut):** `{snapshot.project_root}`")
        kind_map = {k.value: 0 for k in MediaKind}
        for f in snapshot.files:
            if f.scan_status.value == "found":
                kind_map[f.media_kind.value] = kind_map.get(f.media_kind.value, 0) + 1
        st.json(kind_map)
