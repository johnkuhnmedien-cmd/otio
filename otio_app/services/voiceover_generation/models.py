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
    BRIEF_NEGATIVE_RULE_INSTRUCTIONS,
    BRIEF_NEGATIVE_RULE_LABELS,
    BRIEF_TONE_TAG_CHOICES,
    DEFAULT_NEGATIVE_RULE_FLAGS,
    DRAMATURGY_ROLE_LABELS,
    DRAMATURGY_ROLES,
    DRAMATURGY_STATUS_CONFIRMED,
    DRAMATURGY_STATUS_DRAFT,
    ENERGY_CHOICES,
    ENERGY_LABELS,
    ENERGY_MEDIUM,
    FACTUALITY_MODE_CHOICES,
    FACTUALITY_MODE_LABELS,
    FACTUALITY_MODE_NORMAL_SAFE_GENERAL_KNOWLEDGE,
    AUDIO_SCOPE_FOLDER,
    AUDIO_STATUS_MISSING,
    ELEVENLABS_DEFAULT_MODEL_ID,
    ELEVENLABS_DEFAULT_OUTPUT_FORMAT,
    INTRO_HOOK_DEFAULT_MAX_WORDS,
    INTRO_HOOK_DEFAULT_MIN_WORDS,
    INTRO_HOOK_DEFAULT_TARGET_WORDS,
    INTRO_HOOK_STATUS_CONFIRMED,
    INTRO_HOOK_STATUS_DRAFT,
    INTRO_HOOK_TYPE_CINEMATIC_PROMISE,
    ITEM_READINESS_MISSING_AUDIO,
    MAX_VOICEOVER_REVIEW_ATTEMPTS,
    PLAN_STATUS_TEXT_READY,
    TTS_RUN_STATUS_FAIL,
    VO_ERROR_TYPES_ALL,
    VO_ERROR_TYPES_DETERMINISTIC,
    VO_ERROR_TYPES_LLM_REVIEW,
    VOICEOVER_GEN_DEFAULT_MODEL,
    VOICEOVER_GEN_DEFAULT_PROVIDER,
    VOICEOVER_GEN_DEFAULT_WORD_TOLERANCE_PERCENT,
    VOICEOVER_GEN_MAX_FOLDER_WORDS,
    VOICEOVER_GEN_MIN_FOLDER_WORDS,
    VOICEOVER_GEN_MODEL_PRESETS,
    VOICEOVER_GEN_PROVIDERS,
    VOICEOVER_GEN_ROLE_LABELS,
    VOICEOVER_GEN_ROLES,
    VOICEOVER_SETTING_STATUS_PENDING,
    VOICEOVER_STATUS_DRAFT,
    WEAK_ASSET_MATCH_CONFIDENCE_THRESHOLD,
)

__all__ = [
    "BRIEF_LANGUAGE_CHOICES",
    "BRIEF_NEGATIVE_RULE_FLAGS",
    "BRIEF_NEGATIVE_RULE_INSTRUCTIONS",
    "BRIEF_NEGATIVE_RULE_LABELS",
    "BRIEF_TONE_TAG_CHOICES",
    "DEFAULT_NEGATIVE_RULE_FLAGS",
    "DRAMATURGY_ROLE_LABELS",
    "DRAMATURGY_ROLES",
    "DRAMATURGY_STATUS_CONFIRMED",
    "DRAMATURGY_STATUS_DRAFT",
    "ENERGY_CHOICES",
    "ENERGY_LABELS",
    "FACTUALITY_MODE_CHOICES",
    "FACTUALITY_MODE_LABELS",
    "MAX_VOICEOVER_REVIEW_ATTEMPTS",
    "VO_ERROR_TYPES_ALL",
    "VO_ERROR_TYPES_DETERMINISTIC",
    "VO_ERROR_TYPES_LLM_REVIEW",
    "VOICEOVER_GEN_DEFAULT_MODEL",
    "VOICEOVER_GEN_DEFAULT_PROVIDER",
    "VOICEOVER_GEN_DEFAULT_WORD_TOLERANCE_PERCENT",
    "VOICEOVER_GEN_MAX_FOLDER_WORDS",
    "VOICEOVER_GEN_MIN_FOLDER_WORDS",
    "VOICEOVER_GEN_MODEL_PRESETS",
    "VOICEOVER_GEN_PROVIDERS",
    "VOICEOVER_GEN_ROLE_LABELS",
    "VOICEOVER_GEN_ROLES",
    "WEAK_ASSET_MATCH_CONFIDENCE_THRESHOLD",
    "ProjectBrief",
    "VoiceoverStyleReferences",
    "VoiceoverStyleProfile",
    "StyleProfileLibraryEntry",
    "StyleProfileLibrary",
    "LlmRoleSettings",
    "VoiceoverGenerationModelSettings",
    "LlmRunManifest",
    "FolderInventorySummary",
    "FolderInventorySummariesDocument",
    "DramaturgyFolderEntry",
    "DramaturgyPlan",
    "FolderVoiceoverSetting",
    "FolderVoiceoverSettingsDocument",
    "SentenceItem",
    "FolderVoiceoverDraft",
    "FolderVoiceoversDocument",
    "ValidationError",
    "FolderVoiceoverValidationReport",
    "FolderVoiceoverValidationReportsDocument",
    "IntroHookSettings",
    "IntroHookVisualBeat",
    "IntroHookCandidate",
    "IntroHookCandidatesDocument",
    "ConfirmedIntroHook",
    "ElevenLabsSettings",
    "TtsRunManifest",
    "VoiceoverAudioItem",
    "VoiceoverAudioManifest",
    "AlignmentItem",
    "VoiceoverAlignment",
    "ReadinessError",
    "ConfirmedIntroPlanItem",
    "ConfirmedFolderPlanItem",
    "ProjectPlanReadiness",
    "ConfirmedVoiceoverProjectPlan",
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


class StyleProfileLibraryEntry(BaseModel):
    """Ein benannter, projektübergreifend wiederverwendbarer Style-Profile-Snapshot."""

    name: str
    profile: VoiceoverStyleProfile
    saved_at: datetime = Field(default_factory=_utcnow)


class StyleProfileLibrary(BaseModel):
    """Projektübergreifende Bibliothek gespeicherter Style Profiles.

    Wird NICHT unter dem Arbeitsordner eines Projekts gespeichert, sondern
    global (siehe style_profile_library_service.py), damit ein einmal
    erstelltes Style Profile in jedem weiteren Projekt wiederverwendet werden
    kann."""

    entries: list[StyleProfileLibraryEntry] = Field(default_factory=list)


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


# --- Phase 4: Folder Voice-overs ---


class FolderVoiceoverSetting(BaseModel):
    """Pro-Ordner-Einstellungen für die Voice-over-Erzeugung.

    Defaults werden aus dem bestätigten Dramaturgie-Eintrag vorbefüllt
    (siehe folder_voiceover_settings_service.build_default_folder_voiceover_settings)."""

    folder_name: str
    order_index: int = 0
    enabled: bool = True
    dramaturgy_role: str = "setup"
    target_words: int = 90
    min_words: int = 80
    max_words: int = 100
    word_tolerance_percent: int = VOICEOVER_GEN_DEFAULT_WORD_TOLERANCE_PERCENT
    transition_from_previous: bool = False
    callback_to_previous: bool = False
    use_contrast_with_previous: bool = False
    use_commonality_with_previous: bool = False
    folder_extra_prompt: str = ""
    must_include: list[str] = Field(default_factory=list)
    must_avoid: list[str] = Field(default_factory=list)
    factuality_mode: str = FACTUALITY_MODE_NORMAL_SAFE_GENERAL_KNOWLEDGE
    energy: str = ENERGY_MEDIUM
    status: str = VOICEOVER_SETTING_STATUS_PENDING


class FolderVoiceoverSettingsDocument(BaseModel):
    project_id: str
    generated_at: datetime = Field(default_factory=_utcnow)
    dramaturgy_hash: str = ""
    settings: list[FolderVoiceoverSetting] = Field(default_factory=list)


class SentenceItem(BaseModel):
    """Ein Satz/Beat der Voice-over-Prosa mit strukturierter Asset-Zuordnung.

    Der Zuschauer sieht nur den Fließtext (voiceover_text_full) — sentence_items
    sind die interne, maschinenlesbare Grundlage für Schnittplan, TTS-Alignment
    und Supplement Requests in späteren Phasen."""

    sentence_id: str
    beat_id: str = ""
    text: str = ""
    visual_intent: str = ""
    primary_asset_id: str = ""
    backup_asset_ids: list[str] = Field(default_factory=list)
    asset_match_reason: str = ""
    asset_confidence: float = 0.0
    estimated_duration_sec: float = 0.0
    must_show: list[str] = Field(default_factory=list)
    avoid_showing: list[str] = Field(default_factory=list)
    needs_supplement_asset: bool = False
    supplement_reason: str = ""
    source_inventory_asset_ids_considered: list[str] = Field(default_factory=list)


class FolderVoiceoverDraft(BaseModel):
    project_id: str
    folder_name: str
    order_index: int = 0
    language: str = "DE"
    target_words: int = 0
    min_words: int = 0
    max_words: int = 0
    voiceover_text_full: str = ""
    word_count: int = 0
    sentence_items: list[SentenceItem] = Field(default_factory=list)
    transition_from_previous_used: bool = False
    callback_to_previous_used: bool = False
    contrast_or_commonality_used: bool = False
    used_asset_evidence: list[str] = Field(default_factory=list)
    author_run_id: str = ""
    review_run_id: str = ""
    correction_run_ids: list[str] = Field(default_factory=list)
    status: str = VOICEOVER_STATUS_DRAFT
    risks: list[str] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    confirmed_at: datetime | None = None
    # Staleness-Hashes (§13) — welche Eingaben galten zum Zeitpunkt der Erzeugung.
    project_brief_hash: str = ""
    style_profile_hash: str = ""
    dramaturgy_hash: str = ""
    settings_hash: str = ""
    inventory_hash: str = ""


class FolderVoiceoversDocument(BaseModel):
    project_id: str
    generated_at: datetime = Field(default_factory=_utcnow)
    language: str = "DE"
    items: list[FolderVoiceoverDraft] = Field(default_factory=list)
    status: str = VOICEOVER_STATUS_DRAFT


class ValidationError(BaseModel):
    type: str
    severity: str = "BLOCKER"  # WARNING|BLOCKER
    folder_name: str = ""
    sentence_id: str = ""
    message: str = ""
    fix_hint: str = ""
    retryable: bool = True


class FolderVoiceoverValidationReport(BaseModel):
    project_id: str
    generated_at: datetime = Field(default_factory=_utcnow)
    folder_name: str
    attempt_count: int = 1
    status: str = "PASS"  # PASS|NEEDS_USER_REVIEW|FAIL
    errors: list[ValidationError] = Field(default_factory=list)
    warnings: list[ValidationError] = Field(default_factory=list)
    author_run_ids: list[str] = Field(default_factory=list)
    review_run_ids: list[str] = Field(default_factory=list)
    correction_run_ids: list[str] = Field(default_factory=list)


class FolderVoiceoverValidationReportsDocument(BaseModel):
    """Ein Dokument für alle Ordner — Pfad ist projektweit singular, Inhalt
    ist eine Sammlung pro-Ordner-Reports (Key: folder_name)."""

    project_id: str
    generated_at: datetime = Field(default_factory=_utcnow)
    reports: dict[str, FolderVoiceoverValidationReport] = Field(default_factory=dict)


# --- Phase 5: Intro-Hook ---


class IntroHookSettings(BaseModel):
    project_id: str
    generated_at: datetime = Field(default_factory=_utcnow)
    language: str = "DE"
    target_words: int = INTRO_HOOK_DEFAULT_TARGET_WORDS
    min_words: int = INTRO_HOOK_DEFAULT_MIN_WORDS
    max_words: int = INTRO_HOOK_DEFAULT_MAX_WORDS
    word_tolerance_percent: int = VOICEOVER_GEN_DEFAULT_WORD_TOLERANCE_PERCENT
    tone: str = "cinematic"
    freeform_rule_for_llm: str = ""
    forbidden_phrases: list[str] = Field(default_factory=list)
    allow_questions: bool = True
    allow_strong_claim: bool = True
    allow_direct_place_name: bool = True
    allow_tease_multiple_places: bool = True
    must_include: list[str] = Field(default_factory=list)
    must_avoid: list[str] = Field(default_factory=list)


class IntroHookVisualBeat(BaseModel):
    """Visuelle Zuordnung für einen Teil des Intro-Hooks — der Schnittplan
    (spätere Phase) muss wissen, welche Assets im Intro verwendet werden."""

    hook_beat_id: str
    text: str = ""
    visual_intent: str = ""
    source_folder_name: str = ""
    source_sentence_id: str = ""
    primary_asset_id: str = ""
    backup_asset_ids: list[str] = Field(default_factory=list)
    asset_match_reason: str = ""
    asset_confidence: float = 0.0
    needs_supplement_asset: bool = False
    supplement_reason: str = ""


class IntroHookCandidate(BaseModel):
    hook_id: str
    hook_text: str = ""
    word_count: int = 0
    hook_type: str = INTRO_HOOK_TYPE_CINEMATIC_PROMISE
    used_folders: list[str] = Field(default_factory=list)
    used_sentence_ids: list[str] = Field(default_factory=list)
    visual_beats: list[IntroHookVisualBeat] = Field(default_factory=list)
    hook_potential_score: float = 0.0
    reason: str = ""
    risks: list[str] = Field(default_factory=list)


class IntroHookCandidatesDocument(BaseModel):
    project_id: str
    generated_at: datetime = Field(default_factory=_utcnow)
    language: str = "DE"
    target_words: int = INTRO_HOOK_DEFAULT_TARGET_WORDS
    min_words: int = INTRO_HOOK_DEFAULT_MIN_WORDS
    max_words: int = INTRO_HOOK_DEFAULT_MAX_WORDS
    candidates: list[IntroHookCandidate] = Field(default_factory=list)
    llm_run_id: str = ""
    status: str = INTRO_HOOK_STATUS_DRAFT  # DRAFT|READY|PARSE_FAILED|FAIL
    # Additiv über die Vorgabe hinaus: dokumentiert z. B. eine abweichende
    # Kandidatenanzahl (siehe intro_hook_service — bewusste Entscheidung §12.18).
    risks: list[str] = Field(default_factory=list)


class ConfirmedIntroHook(BaseModel):
    project_id: str
    confirmed_at: datetime = Field(default_factory=_utcnow)
    language: str = "DE"
    hook_id: str
    hook_text: str = ""
    word_count: int = 0
    hook_type: str = ""
    used_folders: list[str] = Field(default_factory=list)
    used_sentence_ids: list[str] = Field(default_factory=list)
    visual_beats: list[IntroHookVisualBeat] = Field(default_factory=list)
    hook_potential_score: float = 0.0
    reason: str = ""
    llm_run_id: str = ""
    status: str = INTRO_HOOK_STATUS_CONFIRMED
    risks: list[str] = Field(default_factory=list)


# --- Phase 6: ElevenLabs Audio/TTS ---


class ElevenLabsSettings(BaseModel):
    """Niemals den API-Key enthalten — der kommt ausschließlich aus dem
    bestehenden Environment-/User-Secrets-System (otio_app.services.api_keys)."""

    project_id: str
    generated_at: datetime = Field(default_factory=_utcnow)
    voice_id: str = ""
    model_id: str = ELEVENLABS_DEFAULT_MODEL_ID
    output_format: str = ELEVENLABS_DEFAULT_OUTPUT_FORMAT
    stability: float = 0.5
    similarity_boost: float = 0.75
    style: float = 0.0
    use_speaker_boost: bool = True
    speed: float = 1.0
    language_code: str = ""


class TtsRunManifest(BaseModel):
    """Traceability für einen einzelnen TTS-Aufruf — niemals API-Key/Header."""

    tts_run_id: str
    project_id: str
    created_at: datetime = Field(default_factory=_utcnow)
    scope: str = AUDIO_SCOPE_FOLDER  # intro|folder
    folder_name: str = ""
    order_index: int = 0
    text_hash: str = ""
    tts_provider: str = "elevenlabs"
    voice_id: str = ""
    model_id: str = ""
    output_format: str = ""
    status: str = TTS_RUN_STATUS_FAIL  # PASS|FAIL
    audio_path: str = ""
    timestamps_path: str = ""
    error_path: str = ""


class VoiceoverAudioItem(BaseModel):
    scope: str = AUDIO_SCOPE_FOLDER  # intro|folder
    folder_name: str = ""
    order_index: int = 0
    voiceover_text_hash: str = ""
    audio_path: str = ""
    audio_version: int = 0
    audio_duration_sec: float = 0.0
    timestamps_path: str = ""
    alignment_path: str = ""
    tts_run_id: str = ""
    status: str = AUDIO_STATUS_MISSING  # AUDIO_READY|STALE|FAILED|MISSING
    created_at: datetime = Field(default_factory=_utcnow)
    error_message: str = ""


class VoiceoverAudioManifest(BaseModel):
    project_id: str
    generated_at: datetime = Field(default_factory=_utcnow)
    tts_provider: str = "elevenlabs"
    tts_model: str = ""
    voice_id: str = ""
    output_format: str = ""
    items: list[VoiceoverAudioItem] = Field(default_factory=list)


class AlignmentItem(BaseModel):
    """sentence_id kann bei Intro-Alignment auch ein hook_beat_id sein."""

    sentence_id: str = ""
    beat_id: str = ""
    text: str = ""
    audio_start_sec: float = 0.0
    audio_end_sec: float = 0.0
    duration_sec: float = 0.0
    primary_asset_id: str = ""
    backup_asset_ids: list[str] = Field(default_factory=list)
    visual_intent: str = ""
    asset_confidence: float = 0.0
    needs_supplement_asset: bool = False
    supplement_reason: str = ""


class VoiceoverAlignment(BaseModel):
    project_id: str
    generated_at: datetime = Field(default_factory=_utcnow)
    scope: str = AUDIO_SCOPE_FOLDER  # intro|folder
    folder_name: str = ""
    audio_path: str = ""
    audio_duration_sec: float = 0.0
    alignment_source: str = "elevenlabs_timestamps"
    items: list[AlignmentItem] = Field(default_factory=list)
    alignment_warnings: list[str] = Field(default_factory=list)


# --- Phase 7: Final Output / confirmed_voiceover_project_plan ---


class ReadinessError(BaseModel):
    type: str
    severity: str = "WARNING"  # WARNING|BLOCKER
    scope: str = "project"  # project|intro|folder|sentence|audio|alignment
    folder_name: str = ""
    sentence_id: str = ""
    message: str = ""
    fix_hint: str = ""


class ConfirmedIntroPlanItem(BaseModel):
    hook_text: str = ""
    word_count: int = 0
    hook_type: str = ""
    used_folders: list[str] = Field(default_factory=list)
    used_sentence_ids: list[str] = Field(default_factory=list)
    visual_beats: list[IntroHookVisualBeat] = Field(default_factory=list)
    audio_path: str = ""
    audio_duration_sec: float = 0.0
    alignment_path: str = ""
    alignment_items: list[AlignmentItem] = Field(default_factory=list)
    audio_status: str = AUDIO_STATUS_MISSING
    # READY|MISSING_AUDIO|MISSING_ALIGNMENT|STALE_AUDIO|WARNING|BLOCKED
    # (BLOCKED additiv erlaubt für fehlendes Asset-Mapping in visual_beats,
    # str-Feld ohne strikte Enum-Validierung — siehe Audit-Hardening §4)
    readiness_status: str = ITEM_READINESS_MISSING_AUDIO


class ConfirmedFolderPlanItem(BaseModel):
    folder_name: str
    order_index: int = 0
    dramaturgy_role: str = ""
    enabled: bool = True
    voiceover_text_full: str = ""
    word_count: int = 0
    target_words: int = 0
    min_words: int = 0
    max_words: int = 0
    sentence_items: list[SentenceItem] = Field(default_factory=list)
    audio_path: str = ""
    audio_duration_sec: float = 0.0
    alignment_path: str = ""
    alignment_items: list[AlignmentItem] = Field(default_factory=list)
    audio_status: str = AUDIO_STATUS_MISSING
    validation_status: str = "UNKNOWN"  # PASS|NEEDS_USER_REVIEW|UNKNOWN
    asset_mapping_status: str = "PASS"  # PASS|WARNINGS|BLOCKED
    # READY|MISSING_AUDIO|MISSING_ALIGNMENT|STALE_AUDIO|WARNING|BLOCKED
    readiness_status: str = ITEM_READINESS_MISSING_AUDIO
    warnings: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)


class ProjectPlanReadiness(BaseModel):
    has_confirmed_dramaturgy: bool = False
    has_confirmed_intro: bool = False
    all_active_folders_have_confirmed_voiceover: bool = False
    all_required_audio_ready: bool = False
    all_alignments_ready: bool = False
    has_asset_mapping_for_all_items: bool = False
    has_no_blockers: bool = False


class ConfirmedVoiceoverProjectPlan(BaseModel):
    """Redaktionelle Quelle der Wahrheit für die spätere Schnittplan-Pipeline.

    Aggregiert NUR bestätigte Artefakte — erzeugt keinen Schnittplan und
    keinen OTIO-Export, plant nichts neu von Gemini/Claude."""

    project_id: str
    generated_at: datetime = Field(default_factory=_utcnow)
    project_title: str = ""
    language: str = "DE"
    # TEXT_READY|AUDIO_PENDING|AUDIO_READY|READY_FOR_CUT|NEEDS_REVIEW
    status: str = PLAN_STATUS_TEXT_READY
    readiness: ProjectPlanReadiness = Field(default_factory=ProjectPlanReadiness)
    project_brief_hash: str = ""
    style_profile_hash: str = ""
    dramaturgy_hash: str = ""
    folder_voiceovers_hash: str = ""
    intro_hook_hash: str = ""
    audio_manifest_hash: str = ""
    intro: ConfirmedIntroPlanItem = Field(default_factory=ConfirmedIntroPlanItem)
    folders: list[ConfirmedFolderPlanItem] = Field(default_factory=list)
    warnings: list[ReadinessError] = Field(default_factory=list)
    blockers: list[ReadinessError] = Field(default_factory=list)
    source_artifacts: dict[str, Any] = Field(default_factory=dict)
