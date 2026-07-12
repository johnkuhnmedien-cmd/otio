"""Schnelles Laden von Schnittplan-Status — ohne volle Shot-Validierung pro Seitenaufruf."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from otio_app.analysis_models import EditPlanDocument
from otio_app.models import Project
from otio_app.project_layout import get_edit_plan_dir, get_edit_plan_path, get_folder_edit_plan_path
from otio_app.services.edit_plan_builder import (
    EditPlanLocationState,
    EditPlanLocationStatus,
)


def _parse_generated_at(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None

_legacy_migrated_projects: set[str] = set()
_document_cache: dict[tuple[str, str], tuple[float, EditPlanDocument | None]] = {}
_meta_cache: dict[tuple[str, str], tuple[float, "EditPlanFolderMeta"]] = {}


@dataclass(frozen=True)
class EditPlanFolderMeta:
    folder_name: str
    confirmed: bool
    shot_count: int
    generated_at: str = ""

    @property
    def has_shots(self) -> bool:
        return self.shot_count > 0


def _read_edit_plan_file(path: Path) -> EditPlanDocument | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return EditPlanDocument.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None


def _migrate_legacy_edit_plan(project: Project) -> None:
    if project.id in _legacy_migrated_projects:
        return
    legacy_path = get_edit_plan_path(project.project_root_path)
    if legacy_path.is_file():
        document = _read_edit_plan_file(legacy_path)
        if document is not None and document.shots:
            by_folder: dict[str, list] = {}
            for shot in document.shots:
                by_folder.setdefault(shot.folder, []).append(shot)
            for folder_name, shots in by_folder.items():
                target = get_folder_edit_plan_path(project.language_work_dir_path, folder_name)
                if target.is_file():
                    continue
                folder_doc = document.model_copy(
                    update={"folder_name": folder_name, "shots": shots}
                )
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(folder_doc.model_dump_json(indent=2), encoding="utf-8")
            backup = legacy_path.with_suffix(".json.migrated")
            legacy_path.rename(backup)
    _legacy_migrated_projects.add(project.id)


def invalidate_edit_plan_cache(project_id: str, folder_name: str | None = None) -> None:
    for cache in (_document_cache, _meta_cache):
        keys = [
            key
            for key in cache
            if key[0] == project_id and (folder_name is None or key[1] == folder_name)
        ]
        for key in keys:
            del cache[key]


def load_edit_plan_folder_meta(project: Project, folder_name: str) -> EditPlanFolderMeta:
    """Nur confirmed + Shot-Anzahl — kein pydantic über alle Shots."""
    _migrate_legacy_edit_plan(project)
    path = get_folder_edit_plan_path(project.language_work_dir_path, folder_name)
    if not path.is_file():
        return EditPlanFolderMeta(folder_name=folder_name, confirmed=False, shot_count=0)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return EditPlanFolderMeta(folder_name=folder_name, confirmed=False, shot_count=0)

    cache_key = (project.id, folder_name)
    cached = _meta_cache.get(cache_key)
    if cached is not None and cached[0] == mtime:
        return cached[1]

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        shots = payload.get("shots") or []
        meta = EditPlanFolderMeta(
            folder_name=folder_name,
            confirmed=bool(payload.get("confirmed")),
            shot_count=len(shots) if isinstance(shots, list) else 0,
            generated_at=str(payload.get("generated_at") or ""),
        )
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        meta = EditPlanFolderMeta(folder_name=folder_name, confirmed=False, shot_count=0)

    _meta_cache[cache_key] = (mtime, meta)
    return meta


def load_edit_plan_cached(project: Project, folder_name: str) -> EditPlanDocument | None:
    _migrate_legacy_edit_plan(project)
    path = get_folder_edit_plan_path(project.language_work_dir_path, folder_name)
    if not path.is_file():
        return None
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None

    cache_key = (project.id, folder_name)
    cached = _document_cache.get(cache_key)
    if cached is not None and cached[0] == mtime:
        document = cached[1]
    else:
        document = _read_edit_plan_file(path)
        _document_cache[cache_key] = (mtime, document)

    if document is None:
        return None
    if document.folder_name is None:
        document = document.model_copy(update={"folder_name": folder_name})
    return document


def resolve_location_status_from_meta(
    folder_name: str,
    meta: EditPlanFolderMeta,
    draft: EditPlanDocument | None = None,
) -> EditPlanLocationStatus:
    """Bevorzugt den neueren Stand zwischen In-Session-Entwurf und Datei.

    Ein Session-Entwurf gewann bisher immer bedingungslos gegenüber der
    gespeicherten Datei — dadurch zeigte die Orts-Auswahl einen veralteten
    Shot-Count/Status an, sobald z. B. der Supplement-Assets-Workflow einen
    frischeren Schnittplan direkt auf die Festplatte geschrieben hatte,
    während im Browser noch ein älterer Entwurf im session_state lag.
    """
    if draft is not None:
        meta_generated_at = _parse_generated_at(meta.generated_at)
        draft_generated_at = draft.generated_at
        prefer_meta = (
            meta.has_shots
            and meta_generated_at is not None
            and draft_generated_at is not None
            and meta_generated_at > draft_generated_at
        )
        if prefer_meta:
            draft = None

    if draft is not None:
        effective = draft
        if not effective.shots:
            return EditPlanLocationStatus(folder_name=folder_name, state=EditPlanLocationState.OPEN)
        if effective.confirmed:
            return EditPlanLocationStatus(
                folder_name=folder_name,
                state=EditPlanLocationState.CONFIRMED,
                shot_count=len(effective.shots),
            )
        return EditPlanLocationStatus(
            folder_name=folder_name,
            state=EditPlanLocationState.DRAFT,
            shot_count=len(effective.shots),
        )
    if not meta.has_shots:
        return EditPlanLocationStatus(folder_name=folder_name, state=EditPlanLocationState.OPEN)
    if meta.confirmed:
        return EditPlanLocationStatus(
            folder_name=folder_name,
            state=EditPlanLocationState.CONFIRMED,
            shot_count=meta.shot_count,
        )
    return EditPlanLocationStatus(
        folder_name=folder_name,
        state=EditPlanLocationState.DRAFT,
        shot_count=meta.shot_count,
    )


def collect_folder_statuses(
    project: Project,
    project_id: str,
    mapped_folders: list[str],
    *,
    get_draft,
) -> list[EditPlanLocationStatus]:
    return [
        resolve_location_status_from_meta(
            folder_name,
            load_edit_plan_folder_meta(project, folder_name),
            get_draft(project_id, folder_name),
        )
        for folder_name in mapped_folders
    ]


def mapped_folders_all_confirmed(project: Project, folder_names: list[str]) -> bool:
    if not folder_names:
        return False
    return all(
        (meta := load_edit_plan_folder_meta(project, folder_name)).confirmed and meta.has_shots
        for folder_name in folder_names
    )


def count_confirmed_folders(project: Project, folder_names: list[str]) -> int:
    return sum(
        1
        for folder_name in folder_names
        if load_edit_plan_folder_meta(project, folder_name).confirmed
    )
