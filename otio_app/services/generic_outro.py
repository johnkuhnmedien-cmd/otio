"""Generic-Asset für Ordner-Ausklingen nach dem letzten gesprochenen Wort."""

from __future__ import annotations

from pathlib import Path

from otio_app.defaults import GENERIC_OUTRO_FILENAMES, GENERIC_OUTRO_FOLDER_NAMES
from otio_app.models import Project
from otio_app.services.clean_media import path_is_readable_file
from otio_app.services.media_utils import list_media_files


def resolve_generic_outro_media(project: Project) -> Path | None:
    """Sucht ein Generic-Outro-Video im Projekt (Ordner ``Generic/`` oder ``_otio/generic_outro.mp4``)."""
    root = project.project_root_path
    for folder_name in GENERIC_OUTRO_FOLDER_NAMES:
        folder = root / folder_name
        if not folder.is_dir():
            continue
        for filename in GENERIC_OUTRO_FILENAMES:
            candidate = folder / filename
            if path_is_readable_file(candidate):
                return candidate.resolve()
        media_files = list_media_files(folder)
        if media_files:
            return media_files[0].resolve()

    work_candidate = project.work_dir_path / "generic_outro.mp4"
    if path_is_readable_file(work_candidate):
        return work_candidate.resolve()
    return None
