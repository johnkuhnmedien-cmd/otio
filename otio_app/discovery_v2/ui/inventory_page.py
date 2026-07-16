"""Streamlit-Seite: Discovery V2 Medienbestand (read-only Bestandsaufnahme)."""

from __future__ import annotations

import streamlit as st

from otio_app.discovery_v2.application.inventory_service import (
    InventoryServiceError,
    get_latest_inventory,
    run_inventory_scan,
)
from otio_app.discovery_v2.domain.inventory import InventorySnapshot, MediaKind
from otio_app.discovery_v2.paths import get_discovery_v2_root
from otio_app.discovery_v2.ui.overview import active_discovery_project


_SESSION_SNAPSHOT_KEY = "discovery_v2_inventory_snapshot"
_SESSION_WARNING_KEY = "discovery_v2_inventory_warning"
_SESSION_ERROR_KEY = "discovery_v2_inventory_error"


def _format_created_at(snapshot: InventorySnapshot) -> str:
    created = snapshot.created_at
    if created.tzinfo is not None:
        return created.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    return created.strftime("%Y-%m-%d %H:%M:%S")


def _render_summary(snapshot: InventorySnapshot) -> None:
    st.subheader("Zusammenfassung")
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
    st.caption(f"Erstellt: {_format_created_at(snapshot)}")
    st.caption(
        "Quellgruppen sind die obersten Ordner der Projektstruktur — "
        "noch keine Kapitel. Kapitel entstehen erst später in der Dramaturgie."
    )


def _render_groups(snapshot: InventorySnapshot) -> None:
    st.subheader("Quellgruppen")
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
            st.write(
                f"**Videos:** {group.video_count}  \n"
                f"**Bilder:** {group.image_count}  \n"
                f"**Audio:** {group.audio_count}  \n"
                f"**Sonstige:** {group.other_count}"
            )
            rows = [
                {
                    "Relativer Pfad": f.relative_path,
                    "Typ": f.media_kind.value,
                    "Größe (Bytes)": f.size_bytes,
                    "Geändert": f.mtime_iso,
                }
                for f in files_by_group.get(group.source_group, [])
            ]
            if rows:
                st.dataframe(rows, hide_index=True, use_container_width=True)
            else:
                st.caption("Keine Dateien in dieser Quellgruppe.")


def _render_excluded(snapshot: InventorySnapshot) -> None:
    if not snapshot.excluded:
        return
    with st.expander(f"Ausgeschlossene Einträge ({snapshot.excluded_count})", expanded=False):
        rows = [
            {"Relativer Pfad": e.relative_path, "Grund": e.reason}
            for e in snapshot.excluded
        ]
        st.dataframe(rows, hide_index=True, use_container_width=True)


def render_discovery_inventory_page() -> None:
    """Medienbestand — Scan nur über expliziten Button."""
    st.title("Medienbestand")
    project = active_discovery_project()
    if project is None:
        return

    discovery_root = get_discovery_v2_root(project.project_root_path)

    st.info(
        "Diese Bestandsaufnahme **liest nur** den Projektordner. "
        "Originaldateien werden nicht verändert. "
        "Es wird **kein Working Media** erzeugt und nichts kopiert oder transkodiert. "
        "Oberste Ordner werden als **Quellgruppen** erfasst — noch keine Kapitel."
    )

    st.write(f"**Projekt:** {project.name}")
    st.write(f"**Projektordner:** `{project.project_root}`")
    st.write(f"**Discovery-Ausgabewurzel:** `{discovery_root}`")

    # Session-Snapshot nach Reload aus Artefakt nachladen, falls nötig.
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

    # Expliziter Trigger — kein Scan bei normalem Rerun.
    if st.button(button_label, type="primary", key="discovery_v2_inventory_scan_btn"):
        try:
            snapshot = run_inventory_scan(project)
            st.session_state[_SESSION_SNAPSHOT_KEY] = snapshot
            st.session_state[_SESSION_WARNING_KEY] = None
            st.session_state[_SESSION_ERROR_KEY] = None
            st.success("Bestandsaufnahme abgeschlossen.")
        except InventoryServiceError as exc:
            st.session_state[_SESSION_ERROR_KEY] = str(exc)
            st.error(str(exc))
            # Vorheriger Snapshot bleibt in Session und auf Disk.

    snapshot = st.session_state.get(_SESSION_SNAPSHOT_KEY)
    if snapshot is None:
        st.caption("Noch keine Bestandsaufnahme vorhanden.")
        return

    _render_summary(snapshot)
    _render_groups(snapshot)
    _render_excluded(snapshot)

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
