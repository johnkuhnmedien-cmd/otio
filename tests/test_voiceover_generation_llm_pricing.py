"""Tests für LLM-Kostenschätzungen (Dramaturgie-Slider)."""

from __future__ import annotations

from otio_app.services.voiceover_generation.llm_pricing import (
    estimate_call_cost_usd,
    estimate_tokens_from_text,
    format_model_price_suffix,
    format_usd,
    resolve_llm_price,
)


def test_resolve_llm_price_sonnet_5_intro() -> None:
    price = resolve_llm_price("anthropic", "claude-sonnet-5")
    assert price.input_usd_per_mtok == 2.0
    assert price.output_usd_per_mtok == 10.0


def test_estimate_call_cost_worst_case_100k_output() -> None:
    estimate = estimate_call_cost_usd(
        provider="anthropic",
        model="claude-sonnet-5",
        input_tokens=50_000,
        output_tokens_ceiling=100_000,
    )
    # 50k * $2/M = $0.10 ; 100k * $10/M = $1.00
    assert abs(estimate.input_cost_usd - 0.10) < 1e-9
    assert abs(estimate.output_ceiling_cost_usd - 1.00) < 1e-9
    assert abs(estimate.total_ceiling_usd - 1.10) < 1e-9


def test_estimate_tokens_from_text() -> None:
    assert estimate_tokens_from_text("") == 0
    assert estimate_tokens_from_text("abcd") == 1
    assert estimate_tokens_from_text("a" * 400) == 100


def test_format_usd() -> None:
    assert format_usd(0.0012) == "$0.0012"
    assert format_usd(1.234) == "$1.23"


def test_resolve_llm_price_gpt_5_6_terra_and_sol() -> None:
    terra = resolve_llm_price("openai", "gpt-5.6-terra")
    sol = resolve_llm_price("openai", "gpt-5.6-sol")
    flash = resolve_llm_price("gemini", "gemini-3.5-flash")
    assert terra.input_usd_per_mtok == 2.5
    assert terra.output_usd_per_mtok == 15.0
    assert sol.input_usd_per_mtok == 5.0
    assert sol.output_usd_per_mtok == 30.0
    assert flash.input_usd_per_mtok == 0.5
    assert flash.output_usd_per_mtok == 3.0


def test_format_model_price_suffix_for_dropdown() -> None:
    suffix = format_model_price_suffix("openai", "gpt-5.6-terra")
    assert "~$2.5/$15 pro 1M Tok" in suffix or "~$2.50/$15 pro 1M Tok" in suffix
