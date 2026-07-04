"""Datenmodelle für Analyse-Ergebnisse (JSON-Ausgabe)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from pydantic import BaseModel, Field


class VoiceSegment(BaseModel):
    start_sec: float
    end_sec: float
    text: str


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
