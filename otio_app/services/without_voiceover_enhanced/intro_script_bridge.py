"""Bridge: bestätigter Intro-Hook → Enhanced Locked-Script Segment.

Die klassische Intro-Bestätigung liegt unter
`intro_hook.confirmed.json`. Enhanced Audio/Cut/Timeline erwarten Intro
als Kapitel-Segment im gesperrten Skript (`folder_name="Intro"`).
"""

from __future__ import annotations

from otio_app.models import Project
from otio_app.services.voiceover_generation.intro_hook_service import (
    load_confirmed_intro_hook,
)
from otio_app.services.without_voiceover_enhanced.io_utils import write_json
from otio_app.services.without_voiceover_enhanced.models import (
    EnhancedScriptDocument,
    ScriptSegment,
    VisualIntent,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    script_draft_path,
    script_locked_path,
)
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    load_locked_script,
    load_script_draft,
)

ENHANCED_INTRO_FOLDER_NAME = "Intro"
ENHANCED_INTRO_SEGMENT_ID = "Intro_segment_001"


def is_intro_folder_name(name: str | None) -> bool:
    slug = (name or "").strip().casefold()
    return slug in {"intro", "introduction"} or slug.startswith("intro_")


def confirmed_intro_text(project: Project) -> str | None:
    hook = load_confirmed_intro_hook(project)
    if hook is None:
        return None
    text = (hook.hook_text or "").strip()
    return text or None


def _rebuild_narration_full(document: EnhancedScriptDocument) -> None:
    document.narration_full = " ".join(
        seg.text.strip() for seg in document.segments if seg.text.strip()
    )


def _renumber_sequence_indices(document: EnhancedScriptDocument) -> None:
    for index, segment in enumerate(document.segments, start=1):
        segment.sequence_index = index


def _merge_intro_visual_intents(
    document: EnhancedScriptDocument,
    project: Project,
) -> bool:
    """Übernimmt Intro-Visual-Beats als VisualIntents (idempotent per intent_id)."""
    hook = load_confirmed_intro_hook(project)
    if hook is None or not hook.visual_beats:
        return False
    existing_ids = {intent.intent_id for intent in document.visual_intents}
    changed = False
    for beat in hook.visual_beats:
        intent_id = f"intro_{beat.hook_beat_id}"
        if intent_id in existing_ids:
            continue
        description = (beat.visual_intent or beat.text or beat.hook_beat_id).strip()
        if not description:
            continue
        document.visual_intents.append(
            VisualIntent(
                intent_id=intent_id,
                description=description,
                subject="",
                location=beat.source_folder_name or "",
                preferred_media_type="video",
                folder_name=ENHANCED_INTRO_FOLDER_NAME,
            )
        )
        existing_ids.add(intent_id)
        changed = True
    return changed


def ensure_confirmed_intro_in_document(
    project: Project,
    document: EnhancedScriptDocument,
) -> bool:
    """Fügt/aktualisiert das Intro-Segment im Dokument. True wenn geändert."""
    text = confirmed_intro_text(project)
    if text is None:
        return False

    changed = False
    intro_segments = [
        seg for seg in document.segments if is_intro_folder_name(seg.folder_name)
    ]
    if intro_segments:
        primary = intro_segments[0]
        if primary.text.strip() != text:
            primary.text = text
            primary.text_changed = True
            changed = True
        if primary.folder_name != ENHANCED_INTRO_FOLDER_NAME:
            primary.folder_name = ENHANCED_INTRO_FOLDER_NAME
            changed = True
        if primary.folder_order_index != 0:
            primary.folder_order_index = 0
            changed = True
        # Canonical segment id if still using a generic intro name.
        if primary.segment_id != ENHANCED_INTRO_SEGMENT_ID and primary.segment_id.startswith(
            "Intro"
        ):
            # Keep existing id to avoid orphaning already synthesized audio.
            pass
        # Drop duplicate intro folders/segments beyond the first.
        if len(intro_segments) > 1:
            keep_id = primary.segment_id
            document.segments = [
                seg
                for seg in document.segments
                if not is_intro_folder_name(seg.folder_name) or seg.segment_id == keep_id
            ]
            changed = True
        # Ensure Intro segment is first.
        if document.segments and document.segments[0].segment_id != primary.segment_id:
            document.segments = [
                seg for seg in document.segments if seg.segment_id != primary.segment_id
            ]
            document.segments.insert(0, primary)
            changed = True
    else:
        document.segments.insert(
            0,
            ScriptSegment(
                segment_id=ENHANCED_INTRO_SEGMENT_ID,
                text=text,
                sequence_index=0,
                semantic_function="intro_hook",
                folder_name=ENHANCED_INTRO_FOLDER_NAME,
                folder_order_index=0,
            ),
        )
        changed = True

    if _merge_intro_visual_intents(document, project):
        changed = True

    if changed:
        _renumber_sequence_indices(document)
        _rebuild_narration_full(document)
    return changed


def ensure_confirmed_intro_in_locked_script(
    project: Project,
) -> EnhancedScriptDocument | None:
    """Synchronisiert bestätigtes Intro in das gesperrte Skript (und Draft-Spiegel)."""
    locked = load_locked_script(project)
    if locked is None:
        return None
    if not ensure_confirmed_intro_in_document(project, locked):
        return locked
    write_json(script_locked_path(project), locked)
    draft = load_script_draft(project)
    if draft is None or draft.script_version == locked.script_version:
        write_json(script_draft_path(project), locked)
    return locked
