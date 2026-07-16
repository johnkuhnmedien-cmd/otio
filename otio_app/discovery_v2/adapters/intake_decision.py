"""Deterministische Intake-Entscheidung aus gespeicherten Registry-/Validation-Feldern.

Kein Dateizugriff, kein ffprobe, kein Hashing, keine Shell.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

from otio_app.discovery_v2.domain.inventory import MediaKind
from otio_app.discovery_v2.domain.media_intake import (
    PROCESSING_PROFILE_VERSION,
    IntakeAction,
    IntakePlanItem,
    IntakePlanItemStatus,
)
from otio_app.discovery_v2.domain.technical_validation import (
    AssetValidationRecord,
    AssetValidationStatus,
)


# Spiegel der Resolve-freundlichen Regeln aus clean_media (nur als Konstanten).
_FRIENDLY_VIDEO_CODECS = frozenset(
    {"h264", "avc", "avc1", "libx264", "mpeg4", "mp4v"}
)
_FRIENDLY_CONTAINERS = frozenset({".mp4", ".mov", ".m4v"})
_ALLOWED_PIXEL_FORMATS = frozenset({"yuv420p", "yuvj420p"})
_ALLOWED_BIT_DEPTHS = frozenset({8})
_PROBLEMATIC_VIDEO_CODECS = frozenset(
    {
        "hevc",
        "h265",
        "prores",
        "dnxhd",
        "dnxhr",
        "vp9",
        "av1",
        "mjpeg",
        "rawvideo",
        "v210",
        "r210",
    }
)
_SUITABLE_AUDIO_EXTENSIONS = frozenset(
    {".wav", ".mp3", ".aac", ".m4a", ".flac", ".aiff", ".aif", ".ogg"}
)
_COPY_IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp"})
_TRANSCODE_IMAGE_EXTENSIONS = frozenset({".heic", ".tif", ".tiff"})

_BLOCKED_VALIDATION_STATUSES = frozenset(
    {
        AssetValidationStatus.SOURCE_MISSING,
        AssetValidationStatus.SOURCE_CHANGED,
        AssetValidationStatus.PROBE_FAILED,
        AssetValidationStatus.UNSUPPORTED_MEDIA_KIND,
        AssetValidationStatus.VALIDATION_ERROR,
    }
)


@dataclass(frozen=True)
class IntakeDecisionSource:
    """Eingabe ausschließlich aus gespeicherten Metadaten."""

    validation: AssetValidationRecord
    extension: str
    source_group: str


@dataclass(frozen=True)
class IntakeDecision:
    planned_action: IntakeAction
    status: IntakePlanItemStatus
    reason_code: str
    reason_detail: str
    proposed_target_extension: str | None = None


def _normalize_extension(extension: str | None, relative_path: str) -> str:
    raw = (extension or "").strip().lower()
    if raw and not raw.startswith("."):
        raw = f".{raw}"
    if raw:
        return raw
    return PurePosixPath(relative_path).suffix.lower()


def _normalize_codec(value: str | None) -> str:
    return (value or "").strip().lower()


def _container_from_fields(
    extension: str, container_format: str | None
) -> str:
    if extension in _FRIENDLY_CONTAINERS:
        return extension
    fmt = (container_format or "").strip().lower()
    if not fmt:
        return extension
    # ffprobe format_name kann komma-getrennt sein (z. B. "mov,mp4,m4a")
    parts = {p.strip() for p in fmt.split(",") if p.strip()}
    for candidate in (".mp4", ".mov", ".m4v"):
        token = candidate.lstrip(".")
        if token in parts or candidate in parts:
            return candidate
    if extension:
        return extension
    if parts:
        first = next(iter(parts))
        return first if first.startswith(".") else f".{first}"
    return ""


def decide_intake_action(source: IntakeDecisionSource) -> IntakeDecision:
    """Entscheidet die Intake-Aktion rein aus gespeicherten Feldern."""
    validation = source.validation
    extension = _normalize_extension(source.extension, validation.source_relative_path)

    if validation.status in _BLOCKED_VALIDATION_STATUSES:
        return IntakeDecision(
            planned_action=IntakeAction.BLOCKED,
            status=IntakePlanItemStatus.BLOCKED,
            reason_code=validation.status.value,
            reason_detail=(
                validation.error_message
                or validation.error_code
                or f"Validation-Status: {validation.status.value}"
            ),
            proposed_target_extension=None,
        )

    if validation.status != AssetValidationStatus.PROBE_SUCCEEDED:
        return IntakeDecision(
            planned_action=IntakeAction.BLOCKED,
            status=IntakePlanItemStatus.BLOCKED,
            reason_code="validation_error",
            reason_detail=(
                f"Unerwarteter Validation-Status: {validation.status.value}"
            ),
        )

    kind = (validation.media_kind or "").strip().lower()
    if kind == MediaKind.VIDEO.value:
        return _decide_video(validation, extension)
    if kind == MediaKind.AUDIO.value:
        return _decide_audio(validation, extension)
    if kind == MediaKind.IMAGE.value:
        return _decide_image(extension)
    return IntakeDecision(
        planned_action=IntakeAction.BLOCKED,
        status=IntakePlanItemStatus.BLOCKED,
        reason_code="unsupported_media_kind",
        reason_detail=f"Medienart nicht unterstützbar: {kind or 'unbekannt'}",
    )


def _decide_video(
    validation: AssetValidationRecord, extension: str
) -> IntakeDecision:
    codec = _normalize_codec(validation.video_codec)
    container = _container_from_fields(extension, validation.container_format)
    pixel_format = (validation.pixel_format or "").strip().lower() or None
    bit_depth = validation.bit_depth

    # Ohne Codec/Container/Grundmaße keine sichere Copy-/Remux-Entscheidung.
    if not codec or not container or validation.width is None or validation.height is None:
        return IntakeDecision(
            planned_action=IntakeAction.TRANSCODE,
            status=IntakePlanItemStatus.PLANNED,
            reason_code="insufficient_copy_metadata",
            reason_detail=(
                "Für eine sichere Copy-/Remux-Entscheidung fehlen gespeicherte "
                "technische Metadaten (Codec, Container und/oder Auflösung)."
            ),
            proposed_target_extension=".mp4",
        )

    # Pixel-Format und Bit-Tiefe müssen aus Validation bekannt sein.
    if pixel_format is None or bit_depth is None:
        return IntakeDecision(
            planned_action=IntakeAction.TRANSCODE,
            status=IntakePlanItemStatus.PLANNED,
            reason_code="insufficient_copy_metadata",
            reason_detail=(
                "Pixel-Format und/oder Bit-Tiefe fehlen in der gespeicherten "
                "technischen Validation — Copy/Remux ist nicht zulässig."
            ),
            proposed_target_extension=".mp4",
        )

    if validation.error_code or validation.error_message:
        return IntakeDecision(
            planned_action=IntakeAction.TRANSCODE,
            status=IntakePlanItemStatus.PLANNED,
            reason_code="insufficient_copy_metadata",
            reason_detail=(
                validation.error_message
                or validation.error_code
                or "Gespeicherter technischer Fehler verhindert Copy/Remux."
            ),
            proposed_target_extension=".mp4",
        )

    if codec in _PROBLEMATIC_VIDEO_CODECS:
        return IntakeDecision(
            planned_action=IntakeAction.TRANSCODE,
            status=IntakePlanItemStatus.PLANNED,
            reason_code="problematic_video_codec",
            reason_detail=f"Problematischer Video-Codec: {codec}",
            proposed_target_extension=".mp4",
        )

    if codec not in _FRIENDLY_VIDEO_CODECS:
        return IntakeDecision(
            planned_action=IntakeAction.TRANSCODE,
            status=IntakePlanItemStatus.PLANNED,
            reason_code="unknown_video_codec",
            reason_detail=f"Unbekannter oder nicht freigegebener Video-Codec: {codec}",
            proposed_target_extension=".mp4",
        )

    if pixel_format not in _ALLOWED_PIXEL_FORMATS:
        return IntakeDecision(
            planned_action=IntakeAction.TRANSCODE,
            status=IntakePlanItemStatus.PLANNED,
            reason_code="incompatible_pixel_format",
            reason_detail=(
                f"Pixel-Format nicht für Copy/Remux freigegeben: {pixel_format}"
            ),
            proposed_target_extension=".mp4",
        )

    if bit_depth not in _ALLOWED_BIT_DEPTHS:
        return IntakeDecision(
            planned_action=IntakeAction.TRANSCODE,
            status=IntakePlanItemStatus.PLANNED,
            reason_code="incompatible_bit_depth",
            reason_detail=(
                f"Bit-Tiefe nicht für Copy/Remux freigegeben: {bit_depth}"
            ),
            proposed_target_extension=".mp4",
        )

    if container not in _FRIENDLY_CONTAINERS:
        return IntakeDecision(
            planned_action=IntakeAction.REMUX,
            status=IntakePlanItemStatus.PLANNED,
            reason_code="unsuitable_container",
            reason_detail=(
                f"Geeigneter Codec/Profil ({codec}, {pixel_format}, "
                f"{bit_depth}-bit), aber ungeeigneter Container ({container})."
            ),
            proposed_target_extension=".mp4",
        )

    return IntakeDecision(
        planned_action=IntakeAction.COPY,
        status=IntakePlanItemStatus.PLANNED,
        reason_code="copy_compatible",
        reason_detail=(
            f"Resolve-freundliches Profil "
            f"({codec}/{container}/{pixel_format}/{bit_depth}-bit)."
        ),
        proposed_target_extension=container,
    )


def _decide_audio(
    validation: AssetValidationRecord, extension: str
) -> IntakeDecision:
    codec = _normalize_codec(validation.audio_codec)
    if codec and extension in _SUITABLE_AUDIO_EXTENSIONS:
        return IntakeDecision(
            planned_action=IntakeAction.COPY,
            status=IntakePlanItemStatus.PLANNED,
            reason_code="copy_compatible",
            reason_detail=f"Erkanntes Audio ({codec}/{extension}).",
            proposed_target_extension=extension,
        )
    return IntakeDecision(
        planned_action=IntakeAction.BLOCKED,
        status=IntakePlanItemStatus.BLOCKED,
        reason_code="audio_not_assessable",
        reason_detail=(
            "Audio ist nicht ausreichend beurteilbar oder problematisch; "
            "kein Audio-Re-Encode geplant."
        ),
        proposed_target_extension=None,
    )


def _decide_image(extension: str) -> IntakeDecision:
    if extension in _COPY_IMAGE_EXTENSIONS:
        return IntakeDecision(
            planned_action=IntakeAction.COPY,
            status=IntakePlanItemStatus.PLANNED,
            reason_code="copy_compatible",
            reason_detail=f"Geeignetes Bildformat ({extension}).",
            proposed_target_extension=extension,
        )
    if extension in _TRANSCODE_IMAGE_EXTENSIONS:
        return IntakeDecision(
            planned_action=IntakeAction.TRANSCODE,
            status=IntakePlanItemStatus.PLANNED,
            reason_code="image_transcode_required",
            reason_detail=(
                f"Bildformat erfordert Transkodierung ({extension}); "
                "Zielendung noch nicht festgelegt."
            ),
            proposed_target_extension=None,
        )
    return IntakeDecision(
        planned_action=IntakeAction.BLOCKED,
        status=IntakePlanItemStatus.BLOCKED,
        reason_code="image_not_assessable",
        reason_detail=f"Bildformat nicht beurteilbar: {extension or 'unbekannt'}",
        proposed_target_extension=None,
    )


def build_plan_item(source: IntakeDecisionSource) -> IntakePlanItem:
    decision = decide_intake_action(source)
    extension = _normalize_extension(
        source.extension, source.validation.source_relative_path
    )
    validation = source.validation
    return IntakePlanItem(
        asset_id=validation.asset_id,
        validation_id=validation.validation_id,
        source_relative_path=validation.source_relative_path,
        source_group=source.source_group or validation.source_group or "__root__",
        media_kind=(validation.media_kind or MediaKind.OTHER.value),
        source_sha256=validation.sha256,
        extension=extension,
        container_format=validation.container_format,
        video_codec=validation.video_codec,
        audio_codec=validation.audio_codec,
        width=validation.width,
        height=validation.height,
        frame_rate_numerator=validation.frame_rate_numerator,
        frame_rate_denominator=validation.frame_rate_denominator,
        embedded_timecode=validation.embedded_timecode,
        pixel_format=validation.pixel_format,
        bit_depth=validation.bit_depth,
        duplicate_group_id=validation.duplicate_group_id,
        planned_action=decision.planned_action,
        status=decision.status,
        reason_code=decision.reason_code,
        reason_detail=decision.reason_detail,
        proposed_target_extension=decision.proposed_target_extension,
        processing_profile_version=PROCESSING_PROFILE_VERSION,
    )
