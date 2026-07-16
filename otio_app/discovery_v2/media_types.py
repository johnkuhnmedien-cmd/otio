"""Discovery-eigene Endungszuordnung — Wrapper um gemeinsame Listen ohne Schreiblogik."""

from __future__ import annotations

from otio_app.discovery_v2.domain.inventory import MediaKind
from otio_app.services.media_utils import (
    AUDIO_EXTENSIONS,
    IMAGE_EXTENSIONS,
    VIDEO_EXTENSIONS,
)


def classify_media_kind(extension: str) -> MediaKind:
    """Klassifiziert eine Dateiendung; unbekannte Endungen → other."""
    ext = extension.lower()
    if not ext.startswith(".") and ext:
        ext = f".{ext}"
    if ext in VIDEO_EXTENSIONS:
        return MediaKind.VIDEO
    if ext in IMAGE_EXTENSIONS:
        return MediaKind.IMAGE
    if ext in AUDIO_EXTENSIONS:
        return MediaKind.AUDIO
    return MediaKind.OTHER
