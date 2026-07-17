"""Central Discovery V2 text gateway for fake-only Phase 9 editorial."""

from __future__ import annotations

from typing import Protocol

from pydantic import ValidationError

from otio_app.discovery_v2.adapters.text_config import TEXT_PROVIDER, load_text_config
from otio_app.discovery_v2.adapters.text_fake import (
    FakeTextAdapter,
    FakeTextTransientError,
)
from otio_app.discovery_v2.domain.editorial import (
    EDITORIAL_ERROR_GATEWAY_UNCONFIGURED,
    EDITORIAL_ERROR_INVALID_SENTENCE_REFERENCE,
    EDITORIAL_ERROR_INVALID_VISUAL_BEAT_REFERENCE,
    EDITORIAL_ERROR_INVALID_VISUAL_INTENT_REFERENCE,
    EDITORIAL_ERROR_MODEL_UNAVAILABLE,
    EDITORIAL_ERROR_RESPONSE_INVALID,
    EDITORIAL_ERROR_RESPONSE_SCHEMA_MISMATCH,
    EDITORIAL_ERROR_RETRY_EXHAUSTED,
    CoverageGatewayPayload,
    NarrativeGatewayPayload,
    ScriptGatewayPayload,
    TextConfig,
    TextGatewayRequest,
    TextGatewayResponse,
)


class TextGatewayError(RuntimeError):
    """Sanitized gateway error with stable editorial error code."""

    def __init__(self, code: str, message: str | None = None) -> None:
        clean = message or code
        super().__init__(clean)
        self.code = code
        self.message = clean


class TextAdapter(Protocol):
    def generate(self, request: TextGatewayRequest) -> dict:
        """Return untrusted model payload for gateway validation."""


class DiscoveryTextGateway:
    """Validate and normalize all Discovery V2 text adapter responses."""

    def __init__(self, config: TextConfig | None = None) -> None:
        self.config = config or load_text_config()
        self.adapter = self._select_adapter(self.config)

    def generate(self, request: TextGatewayRequest) -> TextGatewayResponse:
        self._assert_request_matches_config(request)
        last_error: Exception | None = None
        total_attempts = self.config.max_retries + 1
        for attempt_index in range(total_attempts):
            try:
                raw = self.adapter.generate(request)
                payload = self._validate_payload(raw, request)
                return TextGatewayResponse(
                    request_kind=request.request_kind,
                    provider=self.config.provider,
                    model_identifier=self.config.model_identifier,
                    gateway_version=self.config.gateway_version,
                    prompt_version=request.prompt_version,
                    response_schema_version=request.response_schema_version,
                    attempt_count=attempt_index + 1,
                    narrative=payload if isinstance(payload, NarrativeGatewayPayload) else None,
                    script=payload if isinstance(payload, ScriptGatewayPayload) else None,
                    coverage=payload if isinstance(payload, CoverageGatewayPayload) else None,
                )
            except FakeTextTransientError as exc:
                last_error = exc
            except TextGatewayError as exc:
                if exc.code not in {
                    EDITORIAL_ERROR_RESPONSE_INVALID,
                    EDITORIAL_ERROR_RESPONSE_SCHEMA_MISMATCH,
                    EDITORIAL_ERROR_INVALID_SENTENCE_REFERENCE,
                    EDITORIAL_ERROR_INVALID_VISUAL_BEAT_REFERENCE,
                    EDITORIAL_ERROR_INVALID_VISUAL_INTENT_REFERENCE,
                }:
                    raise
                last_error = exc
            if attempt_index >= self.config.max_retries:
                raise TextGatewayError(
                    EDITORIAL_ERROR_RETRY_EXHAUSTED,
                    "Text gateway response failed after retries.",
                ) from last_error
        raise TextGatewayError(
            EDITORIAL_ERROR_RETRY_EXHAUSTED,
            "Text gateway response failed after retries.",
        ) from last_error

    def _select_adapter(self, config: TextConfig) -> TextAdapter:
        if not config.enabled:
            raise TextGatewayError(
                EDITORIAL_ERROR_GATEWAY_UNCONFIGURED,
                "Text gateway is not enabled.",
            )
        if config.provider != TEXT_PROVIDER:
            raise TextGatewayError(
                EDITORIAL_ERROR_MODEL_UNAVAILABLE,
                "Configured text provider is unavailable.",
            )
        return FakeTextAdapter()

    def _assert_request_matches_config(self, request: TextGatewayRequest) -> None:
        expected_prompt = self.config.prompts.get(request.request_kind)
        expected_schema = self.config.response_schemas.get(request.request_kind)
        expected = {
            "provider": self.config.provider,
            "model_identifier": self.config.model_identifier,
            "gateway_version": self.config.gateway_version,
            "prompt_version": expected_prompt,
            "response_schema_version": expected_schema,
        }
        for field_name, expected_value in expected.items():
            if getattr(request, field_name) != expected_value:
                raise TextGatewayError(
                    EDITORIAL_ERROR_GATEWAY_UNCONFIGURED,
                    "Text request does not match configured gateway.",
                )

    def _validate_payload(
        self,
        raw: object,
        request: TextGatewayRequest,
    ) -> NarrativeGatewayPayload | ScriptGatewayPayload | CoverageGatewayPayload:
        if not isinstance(raw, dict):
            raise TextGatewayError(
                EDITORIAL_ERROR_RESPONSE_INVALID,
                "Text model response was not a JSON object.",
            )
        try:
            if request.request_kind == "narrative":
                payload = NarrativeGatewayPayload.model_validate(raw)
                self._validate_narrative_refs(payload, request)
                return payload
            if request.request_kind in {"script", "structure"}:
                payload = ScriptGatewayPayload.model_validate(raw)
                self._validate_script_refs(payload)
                return payload
            if request.request_kind == "coverage":
                payload = CoverageGatewayPayload.model_validate(raw)
                self._validate_coverage_refs(payload, request)
                return payload
        except ValidationError as exc:
            raise TextGatewayError(
                _validation_error_code(exc),
                "Text model response failed schema validation.",
            ) from exc
        raise TextGatewayError(
            EDITORIAL_ERROR_RESPONSE_SCHEMA_MISMATCH,
            "Unsupported text request kind.",
        )

    def _validate_narrative_refs(
        self,
        payload: NarrativeGatewayPayload,
        request: TextGatewayRequest,
    ) -> None:
        known_observations = {obs.observation_id for obs in request.observations}
        for hook in payload.hooks:
            if hook.narrative_plan_id != payload.narrative_plan.narrative_plan_id:
                raise TextGatewayError(
                    EDITORIAL_ERROR_RESPONSE_SCHEMA_MISMATCH,
                    "Hook references a different narrative plan.",
                )
            if not set(hook.local_evidence_refs).issubset(known_observations):
                raise TextGatewayError(
                    EDITORIAL_ERROR_RESPONSE_SCHEMA_MISMATCH,
                    "Hook referenced unknown local evidence.",
                )

    def _validate_script_refs(self, payload: ScriptGatewayPayload) -> None:
        sentence_ids = {sentence.sentence_id for sentence in payload.sentences}
        claim_ids = {claim.claim_id for claim in payload.claims}
        beat_ids = {beat.visual_beat_id for beat in payload.visual_beats}
        if set(payload.script.sentence_order) != sentence_ids:
            raise TextGatewayError(
                EDITORIAL_ERROR_INVALID_SENTENCE_REFERENCE,
                "Script sentence_order does not match sentences.",
            )
        for sentence in payload.sentences:
            if not set(sentence.claim_ids).issubset(claim_ids):
                raise TextGatewayError(
                    EDITORIAL_ERROR_RESPONSE_SCHEMA_MISMATCH,
                    "Sentence referenced unknown claim.",
                )
            if not set(sentence.visual_beat_ids).issubset(beat_ids):
                raise TextGatewayError(
                    EDITORIAL_ERROR_INVALID_VISUAL_BEAT_REFERENCE,
                    "Sentence referenced unknown visual beat.",
                )
        for beat in payload.visual_beats:
            if not set(beat.sentence_ids).issubset(sentence_ids):
                raise TextGatewayError(
                    EDITORIAL_ERROR_INVALID_SENTENCE_REFERENCE,
                    "Visual beat referenced unknown sentence.",
                )
        for intent in payload.visual_intents:
            if intent.visual_beat_id not in beat_ids:
                raise TextGatewayError(
                    EDITORIAL_ERROR_INVALID_VISUAL_BEAT_REFERENCE,
                    "Visual intent referenced unknown beat.",
                )

    def _validate_coverage_refs(
        self,
        payload: CoverageGatewayPayload,
        request: TextGatewayRequest,
    ) -> None:
        known_intents = {intent.visual_intent_id for intent in request.visual_intents}
        known_assets = set(request.candidate_asset_ids)
        known_observations = {obs.observation_id for obs in request.observations}
        for result in payload.coverage_audit.results:
            if result.visual_intent_id not in known_intents:
                raise TextGatewayError(
                    EDITORIAL_ERROR_INVALID_VISUAL_INTENT_REFERENCE,
                    "Coverage referenced unknown visual intent.",
                )
            if not set(result.candidate_asset_ids).issubset(known_assets):
                raise TextGatewayError(
                    EDITORIAL_ERROR_RESPONSE_SCHEMA_MISMATCH,
                    "Coverage referenced unknown asset candidate.",
                )
            if not set(result.accepted_observation_ids).issubset(known_observations):
                raise TextGatewayError(
                    EDITORIAL_ERROR_RESPONSE_SCHEMA_MISMATCH,
                    "Coverage referenced unknown accepted observation.",
                )


def _validation_error_code(exc: ValidationError) -> str:
    schema_error_types = {
        "extra_forbidden",
        "missing",
        "literal_error",
        "list_type",
        "model_type",
        "enum",
    }
    for error in exc.errors():
        if str(error.get("type")) in schema_error_types:
            return EDITORIAL_ERROR_RESPONSE_SCHEMA_MISMATCH
    return EDITORIAL_ERROR_RESPONSE_INVALID


__all__ = ["DiscoveryTextGateway", "TextAdapter", "TextGatewayError"]
