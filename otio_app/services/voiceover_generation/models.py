"""Datenmodelle für die Dramaturgie-/Voice-over-Generierungs-Pipeline (Phase 2).

Ausschließlich für "Projekt ohne Voice-Over". Diese Modelle dürfen nicht mit
EditPlanDocument oder anderen Produktions-Modellen vermischt werden.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from otio_app.defaults import (
    BRIEF_LANGUAGE_CHOICES,
    BRIEF_NEGATIVE_RULE_FLAGS,
    BRIEF_NEGATIVE_RULE_LABELS,
    BRIEF_TONE_TAG_CHOICES,
    DEFAULT_NEGATIVE_RULE_FLAGS,
    DRAMATURGY_ROLE_LABELS,
    DRAMATURGY_ROLES,
    DRAMATURGY_STATUS_CONFIRMED,
    DRAMATURGY_STATUS_DRAFT,
    VOICEOVER_GEN_DEFAULT_MODEL,
    VOICEOVER_GEN_DEFAULT_PROVIDER,
    VOICEOVER_GEN_DEFAULT_WORD_TOLERANCE_PERCENT,
    VOICEOVER_GEN_MAX_FOLDER_WORDS,
    VOICEOVER_GEN_MIN_FOLDER_WORDS,
    VOICEOVER_GEN_MODEL_PRESETS,
    VOICEOVER_GEN_PROVIDERS,
    VOICEOVER_GEN_ROLE_LABELS,
    VOICEOVER_GEN_ROLES,
)

__all__ = [
    "BRIEF_LANGUAGE_CHOICES",
    "BRIEF_NEGATIVE_RULE_FLAGS",
    "BRIEF_NEGATIVE_RULE_LABELS",
    "BRIEF_TONE_TAG_CHOICES",
    "DEFAULT_NEGATIVE_RULE_FLAGS",
    "DRAMATURGY_ROLE_LABELS",
    "DRAMATURGY_ROLES",
    "DRAMATURGY_STATUS_CONFIRMED",
    "DRAMATURGY_STATUS_DRAFT",
    "VOICEOVER_GEN_DEFAULT_MODEL",
    "VOICEOVER_GEN_DEFAULT_PROVIDER",
    "VOICEOVER_GEN_DEFAULT_WORD_TOLERANCE_PERCENT",
    "VOICEOVER_GEN_MAX_FOLDER_WORDS",
    "VOICEOVER_GEN_MIN_FOLDER_WORDS",
    "VOICEOVER_GEN_MODEL_PRESETS",
    "VOICEOVER_GEN_PROVIDERS",
    "VOICEOVER_GEN_ROLE_LABELS",
    "VOICEOVER_GEN_ROLES",
    "ProjectBrief",
    "VoiceoverStyleReferences",
    "VoiceoverStyleProfile",
    "LlmRoleSettings",
    "VoiceoverGenerationModelSettings",
    "LlmRunManifest",
    "FolderInventorySummary",
    "FolderInventorySummariesDocument",
    "DramaturgyFolderEntry",
    "DramaturgyPlan",
]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ProjectBrief(BaseModel):
    project_id: str
    generated_at: datetime = Field(default_factory=_utcnow)
    video_title: str = ""
    language: str = "DE"
    tone_tags: list[str] = Field(default_factory=list)
    negative_rule_flags: dict[str, bool] = Field(default_factory=dict)
    negative_rules_freetext: str = ""
    forbidden_phrases: list[str] = Field(default_factory=list)
    global_extra_prompt: str = ""


class VoiceoverStyleReferences(BaseModel):
    project_id: str
    generated_at: datetime = Field(default_factory=_utcnow)
    intro_reference_texts: list[str] = Field(default_factory=list)
    segment_reference_texts: list[str] = Field(default_factory=list)
    uploaded_file_names: list[str] = Field(default_factory=list)
    uploaded_file_texts: list[str] = Field(default_factory=list)


class VoiceoverStyleProfile(BaseModel):
    project_id: str
    generated_at: datetime = Field(default_factory=_utcnow)
    language: str = ""
    overall_tone: str = ""
    narration_style: str = ""
    sentence_length: str = ""
    pacing: str = ""
    imagery_style: str = ""
    intro_hook_style: str = ""
    segment_style: str = ""
    do: list[str] = Field(default_factory=list)
    dont: list[str] = Field(default_factory=list)
    forbidden_phrases: list[str] = Field(default_factory=list)
    avoid_copying_reference_text: bool = True
    style_summary_for_prompts: str = ""
    source_reference_hash: str = ""
    project_brief_hash: str = ""
    llm_run_id: str = ""


class LlmRoleSettings(BaseModel):
    provider: str = VOICEOVER_GEN_DEFAULT_PROVIDER
    model: str = VOICEOVER_GEN_DEFAULT_MODEL


class VoiceoverGenerationModelSettings(BaseModel):
    style_profile: LlmRoleSettings = Field(default_factory=LlmRoleSettings)
    dramaturgy: LlmRoleSettings = Field(default_factory=LlmRoleSettings)
    voiceover_author: LlmRoleSettings = Field(default_factory=LlmRoleSettings)
    voiceover_review: LlmRoleSettings = Field(default_factory=LlmRoleSettings)
    intro: LlmRoleSettings = Field(default_factory=LlmRoleSettings)


class LlmRunManifest(BaseModel):
    """Pflichtfelder aus der LLM-Traceability-Vorgabe (§8)."""

    run_id: str
    stage: str
    provider: str
    model: str
    prompt_hash: str
    created_at: datetime = Field(default_factory=_utcnow)
    status: str = "PASS"
    latency_ms: int = 0
    token_usage: dict[str, int] = Field(default_factory=dict)


def as_str_list(value: Any) -> list[str]:
    """Robuste Konvertierung einer LLM-Antwort in eine Liste von Strings."""
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


class FolderInventorySummary(BaseModel):
    """Reine Python-Aggregation aus dem Inventory — kein LLM-Call (Phase 3 §1)."""

    folder_name: str
    asset_count: int = 0
    video_count: int = 0
    image_count: int = 0
    total_video_duration_sec: float = 0.0
    average_video_duration_sec: float = 0.0
    has_people: bool = False
    has_motion: bool = False
    has_wide_shots: bool = False
    has_detail_shots: bool = False
    has_establishing_shots: bool = False
    dominant_visual_themes: list[str] = Field(default_factory=list)
    notable_asset_descriptions: list[str] = Field(default_factory=list)
    visual_strength_score: float = 0.0
    asset_diversity_score: float = 0.0
    estimated_voiceover_word_count: int = 0
    estimated_min_words: int = 0
    estimated_max_words: int = 0
    risks: list[str] = Field(default_factory=list)


class FolderInventorySummariesDocument(BaseModel):
    """Debug-Artefakt: exakt das, was an das Dramaturgie-LLM ging."""

    project_id: str
    generated_at: datetime = Field(default_factory=_utcnow)
    folder_summaries: list[FolderInventorySummary] = Field(default_factory=list)


class DramaturgyFolderEntry(BaseModel):
    folder_name: str
    order_index: int
    enabled: bool = True
    dramaturgy_role: str = "setup"
    reason: str = ""
    visual_strength_score: float = 0.0
    asset_diversity_score: float = 0.0
    hook_potential_score: float = 0.0
    recommended_word_count: int = 0
    recommended_min_words: int = 0
    recommended_max_words: int = 0
    transition_goal_to_next: str = ""
    transition_from_previous_hint: str = ""
    contrast_or_commonality_hint: str = ""
    risks: list[str] = Field(default_factory=list)


class DramaturgyPlan(BaseModel):
    project_id: str
    generated_at: datetime = Field(default_factory=_utcnow)
    confirmed_at: datetime | None = None
    language: str = "DE"
    project_title: str = ""
    core_promise: str = ""
    narrative_arc: str = ""
    recommended_folder_order: list[DramaturgyFolderEntry] = Field(default_factory=list)
    global_transition_strategy: str = ""
    inventory_summary_hash: str = ""
    project_brief_hash: str = ""
    style_profile_hash: str = ""
    llm_run_id: str = ""
    status: str = DRAMATURGY_STATUS_DRAFT
    risks: list[str] = Field(default_factory=list)
