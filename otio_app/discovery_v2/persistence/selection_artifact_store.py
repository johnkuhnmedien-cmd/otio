"""Atomische JSON-Speicherung bestätigter Discovery-V2-Auswahlen."""

from __future__ import annotations

from pathlib import Path

from otio_app.discovery_v2.domain.selection import (
    InventorySelection,
    SelectionLatestPointer,
)
from otio_app.discovery_v2.paths import (
    assert_path_is_under_discovery_v2,
    get_discovery_v2_root,
)
from otio_app.discovery_v2.persistence.inventory_artifact_store import (
    InventoryArtifactError,
    _atomic_write_text,
    ensure_inventory_dirs,
    inventory_dir,
)


def selections_dir(project_root: Path) -> Path:
    return inventory_dir(project_root) / "selections"


def selection_path(project_root: Path, selection_id: str) -> Path:
    return selections_dir(project_root) / f"{selection_id}.json"


def selection_latest_pointer_path(project_root: Path) -> Path:
    return inventory_dir(project_root) / "selection_latest.json"


def ensure_selection_dirs(project_root: Path) -> None:
    ensure_inventory_dirs(project_root)
    try:
        selections_dir(project_root).mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise InventoryArtifactError(
            f"Auswahl-Verzeichnis konnte nicht erstellt werden: {exc}"
        ) from exc
    assert_path_is_under_discovery_v2(selections_dir(project_root), project_root)


def save_selection(project_root: Path, selection: InventorySelection) -> Path:
    """Schreibt eine neue Bestätigung und aktualisiert den Pointer."""
    ensure_selection_dirs(project_root)
    target = selection_path(project_root, selection.selection_id)
    assert_path_is_under_discovery_v2(target, project_root)
    if target.exists():
        raise InventoryArtifactError(
            f"Auswahl existiert bereits und darf nicht überschrieben werden: {target}"
        )

    try:
        _atomic_write_text(target, selection.model_dump_json(indent=2))
    except OSError as exc:
        raise InventoryArtifactError(
            f"Auswahl konnte nicht atomar geschrieben werden: {exc}"
        ) from exc

    pointer = SelectionLatestPointer(
        selection_id=selection.selection_id,
        scan_id=selection.scan_id,
        created_at=selection.created_at,
        confirmed_at=selection.confirmed_at,
        selection_relative_path=f"inventory/selections/{selection.selection_id}.json",
    )
    latest = selection_latest_pointer_path(project_root)
    assert_path_is_under_discovery_v2(latest, project_root)
    try:
        _atomic_write_text(latest, pointer.model_dump_json(indent=2))
    except OSError as exc:
        raise InventoryArtifactError(
            f"selection_latest.json konnte nicht atomar geschrieben werden: {exc}"
        ) from exc
    return target


def load_selection(path: Path) -> InventorySelection:
    try:
        return InventorySelection.model_validate_json(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise InventoryArtifactError(f"Auswahl nicht gefunden: {path}") from exc
    except Exception as exc:
        raise InventoryArtifactError(
            f"Auswahl ungültig oder beschädigt: {path}"
        ) from exc


def load_latest_selection(
    project_root: Path,
) -> tuple[InventorySelection | None, str | None]:
    """Lädt die letzte bestätigte Auswahl.

    Returns:
        (selection, warning)
    """
    latest = selection_latest_pointer_path(project_root)
    if not latest.exists():
        return None, None
    try:
        pointer = SelectionLatestPointer.model_validate_json(
            latest.read_text(encoding="utf-8")
        )
    except Exception:
        return None, (
            "Die Datei `selection_latest.json` ist beschädigt oder ungültig. "
            "Bitte die Medienauswahl erneut bestätigen."
        )

    path = get_discovery_v2_root(project_root) / pointer.selection_relative_path
    if not path.exists():
        path = selection_path(project_root, pointer.selection_id)
    if not path.exists():
        return None, (
            f"Die letzte Auswahl (`{pointer.selection_id}`) wurde nicht gefunden. "
            "Bitte die Medienauswahl erneut bestätigen."
        )
    try:
        return load_selection(path), None
    except InventoryArtifactError as exc:
        return None, str(exc)
