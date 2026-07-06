"""Validierung von Timeline-Items vor OTIO-Export."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from otio_app.analysis_models import EditPlanSettings, TimelineItem
from otio_app.services.duration_rules import MAX_DURATION_SEC, MIN_DURATION_SEC


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


def _is_narration_item(item: TimelineItem) -> bool:
    return item.type in {"video_shot", "image_shot"}


def _is_outro_item(item: TimelineItem) -> bool:
    return item.type == "generic_outro_visual"


def validate_timeline_items(
    items: list[TimelineItem],
    *,
    settings: EditPlanSettings,
    allow_black_outro: bool = False,
    fps: float = 25.0,
) -> TimelineValidationResult:
    """Prüft Dauerregeln, Voice-Abdeckung und Outro-Planung."""
    result = TimelineValidationResult()
    if not items:
        result.status = ValidationStatus.BLOCKED
        result.errors.append("Keine Timeline-Items im Schnittplan.")
        return result

    narration_items = [item for item in items if _is_narration_item(item)]
    outro_items = [item for item in items if _is_outro_item(item)]

    for item in items:
        duration = item.final_duration_sec or item.duration_sec
        if duration > MAX_DURATION_SEC + 0.01:
            result.errors.append(
                f"{item.timeline_item_id}: final_duration_sec {duration:.1f}s > {MAX_DURATION_SEC}s"
            )
        if duration < MIN_DURATION_SEC - 0.01 and not item.allow_black:
            result.errors.append(
                f"{item.timeline_item_id}: duration_sec {duration:.1f}s < {MIN_DURATION_SEC}s"
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

    for item in narration_items:
        pass  # Outro-Anhängen am letzten Shot wird über fehlende generic_outro_visual erkannt.

    # Voice-Abdeckung: während Narration muss sichtbares Element existieren
    if narration_items:
        voice_end = max(item.voice_end_sec for item in narration_items)
        video_end = max(
            (item.timeline_out_sec for item in narration_items),
            default=0.0,
        )
        if video_end + 0.05 < voice_end:
            result.errors.append(
                "Visuelles Loch während aktivem Voice-over — Videospur endet vor Voice."
            )

    # Nach Voice: nur geplante Outros
    if narration_items and outro_items:
        narration_end = max(item.timeline_out_sec for item in narration_items)
        first_outro_start = min(item.timeline_in_sec for item in outro_items)
        if first_outro_start > narration_end + 0.05:
            if not allow_black_outro:
                result.errors.append(
                    "Ungeplantes visuelles Loch nach Voice-over vor Outro-Elementen."
                )

    if result.errors:
        if result.status != ValidationStatus.AWAITING_APPROVAL:
            result.status = ValidationStatus.BLOCKED
    return result


def validate_no_exporter_asset_selection() -> None:
    """Dokumentations-Hook — der Exporter wählt keine Assets (nur Lesen)."""
    return None
