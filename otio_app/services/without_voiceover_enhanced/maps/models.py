"""Datenmodell für Enhanced-Kartenpläne und Renderstatus."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

MAP_STYLE_VERSION = "map_style_v1"
ENGINE_STYLE_VERSION = "otio-vintage-map-v11"
MAP_DURATION_SEC = 9.0
MAP_FPS = 25
MAP_DURATION_FRAMES = 225
MAP_FADE_SEC = 0.5
MAP_RESOLUTION_HD = "hd"
MAP_RESOLUTION_4K = "4k"
MAP_HD_WIDTH = 1920
MAP_HD_HEIGHT = 1080
MAP_4K_WIDTH = 3840
MAP_4K_HEIGHT = 2160
MAP_MAX_PARALLEL_HD = 4
MAP_MAX_PARALLEL_4K = 2
MAP_ANIMATION_OPENING = "opening"
MAP_ANIMATION_TRANSITION = "transition"
COORDINATE_STATUS_MISSING = "missing"
COORDINATE_STATUS_NEEDS_REVIEW = "needs_review"
COORDINATE_STATUS_RESOLVED = "resolved"
COORDINATE_STATUS_MANUAL = "manual"
RENDER_STATUS_IDLE = "idle"
RENDER_STATUS_BLOCKED = "blocked"
RENDER_STATUS_WAITING = "waiting"
RENDER_STATUS_PREPARING = "preparing"
RENDER_STATUS_RENDERING = "rendering"
RENDER_STATUS_VALIDATING = "validating"
RENDER_STATUS_DONE = "done"
RENDER_STATUS_FAILED = "failed"
RENDER_STATUS_CANCELLED = "cancelled"
CONFIDENCE_AUTO_RENDER_MIN = 0.75

RENDER_STATUS_LABELS: dict[str, str] = {
    RENDER_STATUS_IDLE: "wartet",
    RENDER_STATUS_WAITING: "wartet",
    RENDER_STATUS_PREPARING: "Renderer wird vorbereitet",
    RENDER_STATUS_RENDERING: "wird gerendert",
    RENDER_STATUS_VALIDATING: "wird geprüft",
    RENDER_STATUS_DONE: "fertig",
    RENDER_STATUS_FAILED: "fehlgeschlagen",
    RENDER_STATUS_CANCELLED: "abgebrochen",
    RENDER_STATUS_BLOCKED: "blockiert",
}

MAP_HEADING_BY_LANGUAGE: dict[str, str] = {
    "EN": "Travel Route",
    "DE": "Reiseroute",
    "FR": "Itinéraire",
    "IT": "Itinerario",
    "ES": "Ruta de viaje",
    "PT": "Rota de viagem",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MapRenderSettings(BaseModel):
    resolution: Literal["hd", "4k"] = MAP_RESOLUTION_HD
    max_parallel: int = MAP_MAX_PARALLEL_HD
    show_vehicle: bool = False


class MapCoordinateRecord(BaseModel):
    chapter_id: str
    original_label: str
    display_label: str = ""
    latitude: float | None = None
    longitude: float | None = None
    confidence: float = 0.0
    status: str = COORDINATE_STATUS_MISSING
    source: str = ""
    country_context: str = ""
    note: str = ""

    @property
    def has_coordinates(self) -> bool:
        return self.latitude is not None and self.longitude is not None


class MapCoordinatesDocument(BaseModel):
    project_id: str
    country: str = ""
    updated_at: datetime = Field(default_factory=_utcnow)
    places: dict[str, MapCoordinateRecord] = Field(default_factory=dict)


class MapPlanItem(BaseModel):
    map_sequence_id: str
    project_id: str
    chapter_id: str
    chapter_ordinal: int
    chapter_count: int
    original_chapter_label: str
    localized_display_label: str
    heading: str
    language: str
    country: str
    from_chapter_id: str = ""
    from_original_chapter_label: str = ""
    from_localized_display_label: str = ""
    start_latitude: float | None = None
    start_longitude: float | None = None
    end_latitude: float | None = None
    end_longitude: float | None = None
    start_coordinate_status: str = COORDINATE_STATUS_MISSING
    end_coordinate_status: str = COORDINATE_STATUS_MISSING
    animation_mode: str
    show_vehicle: bool = False
    duration_in_frames: int = MAP_DURATION_FRAMES
    fps: int = MAP_FPS
    resolution: str = MAP_RESOLUTION_HD
    width: int = MAP_HD_WIDTH
    height: int = MAP_HD_HEIGHT
    style_version: str = MAP_STYLE_VERSION
    output_filename: str
    plan_hash: str = ""
    render_status: str = RENDER_STATUS_IDLE
    blocked_reason: str = ""
    output_path: str = ""
    progress: float = 0.0
    error_detail: str = ""
    media_hash: str = ""

    @property
    def content_hash(self) -> str:
        return self.plan_hash

    @property
    def coordinate_status(self) -> str:
        """Worst-case status of start/end (missing < review < resolved < manual)."""
        rank = {
            COORDINATE_STATUS_MISSING: 0,
            COORDINATE_STATUS_NEEDS_REVIEW: 1,
            COORDINATE_STATUS_RESOLVED: 2,
            COORDINATE_STATUS_MANUAL: 3,
        }
        statuses = [self.start_coordinate_status]
        if self.animation_mode != MAP_ANIMATION_OPENING:
            statuses.append(self.end_coordinate_status)
        return min(statuses, key=lambda status: rank.get(status, 0))


class MapPlanDocument(BaseModel):
    project_id: str
    language: str
    country: str = ""
    heading: str = ""
    chapter_count: int = 0
    dramaturgy_fingerprint: str = ""
    settings: MapRenderSettings = Field(default_factory=MapRenderSettings)
    style_version: str = MAP_STYLE_VERSION
    created_at: datetime = Field(default_factory=_utcnow)
    maps: list[MapPlanItem] = Field(default_factory=list)
