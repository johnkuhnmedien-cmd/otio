"""UI: Research-Excel hochladen → Adobe Stock lizenzieren/herunterladen."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from otio_app.paths import clean_user_path_input
from otio_app.services.adobe_research_import import (
    download_research_import,
    parse_research_excel,
)
from otio_app.services.supplement_sources.adobe_stock import AdobeStockAdapter


def render_adobe_research_import_page() -> None:
    st.header("Adobe Stock Import (Research-Excel)")
    st.caption(
        "Vor der Projektanlage: Research-Template hochladen, Zielordner wählen, "
        "Kapitelordner anlegen und Assets als `{Kapitel}_Asset_01` lizenzieren/herunterladen."
    )

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

    if uploaded is not None:
        try:
            plan = parse_research_excel(uploaded.getvalue())
        except Exception as exc:  # noqa: BLE001
            st.error(f"Excel konnte nicht gelesen werden: {exc}")
            return

        st.subheader("Vorschau")
        st.write(
            f"**{plan.chapter_count} Kapitel** · **{plan.asset_count} Assets** "
            f"(Sheet `{plan.sheet_name}`)"
        )
        preview_rows = [
            {
                "Kapitel": ch.title,
                "Ordner": ch.folder_name,
                "Assets": ch.asset_count,
                "Beispiel-IDs": ", ".join(a.asset_id for a in ch.assets[:3]),
            }
            for ch in plan.chapters
        ]
        st.dataframe(preview_rows, use_container_width=True, hide_index=True)

        chapter_labels = [ch.title for ch in plan.chapters]
        selected = st.multiselect(
            "Kapitel zum Import (leer = alle)",
            options=chapter_labels,
            default=chapter_labels,
            key="adobe_research_chapters",
        )
        skip_existing = st.checkbox(
            "Bereits heruntergeladene Asset-IDs überspringen",
            value=True,
            key="adobe_research_skip_existing",
        )

        target = clean_user_path_input(target_raw) if target_raw.strip() else ""
        if target:
            st.caption(f"Ziel: `{target}`")
            example = plan.chapters[0] if plan.chapters else None
            if example:
                st.caption(
                    f"Beispiel: `{Path(target) / example.folder_name / (example.folder_name + '_Asset_01.mp4')}`"
                )

        can_run = bool(target) and bool(selected) and readiness.acquire_enabled
        if st.button(
            "Lizenzieren & herunterladen",
            type="primary",
            disabled=not can_run,
            key="adobe_research_run",
        ):
            if not target:
                st.error("Bitte Zielordner angeben.")
                return
            progress = st.progress(0.0, text="Starte Import…")
            status = st.empty()

            def _on_progress(done: int, total: int, folder: str, asset_id: str) -> None:
                ratio = (done / total) if total else 1.0
                progress.progress(
                    min(1.0, ratio),
                    text=f"{done}/{total} · {folder} · ID {asset_id}",
                )
                status.caption(f"Aktuell: `{folder}` / Adobe `{asset_id}`")

            try:
                result = download_research_import(
                    plan,
                    target,
                    chapter_titles=selected,
                    skip_existing_ids=skip_existing,
                    progress_callback=_on_progress,
                )
            except Exception as exc:  # noqa: BLE001
                progress.empty()
                st.error(f"Import fehlgeschlagen: {exc}")
                return

            progress.progress(1.0, text="Fertig")
            st.success(
                f"Fertig: {result.downloaded} neu · {result.skipped} übersprungen · "
                f"{result.errors} Fehler"
            )
            st.caption(f"Manifest: `{result.manifest_path}`")
            if result.errors:
                err_rows = [
                    {
                        "Kapitel": item.chapter_title,
                        "Asset ID": item.asset_id,
                        "Fehler": item.message,
                    }
                    for item in result.items
                    if item.status == "error"
                ]
                st.dataframe(err_rows, use_container_width=True, hide_index=True)
    else:
        st.info("Excel hochladen, um die Kapitelvorschau zu sehen.")
