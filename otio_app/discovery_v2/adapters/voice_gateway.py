"""Central Discovery V2 voice generation gateway."""

from __future__ import annotations

import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from otio_app.discovery_v2.adapters.voice_config import VoiceConfig, load_voice_config
from otio_app.discovery_v2.adapters.voice_fake import (
    FakeVoiceAdapter,
    FakeVoiceError,
    FakeVoiceRequest,
)
from otio_app.discovery_v2.domain.narration import (
    NARRATION_ERROR_VOICE_GATEWAY_UNCONFIGURED,
    NARRATION_ERROR_VOICE_GENERATION_FAILED,
    NARRATION_ERROR_VOICE_PROVIDER_UNAVAILABLE,
    NARRATION_ERROR_VOICE_SEGMENT_INVALID,
    VOICE_AUDIO_FORMAT,
    VOICE_CHANNELS,
    VOICE_PROVIDER_FAKE,
    VOICE_SAMPLE_RATE_HZ,
    compute_sha256,
    duration_from_samples,
    fake_voice_sample_count,
    normalize_sentence_text,
)


class VoiceGatewayError(RuntimeError):
    """Sanitized gateway error with stable narration error code."""

    def __init__(self, code: str, message: str | None = None) -> None:
        clean = message or code
        super().__init__(clean)
        self.code = code
        self.message = clean


@dataclass(frozen=True)
class VoiceGatewayRequest:
    text: str
    cache_key: str
    output_path: Path
    provider: str = VOICE_PROVIDER_FAKE


@dataclass(frozen=True)
class VoiceGatewayResponse:
    path: Path
    audio_format: str
    sample_rate_hz: int
    channels: int
    sample_count: int
    duration_seconds: float
    byte_size: int
    audio_sha256: str


class VoiceAdapter(Protocol):
    def generate(self, request: FakeVoiceRequest) -> Path:
        """Write audio and return the generated path."""


class VoiceGenerationGateway:
    """Selects the configured fake voice adapter and validates WAV output."""

    def __init__(self, config: VoiceConfig | None = None) -> None:
        self.config = config or load_voice_config()
        self.adapter = self._select_adapter(self.config)

    def generate(self, request: VoiceGatewayRequest) -> VoiceGatewayResponse:
        if request.provider != self.config.provider:
            raise VoiceGatewayError(
                NARRATION_ERROR_VOICE_GATEWAY_UNCONFIGURED,
                "Voice request does not match configured provider.",
            )
        text = normalize_sentence_text(request.text)
        if not text:
            raise VoiceGatewayError(
                NARRATION_ERROR_VOICE_SEGMENT_INVALID,
                "Voice segment text is empty.",
            )
        try:
            expected_samples = fake_voice_sample_count(text, self.config.sample_rate_hz)
            path = self.adapter.generate(
                FakeVoiceRequest(
                    text=text,
                    cache_key=request.cache_key,
                    output_path=request.output_path,
                    sample_rate_hz=self.config.sample_rate_hz,
                    channels=self.config.channels,
                )
            )
            response = validate_fake_wav(path)
        except FakeVoiceError as exc:
            raise VoiceGatewayError(exc.code, "Fake voice generation failed.") from exc
        except ValueError as exc:
            raise VoiceGatewayError(
                NARRATION_ERROR_VOICE_GENERATION_FAILED,
                "Voice input is invalid.",
            ) from exc
        except OSError as exc:
            raise VoiceGatewayError(
                NARRATION_ERROR_VOICE_GENERATION_FAILED,
                "Voice artifact could not be written.",
            ) from exc
        if response.sample_count != expected_samples:
            raise VoiceGatewayError(
                NARRATION_ERROR_VOICE_SEGMENT_INVALID,
                "Fake voice sample count does not match duration contract.",
            )
        return response

    def _select_adapter(self, config: VoiceConfig) -> VoiceAdapter:
        if not config.enabled:
            raise VoiceGatewayError(
                NARRATION_ERROR_VOICE_GATEWAY_UNCONFIGURED,
                "Voice gateway is not enabled.",
            )
        if config.provider != VOICE_PROVIDER_FAKE:
            raise VoiceGatewayError(
                NARRATION_ERROR_VOICE_PROVIDER_UNAVAILABLE,
                "Configured voice provider is unavailable.",
            )
        return FakeVoiceAdapter()


def validate_fake_wav(path: Path) -> VoiceGatewayResponse:
    wav_path = Path(path)
    if not wav_path.is_file():
        raise VoiceGatewayError(NARRATION_ERROR_VOICE_SEGMENT_INVALID, "WAV file is missing.")
    byte_size = wav_path.stat().st_size
    digest = compute_sha256(wav_path.read_bytes())
    try:
        with wave.open(str(wav_path), "rb") as wav:
            channels = wav.getnchannels()
            sample_width = wav.getsampwidth()
            sample_rate = wav.getframerate()
            sample_count = wav.getnframes()
            wav.readframes(sample_count)
    except (wave.Error, EOFError, OSError) as exc:
        raise VoiceGatewayError(
            NARRATION_ERROR_VOICE_SEGMENT_INVALID,
            "WAV header is invalid.",
        ) from exc
    if channels != VOICE_CHANNELS or sample_width != 2 or sample_rate != VOICE_SAMPLE_RATE_HZ:
        raise VoiceGatewayError(
            NARRATION_ERROR_VOICE_SEGMENT_INVALID,
            "WAV format must be PCM s16le 48kHz mono.",
        )
    if sample_count <= 0:
        raise VoiceGatewayError(
            NARRATION_ERROR_VOICE_SEGMENT_INVALID,
            "WAV sample count must be positive.",
        )
    return VoiceGatewayResponse(
        path=wav_path,
        audio_format=VOICE_AUDIO_FORMAT,
        sample_rate_hz=sample_rate,
        channels=channels,
        sample_count=sample_count,
        duration_seconds=duration_from_samples(sample_count, sample_rate),
        byte_size=byte_size,
        audio_sha256=digest,
    )


__all__ = [
    "VoiceAdapter",
    "VoiceGatewayError",
    "VoiceGatewayRequest",
    "VoiceGatewayResponse",
    "VoiceGenerationGateway",
    "validate_fake_wav",
]
