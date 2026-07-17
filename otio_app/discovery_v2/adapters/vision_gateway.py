"""Central Discovery V2 vision gateway for fake-only Phase 8C."""

from __future__ import annotations

from typing import Protocol

from pydantic import ValidationError

from otio_app.discovery_v2.adapters.vision_config import (
    VISION_PROVIDER,
    load_vision_config,
)
from otio_app.discovery_v2.adapters.vision_fake import (
    FakeVisionAdapter,
    FakeVisionTransientError,
)
from otio_app.discovery_v2.domain.visual_observation import (
    ANALYSIS_ERROR_ANALYSIS_GATEWAY_UNCONFIGURED,
    ANALYSIS_ERROR_ANALYSIS_RETRY_EXHAUSTED,
    ANALYSIS_ERROR_MODEL_RESPONSE_INVALID,
    ANALYSIS_ERROR_MODEL_RESPONSE_SCHEMA_MISMATCH,
    ANALYSIS_ERROR_PROVIDER_TRANSIENT,
    ANALYSIS_ERROR_VISION_MODEL_UNAVAILABLE,
    VisionConfig,
    VisionGatewayRequest,
    VisionGatewayResponse,
    VisualObservation,
)


class VisionGatewayError(RuntimeError):
    """Sanitized gateway error with stable analysis error code."""

    def __init__(self, code: str, message: str | None = None) -> None:
        clean = message or code
        super().__init__(clean)
        self.code = code
        self.message = clean


class VisionAdapter(Protocol):
    def analyze(self, request: VisionGatewayRequest) -> dict:
        """Return untrusted model payload for gateway validation."""


class DiscoveryVisionGateway:
    """Validate and normalize all Discovery V2 vision adapter responses."""

    def __init__(self, config: VisionConfig | None = None) -> None:
        self.config = config or load_vision_config()
        self.adapter = self._select_adapter(self.config)

    def analyze(self, request: VisionGatewayRequest) -> VisionGatewayResponse:
        self._assert_request_matches_config(request)
        last_transient: FakeVisionTransientError | None = None
        total_attempts = self.config.max_retries + 1
        for attempt_index in range(total_attempts):
            try:
                raw = self.adapter.analyze(request)
                observation = self._validate_observation(raw, request)
                return VisionGatewayResponse(
                    observation=observation,
                    provider=self.config.provider,
                    model_identifier=self.config.model_identifier,
                    gateway_version=self.config.gateway_version,
                    prompt_version=self.config.prompt_version,
                    response_schema_version=self.config.response_schema_version,
                    attempt_count=attempt_index + 1,
                )
            except FakeVisionTransientError as exc:
                last_transient = exc
                if attempt_index >= self.config.max_retries:
                    raise VisionGatewayError(
                        ANALYSIS_ERROR_ANALYSIS_RETRY_EXHAUSTED,
                        "Vision provider transient error after retries.",
                    ) from exc
        raise VisionGatewayError(
            ANALYSIS_ERROR_PROVIDER_TRANSIENT,
            "Vision provider transient error.",
        ) from last_transient

    def _select_adapter(self, config: VisionConfig) -> VisionAdapter:
        if not config.enabled:
            raise VisionGatewayError(
                ANALYSIS_ERROR_ANALYSIS_GATEWAY_UNCONFIGURED,
                "Vision gateway is not enabled.",
            )
        if config.provider != VISION_PROVIDER:
            raise VisionGatewayError(
                ANALYSIS_ERROR_VISION_MODEL_UNAVAILABLE,
                "Configured vision provider is unavailable.",
            )
        return FakeVisionAdapter()

    def _assert_request_matches_config(self, request: VisionGatewayRequest) -> None:
        expected = {
            "provider": self.config.provider,
            "model_identifier": self.config.model_identifier,
            "gateway_version": self.config.gateway_version,
            "prompt_version": self.config.prompt_version,
            "response_schema_version": self.config.response_schema_version,
        }
        for field_name, expected_value in expected.items():
            if getattr(request, field_name) != expected_value:
                raise VisionGatewayError(
                    ANALYSIS_ERROR_ANALYSIS_GATEWAY_UNCONFIGURED,
                    "Vision request does not match configured gateway.",
                )

    def _validate_observation(
        self,
        raw: dict,
        request: VisionGatewayRequest,
    ) -> VisualObservation:
        try:
            observation = VisualObservation.model_validate(raw)
        except ValidationError as exc:
            raise VisionGatewayError(
                _validation_error_code(exc),
                "Vision model response failed schema validation.",
            ) from exc
        evidence = set(observation.evidence_frame_ids)
        if not evidence.issubset(request.frame_ids):
            raise VisionGatewayError(
                ANALYSIS_ERROR_MODEL_RESPONSE_SCHEMA_MISMATCH,
                "Vision model referenced unknown evidence frames.",
            )
        return observation


def _validation_error_code(exc: ValidationError) -> str:
    schema_error_types = {
        "extra_forbidden",
        "missing",
        "literal_error",
        "list_type",
        "model_type",
    }
    for error in exc.errors():
        if str(error.get("type")) in schema_error_types:
            return ANALYSIS_ERROR_MODEL_RESPONSE_SCHEMA_MISMATCH
    return ANALYSIS_ERROR_MODEL_RESPONSE_INVALID


__all__ = [
    "DiscoveryVisionGateway",
    "VisionAdapter",
    "VisionGatewayError",
]
