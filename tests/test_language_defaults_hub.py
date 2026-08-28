"""Zentrale Sprachstandards-Seite: Katalog, Kopie, Navigation, Speichern."""

from __future__ import annotations

from pathlib import Path

import pytest

from otio_app.defaults import BRIEF_LANGUAGE_CHOICES
from otio_app.services.voiceover_generation.language_defaults_hub_service import (
    copy_language_defaults,
    delete_language_standard,
    language_defaults_overview,
    language_has_standard,
)
from otio_app.services.voiceover_generation.models import (
    IntroHookLanguageDefaults,
    ProjectBriefLanguageDefaults,
)
from otio_app.services.voiceover_generation.project_brief_defaults_service import (
    save_language_brief_defaults,
)
from otio_app.services.voiceover_generation.intro_hook_defaults_service import (
    save_language_intro_defaults,
)
from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
    CutPlanOptions,
)
from otio_app.services.without_voiceover_enhanced.cut_plan_options_defaults_service import (
    save_language_cut_plan_defaults,
)


@pytest.fixture()
def language_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    targets = [
        "otio_app.services.voiceover_generation.project_brief_defaults_service.ensure_data_dir",
        "otio_app.services.voiceover_generation.style_reference_defaults_service.ensure_data_dir",
        "otio_app.services.voiceover_generation.dramaturgy_defaults_service.ensure_data_dir",
        "otio_app.services.voiceover_generation.intro_hook_defaults_service.ensure_data_dir",
        "otio_app.services.voiceover_generation.elevenlabs_voice_defaults_service.ensure_data_dir",
        "otio_app.services.without_voiceover_enhanced.cut_plan_options_defaults_service.ensure_data_dir",
        "otio_app.services.voiceover_generation.language_defaults_catalog.ensure_data_dir",
    ]
    for target in targets:
        monkeypatch.setattr(target, lambda: data_dir)
    return data_dir


def test_overview_starts_empty(language_data_dir: Path) -> None:
    overview = language_defaults_overview()
    assert set(overview) == set(BRIEF_LANGUAGE_CHOICES)
    for language in BRIEF_LANGUAGE_CHOICES:
        assert overview[language]["project_brief"] is False
        assert overview[language]["cut_plan_options"] is False
        assert language_has_standard("project_brief", language) is False


def test_copy_language_defaults_copies_only_set_entries(
    language_data_dir: Path,
) -> None:
    save_language_brief_defaults(
        "pt",
        ProjectBriefLanguageDefaults(tone_tags=["cinematic"], global_extra_prompt="PT extra"),
    )
    save_language_intro_defaults(
        "pt",
        IntroHookLanguageDefaults(target_words=80, tone="documentary"),
    )
    save_language_cut_plan_defaults(
        "pt",
        CutPlanOptions(
            llm_cut_model="openai:gpt-5.6-terra",
            llm_cut_prefix_count=4,
            llm_cut_prefix_model="openai:gpt-5.6-sol",
        ),
    )
    copied = copy_language_defaults("pt", "jp")
    assert "project_brief" in copied
    assert "intro" in copied
    assert "cut_plan_options" in copied
    assert "style_references" not in copied
    assert language_has_standard("project_brief", "JP") is True
    assert language_has_standard("style_references", "JP") is False
    from otio_app.services.voiceover_generation.project_brief_defaults_service import (
        load_language_brief_defaults,
    )
    from otio_app.services.without_voiceover_enhanced.cut_plan_options_defaults_service import (
        load_language_cut_plan_defaults,
    )

    loaded_brief = load_language_brief_defaults("jp")
    assert loaded_brief is not None
    assert loaded_brief.tone_tags == ["cinematic"]
    loaded_cut = load_language_cut_plan_defaults("JP")
    assert loaded_cut is not None
    assert loaded_cut.llm_cut_prefix_count == 4
    assert loaded_cut.llm_cut_prefix_model == "openai:gpt-5.6-sol"


def test_copy_same_language_is_noop(language_data_dir: Path) -> None:
    assert copy_language_defaults("de", "DE") == []


def test_delete_language_standard(language_data_dir: Path) -> None:
    save_language_brief_defaults(
        "en", ProjectBriefLanguageDefaults(tone_tags=["calm"])
    )
    assert language_has_standard("project_brief", "EN") is True
    delete_language_standard("project_brief", "en")
    assert language_has_standard("project_brief", "EN") is False


def test_hub_page_is_wired_after_saved_projects() -> None:
    from otio_app.ui.navigation import (
        PAGE_LANGUAGE_DEFAULTS,
        PAGE_LIST,
        NAVIGATION_OPTIONS,
        VOICEOVER_GEN_ENHANCED_NAVIGATION_OPTIONS,
        VOICEOVER_GEN_NAVIGATION_OPTIONS,
    )

    assert PAGE_LANGUAGE_DEFAULTS == "Sprachstandards"
    for options in (
        NAVIGATION_OPTIONS,
        VOICEOVER_GEN_NAVIGATION_OPTIONS,
        VOICEOVER_GEN_ENHANCED_NAVIGATION_OPTIONS,
    ):
        assert options.index(PAGE_LANGUAGE_DEFAULTS) == options.index(PAGE_LIST) + 1

    source = (
        Path(__file__).resolve().parents[1]
        / "otio_app"
        / "ui"
        / "voiceover_generation"
        / "language_defaults_hub.py"
    ).read_text(encoding="utf-8")
    compile(source, str(Path(__file__).resolve().parents[1] / "otio_app" / "ui" / "voiceover_generation" / "language_defaults_hub.py"), "exec")
    assert "Alle Abschnitte für" in source
    assert "Von anderer Sprache kopieren" in source
    assert "Erste N Cuts mit anderem Modell" in (
        Path(__file__).resolve().parents[1]
        / "otio_app"
        / "ui"
        / "without_voiceover_enhanced"
        / "cut_plan_defaults_form.py"
    ).read_text(encoding="utf-8")


def test_hub_apptest_renders_and_saves_brief(
    language_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from streamlit.testing.v1 import AppTest

    script = (
        Path(__file__).resolve().parents[1]
        / "tests"
        / "_apptest_scripts"
        / "language_defaults_hub_repro.py"
    )
    monkeypatch.setenv("REPRO_DATA_DIR", str(language_data_dir))
    at = AppTest.from_file(str(script), default_timeout=20)
    at.run()
    assert not at.exception
    texts = " ".join(str(item.value) for item in at.markdown)
    texts += " ".join(str(item.value) for item in at.header)
    assert "Sprachstandards" in texts or any(
        "Sprachstandards" in str(item) for item in at.header
    )
    button_labels = [str(button.label) for button in at.button]
    assert any("Alle Abschnitte" in label for label in button_labels)
    assert any("PT-Standard speichern" in label or "DE-Standard speichern" in label for label in button_labels)
    assert "lang_hub_language" in [select.key for select in at.selectbox] or any(
        select.label == "Sprache" for select in at.selectbox
    )
