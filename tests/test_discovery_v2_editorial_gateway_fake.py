"""Phase 9 fake text gateway tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from otio_app.discovery_v2.adapters import text_config
from otio_app.discovery_v2.adapters.text_fake import (
    FakeTextTransientError,
    reset_fake_text_test_hook,
    set_fake_text_test_hook,
)
from otio_app.discovery_v2.adapters.text_gateway import (
    DiscoveryTextGateway,
    TextGatewayError,
)
from otio_app.discovery_v2.domain.editorial import (
    EDITORIAL_ERROR_MODEL_UNAVAILABLE,
    EDITORIAL_ERROR_RETRY_EXHAUSTED,
    EditorialReadyObservationInput,
    ProjectBrief,
    ProjectBriefStatus,
    TextGatewayRequest,
    compute_text_sha256,
)


@pytest.fixture(autouse=True)
def _reset_fake_text() -> None:
    reset_fake_text_test_hook()
    yield
    reset_fake_text_test_hook()


def _brief(topic: str = "Fake Topic") -> ProjectBrief:
    now = datetime.now(timezone.utc)
    return ProjectBrief(
        project_brief_id="brief-1",
        project_id="project-1",
        language="de",
        topic=topic,
        target_audience="Audience",
        tone="klar",
        brief_version=1,
        content_sha256=compute_text_sha256(topic),
        status=ProjectBriefStatus.ACTIVE,
        created_at=now,
    )


def _request(topic: str = "Fake Topic") -> TextGatewayRequest:
    config = text_config.load_text_config()
    return TextGatewayRequest(
        project_id="project-1",
        run_id="run-1",
        request_kind="narrative",
        prompt=config.prompts["narrative"],
        provider=config.provider,
        model_identifier=config.model_identifier,
        gateway_version=config.gateway_version,
        prompt_version=config.prompts["narrative"],
        response_schema_version=config.response_schemas["narrative"],
        project_brief=_brief(topic),
        observations=[
            EditorialReadyObservationInput(
                observation_id="obs-1",
                asset_id="asset-1",
                analysis_identity_id="identity-1",
                working_media_id="wm-1",
                summary="Local observation",
                evidence_frame_ids=["frame-1"],
                geographic_confidence=0.0,
                synthetic_confidence=0.0,
                uncertainty_notes=[],
                observation_sha256="a" * 64,
                frame_set_fingerprint="b" * 64,
            )
        ],
        candidate_asset_ids=["asset-1"],
        input_fingerprint="fp-1",
    )


def test_gateway_uses_fake_only_and_unknown_provider_errors() -> None:
    response = DiscoveryTextGateway().generate(_request())
    assert response.provider == "fake"
    assert response.narrative is not None
    assert len(response.narrative.hooks) == 3
    bad_config = replace(text_config.load_text_config(), provider="other")
    with pytest.raises(TextGatewayError) as exc:
        DiscoveryTextGateway(config=bad_config)
    assert exc.value.code == EDITORIAL_ERROR_MODEL_UNAVAILABLE


def test_gateway_retries_transient_timeout_then_succeeds() -> None:
    calls = {"count": 0}

    def hook(request):
        calls["count"] += 1
        if calls["count"] < 2:
            return FakeTextTransientError("timeout")
        return None

    set_fake_text_test_hook(hook)
    response = DiscoveryTextGateway().generate(_request())
    assert response.attempt_count == 2
    assert calls["count"] == 2


def test_gateway_retries_invalid_and_schema_mismatch_then_exhausts() -> None:
    for topic in ("fake_text_force_invalid_json", "fake_text_force_extra_field", "fake_text_force_schema_error"):
        with pytest.raises(TextGatewayError) as exc:
            DiscoveryTextGateway().generate(_request(topic))
        assert exc.value.code == EDITORIAL_ERROR_RETRY_EXHAUSTED


def test_fake_forced_rate_limit_retries_then_exhausts() -> None:
    with pytest.raises(TextGatewayError) as exc:
        DiscoveryTextGateway().generate(_request("fake_text_force_rate_limit"))
    assert exc.value.code == EDITORIAL_ERROR_RETRY_EXHAUSTED


def test_new_text_modules_have_no_real_provider_or_http_imports() -> None:
    for rel in (
        "otio_app/discovery_v2/adapters/text_config.py",
        "otio_app/discovery_v2/adapters/text_gateway.py",
        "otio_app/discovery_v2/adapters/text_fake.py",
        "otio_app/discovery_v2/jobs/editorial_worker.py",
    ):
        source = Path(rel).read_text(encoding="utf-8").lower()
        for needle in ("gemini", "openai", "anthropic", "openrouter", "httpx", "requests"):
            assert needle not in source


def test_worker_imports_gateway_not_fake_adapter_directly() -> None:
    source = Path("otio_app/discovery_v2/jobs/editorial_worker.py").read_text(encoding="utf-8")
    assert "DiscoveryTextGateway" in source
    assert "FakeTextAdapter" not in source
