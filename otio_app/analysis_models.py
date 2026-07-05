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


class EditPlanDocument(BaseModel):
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    project_id: str
    folder_name: Optional[str] = None
    confirmed: bool = False
    settings: EditPlanSettings = Field(default_factory=EditPlanSettings)
    shots: List[EditPlanShot] = Field(default_factory=list)
