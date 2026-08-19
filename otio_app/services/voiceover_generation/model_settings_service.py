"""Provider-/Modell-Einstellungen pro Rolle (Voice-over-Generierungs-Pipeline).

API-Keys werden ausschließlich über das bestehende Environment-/API-Key-System
gelesen (otio_app.services.api_keys) — hier wird nie ein Key gespeichert oder
geloggt, nur provider+model als freier Text.
"""

from __future__ import annotations

import json

from otio_app.defaults import (
    VOICEOVER_GEN_DEFAULT_MODEL,
    VOICEOVER_GEN_DEFAULT_PROVIDER,
    VOICEOVER_GEN_DRAMATURGY_DEFAULT_MODEL,
    VOICEOVER_GEN_DRAMATURGY_DEFAULT_PROVIDER,
    VOICEOVER_GEN_ENHANCED_FUNNEL_DEFAULT_PROVIDER,
    VOICEOVER_GEN_INTRO_DEFAULT_MODEL,
    VOICEOVER_GEN_INTRO_DEFAULT_PROVIDER,
    VOICEOVER_GEN_MODEL_CHOICES,
    VOICEOVER_GEN_MODEL_LABELS,
    VOICEOVER_GEN_MODEL_PRESETS,
    VOICEOVER_GEN_PROVIDERS,
    resolve_funnel_gemini_model,
)
from otio_app.models import Project
from otio_app.project_layout import get_model_settings_path
from otio_app.services.voiceover_generation.models import (
    LlmRoleSettings,
    VoiceoverGenerationModelSettings,
)

# Revision 2: Dramaturgie GPT-5.6 Terra.
# Revision 3: Intro-Skript GPT-5.6 Terra (Cut-Modelle bleiben pro Sprache).
MODEL_SETTINGS_REVISION = 3

__all__ = [
    "MODEL_SETTINGS_REVISION",
    "VOICEOVER_GEN_PROVIDERS",
    "VOICEOVER_GEN_MODEL_PRESETS",
    "VOICEOVER_GEN_MODEL_CHOICES",
    "VOICEOVER_GEN_MODEL_LABELS",
    "default_model_settings",
    "load_model_settings",
    "save_model_settings",
    "resolve_llm_model_id",
    "split_llm_model_id",
    "combined_model_id",
    "format_voiceover_gen_model_label",
]


def default_model_settings() -> VoiceoverGenerationModelSettings:
    return VoiceoverGenerationModelSettings(settings_revision=MODEL_SETTINGS_REVISION)


def _legacy_implicit_anthropic_default(role: LlmRoleSettings) -> bool:
    return (
        role.provider == VOICEOVER_GEN_DEFAULT_PROVIDER
        and role.model == VOICEOVER_GEN_DEFAULT_MODEL
    )


def upgrade_model_settings(
    settings: VoiceoverGenerationModelSettings,
) -> tuple[VoiceoverGenerationModelSettings, bool]:
    """Hebt gespeicherte Settings auf die aktuelle Revision.

    Revision 2 setzt Dramaturgie auf GPT-5.6 Terra, wenn noch der alte
    implizite Anthropic-Standard drinsteht (oft mitgespeichert, weil jede
    Rolle die ganze Datei schreibt). Revision 3 dasselbe fürs Intro-Skript.
    Cut-Modelle werden nicht angefasst.
    """
    if settings.settings_revision >= MODEL_SETTINGS_REVISION:
        return settings, False
    updates: dict = {"settings_revision": MODEL_SETTINGS_REVISION}
    if settings.settings_revision < 2 and _legacy_implicit_anthropic_default(
        settings.dramaturgy
    ):
        updates["dramaturgy"] = LlmRoleSettings(
            provider=VOICEOVER_GEN_DRAMATURGY_DEFAULT_PROVIDER,
            model=VOICEOVER_GEN_DRAMATURGY_DEFAULT_MODEL,
        )
    if settings.settings_revision < 3 and _legacy_implicit_anthropic_default(
        settings.intro
    ):
        updates["intro"] = LlmRoleSettings(
            provider=VOICEOVER_GEN_INTRO_DEFAULT_PROVIDER,
            model=VOICEOVER_GEN_INTRO_DEFAULT_MODEL,
        )
    return settings.model_copy(update=updates), True


def _normalize_enhanced_funnel_role(
    settings: VoiceoverGenerationModelSettings,
) -> tuple[VoiceoverGenerationModelSettings, bool]:
    """Ersetzt tote Funnel-IDs (z. B. gemini-1.5-flash) durch den Funnel-Standard.

    Cut-Modelle bleiben unangetastet — nur ``enhanced_supplement_funnel``.
    """
    role = settings.enhanced_supplement_funnel
    resolved = resolve_funnel_gemini_model(role.model)
    if (
        role.provider == VOICEOVER_GEN_ENHANCED_FUNNEL_DEFAULT_PROVIDER
        and role.model == resolved
    ):
        return settings, False
    return settings.model_copy(
        update={
            "enhanced_supplement_funnel": LlmRoleSettings(
                provider=VOICEOVER_GEN_ENHANCED_FUNNEL_DEFAULT_PROVIDER,
                model=resolved,
            )
        }
    ), True


def load_model_settings(project: Project) -> VoiceoverGenerationModelSettings:
    path = get_model_settings_path(project.language_work_dir_path)
    if not path.is_file():
        return default_model_settings()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        loaded = VoiceoverGenerationModelSettings.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return default_model_settings()
    upgraded, changed = upgrade_model_settings(loaded)
    upgraded, funnel_changed = _normalize_enhanced_funnel_role(upgraded)
    if changed or funnel_changed:
        save_model_settings(project, upgraded)
    return upgraded


def save_model_settings(
    project: Project, settings: VoiceoverGenerationModelSettings
) -> VoiceoverGenerationModelSettings:
    path = get_model_settings_path(project.language_work_dir_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(settings.model_dump_json(indent=2), encoding="utf-8")
    return settings


def resolve_llm_model_id(provider: str, model: str) -> str:
    """Baut die von plan_llm_client erwartete Modell-ID.

    plan_llm_client.plan_model_provider() erkennt Provider anhand von Präfixen
    ("openai:", "anthropic:", "xai:", "openrouter:") und behandelt alles ohne
    Präfix als Gemini.
    """
    normalized_provider = (provider or "").strip().lower()
    normalized_model = (model or "").strip()
    if normalized_provider == "openai":
        return f"openai:{normalized_model}"
    if normalized_provider == "anthropic":
        return f"anthropic:{normalized_model}"
    if normalized_provider == "xai":
        return f"xai:{normalized_model}"
    if normalized_provider == "openrouter":
        return f"openrouter:{normalized_model}"
    return normalized_model


def split_llm_model_id(resolved_model_id: str) -> tuple[str, str]:
    """Kehrt resolve_llm_model_id() um: zerlegt eine kombinierte, im UI als EIN
    Dropdown-Wert gewählte Modell-ID (z. B. "openai:gpt-5.5") wieder in die
    getrennt gespeicherten Felder (provider, model) von LlmRoleSettings."""
    value = (resolved_model_id or "").strip()
    if value.startswith("openai:"):
        return "openai", value[len("openai:") :]
    if value.startswith("anthropic:"):
        return "anthropic", value[len("anthropic:") :]
    if value.startswith("xai:"):
        return "xai", value[len("xai:") :]
    if value.startswith("openrouter:"):
        return "openrouter", value[len("openrouter:") :]
    return "gemini", value


def combined_model_id(role_settings: LlmRoleSettings) -> str:
    return resolve_llm_model_id(role_settings.provider, role_settings.model)


def format_voiceover_gen_model_label(model_id: str) -> str:
    return VOICEOVER_GEN_MODEL_LABELS.get(model_id, model_id)
