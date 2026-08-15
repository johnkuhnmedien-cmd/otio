"""UI-Tests für den Style-References-Tab — Nutzerfeedback Juli 2026:

1. "Modell Einstellungen bitte vereinfachen. Nur die Modelle listen (kein
   Freitext), nicht eine gesonderte Spalte für Provider."
2. "Wie speichere ich eine Style Reference ab? [...] Ich will gespeicherte
   Style Profiles für alle Projekte die ich erstelle aufrufbar machen."
"""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from otio_app.defaults import VOICEOVER_GEN_MODEL_LABELS, VOICEOVER_GEN_ROLE_LABELS

PROJECT_ID = "repro-project"
SCRIPT_PATH = Path(__file__).parent / "_apptest_scripts" / "style_references_repro.py"


def _run_repro(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AppTest:
    monkeypatch.setenv("REPRO_ROOT", str(tmp_path))
    monkeypatch.setenv("REPRO_PROJECT_ID", PROJECT_ID)
    at = AppTest.from_file(str(SCRIPT_PATH))
    at.run()
    assert not at.exception, at.exception
    return at


def test_page_renders_without_exception(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _run_repro(tmp_path, monkeypatch)


def test_style_source_mode_radio_is_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    at = _run_repro(tmp_path, monkeypatch)
    radio_labels = [radio.label for radio in at.radio]
    assert "Wie soll der Stil an die späteren LLM-Schritte gehen?" in radio_labels


def test_only_one_build_button_when_no_profile_exists_yet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nutzerfeedback: 'Was ist der Unterschied zwischen den beiden Buttons?'
    — beide taten exakt dasselbe. Jetzt gibt es nur EINEN Button, dessen
    Beschriftung sich je nach Zustand ändert."""
    at = _run_repro(tmp_path, monkeypatch)
    button_labels = [button.label for button in at.button]
    assert button_labels.count("Style Profile erstellen") == 1
    assert "Style Profile neu erstellen" not in button_labels


def test_button_label_switches_to_neu_erstellen_once_profile_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from otio_app.models import Project, ProjectMode
    from otio_app.services.voiceover_generation.models import VoiceoverStyleProfile
    from otio_app.services.voiceover_generation.style_profile_service import save_style_profile

    project = Project(
        id=PROJECT_ID,
        name="Repro",
        project_root=str(tmp_path / "USA"),
        work_dir=str(tmp_path / "USA" / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )
    save_style_profile(project, VoiceoverStyleProfile(project_id=project.id, overall_tone="calm"))

    at = _run_repro(tmp_path, monkeypatch)
    button_labels = [button.label for button in at.button]
    assert button_labels.count("Style Profile neu erstellen") == 1
    assert "Style Profile erstellen" not in button_labels

    captions = " ".join(caption.value for caption in at.caption)
    assert "Ersetzt das aktuell gespeicherte Style Profile" in captions


def test_model_settings_has_one_selectbox_per_role_no_free_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    at = _run_repro(tmp_path, monkeypatch)
    selectbox_labels = {selectbox.label for selectbox in at.selectbox}
    for label in VOICEOVER_GEN_ROLE_LABELS.values():
        assert label in selectbox_labels

    # Keine separate "Provider"-Spalte mehr, kein Modell-Freitext mehr.
    assert not any("provider" in label.lower() for label in selectbox_labels)
    text_input_labels = {text_input.label for text_input in at.text_input}
    assert not any("modell" in label.lower() for label in text_input_labels)


def test_model_selectbox_options_are_curated_model_choices(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    at = _run_repro(tmp_path, monkeypatch)
    style_profile_selectbox = next(
        selectbox
        for selectbox in at.selectbox
        if selectbox.label == VOICEOVER_GEN_ROLE_LABELS["style_profile"]
    )
    # AppTest liefert die formatierten Anzeige-Labels (format_func), nicht die
    # rohen Modell-IDs — daher gegen die Label-Werte prüfen.
    assert set(style_profile_selectbox.options) == set(VOICEOVER_GEN_MODEL_LABELS.values())
    # Default aus VoiceoverGenerationModelSettings ist anthropic/claude-sonnet-5
    # (value liefert im Gegensatz zu options die rohe, ungeformatete Modell-ID).
    assert style_profile_selectbox.value == "anthropic:claude-sonnet-5"


def test_ui_shows_language_standard_buttons(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    at = _run_repro(tmp_path, monkeypatch)
    button_labels = {button.label for button in at.button}
    assert any("Als Standard für" in label for label in button_labels)
    assert any("Standard zurück" in label for label in button_labels)
    captions = " ".join(caption.value for caption in at.caption)
    assert "Projektsprache" in captions


def test_save_language_standard_from_ui_persists_references(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    at = _run_repro(tmp_path, monkeypatch)
    intro = next(area for area in at.text_area if area.label == "Beispiel-Intro 1")
    at = intro.set_value("Die Wunder von Italien").run()
    assert not at.exception, at.exception
    save_lang = next(button for button in at.button if "Als Standard für" in button.label)
    at = save_lang.click().run()
    assert not at.exception, at.exception

    import otio_app.services.voiceover_generation.style_reference_defaults_service as defaults

    defaults.ensure_data_dir = lambda: tmp_path / "global_data"
    loaded = defaults.load_language_style_defaults("DE")
    assert loaded is not None
    assert loaded.intro_reference_texts == ["Die Wunder von Italien"]
    assert loaded.style_mode == "profile"


def test_style_profile_library_section_is_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    at = _run_repro(tmp_path, monkeypatch)
    subheaders = {subheader.value for subheader in at.subheader}
    assert "Style Profile Bibliothek (projektübergreifend)" in subheaders


def test_library_save_button_disabled_hint_when_no_profile_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    at = _run_repro(tmp_path, monkeypatch)
    captions = " ".join(caption.value for caption in at.caption)
    assert "existiert noch kein Style Profile" in captions
    assert "Bibliothek ist noch leer" in captions


def test_save_profile_to_library_then_load_into_another_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from otio_app.models import Project, ProjectMode
    from otio_app.services.voiceover_generation.style_profile_library_service import (
        get_profile_from_library,
    )
    from otio_app.services.voiceover_generation.style_profile_service import (
        load_style_profile,
        save_style_profile,
    )
    from otio_app.services.voiceover_generation.models import VoiceoverStyleProfile

    def _project() -> Project:
        return Project(
            id=PROJECT_ID,
            name="Repro",
            project_root=str(tmp_path / "USA"),
            work_dir=str(tmp_path / "USA" / "_otio"),
            project_mode=ProjectMode.WITHOUT_VOICEOVER,
            asset_subdir_names=["Grand Canyon"],
            selected_asset_subdirs=["Grand Canyon"],
        )

    project = _project()
    save_style_profile(
        project,
        VoiceoverStyleProfile(project_id=project.id, overall_tone="calm, cinematic"),
    )

    at = _run_repro(tmp_path, monkeypatch)
    name_input = next(
        text_input
        for text_input in at.text_input
        if text_input.label == "Name in der Bibliothek"
    )
    at = name_input.set_value("Ruhige Dokumentation").run()
    save_button = next(b for b in at.button if b.label == "In Bibliothek speichern")
    at = save_button.click().run()
    assert not at.exception, at.exception

    # Isolierte Bibliothek unter root/global_data (siehe Repro-Skript) enthält
    # den neuen Eintrag — nicht nur session_state.
    import otio_app.services.voiceover_generation.style_profile_library_service as lib_service

    lib_service.ensure_data_dir = lambda: tmp_path / "global_data"
    loaded_profile = get_profile_from_library("Ruhige Dokumentation")
    assert loaded_profile is not None
    assert loaded_profile.overall_tone == "calm, cinematic"

    # Die Original-Projekt-Kopie ist jetzt ebenfalls mit dem Bibliotheksnamen
    # verknüpft (Nutzerfeedback: Name statt Häkchen in den Voraussetzungen).
    assert load_style_profile(project).library_name == "Ruhige Dokumentation"

    # In ein zweites, unabhängiges Projekt übernehmen.
    other_project = Project(
        id="other-project",
        name="Anderes Projekt",
        project_root=str(tmp_path / "Other"),
        work_dir=str(tmp_path / "Other" / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=["Yellowstone"],
        selected_asset_subdirs=["Yellowstone"],
    )
    save_style_profile(other_project, loaded_profile)
    assert load_style_profile(other_project).overall_tone == "calm, cinematic"
    assert load_style_profile(other_project).library_name == "Ruhige Dokumentation"


def test_loading_raw_library_into_project_does_not_raise_streamlit_api_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: Laden nach Widget-Instanziierung setzte session_state der
    Radio/Textarea-Keys → StreamlitAPIException."""
    from otio_app.models import Project, ProjectMode
    from otio_app.services.voiceover_generation.models import STYLE_MODE_RAW_TEXT
    from otio_app.services.voiceover_generation.raw_style_library_service import (
        save_raw_to_library,
    )
    from otio_app.services.voiceover_generation.style_reference_service import (
        load_style_references,
    )
    import otio_app.services.voiceover_generation.raw_style_library_service as raw_lib
    import otio_app.services.voiceover_generation.style_profile_library_service as profile_lib

    monkeypatch.setenv("REPRO_ROOT", str(tmp_path))
    monkeypatch.setenv("REPRO_PROJECT_ID", PROJECT_ID)
    global_data = tmp_path / "global_data"
    raw_lib.ensure_data_dir = lambda: global_data
    profile_lib.ensure_data_dir = lambda: global_data

    save_raw_to_library(
        "Wunder DE",
        raw_reference_text="Ruhige Kapitel-Prosa mit konkreten Ortsdetails.",
        raw_intro_reference_text="Kurzer Intro-Hook mit Versprechen.",
    )

    at = AppTest.from_file(str(SCRIPT_PATH))
    at.run()
    assert not at.exception, at.exception

    mode_radio = next(
        radio
        for radio in at.radio
        if radio.label == "Wie soll der Stil an die späteren LLM-Schritte gehen?"
    )
    at = mode_radio.set_value(STYLE_MODE_RAW_TEXT).run()
    assert not at.exception, at.exception

    select_raw = next(
        selectbox
        for selectbox in at.selectbox
        if selectbox.label == "Gespeicherter Raw-Text-Satz"
    )
    at = select_raw.set_value("Wunder DE").run()
    load_buttons = [b for b in at.button if b.label == "In dieses Projekt laden"]
    assert len(load_buttons) == 1
    at = load_buttons[0].click().run()
    assert not at.exception, at.exception

    project = Project(
        id=PROJECT_ID,
        name="Repro",
        project_root=str(tmp_path / "USA"),
        work_dir=str(tmp_path / "USA" / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )
    loaded = load_style_references(project)
    assert loaded.style_mode == STYLE_MODE_RAW_TEXT
    assert "Kapitel-Prosa" in loaded.raw_reference_text
    assert "Intro-Hook" in loaded.raw_intro_reference_text
    assert loaded.raw_library_name == "Wunder DE"


def test_loading_from_library_via_ui_tags_project_profile_with_library_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nutzerfeedback: 'Können wir das geladene Profil anzeigen, also den
    Namen anstatt einem Haken?' — das Laden aus der Bibliothek über den
    Button muss den Namen auf der Projekt-Kopie hinterlegen."""
    from otio_app.models import Project, ProjectMode
    from otio_app.services.voiceover_generation.style_profile_service import load_style_profile

    monkeypatch.setenv("REPRO_ROOT", str(tmp_path))
    monkeypatch.setenv("REPRO_PROJECT_ID", PROJECT_ID)

    import otio_app.services.voiceover_generation.style_profile_library_service as lib_service
    from otio_app.services.voiceover_generation.models import VoiceoverStyleProfile

    lib_service.ensure_data_dir = lambda: tmp_path / "global_data"
    lib_service.save_profile_to_library(
        "Ruhige Dokumentation",
        VoiceoverStyleProfile(project_id="original-project", overall_tone="calm"),
    )

    at = AppTest.from_file(str(SCRIPT_PATH))
    at.run()
    assert not at.exception, at.exception

    select_library_entry = next(
        selectbox for selectbox in at.selectbox if selectbox.label == "Gespeichertes Style Profile"
    )
    at = select_library_entry.set_value("Ruhige Dokumentation").run()
    load_button = next(b for b in at.button if b.label == "In dieses Projekt laden")
    at = load_button.click().run()
    assert not at.exception, at.exception

    project = Project(
        id=PROJECT_ID,
        name="Repro",
        project_root=str(tmp_path / "USA"),
        work_dir=str(tmp_path / "USA" / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )
    loaded = load_style_profile(project)
    assert loaded is not None
    assert loaded.library_name == "Ruhige Dokumentation"
