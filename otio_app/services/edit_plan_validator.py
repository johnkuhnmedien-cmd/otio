"""Validierung von Timeline-Items vor OTIO-Export."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from otio_app.analysis_models import EditPlanRulesDocument, EditPlanSettings, TimelineItem, VoiceoverPlan
from otio_app.defaults import RIGHTS_STATUS_NEEDS_LICENSE_REVIEW, RIGHTS_STATUS_NEEDS_REVIEW
from otio_app.services.asset_usage import append_max_usage_to_validation_report, validate_max_asset_usage_blockers
from otio_app.services.clean_media import path_is_readable_file
from otio_app.services.duration_rules import MAX_DURATION_SEC, MIN_DURATION_SEC
from otio_app.services.media_utils import is_image_media
from otio_app.services.timeline_plan_builder import NARRATION_VISUAL_TYPES, VISUAL_VIDEO_TYPES


RETRYABLE_ERROR_TYPES = frozenset(
    {
        "SHOT_TOO_SHORT",
        "SHOT_TOO_LONG",
        "ASSET_USAGE_LIMIT_EXCEEDED",
        "ASSET_REUSE_DISTANCE_TOO_SHORT",
        "INSUFFICIENT_PARTS",
    }
)

ASSET_RULE_ERROR_TYPES = frozenset(
    {
        "ASSET_USAGE_LIMIT_EXCEEDED",
        "ASSET_REUSE_DISTANCE_TOO_SHORT",
    }
)


@dataclass
class PlanValidationError:
    """Strukturierter Validierungsfehler für Retry-Loop und Reports."""

    type: str
    message: str = ""
    asset_id: str | None = None
    usage_count: int | None = None
    max_allowed: int | None = None
    timeline_item_ids: list[str] | None = None
    timeline_item_id: str | None = None
    duration_sec: float | None = None
    min_sec: float | None = None
    max_sec: float | None = None
    segment_id: str | None = None
    reason: str | None = None
    previous_item_id: str | None = None
    current_item_id: str | None = None
    actual_distance_shots: int | None = None
    required_distance_shots: int | None = None
    beat_id: str | None = None
    allowed_parts_min: int | None = None
    allowed_parts_max: int | None = None
    actual_parts: int | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"type": self.type}
        if self.message:
            payload["message"] = self.message
        for key in (
            "asset_id",
            "usage_count",
            "max_allowed",
            "timeline_item_ids",
            "timeline_item_id",
            "duration_sec",
            "min_sec",
            "max_sec",
            "segment_id",
            "reason",
            "previous_item_id",
            "current_item_id",
            "actual_distance_shots",
            "required_distance_shots",
            "beat_id",
            "allowed_parts_min",
            "allowed_parts_max",
            "actual_parts",
        ):
            value = getattr(self, key)
            if value is not None:
                payload[key] = value
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlanValidationError:
        known = {field.name for field in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        kwargs = {key: data[key] for key in data if key in known}
        if "type" not in kwargs:
            kwargs["type"] = str(data.get("type", "UNKNOWN"))
        return cls(**kwargs)

    def is_retryable(self) -> bool:
        if self.type in RETRYABLE_ERROR_TYPES:
            return True
        if self.type == "TIMELINE_VALIDATION" and self.message:
            markers = (
                "Textsegment",
                "duration_sec",
                "final_duration_sec",
                "Voice-over",
                "Visuelles Loch",
                "SHOT_TOO",
                "ASSET_",
                "INSUFFICIENT_PARTS",
            )
            return any(marker in self.message for marker in markers)
        return False


def plan_validation_error_to_message(error: PlanValidationError) -> str:
    """Menschenlesbare Zeile für UI und Gemini-Korrektur."""
    if error.message:
        return error.message
    if error.type == "ASSET_USAGE_LIMIT_EXCEEDED" and error.asset_id:
        return (
            f"ASSET_USAGE_LIMIT_EXCEEDED: `{error.asset_id}` "
            f"{error.usage_count}× (max {error.max_allowed})"
        )
    if error.type == "ASSET_REUSE_DISTANCE_TOO_SHORT" and error.asset_id:
        return (
            f"ASSET_REUSE_DISTANCE_TOO_SHORT: `{error.asset_id}` "
            f"Abstand {error.actual_distance_shots} Shots (min {error.required_distance_shots})"
        )
    if error.type == "SHOT_TOO_SHORT" and error.timeline_item_id:
        return (
            f"SHOT_TOO_SHORT: {error.timeline_item_id} "
            f"{error.duration_sec:.1f}s < {error.min_sec:.1f}s"
        )
    if error.type == "SHOT_TOO_LONG" and error.timeline_item_id:
        return (
            f"SHOT_TOO_LONG: {error.timeline_item_id} "
            f"{error.duration_sec:.1f}s > {error.max_sec:.1f}s"
        )
    return f"{error.type}: {error.to_dict()}"


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
    allow_asset_rule_overrides: bool = False,
) -> TimelineValidationResult:
    """Prüft Dauerregeln, Voice-Abdeckung und Outro-Planung."""
    result = TimelineValidationResult()
    if not items:
        result.status = ValidationStatus.BLOCKED
        result.errors.append("Keine Timeline-Items im Schnittplan.")
        return result

    narration_items = [item for item in items if _is_narration_item(item)]
    outro_items = [item for item in items if _is_outro_item(item)]

    beat_spans: dict[str, float] = {}
    for beat_id in {item.beat_id for item in narration_items if item.beat_id}:
        beat_items = [item for item in narration_items if item.beat_id == beat_id]
        if beat_items:
            beat_spans[beat_id] = max(item.voice_end_sec for item in beat_items) - min(
                item.voice_start_sec for item in beat_items
            )

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
                beat_total = beat_spans.get(item.beat_id or "", duration)
                if beat_total + 0.01 < min_duration_sec:
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
            message = (
                f"max_asset_usage: `{violation.asset_id}` {violation.usage_count}× "
                f"(max {violation.max_allowed})"
            )
            if allow_asset_rule_overrides:
                result.warnings.append(
                    f"Regel-Hinweis (Export trotzdem möglich): {message}"
                )
            else:
                result.errors.append(message)
        if usage_violations and not allow_asset_rule_overrides:
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


TIMING_VALIDATION_MARKERS = (
    "Textsegment",
    "duration_sec",
    "final_duration_sec",
    "section_outro_sec",
    "Voice-over bis",
    "Visuelles Loch",
    "generic_outro startet",
    "voiceover.timeline",
    "voiceover.duration",
    "voiceover.source",
    "video source_in_sec",
    "nicht vollständig als",
    "Min. Shot",
    "unter Min. Shot",
    "ASSET_USAGE_LIMIT_EXCEEDED",
    "ASSET_REUSE_DISTANCE_TOO_SHORT",
    "SHOT_TOO_SHORT",
    "SHOT_TOO_LONG",
    "INSUFFICIENT_PARTS",
)


SHOT_DURATION_RULE_TYPES = frozenset(
    {
        "video_shot",
        "image_with_background",
        "generic_narration_visual",
        "generic_outro_visual",
    }
)


def _is_soft_timeline_message(message: str) -> bool:
    return any(
        phrase in message
        for phrase in (
            "kein resolved_media_path",
            "section_outro_sec > 0, aber keine",
            "nicht vollständig als Outro-Elemente geplant",
            "generic_narration_visual fehlt",
        )
    )


@dataclass
class FinalPlanValidationResult:
    ok: bool
    status: ValidationStatus
    errors: list[PlanValidationError] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def has_retryable_errors(self) -> bool:
        return any(error.is_retryable() for error in self.errors)

    @property
    def retryable_errors(self) -> list[PlanValidationError]:
        return [error for error in self.errors if error.is_retryable()]


def validate_shot_duration_rules(
    items: list[TimelineItem],
    *,
    settings: EditPlanSettings,
) -> list[PlanValidationError]:
    """Harte Min./Max.-Shot-Prüfung auf finalen Visual-Shots."""
    min_sec = float(settings.shot_min_sec) if settings.shot_min_sec else MIN_DURATION_SEC
    max_sec = float(settings.shot_max_sec) if settings.shot_max_sec else MAX_DURATION_SEC

    segment_spans: dict[str, float] = {}
    for item in items:
        if item.type not in SHOT_DURATION_RULE_TYPES or not item.beat_id:
            continue
        segment_spans.setdefault(item.beat_id, 0.0)
        span = max(0.0, item.voice_end_sec - item.voice_start_sec)
        segment_spans[item.beat_id] = max(segment_spans[item.beat_id], span)

    errors: list[PlanValidationError] = []
    for item in items:
        if item.type not in SHOT_DURATION_RULE_TYPES:
            continue
        duration = item.final_duration_sec or item.duration_sec
        segment_id = item.beat_id or ""
        segment_span = segment_spans.get(segment_id, duration)

        if duration > max_sec + 0.01:
            errors.append(
                PlanValidationError(
                    type="SHOT_TOO_LONG",
                    timeline_item_id=item.timeline_item_id,
                    duration_sec=duration,
                    max_sec=max_sec,
                    segment_id=segment_id or None,
                    beat_id=segment_id or None,
                    reason="Shot longer than maximum duration",
                )
            )
        if duration + 0.01 < min_sec:
            if segment_span + 0.01 < min_sec:
                continue
            errors.append(
                PlanValidationError(
                    type="SHOT_TOO_SHORT",
                    timeline_item_id=item.timeline_item_id,
                    duration_sec=duration,
                    min_sec=min_sec,
                    segment_id=segment_id or None,
                    beat_id=segment_id or None,
                    reason="Shot shorter than minimum duration",
                )
            )
    return errors


def validate_asset_usage_rules(
    items: list[TimelineItem],
    *,
    rules_doc: EditPlanRulesDocument,
) -> list[PlanValidationError]:
    """Globale Asset-Nutzung und Mindestabstand auf finalen timeline_items."""
    from otio_app.services.asset_usage import (
        asset_id_from_timeline_item,
        get_asset_usage_rules,
        visual_usage_timeline_items,
    )

    rules = get_asset_usage_rules(rules_doc)
    errors: list[PlanValidationError] = []
    visual_items = visual_usage_timeline_items(items)
    usage_to_item_ids: dict[str, list[str]] = {}

    for item in visual_items:
        asset_id = asset_id_from_timeline_item(item)
        if not asset_id:
            continue
        usage_to_item_ids.setdefault(asset_id, []).append(item.timeline_item_id)

    if rules.max_asset_usage is not None:
        for asset_id, item_ids in sorted(usage_to_item_ids.items()):
            count = len(item_ids)
            if count > rules.max_asset_usage:
                errors.append(
                    PlanValidationError(
                        type="ASSET_USAGE_LIMIT_EXCEEDED",
                        asset_id=asset_id,
                        usage_count=count,
                        max_allowed=rules.max_asset_usage,
                        timeline_item_ids=item_ids,
                    )
                )

    if rules.min_asset_reuse_distance_shots > 0:
        last_used_at: dict[str, tuple[int, str]] = {}
        for index, item in enumerate(visual_items, start=1):
            asset_id = asset_id_from_timeline_item(item)
            if not asset_id:
                continue
            if asset_id in last_used_at:
                previous_index, previous_item_id = last_used_at[asset_id]
                actual_distance = index - previous_index - 1
                if (index - previous_index) <= rules.min_asset_reuse_distance_shots:
                    errors.append(
                        PlanValidationError(
                            type="ASSET_REUSE_DISTANCE_TOO_SHORT",
                            asset_id=asset_id,
                            previous_item_id=previous_item_id,
                            current_item_id=item.timeline_item_id,
                            actual_distance_shots=actual_distance,
                            required_distance_shots=rules.min_asset_reuse_distance_shots,
                        )
                    )
            last_used_at[asset_id] = (index, item.timeline_item_id)

    return errors


def _timeline_errors_to_plan_errors(messages: list[str]) -> list[PlanValidationError]:
    errors: list[PlanValidationError] = []
    for message in messages:
        if "ASSET_USAGE_LIMIT_EXCEEDED" in message or message.startswith("max_asset_usage"):
            continue
        retryable = any(marker in message for marker in TIMING_VALIDATION_MARKERS)
        error_type = "TIMELINE_VALIDATION"
        if "SHOT_TOO_SHORT" in message or (
            "duration_sec" in message and "<" in message
        ):
            error_type = "SHOT_TOO_SHORT"
        elif "SHOT_TOO_LONG" in message or (
            "final_duration_sec" in message and ">" in message
        ):
            error_type = "SHOT_TOO_LONG"
        elif "INSUFFICIENT_PARTS" in message:
            error_type = "INSUFFICIENT_PARTS"
        errors.append(PlanValidationError(type=error_type, message=message))
    return errors


def validate_final_edit_plan(
    items: list[TimelineItem],
    *,
    settings: EditPlanSettings,
    voiceover: VoiceoverPlan | None,
    rules_doc: EditPlanRulesDocument | None = None,
    extra_errors: list[PlanValidationError] | None = None,
) -> FinalPlanValidationResult:
    """Zentrale harte Validierung auf finalen timeline_items."""
    errors: list[PlanValidationError] = list(extra_errors or [])
    errors.extend(validate_shot_duration_rules(items, settings=settings))
    if rules_doc is not None:
        errors.extend(validate_asset_usage_rules(items, rules_doc=rules_doc))

    timeline_result = validate_timeline_items(
        items,
        settings=settings,
        voiceover=voiceover,
        opening_title_required=False,
        require_rendered_media=False,
        rules_doc=None,
    )
    errors.extend(_timeline_errors_to_plan_errors(timeline_result.errors))

    # Shot-Dauer-Fehler kommen strukturiert — String-Duplikate vermeiden.
    errors = [
        error
        for error in errors
        if not (
            error.type == "TIMELINE_VALIDATION"
            and error.message
            and ("duration_sec" in error.message or "final_duration_sec" in error.message)
        )
    ]

    hard_blockers = [
        error
        for error in errors
        if error.is_retryable()
        or (
            error.type == "TIMELINE_VALIDATION"
            and error.message
            and not _is_soft_timeline_message(error.message)
        )
    ]
    effective_status = timeline_result.status
    if timeline_result.status == ValidationStatus.BLOCKED and timeline_result.errors:
        if all(_is_soft_timeline_message(message) for message in timeline_result.errors):
            effective_status = ValidationStatus.AWAITING_APPROVAL
    ok = not hard_blockers and effective_status in {
        ValidationStatus.OK,
        ValidationStatus.AWAITING_APPROVAL,
    }
    return FinalPlanValidationResult(
        ok=ok,
        status=ValidationStatus.BLOCKED if hard_blockers else effective_status,
        errors=hard_blockers,
        warnings=timeline_result.warnings,
    )


def retryable_validation_errors(errors: list[PlanValidationError | str]) -> list[PlanValidationError]:
    structured: list[PlanValidationError] = []
    for entry in errors:
        if isinstance(entry, PlanValidationError):
            if entry.is_retryable():
                structured.append(entry)
        elif any(marker in entry for marker in TIMING_VALIDATION_MARKERS):
            structured.append(PlanValidationError(type="TIMELINE_VALIDATION", message=entry))
    return structured


def should_retry_gemini_for_validation(errors: list[PlanValidationError | str]) -> bool:
    return bool(retryable_validation_errors(errors))


def timing_validation_errors(errors: list[str]) -> list[str]:
    """Filtert Validierungsfehler, die ein erneuter Gemini-Lauf beheben könnte."""
    return [error for error in errors if any(marker in error for marker in TIMING_VALIDATION_MARKERS)]


def collect_min_shot_violations(
    shots,
    *,
    min_sec: float,
) -> list[str]:
    """Findet Narration-Shots unter Min. Shot, wenn der Beat lang genug wäre."""
    from collections import defaultdict

    by_beat: dict[str, list] = defaultdict(list)
    for shot in shots:
        if getattr(shot, "section_outro", False):
            continue
        beat_id = getattr(shot, "beat_id", "") or "_unscoped"
        by_beat[beat_id].append(shot)

    errors: list[str] = []
    for beat_id, beat_shots in by_beat.items():
        beat_total = max(s.voice_end_sec for s in beat_shots) - min(s.voice_start_sec for s in beat_shots)
        if beat_total + 0.01 < min_sec:
            continue
        for shot in beat_shots:
            if shot.duration_sec + 0.01 < min_sec:
                label = beat_id if beat_id != "_unscoped" else shot.folder
                errors.append(
                    f"{label}: Shot {shot.duration_sec:.1f}s unter Min. Shot {min_sec:.1f}s "
                    f"(Beat-Dauer {beat_total:.1f}s)"
                )
    return errors


def should_retry_gemini_for_timing(errors: list[str]) -> bool:
    return should_retry_gemini_for_validation(
        [PlanValidationError(type="TIMELINE_VALIDATION", message=error) for error in errors]
    )


def validate_folder_plan_timing(
    items: list[TimelineItem],
    *,
    settings: EditPlanSettings,
    voiceover: VoiceoverPlan | None,
) -> TimelineValidationResult:
    """Timing-Validierung für einen Ordner-Vorschauplan (vor Bestätigen)."""
    return validate_timeline_items(
        items,
        settings=settings,
        voiceover=voiceover,
        opening_title_required=False,
        require_rendered_media=False,
        rules_doc=None,
    )


def validate_no_exporter_asset_selection() -> None:
    """Dokumentations-Hook — der Exporter wählt keine Assets (nur Lesen)."""
    return None
