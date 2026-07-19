"""Script Lock für without_voiceover_enhanced."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

from otio_app.models import Project
from otio_app.services.without_voiceover_enhanced.io_utils import load_model, write_json
from otio_app.services.without_voiceover_enhanced.models import (
    EnhancedScriptDocument,
    ScriptSegment,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    script_draft_path,
    script_locked_path,
)
from otio_app.services.without_voiceover_enhanced.script_prompts import FORBIDDEN_PHRASES


class ScriptLockError(RuntimeError):
    pass


def _next_version(current: str | None) -> str:
    if not current:
        return "script-v1"
    match = re.fullmatch(r"script-v(\d+)", current.strip())
    if not match:
        return "script-v1"
    return f"script-v{int(match.group(1)) + 1}"


def load_script_draft(project: Project) -> EnhancedScriptDocument | None:
    return load_model(script_draft_path(project), EnhancedScriptDocument)


def load_locked_script(project: Project) -> EnhancedScriptDocument | None:
    doc = load_model(script_locked_path(project), EnhancedScriptDocument)
    if doc is None:
        return None
    if doc.script_status != "locked":
        return None
    return doc


def require_locked_script(project: Project) -> EnhancedScriptDocument:
    doc = load_locked_script(project)
    if doc is None:
        raise ScriptLockError("Kein gesperrtes Skript vorhanden (script_locked.json).")
    return doc


def save_script_draft(project: Project, document: EnhancedScriptDocument) -> Path:
    document.script_status = "draft"
    return write_json(script_draft_path(project), document)


def detect_forbidden_phrases(text: str) -> list[str]:
    found: list[str] = []
    for phrase in FORBIDDEN_PHRASES:
        if phrase.lower() in text.lower():
            found.append(phrase)
    return found


def lock_script(project: Project, document: EnhancedScriptDocument | None = None) -> EnhancedScriptDocument:
    """Sperrt das Skript mit eindeutiger Version."""
    draft = document or load_script_draft(project)
    if draft is None:
        raise ScriptLockError("Kein Skript-Draft zum Sperren vorhanden.")
    if not draft.segments:
        raise ScriptLockError("Skript enthält keine Segmente.")
    forbidden = detect_forbidden_phrases(draft.narration_full)
    draft.forbidden_phrases_found = forbidden

    previous = load_model(script_locked_path(project), EnhancedScriptDocument)
    version = _next_version(previous.script_version if previous else None)
    draft.script_version = version
    draft.script_status = "locked"
    draft.locked_at = datetime.now(timezone.utc).isoformat()
    write_json(script_locked_path(project), draft)
    write_json(script_draft_path(project), draft)
    return draft


def mark_segment_text_changed(
    project: Project,
    segment_id: str,
    new_text: str,
) -> EnhancedScriptDocument:
    """Textänderung an gesperrtem Skript → Segment geändert, Version bleibt bis Relock.

    Audio muss als stale markiert werden (Aufrufer / audio_timing_service).
    """
    locked = require_locked_script(project)
    found = False
    updated_segments: list[ScriptSegment] = []
    for segment in locked.segments:
        if segment.segment_id == segment_id:
            if segment.text != new_text:
                segment = segment.model_copy(
                    update={"text": new_text, "text_changed": True}
                )
            found = True
        updated_segments.append(segment)
    if not found:
        raise ScriptLockError(f"Unbekannte Segment-ID: {segment_id}")
    locked.segments = updated_segments
    locked.narration_full = " ".join(s.text for s in updated_segments)
    locked.script_status = "draft"  # must re-lock after edits
    write_json(script_draft_path(project), locked)
    # Invalidate lock file so ElevenLabs cannot silently use old text.
    if script_locked_path(project).is_file():
        script_locked_path(project).unlink()
    return locked


def content_fingerprint(document: EnhancedScriptDocument) -> str:
    payload = "|".join(f"{s.segment_id}:{s.text}" for s in document.segments)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
