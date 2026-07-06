"""Datenmodelle für Analyse-Ergebnisse (JSON-Ausgabe)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from pydantic import BaseModel, Field

from otio_app.defaults import DEFAULT_GEMINI_MODEL


class VoiceWord(BaseModel):
    start_sec: float
    end_sec: float
    word: str


class VoiceSegment(BaseModel):
    start_sec: float
    end_sec: float
    text: str
    words: List[VoiceWord] = Field(default_factory=list)


class VoiceFileAnalysis(BaseModel):
    path: str
    duration_sec: Optional[float] = None
    segments: List[VoiceSegment] = Field(default_factory=list)
    error: Optional[str] = None


class VoiceAnalysisDocument(BaseModel):
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    project_id: str
    language: str
    files: List[VoiceFileAnalysis] = Field(default_factory=list)


class MediaProbeInfo(BaseModel):
    duration_sec: Optional[float] = None
    video_codec: Optional[str] = None
    audio_codec: Optional[str] = None
    pixel_format: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    container: Optional[str] = None


class CleanMediaEntry(BaseModel):
    original_path: str
    clean_path: Optional[str] = None
    status: str = "pending"
    needs_transcode: bool = False
    decode_ok: bool = True
    probe: Optional[MediaProbeInfo] = None
    error: Optional[str] = None
    transcoded_at: Optional[datetime] = None


class CleanMediaManifest(BaseModel):
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    project_id: str
    folder: str
    entries: List[CleanMediaEntry] = Field(default_factory=list)


class AssetMediaAnalysis(BaseModel):
    path: str
    description: str = ""
    frames_used: List[str] = Field(default_factory=list)
    error: Optional[str] = None


class AssetFolderAnalysis(BaseModel):
    folder: str
    description: str = ""
    media_files: List[str] = Field(default_factory=list)
    frames_used: List[str] = Field(default_factory=list)
    assets: List[AssetMediaAnalysis] = Field(default_factory=list)
    error: Optional[str] = None


class InventoryDocument(BaseModel):
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    project_id: str
    items: List[AssetFolderAnalysis] = Field(default_factory=list)


class ManualFolderCompletionDocument(BaseModel):
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    project_id: str
    folders: List[str] = Field(default_factory=list)


class VoiceFolderMappingEntry(BaseModel):
    voice_file: str
    folder: Optional[str] = None
    match_method: str = "filename"
    confirmed: bool = False


class VoiceFolderMappingDocument(BaseModel):
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    project_id: str
    confirmed: bool = False
    entries: List[VoiceFolderMappingEntry] = Field(default_factory=list)


class EditPlanSettings(BaseModel):
    shot_min_sec: float = 3.0
    shot_max_sec: float = 8.0
    audio_offset_sec: float = 1.0
    section_outro_sec: float = 5.0
    text_splitters: List[str] = Field(default_factory=lambda: [", und ", ", ", " und "])
    fallback_order: List[str] = Field(
        default_factory=lambda: ["local", "adobe_stock", "pexels", "gemini_image"]
    )
    gemini_model: str = "gemini-2.0-flash"


class EditPlanRule(BaseModel):
    """Eine Schnittregel — dauerhaft pro Projekt gespeichert."""

    id: str
    rule_type: str
    enabled: bool = True
    params: dict[str, int | float | str | bool] = Field(default_factory=dict)
    label: str = ""


class EditPlanRulesDocument(BaseModel):
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    project_id: str
    rules: List[EditPlanRule] = Field(default_factory=list)
    gemini_prompt: str = ""


class EditPlanShot(BaseModel):
    voice_file: str
    folder: str
    voice_start_sec: float
    voice_end_sec: float
    duration_sec: float
    asset_path: Optional[str] = None
    asset_source: str = "local"
    motif: str = ""
    passage_text: str = ""
    confidence: Optional[str] = None
    section_outro: bool = False


class TimelineItemTransform(BaseModel):
    scaling_mode: str = "fill"
    zoom_x: float = 1.0
    zoom_y: float = 1.0
    position_x: float = 0.0
    position_y: float = 0.0


class TimelineItem(BaseModel):
    timeline_item_id: str
    type: str
    section_id: str
    folder_name: str
    voice_file: str = ""
    asset_id: str = ""
    shot_id: str = ""
    resolved_media_path: str = ""
    original_asset_path: Optional[str] = None
    asset_role: str = ""
    timeline_in_sec: float = 0.0
    timeline_out_sec: float = 0.0
    duration_sec: float = 0.0
    final_duration_sec: float = 0.0
    source_in_sec: float = 0.0
    source_out_sec: float = 0.0
    voice_start_sec: float = 0.0
    voice_end_sec: float = 0.0
    selection_reason: str = ""
    confidence: float = 0.0
    transform: TimelineItemTransform = Field(default_factory=TimelineItemTransform)
    warnings: List[str] = Field(default_factory=list)
    media_source_type: str = "local"
    motif: str = ""
    passage_text: str = ""
    allow_black: bool = False


class EditPlanDocument(BaseModel):
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    project_id: str
    folder_name: Optional[str] = None
    confirmed: bool = False
    settings: EditPlanSettings = Field(default_factory=EditPlanSettings)
    shots: List[EditPlanShot] = Field(default_factory=list)
    timeline_items: List[TimelineItem] = Field(default_factory=list)
    allow_black_outro: bool = False
