"""Globale Enhanced-Cut-Plan-Settings pro Sprache."""

from __future__ import annotations

from pathlib import Path

import pytest

from otio_app.defaults import (
    CUT_PLAN_OPTIONS_DEFAULTS_FILENAME,
    DEFAULT_ENHANCED_WORK_SUBDIR,
)
from otio_app.models import Project, ProjectMode
from otio_app.services.voiceover_generation.language_defaults_catalog import (
    get_language_standard,
)
from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
    CUT_PLAN_MODE_UNIFIED,
    UNIFIED_CUT_STYLE_KEYWORD_FLOW,
    CutPlanOptions,
    load_cut_plan_options,
    persist_cut_plan_options,
    resolve_llm_cut_model_id,
    save_cut_plan_options,
)
from otio_app.services.without_voiceover_enhanced.cut_plan_options_defaults_service import (
    apply_language_defaults_to_options,
    default_cut_plan_options_for_project,
    get_cut_plan_options_defaults_path,
    load_language_cut_plan_defaults,
    save_language_cut_plan_defaults,
)


def _project(tmp_path: Path, *, language: str = "pt") -> Project:
    root = tmp_path / "Greece"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    (root / "Athens").mkdir()
    return Project(
        id="pt-greece-cut",
        name="PT_Greece",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        language=language,
        video_place="Griechenland",
        asset_subdir_names=["Athens"],
        selected_asset_subdirs=["Athens"],
    )


@pytest.fixture()
def cut_plan_defaults_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.cut_plan_options_defaults_service.ensure_data_dir",
        lambda: data_dir,
    )
    return data_dir


def test_save_and_load_language_cut_plan_defaults(
    cut_plan_defaults_dir: Path,
) -> None:
    saved = save_language_cut_plan_defaults(
        "pt",
        CutPlanOptions(
            cut_plan_mode=CUT_PLAN_MODE_UNIFIED,
            unified_cut_style=UNIFIED_CUT_STYLE_KEYWORD_FLOW,
            shot_min_sec=4.0,
            shot_max_sec=12.0,
            min_asset_reuse_distance_shots=6,
            voiceover_preroll_sec=2.0,
            elevenlabs_music_count=5,
            llm_cut_model="anthropic:claude-opus-5",
        ),
    )
    assert saved.cut_plan_mode == CUT_PLAN_MODE_UNIFIED
    assert saved.unified_cut_style == UNIFIED_CUT_STYLE_KEYWORD_FLOW
    assert saved.shot_min_sec == 4.0
    assert saved.elevenlabs_music_count == 5
    assert saved.llm_cut_model == "anthropic:claude-opus-5"
    loaded = load_language_cut_plan_defaults("PT")
    assert loaded is not None
    assert loaded.shot_max_sec == 12.0
    assert loaded.min_asset_reuse_distance_shots == 6
    assert loaded.voiceover_preroll_sec == 2.0
    assert loaded.llm_cut_model == "anthropic:claude-opus-5"
    assert loaded.llm_cut_prefix_count == 0
    assert loaded.llm_cut_prefix_model == ""
    assert get_cut_plan_options_defaults_path().is_relative_to(cut_plan_defaults_dir)
    assert get_cut_plan_options_defaults_path().name == CUT_PLAN_OPTIONS_DEFAULTS_FILENAME


def test_load_project_options_uses_language_standard_when_file_missing(
    tmp_path: Path, cut_plan_defaults_dir: Path
) -> None:
    save_language_cut_plan_defaults(
        "pt",
        CutPlanOptions(
            unified_cut_style=UNIFIED_CUT_STYLE_KEYWORD_FLOW,
            shot_min_sec=5.0,
            elevenlabs_music_count=8,
            llm_cut_model="openai:gpt-5.6-sol",
        ),
    )
    project = _project(tmp_path, language="pt")
    loaded = load_cut_plan_options(project)
    assert loaded.unified_cut_style == UNIFIED_CUT_STYLE_KEYWORD_FLOW
    assert loaded.shot_min_sec == 5.0
    assert loaded.elevenlabs_music_count == 8
    assert loaded.llm_cut_model == "openai:gpt-5.6-sol"
    assert resolve_llm_cut_model_id(project) == "openai:gpt-5.6-sol"


def test_existing_project_options_are_not_overwritten_by_language_standard(
    tmp_path: Path, cut_plan_defaults_dir: Path
) -> None:
    save_language_cut_plan_defaults(
        "pt",
        CutPlanOptions(shot_min_sec=5.0, elevenlabs_music_count=8),
    )
    project = _project(tmp_path, language="pt")
    save_cut_plan_options(
        project,
        CutPlanOptions(shot_min_sec=3.5, elevenlabs_music_count=2),
    )
    loaded = load_cut_plan_options(project)
    assert loaded.shot_min_sec == 3.5
    assert loaded.elevenlabs_music_count == 2


def test_apply_language_defaults_clamps_and_keeps_mode(
    cut_plan_defaults_dir: Path,
) -> None:
    defaults = save_language_cut_plan_defaults(
        "de",
        CutPlanOptions(
            cut_plan_mode=CUT_PLAN_MODE_UNIFIED,
            shot_min_sec=6.0,
            shot_max_sec=4.0,
        ),
    )
    applied = apply_language_defaults_to_options(
        CutPlanOptions(shot_min_sec=3.0),
        defaults,
    )
    assert applied.cut_plan_mode == CUT_PLAN_MODE_UNIFIED
    assert applied.shot_min_sec == 6.0
    assert applied.shot_max_sec == 6.0


def test_default_for_project_without_standard_is_hardcoded(
    tmp_path: Path, cut_plan_defaults_dir: Path
) -> None:
    project = _project(tmp_path, language="fr")
    options = default_cut_plan_options_for_project(project)
    assert options.shot_max_sec == 8.0
    assert options.min_asset_reuse_distance_shots == 4
    assert options.elevenlabs_music_count == 4
    assert options.llm_cut_model == ""


def test_catalog_includes_cut_plan_options_standard() -> None:
    item = get_language_standard("cut_plan_options")
    assert item.filename == CUT_PLAN_OPTIONS_DEFAULTS_FILENAME
    assert item.tab == "⑦ Cut Plan"
    assert item.per_language is True
    assert "LLM-Cut-Modell" in item.stores
    assert "Prefix" in item.stores
    assert "SFX-Planner-Modell" in item.stores


def test_persist_mirrors_llm_cut_model_to_both_roles(
    tmp_path: Path, cut_plan_defaults_dir: Path
) -> None:
    from otio_app.services.voiceover_generation.model_settings_service import (
        load_model_settings,
    )

    project = _project(tmp_path)
    saved = persist_cut_plan_options(
        project,
        CutPlanOptions(llm_cut_model="anthropic:claude-opus-5"),
    )
    assert saved.llm_cut_model == "anthropic:claude-opus-5"
    settings = load_model_settings(project)
    assert settings.enhanced_rough_cut.provider == "anthropic"
    assert settings.enhanced_rough_cut.model == "claude-opus-5"
    assert settings.enhanced_final_cut.provider == "anthropic"
    assert settings.enhanced_final_cut.model == "claude-opus-5"
    assert resolve_llm_cut_model_id(project) == "anthropic:claude-opus-5"


def test_persist_llm_cut_model_does_not_overwrite_sfx_planner(
    tmp_path: Path, cut_plan_defaults_dir: Path
) -> None:
    from otio_app.services.voiceover_generation.model_settings_service import (
        load_model_settings,
        save_model_settings,
    )
    from otio_app.services.voiceover_generation.models import (
        LlmRoleSettings,
        VoiceoverGenerationModelSettings,
    )
    from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
        persist_llm_cut_model,
    )

    project = _project(tmp_path)
    save_model_settings(
        project,
        VoiceoverGenerationModelSettings(
            enhanced_sfx_planner=LlmRoleSettings(
                provider="openai", model="gpt-5.4-mini"
            ),
        ),
    )
    persist_llm_cut_model(project, "anthropic:claude-opus-5")
    settings = load_model_settings(project)
    assert settings.enhanced_sfx_planner.model == "gpt-5.4-mini"
    assert settings.enhanced_rough_cut.model == "claude-opus-5"
    assert load_cut_plan_options(project).llm_cut_model == "anthropic:claude-opus-5"


def test_persist_llm_cut_model_does_not_overwrite_sfx_planner(
    tmp_path: Path, cut_plan_defaults_dir: Path
) -> None:
    from otio_app.services.voiceover_generation.model_settings_service import (
        load_model_settings,
        save_model_settings,
    )
    from otio_app.services.voiceover_generation.models import (
        LlmRoleSettings,
        VoiceoverGenerationModelSettings,
    )
    from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
        persist_llm_cut_model,
    )

    project = _project(tmp_path)
    save_model_settings(
        project,
        VoiceoverGenerationModelSettings(
            enhanced_sfx_planner=LlmRoleSettings(
                provider="openai", model="gpt-5.4-mini"
            ),
        ),
    )
    persist_llm_cut_model(project, "anthropic:claude-opus-5")
    settings = load_model_settings(project)
    assert settings.enhanced_sfx_planner.model == "gpt-5.4-mini"
    assert settings.enhanced_rough_cut.model == "claude-opus-5"
    assert load_cut_plan_options(project).llm_cut_model == "anthropic:claude-opus-5"


def test_resolve_llm_cut_falls_back_to_model_settings_when_unset(
    tmp_path: Path, cut_plan_defaults_dir: Path
) -> None:
    from otio_app.services.voiceover_generation.model_settings_service import (
        save_model_settings,
    )
    from otio_app.services.voiceover_generation.models import (
        LlmRoleSettings,
        VoiceoverGenerationModelSettings,
    )

    project = _project(tmp_path)
    save_cut_plan_options(project, CutPlanOptions(shot_min_sec=3.0))
    save_model_settings(
        project,
        VoiceoverGenerationModelSettings(
            enhanced_rough_cut=LlmRoleSettings(
                provider="anthropic", model="claude-fable-5"
            ),
            enhanced_final_cut=LlmRoleSettings(
                provider="openai", model="gpt-5.6-terra"
            ),
        ),
    )
    assert load_cut_plan_options(project).llm_cut_model == ""
    assert resolve_llm_cut_model_id(project) == "anthropic:claude-fable-5"


def test_resolve_prefers_cut_plan_options_over_model_settings(
    tmp_path: Path, cut_plan_defaults_dir: Path
) -> None:
    from otio_app.services.voiceover_generation.model_settings_service import (
        save_model_settings,
    )
    from otio_app.services.voiceover_generation.models import (
        LlmRoleSettings,
        VoiceoverGenerationModelSettings,
    )

    project = _project(tmp_path)
    save_cut_plan_options(
        project, CutPlanOptions(llm_cut_model="openai:gpt-5.6-sol")
    )
    save_model_settings(
        project,
        VoiceoverGenerationModelSettings(
            enhanced_rough_cut=LlmRoleSettings(
                provider="anthropic", model="claude-opus-5"
            ),
        ),
    )
    assert resolve_llm_cut_model_id(project) == "openai:gpt-5.6-sol"


def test_auto_run_helper_uses_language_llm_cut_model(
    tmp_path: Path, cut_plan_defaults_dir: Path
) -> None:
    from otio_app.services.without_voiceover_enhanced.enhanced_auto_run_service import (
        llm_cut_provider_model,
    )

    save_language_cut_plan_defaults(
        "pt",
        CutPlanOptions(llm_cut_model="anthropic:claude-opus-5"),
    )
    project = _project(tmp_path)
    assert llm_cut_provider_model(project) == ("anthropic", "claude-opus-5")


def test_resolve_llm_cut_prefix_uses_sol_for_intro_and_first_chapter(
    tmp_path: Path, cut_plan_defaults_dir: Path
) -> None:
    root = tmp_path / "Greece"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    for folder in ("Athens", "Thessaloniki"):
        (root / folder).mkdir()
    project = Project(
        id="pt-greece-cut-prefix",
        name="PT_Greece",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        language="pt",
        video_place="Griechenland",
        asset_subdir_names=["Athens", "Thessaloniki"],
        selected_asset_subdirs=["Athens", "Thessaloniki"],
    )
    save_cut_plan_options(
        project,
        CutPlanOptions(
            llm_cut_model="openai:gpt-5.6-terra",
            llm_cut_prefix_count=2,
            llm_cut_prefix_model="openai:gpt-5.6-sol",
        ),
    )
    assert resolve_llm_cut_model_id(project) == "openai:gpt-5.6-terra"
    assert resolve_llm_cut_model_id(project, is_intro=True) == "openai:gpt-5.6-sol"
    assert (
        resolve_llm_cut_model_id(project, folder_name="Athens")
        == "openai:gpt-5.6-sol"
    )
    assert (
        resolve_llm_cut_model_id(project, folder_name="Thessaloniki")
        == "openai:gpt-5.6-terra"
    )


def test_resolve_llm_cut_prefix_off_when_count_zero_or_model_empty(
    tmp_path: Path, cut_plan_defaults_dir: Path
) -> None:
    project = _project(tmp_path)
    save_cut_plan_options(
        project,
        CutPlanOptions(
            llm_cut_model="openai:gpt-5.6-terra",
            llm_cut_prefix_count=0,
            llm_cut_prefix_model="openai:gpt-5.6-sol",
        ),
    )
    assert resolve_llm_cut_model_id(project, is_intro=True) == "openai:gpt-5.6-terra"
    save_cut_plan_options(
        project,
        CutPlanOptions(
            llm_cut_model="openai:gpt-5.6-terra",
            llm_cut_prefix_count=2,
            llm_cut_prefix_model="",
        ),
    )
    assert resolve_llm_cut_model_id(project, is_intro=True) == "openai:gpt-5.6-terra"
    live = CutPlanOptions(
        llm_cut_model="openai:gpt-5.6-terra",
        llm_cut_prefix_count=2,
        llm_cut_prefix_model="openai:gpt-5.6-sol",
    )
    assert (
        resolve_llm_cut_model_id(project, is_intro=True, options=live)
        == "openai:gpt-5.6-sol"
    )


def test_language_defaults_roundtrip_llm_cut_prefix(
    cut_plan_defaults_dir: Path,
) -> None:
    saved = save_language_cut_plan_defaults(
        "pt",
        CutPlanOptions(
            llm_cut_model="openai:gpt-5.6-terra",
            llm_cut_prefix_count=2,
            llm_cut_prefix_model="openai:gpt-5.6-sol",
        ),
    )
    assert saved.llm_cut_prefix_count == 2
    assert saved.llm_cut_prefix_model == "openai:gpt-5.6-sol"
    loaded = load_language_cut_plan_defaults("PT")
    assert loaded is not None
    assert loaded.llm_cut_prefix_count == 2
    assert loaded.llm_cut_prefix_model == "openai:gpt-5.6-sol"


def test_auto_run_helper_uses_prefix_model_for_intro_and_first_chapter(
    tmp_path: Path, cut_plan_defaults_dir: Path
) -> None:
    from otio_app.services.without_voiceover_enhanced.enhanced_auto_run_service import (
        llm_cut_provider_model,
    )

    root = tmp_path / "Greece"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    for folder in ("Athens", "Győr"):
        (root / folder).mkdir()
    project = Project(
        id="pt-greece-cut-prefix-auto",
        name="PT_Greece",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        language="pt",
        video_place="Griechenland",
        asset_subdir_names=["Athens", "Győr"],
        selected_asset_subdirs=["Athens", "Győr"],
    )
    save_cut_plan_options(
        project,
        CutPlanOptions(
            llm_cut_model="openai:gpt-5.6-terra",
            llm_cut_prefix_count=2,
            llm_cut_prefix_model="openai:gpt-5.6-sol",
        ),
    )
    assert llm_cut_provider_model(project) == ("openai", "gpt-5.6-terra")
    assert llm_cut_provider_model(project, is_intro=True) == ("openai", "gpt-5.6-sol")
    assert llm_cut_provider_model(project, folder_name="Athens") == (
        "openai",
        "gpt-5.6-sol",
    )
    assert llm_cut_provider_model(project, folder_name="Győr") == (
        "openai",
        "gpt-5.6-terra",
    )


def test_cut_plan_ui_has_single_llm_cut_model_selectbox() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "otio_app"
        / "ui"
        / "without_voiceover_enhanced"
        / "cut_plan_tab.py"
    ).read_text(encoding="utf-8")
    assert source.count("enh_opt_llm_cut_model_") == 1
    assert source.count("enh_opt_llm_cut_prefix_model_") == 1
    assert source.count("enh_opt_llm_cut_prefix_count_") == 1
    assert "Standard-Modell (Unified Cut / Auto-Lauf)" in source
    assert 'label="Modell (Unified Cut)"' not in source
    assert "key_prefix}_model_" not in source
