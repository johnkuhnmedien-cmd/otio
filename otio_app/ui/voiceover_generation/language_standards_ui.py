"""UI: Speicherorte der globalen Sprach-Standards."""

from __future__ import annotations

import streamlit as st

from otio_app.services.voiceover_generation.language_defaults_catalog import (
    get_language_standard,
    language_standards_dir,
    list_language_standard_files,
    list_shared_library_files,
)


def render_language_standard_path_caption(key: str) -> None:
    """Eine Zeile mit dem exakten Dateipfad neben „Als Standard speichern“."""
    item = get_language_standard(key)
    st.caption(f"Datei: `{item.path}`")


def render_language_standards_expander(*, expanded: bool = False) -> None:
    """Listet alle globalen Sprach-Standard-Dateien mit Absolutpfad."""
    with st.expander("📂 Wo liegen die Sprach-Standards?", expanded=expanded):
        st.caption(
            f"Gemeinsamer Ordner: `{language_standards_dir()}`. "
            "Pro Sprache ein Eintrag unter `by_language` (z. B. PT, EN, FR, IT, JP, KR). "
            "Der Videotitel und erzeugte Texte bleiben im Projekt. "
            "Fehlende Sprachen in der Projektliste löschen diese Dateien nicht."
        )
        for item in list_language_standard_files():
            st.markdown(f"**{item.tab}** — `{item.filename}`")
            st.caption(str(item.path))
            st.caption(f"Enthält: {item.stores}. Nicht: {item.not_stored}.")
        st.markdown("**Weitere globale Dateien (nicht pro Sprache)**")
        for item in list_shared_library_files():
            st.markdown(f"**{item.tab}** — `{item.path}`")
            st.caption(f"Enthält: {item.stores}.")
