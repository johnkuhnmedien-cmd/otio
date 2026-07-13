"""Datenmodelle für Kapitel-Karten (Nano Banana / Gemini Image)."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field

from otio_app.defaults import (
    CHAPTER_MAP_ASPECT_RATIO,
    CHAPTER_MAP_MODEL_DEFAULT,
    CHAPTER_MAP_STATUS_MISSING,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ChapterMapSettings(BaseModel):
    """Pfad zu Style-Beispielbildern und Modellwahl."""

    style_example_1_path: str = ""
    style_example_2_path: str = ""
    model: str = CHAPTER_MAP_MODEL_DEFAULT
    aspect_ratio: str = CHAPTER_MAP_ASPECT_RATIO


class ChapterMapEntry(BaseModel):
    order_index: int
    display_number: int = 0
    folder_name: str
    filename: str = ""
    relative_path: str = ""
    absolute_path: str = ""
    previous_map_path: str = ""
    language: str = "EN"
    model: str = CHAPTER_MAP_MODEL_DEFAULT
    status: str = CHAPTER_MAP_STATUS_MISSING
    error: str = ""
    width: int = 0
    height: int = 0
    generated_at: datetime | None = None


class ChapterMapManifest(BaseModel):
    project_id: str
    language: str = "EN"
    model: str = CHAPTER_MAP_MODEL_DEFAULT
    aspect_ratio: str = CHAPTER_MAP_ASPECT_RATIO
    updated_at: datetime = Field(default_factory=_utcnow)
    entries: list[ChapterMapEntry] = Field(default_factory=list)
