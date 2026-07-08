"""Persistenz für Style-Referenzen (Beispielskripte) — Projekt ohne Voice-Over.

Nur Klartext (.txt/.md) wird unterstützt — keine PDF/DOCX-Verarbeitung.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from otio_app.models import Project
from otio_app.project_layout import (
    get_style_references_uploads_dir,
    get_voiceover_style_references_path,
)
from otio_app.services.voiceover_generation.models import VoiceoverStyleReferences

ALLOWED_UPLOAD_EXTENSIONS = (".txt", ".md")
MAX_UPLOAD_CHARS = 20_000


def default_style_references(project: Project) -> VoiceoverStyleReferences:
    return VoiceoverStyleReferences(project_id=project.id)


def load_style_references(project: Project) -> VoiceoverStyleReferences:
    path = get_voiceover_style_references_path(project.work_dir_path)
    if not path.is_file():
        return default_style_references(project)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return VoiceoverStyleReferences.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return default_style_references(project)


def is_allowed_upload_filename(filename: str) -> bool:
    """Nur .txt/.md — bewusst keine PDF/DOCX-Verarbeitung in Phase 2."""
    return filename.lower().endswith(ALLOWED_UPLOAD_EXTENSIONS)


def truncate_upload_text(text: str, *, max_chars: int = MAX_UPLOAD_CHARS) -> tuple[str, bool]:
    """Begrenzt sehr große Uploads. Gibt (text, wurde_gekuerzt) zurück."""
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars], True


def _safe_upload_filename(index: int, filename: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(filename).name) or f"upload_{index}"
    return f"{index:02d}_{stem}"


def save_style_references(
    project: Project,
    refs: VoiceoverStyleReferences,
) -> VoiceoverStyleReferences:
    """Speichert die konsolidierte JSON und zusätzlich jeden Upload als reine
    Textdatei unter style_references/uploads/ (Audit-Spur, keine Binärdaten)."""
    normalized = refs.model_copy(
        update={"project_id": project.id, "generated_at": datetime.now(timezone.utc)}
    )
    path = get_voiceover_style_references_path(project.work_dir_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(normalized.model_dump_json(indent=2), encoding="utf-8")

    if normalized.uploaded_file_names:
        uploads_dir = get_style_references_uploads_dir(project.work_dir_path)
        uploads_dir.mkdir(parents=True, exist_ok=True)
        for index, (name, text) in enumerate(
            zip(normalized.uploaded_file_names, normalized.uploaded_file_texts), start=1
        ):
            safe_name = _safe_upload_filename(index, name)
            (uploads_dir / safe_name).write_text(text, encoding="utf-8")

    return normalized
