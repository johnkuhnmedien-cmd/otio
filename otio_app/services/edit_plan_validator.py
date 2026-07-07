"""Validierung von Timeline-Items vor OTIO-Export."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from otio_app.analysis_models import EditPlanRulesDocument, EditPlanSettings, TimelineItem, VoiceoverPlan
from otio_app.defaults import RIGHTS_STATUS_NEEDS_LICENSE_REVIEW, RIGHTS_STATUS_NEEDS_REVIEW
from otio_app.services.asset_usage import append_max_usage_to_validation_report, validate_max_asset_usage_blockers
from otio_app.services.clean_media import path_is_readable_file
from otio_app.services.duration_rules import MAX_DURATION_SEC, MIN_DURATION_SEC
from otio_app.services.media_utils import is_image_media
from otio_app.services.timeline_plan_builder import NARRATION_VISUAL_TYPES, VISUAL_VIDEO_TYPES


class ValidationStatus(str, Enum):
    OK = "OK"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    BLOCKED = "BLOCKED"


@dataclass
class TimelineValidationResult:
    status: ValidationStatus = ValidationStatus.OK
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return self.status == ValidationStatus.OK


def _is_opening_title_item(item: TimelineItem) -> bool:
    return item.type == "opening_title"


def validate_opening_titles(
    items: list[TimelineItem],
    *,
    opening_title_required: bool = False,
    require_rendered_media: bool = False,
) -> TimelineValidationResult:
    """Prüft Opening-Title-Elemente vor OTIO-Export."""
    result = TimelineValidationResult()
    title_items = [item for item in items if _is_opening_title_item(item)]

    if opening_title_required and not title_items:
        result.status = ValidationStatus.AWAITING_APPROVAL
        result.errors.append(
            "Ordner-Titel-Regel aktiv, aber kein opening_title im Schnittplan. "
            "Bitte unter „Vorschlag“ den Schnittplan neu generieren."
        )
        return result

    for item in title_items:
        if item.duration_sec <= 0.01:
            result.errors.append(f"{item.timeline_item_id}: duration_sec muss > 0 sein.")
        if item.timeline_in_sec < -0.001:
            result.errors.append(f"{item.timeline_item_id}: timeline_in_sec muss >= 0 sein.")
        if item.track != "V2":
            result.errors.append(f"{item.timeline_item_id}: track muss V2 sein (ist {item.track!r}).")
        if not item.text.strip():
            result.errors.append(f"{item.timeline_item_id}: text fehlt.")
        media_path = item.rendered_media_path or item.resolved_media_path
        if not media_path:
            result.errors.append(f"{item.timeline_item_id}: rendered_media_path fehlt.")
        elif require_rendered_media and not path_is_readable_file(Path(media_path)):
            result.errors.append(
                f"{item.timeline_item_id}: gerenderte Titeldatei nicht lesbar: {media_path}"
            )

    if result.errors:
        result.status = ValidationStatus.BLOCKED
    return result


def _is_narration_item(item: TimelineItem) -> bool:
    return item.type in NARRATION_VISUAL_TYPES


def _is_outro_item(item: TimelineItem) -> bool:
    return item.type == "generic_outro_visual"


def _is_visual_video_item(item: TimelineItem) -> bool:
    if item.type not in VISUAL_VIDEO_TYPES:
        return False
    path = item.resolved_media_path or ""
    return bool(path) and not is_image_media(Path(path))


def validate_voiceover_plan(
    voiceover: VoiceoverPlan | None,
    *,
    settings: EditPlanSettings,
    items: list[TimelineItem],
) -> TimelineValidationResult:
    """Harte Checks für Voice-over-Trim und Timing."""
    result = TimelineValidationResult()
    if voiceover is None:
        result.status = ValidationStatus.AWAITING_APPROVAL
        result.errors.append("Kein voiceover-Block im Schnittplan.")
        return result

    offset = max(0.0, float(settings.audio_offset_sec))
    trim_sec = max(0.0, float(settings.video_head_trim_sec))

    if abs(voiceover.source_in_sec) > 0.001:
        result.errors.append(
            f"voiceover.source_in_sec muss 0.0 sein (ist {voiceover.source_in_sec:.3f})."
        )

    if voiceover.trim_policy != "disabled" and settings.voiceover_trim_policy == "disabled":
        result.errors.append(
            f"voiceover.trim_policy muss disabled sein (ist {voiceover.trim_policy!r})."
        )

    if abs(voiceover.timeline_start_sec - offset) > 0.05:
        result.errors.append(
            f"voiceover.timeline_start_sec ({voiceover.timeline_start_sec:.2f}) "
            f"entspricht nicht audio_offset_sec ({offset:.2f})."
        )

    if voiceover.duration_source != "ffprobe":
        result.errors.append(
            f"voiceover.duration_source muss ffprobe sein (ist {voiceover.duration_source!r})."
        )

    expected_end = offset + voiceover.duration_sec
    if abs(voiceover.timeline_end_sec - expected_end) > 0.05:
        result.errors.append(
            f"voiceover.timeline_end_sec ({voiceover.timeline_end_sec:.2f}) "
            f"≠ audio_offset + duration ({expected_end:.2f})."
        )

    if voiceover.source_out_sec - voiceover.source_in_sec + 0.05 < voiceover.duration_sec:
        result.errors.append("voiceover.duration_sec stimmt nicht mit source_out - source_in überein.")

    narration_items = [item for item in items if _is_narration_item(item)]
    if voiceover.duration_sec > 0.05 and narration_items:
        text_items = [
            item for item in narration_items if item.type != "generic_narration_visual"
        ]
        if text_items:
            last_speech_end = max(item.voice_end_sec for item in text_items)
            if voiceover.duration_sec + 0.05 < last_speech_end:
                result.errors.append(
                    "Voice-over wurde auf letztes Textsegment-Ende gekürzt "
                    f"({voiceover.duration_sec:.2f}s < {last_speech_end:.2f}s)."
                )

    trim_policy = settings.video_head_trim_policy or "fixed_trim"
    if trim_policy == "fixed_trim" and trim_sec > 0:
        for item in items:
            if _is_visual_video_item(item):
                if abs(item.source_in_sec - trim_sec) > 0.05:
                    result.errors.append(
                        f"{item.timeline_item_id}: video source_in_sec muss {trim_sec} sein "
                        f"(ist {item.source_in_sec:.2f})."
                    )
            elif item.type in {"video_shot", "generic_narration_visual", "generic_outro_visual"}:
                path = item.resolved_media_path or ""
                if path and is_image_media(Path(path)) and item.source_in_sec > 0.05:
                    result.errors.append(
                        f"{item.timeline_item_id}: Bild-Asset darf kein video_head_trim haben."
                    )

    outro_items = [item for item in items if _is_outro_item(item)]
    if outro_items:
        first_outro = min(item.timeline_in_sec for item in outro_items)
        if first_outro + 0.05 < voiceover.timeline_end_sec:
            result.errors.append(
                f"generic_outro startet bei {first_outro:.2f}s vor "
                f"voiceover.timeline_end_sec ({voiceover.timeline_end_sec:.2f})."
            )

    narration_visual_end = max(
        (item.timeline_out_sec for item in items if _is_narration_item(item)),
        default=0.0,
    )
    if narration_visual_end + 0.05 < voiceover.timeline_end_sec:
        filler_present = any(item.type == "generic_narration_visual" for item in items)
        if not filler_present:
            result.status = ValidationStatus.AWAITING_APPROVAL
            result.errors.append(
                f"Visuelle Abdeckung endet bei {narration_visual_end:.2f}s, "
                f"Voice-over bis {voiceover.timeline_end_sec:.2f}s — "
                "generic_narration_visual fehlt."
            )

    if result.errors and result.status != ValidationStatus.AWAITING_APPROVAL:
        result.status = ValidationStatus.BLOCKED
    return result


def validate_timeline_items(
    items: list[TimelineItem],
    *,
    settings: EditPlanSettings,
    allow_black_outro: bool = False,
    fps: float = 25.0,
    voiceover: VoiceoverPlan | None = None,
    opening_title_required: bool = False,
    require_rendered_media: bool = False,
    rules_doc: EditPlanRulesDocument | None = None,
    work_dir_path: Path | None = None,
) -> TimelineValidationResult:
    """Prüft Dauerregeln, Voice-Abdeckung und Outro-Planung."""
    result = TimelineValidationResult()
    if not items:
        result.status = ValidationStatus.BLOCKED
        result.errors.append("Keine Timeline-Items im Schnittplan.")
        return result

    narration_items = [item for item in items if _is_narration_item(item)]
    outro_items = [item for item in items if _is_outro_item(item)]

    # Die Min./Max.-Shot-Regeln sind projektspezifisch konfigurierbar (Tab
    # „Regeln → Timing & Gemini“). Zuvor wurden hier fest verdrahtete
    # Default-Konstanten (3.0/8.0s) geprüft — unabhängig davon, was der Nutzer
    # tatsächlich eingestellt hatte. Das führte u.a. dazu, dass Meldungen wie
    # "> 8.0s" erschienen, obwohl der Nutzer z.B. 10s erlaubt hatte, oder dass
    # ein durch fehlerhafte Konfiguration (min > max) entstandener Ausreißer
    # nicht als das erkannt wurde, was er ist.
    max_duration_sec = float(settings.shot_max_sec) if settings.shot_max_sec else MAX_DURATION_SEC
    min_duration_sec = float(settings.shot_min_sec) if settings.shot_min_sec else MIN_DURATION_SEC
    if min_duration_sec > max_duration_sec:
        result.warnings.append(
            f"Konfiguration: Min. Shot ({min_duration_sec:.1f}s) > Max. Shot "
            f"({max_duration_sec:.1f}s) — Max. Shot wird als harte Obergrenze verwendet."
        )

    for item in items:
        if _is_opening_title_item(item):
            continue
        duration = item.final_duration_sec or item.duration_sec
        if duration > max_duration_sec + 0.01:
            result.errors.append(
                f"{item.timeline_item_id}: final_duration_sec {duration:.1f}s > {max_duration_sec}s"
            )
        if duration < min_duration_sec - 0.01 and not item.allow_black:
            if item.type != "generic_narration_visual":
                voice_span = max(0.0, item.voice_end_sec - item.voice_start_sec)
                if voice_span + 0.01 < min_duration_sec:
                    pass
                else:
                    result.errors.append(
                        f"{item.timeline_item_id}: duration_sec {duration:.1f}s < {min_duration_sec}s"
                    )
        if not item.resolved_media_path and not item.allow_black:
            result.errors.append(f"{item.timeline_item_id}: kein resolved_media_path")

        if item.source_out_sec < item.source_in_sec - 0.01:
            result.errors.append(f"{item.timeline_item_id}: source_out < source_in")

        frame_duration = 1.0 / fps if fps > 0 else 0.04
        for label, value in (
            ("timeline_in_sec", item.timeline_in_sec),
            ("timeline_out_sec", item.timeline_out_sec),
            ("duration_sec", duration),
        ):
            frames = value / frame_duration
            if abs(frames - round(frames)) > 0.02:
                result.warnings.append(
                    f"{item.timeline_item_id}: {label}={value:.3f}s nicht auf ganzen Frames"
                )

    for item in narration_items:
        if item.section_id and item.type == "generic_outro_visual":
            result.errors.append(
                f"{item.timeline_item_id}: Narration-Shot darf nicht generic_outro_visual sein."
            )

    expected_outro = settings.section_outro_sec
    actual_outro = sum(item.duration_sec for item in outro_items)
    if expected_outro > 0.05 and actual_outro + 0.05 < expected_outro:
        result.errors.append(
            f"section_outro_sec ({expected_outro:.1f}s) nicht vollständig als "
            f"Outro-Elemente geplant ({actual_outro:.1f}s)."
        )

    if expected_outro > 0.05 and not outro_items:
        result.status = ValidationStatus.AWAITING_APPROVAL
        result.errors.append(
            "section_outro_sec > 0, aber keine generic_outro_visual-Elemente im Plan."
        )

    voice_end_target = voiceover.timeline_end_sec if voiceover else None
    if voice_end_target is not None and narration_items:
        active_voice_end = voice_end_target
        video_end = max(
            (item.timeline_out_sec for item in items if _is_narration_item(item)),
            default=0.0,
        )
        if video_end + 0.05 < active_voice_end:
            result.errors.append(
                "Visuelles Loch während aktivem Voice-over — Videospur endet vor Voice."
            )
    elif narration_items:
        voice_end = max(item.voice_end_sec for item in narration_items)
        video_end = max(
            (item.timeline_out_sec for item in narration_items),
            default=0.0,
        )
        if video_end + 0.05 < voice_end:
            result.errors.append(
                "Visuelles Loch während aktivem Voice-over — Videospur endet vor Voice."
            )

    if narration_items and outro_items and voiceover is None:
        narration_end = max(item.timeline_out_sec for item in narration_items)
        first_outro_start = min(item.timeline_in_sec for item in outro_items)
        if first_outro_start > narration_end + 0.05:
            if not allow_black_outro:
                result.errors.append(
                    "Ungeplantes visuelles Loch nach Voice-over vor Outro-Elementen."
                )

    title_result = validate_opening_titles(
        items,
        opening_title_required=opening_title_required,
        require_rendered_media=require_rendered_media,
    )
    result.errors.extend(title_result.errors)
    result.warnings.extend(title_result.warnings)
    if title_result.status == ValidationStatus.AWAITING_APPROVAL:
        result.status = ValidationStatus.AWAITING_APPROVAL
    elif title_result.status == ValidationStatus.BLOCKED and result.status == ValidationStatus.OK:
        result.status = ValidationStatus.BLOCKED

    voice_result = validate_voiceover_plan(voiceover, settings=settings, items=items)
    result.errors.extend(voice_result.errors)
    result.warnings.extend(voice_result.warnings)
    if voice_result.status == ValidationStatus.AWAITING_APPROVAL:
        result.status = ValidationStatus.AWAITING_APPROVAL
    elif voice_result.status == ValidationStatus.BLOCKED and result.status == ValidationStatus.OK:
        result.status = ValidationStatus.BLOCKED

    if rules_doc is not None:
        usage_violations = validate_max_asset_usage_blockers(
            timeline_items=items,
            rules_doc=rules_doc,
        )
        for violation in usage_violations:
            result.errors.append(
                f"max_asset_usage: `{violation.asset_id}` {violation.usage_count}× "
                f"(max {violation.max_allowed})"
            )
        if usage_violations:
            result.status = ValidationStatus.BLOCKED
            if work_dir_path is not None:
                append_max_usage_to_validation_report(work_dir_path, usage_violations)

    for item in items:
        if item.rights_status in {
            RIGHTS_STATUS_NEEDS_LICENSE_REVIEW,
            RIGHTS_STATUS_NEEDS_REVIEW,
        }:
            result.errors.append(
                f"{item.timeline_item_id}: rights_status={item.rights_status} — "
                "manuelle Rechtefreigabe erforderlich."
            )
            result.status = ValidationStatus.BLOCKED

    if result.errors:
        hard_block_markers = (
            "final_duration_sec",
            "duration_sec",
            "source_out < source_in",
            "source_in_sec muss",
            "voiceover.source_in_sec",
        )
        if any(
            any(marker in error for marker in hard_block_markers) for error in result.errors
        ):
            result.status = ValidationStatus.BLOCKED
        elif result.status != ValidationStatus.AWAITING_APPROVAL:
            result.status = ValidationStatus.BLOCKED
    return result


def validate_no_exporter_asset_selection() -> None:
    """Dokumentations-Hook — der Exporter wählt keine Assets (nur Lesen)."""
    return None
