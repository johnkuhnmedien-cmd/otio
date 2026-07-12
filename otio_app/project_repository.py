"""CRUD-Operationen für Projekte."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from otio_app.database import get_connection
from otio_app.models import (
    Project,
    ProjectCreate,
    ProjectMode,
    ProjectStatus,
    validate_asset_selection,
)
from otio_app.project_layout import scan_project_structure


def _normalize_language(language: str) -> str:
    return (language or "de").strip().lower() or "de"


def _parse_json_string_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if isinstance(data, list):
        return [str(item) for item in data]
    return []


def _row_to_project(row: sqlite3.Row) -> Project:
    asset_subdir_names = _parse_json_string_list(row["asset_subdir_names"])
    selected_asset_subdirs = _parse_json_string_list(row["selected_asset_subdirs"])
    if not selected_asset_subdirs and asset_subdir_names:
        selected_asset_subdirs = list(asset_subdir_names)

    row_keys = row.keys()
    project_mode = ProjectMode(row["project_mode"]) if "project_mode" in row_keys else ProjectMode.WITH_VOICEOVER

    return Project(
        id=row["id"],
        name=row["name"],
        project_root=row["project_root"],
        work_dir=row["work_dir"],
        project_mode=project_mode,
        voice_over_subdir=row["voice_over_subdir"],
        language=row["language"],
        frames_per_shot=row["frames_per_shot"],
        fps=row["fps"],
        width=row["width"],
        height=row["height"],
        aspect_ratio=row["aspect_ratio"],
        target_platform=row["target_platform"],
        status=ProjectStatus(row["status"]),
        asset_subdir_names=asset_subdir_names,
        selected_asset_subdirs=selected_asset_subdirs,
        notes=row["notes"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def find_projects_by_root(
    project_root: str | Path,
    db_path: Path | None = None,
) -> list[Project]:
    """Alle DB-Projekte mit demselben Projektroot (beliebige Sprachen)."""
    try:
        root = str(Path(project_root).expanduser().resolve())
    except OSError:
        root = str(Path(str(project_root)).expanduser())
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM projects WHERE project_root = ? ORDER BY created_at DESC",
            (root,),
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_project(row) for row in rows]


def find_project_by_root_and_language(
    project_root: str | Path,
    language: str,
    db_path: Path | None = None,
) -> Project | None:
    """Findet ein Projekt mit gleichem Root und (case-insensitive) gleicher Sprache."""
    try:
        root = str(Path(project_root).expanduser().resolve())
    except OSError:
        root = str(Path(str(project_root)).expanduser())
    lang = _normalize_language(language)
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            """
            SELECT * FROM projects
            WHERE project_root = ? AND lower(language) = ?
            LIMIT 1
            """,
            (root, lang),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return _row_to_project(row)


def create_project(
    data: ProjectCreate,
    db_path: Path | None = None,
    asset_subdir_names: list[str] | None = None,
    selected_asset_subdirs: list[str] | None = None,
) -> Project:
    """Legt ein neues Projekt in der Datenbank an."""
    # Sprache normalisieren, damit de/DE nicht doppelt angelegt werden.
    data = data.model_copy(update={"language": _normalize_language(data.language)})

    existing = find_project_by_root_and_language(
        data.project_root,
        data.language,
        db_path=db_path,
    )
    if existing is not None:
        raise ValueError(
            f"Am Projektordner gibt es bereits ein Projekt in Sprache "
            f"„{existing.language}“ (Name: {existing.name}). "
            "Für dieselbe Sprache bitte das bestehende Projekt öffnen — "
            "für eine andere Sprache ein neues Projekt mit anderer Sprache anlegen."
        )

    if asset_subdir_names is None:
        scan = scan_project_structure(
            data.project_root_path,
            data.work_dir_path,
            data.voice_over_subdir,
            data.language,
        )
        asset_subdir_names = scan.asset_subdir_names
    if selected_asset_subdirs is None:
        selected_asset_subdirs = list(asset_subdir_names)
    else:
        selected_asset_subdirs = validate_asset_selection(
            asset_subdir_names,
            selected_asset_subdirs,
        )

    project = Project.from_create(
        data,
        asset_subdir_names,
        selected_asset_subdirs,
    )
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO projects (
                id, name, project_root, work_dir, project_mode, voice_over_subdir,
                language, frames_per_shot, fps, width, height, aspect_ratio,
                target_platform, status, asset_subdir_names,
                selected_asset_subdirs, notes, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project.id,
                project.name,
                project.project_root,
                project.work_dir,
                project.project_mode.value,
                project.voice_over_subdir,
                project.language,
                project.frames_per_shot,
                project.fps,
                project.width,
                project.height,
                project.aspect_ratio,
                project.target_platform,
                project.status.value,
                json.dumps(project.asset_subdir_names, ensure_ascii=False),
                json.dumps(project.selected_asset_subdirs, ensure_ascii=False),
                project.notes,
                project.created_at.isoformat(),
                project.updated_at.isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    # Language-Scope `_otio/{LANG}/` sofort anlegen (auch wenn _otio schon existiert).
    from otio_app.services.language_scope import ensure_language_scope

    ensure_language_scope(project)
    return project


def list_projects(db_path: Path | None = None) -> list[Project]:
    """Gibt alle Projekte sortiert nach Erstellungsdatum zurück."""
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM projects ORDER BY created_at DESC"
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_project(row) for row in rows]


def get_project_by_id(
    project_id: str,
    db_path: Path | None = None,
) -> Project | None:
    """Lädt ein Projekt anhand der ID."""
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM projects WHERE id = ?",
            (project_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return _row_to_project(row)


def update_project_selection(
    project_id: str,
    selected_asset_subdirs: list[str],
    db_path: Path | None = None,
) -> Project:
    """Aktualisiert die ausgewählten Asset-Ordner."""
    project = get_project_by_id(project_id, db_path=db_path)
    if project is None:
        raise ValueError(f"Projekt nicht gefunden: {project_id}")

    validated = validate_asset_selection(
        project.asset_subdir_names,
        selected_asset_subdirs,
    )
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            UPDATE projects
            SET selected_asset_subdirs = ?, updated_at = ?
            WHERE id = ?
            """,
            (json.dumps(validated, ensure_ascii=False), now, project_id),
        )
        conn.commit()
    finally:
        conn.close()
    updated = get_project_by_id(project_id, db_path=db_path)
    assert updated is not None
    return updated


def update_project_status(
    project_id: str,
    status: ProjectStatus,
    db_path: Path | None = None,
) -> Project:
    """Setzt den Projektstatus."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE projects SET status = ?, updated_at = ? WHERE id = ?",
            (status.value, now, project_id),
        )
        conn.commit()
    finally:
        conn.close()
    updated = get_project_by_id(project_id, db_path=db_path)
    if updated is None:
        raise ValueError(f"Projekt nicht gefunden: {project_id}")
    return updated
