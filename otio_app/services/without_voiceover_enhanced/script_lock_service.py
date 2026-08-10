"""Script Lock für without_voiceover_enhanced."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

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
from otio_app.services.without_voiceover_enhanced.script_chapter_text import (
    AUTHOR_PAUSE_MARKER_RE,
    ChapterDisplayTextError,
    canonicalize_script_document_to_pause_blocks,
    chapter_display_text,
    join_spoken_segment_texts,
    normalize_author_pause_seconds,
    parse_chapter_display_text,
    strip_author_pause_markers_from_text,
)
from otio_app.services.without_voiceover_enhanced.script_prompts import FORBIDDEN_PHRASES


class ScriptLockError(RuntimeError):
    pass


def _normalize_document_pause_markers(document: EnhancedScriptDocument) -> None:
    """Pause-Blöcke kanonisieren; narration bereinigen.

    Feingranulare LLM-Segmente ohne Autorenpause werden zu Kapitelblöcken
    zusammengeführt. Verbleibende Inline-Marker (z. B. Intro-Fließtext) bleiben
    in segment.text und gehen beim TTS an eleven_v3.
    """
    canonicalize_script_document_to_pause_blocks(document)


def _validate_author_pauses_for_lock(document: EnhancedScriptDocument) -> None:
    """Prüft Autorenpausen und Roundtrip der sichtbaren Darstellung."""
    for segment in document.segments:
        try:
            normalize_author_pause_seconds(segment.author_pause_after_seconds)
        except ChapterDisplayTextError as exc:
            raise ScriptLockError(
                f"{segment.segment_id}: {exc}"
            ) from exc
    # narration_full muss frei von Produktionsmarkern sein (gesprochener Text).
    if AUTHOR_PAUSE_MARKER_RE.search(document.narration_full or ""):
        document.narration_full = strip_author_pause_markers_from_text(
            document.narration_full or ""
        )
    if AUTHOR_PAUSE_MARKER_RE.search(document.narration_full or ""):
        raise ScriptLockError(
            "Pausemarker dürfen nicht in narration_full stehen."
        )
    # Roundtrip nur für Kapitel ohne verbleibende Inline-Marker in segment.text.
    folders = sorted(
        {seg.folder_name for seg in document.segments if seg.folder_name}
    )
    for folder_name in folders:
        segs = [
            seg
            for seg in document.segments
            if seg.folder_name == folder_name and (seg.text or "").strip()
        ]
        if not any(float(seg.author_pause_after_seconds or 0.0) > 0 for seg in segs):
            continue
        if any(AUTHOR_PAUSE_MARKER_RE.search(seg.text or "") for seg in segs):
            continue
        rendered = chapter_display_text(segs)
        try:
            parsed = parse_chapter_display_text(
                rendered,
                folder_name=folder_name,
                folder_order_index=segs[0].folder_order_index if segs else 0,
                segment_id_prefix="roundtrip",
            )
        except ChapterDisplayTextError as exc:
            raise ScriptLockError(
                f"Kapitel „{folder_name}“: Pausendarstellung ungültig — {exc}"
            ) from exc
        orig_texts = [seg.text.strip() for seg in segs]
        parsed_texts = [seg.text.strip() for seg in parsed]
        if orig_texts != parsed_texts:
            raise ScriptLockError(
                f"Kapitel „{folder_name}“: Spoken-Text Roundtrip fehlgeschlagen."
            )
        orig_pauses = [
            float(seg.author_pause_after_seconds or 0.0) for seg in segs
        ]
        parsed_pauses = [
            float(seg.author_pause_after_seconds or 0.0) for seg in parsed
        ]
        if orig_pauses != parsed_pauses:
            raise ScriptLockError(
                f"Kapitel „{folder_name}“: Autorenpausen Roundtrip fehlgeschlagen."
            )


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
    # Bestätigtes Intro (Schritt ⑤) in das Locked-Script übernehmen, falls vorhanden.
    from otio_app.services.without_voiceover_enhanced.intro_script_bridge import (
        ensure_confirmed_intro_in_document,
    )

    ensure_confirmed_intro_in_document(project, draft)
    if not draft.segments:
        raise ScriptLockError("Skript enthält keine Segmente.")
    _normalize_document_pause_markers(draft)
    _validate_author_pauses_for_lock(draft)
    draft.narration_full = join_spoken_segment_texts(draft.segments)
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


def _invalidate_lock_keep_draft(document: EnhancedScriptDocument, project: Project) -> EnhancedScriptDocument:
    document.narration_full = join_spoken_segment_texts(document.segments)
    document.script_status = "draft"
    document.forbidden_phrases_found = detect_forbidden_phrases(document.narration_full)
    write_json(script_draft_path(project), document)
    if script_locked_path(project).is_file():
        script_locked_path(project).unlink()
    return document


def mark_segment_text_changed(
    project: Project,
    segment_id: str,
    new_text: str,
) -> EnhancedScriptDocument:
    """Textänderung → Draft speichern, Script Lock aufheben (Audio stale)."""
    draft = load_script_draft(project) or load_locked_script(project)
    if draft is None:
        raise ScriptLockError("Kein Skript zum Bearbeiten vorhanden.")
    found = False
    updated_segments: list[ScriptSegment] = []
    for segment in draft.segments:
        if segment.segment_id == segment_id:
            if segment.text != new_text:
                segment = segment.model_copy(
                    update={"text": new_text, "text_changed": True}
                )
            found = True
        updated_segments.append(segment)
    if not found:
        raise ScriptLockError(f"Unbekannte Segment-ID: {segment_id}")
    draft.segments = updated_segments
    return _invalidate_lock_keep_draft(draft, project)


def update_folder_chapter_narration(
    project: Project,
    folder_name: str,
    new_text: str,
) -> EnhancedScriptDocument:
    """Ersetzt das Kapitel-Skript aus sichtbarem Text inkl. Autorenpausen-Markern."""
    draft = load_script_draft(project) or load_locked_script(project)
    if draft is None:
        raise ScriptLockError("Kein Skript zum Bearbeiten vorhanden.")
    text = (new_text or "").strip()
    if not text:
        raise ScriptLockError("Kapitel-Text darf nicht leer sein.")

    keep = [seg for seg in draft.segments if seg.folder_name != folder_name]
    old = [seg for seg in draft.segments if seg.folder_name == folder_name]
    if not old:
        raise ScriptLockError(f"Kein Skript für Kapitel „{folder_name}“ vorhanden.")

    order_index = old[0].folder_order_index
    intent_ids: list[str] = []
    for seg in old:
        for intent_id in seg.visual_intent_ids:
            if intent_id not in intent_ids:
                intent_ids.append(intent_id)

    from otio_app.project_layout import safe_folder_slug

    slug = safe_folder_slug(folder_name)
    try:
        replacements = parse_chapter_display_text(
            text,
            folder_name=folder_name,
            folder_order_index=order_index,
            segment_id_prefix=f"{slug}_segment",
            default_semantic_function=old[0].semantic_function or "narration",
        )
    except ChapterDisplayTextError as exc:
        raise ScriptLockError(str(exc)) from exc

    if replacements:
        first = replacements[0]
        replacements[0] = first.model_copy(
            update={
                "visual_intent_ids": intent_ids,
                "fact_check_required": any(seg.fact_check_required for seg in old),
            }
        )

    draft.segments = keep + replacements
    draft.segments.sort(
        key=lambda seg: (seg.folder_order_index, seg.sequence_index, seg.segment_id)
    )
    for index, segment in enumerate(draft.segments, start=1):
        segment.sequence_index = index
    return _invalidate_lock_keep_draft(draft, project)


def content_fingerprint(document: EnhancedScriptDocument) -> str:
    payload = "|".join(
        f"{s.segment_id}:{s.text}:{float(s.author_pause_after_seconds or 0.0):.2f}"
        for s in document.segments
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
