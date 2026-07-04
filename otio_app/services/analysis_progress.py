"""Fortschritts-Callbacks für lange Analyse-Läufe."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal, Optional

AnalysisPhase = Literal[
    "start",
    "folder_start",
    "folder_skip",
    "media_start",
    "media_done",
    "folder_done",
    "complete",
    "cancelled",
    "file_start",
    "file_done",
]

ProgressCallback = Callable[[AnalysisPhase, dict], None]


@dataclass
class AnalysisRunReport:
    folders_processed: list[str] = field(default_factory=list)
    folders_skipped: list[str] = field(default_factory=list)
    media_analyzed: int = 0
    media_cached: int = 0
    media_failed: int = 0
    failures: list[str] = field(default_factory=list)
    cancelled: bool = False


@dataclass
class VoiceAnalysisRunReport:
    files_analyzed: int = 0
    files_cached: int = 0
    files_failed: int = 0
    failures: list[str] = field(default_factory=list)
    cancelled: bool = False
    output_written: bool = False


def noop_progress(_phase: AnalysisPhase, _data: dict) -> None:
    return None
