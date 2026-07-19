"""Atomische JSON-Speicherung der Discovery-V2-Bestandsaufnahme."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from otio_app.discovery_v2.domain.inventory import (
    InventoryLatestPointer,
    InventorySnapshot,
)
from otio_app.discovery_v2.paths import (
    assert_path_is_under_discovery_v2,
    get_discovery_v2_root,
)


class InventoryArtifactError(ValueError):
    """Fehler beim Lesen/Schreiben von Inventory-Artefakten."""


def inventory_dir(project_root: Path) -> Path:
    return get_discovery_v2_root(project_root) / "inventory"


def snapshots_dir(project_root: Path) -> Path:
    return inventory_dir(project_root) / "snapshots"


def latest_pointer_path(project_root: Path) -> Path:
    return inventory_dir(project_root) / "latest.json"


def snapshot_path(project_root: Path, scan_id: str) -> Path:
    return snapshots_dir(project_root) / f"{scan_id}.json"


def _atomic_write_text(path: Path, text: str) -> None:
    """Schreibt UTF-8 atomar via temp-Datei + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path_str = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path_str, str(path))
    except Exception:
        try:
            os.unlink(tmp_path_str)
        except OSError:
            pass
        raise


def ensure_inventory_dirs(project_root: Path) -> None:
    root = get_discovery_v2_root(project_root)
    try:
        root.mkdir(parents=True, exist_ok=True)
        snapshots_dir(project_root).mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise InventoryArtifactError(
            f"Discovery-Ausgabewurzel `_otio_v2` konnte nicht erstellt werden: {exc}"
        ) from exc
    assert_path_is_under_discovery_v2(inventory_dir(project_root), project_root)
    assert_path_is_under_discovery_v2(snapshots_dir(project_root), project_root)


def save_snapshot(project_root: Path, snapshot: InventorySnapshot) -> Path:
    """Schreibt einen neuen Snapshot und aktualisiert den latest-Pointer."""
    ensure_inventory_dirs(project_root)
    target = snapshot_path(project_root, snapshot.scan_id)
    assert_path_is_under_discovery_v2(target, project_root)
    if target.exists():
        raise InventoryArtifactError(
            f"Snapshot existiert bereits und darf nicht überschrieben werden: {target}"
        )

    try:
        _atomic_write_text(
            target,
            snapshot.model_dump_json(indent=2),
        )
    except OSError as exc:
        raise InventoryArtifactError(
            f"Snapshot konnte nicht atomar geschrieben werden: {exc}"
        ) from exc

    pointer = InventoryLatestPointer(
        scan_id=snapshot.scan_id,
        created_at=snapshot.created_at,
        snapshot_relative_path=f"inventory/snapshots/{snapshot.scan_id}.json",
    )
    latest = latest_pointer_path(project_root)
    assert_path_is_under_discovery_v2(latest, project_root)
    try:
        _atomic_write_text(latest, pointer.model_dump_json(indent=2))
    except OSError as exc:
        raise InventoryArtifactError(
            f"latest.json konnte nicht atomar geschrieben werden: {exc}"
        ) from exc
    return target


def load_snapshot(path: Path) -> InventorySnapshot:
    try:
        raw = path.read_text(encoding="utf-8")
        return InventorySnapshot.model_validate_json(raw)
    except FileNotFoundError as exc:
        raise InventoryArtifactError(f"Snapshot nicht gefunden: {path}") from exc
    except Exception as exc:
        raise InventoryArtifactError(
            f"Snapshot ungültig oder beschädigt: {path}"
        ) from exc


def load_latest_snapshot(
    project_root: Path,
) -> tuple[InventorySnapshot | None, str | None]:
    """Lädt den letzten Snapshot.

    Returns:
        (snapshot, warning) — warning gesetzt bei beschädigtem latest.json.
    """
    latest = latest_pointer_path(project_root)
    if not latest.exists():
        return None, None
    try:
        pointer = InventoryLatestPointer.model_validate_json(
            latest.read_text(encoding="utf-8")
        )
    except Exception:
        return None, (
            "Die Datei `latest.json` ist beschädigt oder ungültig. "
            "Bitte eine neue Bestandsaufnahme starten."
        )

    snap = get_discovery_v2_root(project_root) / pointer.snapshot_relative_path
    # Fallback: bekannter Snapshot-Pfad aus scan_id
    if not snap.exists():
        snap = snapshot_path(project_root, pointer.scan_id)
    if not snap.exists():
        return None, (
            f"Der letzte Snapshot (`{pointer.scan_id}`) wurde nicht gefunden. "
            "Bitte eine neue Bestandsaufnahme starten."
        )
    try:
        return load_snapshot(snap), None
    except InventoryArtifactError as exc:
        return None, str(exc)
