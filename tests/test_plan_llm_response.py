"""Tests für PlanLlmResponse Metadaten."""

from __future__ import annotations

from unittest.mock import patch

from otio_app.services.plan_llm_client import generate_plan_text_with_metadata


def test_generate_plan_text_with_metadata_returns_response() -> None:
    with patch(
        "otio_app.services.plan_llm_client._generate_openai_text_with_usage",
        return_value=('{"beats":[]}', {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3}),
    ):
        response = generate_plan_text_with_metadata(prompt="test", model="openai:gpt-5.5")
    assert response.raw_text == '{"beats":[]}'
    assert response.provider == "openai"
    assert response.token_usage["total_tokens"] == 3
