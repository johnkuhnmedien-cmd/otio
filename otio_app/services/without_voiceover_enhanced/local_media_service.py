"""Manuelle lokale Dateizuordnung für akzeptierte Stock-Supplements (R1)."""

from __future__ import annotations

from pathlib import Path

from otio_app.models import Project
from otio_app.services.media_utils import probe_duration_seconds
from otio_app.services.without_voiceover_enhanced.io_utils import load_model, write_json
from otio_app.services.without_voiceover_enhanced.models import (
    AcceptedSupplementsDocument,
    StockCandidate,
)
from otio_app.services.without_voiceover_enhanced.paths import accepted_supplements_path

STATUS_SELECTED = "selected"
STATUS_LOCAL_MEDIA_MISSING = "local_media_missing"
STATUS_LOCAL_MEDIA_INVALID = "local_media_invalid"
STATUS_EXPORT_READY = "export_ready"


class LocalMediaError(RuntimeError):
    pass


def is_http_url(value: str | None) -> bool:
    text = (value or "").strip().lower()
    return text.startswith("http://") or text.startswith("https://")


def validate_local_media_path(raw_path: str | None) -> tuple[str, str | None]:
    """Gibt (status, error) zurück."""
    if raw_path is None or not str(raw_path).strip():
        return STATUS_LOCAL_MEDIA_MISSING, "Keine lokale Mediendatei zugeordnet."
    text = str(raw_path).strip()
    if is_http_url(text):
        return (
            STATUS_LOCAL_MEDIA_INVALID,
            "local_media_path darf keine HTTP/HTTPS-URL sein.",
        )
    path = Path(text).expanduser()
    if not path.is_file():
        return STATUS_LOCAL_MEDIA_MISSING, f"Lokale Datei existiert nicht: {path}"
    # Technical readability: must be openable as binary; prefer media probe when possible.
    try:
        with path.open("rb") as handle:
            handle.read(16)
    except OSError as exc:
        return STATUS_LOCAL_MEDIA_INVALID, f"Lokale Datei technisch unlesbar: {exc}"
    # Optional duration probe — failure for still images is OK if file is readable.
    _ = probe_duration_seconds(path)
    return STATUS_EXPORT_READY, None


def refresh_supplement_validation(candidate: StockCandidate) -> StockCandidate:
    if not candidate.selected and not candidate.local_media_path:
        candidate.media_validation_status = STATUS_SELECTED
        candidate.media_validation_error = None
        return candidate
    if not candidate.local_media_path:
        candidate.media_validation_status = STATUS_LOCAL_MEDIA_MISSING
        candidate.media_validation_error = (
            f"Supplement {candidate.candidate_id} besitzt keine validierte lokale "
            "Mediendatei. Ordne zuerst eine lokale Originaldatei zu."
        )
        return candidate
    status, error = validate_local_media_path(candidate.local_media_path)
    candidate.media_validation_status = status
    candidate.media_validation_error = error
    return candidate


def assign_local_media_path(
    project: Project,
    candidate_id: str,
    local_media_path: str,
) -> StockCandidate:
    accepted = load_model(accepted_supplements_path(project), AcceptedSupplementsDocument)
    if accepted is None:
        raise LocalMediaError("Keine akzeptierten Supplements vorhanden.")
    updated: StockCandidate | None = None
    for index, candidate in enumerate(accepted.supplements):
        if candidate.candidate_id != candidate_id:
            continue
        candidate.local_media_path = str(local_media_path).strip()
        candidate.selected = True
        candidate = refresh_supplement_validation(candidate)
        accepted.supplements[index] = candidate
        updated = candidate
        break
    if updated is None:
        raise LocalMediaError(f"Unbekanntes Supplement: {candidate_id}")
    write_json(accepted_supplements_path(project), accepted)
    return updated


def list_export_ready_supplements(project: Project) -> list[StockCandidate]:
    accepted = load_model(accepted_supplements_path(project), AcceptedSupplementsDocument)
    if accepted is None:
        return []
    ready: list[StockCandidate] = []
    changed = False
    for index, candidate in enumerate(accepted.supplements):
        refreshed = refresh_supplement_validation(candidate)
        if refreshed.model_dump() != candidate.model_dump():
            accepted.supplements[index] = refreshed
            changed = True
        if refreshed.media_validation_status == STATUS_EXPORT_READY:
            ready.append(refreshed)
    if changed:
        write_json(accepted_supplements_path(project), accepted)
    return ready


def require_export_ready_local_path(candidate: StockCandidate) -> str:
    refreshed = refresh_supplement_validation(candidate)
    if refreshed.media_validation_status != STATUS_EXPORT_READY:
        raise LocalMediaError(
            refreshed.media_validation_error
            or (
                f"Supplement {candidate.candidate_id} besitzt keine validierte "
                "lokale Mediendatei. Ordne zuerst eine lokale Originaldatei zu."
            )
        )
    path = str(refreshed.local_media_path or "").strip()
    if is_http_url(path):
        raise LocalMediaError(
            f"Supplement {candidate.candidate_id}: lokale Mediendatei darf keine Web-URL sein."
        )
    return path
