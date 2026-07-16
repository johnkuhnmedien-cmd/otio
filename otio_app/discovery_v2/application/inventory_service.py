"""Application-Service: Bestandsaufnahme orchestrieren (Scan + Artefakte)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from otio_app.discovery_v2.adapters.filesystem_inventory import (
    InventoryScanError,
    scan_project_filesystem,
)
from otio_app.discovery_v2.domain.inventory import InventorySnapshot
from otio_app.discovery_v2.persistence.inventory_artifact_store import (
    InventoryArtifactError,
    load_latest_snapshot,
    save_snapshot,
)
from otio_app.models import Project, ProjectMode


class InventoryServiceError(ValueError):
    """Fachlicher Fehler der Bestandsaufnahme."""


def require_discovery_project(project: Project | None) -> Project:
    if project is None:
        raise InventoryServiceError("Kein aktives Projekt ausgewählt.")
    if project.project_mode != ProjectMode.DISCOVERY_V2:
        raise InventoryServiceError(
            "Bestandsaufnahme ist nur für Discovery-V2-Projekte verfügbar."
        )
    if not project.project_root or not str(project.project_root).strip():
        raise InventoryServiceError("Projektroot fehlt.")
    return project


def run_inventory_scan(project: Project) -> InventorySnapshot:
    """Führt einen expliziten Scan aus und speichert bei Erfolg Artefakte."""
    project = require_discovery_project(project)
    root = project.project_root_path
    try:
        result = scan_project_filesystem(root)
    except InventoryScanError as exc:
        raise InventoryServiceError(str(exc)) from exc

    snapshot = InventorySnapshot(
        scan_id=str(uuid4()),
        project_id=project.id,
        project_root=str(root.expanduser().resolve()),
        created_at=datetime.now(timezone.utc),
        source_group_count=len(result.source_groups),
        file_count=sum(1 for f in result.files if f.scan_status.value == "found"),
        video_count=result.video_count,
        image_count=result.image_count,
        audio_count=result.audio_count,
        other_count=result.other_count,
        excluded_count=len(result.excluded),
        source_groups=result.source_groups,
        files=result.files,
        excluded=result.excluded,
    )
    try:
        save_snapshot(root, snapshot)
    except InventoryArtifactError as exc:
        raise InventoryServiceError(str(exc)) from exc
    return snapshot


def get_latest_inventory(
    project: Project,
) -> tuple[InventorySnapshot | None, str | None]:
    """Lädt den letzten erfolgreichen Snapshot (für Reload)."""
    project = require_discovery_project(project)
    try:
        return load_latest_snapshot(project.project_root_path)
    except InventoryArtifactError as exc:
        return None, str(exc)


def inventory_artifact_root(project: Project) -> Path:
    from otio_app.discovery_v2.paths import get_discovery_v2_root

    return get_discovery_v2_root(project.project_root_path) / "inventory"
