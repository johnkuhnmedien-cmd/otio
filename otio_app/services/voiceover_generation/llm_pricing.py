"""Grobe LLM-Preisschätzungen (USD) für UI-Transparenz.

Wichtig: `max_output_tokens` ist ein Ceiling — abgerechnet werden nur
tatsächlich verbrauchte Tokens. Die UI zeigt deshalb Worst-Case-Kosten für
das gewählte Output-Limit plus geschätzte Input-Kosten.
"""

from __future__ import annotations

from dataclasses import dataclass

# Preise in USD pro 1M Tokens (Stand ~Juli 2026, Näherungswerte).
# Schlüssel: provider + model substring (lowercase match).
_PRICING_TABLE: tuple[tuple[str, str, float, float, str], ...] = (
    # provider_substr, model_substr, input_usd/MTok, output_usd/MTok, label
    # Spezifischere Treffer zuerst (Substring-Match).
    ("openai", "gpt-5.6-sol", 5.0, 30.0, "GPT-5.6 Sol (~$5/$30 pro 1M)"),
    ("openai", "gpt-5.6-terra", 2.5, 15.0, "GPT-5.6 Terra (~$2.50/$15 pro 1M)"),
    ("openai", "gpt-5.6-luna", 1.0, 6.0, "GPT-5.6 Luna (~$1/$6 pro 1M)"),
    ("openai", "gpt-5.4-mini", 0.40, 1.60, "OpenAI GPT-5.4 mini (Näherung)"),
    ("openai", "gpt-5.5", 5.0, 15.0, "OpenAI GPT-5.5 (Näherung)"),
    ("openai", "gpt-5", 5.0, 15.0, "OpenAI GPT-5-Klasse (Näherung)"),
    ("openai", "gpt-4", 2.5, 10.0, "OpenAI GPT-4-Klasse (Näherung)"),
    ("anthropic", "opus", 5.0, 25.0, "Claude Opus (~$5/$25 pro 1M)"),
    ("anthropic", "fable-5", 10.0, 50.0, "Claude Fable 5 (~$10/$50 pro 1M)"),
    ("anthropic", "haiku", 1.0, 5.0, "Claude Haiku (~$1/$5 pro 1M)"),
    ("anthropic", "sonnet-5", 2.0, 10.0, "Claude Sonnet 5 Intro (~$2/$10 pro 1M bis 31.08.2026)"),
    ("anthropic", "sonnet", 3.0, 15.0, "Claude Sonnet (~$3/$15 pro 1M)"),
    ("gemini", "3.5-flash", 0.50, 3.00, "Gemini 3.5 Flash (~$0.50/$3 pro 1M)"),
    ("gemini", "flash-lite", 0.10, 0.40, "Gemini Flash Lite (Näherung)"),
    ("gemini", "flash", 0.15, 0.60, "Gemini Flash (Näherung)"),
    ("gemini", "pro", 1.25, 10.0, "Gemini Pro (Näherung)"),
    ("xai", "grok", 3.0, 15.0, "xAI Grok (Näherung)"),
    ("openrouter", "", 3.0, 15.0, "OpenRouter (Näherung — abhängig vom Modell)"),
)

_DEFAULT_INPUT_USD_PER_MTOK = 3.0
_DEFAULT_OUTPUT_USD_PER_MTOK = 15.0
_DEFAULT_LABEL = "Standard-Näherung (~$3/$15 pro 1M)"


@dataclass(frozen=True)
class LlmPriceQuote:
    provider: str
    model: str
    input_usd_per_mtok: float
    output_usd_per_mtok: float
    label: str


@dataclass(frozen=True)
class LlmCostEstimate:
    input_tokens: int
    output_tokens_ceiling: int
    input_cost_usd: float
    output_ceiling_cost_usd: float
    total_ceiling_usd: float
    price: LlmPriceQuote


def resolve_llm_price(provider: str, model: str) -> LlmPriceQuote:
    combined = f"{(provider or '').strip().lower()} {(model or '').strip().lower()}"
    for prov_sub, model_sub, inp, out, label in _PRICING_TABLE:
        if prov_sub and prov_sub not in combined:
            continue
        if model_sub and model_sub not in combined:
            continue
        return LlmPriceQuote(
            provider=provider,
            model=model,
            input_usd_per_mtok=inp,
            output_usd_per_mtok=out,
            label=label,
        )
    return LlmPriceQuote(
        provider=provider,
        model=model,
        input_usd_per_mtok=_DEFAULT_INPUT_USD_PER_MTOK,
        output_usd_per_mtok=_DEFAULT_OUTPUT_USD_PER_MTOK,
        label=_DEFAULT_LABEL,
    )


def estimate_tokens_from_text(text: str) -> int:
    """Grobe Token-Schätzung (~4 Zeichen ≈ 1 Token)."""
    cleaned = (text or "").strip()
    if not cleaned:
        return 0
    return max(1, len(cleaned) // 4)


@dataclass(frozen=True)
class LlmActualCost:
    input_tokens: int
    output_tokens: int
    input_cost_usd: float
    output_cost_usd: float
    total_usd: float
    price: LlmPriceQuote
    price_unknown: bool


def actual_call_cost_usd(
    *,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> LlmActualCost:
    """Echte Kosten aus abgerechneten Tokens × interner Preisliste (kein Ceiling)."""
    price = resolve_llm_price(provider, model)
    inp = max(0, int(input_tokens))
    out = max(0, int(output_tokens))
    input_cost = (inp / 1_000_000.0) * price.input_usd_per_mtok
    output_cost = (out / 1_000_000.0) * price.output_usd_per_mtok
    return LlmActualCost(
        input_tokens=inp,
        output_tokens=out,
        input_cost_usd=input_cost,
        output_cost_usd=output_cost,
        total_usd=input_cost + output_cost,
        price=price,
        price_unknown=price.label == _DEFAULT_LABEL,
    )


def estimate_call_cost_usd(
    *,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens_ceiling: int,
) -> LlmCostEstimate:
    price = resolve_llm_price(provider, model)
    input_cost = (max(0, input_tokens) / 1_000_000.0) * price.input_usd_per_mtok
    output_ceiling = (
        max(0, output_tokens_ceiling) / 1_000_000.0
    ) * price.output_usd_per_mtok
    return LlmCostEstimate(
        input_tokens=max(0, input_tokens),
        output_tokens_ceiling=max(0, output_tokens_ceiling),
        input_cost_usd=input_cost,
        output_ceiling_cost_usd=output_ceiling,
        total_ceiling_usd=input_cost + output_ceiling,
        price=price,
    )


def format_usd(amount: float) -> str:
    if amount < 0.01:
        return f"${amount:.4f}"
    return f"${amount:.2f}"


def format_model_price_suffix(provider: str, model: str) -> str:
    """Kurzer Preis-Hinweis für Dropdown-Labels, z. B. ' · ~$2.50/$15 /1M'."""
    price = resolve_llm_price(provider, model)
    inp = price.input_usd_per_mtok
    out = price.output_usd_per_mtok
    inp_txt = f"${inp:g}" if inp >= 0.01 else f"${inp:.2f}"
    out_txt = f"${out:g}" if out >= 0.01 else f"${out:.2f}"
    return f" · ~{inp_txt}/{out_txt} pro 1M Tok"
