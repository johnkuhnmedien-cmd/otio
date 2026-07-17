"""Fake-only Discovery V2 vision configuration for Phase 8C."""

from __future__ import annotations

from otio_app.discovery_v2.domain.visual_observation import (
    GATEWAY_VERSION,
    PROMPT_VERSION,
    RESPONSE_SCHEMA_VERSION,
    VisionConfig,
)


VISION_PROVIDER = "fake"
VISION_MODEL_IDENTIFIER = "fake-vision-v1"
MAX_RETRIES = 2
MAX_FRAMES_PER_VIDEO = 24
MAX_FRAMES_PER_RUN = 96
MAX_FRAME_BYTES = 8 * 1024 * 1024
MAX_RUN_BYTES = 64 * 1024 * 1024
VISION_TIMEOUT_SECONDS = 30


def load_vision_config() -> VisionConfig:
    """Return the only supported Phase 8C vision configuration."""

    return VisionConfig(
        provider=VISION_PROVIDER,
        enabled=True,
        model_identifier=VISION_MODEL_IDENTIFIER,
        gateway_version=GATEWAY_VERSION,
        prompt_version=PROMPT_VERSION,
        response_schema_version=RESPONSE_SCHEMA_VERSION,
        max_retries=MAX_RETRIES,
        max_frames_per_video=MAX_FRAMES_PER_VIDEO,
        max_frames_per_run=MAX_FRAMES_PER_RUN,
        max_frame_bytes=MAX_FRAME_BYTES,
        max_run_bytes=MAX_RUN_BYTES,
        timeout_seconds=VISION_TIMEOUT_SECONDS,
    )


def reset_vision_config_for_tests() -> None:
    """Phase 8C config has no mutable runtime state."""

    return None


__all__ = [
    "MAX_FRAME_BYTES",
    "MAX_FRAMES_PER_RUN",
    "MAX_FRAMES_PER_VIDEO",
    "MAX_RETRIES",
    "MAX_RUN_BYTES",
    "VISION_MODEL_IDENTIFIER",
    "VISION_PROVIDER",
    "VISION_TIMEOUT_SECONDS",
    "load_vision_config",
    "reset_vision_config_for_tests",
]
