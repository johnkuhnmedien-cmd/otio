"""Domain contracts for Discovery V2 Phase 11 narration."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from fractions import Fraction
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

NARRATION_SCHEMA_VERSION = "narration-v1"

VOICE_PROVIDER_FAKE = "fake"
VOICE_IDENTIFIER_FAKE = "fake-neutral-v1"
VOICE_SETTINGS_VERSION_FAKE = "fake-voice-settings-v1"
VOICE_ADAPTER_VERSION_FAKE = "fake-voice-v1"
VOICE_AUDIO_FORMAT = "wav-pcm-s16le"
VOICE_SAMPLE_RATE_HZ = 48000
VOICE_CHANNELS = 1

MIN_SENTENCE_S = 0.40
MAX_SENTENCE_S = 18.0
CHAR_SECONDS = 0.055
MAX_TEXT_LEN = 2000
MAX_SEGMENTS = 500

COLD_OPEN_MIN_S = 0.0
COLD_OPEN_MAX_S = 3.0
NORMAL_PAUSE_MIN_S = 0.15
NORMAL_PAUSE_MAX_S = 2.5
VISUAL_BREATH_MAX_S = 4.0
CLOSING_HOLD_MAX_S = 5.0
MAX_PAUSE_RATIO = 0.55

PROMPT_VERSION_PAUSE_DIRECTION = "pause-direction-v1"
RESPONSE_SCHEMA_PAUSE_DIRECTION = "pause-direction-response-v1"
TIMING_PROFILE_VERSION = "narration-timing-v1"
TEXT_REQUEST_KIND_PAUSE_DIRECTION = "pause_direction"

NARRATION_RUN_SCOPE_VOICE = "voice_generation_only"
NARRATION_RUN_SCOPE_PAUSE = "pause_direction_only"
NARRATION_RUN_SCOPE_TIMING = "narration_timing_resolve_only"

NARRATION_ERROR_SCRIPT_LOCK_MISSING = "script_lock_missing"
NARRATION_ERROR_SCRIPT_LOCK_INVALIDATED = "script_lock_invalidated"
NARRATION_ERROR_SCRIPT_LOCK_FINGERPRINT_MISMATCH = "script_lock_fingerprint_mismatch"
NARRATION_ERROR_VOICE_PROFILE_MISSING = "voice_profile_missing"
NARRATION_ERROR_VOICE_PROFILE_INVALID = "voice_profile_invalid"
NARRATION_ERROR_VOICE_GATEWAY_UNCONFIGURED = "voice_gateway_unconfigured"
NARRATION_ERROR_VOICE_PROVIDER_UNAVAILABLE = "voice_provider_unavailable"
NARRATION_ERROR_VOICE_GENERATION_FAILED = "voice_generation_failed"
NARRATION_ERROR_VOICE_SEGMENT_INVALID = "voice_segment_invalid"
NARRATION_ERROR_VOICE_SEGMENT_MISSING = "voice_segment_missing"
NARRATION_ERROR_VOICE_SEGMENT_HASH_MISMATCH = "voice_segment_hash_mismatch"
NARRATION_ERROR_VOICE_ARTIFACT_CONFLICT = "voice_artifact_conflict"
NARRATION_ERROR_PAUSE_GATEWAY_UNCONFIGURED = "pause_gateway_unconfigured"
NARRATION_ERROR_PAUSE_RESPONSE_INVALID = "pause_response_invalid"
NARRATION_ERROR_PAUSE_RESPONSE_SCHEMA_MISMATCH = "pause_response_schema_mismatch"
NARRATION_ERROR_INVALID_PAUSE_REFERENCE = "invalid_pause_reference"
NARRATION_ERROR_PAUSE_DIRECTION_CONFLICT = "pause_direction_conflict"
NARRATION_ERROR_PAUSE_RETRY_EXHAUSTED = "pause_retry_exhausted"
NARRATION_ERROR_INPUT_STALE = "narration_input_stale"
NARRATION_ERROR_TIMING_RESOLUTION_FAILED = "timing_resolution_failed"
NARRATION_ERROR_INVALID_TIMELINE = "invalid_narration_timeline"
NARRATION_ERROR_INVALID_TIMEBASE = "invalid_timebase"
NARRATION_ERROR_RUN_ALREADY_ACTIVE = "narration_run_already_active"
NARRATION_ERROR_ANALYSIS_RUN_ALREADY_ACTIVE = "analysis_run_already_active"
NARRATION_ERROR_EDITORIAL_RUN_ALREADY_ACTIVE = "editorial_run_already_active"
NARRATION_ERROR_SUPPLEMENTATION_RUN_ALREADY_ACTIVE = "supplementation_run_already_active"
NARRATION_ERROR_REGISTRY_WRITE_FAILED = "narration_registry_write_failed"
NARRATION_ERROR_ARTIFACT_WRITE_FAILED = "narration_artifact_write_failed"
NARRATION_ERROR_WORKER_INTERRUPTED = "worker_interrupted"
NARRATION_ERROR_REPORT_WRITE_FAILED = "report_write_failed"

NARRATION_ERROR_CODES = (
    NARRATION_ERROR_SCRIPT_LOCK_MISSING,
    NARRATION_ERROR_SCRIPT_LOCK_INVALIDATED,
    NARRATION_ERROR_SCRIPT_LOCK_FINGERPRINT_MISMATCH,
    NARRATION_ERROR_VOICE_PROFILE_MISSING,
    NARRATION_ERROR_VOICE_PROFILE_INVALID,
    NARRATION_ERROR_VOICE_GATEWAY_UNCONFIGURED,
    NARRATION_ERROR_VOICE_PROVIDER_UNAVAILABLE,
    NARRATION_ERROR_VOICE_GENERATION_FAILED,
    NARRATION_ERROR_VOICE_SEGMENT_INVALID,
    NARRATION_ERROR_VOICE_SEGMENT_MISSING,
    NARRATION_ERROR_VOICE_SEGMENT_HASH_MISMATCH,
    NARRATION_ERROR_VOICE_ARTIFACT_CONFLICT,
    NARRATION_ERROR_PAUSE_GATEWAY_UNCONFIGURED,
    NARRATION_ERROR_PAUSE_RESPONSE_INVALID,
    NARRATION_ERROR_PAUSE_RESPONSE_SCHEMA_MISMATCH,
    NARRATION_ERROR_INVALID_PAUSE_REFERENCE,
    NARRATION_ERROR_PAUSE_DIRECTION_CONFLICT,
    NARRATION_ERROR_PAUSE_RETRY_EXHAUSTED,
    NARRATION_ERROR_INPUT_STALE,
    NARRATION_ERROR_TIMING_RESOLUTION_FAILED,
    NARRATION_ERROR_INVALID_TIMELINE,
    NARRATION_ERROR_INVALID_TIMEBASE,
    NARRATION_ERROR_RUN_ALREADY_ACTIVE,
    NARRATION_ERROR_ANALYSIS_RUN_ALREADY_ACTIVE,
    NARRATION_ERROR_EDITORIAL_RUN_ALREADY_ACTIVE,
    NARRATION_ERROR_SUPPLEMENTATION_RUN_ALREADY_ACTIVE,
    NARRATION_ERROR_REGISTRY_WRITE_FAILED,
    NARRATION_ERROR_ARTIFACT_WRITE_FAILED,
    NARRATION_ERROR_WORKER_INTERRUPTED,
    NARRATION_ERROR_REPORT_WRITE_FAILED,
)

NarrationRunScopeLiteral = Literal[
    "voice_generation_only",
    "pause_direction_only",
    "narration_timing_resolve_only",
]


class VoiceProfileStatus(str, Enum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    INVALID = "invalid"


class NarrationRunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


ACTIVE_NARRATION_RUN_STATUSES = frozenset(
    {NarrationRunStatus.QUEUED, NarrationRunStatus.RUNNING}
)


class NarrationAttemptStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    REUSED = "reused"
    INTERRUPTED = "interrupted"


class VoiceSegmentStatus(str, Enum):
    PUBLISHED = "published"
    FAILED = "failed"
    SUPERSEDED = "superseded"


class PauseDirectionPlanStatus(str, Enum):
    DRAFT = "draft"
    COMPLETED = "completed"
    FAILED = "failed"
    STALE = "stale"
    SUPERSEDED = "superseded"


class PausePositionKind(str, Enum):
    BEFORE_SENTENCE = "before_sentence"
    AFTER_SENTENCE = "after_sentence"
    BETWEEN_SENTENCES = "between_sentences"
    TIMELINE_START = "timeline_start"
    TIMELINE_END = "timeline_end"


class PauseFunction(str, Enum):
    COLD_OPEN = "cold_open"
    HOOK_BREATH = "hook_breath"
    SENTENCE_TRANSITION = "sentence_transition"
    SECTION_TRANSITION = "section_transition"
    EMPHASIS = "emphasis"
    VISUAL_BREATH = "visual_breath"
    CLOSING_HOLD = "closing_hold"
    NO_PAUSE = "no_pause"


NORMAL_PAUSE_FUNCTIONS = frozenset(
    {
        PauseFunction.HOOK_BREATH,
        PauseFunction.SENTENCE_TRANSITION,
        PauseFunction.SECTION_TRANSITION,
        PauseFunction.EMPHASIS,
    }
)


class PauseHardness(str, Enum):
    HARD = "hard"
    SOFT = "soft"


class PauseUncertainty(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class NarrationTimelineStatus(str, Enum):
    COMPLETED = "completed"
    STALE = "stale"
    SUPERSEDED = "superseded"
    INVALID = "invalid"


class NarrationTimelineEntryType(str, Enum):
    VOICE = "voice"
    PAUSE = "pause"
    VISUAL_ONLY = "visual_only"


class VoiceOutputProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    audio_format: Literal["wav-pcm-s16le"] = VOICE_AUDIO_FORMAT
    sample_rate_hz: int = VOICE_SAMPLE_RATE_HZ
    channels: int = VOICE_CHANNELS


class VoiceProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    voice_profile_id: str
    project_id: str
    language: str
    provider: Literal["fake"] = VOICE_PROVIDER_FAKE
    voice_identifier: str = VOICE_IDENTIFIER_FAKE
    voice_settings_version: str = VOICE_SETTINGS_VERSION_FAKE
    output_profile: VoiceOutputProfile = Field(default_factory=VoiceOutputProfile)
    version: int = Field(default=1, ge=1)
    adapter_version: str = VOICE_ADAPTER_VERSION_FAKE
    audio_format: Literal["wav-pcm-s16le"] = VOICE_AUDIO_FORMAT
    sample_rate: int = Field(default=VOICE_SAMPLE_RATE_HZ, gt=0)
    channels: int = Field(default=VOICE_CHANNELS, ge=1)
    supersedes_voice_profile_id: str | None = None
    status: VoiceProfileStatus = VoiceProfileStatus.ACTIVE
    created_at: datetime

    @model_validator(mode="after")
    def _fake_profile_contract(self) -> "VoiceProfile":
        if self.audio_format != VOICE_AUDIO_FORMAT:
            raise ValueError("Fake voice profile audio_format must be wav-pcm-s16le")
        if self.sample_rate != VOICE_SAMPLE_RATE_HZ:
            raise ValueError("Fake voice profile sample_rate must be 48000")
        if self.channels != VOICE_CHANNELS:
            raise ValueError("Fake voice profile channels must be 1")
        if self.output_profile.audio_format != self.audio_format:
            raise ValueError("Voice profile output_profile audio_format mismatch")
        if self.output_profile.sample_rate_hz != self.sample_rate:
            raise ValueError("Voice profile output_profile sample_rate mismatch")
        if self.output_profile.channels != self.channels:
            raise ValueError("Voice profile output_profile channels mismatch")
        if self.supersedes_voice_profile_id == self.voice_profile_id:
            raise ValueError("Voice profile cannot supersede itself")
        return self


class VoiceGenerationRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    project_id: str
    script_lock_id: str
    script_id: str
    voice_profile_id: str
    input_fingerprint: str
    provider: Literal["fake"] = VOICE_PROVIDER_FAKE
    adapter_version: str = VOICE_ADAPTER_VERSION_FAKE
    scope: NarrationRunScopeLiteral = NARRATION_RUN_SCOPE_VOICE
    status: NarrationRunStatus
    sentence_count: int = Field(ge=0)
    segments_created: int = Field(default=0, ge=0)
    segments_reused: int = Field(default=0, ge=0)
    segments_failed: int = Field(default=0, ge=0)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_code: str | None = None
    error_message: str | None = None
    relative_report_path: str | None = None
    created_at: datetime
    schema_version: str = NARRATION_SCHEMA_VERSION


class VoiceGenerationAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempt_id: str
    run_id: str
    project_id: str
    scope: NarrationRunScopeLiteral
    sentence_id: str | None = None
    segment_id: str | None = None
    cache_key: str | None = None
    provider: str = VOICE_PROVIDER_FAKE
    adapter_version: str | None = VOICE_ADAPTER_VERSION_FAKE
    input_fingerprint: str | None = None
    status: NarrationAttemptStatus
    relative_json_path: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    completed_at: datetime | None = None


class VoiceSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    segment_id: str
    run_id: str
    script_lock_id: str
    script_id: str
    sentence_id: str
    sentence_ordinal: int = Field(ge=0)
    text_hash: str
    voice_profile_id: str
    provider: Literal["fake"] = VOICE_PROVIDER_FAKE
    voice_identifier: str = VOICE_IDENTIFIER_FAKE
    voice_settings_version: str = VOICE_SETTINGS_VERSION_FAKE
    adapter_version: str = VOICE_ADAPTER_VERSION_FAKE
    audio_format: Literal["wav-pcm-s16le"] = VOICE_AUDIO_FORMAT
    sample_rate_hz: int = VOICE_SAMPLE_RATE_HZ
    channels: int = VOICE_CHANNELS
    sample_count: int = Field(gt=0)
    duration_seconds: float = Field(gt=0)
    byte_size: int = Field(gt=0)
    audio_sha256: str
    relative_path: str
    status: VoiceSegmentStatus
    created_at: datetime

    @model_validator(mode="after")
    def _duration_matches_samples(self) -> "VoiceSegment":
        expected = duration_from_samples(self.sample_count, self.sample_rate_hz)
        if abs(self.duration_seconds - expected) > (1.0 / self.sample_rate_hz):
            raise ValueError("Voice segment duration does not match sample count")
        return self


class PauseDirection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    direction_id: str
    pause_plan_id: str
    ordinal: int = Field(ge=0)
    position_kind: PausePositionKind
    sentence_id: str | None = None
    segment_id: str | None = None
    anchor_ordinal: int | None = Field(default=None, ge=0)
    function: PauseFunction
    min_duration_intent_s: float = Field(ge=0.0)
    preferred_duration_intent_s: float = Field(ge=0.0)
    max_duration_intent_s: float = Field(ge=0.0)
    hardness: PauseHardness = PauseHardness.SOFT
    rationale: str
    uncertainty: PauseUncertainty = PauseUncertainty.LOW

    @model_validator(mode="after")
    def _duration_order(self) -> "PauseDirection":
        if not (
            self.min_duration_intent_s
            <= self.preferred_duration_intent_s
            <= self.max_duration_intent_s
        ):
            raise ValueError("Pause duration intents must be min <= preferred <= max")
        return self


class PauseDirectionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pause_plan_id: str
    project_id: str
    script_lock_id: str
    voice_run_id: str
    prompt_version: str = PROMPT_VERSION_PAUSE_DIRECTION
    model_identifier: str
    gateway_version: str
    response_schema_version: str = RESPONSE_SCHEMA_PAUSE_DIRECTION
    provider: Literal["fake"] = VOICE_PROVIDER_FAKE
    input_fingerprint: str
    global_notes: list[str] = Field(default_factory=list)
    status: PauseDirectionPlanStatus
    created_at: datetime
    schema_version: str = NARRATION_SCHEMA_VERSION


class PauseDirectionGatewayPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pause_plan: PauseDirectionPlan
    directions: list[PauseDirection] = Field(default_factory=list)


class NarrationTimebase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fps_numerator: int = Field(gt=0)
    fps_denominator: int = Field(gt=0)
    fps: float = Field(gt=0)

    @model_validator(mode="after")
    def _fps_matches_ratio(self) -> "NarrationTimebase":
        ratio = self.fps_numerator / self.fps_denominator
        if abs(self.fps - ratio) > 1e-6:
            raise ValueError("Timebase fps does not match numerator/denominator")
        return self


class NarrationTimelineEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_id: str
    ordinal: int = Field(ge=0)
    entry_type: NarrationTimelineEntryType
    sentence_id: str | None = None
    voice_segment_id: str | None = None
    pause_direction_id: str | None = None
    start_seconds: float = Field(ge=0.0)
    end_seconds: float = Field(gt=0.0)
    duration_seconds: float = Field(gt=0.0)
    start_frame: int = Field(ge=0)
    end_frame: int = Field(gt=0)
    function: str
    technical_notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _entry_invariants(self) -> "NarrationTimelineEntry":
        if self.end_seconds <= self.start_seconds:
            raise ValueError("Timeline entry end must be after start")
        if abs((self.end_seconds - self.start_seconds) - self.duration_seconds) > 1e-6:
            raise ValueError("Timeline entry duration does not match start/end")
        if self.end_frame <= self.start_frame:
            raise ValueError("Timeline entry end_frame must be exclusive and after start_frame")
        return self


class ResolvedNarrationTimeline(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timeline_id: str
    project_id: str
    script_lock_id: str
    voice_run_id: str
    pause_plan_id: str
    timing_profile_version: str = TIMING_PROFILE_VERSION
    timebase: NarrationTimebase
    total_duration_seconds: float = Field(ge=0.0)
    total_frames: int = Field(ge=0)
    entries: list[NarrationTimelineEntry] = Field(default_factory=list)
    input_fingerprint: str
    status: NarrationTimelineStatus
    created_at: datetime
    schema_version: str = NARRATION_SCHEMA_VERSION

    @model_validator(mode="after")
    def _timeline_invariants(self) -> "ResolvedNarrationTimeline":
        for expected_ordinal, entry in enumerate(self.entries):
            if entry.ordinal != expected_ordinal:
                raise ValueError("Timeline entry ordinals must be gapless")
            if expected_ordinal and entry.start_frame != self.entries[expected_ordinal - 1].end_frame:
                raise ValueError("Timeline frame boundaries must be adjacent")
        if self.entries:
            last = self.entries[-1]
            if self.total_frames != last.end_frame:
                raise ValueError("Timeline total_frames must match last end_frame")
            if abs(self.total_duration_seconds - last.end_seconds) > (1.0 / self.timebase.fps):
                raise ValueError("Timeline total duration must match last end")
        return self


class NarrationProjectState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str
    current_voice_profile_id: str | None = None
    current_voice_run_id: str | None = None
    current_pause_plan_id: str | None = None
    current_timeline_id: str | None = None
    current_script_lock_id: str | None = None
    updated_at: datetime


@dataclass(frozen=True)
class VoiceSegmentCacheIdentity:
    cache_key: str
    segment_id: str
    text_hash: str


def compute_sha256(value: object) -> str:
    if isinstance(value, bytes):
        payload = value
    elif isinstance(value, str):
        payload = value.encode("utf-8")
    else:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def normalize_sentence_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())


def sentence_text_hash(text: str) -> str:
    return compute_sha256(normalize_sentence_text(text))


def fake_voice_duration_seconds(text: str) -> float:
    normalized = normalize_sentence_text(text)
    if not normalized:
        raise ValueError(NARRATION_ERROR_VOICE_SEGMENT_INVALID)
    if len(normalized) > MAX_TEXT_LEN:
        raise ValueError(NARRATION_ERROR_VOICE_GENERATION_FAILED)
    raw = min(MAX_SENTENCE_S, max(MIN_SENTENCE_S, len(normalized) * CHAR_SECONDS))
    return round(raw, 3)


def fake_voice_sample_count(text: str, sample_rate_hz: int = VOICE_SAMPLE_RATE_HZ) -> int:
    return max(1, int(round(fake_voice_duration_seconds(text) * sample_rate_hz)))


def duration_from_samples(sample_count: int, sample_rate_hz: int = VOICE_SAMPLE_RATE_HZ) -> float:
    return sample_count / sample_rate_hz


def voice_segment_cache_identity(
    *,
    script_lock_id: str,
    sentence_id: str,
    sentence_text: str,
    voice_profile: VoiceProfile,
    adapter_version: str | None = None,
) -> VoiceSegmentCacheIdentity:
    text_hash = sentence_text_hash(sentence_text)
    effective_adapter_version = adapter_version or voice_profile.adapter_version
    cache_key = compute_sha256(
        {
            "script_lock_id": script_lock_id,
            "sentence_id": sentence_id,
            "text_hash": text_hash,
            "voice_profile_id": voice_profile.voice_profile_id,
            "voice_profile_version": voice_profile.version,
            "provider": voice_profile.provider,
            "voice_identifier": voice_profile.voice_identifier,
            "voice_settings_version": voice_profile.voice_settings_version,
            "adapter_version": effective_adapter_version,
            "audio_format": voice_profile.audio_format,
            "sample_rate": voice_profile.sample_rate,
            "channels": voice_profile.channels,
            "output_profile": voice_profile.output_profile.model_dump(mode="json"),
        }
    )
    segment_id = str(uuid5(NAMESPACE_URL, f"otio-discovery-v2-narration-segment:{cache_key}"))
    return VoiceSegmentCacheIdentity(cache_key=cache_key, segment_id=segment_id, text_hash=text_hash)


def voice_run_input_fingerprint(
    *,
    script_lock_id: str,
    lock_fingerprint: str,
    voice_profile: VoiceProfile,
    sentences: list[object],
) -> str:
    def _get(sentence: object, name: str) -> object:
        if isinstance(sentence, dict):
            return sentence[name]
        return getattr(sentence, name)

    rows = [
        {
            "sentence_id": str(_get(sentence, "sentence_id")),
            "ordinal": int(_get(sentence, "ordinal")),
            "text_hash": sentence_text_hash(str(_get(sentence, "text"))),
        }
        for sentence in sentences
    ]
    rows.sort(key=lambda item: (item["ordinal"], item["sentence_id"]))
    return compute_sha256(
        {
            "script_lock_id": script_lock_id,
            "lock_fingerprint": lock_fingerprint,
            "voice_profile": voice_profile.model_dump(mode="json"),
            "sentences": rows,
            "adapter_version": voice_profile.adapter_version,
        }
    )


def segment_set_fingerprint(segments: list[VoiceSegment]) -> str:
    rows = [
        {
            "segment_id": segment.segment_id,
            "sentence_id": segment.sentence_id,
            "sentence_ordinal": segment.sentence_ordinal,
            "text_hash": segment.text_hash,
            "audio_sha256": segment.audio_sha256,
            "duration_seconds": segment.duration_seconds,
        }
        for segment in segments
        if segment.status == VoiceSegmentStatus.PUBLISHED
    ]
    rows.sort(key=lambda item: (item["sentence_ordinal"], item["segment_id"]))
    return compute_sha256(rows)


def pause_plan_input_fingerprint(
    *,
    script_lock_id: str,
    voice_run_id: str,
    segments: list[VoiceSegment],
    gateway_version: str,
    provider: str,
    model_identifier: str,
    prompt_version: str = PROMPT_VERSION_PAUSE_DIRECTION,
    response_schema_version: str = RESPONSE_SCHEMA_PAUSE_DIRECTION,
) -> str:
    return compute_sha256(
        {
            "script_lock_id": script_lock_id,
            "voice_run_id": voice_run_id,
            "segment_set_fingerprint": segment_set_fingerprint(segments),
            "gateway_version": gateway_version,
            "provider": provider,
            "model_identifier": model_identifier,
            "prompt_version": prompt_version,
            "response_schema_version": response_schema_version,
        }
    )


def timing_input_fingerprint(
    *,
    script_lock_id: str,
    voice_run_id: str,
    pause_plan_id: str,
    timebase: NarrationTimebase,
    timing_profile_version: str = TIMING_PROFILE_VERSION,
) -> str:
    return compute_sha256(
        {
            "script_lock_id": script_lock_id,
            "voice_run_id": voice_run_id,
            "pause_plan_id": pause_plan_id,
            "timebase": timebase.model_dump(mode="json"),
            "timing_profile_version": timing_profile_version,
        }
    )


def timebase_from_fps(fps: float) -> NarrationTimebase:
    if fps <= 0:
        raise ValueError(NARRATION_ERROR_INVALID_TIMEBASE)
    known = {
        24000 / 1001: (24000, 1001),
        24.0: (24, 1),
        25.0: (25, 1),
        30000 / 1001: (30000, 1001),
        30.0: (30, 1),
    }
    for known_fps, (num, den) in known.items():
        if abs(float(fps) - known_fps) < 0.01:
            return NarrationTimebase(fps_numerator=num, fps_denominator=den, fps=num / den)
    fraction = Fraction(float(fps)).limit_denominator(1001)
    return NarrationTimebase(
        fps_numerator=fraction.numerator,
        fps_denominator=fraction.denominator,
        fps=fraction.numerator / fraction.denominator,
    )


def seconds_to_frame_floor(seconds: float, timebase: NarrationTimebase) -> int:
    if seconds < 0:
        raise ValueError(NARRATION_ERROR_INVALID_TIMELINE)
    return int(math.floor(seconds * timebase.fps + 1e-9))


def pause_max_for_function(function: PauseFunction) -> float:
    if function == PauseFunction.COLD_OPEN:
        return COLD_OPEN_MAX_S
    if function == PauseFunction.VISUAL_BREATH:
        return VISUAL_BREATH_MAX_S
    if function == PauseFunction.CLOSING_HOLD:
        return CLOSING_HOLD_MAX_S
    if function == PauseFunction.NO_PAUSE:
        return 0.0
    return NORMAL_PAUSE_MAX_S


def pause_min_for_function(function: PauseFunction) -> float:
    if function in {PauseFunction.COLD_OPEN, PauseFunction.VISUAL_BREATH, PauseFunction.CLOSING_HOLD, PauseFunction.NO_PAUSE}:
        return 0.0
    return NORMAL_PAUSE_MIN_S


def clamp_pause_duration(direction: PauseDirection) -> float:
    if direction.function == PauseFunction.NO_PAUSE:
        return 0.0
    minimum = pause_min_for_function(direction.function)
    maximum = pause_max_for_function(direction.function)
    value = direction.preferred_duration_intent_s
    return min(maximum, max(minimum, value))


__all__ = [name for name in globals() if name.startswith("NARRATION_")] + [
    "ACTIVE_NARRATION_RUN_STATUSES",
    "CHAR_SECONDS",
    "CLOSING_HOLD_MAX_S",
    "COLD_OPEN_MAX_S",
    "COLD_OPEN_MIN_S",
    "MAX_PAUSE_RATIO",
    "MAX_SEGMENTS",
    "MAX_SENTENCE_S",
    "MAX_TEXT_LEN",
    "MIN_SENTENCE_S",
    "NORMAL_PAUSE_FUNCTIONS",
    "NORMAL_PAUSE_MAX_S",
    "NORMAL_PAUSE_MIN_S",
    "NarrationAttemptStatus",
    "NarrationProjectState",
    "NarrationRunScopeLiteral",
    "NarrationRunStatus",
    "NarrationTimebase",
    "NarrationTimelineEntry",
    "NarrationTimelineEntryType",
    "NarrationTimelineStatus",
    "PauseDirection",
    "PauseDirectionGatewayPayload",
    "PauseDirectionPlan",
    "PauseDirectionPlanStatus",
    "PauseFunction",
    "PauseHardness",
    "PausePositionKind",
    "PauseUncertainty",
    "PROMPT_VERSION_PAUSE_DIRECTION",
    "ResolvedNarrationTimeline",
    "RESPONSE_SCHEMA_PAUSE_DIRECTION",
    "TEXT_REQUEST_KIND_PAUSE_DIRECTION",
    "TIMING_PROFILE_VERSION",
    "VISUAL_BREATH_MAX_S",
    "VOICE_ADAPTER_VERSION_FAKE",
    "VOICE_AUDIO_FORMAT",
    "VOICE_CHANNELS",
    "VOICE_IDENTIFIER_FAKE",
    "VOICE_PROVIDER_FAKE",
    "VOICE_SAMPLE_RATE_HZ",
    "VOICE_SETTINGS_VERSION_FAKE",
    "VoiceGenerationAttempt",
    "VoiceGenerationRun",
    "VoiceOutputProfile",
    "VoiceProfile",
    "VoiceProfileStatus",
    "VoiceSegment",
    "VoiceSegmentCacheIdentity",
    "VoiceSegmentStatus",
    "clamp_pause_duration",
    "compute_sha256",
    "duration_from_samples",
    "fake_voice_duration_seconds",
    "fake_voice_sample_count",
    "normalize_sentence_text",
    "pause_max_for_function",
    "pause_min_for_function",
    "pause_plan_input_fingerprint",
    "seconds_to_frame_floor",
    "segment_set_fingerprint",
    "sentence_text_hash",
    "timebase_from_fps",
    "timing_input_fingerprint",
    "voice_run_input_fingerprint",
    "voice_segment_cache_identity",
]
