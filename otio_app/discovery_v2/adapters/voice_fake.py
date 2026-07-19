"""Deterministic offline fake voice adapter for Discovery V2 Phase 11."""

from __future__ import annotations

import hashlib
import math
import struct
import wave
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from otio_app.discovery_v2.domain.narration import (
    NARRATION_ERROR_VOICE_GENERATION_FAILED,
    NARRATION_ERROR_VOICE_SEGMENT_INVALID,
    VOICE_CHANNELS,
    VOICE_SAMPLE_RATE_HZ,
    fake_voice_sample_count,
    normalize_sentence_text,
)


class FakeVoiceError(RuntimeError):
    """Synthetic fake voice failure with a stable sanitized code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class FakeVoiceRequest:
    text: str
    cache_key: str
    output_path: Path
    sample_rate_hz: int = VOICE_SAMPLE_RATE_HZ
    channels: int = VOICE_CHANNELS


FakeVoiceHook = Callable[[FakeVoiceRequest], Exception | None]
_TEST_HOOK: FakeVoiceHook | None = None
_CALL_COUNT = 0


def set_fake_voice_test_hook(hook: FakeVoiceHook | None) -> None:
    global _TEST_HOOK
    _TEST_HOOK = hook


def reset_fake_voice_test_hook() -> None:
    set_fake_voice_test_hook(None)


def reset_fake_voice_call_count() -> None:
    global _CALL_COUNT
    _CALL_COUNT = 0


def fake_voice_call_count() -> int:
    return _CALL_COUNT


class FakeVoiceAdapter:
    """Writes deterministic non-speech WAV files using only stdlib modules."""

    def generate(self, request: FakeVoiceRequest) -> Path:
        global _CALL_COUNT
        if _TEST_HOOK is not None:
            hooked = _TEST_HOOK(request)
            if isinstance(hooked, Exception):
                raise hooked
        _CALL_COUNT += 1

        text = normalize_sentence_text(request.text)
        if not text:
            raise FakeVoiceError(NARRATION_ERROR_VOICE_SEGMENT_INVALID)
        forced = _forced_error(text)
        if forced is not None:
            raise FakeVoiceError(forced)

        sample_count = fake_voice_sample_count(text, request.sample_rate_hz)
        request.output_path.parent.mkdir(parents=True, exist_ok=True)
        seed = hashlib.sha256(request.cache_key.encode("utf-8")).digest()
        frequency = 220 + int.from_bytes(seed[:2], "big") % 660
        amplitude = 2400 + int.from_bytes(seed[2:4], "big") % 3200
        phase = (int.from_bytes(seed[4:8], "big") % 6283) / 1000.0
        with wave.open(str(request.output_path), "wb") as wav:
            wav.setnchannels(request.channels)
            wav.setsampwidth(2)
            wav.setframerate(request.sample_rate_hz)
            frames = bytearray()
            for index in range(sample_count):
                # Non-speech deterministic tone; cache key controls timbre.
                value = int(amplitude * math.sin((2.0 * math.pi * frequency * index / request.sample_rate_hz) + phase))
                frames.extend(struct.pack("<h", max(-32767, min(32767, value))))
            wav.writeframes(bytes(frames))
        return request.output_path


def _forced_error(text: str) -> str | None:
    lowered = text.lower()
    if "fake_voice_force_invalid" in lowered:
        return NARRATION_ERROR_VOICE_SEGMENT_INVALID
    if "fake_voice_force_failure" in lowered or "fake_voice_force_timeout" in lowered:
        return NARRATION_ERROR_VOICE_GENERATION_FAILED
    return None


__all__ = [
    "FakeVoiceAdapter",
    "FakeVoiceError",
    "FakeVoiceRequest",
    "fake_voice_call_count",
    "reset_fake_voice_call_count",
    "reset_fake_voice_test_hook",
    "set_fake_voice_test_hook",
]
