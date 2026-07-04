"""Manuelle Freigabe von Asset-Ordnern (Status grün ohne vollständige Analyse)."""

from __future__ import annotations

import json
from pathlib import Path

from otio_app.analysis_models import ManualFolderCompletionDocument
from otio_app.defaults import MANUAL_FOLDER_COMPLETION_FILENAME
from otio_app.models import Project


def get_manual_completion_path(work_dir: Path) -> Path:
    return work_dir / MANUAL_FOLDER_COMPLETION_FILENAME


def load_manual_completion(project: Project) -> ManualFolderCompletionDocument:
    path = get_manual_completion_path(project.work_dir_path)
    if not path.is_file():
        return ManualFolderCompletionDocument(project_id=project.id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        document = ManualFolderCompletionDocument.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return ManualFolderCompletionDocument(project_id=project.id)
    if document.project_id != project.id:
        return ManualFolderCompletionDocument(project_id=project.id)
    return document


def save_manual_completion(
    project: Project,
    document: ManualFolderCompletionDocument,
) -> None:
    path = get_manual_completion_path(project.work_dir_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document.model_dump_json(indent=2), encoding="utf-8")


def is_manually_complete(project: Project, folder_name: str) -> bool:
    document = load_manual_completion(project)
    return folder_name in document.folders


def set_manually_complete(
    project: Project,
    folder_name: str,
    *,
    complete: bool,
) -> None:
    document = load_manual_completion(project)
    folders = set(document.folders)
    if complete:
        folders.add(folder_name)
    else:
        folders.discard(folder_name)
    document.folders = sorted(folders, key=str.casefold)
    save_manual_completion(project, document)

    from otio_app.services.inventory_loader import sync_folder_inventory_with_status

    sync_folder_inventory_with_status(project, folder_name)
