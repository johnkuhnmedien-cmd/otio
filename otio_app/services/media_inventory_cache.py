"""Medien-Cache für Asset-Analysen (pro Datei unter _otio/cache/inventory/)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from otio_app.analysis_models import AssetMediaAnalysis
from otio_app.models import Project
from otio_app.project_layout import safe_folder_slug


def media_cache_path(project: Project, folder_name: str, media_path: Path) -> Path:
    cache_dir = (
        project.work_dir_path
        / "cache"
        / "inventory"
        / safe_folder_slug(folder_name)
    )
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"{safe_folder_slug(media_path.name)}.json"


def load_cached_media(cache_file: Path) -> Optional[AssetMediaAnalysis]:
    """Lädt einen gültigen Cache-Eintrag oder None bei Fehler/kaputtem Cache."""
    if not cache_file.is_file():
        return None
    try:
        payload = json.loads(cache_file.read_text(encoding="utf-8"))
        return AssetMediaAnalysis.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        try:
            cache_file.unlink(missing_ok=True)
        except OSError:
            pass
        return None


def is_completed_analysis(entry: AssetMediaAnalysis) -> bool:
    """True, wenn dieses Asset bereits analysiert wurde (Erfolg oder dokumentierter Fehler)."""
    if entry.description.strip():
        return True
    return bool(entry.error)


def save_cached_media(cache_file: Path, entry: AssetMediaAnalysis) -> None:
    cache_file.write_text(entry.model_dump_json(indent=2), encoding="utf-8")
