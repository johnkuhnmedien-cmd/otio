"""Projektweit eindeutige Enhanced-Asset-IDs (Ordner-Scope + Stem + kurzer Hash).

Der Hash basiert auf dem projekt-relativen Pfad — nicht auf absoluten
Rechnerpfaden — damit IDs zwischen Maschinen stabil bleiben.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from otio_app.models import Project
from otio_app.project_layout import safe_folder_slug

_LEGACY_STEM_ID_RE = re.compile(r"^asset_[a-z0-9_]+$", re.IGNORECASE)


def project_relative_posix(project: Project, path: Path | str) -> str:
    """Normalisierter projekt-relativer POSIX-Pfad; Fallback: Name + Parents."""
    root = Path(project.project_root).expanduser().resolve()
    media = Path(path).expanduser()
    try:
        resolved = media.resolve()
    except OSError:
        resolved = media
    try:
        rel = resolved.relative_to(root)
        return rel.as_posix()
    except ValueError:
        # Außerhalb des Projektroots (z. B. Supplement-Downloads unter work_dir).
        work = project.work_dir_path.expanduser().resolve()
        try:
            rel = resolved.relative_to(work)
            return f"_work/{rel.as_posix()}"
        except ValueError:
            return f"_external/{resolved.name}"


def stable_path_hash(relative_posix: str, *, length: int = 8) -> str:
    digest = hashlib.sha1(relative_posix.encode("utf-8")).hexdigest()
    return digest[: max(4, length)]


def _slug_stem(path: Path | str) -> str:
    stem = Path(path).stem.strip() or "unknown"
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", stem).strip("_").lower()
    return slug or "unknown"


def enhanced_asset_id_for_path(
    project: Project,
    path: Path | str,
    *,
    folder_name: str = "",
) -> str:
    """Kanonische Enhanced-ID: asset__{folder}__{stem}__{hash8}."""
    rel = project_relative_posix(project, path)
    if folder_name.strip():
        raw_folder = folder_name
    else:
        parts = Path(rel).parts
        raw_folder = parts[0] if parts else "media"
    folder_slug = re.sub(
        r"[^a-zA-Z0-9]+", "_", safe_folder_slug(raw_folder)
    ).strip("_").lower() or "media"
    stem = _slug_stem(path)
    digest = stable_path_hash(rel)
    return f"asset__{folder_slug}__{stem}__{digest}"


def is_legacy_ambiguous_asset_id(asset_id: str) -> bool:
    """Alte Stem-IDs ohne Ordner-Scope (z. B. asset_asset00011)."""
    text = (asset_id or "").strip()
    if not text:
        return False
    if text.startswith("asset__") and text.count("__") >= 3:
        return False
    return bool(_LEGACY_STEM_ID_RE.fullmatch(text))


def canonicalize_inventory_asset_id(
    project: Project,
    *,
    path: Path | str,
    folder_name: str,
    existing_id: str = "",
) -> str:
    """Behält bestehende nichtleere IDs; erzeugt kanonische ID nur wenn leer.

    Kollisionen gleicher IDs auf unterschiedliche Pfade werden im Katalog
    fail-closed erkannt — hier kein stilles Überschreiben.
    """
    current = (existing_id or "").strip()
    if current:
        return current
    return enhanced_asset_id_for_path(project, path, folder_name=folder_name)
