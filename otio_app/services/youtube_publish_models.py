"""YouTube Publish: Beschreibung, Hashtags, Kapitel, Quiz aus Timeline-Kapiteln."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class YouTubeChapter(BaseModel):
    folder_name: str
    display_title: str
    video_start_sec: float = 0.0
    video_duration_sec: float = 0.0
    timestamp: str = "00:00"


class YouTubeQuizOption(BaseModel):
    label: str
    text: str
    is_correct: bool = False


class YouTubeQuizItem(BaseModel):
    order_index: int = 1
    question: str = ""
    options: list[YouTubeQuizOption] = Field(default_factory=list)
    correct_option_label: str = ""
    insert_at_sec: float = 0.0
    insert_timestamp: str = "00:00"
    reason: str = ""


class YouTubeMetadataDocument(BaseModel):
    project_id: str
    generated_at: datetime = Field(default_factory=_utcnow)
    language: str = "DE"
    title: str = ""
    # On-screen card: translated "Die Wunder von" + country/region (two lines).
    wonders_title_formula: str = ""
    wonders_title_place: str = ""
    description: str = ""
    description_body: str = ""
    hashtags: str = ""
    chapters: list[YouTubeChapter] = Field(default_factory=list)
    quizzes: list[YouTubeQuizItem] = Field(default_factory=list)
    total_duration_sec: float = 0.0
    quiz_count_target: int = 0
    folder_names: list[str] = Field(default_factory=list)
    provider: str = ""
    model: str = ""
    llm_run_id: str = ""
    status: str = "PASS"
    error: str = ""

    def formatted_wonders_title(self) -> str:
        formula = (self.wonders_title_formula or "").strip()
        place = (self.wonders_title_place or "").strip()
        if formula and place:
            return f"{formula}\n{place}"
        return formula or place


class YouTubePublishContext(BaseModel):
    """Deterministischer Kontext für Prompt + Persistenz (ohne LLM-Text)."""

    title: str = ""
    language: str = "DE"
    total_duration_sec: float = 0.0
    quiz_count: int = 1
    chapters: list[YouTubeChapter] = Field(default_factory=list)
    intro_text: str = ""
    folder_scripts: list[dict[str, str]] = Field(default_factory=list)
    folder_names: list[str] = Field(default_factory=list)


class YouTubePublishResult(BaseModel):
    status: str = "PASS"
    document: Optional[YouTubeMetadataDocument] = None
    error: str = ""
    llm_run_id: str = ""
    provider: str = ""
    model: str = ""
