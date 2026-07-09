"""Audio-Alignment: ElevenLabs-Character-Timestamps auf die bestehende
Satz-/Beat-Struktur mappen (Phase 6 §7/§8).

Wichtig: Das Alignment erhält die bestehende Struktur aus den bestätigten
Voice-over-/Intro-Dokumenten. Es erfindet keine neuen Sätze und rät keine
neue Asset-Zuordnung — primary_asset_id, backup_asset_ids, visual_intent und
needs_supplement_asset werden 1:1 übernommen.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from otio_app.defaults import (
    ALIGNMENT_WARNING_AUDIO_DURATION_MISMATCH,
    ALIGNMENT_WARNING_EMPTY_SEGMENT_TEXT,
    ALIGNMENT_WARNING_MISSING_CHARACTER_TIMESTAMPS,
    ALIGNMENT_WARNING_NON_MONOTONIC_TIMESTAMPS,
    ALIGNMENT_WARNING_TEXT_SEGMENT_NOT_FOUND,
    ALIGNMENT_WARNING_USED_PROPORTIONAL_FALLBACK,
    AUDIO_SCOPE_FOLDER,
    AUDIO_SCOPE_INTRO,
)
from otio_app.models import Project
from otio_app.project_layout import get_folder_alignment_path, get_intro_alignment_path
from otio_app.services.voiceover_generation.dramaturgy_service import load_confirmed_dramaturgy
from otio_app.services.voiceover_generation.intro_hook_service import load_confirmed_intro_hook
from otio_app.services.voiceover_generation.models import (
    AlignmentItem,
    VoiceoverAlignment,
    VoiceoverAudioItem,
)
from otio_app.services.voiceover_generation.text_segment_matching import (
    build_normalized_index_map,
    find_segment_span,
)
from otio_app.services.voiceover_generation.voiceover_author_service import (
    load_folder_voiceovers_confirmed,
)

__all__ = [
    "build_folder_alignment",
    "build_intro_alignment",
    "save_alignment",
    "load_alignment",
]


def _resolve_order_index(project: Project, folder_name: str) -> int:
    plan = load_confirmed_dramaturgy(project)
    if plan is None:
        return 0
    entry = next((e for e in plan.recommended_folder_order if e.folder_name == folder_name), None)
    return entry.order_index if entry is not None else 0


def _segment_times_from_char_range(
    starts: list[float],
    ends: list[float],
    orig_start: int,
    orig_end_exclusive: int,
) -> tuple[float, float] | None:
    n = min(len(starts), len(ends))
    if n == 0:
        return None
    start_idx = max(0, min(orig_start, n - 1))
    end_idx = max(0, min(orig_end_exclusive - 1, n - 1))
    if start_idx > end_idx:
        end_idx = start_idx
    return starts[start_idx], ends[end_idx]


def _align_segments(
    full_text: str,
    segments: list[tuple[str, str]],
    alignment: dict[str, Any],
) -> tuple[dict[str, tuple[float, float]], list[str]]:
    """Kernalgorithmus (Phase 6 §8). Gibt {segment_id: (start_sec, end_sec)}
    und eine Liste von Warnungen zurück. Bricht NIE wegen kleiner
    Normalisierungsprobleme ab — nur Warnungen, kein Alignment-Fail."""
    warnings: list[str] = []
    starts = alignment.get("character_start_times_seconds") or []
    ends = alignment.get("character_end_times_seconds") or []

    if not starts or not ends:
        warnings.append(ALIGNMENT_WARNING_MISSING_CHARACTER_TIMESTAMPS)
        return {}, warnings

    if any(starts[i] > starts[i + 1] for i in range(len(starts) - 1)) or any(
        ends[i] > ends[i + 1] for i in range(len(ends) - 1)
    ):
        warnings.append(ALIGNMENT_WARNING_NON_MONOTONIC_TIMESTAMPS)

    normalized_full, index_map = build_normalized_index_map(full_text)
    total_duration = ends[-1] if ends else 0.0
    total_chars = sum(len(text) for _, text in segments) or 1

    results: dict[str, tuple[float, float]] = {}
    search_from = 0
    cursor_time = 0.0

    for segment_id, segment_text in segments:
        if not segment_text.strip():
            warnings.append(f"{ALIGNMENT_WARNING_EMPTY_SEGMENT_TEXT}: {segment_id}")
            results[segment_id] = (cursor_time, cursor_time)
            continue

        normalized_segment, _ = build_normalized_index_map(segment_text)
        span = find_segment_span(normalized_full, index_map, normalized_segment, search_from=search_from)
        if span is not None:
            orig_start, orig_end, new_cursor = span
            times = _segment_times_from_char_range(starts, ends, orig_start, orig_end)
            if times is not None:
                start_sec, end_sec = times
                results[segment_id] = (start_sec, end_sec)
                search_from = new_cursor
                cursor_time = end_sec
                continue

        warnings.append(f"{ALIGNMENT_WARNING_TEXT_SEGMENT_NOT_FOUND}: {segment_id}")
        proportion = len(segment_text) / total_chars
        fallback_duration = total_duration * proportion
        start_sec = cursor_time
        end_sec = min(total_duration, start_sec + fallback_duration) if total_duration else start_sec
        results[segment_id] = (start_sec, end_sec)
        warnings.append(f"{ALIGNMENT_WARNING_USED_PROPORTIONAL_FALLBACK}: {segment_id}")
        cursor_time = end_sec

    return results, warnings


def build_folder_alignment(
    project: Project,
    folder_name: str,
    audio_item: VoiceoverAudioItem,
    elevenlabs_timestamps: dict[str, Any],
    *,
    tts_text: str | None = None,
) -> VoiceoverAlignment:
    """tts_text sollte der EXAKT an ElevenLabs gesendete Text sein (siehe
    tts_text_builder.build_tts_ready_text) — bei eleven_v3 kann dieser sich
    von draft.voiceover_text_full unterscheiden, wenn Pause-Tags eingefügt
    wurden. Ohne tts_text (z. B. ältere Aufrufer, Tests) wird wie bisher
    draft.voiceover_text_full verwendet."""
    confirmed_document = load_folder_voiceovers_confirmed(project)
    draft = next((item for item in confirmed_document.items if item.folder_name == folder_name), None)
    if draft is None:
        raise ValueError(f"Kein bestätigter Voice-over-Text für '{folder_name}' vorhanden.")

    full_text = tts_text if tts_text is not None else draft.voiceover_text_full
    segments = [(item.sentence_id, item.text) for item in draft.sentence_items]
    times_by_id, warnings = _align_segments(full_text, segments, elevenlabs_timestamps)

    items: list[AlignmentItem] = []
    for sentence_item in draft.sentence_items:
        start_sec, end_sec = times_by_id.get(sentence_item.sentence_id, (0.0, 0.0))
        items.append(
            AlignmentItem(
                sentence_id=sentence_item.sentence_id,
                beat_id=sentence_item.beat_id,
                text=sentence_item.text,
                audio_start_sec=round(start_sec, 3),
                audio_end_sec=round(end_sec, 3),
                duration_sec=round(max(0.0, end_sec - start_sec), 3),
                primary_asset_id=sentence_item.primary_asset_id,
                backup_asset_ids=list(sentence_item.backup_asset_ids),
                visual_intent=sentence_item.visual_intent,
                asset_confidence=sentence_item.asset_confidence,
                needs_supplement_asset=sentence_item.needs_supplement_asset,
                supplement_reason=sentence_item.supplement_reason,
            )
        )

    if audio_item.audio_duration_sec and times_by_id:
        last_end = max((end for _, end in times_by_id.values()), default=0.0)
        if abs(audio_item.audio_duration_sec - last_end) > 1.0:
            warnings.append(ALIGNMENT_WARNING_AUDIO_DURATION_MISMATCH)

    return VoiceoverAlignment(
        project_id=project.id,
        scope=AUDIO_SCOPE_FOLDER,
        folder_name=folder_name,
        audio_path=audio_item.audio_path,
        audio_duration_sec=audio_item.audio_duration_sec,
        items=items,
        alignment_warnings=warnings,
    )


def build_intro_alignment(
    project: Project,
    audio_item: VoiceoverAudioItem,
    elevenlabs_timestamps: dict[str, Any],
) -> VoiceoverAlignment:
    confirmed_hook = load_confirmed_intro_hook(project)
    if confirmed_hook is None:
        raise ValueError("Kein bestätigter Intro-Hook vorhanden.")

    segments = [(beat.hook_beat_id, beat.text) for beat in confirmed_hook.visual_beats]
    times_by_id, warnings = _align_segments(confirmed_hook.hook_text, segments, elevenlabs_timestamps)

    items: list[AlignmentItem] = []
    for beat in confirmed_hook.visual_beats:
        start_sec, end_sec = times_by_id.get(beat.hook_beat_id, (0.0, 0.0))
        items.append(
            AlignmentItem(
                sentence_id=beat.hook_beat_id,
                beat_id=beat.hook_beat_id,
                text=beat.text,
                audio_start_sec=round(start_sec, 3),
                audio_end_sec=round(end_sec, 3),
                duration_sec=round(max(0.0, end_sec - start_sec), 3),
                primary_asset_id=beat.primary_asset_id,
                backup_asset_ids=list(beat.backup_asset_ids),
                visual_intent=beat.visual_intent,
                asset_confidence=beat.asset_confidence,
                needs_supplement_asset=beat.needs_supplement_asset,
                supplement_reason=beat.supplement_reason,
            )
        )

    if audio_item.audio_duration_sec and times_by_id:
        last_end = max((end for _, end in times_by_id.values()), default=0.0)
        if abs(audio_item.audio_duration_sec - last_end) > 1.0:
            warnings.append(ALIGNMENT_WARNING_AUDIO_DURATION_MISMATCH)

    return VoiceoverAlignment(
        project_id=project.id,
        scope=AUDIO_SCOPE_INTRO,
        folder_name="",
        audio_path=audio_item.audio_path,
        audio_duration_sec=audio_item.audio_duration_sec,
        items=items,
        alignment_warnings=warnings,
    )


def _alignment_path(project: Project, scope: str, folder_name: str) -> Path:
    if scope == AUDIO_SCOPE_INTRO:
        return get_intro_alignment_path(project.work_dir_path)
    order_index = _resolve_order_index(project, folder_name)
    return get_folder_alignment_path(project.work_dir_path, order_index, folder_name)


def save_alignment(
    project: Project, scope: str, folder_name: str, alignment: VoiceoverAlignment
) -> Path:
    path = _alignment_path(project, scope, folder_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = alignment.model_copy(update={"project_id": project.id})
    path.write_text(normalized.model_dump_json(indent=2), encoding="utf-8")
    return path


def load_alignment(project: Project, scope: str, folder_name: str) -> VoiceoverAlignment | None:
    path = _alignment_path(project, scope, folder_name)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return VoiceoverAlignment.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None
