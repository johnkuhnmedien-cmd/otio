"""Repository-Operationen für Discovery-V2-Asset-Registry (SQLite)."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from otio_app.discovery_v2.domain.asset_registry import (
    RegistryAssetRecord,
    RegistryImportRecord,
    RegistryImportStatus,
)
from otio_app.discovery_v2.domain.inventory import MediaKind
from otio_app.discovery_v2.persistence.asset_registry_database import (
    RegistryDatabaseError,
    get_registry_connection,
)
from otio_app.discovery_v2.persistence.inventory_artifact_store import (
    InventoryArtifactError,
    _atomic_write_text,
)
from otio_app.discovery_v2.paths import (
    assert_path_is_under_discovery_v2,
    get_discovery_v2_root,
)
from otio_app.discovery_v2.domain.asset_registry import (
    RegistryImportLatestPointer,
    RegistryImportReport,
)


def imports_dir(project_root: Path) -> Path:
    from otio_app.discovery_v2.persistence.asset_registry_database import registry_dir

    return registry_dir(project_root) / "imports"


def import_report_path(project_root: Path, import_id: str) -> Path:
    return imports_dir(project_root) / f"{import_id}.json"


def latest_import_pointer_path(project_root: Path) -> Path:
    from otio_app.discovery_v2.persistence.asset_registry_database import registry_dir

    return registry_dir(project_root) / "latest_import.json"


def find_asset_by_relative_path(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    source_relative_path: str,
) -> RegistryAssetRecord | None:
    row = conn.execute(
        """
        SELECT * FROM assets
        WHERE project_id = ? AND source_relative_path = ?
        """,
        (project_id, source_relative_path),
    ).fetchone()
    return None if row is None else _row_to_asset(row)


def list_assets_for_project(
    conn: sqlite3.Connection, *, project_id: str
) -> list[RegistryAssetRecord]:
    rows = conn.execute(
        "SELECT * FROM assets WHERE project_id = ? ORDER BY source_relative_path",
        (project_id,),
    ).fetchall()
    return [_row_to_asset(row) for row in rows]


def find_import_by_selection_id(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    selection_id: str,
) -> RegistryImportRecord | None:
    row = conn.execute(
        """
        SELECT * FROM selection_imports
        WHERE project_id = ? AND selection_id = ?
        """,
        (project_id, selection_id),
    ).fetchone()
    return None if row is None else _row_to_import(row)


def list_import_memberships(
    conn: sqlite3.Connection, *, import_id: str
) -> list[tuple[str, str]]:
    rows = conn.execute(
        """
        SELECT asset_id, source_relative_path
        FROM selection_import_assets
        WHERE import_id = ?
        ORDER BY source_relative_path
        """,
        (import_id,),
    ).fetchall()
    return [(str(r["asset_id"]), str(r["source_relative_path"])) for r in rows]


def upsert_asset(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    source_relative_path: str,
    source_group: str,
    file_name: str,
    extension: str,
    media_kind: MediaKind,
    size_bytes: int,
    mtime_ns: int,
    now: datetime,
) -> tuple[RegistryAssetRecord, bool]:
    """Insert oder Update. Returns (record, created_new)."""
    existing = find_asset_by_relative_path(
        conn, project_id=project_id, source_relative_path=source_relative_path
    )
    if existing is not None:
        conn.execute(
            """
            UPDATE assets
            SET source_group = ?, file_name = ?, extension = ?, media_kind = ?,
                size_bytes = ?, mtime_ns = ?, updated_at = ?
            WHERE asset_id = ?
            """,
            (
                source_group,
                file_name,
                extension,
                media_kind.value,
                size_bytes,
                mtime_ns,
                now.isoformat(),
                existing.asset_id,
            ),
        )
        updated = find_asset_by_relative_path(
            conn, project_id=project_id, source_relative_path=source_relative_path
        )
        assert updated is not None
        return updated, False

    asset_id = str(uuid4())
    conn.execute(
        """
        INSERT INTO assets (
            asset_id, project_id, source_relative_path, source_group,
            file_name, extension, media_kind, size_bytes, mtime_ns,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            asset_id,
            project_id,
            source_relative_path,
            source_group,
            file_name,
            extension,
            media_kind.value,
            size_bytes,
            mtime_ns,
            now.isoformat(),
            now.isoformat(),
        ),
    )
    created = find_asset_by_relative_path(
        conn, project_id=project_id, source_relative_path=source_relative_path
    )
    assert created is not None
    return created, True


def insert_selection_import(
    conn: sqlite3.Connection,
    record: RegistryImportRecord,
) -> None:
    conn.execute(
        """
        INSERT INTO selection_imports (
            import_id, project_id, selection_id, scan_id,
            source_selection_relative_path, imported_at, status, selected_asset_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record.import_id,
            record.project_id,
            record.selection_id,
            record.scan_id,
            record.source_selection_relative_path,
            record.imported_at.isoformat(),
            record.status.value,
            record.selected_asset_count,
        ),
    )


def insert_import_membership(
    conn: sqlite3.Connection,
    *,
    import_id: str,
    asset_id: str,
    source_relative_path: str,
) -> None:
    conn.execute(
        """
        INSERT INTO selection_import_assets (import_id, asset_id, source_relative_path)
        VALUES (?, ?, ?)
        """,
        (import_id, asset_id, source_relative_path),
    )


def save_import_report(project_root: Path, report: RegistryImportReport) -> Path:
    from otio_app.discovery_v2.persistence.asset_registry_database import ensure_registry_dir

    ensure_registry_dir(project_root)
    try:
        imports_dir(project_root).mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise InventoryArtifactError(
            f"Importbericht-Verzeichnis nicht beschreibbar: {exc}"
        ) from exc

    target = import_report_path(project_root, report.import_id)
    assert_path_is_under_discovery_v2(target, project_root)
    if target.exists():
        # Idempotente Wiederholung: Inhalt ersetzen nur wenn Pointer-Reparatur
        # denselben Import schreibt — historische IDs werden nicht überschrieben
        # mit anderem Inhalt. Für Reparatur denselben Report erneut schreiben.
        pass
    try:
        _atomic_write_text(target, report.model_dump_json(indent=2))
    except OSError as exc:
        raise InventoryArtifactError(
            f"Importbericht konnte nicht atomar geschrieben werden: {exc}"
        ) from exc

    pointer = RegistryImportLatestPointer(
        import_id=report.import_id,
        selection_id=report.selection_id,
        scan_id=report.scan_id,
        imported_at=report.imported_at,
        status=report.status,
        report_relative_path=f"registry/imports/{report.import_id}.json",
    )
    latest = latest_import_pointer_path(project_root)
    assert_path_is_under_discovery_v2(latest, project_root)
    try:
        _atomic_write_text(latest, pointer.model_dump_json(indent=2))
    except OSError as exc:
        raise InventoryArtifactError(
            f"latest_import.json konnte nicht atomar geschrieben werden: {exc}"
        ) from exc
    return target


def load_import_report(path: Path) -> RegistryImportReport:
    try:
        return RegistryImportReport.model_validate_json(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise InventoryArtifactError(f"Importbericht nicht gefunden: {path}") from exc
    except Exception as exc:
        raise InventoryArtifactError(
            f"Importbericht ungültig oder beschädigt: {path}"
        ) from exc


def load_latest_import_report(
    project_root: Path,
) -> tuple[RegistryImportReport | None, str | None]:
    latest = latest_import_pointer_path(project_root)
    if not latest.exists():
        return None, None
    try:
        pointer = RegistryImportLatestPointer.model_validate_json(
            latest.read_text(encoding="utf-8")
        )
    except Exception:
        return None, (
            "Die Datei `latest_import.json` ist beschädigt oder ungültig."
        )
    path = get_discovery_v2_root(project_root) / pointer.report_relative_path
    if not path.exists():
        path = import_report_path(project_root, pointer.import_id)
    if not path.exists():
        return None, (
            f"Importbericht `{pointer.import_id}` wurde nicht gefunden."
        )
    try:
        return load_import_report(path), None
    except InventoryArtifactError as exc:
        return None, str(exc)


def build_report_from_import(
    conn: sqlite3.Connection,
    *,
    project_root: Path,
    import_record: RegistryImportRecord,
    new_asset_count: int = 0,
    reused_asset_count: int = 0,
) -> RegistryImportReport:
    memberships = list_import_memberships(conn, import_id=import_record.import_id)
    kind_counts: dict[str, int] = {}
    groups: set[str] = set()
    for asset_id, _rel in memberships:
        row = conn.execute(
            "SELECT media_kind, source_group FROM assets WHERE asset_id = ?",
            (asset_id,),
        ).fetchone()
        if row is None:
            continue
        kind = str(row["media_kind"])
        kind_counts[kind] = kind_counts.get(kind, 0) + 1
        groups.add(str(row["source_group"]))
    return RegistryImportReport(
        import_id=import_record.import_id,
        project_id=import_record.project_id,
        selection_id=import_record.selection_id,
        scan_id=import_record.scan_id,
        imported_at=import_record.imported_at,
        status=import_record.status,
        asset_count=import_record.selected_asset_count,
        new_asset_count=new_asset_count,
        reused_asset_count=reused_asset_count,
        source_groups=sorted(groups),
        media_kind_counts=kind_counts,
        registry_sqlite_relative_path="registry/assets.sqlite3",
        source_selection_relative_path=import_record.source_selection_relative_path,
    )


def open_registry(project_root: Path) -> sqlite3.Connection:
    try:
        return get_registry_connection(project_root)
    except RegistryDatabaseError:
        raise


def _row_to_asset(row: sqlite3.Row) -> RegistryAssetRecord:
    return RegistryAssetRecord(
        asset_id=str(row["asset_id"]),
        project_id=str(row["project_id"]),
        source_relative_path=str(row["source_relative_path"]),
        source_group=str(row["source_group"]),
        file_name=str(row["file_name"]),
        extension=str(row["extension"]),
        media_kind=MediaKind(str(row["media_kind"])),
        size_bytes=int(row["size_bytes"]),
        mtime_ns=int(row["mtime_ns"]),
        created_at=datetime.fromisoformat(str(row["created_at"])),
        updated_at=datetime.fromisoformat(str(row["updated_at"])),
    )


def _row_to_import(row: sqlite3.Row) -> RegistryImportRecord:
    return RegistryImportRecord(
        import_id=str(row["import_id"]),
        project_id=str(row["project_id"]),
        selection_id=str(row["selection_id"]),
        scan_id=str(row["scan_id"]),
        source_selection_relative_path=str(row["source_selection_relative_path"]),
        imported_at=datetime.fromisoformat(str(row["imported_at"])),
        status=RegistryImportStatus(str(row["status"])),
        selected_asset_count=int(row["selected_asset_count"]),
    )
