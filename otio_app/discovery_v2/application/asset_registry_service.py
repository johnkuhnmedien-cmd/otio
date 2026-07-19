"""Application-Service: bestätigte Auswahl in die Asset Registry importieren."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from otio_app.defaults import DEFAULT_DISCOVERY_V2_WORK_SUBDIR, DEFAULT_WORK_SUBDIR
from otio_app.discovery_v2.application.inventory_service import (
    InventoryServiceError,
    get_latest_inventory,
    require_discovery_project,
)
from otio_app.discovery_v2.application.selection_service import (
    effective_selection_status,
    get_latest_confirmed_selection,
)
from otio_app.discovery_v2.domain.asset_registry import (
    RegistryImportRecord,
    RegistryImportReport,
    RegistryImportResult,
    RegistryImportStatus,
)
from otio_app.discovery_v2.domain.inventory import (
    InventoryFileEntry,
    InventorySnapshot,
    MediaKind,
    ScanStatus,
)
from otio_app.discovery_v2.domain.selection import (
    InventorySelection,
    SelectionStatus,
)
from otio_app.discovery_v2.persistence.asset_registry_database import (
    RegistryDatabaseError,
    registry_sqlite_path,
)
from otio_app.discovery_v2.persistence.asset_registry_repository import (
    build_report_from_import,
    find_import_by_selection_id,
    import_report_path,
    insert_import_membership,
    insert_selection_import,
    list_assets_for_project,
    list_import_memberships,
    open_registry,
    save_import_report,
    upsert_asset,
)
from otio_app.discovery_v2.persistence.inventory_artifact_store import (
    InventoryArtifactError,
)
from otio_app.discovery_v2.persistence.selection_artifact_store import (
    selection_path,
)
from otio_app.models import Project


class AssetRegistryServiceError(InventoryServiceError):
    """Fachlicher Registry-Fehler."""


def _mtime_iso_from_timestamp(st_mtime: float) -> str:
    return datetime.fromtimestamp(st_mtime, tz=timezone.utc).isoformat()


def _path_is_under_reserved(relative_path: str) -> str | None:
    parts = Path(relative_path).parts
    if not parts:
        return "leerer Pfad"
    if parts[0] == DEFAULT_WORK_SUBDIR or DEFAULT_WORK_SUBDIR in parts:
        return f"Pfad liegt unter `{DEFAULT_WORK_SUBDIR}/`"
    if parts[0] == DEFAULT_DISCOVERY_V2_WORK_SUBDIR or DEFAULT_DISCOVERY_V2_WORK_SUBDIR in parts:
        return f"Pfad liegt unter `{DEFAULT_DISCOVERY_V2_WORK_SUBDIR}/`"
    return None


def _validate_selected_file(
    project_root: Path,
    entry: InventoryFileEntry,
) -> int:
    """Prüft leichte FS-Metadaten. Returns mtime_ns."""
    reserved = _path_is_under_reserved(entry.relative_path)
    if reserved:
        raise AssetRegistryServiceError(reserved)

    root = project_root.expanduser().resolve()
    raw = root / entry.relative_path
    if raw.is_symlink():
        raise AssetRegistryServiceError(
            f"Ausgewählter Pfad ist keine reguläre Datei: {entry.relative_path}"
        )
    if not raw.exists():
        raise AssetRegistryServiceError(
            f"Ausgewählte Datei fehlt: {entry.relative_path}. "
            "Bitte Bestandsaufnahme und Auswahl erneut durchführen."
        )
    try:
        path = raw.resolve(strict=True)
        path.relative_to(root)
    except (OSError, ValueError) as exc:
        raise AssetRegistryServiceError(
            f"Pfad außerhalb des Projektroots: {entry.relative_path}"
        ) from exc

    # Absolute Lage unter reservierten Wurzeln ablehnen.
    classic_root = root / DEFAULT_WORK_SUBDIR
    v2_root = root / DEFAULT_DISCOVERY_V2_WORK_SUBDIR
    for banned, label in ((classic_root, DEFAULT_WORK_SUBDIR), (v2_root, DEFAULT_DISCOVERY_V2_WORK_SUBDIR)):
        try:
            path.relative_to(banned)
        except ValueError:
            pass
        else:
            raise AssetRegistryServiceError(f"Pfad liegt unter `{label}/`")

    if not path.is_file() or path.is_symlink():
        raise AssetRegistryServiceError(
            f"Ausgewählter Pfad ist keine reguläre Datei: {entry.relative_path}"
        )
    try:
        stat = path.stat()
    except OSError as exc:
        raise AssetRegistryServiceError(
            f"Metadaten nicht lesbar: {entry.relative_path} ({exc})"
        ) from exc

    if int(stat.st_size) != int(entry.size_bytes):
        raise AssetRegistryServiceError(
            f"Dateigröße hat sich geändert: {entry.relative_path}. "
            "Bitte Bestandsaufnahme und Auswahl erneut durchführen."
        )
    current_iso = _mtime_iso_from_timestamp(stat.st_mtime)
    if entry.mtime_iso and current_iso != entry.mtime_iso:
        raise AssetRegistryServiceError(
            f"Änderungszeit hat sich geändert: {entry.relative_path}. "
            "Bitte Bestandsaufnahme und Auswahl erneut durchführen."
        )
    return int(stat.st_mtime_ns)


def _snapshot_entry_map(snapshot: InventorySnapshot) -> dict[str, InventoryFileEntry]:
    return {
        f.relative_path: f
        for f in snapshot.files
        if f.scan_status == ScanStatus.FOUND
    }


def can_import_selection(
    project: Project,
    snapshot: InventorySnapshot | None = None,
    selection: InventorySelection | None = None,
    status: SelectionStatus | None = None,
) -> tuple[bool, str | None, RegistryImportStatus | None]:
    """Prüft Importvoraussetzungen ohne Seiteneffekte."""
    try:
        require_discovery_project(project)
    except InventoryServiceError as exc:
        return False, str(exc), RegistryImportStatus.FAILED

    if snapshot is None:
        snapshot, snap_warn = get_latest_inventory(project)
        if snap_warn and snapshot is None:
            return False, snap_warn, RegistryImportStatus.FAILED
    if snapshot is None:
        return False, "Kein Inventory-Snapshot vorhanden.", RegistryImportStatus.FAILED

    if selection is None or status is None:
        selection, status, sel_warn = get_latest_confirmed_selection(
            project, current_scan_id=snapshot.scan_id
        )
        if sel_warn and selection is None:
            return False, sel_warn, RegistryImportStatus.FAILED
    if selection is None:
        return False, "Bestätige zuerst deine Medienauswahl.", RegistryImportStatus.FAILED

    if status == SelectionStatus.STALE or selection.scan_id != snapshot.scan_id:
        return (
            False,
            "Die bestätigte Auswahl gehört zu einer älteren Bestandsaufnahme. "
            "Bitte prüfe und bestätige den aktuellen Bestand erneut.",
            RegistryImportStatus.STALE_SELECTION,
        )
    if selection.selected_media_count <= 0 or not selection.selected_relative_paths:
        return False, "Die Auswahl enthält keine Medien.", RegistryImportStatus.FAILED
    return True, None, None


def import_confirmed_selection(project: Project) -> RegistryImportResult:
    """Importiert die aktuelle bestätigte Auswahl transaktional in die Registry."""
    project = require_discovery_project(project)
    snapshot, snap_warn = get_latest_inventory(project)
    if snapshot is None:
        raise AssetRegistryServiceError(
            snap_warn or "Kein Inventory-Snapshot vorhanden."
        )

    selection, status, sel_warn = get_latest_confirmed_selection(
        project, current_scan_id=snapshot.scan_id
    )
    if selection is None:
        raise AssetRegistryServiceError(
            sel_warn or "Bestätige zuerst deine Medienauswahl."
        )
    if status == SelectionStatus.STALE or selection.scan_id != snapshot.scan_id:
        return RegistryImportResult(
            status=RegistryImportStatus.STALE_SELECTION,
            message=(
                "Die bestätigte Auswahl gehört zu einer älteren Bestandsaufnahme. "
                "Bitte prüfe und bestätige den aktuellen Bestand erneut."
            ),
            selection_id=selection.selection_id,
            scan_id=selection.scan_id,
        )

    if selection.selected_media_count <= 0 or not selection.selected_relative_paths:
        raise AssetRegistryServiceError("Die Auswahl enthält keine Medien.")

    entries_by_path = _snapshot_entry_map(snapshot)
    validated: list[tuple[InventoryFileEntry, int]] = []
    for rel in selection.selected_relative_paths:
        entry = entries_by_path.get(rel)
        if entry is None:
            raise AssetRegistryServiceError(
                f"Ausgewählter Pfad stammt nicht aus dem Snapshot: {rel}"
            )
        if entry.media_kind == MediaKind.OTHER:
            raise AssetRegistryServiceError(
                f"Sonstige Dateien dürfen nicht importiert werden: {rel}"
            )
        mtime_ns = _validate_selected_file(project.project_root_path, entry)
        validated.append((entry, mtime_ns))

    selection_rel = f"inventory/selections/{selection.selection_id}.json"
    # Existenz der Selection-Datei prüfen (Artefakt)
    if not selection_path(project.project_root_path, selection.selection_id).is_file():
        raise AssetRegistryServiceError(
            f"Selection-Artefakt fehlt: {selection_rel}"
        )

    try:
        conn = open_registry(project.project_root_path)
    except RegistryDatabaseError as exc:
        raise AssetRegistryServiceError(str(exc)) from exc

    try:
        existing = find_import_by_selection_id(
            conn, project_id=project.id, selection_id=selection.selection_id
        )
        if existing is not None:
            report_path = import_report_path(project.project_root_path, existing.import_id)
            if not report_path.exists():
                report = build_report_from_import(
                    conn,
                    project_root=project.project_root_path,
                    import_record=existing,
                )
                try:
                    save_import_report(project.project_root_path, report)
                except InventoryArtifactError as exc:
                    raise AssetRegistryServiceError(
                        f"Importbericht konnte nicht repariert werden: {exc}"
                    ) from exc
            else:
                from otio_app.discovery_v2.persistence.asset_registry_repository import (
                    load_import_report,
                )

                report = load_import_report(report_path)
            memberships = list_import_memberships(conn, import_id=existing.import_id)
            return RegistryImportResult(
                status=RegistryImportStatus.ALREADY_IMPORTED,
                message=(
                    "Diese Auswahl wurde bereits in die Asset Registry übernommen. "
                    "Es wurden keine neuen Datensätze erzeugt."
                ),
                import_id=existing.import_id,
                selection_id=existing.selection_id,
                scan_id=existing.scan_id,
                asset_count=len(memberships),
                new_asset_count=0,
                reused_asset_count=len(memberships),
                report=report,
            )

        now = datetime.now(timezone.utc)
        import_id = str(uuid4())
        new_count = 0
        reused_count = 0
        asset_ids: list[tuple[str, str]] = []

        try:
            conn.execute("BEGIN")
            for entry, mtime_ns in validated:
                asset, created = upsert_asset(
                    conn,
                    project_id=project.id,
                    source_relative_path=entry.relative_path,
                    source_group=entry.source_group,
                    file_name=entry.filename,
                    extension=entry.extension,
                    media_kind=entry.media_kind,
                    size_bytes=entry.size_bytes,
                    mtime_ns=mtime_ns,
                    now=now,
                )
                if created:
                    new_count += 1
                else:
                    reused_count += 1
                asset_ids.append((asset.asset_id, entry.relative_path))

            import_record = RegistryImportRecord(
                import_id=import_id,
                project_id=project.id,
                selection_id=selection.selection_id,
                scan_id=selection.scan_id,
                source_selection_relative_path=selection_rel,
                imported_at=now,
                status=RegistryImportStatus.IMPORTED,
                selected_asset_count=len(asset_ids),
            )
            insert_selection_import(conn, import_record)
            for asset_id, rel in asset_ids:
                insert_import_membership(
                    conn,
                    import_id=import_id,
                    asset_id=asset_id,
                    source_relative_path=rel,
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

        report = RegistryImportReport(
            import_id=import_id,
            project_id=project.id,
            selection_id=selection.selection_id,
            scan_id=selection.scan_id,
            imported_at=now,
            status=RegistryImportStatus.IMPORTED,
            asset_count=len(asset_ids),
            new_asset_count=new_count,
            reused_asset_count=reused_count,
            source_groups=sorted({e.source_group for e, _ in validated}),
            media_kind_counts={
                kind.value: sum(1 for e, _ in validated if e.media_kind == kind)
                for kind in (MediaKind.VIDEO, MediaKind.IMAGE, MediaKind.AUDIO)
                if any(e.media_kind == kind for e, _ in validated)
            },
            registry_sqlite_relative_path="registry/assets.sqlite3",
            source_selection_relative_path=selection_rel,
        )
        try:
            save_import_report(project.project_root_path, report)
        except InventoryArtifactError as exc:
            # SQLite ist Wahrheit — Wiederholung repariert den Bericht.
            return RegistryImportResult(
                status=RegistryImportStatus.IMPORTED,
                message=(
                    "Registry-Import in SQLite erfolgreich, aber der JSON-Bericht "
                    f"konnte nicht geschrieben werden: {exc}. "
                    "Bitte den Import erneut auslösen (idempotent)."
                ),
                import_id=import_id,
                selection_id=selection.selection_id,
                scan_id=selection.scan_id,
                asset_count=len(asset_ids),
                new_asset_count=new_count,
                reused_asset_count=reused_count,
                report=report,
            )

        return RegistryImportResult(
            status=RegistryImportStatus.IMPORTED,
            message="Auswahl erfolgreich in die Asset Registry übernommen.",
            import_id=import_id,
            selection_id=selection.selection_id,
            scan_id=selection.scan_id,
            asset_count=len(asset_ids),
            new_asset_count=new_count,
            reused_asset_count=reused_count,
            report=report,
        )
    finally:
        conn.close()


def get_registry_summary(project: Project) -> dict:
    """Kleine Zusammenfassung für die UI."""
    project = require_discovery_project(project)
    db_path = registry_sqlite_path(project.project_root_path)
    if not db_path.exists():
        return {
            "exists": False,
            "asset_count": 0,
            "registry_sqlite_relative_path": "registry/assets.sqlite3",
        }
    conn = open_registry(project.project_root_path)
    try:
        assets = list_assets_for_project(conn, project_id=project.id)
        return {
            "exists": True,
            "asset_count": len(assets),
            "registry_sqlite_relative_path": "registry/assets.sqlite3",
            "source_groups": sorted({a.source_group for a in assets}),
        }
    finally:
        conn.close()
