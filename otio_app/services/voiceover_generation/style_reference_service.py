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
from otio_app.services.voiceover_generation.models import (
    STYLE_MODE_CHOICES,
    STYLE_MODE_PROFILE,
    STYLE_MODE_RAW_TEXT,
    VoiceoverStyleProfile,
    VoiceoverStyleReferences,
)

ALLOWED_UPLOAD_EXTENSIONS = (".txt", ".md")
MAX_UPLOAD_CHARS = 20_000
MAX_RAW_REFERENCE_CHARS = 40_000


def default_style_references(project: Project) -> VoiceoverStyleReferences:
    return VoiceoverStyleReferences(project_id=project.id)


def normalize_style_mode(style_mode: str | None) -> str:
    mode = (style_mode or STYLE_MODE_PROFILE).strip().lower()
    if mode in STYLE_MODE_CHOICES:
        return mode
    return STYLE_MODE_PROFILE


def is_raw_style_mode(refs: VoiceoverStyleReferences) -> bool:
    return normalize_style_mode(refs.style_mode) == STYLE_MODE_RAW_TEXT


def format_raw_style_reference_for_prompts(raw_text: str) -> str:
    text = (raw_text or "").strip()
    if not text:
        return (
            "(kein Raw-Style-Text hinterlegt — neutraler dokumentarischer Standardstil)"
        )
    return (
        "RAW STYLE REFERENCE — use only as style inspiration; "
        "do not copy wording or sentences verbatim:\n"
        f"{text}"
    )


def format_style_profile_summary_for_prompts(
    style_profile: VoiceoverStyleProfile | None,
) -> str:
    if style_profile is None:
        return "(kein Style Profile vorhanden — neutraler dokumentarischer Standardstil)"
    return (
        f"- overall_tone: {style_profile.overall_tone or '-'}\n"
        f"- narration_style: {style_profile.narration_style or '-'}\n"
        f"- sentence_length: {style_profile.sentence_length or '-'}\n"
        f"- pacing: {style_profile.pacing or '-'}\n"
        f"- imagery_style: {style_profile.imagery_style or '-'}\n"
        f"- segment_style: {style_profile.segment_style or '-'}\n"
        f"- style_summary_for_prompts: {style_profile.style_summary_for_prompts or '-'}"
    )


def style_context_text_for_prompts(
    project: Project,
    *,
    detailed: bool = False,
) -> str:
    """Textblock für LLM-Prompts: Raw-Referenz oder Style Profile."""
    refs = load_style_references(project)
    if is_raw_style_mode(refs):
        return format_raw_style_reference_for_prompts(refs.raw_reference_text)

    from otio_app.services.voiceover_generation.style_profile_service import (
        load_style_profile,
    )

    profile = load_style_profile(project)
    if detailed:
        if profile is None:
            return "(kein Style Profile)"
        return profile.model_dump_json(indent=2)
    return format_style_profile_summary_for_prompts(profile)


def load_style_references(project: Project) -> VoiceoverStyleReferences:
    path = get_voiceover_style_references_path(project.language_work_dir_path)
    if not path.is_file():
        return default_style_references(project)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        refs = VoiceoverStyleReferences.model_validate(payload)
        return refs.model_copy(update={"style_mode": normalize_style_mode(refs.style_mode)})
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
    raw_text = refs.raw_reference_text or ""
    if len(raw_text) > MAX_RAW_REFERENCE_CHARS:
        raw_text = raw_text[:MAX_RAW_REFERENCE_CHARS]
    normalized = refs.model_copy(
        update={
            "project_id": project.id,
            "generated_at": datetime.now(timezone.utc),
            "style_mode": normalize_style_mode(refs.style_mode),
            "raw_reference_text": raw_text,
        }
    )
    path = get_voiceover_style_references_path(project.language_work_dir_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(normalized.model_dump_json(indent=2), encoding="utf-8")

    if normalized.uploaded_file_names:
        uploads_dir = get_style_references_uploads_dir(project.language_work_dir_path)
        uploads_dir.mkdir(parents=True, exist_ok=True)
        for index, (name, text) in enumerate(
            zip(normalized.uploaded_file_names, normalized.uploaded_file_texts), start=1
        ):
            safe_name = _safe_upload_filename(index, name)
            (uploads_dir / safe_name).write_text(text, encoding="utf-8")

    return normalized
