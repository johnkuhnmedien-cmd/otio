"""Definitionen externer API-Anbieter (Schlüssel-Namen & Metadaten)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ApiProvider:
    env_key: str
    label: str
    description: str
    placeholder: str
    docs_url: str = ""
    implemented: bool = False


API_PROVIDERS: tuple[ApiProvider, ...] = (
    ApiProvider(
        env_key="GEMINI_API_KEY",
        label="Google Gemini",
        description="Asset-Analyse (Frames) und optional Voice-over via Cloud.",
        placeholder="AIza…",
        docs_url="https://aistudio.google.com/apikey",
        implemented=True,
    ),
    ApiProvider(
        env_key="OPENAI_API_KEY",
        label="OpenAI (ChatGPT)",
        description="Schnittplan-Vorschläge mit GPT-5.5 / GPT-5.4 mini (experimentell).",
        placeholder="sk-…",
        docs_url="https://platform.openai.com/api-keys",
        implemented=True,
    ),
    ApiProvider(
        env_key="ANTHROPIC_API_KEY",
        label="Claude (Anthropic)",
        description="Schnittplan-Vorschläge mit Claude Opus 4.8 / Sonnet 5 (experimentell).",
        placeholder="sk-ant-…",
        docs_url="https://console.anthropic.com/settings/keys",
        implemented=True,
    ),
    ApiProvider(
        env_key="ELEVENLABS_API_KEY",
        label="ElevenLabs",
        description="Text-to-Speech für die Voice-over-Generierungs-Pipeline (Projekt ohne Voice-Over).",
        placeholder="sk_…",
        docs_url="https://elevenlabs.io/app/settings/api-keys",
        implemented=True,
    ),
    ApiProvider(
        env_key="PEXELS_API_KEY",
        label="Pexels",
        description="Geplant für Stock-Foto/Video-Fallback im Schnittplan.",
        placeholder="…",
        docs_url="https://www.pexels.com/api/",
        implemented=False,
    ),
    ApiProvider(
        env_key="ADOBE_STOCK_API_KEY",
        label="Adobe Stock — API Key",
        description=(
            "Für die Supplement-Suche im Cut Plan (Adobe wird vor Pexels durchsucht). "
            "Generative-AI-Assets werden dabei standardmäßig ausgeschlossen."
        ),
        placeholder="…",
        docs_url="https://developer.adobe.com/stock/docs/getting-started/",
        implemented=True,
    ),
    ApiProvider(
        env_key="ADOBE_STOCK_ACCESS_TOKEN",
        label="Adobe Stock — Access Token",
        description=(
            "Nur für automatische Lizenzierung/Download nötig (sofortige Lizenzierung + "
            "4K/HD-Download im Cut Plan). Ohne Token funktioniert die reine Suche trotzdem."
        ),
        placeholder="Bearer-Token …",
        docs_url="https://developer.adobe.com/stock/docs/getting-started/03-api-authentication/",
        implemented=True,
    ),
    ApiProvider(
        env_key="UNSPLASH_ACCESS_KEY",
        label="Unsplash",
        description="Geplant für Bild-Fallback über die Unsplash API.",
        placeholder="…",
        docs_url="https://unsplash.com/developers",
        implemented=False,
    ),
    ApiProvider(
        env_key="PIXABAY_API_KEY",
        label="Pixabay",
        description="Geplant für freie Stock-Bilder/Videos als Fallback.",
        placeholder="…",
        docs_url="https://pixabay.com/api/docs/",
        implemented=False,
    ),
)


def get_provider(env_key: str) -> ApiProvider | None:
    for provider in API_PROVIDERS:
        if provider.env_key == env_key:
            return provider
    return None
