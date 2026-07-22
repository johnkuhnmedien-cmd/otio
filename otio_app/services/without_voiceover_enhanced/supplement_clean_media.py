"""Clean Media nur für neu export_ready Supplements (Enhanced)."""

from __future__ import annotations

import logging
from pathlib import Path

from otio_app.models import Project
from otio_app.services.clean_media import (
    CLEAN_STATUS_CLEAN,
    process_and_persist_media_file,
    resolve_effective_media_path,
)

logger = logging.getLogger(__name__)


def ensure_new_supplement_clean_media(
    project: Project,
    *,
    folder_name: str,
    media_path: Path | str,
) -> Path:
    """Clean Media für genau diese neue Supplement-Datei; liefert effektiven Pfad.

    Bei Fehlern: Originalpfad zurück (Export kann später Test-Gaps nutzen).
    Kein Massen-Rerun über bestehende Supplements.
    """
    source = Path(media_path).expanduser()
    folder = (folder_name or "").strip() or (
        project.selected_asset_subdirs[0] if project.selected_asset_subdirs else "Assets"
    )
    if not source.is_file():
        return source
    try:
        entry = process_and_persist_media_file(project, folder, source)
        if (
            entry.status == CLEAN_STATUS_CLEAN
            and entry.clean_path
            and Path(entry.clean_path).is_file()
        ):
            return Path(entry.clean_path)
        return resolve_effective_media_path(project, folder, source)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Clean Media für neues Supplement fehlgeschlagen (%s): %s",
            source.name,
            exc,
        )
        return source
