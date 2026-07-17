"""Fake-only Discovery V2 voice configuration for Phase 11 narration."""

from __future__ import annotations

from dataclasses import dataclass

from otio_app.discovery_v2.domain.narration import (
    VOICE_ADAPTER_VERSION_FAKE,
    VOICE_CHANNELS,
    VOICE_IDENTIFIER_FAKE,
    VOICE_PROVIDER_FAKE,
    VOICE_SAMPLE_RATE_HZ,
    VOICE_SETTINGS_VERSION_FAKE,
)


@dataclass(frozen=True)
class VoiceConfig:
    provider: str
    enabled: bool
    voice_identifier: str
    voice_settings_version: str
    adapter_version: str
    sample_rate_hz: int
    channels: int


def load_voice_config() -> VoiceConfig:
    return VoiceConfig(
        provider=VOICE_PROVIDER_FAKE,
        enabled=True,
        voice_identifier=VOICE_IDENTIFIER_FAKE,
        voice_settings_version=VOICE_SETTINGS_VERSION_FAKE,
        adapter_version=VOICE_ADAPTER_VERSION_FAKE,
        sample_rate_hz=VOICE_SAMPLE_RATE_HZ,
        channels=VOICE_CHANNELS,
    )


def reset_voice_config_for_tests() -> None:
    return None


__all__ = ["VoiceConfig", "load_voice_config", "reset_voice_config_for_tests"]
