from __future__ import annotations

import wave
from pathlib import Path

import pytest

from otio_app.discovery_v2.adapters.voice_fake import (
    fake_voice_call_count,
    reset_fake_voice_call_count,
)
from otio_app.discovery_v2.adapters.voice_gateway import (
    VoiceGatewayError,
    VoiceGatewayRequest,
    VoiceGenerationGateway,
)
from otio_app.discovery_v2.domain.narration import (
    NARRATION_ERROR_VOICE_SEGMENT_INVALID,
    VOICE_CHANNELS,
    VOICE_SAMPLE_RATE_HZ,
)


def test_fake_voice_gateway_writes_deterministic_readable_wav(tmp_path: Path) -> None:
    reset_fake_voice_call_count()
    request = VoiceGatewayRequest(
        text="Eine kurze Fake-Narration.",
        cache_key="cache-key-1",
        output_path=tmp_path / "one.wav",
    )
    first = VoiceGenerationGateway().generate(request)
    second = VoiceGenerationGateway().generate(
        VoiceGatewayRequest(
            text=request.text,
            cache_key=request.cache_key,
            output_path=tmp_path / "two.wav",
        )
    )
    assert first.audio_sha256 == second.audio_sha256
    assert fake_voice_call_count() == 2
    with wave.open(str(first.path), "rb") as wav:
        assert wav.getframerate() == VOICE_SAMPLE_RATE_HZ
        assert wav.getnchannels() == VOICE_CHANNELS
        assert wav.getsampwidth() == 2
        assert wav.getnframes() == first.sample_count
        assert wav.readframes(first.sample_count)


def test_fake_voice_gateway_rejects_empty_text_without_wav(tmp_path: Path) -> None:
    with pytest.raises(VoiceGatewayError) as exc:
        VoiceGenerationGateway().generate(
            VoiceGatewayRequest(text="  ", cache_key="empty", output_path=tmp_path / "empty.wav")
        )
    assert exc.value.code == NARRATION_ERROR_VOICE_SEGMENT_INVALID
    assert not (tmp_path / "empty.wav").exists()
