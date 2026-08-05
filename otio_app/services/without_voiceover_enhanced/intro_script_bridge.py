"""Bridge: bestätigter Intro-Hook → Enhanced Locked-Script Segment.

Die klassische Intro-Bestätigung liegt unter
`intro_hook.confirmed.json`. Enhanced Audio/Cut/Timeline erwarten Intro
als Kapitel-Segment im gesperrten Skript (`folder_name="Intro"`).

Pausemarker im Hook-Text (`[pause N seconds]`) werden in strukturierte
`author_pause_after_seconds` überführt; beim TTS entstehen daraus eleven_v3-Tags.
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
from otio_app.services.without_voiceover_enhanced.script_chapter_text import (
    AUTHOR_PAUSE_MARKER_RE,
    ChapterDisplayTextError,
    join_spoken_segment_texts,
    parse_chapter_display_text,
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
    document.narration_full = join_spoken_segment_texts(document.segments)


def _renumber_sequence_indices(document: EnhancedScriptDocument) -> None:
    for index, segment in enumerate(document.segments, start=1):
        segment.sequence_index = index


def _intro_segments_from_text(text: str) -> list[ScriptSegment]:
    """Baut Intro-Segmente; zeilenweise Pausemarker → author_pause_after_seconds."""
    raw = (text or "").strip()
    if not raw:
        return []
    if AUTHOR_PAUSE_MARKER_RE.search(raw):
        try:
            parsed = parse_chapter_display_text(
                raw,
                folder_name=ENHANCED_INTRO_FOLDER_NAME,
                folder_order_index=0,
                segment_id_prefix="Intro_segment",
                default_semantic_function="intro_hook",
            )
            return [
                seg.model_copy(update={"text_changed": False})
                for seg in parsed
            ]
        except ChapterDisplayTextError:
            pass
    return [
        ScriptSegment(
            segment_id=ENHANCED_INTRO_SEGMENT_ID,
            text=raw,
            sequence_index=0,
            semantic_function="intro_hook",
            folder_name=ENHANCED_INTRO_FOLDER_NAME,
            folder_order_index=0,
        )
    ]


def _intro_segments_equivalent(
    existing: list[ScriptSegment],
    desired: list[ScriptSegment],
) -> bool:
    if len(existing) != len(desired):
        return False
    for left, right in zip(existing, desired):
        if (left.text or "").strip() != (right.text or "").strip():
            return False
        if float(left.author_pause_after_seconds or 0.0) != float(
            right.author_pause_after_seconds or 0.0
        ):
            return False
        if left.folder_name != ENHANCED_INTRO_FOLDER_NAME:
            return False
        if int(left.folder_order_index or 0) != 0:
            return False
    return True


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
    """Fügt/aktualisiert Intro-Segment(e) im Dokument. True wenn geändert."""
    text = confirmed_intro_text(project)
    if text is None:
        return False

    changed = False
    desired = _intro_segments_from_text(text)
    if not desired:
        return False

    intro_segments = [
        seg for seg in document.segments if is_intro_folder_name(seg.folder_name)
    ]
    non_intro = [
        seg for seg in document.segments if not is_intro_folder_name(seg.folder_name)
    ]

    if not _intro_segments_equivalent(intro_segments, desired):
        # Preserve first existing intro segment_id when still a single segment,
        # so already synthesized audio is not orphaned.
        if len(desired) == 1 and intro_segments:
            primary_id = intro_segments[0].segment_id
            desired[0] = desired[0].model_copy(update={"segment_id": primary_id})
        document.segments = [*desired, *non_intro]
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
