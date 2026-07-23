"""Persistenz für Supplement Requests und Kandidaten."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from otio_app.analysis_models import SupplementCandidate, SupplementRequest, SupplementRequestsDocument
from otio_app.models import Project
from otio_app.project_layout import get_supplement_requests_path


def load_supplement_requests(project: Project) -> SupplementRequestsDocument:
    path = get_supplement_requests_path(project.language_work_dir_path)
    if not path.is_file():
        return SupplementRequestsDocument(project_id=project.id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return SupplementRequestsDocument.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return SupplementRequestsDocument(project_id=project.id)


def save_supplement_requests(project: Project, document: SupplementRequestsDocument) -> Path:
    path = get_supplement_requests_path(project.language_work_dir_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document.model_dump_json(indent=2), encoding="utf-8")
    return path


def upsert_requests(
    project: Project,
    requests: list[SupplementRequest],
) -> SupplementRequestsDocument:
    document = load_supplement_requests(project)
    by_id = {entry.supplement_request_id: entry for entry in document.requests}
    for request in requests:
        by_id[request.supplement_request_id] = request
    merged = SupplementRequestsDocument(
        project_id=project.id,
        requests=sorted(by_id.values(), key=lambda entry: entry.supplement_request_id),
        candidates=document.candidates,
    )
    save_supplement_requests(project, merged)
    return merged


def add_candidates(
    project: Project,
    candidates: list[SupplementCandidate],
) -> SupplementRequestsDocument:
    document = load_supplement_requests(project)
    existing = {entry.candidate_id: entry for entry in document.candidates}
    for candidate in candidates:
        existing[candidate.candidate_id] = candidate
    merged = SupplementRequestsDocument(
        project_id=project.id,
        requests=document.requests,
        candidates=sorted(existing.values(), key=lambda entry: entry.candidate_id),
    )
    save_supplement_requests(project, merged)
    return merged


def update_request(
    project: Project,
    request_id: str,
    **updates,
) -> SupplementRequest | None:
    document = load_supplement_requests(project)
    updated_request: SupplementRequest | None = None
    new_requests: list[SupplementRequest] = []
    for request in document.requests:
        if request.supplement_request_id != request_id:
            new_requests.append(request)
            continue
        updated_request = request.model_copy(
            update={**updates, "updated_at": datetime.now(timezone.utc)}
        )
        new_requests.append(updated_request)
    if updated_request is None:
        return None
    save_supplement_requests(
        project,
        SupplementRequestsDocument(
            project_id=project.id,
            requests=new_requests,
            candidates=document.candidates,
        ),
    )
    return updated_request


def requests_for_folder(document: SupplementRequestsDocument, folder_name: str) -> list[SupplementRequest]:
    return [entry for entry in document.requests if entry.folder_name == folder_name]


def pending_supplement_count(document: SupplementRequestsDocument) -> int:
    return sum(
        1
        for entry in document.requests
        if entry.status
        not in {"INVENTORY_UPDATED", "READY_FOR_REPLAN", "CANCELLED"}
    )
