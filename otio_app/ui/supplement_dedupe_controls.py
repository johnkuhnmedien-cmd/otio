"""Gemeinsame Streamlit-Controls für Supplement-Duplikat-Aufräumen."""

from __future__ import annotations

import streamlit as st

from otio_app.models import Project
from otio_app.project_layout import safe_folder_slug
from otio_app.services.supplement_dedupe import (
    cleanup_cut_plan_supplement_orphans,
    cleanup_supplement_duplicates,
    scan_cut_plan_supplement_orphans,
    scan_supplement_duplicates,
)


def render_folder_supplement_dedupe_controls(
    project: Project,
    folder_name: str,
    *,
    key_prefix: str,
) -> None:
    """Aufräumen von `{folder}/_supplemental/_provider/`-Duplikaten."""
    groups = scan_supplement_duplicates(project, folder_name)
    duplicate_files = sum(len(group.remove) for group in groups)
    if groups:
        st.caption(
            f"**{folder_name}**: {duplicate_files} Duplikat-Datei(en) in "
            f"{len(groups)} Provider-Asset-Gruppe(n) unter `_supplemental/`"
        )
    else:
        st.caption(f"**{folder_name}**: keine `_supplemental/`-Duplikate")
        return

    slug = safe_folder_slug(folder_name)
    preview_clicked = st.button(
        "Duplikate prüfen",
        key=f"{key_prefix}_preview_{project.id}_{slug}",
        help="Gleiche Pexels-/Adobe-IDs — je ID eine Datei behalten",
    )
    cleanup_clicked = st.button(
        "Duplikate aufräumen",
        key=f"{key_prefix}_cleanup_{project.id}_{slug}",
        help="Entfernt doppelte Downloads unter `_supplemental/_…/`",
    )
    if preview_clicked:
        with st.expander(f"Duplikat-Vorschau — {folder_name}", expanded=True):
            for group in groups:
                st.markdown(
                    f"**{group.provider}:{group.provider_asset_id}** "
                    f"({group.count}×) — behalten: "
                    f"`{group.keep.name if group.keep is not None else '—'}`"
                )
                for path in group.remove:
                    st.caption(f"entfernen: `{path.name}`")
    if cleanup_clicked:
        report = cleanup_supplement_duplicates(project, folder_name, dry_run=False)
        st.success(
            f"{len(report.deleted_media)} Duplikat-Datei(en) entfernt "
            f"({report.group_count} Gruppen). "
            f"Inventory −{report.inventory_pruned}, "
            f"Clean-Dateien −{len(report.deleted_clean)}."
        )
        st.rerun()


def render_cut_plan_supplement_orphan_controls(project: Project) -> None:
    """Aufräumen ungenutzter Kopien unter `cut_plan/supplement_assets/`."""
    groups = scan_cut_plan_supplement_orphans(project)
    removable = sum(len(group.remove) for group in groups)
    st.markdown("##### Cut-Plan Supplement-Dateien")
    st.caption(
        "Downloads liegen unter `_otio/…/cut_plan/supplement_assets/{request}/` "
        "(getrennt von `{Ordner}/_supplemental/`). Bereits bekannte Provider-IDs "
        "werden nicht erneut heruntergeladen — bei Wiederverwendung entsteht "
        "trotzdem eine Kopie je Request."
    )
    if not groups:
        st.caption("Keine ungenutzten Cut-Plan-Supplement-Duplikate gefunden.")
        return

    st.caption(
        f"{removable} ungenutzte Kopie(n) in {len(groups)} Provider-Asset-Gruppe(n) "
        "(Dateien, die der aktuelle Cut Plan / das Manifest nicht referenziert)."
    )
    preview = st.button(
        "Ungenutzte Cut-Plan-Kopien prüfen",
        key=f"cut_plan_orphan_preview_{project.id}",
    )
    cleanup = st.button(
        "Ungenutzte Cut-Plan-Kopien aufräumen",
        key=f"cut_plan_orphan_cleanup_{project.id}",
        type="secondary",
    )
    if preview:
        with st.expander("Cut-Plan-Orphan-Vorschau", expanded=True):
            for group in groups:
                st.markdown(
                    f"**{group.provider}:{group.provider_asset_id}** — "
                    f"behalten: `{group.keep.name if group.keep else '—'}`"
                )
                for path in group.remove:
                    st.caption(f"entfernen: `{path}`")
    if cleanup:
        report = cleanup_cut_plan_supplement_orphans(project, dry_run=False)
        st.success(f"{len(report.deleted_media)} ungenutzte Cut-Plan-Kopie(n) entfernt.")
        st.rerun()
