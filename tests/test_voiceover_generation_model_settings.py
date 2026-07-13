"""Phase 2: Model-Settings-Service-Tests (Provider/Modell pro Rolle)."""

from __future__ import annotations

from pathlib import Path

import pytest

from otio_app.defaults import VOICEOVER_GEN_MODEL_CHOICES, VOICEOVER_GEN_MODEL_LABELS
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import get_model_settings_path, get_voiceover_generation_dir
from otio_app.services.voiceover_generation.model_settings_service import (
    combined_model_id,
    default_model_settings,
    format_voiceover_gen_model_label,
    load_model_settings,
    resolve_llm_model_id,
    save_model_settings,
    split_llm_model_id,
)
from otio_app.services.voiceover_generation.models import (
    LlmRoleSettings,
    VOICEOVER_GEN_ROLES,
    VoiceoverGenerationModelSettings,
)


def _make_project(tmp_path: Path) -> Project:
    project_root = tmp_path / "USA"
    project_root.mkdir()
    return Project(
        id="settings-project",
        name="Settings Test",
        project_root=str(project_root),
        work_dir=str(project_root / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )


def test_default_model_settings_has_all_roles() -> None:
    settings = default_model_settings()
    for role in VOICEOVER_GEN_ROLES:
        role_settings = getattr(settings, role)
        # Phase 11.1: cut_plan_supplement_query hat bewusst einen ANDEREN
        # Standard (Gemini, schnell/günstig für kurze Suchqueries statt
        # redaktioneller Texte) — siehe VOICEOVER_GEN_CUT_PLAN_SUPPLEMENT_QUERY_
        # DEFAULT_PROVIDER/_MODEL in defaults.py.
        if role == "cut_plan_supplement_query":
            assert role_settings.provider == "gemini"
            assert role_settings.model == "gemini-3.1-flash-lite"
        else:
            assert role_settings.provider == "anthropic"
            assert role_settings.model == "claude-sonnet-5"


def test_load_model_settings_returns_default_when_missing(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    settings = load_model_settings(project)
    assert settings.style_profile.provider == "anthropic"


def test_save_and_load_model_settings_roundtrip(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    settings = VoiceoverGenerationModelSettings(
        style_profile=LlmRoleSettings(provider="openai", model="gpt-5.5"),
        dramaturgy=LlmRoleSettings(provider="gemini", model="gemini-3.1-pro-preview"),
    )
    save_model_settings(project, settings)

    loaded = load_model_settings(project)
    assert loaded.style_profile.provider == "openai"
    assert loaded.style_profile.model == "gpt-5.5"
    assert loaded.dramaturgy.provider == "gemini"
    # Nicht überschriebene Rollen behalten ihre Defaults.
    assert loaded.voiceover_author.provider == "anthropic"


def test_save_model_settings_writes_only_under_voiceover_generation_dir(
    tmp_path: Path,
) -> None:
    project = _make_project(tmp_path)
    save_model_settings(project, default_model_settings())
    path = get_model_settings_path(project.language_work_dir_path)
    assert path.is_file()
    assert path.is_relative_to(get_voiceover_generation_dir(project.language_work_dir_path))


def test_resolve_llm_model_id_for_openai() -> None:
    assert resolve_llm_model_id("openai", "gpt-5.5") == "openai:gpt-5.5"


def test_resolve_llm_model_id_for_anthropic() -> None:
    assert resolve_llm_model_id("anthropic", "claude-sonnet-5") == "anthropic:claude-sonnet-5"


def test_resolve_llm_model_id_for_xai() -> None:
    assert resolve_llm_model_id("xai", "grok-4.5") == "xai:grok-4.5"


def test_resolve_llm_model_id_for_gemini_has_no_prefix() -> None:
    assert resolve_llm_model_id("gemini", "gemini-3.1-flash-lite") == "gemini-3.1-flash-lite"


def test_split_llm_model_id_for_openai() -> None:
    assert split_llm_model_id("openai:gpt-5.5") == ("openai", "gpt-5.5")


def test_split_llm_model_id_for_anthropic() -> None:
    assert split_llm_model_id("anthropic:claude-sonnet-5") == ("anthropic", "claude-sonnet-5")


def test_split_llm_model_id_for_xai() -> None:
    assert split_llm_model_id("xai:grok-4.5") == ("xai", "grok-4.5")


def test_split_llm_model_id_for_gemini_has_no_prefix() -> None:
    assert split_llm_model_id("gemini-3.1-pro-preview") == ("gemini", "gemini-3.1-pro-preview")


@pytest.mark.parametrize("model_id", VOICEOVER_GEN_MODEL_CHOICES)
def test_split_and_resolve_llm_model_id_roundtrip(model_id: str) -> None:
    provider, model = split_llm_model_id(model_id)
    assert resolve_llm_model_id(provider, model) == model_id


def test_combined_model_id_matches_one_of_the_curated_choices() -> None:
    role_settings = LlmRoleSettings(provider="anthropic", model="claude-sonnet-5")
    assert combined_model_id(role_settings) in VOICEOVER_GEN_MODEL_CHOICES


def test_every_curated_model_choice_has_a_label() -> None:
    for model_id in VOICEOVER_GEN_MODEL_CHOICES:
        assert VOICEOVER_GEN_MODEL_LABELS[model_id]


def test_format_voiceover_gen_model_label_falls_back_to_raw_id_for_unknown_model() -> None:
    assert format_voiceover_gen_model_label("some-unknown-model") == "some-unknown-model"


def test_format_voiceover_gen_model_label_returns_known_label() -> None:
    label = format_voiceover_gen_model_label("anthropic:claude-sonnet-5")
    assert label == VOICEOVER_GEN_MODEL_LABELS["anthropic:claude-sonnet-5"]
