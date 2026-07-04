"""CRUD-Operationen für Projekte."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from otio_app.database import get_connection
from otio_app.models import Project, ProjectCreate, ProjectStatus


def _row_to_project(row: sqlite3.Row) -> Project:
    return Project(
        id=row["id"],
        name=row["name"],
        project_root=row["project_root"],
        work_dir=row["work_dir"],
        voice_over_subdir=row["voice_over_subdir"],
        language=row["language"],
        frames_per_shot=row["frames_per_shot"],
        fps=row["fps"],
        width=row["width"],
        height=row["height"],
        aspect_ratio=row["aspect_ratio"],
        target_platform=row["target_platform"],
        status=ProjectStatus(row["status"]),
        notes=row["notes"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def create_project(
    data: ProjectCreate,
    db_path: Path | None = None,
) -> Project:
    """Legt ein neues Projekt in der Datenbank an."""
    project = Project.from_create(data)
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO projects (
                id, name, project_root, work_dir, voice_over_subdir,
                language, frames_per_shot, fps, width, height, aspect_ratio,
                target_platform, status, notes, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project.id,
                project.name,
                project.project_root,
                project.work_dir,
                project.voice_over_subdir,
                project.language,
                project.frames_per_shot,
                project.fps,
                project.width,
                project.height,
                project.aspect_ratio,
                project.target_platform,
                project.status.value,
                project.notes,
                project.created_at.isoformat(),
                project.updated_at.isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()
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
