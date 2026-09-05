"""Füllt fehlende YouTube-Metadaten-Pfade in project_layout nach.

Ältere lokale Kopien von ``project_layout.py`` kennen
``get_project_youtube_metadata_path`` nicht. Neuere
``youtube_publish_service.py`` importiert den Namen hart — Streamlit fällt
dann schon beim Start um. Dieser Helfer setzt die Namen am Modul, bevor
der harte Import läuft.
"""

from __future__ import annotations

from pathlib import Path


def install_youtube_metadata_paths() -> None:
    import otio_app.project_layout as layout

    if getattr(layout, "get_project_youtube_metadata_path", None) is None:

        def get_project_youtube_metadata_path(
            project_root: Path,
            voice_over_subdir: str,
            language: str,
        ) -> Path:
            from otio_app.defaults import YOUTUBE_METADATA_FILENAME

            return (
                layout.get_voice_over_dir(project_root, voice_over_subdir, language)
                / YOUTUBE_METADATA_FILENAME
            )

        layout.get_project_youtube_metadata_path = get_project_youtube_metadata_path

    if getattr(layout, "get_project_youtube_metadata_text_path", None) is None:

        def get_project_youtube_metadata_text_path(
            project_root: Path,
            voice_over_subdir: str,
            language: str,
        ) -> Path:
            del voice_over_subdir
            return (
                Path(project_root)
                / f"youtube_metadata_{layout.language_folder_name(language)}.txt"
            )

        layout.get_project_youtube_metadata_text_path = (
            get_project_youtube_metadata_text_path
        )


install_youtube_metadata_paths()
