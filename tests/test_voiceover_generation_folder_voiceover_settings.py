"""Phase 4: Folder-Voice-over-Settings — Vorbefüllung aus Dramaturgie."""

from __future__ import annotations

from pathlib import Path

import pytest

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis
from otio_app.defaults import (
    VOICEOVER_GEN_DEFAULT_FOLDER_MAX_WORDS,
    VOICEOVER_GEN_DEFAULT_FOLDER_MIN_WORDS,
    VOICEOVER_GEN_DEFAULT_FOLDER_TARGET_WORDS,
)
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import get_folder_inventory_path, get_folder_voiceover_settings_path
from otio_app.services.voiceover_generation.dramaturgy_service import save_confirmed_dramaturgy
from otio_app.services.voiceover_generation.folder_voiceover_settings_service import (
    apply_standard_word_target_to_enabled_settings,
    build_default_folder_voiceover_settings,
    enabled_settings,
    load_folder_voiceover_settings,
    save_folder_voiceover_settings,
    update_folder_voiceover_settings,
)
from otio_app.services.voiceover_generation.models import (
    DramaturgyFolderEntry,
    DramaturgyPlan,
    FolderVoiceoverSetting,
)


def _make_project_with_confirmed_dramaturgy(tmp_path: Path) -> Project:
    project_root = tmp_path / "USA"
    project_root.mkdir()
    for folder in ("Grand Canyon", "Yellowstone"):
        (project_root / folder).mkdir()
    project = Project(
        id="settings-project",
        name="Settings Test",
        project_root=str(project_root),
        work_dir=str(project_root / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=["Grand Canyon", "Yellowstone"],
        selected_asset_subdirs=["Grand Canyon", "Yellowstone"],
    )
    for folder in ("Grand Canyon", "Yellowstone"):
        path = get_folder_inventory_path(project.work_dir_path, folder)
        path.parent.mkdir(parents=True, exist_ok=True)
        analysis = AssetFolderAnalysis(
            folder=folder,
            assets=[AssetMediaAnalysis(path=f"{folder}/clip1.mp4", description=f"{folder} view")],
        )
        path.write_text(analysis.model_dump_json(indent=2), encoding="utf-8")

    plan = DramaturgyPlan(
        project_id=project.id,
        recommended_folder_order=[
            DramaturgyFolderEntry(
                folder_name="Grand Canyon",
                order_index=1,
                enabled=True,
                dramaturgy_role="opener",
                recommended_word_count=140,
                recommended_min_words=126,
                recommended_max_words=154,
            ),
            DramaturgyFolderEntry(
                folder_name="Yellowstone",
                order_index=2,
                enabled=False,
                dramaturgy_role="setup",
                recommended_word_count=0,
                recommended_min_words=0,
                recommended_max_words=0,
            ),
        ],
    )
    save_confirmed_dramaturgy(project, plan)
    return project


def test_settings_prefilled_from_confirmed_dramaturgy(tmp_path: Path) -> None:
    project = _make_project_with_confirmed_dramaturgy(tmp_path)
    document = build_default_folder_voiceover_settings(project)

    by_folder = {setting.folder_name: setting for setting in document.settings}
    assert by_folder["Grand Canyon"].target_words == 140
    assert by_folder["Grand Canyon"].min_words == 126
    assert by_folder["Grand Canyon"].max_words == 154
    assert by_folder["Grand Canyon"].dramaturgy_role == "opener"
    assert by_folder["Grand Canyon"].enabled is True
    assert by_folder["Yellowstone"].enabled is False


def test_settings_fall_back_to_heuristic_when_dramaturgy_words_missing(tmp_path: Path) -> None:
    project = _make_project_with_confirmed_dramaturgy(tmp_path)
    document = build_default_folder_voiceover_settings(project)
    yellowstone = next(s for s in document.settings if s.folder_name == "Yellowstone")
    # 0-Werte in der Dramaturgie -> Phase-3-Heuristik greift, Ergebnis > 0.
    assert yellowstone.target_words > 0
    assert yellowstone.min_words > 0
    assert yellowstone.max_words > 0


def test_only_enabled_folders_are_returned_by_enabled_settings(tmp_path: Path) -> None:
    project = _make_project_with_confirmed_dramaturgy(tmp_path)
    document = build_default_folder_voiceover_settings(project)
    active = enabled_settings(document)
    assert [setting.folder_name for setting in active] == ["Grand Canyon"]


def test_enabled_settings_handles_none_document() -> None:
    assert enabled_settings(None) == []


def test_save_and_load_settings_roundtrip(tmp_path: Path) -> None:
    project = _make_project_with_confirmed_dramaturgy(tmp_path)
    document = build_default_folder_voiceover_settings(project)
    save_folder_voiceover_settings(project, document)

    loaded = load_folder_voiceover_settings(project)
    assert loaded is not None
    assert {s.folder_name for s in loaded.settings} == {"Grand Canyon", "Yellowstone"}

    path = get_folder_voiceover_settings_path(project.work_dir_path)
    assert path.is_file()


def test_load_settings_returns_none_when_missing(tmp_path: Path) -> None:
    project = _make_project_with_confirmed_dramaturgy(tmp_path)
    assert load_folder_voiceover_settings(project) is None


def test_update_folder_voiceover_settings_applies_edits(tmp_path: Path) -> None:
    project = _make_project_with_confirmed_dramaturgy(tmp_path)
    document = build_default_folder_voiceover_settings(project)
    save_folder_voiceover_settings(project, document)

    edited_rows = [
        {
            "folder_name": "Grand Canyon",
            "target_words": 200,
            "energy": "high",
            "must_include": "sunset, silence",
            "enabled": True,
        },
        {"folder_name": "Yellowstone", "enabled": True},
    ]
    updated = update_folder_voiceover_settings(project, edited_rows)
    by_folder = {setting.folder_name: setting for setting in updated.settings}
    assert by_folder["Grand Canyon"].target_words == 200
    assert by_folder["Grand Canyon"].energy == "high"
    assert by_folder["Grand Canyon"].must_include == ["sunset", "silence"]
    assert by_folder["Yellowstone"].enabled is True

    reloaded = load_folder_voiceover_settings(project)
    assert reloaded.settings == updated.settings


def _make_project_with_three_enabled_folders(tmp_path: Path) -> Project:
    """Drei aktivierte Ordner mit durchgängig nicht-leeren Dramaturgie-Hinweisen
    — deckt Übergang-von-vorher/Kontrast (rückwärts) sowie Übergang-zum-
    naechsten-Kapitel (vorwärts) für ersten/mittleren/letzten Ordner ab."""
    project_root = tmp_path / "USA"
    project_root.mkdir()
    folders = ["Grand Canyon", "Yellowstone", "Zion"]
    for folder in folders:
        (project_root / folder).mkdir()
    project = Project(
        id="settings-project-3",
        name="Settings Test 3",
        project_root=str(project_root),
        work_dir=str(project_root / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=folders,
        selected_asset_subdirs=folders,
    )
    for folder in folders:
        path = get_folder_inventory_path(project.work_dir_path, folder)
        path.parent.mkdir(parents=True, exist_ok=True)
        analysis = AssetFolderAnalysis(
            folder=folder,
            assets=[AssetMediaAnalysis(path=f"{folder}/clip1.mp4", description=f"{folder} view")],
        )
        path.write_text(analysis.model_dump_json(indent=2), encoding="utf-8")

    plan = DramaturgyPlan(
        project_id=project.id,
        recommended_folder_order=[
            DramaturgyFolderEntry(
                folder_name="Grand Canyon",
                order_index=1,
                enabled=True,
                dramaturgy_role="opener",
                recommended_word_count=140,
                recommended_min_words=126,
                recommended_max_words=154,
                transition_from_previous_hint="",
                contrast_or_commonality_hint="",
                transition_goal_to_next="Segue to the geysers of Yellowstone.",
            ),
            DramaturgyFolderEntry(
                folder_name="Yellowstone",
                order_index=2,
                enabled=True,
                dramaturgy_role="setup",
                recommended_word_count=140,
                recommended_min_words=126,
                recommended_max_words=154,
                transition_from_previous_hint="Leaving the canyon behind.",
                contrast_or_commonality_hint="Contrast rock vs. geothermal.",
                transition_goal_to_next="Segue to Zion's red cliffs.",
            ),
            DramaturgyFolderEntry(
                folder_name="Zion",
                order_index=3,
                enabled=True,
                dramaturgy_role="climax",
                recommended_word_count=140,
                recommended_min_words=126,
                recommended_max_words=154,
                transition_from_previous_hint="From geysers to red rock.",
                contrast_or_commonality_hint="Contrast heat vs. stone.",
                transition_goal_to_next="",
            ),
        ],
    )
    save_confirmed_dramaturgy(project, plan)
    return project


def test_first_enabled_folder_never_gets_backward_transition_or_contrast(
    tmp_path: Path,
) -> None:
    """Nutzerfeedback: 'Beim ersten Ort macht Übergang von vorher/Kontrast
    keinen Sinn und muss immer aus sein.' — auch wenn der Dramaturgie-Hinweis
    nicht leer ist."""
    project = _make_project_with_three_enabled_folders(tmp_path)
    document = build_default_folder_voiceover_settings(project)
    by_folder = {setting.folder_name: setting for setting in document.settings}

    first = by_folder["Grand Canyon"]
    assert first.transition_from_previous is False
    assert first.use_contrast_with_previous is False


def test_last_enabled_folder_never_gets_forward_transition(tmp_path: Path) -> None:
    """Übergang zum nächsten Kapitel ist beim LETZTEN Ordner immer aus —
    unabhängig davon, ob transition_goal_to_next gesetzt ist (hier ist er es
    nicht, aber die Regel muss auch bei gesetztem Hinweis greifen — siehe
    nächster Test)."""
    project = _make_project_with_three_enabled_folders(tmp_path)
    document = build_default_folder_voiceover_settings(project)
    by_folder = {setting.folder_name: setting for setting in document.settings}

    last = by_folder["Zion"]
    assert last.transition_to_next is False


def test_middle_folder_gets_both_backward_and_forward_transitions_from_hints(
    tmp_path: Path,
) -> None:
    """Ein mittlerer Ordner mit nicht-leeren Hinweisen in BEIDE Richtungen
    bekommt sowohl den rückwärtigen als auch den vorwärtigen Übergang aktiv."""
    project = _make_project_with_three_enabled_folders(tmp_path)
    document = build_default_folder_voiceover_settings(project)
    by_folder = {setting.folder_name: setting for setting in document.settings}

    middle = by_folder["Yellowstone"]
    assert middle.transition_from_previous is True
    assert middle.use_contrast_with_previous is True
    assert middle.transition_to_next is True


def test_first_folder_still_gets_forward_transition_when_hint_present(
    tmp_path: Path,
) -> None:
    """Gegenprobe zur Korrektur aus dem Nutzergespräch: der ERSTE Ordner darf
    (im Gegensatz zum rückwärtigen Übergang) den VORWÄRTIGEN Übergang
    weiterhin aktiv bekommen, wenn ein transition_goal_to_next-Hinweis da ist —
    dort gibt es ja tatsächlich einen nächsten Ort."""
    project = _make_project_with_three_enabled_folders(tmp_path)
    document = build_default_folder_voiceover_settings(project)
    by_folder = {setting.folder_name: setting for setting in document.settings}

    first = by_folder["Grand Canyon"]
    assert first.transition_to_next is True


def test_single_enabled_folder_gets_neither_backward_nor_forward_transition(
    tmp_path: Path,
) -> None:
    """Edge Case: ein einziger aktivierter Ordner ist gleichzeitig 'erster' UND
    'letzter' — beide Regeln müssen gleichzeitig greifen."""
    project = _make_project_with_confirmed_dramaturgy(tmp_path)
    document = build_default_folder_voiceover_settings(project)
    enabled = enabled_settings(document)
    assert len(enabled) == 1
    assert enabled[0].transition_from_previous is False
    assert enabled[0].use_contrast_with_previous is False
    assert enabled[0].transition_to_next is False


def test_update_folder_voiceover_settings_persists_transition_to_next(
    tmp_path: Path,
) -> None:
    project = _make_project_with_confirmed_dramaturgy(tmp_path)
    document = build_default_folder_voiceover_settings(project)
    save_folder_voiceover_settings(project, document)

    edited_rows = [
        {"folder_name": "Grand Canyon", "transition_to_next": True, "enabled": True},
        {"folder_name": "Yellowstone", "enabled": False},
    ]
    updated = update_folder_voiceover_settings(project, edited_rows)
    by_folder = {setting.folder_name: setting for setting in updated.settings}
    assert by_folder["Grand Canyon"].transition_to_next is True


# --- Phase 1 (Juli 2026): neue Standard-Zielwortanzahl 135/120/150 ---


def test_folder_voiceover_setting_model_default_is_135_words() -> None:
    """Der reine Pydantic-Modell-Default (ohne jede weitere Angabe) muss dem
    neuen Standard entsprechen — Nutzerwunsch, die Zielwortanzahl auf 135 zu
    senken, um dem Cut Plan mehr Spielraum zu geben."""
    setting = FolderVoiceoverSetting(folder_name="Grand Canyon")
    assert setting.target_words == 135 == VOICEOVER_GEN_DEFAULT_FOLDER_TARGET_WORDS
    assert setting.min_words == 120 == VOICEOVER_GEN_DEFAULT_FOLDER_MIN_WORDS
    assert setting.max_words == 150 == VOICEOVER_GEN_DEFAULT_FOLDER_MAX_WORDS


def test_apply_standard_word_target_raises_without_existing_settings(tmp_path: Path) -> None:
    project = _make_project_with_confirmed_dramaturgy(tmp_path)
    with pytest.raises(ValueError):
        apply_standard_word_target_to_enabled_settings(project)


def test_apply_standard_word_target_only_touches_enabled_folders(tmp_path: Path) -> None:
    """Nutzerwunsch: der Button darf nur aktivierte Ordner ändern —
    deaktivierte Ordner (hier Yellowstone) bleiben unangetastet."""
    project = _make_project_with_confirmed_dramaturgy(tmp_path)
    document = build_default_folder_voiceover_settings(project)
    # Vorab abweichende Werte setzen, damit ein Nicht-Verändern klar erkennbar ist.
    document = document.model_copy(
        update={
            "settings": [
                setting.model_copy(update={"target_words": 999, "min_words": 900, "max_words": 1000})
                for setting in document.settings
            ]
        }
    )
    save_folder_voiceover_settings(project, document)

    updated = apply_standard_word_target_to_enabled_settings(project)
    by_folder = {setting.folder_name: setting for setting in updated.settings}

    assert by_folder["Grand Canyon"].target_words == VOICEOVER_GEN_DEFAULT_FOLDER_TARGET_WORDS
    assert by_folder["Grand Canyon"].min_words == VOICEOVER_GEN_DEFAULT_FOLDER_MIN_WORDS
    assert by_folder["Grand Canyon"].max_words == VOICEOVER_GEN_DEFAULT_FOLDER_MAX_WORDS

    # Yellowstone ist deaktiviert -> bleibt bei den absichtlich abweichenden Werten.
    assert by_folder["Yellowstone"].target_words == 999
    assert by_folder["Yellowstone"].min_words == 900
    assert by_folder["Yellowstone"].max_words == 1000


def test_apply_standard_word_target_does_not_touch_other_fields(tmp_path: Path) -> None:
    """Nur target_words/min_words/max_words dürfen sich ändern — alle
    anderen Settings-Felder (z. B. energy, must_include) bleiben exakt
    erhalten."""
    project = _make_project_with_confirmed_dramaturgy(tmp_path)
    document = build_default_folder_voiceover_settings(project)
    document = document.model_copy(
        update={
            "settings": [
                setting.model_copy(update={"energy": "high", "must_include": ["sunset"]})
                if setting.folder_name == "Grand Canyon"
                else setting
                for setting in document.settings
            ]
        }
    )
    save_folder_voiceover_settings(project, document)

    updated = apply_standard_word_target_to_enabled_settings(project)
    by_folder = {setting.folder_name: setting for setting in updated.settings}
    assert by_folder["Grand Canyon"].energy == "high"
    assert by_folder["Grand Canyon"].must_include == ["sunset"]


def test_apply_standard_word_target_persists_to_disk(tmp_path: Path) -> None:
    project = _make_project_with_confirmed_dramaturgy(tmp_path)
    document = build_default_folder_voiceover_settings(project)
    save_folder_voiceover_settings(project, document)

    apply_standard_word_target_to_enabled_settings(project)
    reloaded = load_folder_voiceover_settings(project)
    assert reloaded is not None
    grand_canyon = next(s for s in reloaded.settings if s.folder_name == "Grand Canyon")
    assert grand_canyon.target_words == VOICEOVER_GEN_DEFAULT_FOLDER_TARGET_WORDS
