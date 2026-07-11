"""Phase 4: Folder-Voice-overs-Tab — UI-Guard, Sperre ohne Dramaturgie, Smoke-Tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis
from otio_app.defaults import (
    VOICEOVER_GEN_DEFAULT_FOLDER_MAX_WORDS,
    VOICEOVER_GEN_DEFAULT_FOLDER_MIN_WORDS,
    VOICEOVER_GEN_DEFAULT_FOLDER_TARGET_WORDS,
)
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import get_edit_plan_dir, get_exports_dir, get_folder_inventory_path
from otio_app.services.plan_llm_client import PlanLlmResponse
from otio_app.services.voiceover_generation.dramaturgy_service import save_confirmed_dramaturgy
from otio_app.services.voiceover_generation.folder_voiceover_settings_service import (
    build_default_folder_voiceover_settings,
    load_folder_voiceover_settings,
    save_folder_voiceover_settings,
)
from otio_app.services.voiceover_generation.models import DramaturgyFolderEntry, DramaturgyPlan
from otio_app.services.voiceover_generation.voiceover_author_service import (
    generate_folder_voiceover,
    load_folder_voiceovers_confirmed,
    load_folder_voiceovers_draft,
)
from otio_app.ui.voiceover_generation.folder_voiceovers_tab import render_folder_voiceovers_page

_AUTHOR_MODULE = "otio_app.services.voiceover_generation.voiceover_author_service"
_APPTEST_SCRIPT_PATH = (
    Path(__file__).parent / "_apptest_scripts" / "voiceover_gen_model_settings_repro.py"
)


def _make_project(tmp_path: Path, *, mode: ProjectMode) -> Project:
    project_root = tmp_path / "USA"
    (project_root / "Grand Canyon").mkdir(parents=True)
    return Project(
        id="fvo-ui-project",
        name="Folder Voiceover UI Test",
        project_root=str(project_root),
        work_dir=str(project_root / "_otio"),
        project_mode=mode,
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )


def _patch_project_selector(project: Project, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("otio_app.ui.project_context.list_projects", lambda: [project])
    monkeypatch.setattr(
        "otio_app.ui.project_context.get_project_by_id",
        lambda project_id: project if project_id == project.id else None,
    )
    monkeypatch.setattr(
        "streamlit.session_state", {"active_project_id": project.id}, raising=False
    )


def test_page_renders_without_exception_when_no_confirmed_dramaturgy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _make_project(tmp_path, mode=ProjectMode.WITHOUT_VOICEOVER)
    _patch_project_selector(project, monkeypatch)

    render_folder_voiceovers_page()  # darf nicht werfen


def test_page_locked_without_confirmed_dramaturgy_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _make_project(tmp_path, mode=ProjectMode.WITHOUT_VOICEOVER)
    _patch_project_selector(project, monkeypatch)

    render_folder_voiceovers_page()

    assert not (project.work_dir_path / "voiceover_generation" / "folder_voiceover_settings.json").exists()
    assert not get_edit_plan_dir(project.work_dir_path).exists()
    assert not get_exports_dir(project.work_dir_path).exists()


def test_page_guards_with_voiceover_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _make_project(tmp_path, mode=ProjectMode.WITH_VOICEOVER)
    _patch_project_selector(project, monkeypatch)

    render_folder_voiceovers_page()  # darf nicht werfen und darf nichts schreiben
    assert not (project.work_dir_path / "voiceover_generation").exists()


def test_page_renders_with_confirmed_dramaturgy_and_draft(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _make_project(tmp_path, mode=ProjectMode.WITHOUT_VOICEOVER)
    inv_path = get_folder_inventory_path(project.work_dir_path, "Grand Canyon")
    inv_path.parent.mkdir(parents=True, exist_ok=True)
    analysis = AssetFolderAnalysis(
        folder="Grand Canyon",
        assets=[AssetMediaAnalysis(path="Grand Canyon/clip1.mp4", description="Weite Aufnahme.")],
    )
    inv_path.write_text(analysis.model_dump_json(indent=2), encoding="utf-8")

    plan = DramaturgyPlan(
        project_id=project.id,
        recommended_folder_order=[
            DramaturgyFolderEntry(folder_name="Grand Canyon", order_index=1, enabled=True)
        ],
    )
    save_confirmed_dramaturgy(project, plan)
    save_folder_voiceover_settings(project, build_default_folder_voiceover_settings(project))

    fake_response = PlanLlmResponse(
        provider="anthropic",
        model="claude-sonnet-5",
        raw_text='{"voiceover_text_full": "Text.", "sentence_items": []}',
    )
    with patch(f"{_AUTHOR_MODULE}.generate_plan_text_with_metadata", return_value=fake_response):
        generate_folder_voiceover(project, "Grand Canyon", provider="anthropic", model="claude-sonnet-5")

    _patch_project_selector(project, monkeypatch)
    render_folder_voiceovers_page()  # darf nicht werfen

    assert not get_edit_plan_dir(project.work_dir_path).exists()
    assert not get_exports_dir(project.work_dir_path).exists()


def test_page_render_with_multiple_folders_does_not_reload_shared_documents_per_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Performance-Fix (Juli 2026, Nutzerfeedback zu langen Ladezeiten bei
    vielen Ordnern): Project Brief / Style Profile / Dramaturgie / Settings
    werden EINMAL pro Seiten-Rendering geladen und an jeden Ordner
    weitergereicht statt einmal PRO ORDNER erneut von der Platte gelesen zu
    werden (via is_draft_stale -> compute_current_hashes)."""
    project_root = tmp_path / "USA"
    for folder in ("Grand Canyon", "Yellowstone"):
        (project_root / folder).mkdir(parents=True)
    project = Project(
        id="fvo-perf-project",
        name="Perf Test",
        project_root=str(project_root),
        work_dir=str(project_root / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=["Grand Canyon", "Yellowstone"],
        selected_asset_subdirs=["Grand Canyon", "Yellowstone"],
    )
    for folder in ("Grand Canyon", "Yellowstone"):
        inv_path = get_folder_inventory_path(project.work_dir_path, folder)
        inv_path.parent.mkdir(parents=True, exist_ok=True)
        analysis = AssetFolderAnalysis(
            folder=folder,
            assets=[AssetMediaAnalysis(path=f"{folder}/clip1.mp4", description="Weite Aufnahme.")],
        )
        inv_path.write_text(analysis.model_dump_json(indent=2), encoding="utf-8")

    plan = DramaturgyPlan(
        project_id=project.id,
        recommended_folder_order=[
            DramaturgyFolderEntry(folder_name="Grand Canyon", order_index=1, enabled=True),
            DramaturgyFolderEntry(folder_name="Yellowstone", order_index=2, enabled=True),
        ],
    )
    save_confirmed_dramaturgy(project, plan)
    save_folder_voiceover_settings(project, build_default_folder_voiceover_settings(project))

    fake_response = PlanLlmResponse(
        provider="anthropic",
        model="claude-sonnet-5",
        raw_text='{"voiceover_text_full": "Text.", "sentence_items": []}',
    )
    with patch(f"{_AUTHOR_MODULE}.generate_plan_text_with_metadata", return_value=fake_response):
        generate_folder_voiceover(project, "Grand Canyon", provider="anthropic", model="claude-sonnet-5")
        generate_folder_voiceover(project, "Yellowstone", provider="anthropic", model="claude-sonnet-5")

    _patch_project_selector(project, monkeypatch)

    from otio_app.services.voiceover_generation.dramaturgy_service import load_confirmed_dramaturgy
    from otio_app.services.voiceover_generation.folder_voiceover_settings_service import (
        load_folder_voiceover_settings,
    )
    from otio_app.services.voiceover_generation.project_brief_service import load_project_brief
    from otio_app.services.voiceover_generation.style_profile_service import load_style_profile

    with (
        patch(f"{_AUTHOR_MODULE}.load_project_brief", wraps=load_project_brief) as brief_mock,
        patch(f"{_AUTHOR_MODULE}.load_style_profile", wraps=load_style_profile) as style_mock,
        patch(f"{_AUTHOR_MODULE}.load_confirmed_dramaturgy", wraps=load_confirmed_dramaturgy) as plan_mock,
        patch(
            f"{_AUTHOR_MODULE}.load_folder_voiceover_settings", wraps=load_folder_voiceover_settings
        ) as settings_mock,
    ):
        render_folder_voiceovers_page()  # darf nicht werfen

    # Diese vier Loader werden NUR von compute_current_hashes (via
    # is_draft_stale) aufgerufen, wenn KEIN vorab geladenes Dokument
    # übergeben wird. Die Seite selbst lädt sie über ihre EIGENEN,
    # ungepatchten Importe (siehe render_folder_voiceovers_page) — deren
    # Aufrufe tauchen hier absichtlich NICHT auf. Vor dem Fix hätte
    # is_draft_stale diese vier Funktionen für JEDEN der zwei Ordner erneut
    # aufgerufen (also 2x jede statt 0x).
    assert brief_mock.call_count == 0
    assert style_mock.call_count == 0
    assert plan_mock.call_count == 0
    assert settings_mock.call_count == 0


def _run_repro(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, project_id: str) -> AppTest:
    monkeypatch.setenv("REPRO_ROOT", str(tmp_path))
    monkeypatch.setenv("REPRO_PROJECT_ID", project_id)
    monkeypatch.setenv(
        "REPRO_RENDER_FUNCTION",
        "otio_app.ui.voiceover_generation.folder_voiceovers_tab:render_folder_voiceovers_page",
    )
    monkeypatch.setenv("REPRO_SETUP", "dramaturgy_and_voiceovers_confirmed")
    monkeypatch.delenv("REPRO_STYLE_PROFILE_LIBRARY_NAME", raising=False)
    at = AppTest.from_file(str(_APPTEST_SCRIPT_PATH))
    at.run()
    assert not at.exception, at.exception
    return at


def test_bulk_action_buttons_are_present_below_drafts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nutzerfeedback: 'Ich will unterhalb der Drafts alle gleichzeitig
    speichern, bestätigen, validieren etc. können.'"""
    at = _run_repro(tmp_path, monkeypatch, "fvo-bulk-ui-project")
    button_labels = {button.label for button in at.button}
    assert "Alle Texte speichern" in button_labels
    assert "Alle neu generieren" in button_labels
    assert "Alle validieren" in button_labels
    assert "Alle bestätigen" in button_labels
    assert "Alle Bestätigungen zurücknehmen" in button_labels

    subheaders = {subheader.value for subheader in at.subheader}
    assert "Alle Ordner gleichzeitig" in subheaders


def test_bulk_action_buttons_have_no_cancel_button(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nutzerfeedback: 'dann warten wir erstmal mit dem Abbrechen Button' —
    es darf (noch) keine Abbrechen-Schaltfläche für die Sammel-Aktionen geben."""
    at = _run_repro(tmp_path, monkeypatch, "fvo-bulk-ui-project")
    button_labels = {button.label for button in at.button}
    assert not any("abbrechen" in label.lower() for label in button_labels)


def test_bulk_confirm_all_button_click_confirms_all_folders_with_drafts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id = "fvo-bulk-confirm-project"
    at = _run_repro(tmp_path, monkeypatch, project_id)

    confirm_all_button = next(b for b in at.button if b.label == "Alle bestätigen")
    at = confirm_all_button.click().run()
    assert not at.exception, at.exception

    project = Project(
        id=project_id,
        name="Repro",
        project_root=str(tmp_path / "USA"),
        work_dir=str(tmp_path / "USA" / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )
    confirmed = load_folder_voiceovers_confirmed(project)
    assert "Grand Canyon" in {item.folder_name for item in confirmed.items}


def test_bulk_save_all_after_confirm_all_does_not_undo_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression (Nutzerfeedback Juli 2026): 'Wenn ich alle auf einmal
    bestätigen will kommt der Status Needs validation.' Reproduziert die
    exakte Reihenfolge: erst 'Alle bestätigen', DANACH 'Alle Texte
    speichern' (z. B. aus Gewohnheit) — der Text hat sich dabei nicht
    geändert, die Bestätigung darf NICHT zurückgesetzt werden."""
    project_id = "fvo-save-after-confirm-project"
    at = _run_repro(tmp_path, monkeypatch, project_id)

    confirm_all_button = next(b for b in at.button if b.label == "Alle bestätigen")
    at = confirm_all_button.click().run()
    assert not at.exception, at.exception

    save_all_button = next(b for b in at.button if b.label == "Alle Texte speichern")
    at = save_all_button.click().run()
    assert not at.exception, at.exception

    project = Project(
        id=project_id,
        name="Repro",
        project_root=str(tmp_path / "USA"),
        work_dir=str(tmp_path / "USA" / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )
    from otio_app.services.voiceover_generation.voiceover_author_service import (
        load_folder_voiceovers_draft,
    )

    draft_doc = load_folder_voiceovers_draft(project)
    draft = next(item for item in draft_doc.items if item.folder_name == "Grand Canyon")
    assert draft.status == "CONFIRMED"


def test_bulk_unconfirm_all_button_click_unconfirms_all_folders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id = "fvo-bulk-unconfirm-project"
    # Erster Lauf: Setup bestätigt "Grand Canyon" bereits (persistiert auf
    # Platte). Zweiter, separater Lauf mit REPRO_SETUP=none: das Setup-Skript
    # wiederholt das Bestätigen NICHT erneut, sodass der Klick unten nicht
    # durch einen automatischen Re-Setup-Rerun überschrieben wird.
    _run_repro(tmp_path, monkeypatch, project_id)

    monkeypatch.setenv("REPRO_SETUP", "none")
    at = AppTest.from_file(str(_APPTEST_SCRIPT_PATH))
    at.run()
    assert not at.exception, at.exception

    unconfirm_all_button = next(b for b in at.button if b.label == "Alle Bestätigungen zurücknehmen")
    assert not unconfirm_all_button.disabled
    at = unconfirm_all_button.click().run()
    assert not at.exception, at.exception

    project = Project(
        id=project_id,
        name="Repro",
        project_root=str(tmp_path / "USA"),
        work_dir=str(tmp_path / "USA" / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )
    confirmed = load_folder_voiceovers_confirmed(project)
    assert confirmed.items == []


# --- Phase 1 (Juli 2026): grüner Button "Zielwortanzahl 135 anwenden" ---


def test_apply_word_target_button_is_present_with_green_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nutzerwunsch: 'Ich will alle neu hinzugefügten Buttons in grün haben,
    damit ich die Neuerungen sofort sehe.' Der Button selbst trägt das
    Erkennungsmerkmal (🟢) direkt im Label."""
    at = _run_repro(tmp_path, monkeypatch, "fvo-word-target-ui-project")
    button_labels = {button.label for button in at.button}
    assert "🟢 Zielwortanzahl 135 auf alle aktiven Folder anwenden" in button_labels


def test_apply_word_target_button_click_overrides_enabled_folder_words(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Klick setzt target_words/min_words/max_words auf den neuen Standard —
    unabhängig vom vorherigen (heuristik-basierten) Wert.

    Nutzt (wie test_bulk_unconfirm_all_button_click_unconfirms_all_folders)
    einen zweiten, separaten AppTest-Lauf mit REPRO_SETUP=none: das
    Setup-Skript baut/speichert sonst bei JEDEM internen Rerun (ausgelöst
    durch das st.rerun() im Button selbst) die Settings unconditionally neu
    aus der Dramaturgie-Heuristik auf — das würde unseren Klick-Effekt in
    genau diesem Testszenario sofort wieder überschreiben, obwohl die echte
    Anwendung außerhalb dieses Test-Repro-Skripts das nicht tut."""
    project_id = "fvo-word-target-click-project"
    _run_repro(tmp_path, monkeypatch, project_id)

    project = Project(
        id=project_id,
        name="Repro",
        project_root=str(tmp_path / "USA"),
        work_dir=str(tmp_path / "USA" / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )
    before = load_folder_voiceover_settings(project)
    grand_canyon_before = next(s for s in before.settings if s.folder_name == "Grand Canyon")
    # Sanity check: das Setup nutzt die Phase-3-Heuristik (recommended_word_count=0
    # in der Dramaturgie), der Ausgangswert ist also NICHT bereits der neue Standard.
    assert grand_canyon_before.target_words != VOICEOVER_GEN_DEFAULT_FOLDER_TARGET_WORDS

    monkeypatch.setenv("REPRO_SETUP", "none")
    at = AppTest.from_file(str(_APPTEST_SCRIPT_PATH))
    at.run()
    assert not at.exception, at.exception

    apply_button = next(
        b for b in at.button if b.label == "🟢 Zielwortanzahl 135 auf alle aktiven Folder anwenden"
    )
    at = apply_button.click().run()
    assert not at.exception, at.exception

    after = load_folder_voiceover_settings(project)
    grand_canyon_after = next(s for s in after.settings if s.folder_name == "Grand Canyon")
    assert grand_canyon_after.target_words == VOICEOVER_GEN_DEFAULT_FOLDER_TARGET_WORDS
    assert grand_canyon_after.min_words == VOICEOVER_GEN_DEFAULT_FOLDER_MIN_WORDS
    assert grand_canyon_after.max_words == VOICEOVER_GEN_DEFAULT_FOLDER_MAX_WORDS


def test_apply_word_target_button_click_does_not_change_existing_voiceover_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nutzervorgabe: 'Der Button ändert nur Settings, nicht sofort Texte.'"""
    project_id = "fvo-word-target-text-project"
    _run_repro(tmp_path, monkeypatch, project_id)

    project = Project(
        id=project_id,
        name="Repro",
        project_root=str(tmp_path / "USA"),
        work_dir=str(tmp_path / "USA" / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )
    draft_before = load_folder_voiceovers_draft(project)
    text_before = next(item for item in draft_before.items if item.folder_name == "Grand Canyon")

    monkeypatch.setenv("REPRO_SETUP", "none")
    at = AppTest.from_file(str(_APPTEST_SCRIPT_PATH))
    at.run()
    assert not at.exception, at.exception

    apply_button = next(
        b for b in at.button if b.label == "🟢 Zielwortanzahl 135 auf alle aktiven Folder anwenden"
    )
    at = apply_button.click().run()
    assert not at.exception, at.exception

    draft_after = load_folder_voiceovers_draft(project)
    text_after = next(item for item in draft_after.items if item.folder_name == "Grand Canyon")
    assert text_after.voiceover_text_full == text_before.voiceover_text_full
    assert text_after.status == text_before.status


# --- Phase 2 (Asset-bewusste Cut-Plan-Vorbereitung): Asset-Readiness-Button ---


def test_asset_readiness_button_is_present_with_green_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    at = _run_repro(tmp_path, monkeypatch, "fvo-readiness-present-project")
    button_labels = {button.label for button in at.button}
    assert "🟢 Asset-Readiness prüfen" in button_labels


def test_asset_readiness_button_click_shows_report_metrics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Repro-Fake-Text ('Text.', sentence_items=[]) hat keine Sätze —
    das Ergebnis muss trotzdem klar als PASS mit 0 Sätzen angezeigt werden,
    ohne Exception."""
    at = _run_repro(tmp_path, monkeypatch, "fvo-readiness-click-project")

    readiness_button = next(b for b in at.button if b.label == "🟢 Asset-Readiness prüfen")
    at = readiness_button.click().run()
    assert not at.exception, at.exception

    metric_values = {metric.label: metric.value for metric in at.metric}
    assert metric_values.get("Sätze") == "0"

    success_or_warning = [s.value for s in at.success] + [w.value for w in at.warning]
    assert any("Asset-Readiness" in message for message in success_or_warning)


def test_asset_readiness_click_does_not_write_any_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nutzervorgabe (Diagnose-Phase): rein lesend, keine Persistenz.

    Nutzt (wie die Zielwortanzahl-Tests) einen zweiten, separaten AppTest-
    Lauf mit REPRO_SETUP=none: sonst würde das Setup-Skript bei JEDEM
    internen Rerun (ausgelöst durch das st.rerun() im Button) erneut den
    Fake-LLM-Aufruf für generate_folder_voiceover ausführen und dabei
    llm_runs/-Artefakte schreiben — das wäre ein reiner Test-Repro-Effekt,
    nicht ein Schreibvorgang der eigentlichen Readiness-Diagnose."""
    project_id = "fvo-readiness-no-write-project"
    _run_repro(tmp_path, monkeypatch, project_id)

    monkeypatch.setenv("REPRO_SETUP", "none")
    at = AppTest.from_file(str(_APPTEST_SCRIPT_PATH))
    at.run()
    assert not at.exception, at.exception

    project_root = tmp_path / "USA"
    work_dir = project_root / "_otio"
    files_before = sorted(p for p in work_dir.rglob("*") if p.is_file())

    readiness_button = next(b for b in at.button if b.label == "🟢 Asset-Readiness prüfen")
    at = readiness_button.click().run()
    assert not at.exception, at.exception

    files_after = sorted(p for p in work_dir.rglob("*") if p.is_file())
    assert files_before == files_after


def test_asset_readiness_flags_sentence_with_invalid_asset_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end über die echte Seite: ein Satz mit einer Asset-ID, die
    nicht im Inventory existiert, muss als Auffälligkeit erscheinen."""
    project_id = "fvo-readiness-invalid-asset-project"
    monkeypatch.setenv("REPRO_ROOT", str(tmp_path))
    monkeypatch.setenv("REPRO_PROJECT_ID", project_id)
    monkeypatch.setenv(
        "REPRO_RENDER_FUNCTION",
        "otio_app.ui.voiceover_generation.folder_voiceovers_tab:render_folder_voiceovers_page",
    )
    monkeypatch.setenv("REPRO_SETUP", "none")
    monkeypatch.delenv("REPRO_STYLE_PROFILE_LIBRARY_NAME", raising=False)

    project_root = tmp_path / "USA"
    (project_root / "Grand Canyon").mkdir(parents=True)
    project = Project(
        id=project_id,
        name="Repro",
        project_root=str(project_root),
        work_dir=str(project_root / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )
    inv_path = get_folder_inventory_path(project.work_dir_path, "Grand Canyon")
    inv_path.parent.mkdir(parents=True, exist_ok=True)
    analysis = AssetFolderAnalysis(
        folder="Grand Canyon",
        assets=[AssetMediaAnalysis(path="Grand Canyon/clip1.mp4", asset_id="asset_clip1")],
    )
    inv_path.write_text(analysis.model_dump_json(indent=2), encoding="utf-8")

    plan = DramaturgyPlan(
        project_id=project.id,
        recommended_folder_order=[
            DramaturgyFolderEntry(folder_name="Grand Canyon", order_index=1, enabled=True)
        ],
    )
    save_confirmed_dramaturgy(project, plan)
    save_folder_voiceover_settings(project, build_default_folder_voiceover_settings(project))

    from otio_app.services.voiceover_generation.models import FolderVoiceoverDraft, SentenceItem
    from otio_app.services.voiceover_generation.voiceover_author_service import (
        load_folder_voiceovers_draft,
        save_folder_voiceovers_draft,
    )

    draft_doc = load_folder_voiceovers_draft(project)
    new_draft_item = FolderVoiceoverDraft(
        project_id=project.id,
        folder_name="Grand Canyon",
        order_index=1,
        voiceover_text_full="Ein Satz ohne echtes Asset.",
        word_count=5,
        sentence_items=[
            SentenceItem(
                sentence_id="sentence_001",
                text="Ein Satz ohne echtes Asset.",
                primary_asset_id="asset_does_not_exist",
            )
        ],
    )
    save_folder_voiceovers_draft(
        project, draft_doc.model_copy(update={"items": [*draft_doc.items, new_draft_item]})
    )

    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(_APPTEST_SCRIPT_PATH))
    at.run()
    assert not at.exception, at.exception

    readiness_button = next(b for b in at.button if b.label == "🟢 Asset-Readiness prüfen")
    at = readiness_button.click().run()
    assert not at.exception, at.exception

    warning_messages = [w.value for w in at.warning]
    assert any("NEEDS_REVIEW" in message for message in warning_messages)


# --- Phase 6 (Asset-bewusste Cut-Plan-Vorbereitung): grüner Regenerier-Button ---


def test_asset_aware_regen_buttons_are_present_with_green_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    at = _run_repro(tmp_path, monkeypatch, "fvo-asset-aware-present-project")
    button_labels = {button.label for button in at.button}
    assert "🟢 Asset-bewusst neu generieren (135 Wörter)" in button_labels
    assert "🟢 Alle asset-bewusst neu generieren (135 Wörter)" in button_labels


def test_asset_aware_regen_button_click_updates_settings_and_regenerates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id = "fvo-asset-aware-click-project"
    _run_repro(tmp_path, monkeypatch, project_id)

    project = Project(
        id=project_id,
        name="Repro",
        project_root=str(tmp_path / "USA"),
        work_dir=str(tmp_path / "USA" / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )
    before = load_folder_voiceover_settings(project)
    grand_canyon_before = next(s for s in before.settings if s.folder_name == "Grand Canyon")
    assert grand_canyon_before.target_words != VOICEOVER_GEN_DEFAULT_FOLDER_TARGET_WORDS

    monkeypatch.setenv("REPRO_SETUP", "none")
    at = AppTest.from_file(str(_APPTEST_SCRIPT_PATH))
    at.run()
    assert not at.exception, at.exception

    regen_button = next(
        b for b in at.button if b.label == "🟢 Asset-bewusst neu generieren (135 Wörter)"
    )
    at = regen_button.click().run()
    assert not at.exception, at.exception

    after = load_folder_voiceover_settings(project)
    grand_canyon_after = next(s for s in after.settings if s.folder_name == "Grand Canyon")
    assert grand_canyon_after.target_words == VOICEOVER_GEN_DEFAULT_FOLDER_TARGET_WORDS


def test_bulk_asset_aware_regen_button_click_updates_all_enabled_folders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id = "fvo-asset-aware-bulk-project"
    _run_repro(tmp_path, monkeypatch, project_id)

    project = Project(
        id=project_id,
        name="Repro",
        project_root=str(tmp_path / "USA"),
        work_dir=str(tmp_path / "USA" / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )

    monkeypatch.setenv("REPRO_SETUP", "none")
    at = AppTest.from_file(str(_APPTEST_SCRIPT_PATH))
    at.run()
    assert not at.exception, at.exception

    bulk_button = next(
        b for b in at.button if b.label == "🟢 Alle asset-bewusst neu generieren (135 Wörter)"
    )
    at = bulk_button.click().run()
    assert not at.exception, at.exception

    after = load_folder_voiceover_settings(project)
    grand_canyon_after = next(s for s in after.settings if s.folder_name == "Grand Canyon")
    assert grand_canyon_after.target_words == VOICEOVER_GEN_DEFAULT_FOLDER_TARGET_WORDS
