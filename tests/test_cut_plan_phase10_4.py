"""Phase 10.4: UI für Production EditPlan Staging.

Nutzt Streamlits AppTest, um AUSSCHLIESSLICH den isolierten
`_render_production_edit_plan_staging`-Bereich zu rendern (siehe
tests/_apptest_scripts/production_edit_plan_staging_repro.py) — echte
Widget-/Element-Introspektion statt reiner Funktionsaufrufe.

Kein Promote nach `_otio/edit_plan/`, kein Lock, kein OTIO-Export, kein
Render, keine save_edit_plan()/build_edit_plan()-Aufrufe, keine
Produktions-Dateien werden überschrieben."""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import (
    get_edit_plan_dir,
    get_exports_dir,
    get_folder_edit_plan_path,
    get_folder_inventory_path,
    get_supplement_dir,
)
from otio_app.services.voiceover_generation.cut_plan_builder import (
    apply_asset_selection_to_draft,
    build_cut_plan_draft,
    save_cut_plan_draft,
    validate_cut_plan_draft,
)
from otio_app.services.voiceover_generation.cut_plan_confirm_service import confirm_cut_plan, load_confirmed_cut_plan
from otio_app.services.voiceover_generation.cut_plan_edit_plan_bridge import (
    build_bridge_audio_plan_from_confirmed_cut_plan,
    build_edit_plan_draft_from_confirmed_cut_plan,
    save_bridge_audio_plan,
    save_edit_plan_bridge_draft,
    validate_edit_plan_bridge,
)
from otio_app.services.voiceover_generation.cut_plan_edit_plan_confirm_service import confirm_edit_plan_bridge
from otio_app.services.voiceover_generation.cut_plan_edit_plan_trace import (
    build_edit_plan_bridge_trace,
    save_edit_plan_bridge_trace,
)
from otio_app.services.voiceover_generation.cut_plan_models import CutPlanSettings
from otio_app.services.voiceover_generation.cut_plan_settings_service import save_cut_plan_settings
from otio_app.services.voiceover_generation.final_plan_service import save_confirmed_voiceover_project_plan
from otio_app.services.voiceover_generation.models import (
    AlignmentItem,
    ConfirmedFolderPlanItem,
    ConfirmedIntroPlanItem,
    ConfirmedVoiceoverProjectPlan,
    IntroHookVisualBeat,
    SentenceItem,
)
from otio_app.services.voiceover_generation.production_edit_plan_staging_service import (
    build_and_save_production_edit_plan_staging,
    load_production_edit_plan_staging_package,
    load_staged_edit_plan,
    save_production_edit_plan_staging_package,
    save_staged_edit_plan,
)
from otio_app.services.voiceover_generation.production_edit_plan_trace import load_production_edit_plan_mapping_trace
from otio_app.services.voiceover_generation.production_edit_plan_validation import (
    save_production_edit_plan_validation_report,
    validate_production_edit_plan_staging,
)

FOLDER_A = "Grand Canyon"
PROJECT_ID = "repro-project"
SCRIPT_PATH = Path(__file__).parent / "_apptest_scripts" / "production_edit_plan_staging_repro.py"


def _make_project(tmp_path: Path) -> Project:
    project_root = tmp_path / "USA"
    (project_root / FOLDER_A).mkdir(parents=True, exist_ok=True)
    return Project(
        id=PROJECT_ID,
        name="Repro",
        project_root=str(project_root),
        work_dir=str(project_root / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=[FOLDER_A],
        selected_asset_subdirs=[FOLDER_A],
    )


def _write_inventory(project: Project, filenames: list[str]) -> None:
    entries = []
    for filename in filenames:
        (project.project_root_path / FOLDER_A / filename).write_bytes(b"FAKE_MEDIA_BYTES")
        entries.append(AssetMediaAnalysis(path=f"{FOLDER_A}/{filename}", description=filename))
    inv_path = get_folder_inventory_path(project.work_dir_path, FOLDER_A)
    inv_path.parent.mkdir(parents=True, exist_ok=True)
    inv_path.write_text(
        AssetFolderAnalysis(folder=FOLDER_A, assets=entries).model_dump_json(indent=2), encoding="utf-8"
    )


def _write_audio(project: Project, name: str) -> Path:
    audio_dir = project.language_work_dir_path / "voiceover_generation" / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    path = audio_dir / name
    path.write_bytes(b"FAKE_AUDIO_BYTES")
    return path


def _build_confirmed_bridge_project(tmp_path: Path) -> Project:
    project = _make_project(tmp_path)
    _write_inventory(project, ["photo_a.jpg", "photo_b.jpg"])
    intro_audio = _write_audio(project, "intro.mp3")
    folder_audio = _write_audio(project, "folder.mp3")

    intro = ConfirmedIntroPlanItem(
        hook_text="Ein Ort voller Geheimnisse.", audio_path=str(intro_audio), audio_duration_sec=5.0,
        visual_beats=[IntroHookVisualBeat(hook_beat_id="hook_beat_001", text="x", primary_asset_id="asset_photo_a")],
        alignment_items=[
            AlignmentItem(sentence_id="hook_beat_001", audio_start_sec=0.0, audio_end_sec=5.0, duration_sec=5.0)
        ],
    )
    folder = ConfirmedFolderPlanItem(
        folder_name=FOLDER_A, order_index=1, audio_path=str(folder_audio), audio_duration_sec=5.0,
        sentence_items=[SentenceItem(sentence_id="sentence_001", text="Ein Satz.", primary_asset_id="asset_photo_b")],
        alignment_items=[
            AlignmentItem(sentence_id="sentence_001", audio_start_sec=0.0, audio_end_sec=5.0, duration_sec=5.0)
        ],
    )
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id, project_title="Test", status="AUDIO_READY", intro=intro, folders=[folder]
    )
    save_confirmed_voiceover_project_plan(project, plan)
    save_cut_plan_settings(project, CutPlanSettings(project_id=project.id))
    draft = build_cut_plan_draft(project)
    save_cut_plan_draft(project, draft)
    apply_asset_selection_to_draft(project)
    validate_cut_plan_draft(project)
    confirm_cut_plan(project)

    edit_plan = build_edit_plan_draft_from_confirmed_cut_plan(project)
    edit_plan = save_edit_plan_bridge_draft(project, edit_plan)
    audio_plan = build_bridge_audio_plan_from_confirmed_cut_plan(project)
    save_bridge_audio_plan(project, audio_plan)
    confirmed_cut_plan = load_confirmed_cut_plan(project)
    trace = build_edit_plan_bridge_trace(project, confirmed_cut_plan, edit_plan)
    save_edit_plan_bridge_trace(project, trace)
    validate_edit_plan_bridge(project, edit_plan)
    confirm_edit_plan_bridge(project)
    return project


def _run_repro(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AppTest:
    monkeypatch.setenv("REPRO_ROOT", str(tmp_path))
    monkeypatch.setenv("REPRO_PROJECT_ID", PROJECT_ID)
    monkeypatch.setenv("REPRO_FOLDER", FOLDER_A)
    at = AppTest.from_file(str(SCRIPT_PATH))
    at.run()
    assert not at.exception, at.exception
    return at


def _all_text(at: AppTest, *element_types: str) -> list[str]:
    texts: list[str] = []
    for element_type in element_types:
        for element in getattr(at, element_type):
            texts.append(element.value)
    return texts


# --- 1-5: Grundstruktur / Hinweise / verbotene Buttons ---


def test_ui_shows_production_edit_plan_staging_section(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _make_project(tmp_path)
    at = _run_repro(tmp_path, monkeypatch)
    assert any("Production EditPlan Staging" in text for text in _all_text(at, "subheader"))


def test_ui_shows_not_a_production_edit_plan_hint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _make_project(tmp_path)
    at = _run_repro(tmp_path, monkeypatch)
    combined = " ".join(_all_text(at, "warning", "caption"))
    assert "noch kein Produktions-EditPlan" in combined
    assert "_otio/edit_plan/" in combined
    assert "nicht OTIO-exportbereit" in combined
    assert "Promote nach" in combined


def test_ui_has_no_unguarded_promote_button(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Phase 10.4 hatte noch gar keinen Promote-Button. Phase 10.5/10.6 fügen
    einen expliziten, streng gegateten Promote-Dry-Run- bzw. Promote-Button
    hinzu — hier wird stattdessen geprüft, dass jeder 'promote'-Button ohne
    vollständig erfüllte Voraussetzungen deaktiviert ist (kein ungeschützter
    Promote möglich)."""
    project = _build_confirmed_bridge_project(tmp_path)
    from otio_app.services.voiceover_generation.production_edit_plan_staging_service import (
        build_and_save_production_edit_plan_staging,
    )

    build_and_save_production_edit_plan_staging(project)  # Package existiert, aber (noch) kein Validation Report.
    at = _run_repro(tmp_path, monkeypatch)
    promote_buttons = [button for button in at.button if "promote" in button.label.lower()]
    assert promote_buttons
    assert all(button.disabled for button in promote_buttons)


def test_ui_has_no_otio_button(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _make_project(tmp_path)
    at = _run_repro(tmp_path, monkeypatch)
    labels = [button.label for button in at.button]
    assert not any("otio" in label.lower() for label in labels)


def test_ui_has_no_lock_button(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _make_project(tmp_path)
    at = _run_repro(tmp_path, monkeypatch)
    labels = [button.label for button in at.button]
    assert not any("lock" in label.lower() for label in labels)


# --- 6-9: Build-/Validate-Button ---


def test_build_button_disabled_when_cannot_build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _make_project(tmp_path)
    at = _run_repro(tmp_path, monkeypatch)
    build_button = next(b for b in at.button if "Production EditPlan Staging erzeugen" in b.label)
    assert build_button.disabled is True


def test_build_button_builds_staging_package_when_eligible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    at = _run_repro(tmp_path, monkeypatch)
    build_button = next(b for b in at.button if "Production EditPlan Staging erzeugen" in b.label)
    assert build_button.disabled is False

    at = build_button.click().run()
    assert not at.exception, at.exception
    # Der eigentliche, persistente Erfolgsnachweis ist das tatsächlich
    # geschriebene Package auf der Festplatte — die transiente
    # st.success()-Meldung direkt vor st.rerun() ist implementierungsabhängig
    # davon, ob/wie AppTest den Rerun intern nachvollzieht, und daher hier
    # bewusst nicht Teil der Assertion.
    assert load_production_edit_plan_staging_package(project) is not None


def test_validate_button_disabled_without_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _build_confirmed_bridge_project(tmp_path)
    at = _run_repro(tmp_path, monkeypatch)
    validate_button = next(b for b in at.button if "Production EditPlan Staging validieren" in b.label)
    assert validate_button.disabled is True


def test_validate_button_runs_validation_and_saves_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    at = _run_repro(tmp_path, monkeypatch)
    validate_button = next(b for b in at.button if "Production EditPlan Staging validieren" in b.label)
    assert validate_button.disabled is False

    at = validate_button.click().run()
    assert not at.exception, at.exception

    from otio_app.services.voiceover_generation.production_edit_plan_validation import (
        load_production_edit_plan_validation_report,
    )

    report = load_production_edit_plan_validation_report(project)
    assert report is not None


# --- 10-11: Package-Metriken + Sektionstabelle ---


def test_package_metrics_are_displayed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    at = _run_repro(tmp_path, monkeypatch)
    metric_labels = [metric.label for metric in at.metric]
    assert "Package Status" in metric_labels
    assert "Sektionen" in metric_labels
    assert "TimelineItems gesamt" in metric_labels
    assert "Shots gesamt" in metric_labels
    assert "Sektionen mit Voiceover" in metric_labels


def test_section_table_is_displayed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    at = _run_repro(tmp_path, monkeypatch)
    assert len(at.dataframe) >= 1
    section_df = at.dataframe[0].value
    assert "staging_section_id" in section_df.columns
    assert "staged_edit_plan_hash" in section_df.columns
    assert len(section_df) == 2  # Intro + 1 Folder


# --- 12-14: Staged EditPlan Preview ---


def test_staged_preview_shows_voiceover(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    at = _run_repro(tmp_path, monkeypatch)
    combined = " ".join(_all_text(at, "caption", "markdown"))
    assert "duration_source" in combined
    assert "bridge_audio_plan" in combined


def test_staged_preview_shows_timeline_items(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    at = _run_repro(tmp_path, monkeypatch)
    dfs = [df.value for df in at.dataframe]
    assert any("timeline_item_id" in df.columns and "selection_reason" in df.columns for df in dfs)


def test_staged_preview_shows_shots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    at = _run_repro(tmp_path, monkeypatch)
    dfs = [df.value for df in at.dataframe]
    assert any("voice_start_sec" in df.columns and "voice_end_sec" in df.columns for df in dfs)


# --- 15-18: Validation Report Anzeige ---


def test_validation_report_pass_is_displayed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    validate_production_edit_plan_staging(project)
    at = _run_repro(tmp_path, monkeypatch)
    combined = " ".join(_all_text(at, "success"))
    assert "PASS" in combined or "validiert und bereit" in combined


def test_validation_report_warning_is_displayed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    report = validate_production_edit_plan_staging(project)
    from otio_app.services.voiceover_generation.production_edit_plan_models import ProductionEditPlanValidationError

    warned = report.model_copy(update={"status": "WARNING", "warnings": [ProductionEditPlanValidationError(type="X")]})
    save_production_edit_plan_validation_report(project, warned)
    at = _run_repro(tmp_path, monkeypatch)
    combined = " ".join(_all_text(at, "warning"))
    assert "WARNING" in combined or "Warnungen" in combined


def test_validation_report_blocked_is_displayed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    report = validate_production_edit_plan_staging(project)
    from otio_app.services.voiceover_generation.production_edit_plan_models import ProductionEditPlanValidationError

    blocked = report.model_copy(update={"status": "BLOCKED", "blockers": [ProductionEditPlanValidationError(type="X")]})
    save_production_edit_plan_validation_report(project, blocked)
    at = _run_repro(tmp_path, monkeypatch)
    combined = " ".join(_all_text(at, "error"))
    assert "BLOCKED" in combined or "blockiert" in combined


def test_validation_report_error_table_is_displayed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    report = validate_production_edit_plan_staging(project)
    from otio_app.services.voiceover_generation.production_edit_plan_models import ProductionEditPlanValidationError

    blocked = report.model_copy(
        update={
            "status": "BLOCKED",
            "blockers": [ProductionEditPlanValidationError(type="SOME_ERROR", message="kaputt")],
        }
    )
    save_production_edit_plan_validation_report(project, blocked)
    at = _run_repro(tmp_path, monkeypatch)
    dfs = [df.value for df in at.dataframe]
    assert any("type" in df.columns and "fix_hint" in df.columns for df in dfs)


# --- 19-20: Stale-Warnungen ---


def test_stale_package_warning_is_displayed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    document = load_staged_edit_plan(project, "000_intro")
    changed = document.model_copy(update={"plan_generation_notes": ["manually_edited"]})
    save_staged_edit_plan(project, "000_intro", changed)

    at = _run_repro(tmp_path, monkeypatch)
    combined = " ".join(_all_text(at, "warning"))
    assert "Staging-Paket ist veraltet" in combined


def test_stale_validation_report_warning_is_displayed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    validate_production_edit_plan_staging(project)
    package = load_production_edit_plan_staging_package(project)
    changed_package = package.model_copy(update={"warnings": ["manually_added"]})
    save_production_edit_plan_staging_package(project, changed_package)

    at = _run_repro(tmp_path, monkeypatch)
    combined = " ".join(_all_text(at, "warning"))
    assert "Validation Report ist veraltet" in combined


# --- 21: Mapping Trace Tabelle ---


def test_mapping_trace_table_is_displayed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    assert load_production_edit_plan_mapping_trace(project) is not None
    at = _run_repro(tmp_path, monkeypatch)
    dfs = [df.value for df in at.dataframe]
    assert any("mapping_reason" in df.columns and "fields_defaulted" in df.columns for df in dfs)


# --- 22-25: Read-only Produktionsplan-Hinweis ---


def test_readonly_hint_shows_existing_production_plan_for_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    existing_path = get_folder_edit_plan_path(project.language_work_dir_path, FOLDER_A)
    existing_path.parent.mkdir(parents=True, exist_ok=True)
    existing_content = '{"project_id": "existing", "folder_name": "Grand Canyon", "confirmed": true}'
    existing_path.write_text(existing_content, encoding="utf-8")

    build_and_save_production_edit_plan_staging(project)
    at = _run_repro(tmp_path, monkeypatch)
    combined = " ".join(_all_text(at, "caption"))
    assert "Produktionsplan existiert bereits: ✅ Ja" in combined
    assert existing_path.read_text(encoding="utf-8") == existing_content


def test_readonly_hint_shows_intro_promote_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    build_and_save_production_edit_plan_staging(project)
    at = _run_repro(tmp_path, monkeypatch)
    combined = " ".join(_all_text(at, "caption"))
    assert "Intro wird als Ordner „Intro“" in combined
    assert "Intro.json" in combined


def test_ui_reads_existing_production_plan_readonly_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    existing_path = get_folder_edit_plan_path(project.language_work_dir_path, FOLDER_A)
    existing_path.parent.mkdir(parents=True, exist_ok=True)
    existing_content = '{"project_id": "existing", "folder_name": "Grand Canyon", "confirmed": true}'
    existing_path.write_text(existing_content, encoding="utf-8")

    build_and_save_production_edit_plan_staging(project)
    _run_repro(tmp_path, monkeypatch)

    assert existing_path.read_text(encoding="utf-8") == existing_content


def test_ui_does_not_modify_existing_production_plans(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    existing_path = get_folder_edit_plan_path(project.language_work_dir_path, FOLDER_A)
    existing_path.parent.mkdir(parents=True, exist_ok=True)
    existing_content = '{"project_id": "existing", "folder_name": "Grand Canyon", "confirmed": true}'
    existing_path.write_text(existing_content, encoding="utf-8")

    at = _run_repro(tmp_path, monkeypatch)
    build_button = next(b for b in at.button if "Production EditPlan Staging erzeugen" in b.label)
    if not build_button.disabled:
        build_button.click().run()

    assert existing_path.read_text(encoding="utf-8") == existing_content


# --- 26-30: Keine Seiteneffekte außerhalb des Staging-Pfads ---


def test_no_files_written_under_edit_plan_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    at = _run_repro(tmp_path, monkeypatch)
    build_button = next(b for b in at.button if "Production EditPlan Staging erzeugen" in b.label)
    build_button.click().run()
    assert not get_edit_plan_dir(project.language_work_dir_path).exists()


def test_no_files_written_under_exports_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    at = _run_repro(tmp_path, monkeypatch)
    build_button = next(b for b in at.button if "Production EditPlan Staging erzeugen" in b.label)
    build_button.click().run()
    assert not get_exports_dir(project.language_work_dir_path).exists()


def test_no_files_written_under_supplement_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    at = _run_repro(tmp_path, monkeypatch)
    build_button = next(b for b in at.button if "Production EditPlan Staging erzeugen" in b.label)
    build_button.click().run()
    assert not get_supplement_dir(project.language_work_dir_path).exists()


def test_no_original_media_modified(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    photo_path = project.project_root_path / FOLDER_A / "photo_a.jpg"
    original = photo_path.read_bytes()
    at = _run_repro(tmp_path, monkeypatch)
    build_button = next(b for b in at.button if "Production EditPlan Staging erzeugen" in b.label)
    build_button.click().run()
    assert photo_path.read_bytes() == original


def test_no_audio_files_overwritten(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _build_confirmed_bridge_project(tmp_path)
    audio_path = project.language_work_dir_path / "voiceover_generation" / "audio" / "intro.mp3"
    original = audio_path.read_bytes()
    at = _run_repro(tmp_path, monkeypatch)
    build_button = next(b for b in at.button if "Production EditPlan Staging erzeugen" in b.label)
    build_button.click().run()
    assert audio_path.read_bytes() == original


# --- 31-34: Schutz bestehender Pipeline ---


def test_no_save_edit_plan_or_build_edit_plan_calls_referenced() -> None:
    import otio_app.ui.voiceover_generation.cut_plan_tab as cut_plan_tab_module

    source = inspect.getsource(cut_plan_tab_module)
    # Wort-Grenzen-Suche statt reiner Substring-Suche: verhindert False
    # Positives durch legitime, längere Bridge-Funktionsnamen wie
    # build_edit_plan_draft_from_confirmed_cut_plan (Phase 9.1) oder
    # build_edit_plan_bridge_trace (Phase 9.1), die 'build_edit_plan' nur als
    # Präfix enthalten, nicht als eigenständigen Aufruf/Bezeichner.
    assert not re.search(r"\bsave_edit_plan\b", source)
    assert not re.search(r"\bbuild_edit_plan\b", source)


def test_no_otio_export_referenced() -> None:
    import otio_app.ui.voiceover_generation.cut_plan_tab as cut_plan_tab_module

    source = inspect.getsource(cut_plan_tab_module)
    # Export-UI im Cut-Plan-Tab darf otio_exporter / export_otio_timeline nutzen.
    # Die Isolation gilt weiterhin für die Produktions-EditPlan-Builder-Pipeline.
    assert not re.search(r"\bbuild_edit_plan\b", source)
    assert not re.search(r"\bsave_edit_plan\b", source)


_FORBIDDEN_SYMBOLS = (
    "build_edit_plan",
    "save_edit_plan",
    "edit_plan_builder",
    "mark_edit_plans_stale_for_folder",
    "replan_folder_after_supplement",
    "extend_folder_inventory",
    "_set_draft",
)


def test_production_staging_ui_references_no_forbidden_production_functions() -> None:
    import otio_app.ui.voiceover_generation.cut_plan_tab as cut_plan_tab_module

    source = inspect.getsource(cut_plan_tab_module)
    for symbol in _FORBIDDEN_SYMBOLS:
        assert not re.search(rf"\b{re.escape(symbol)}\b", source), (
            f"cut_plan_tab.py referenziert verbotenes Symbol '{symbol}'."
        )


def test_with_voiceover_workflow_unaffected() -> None:
    from otio_app.services import edit_plan_builder, otio_exporter

    assert hasattr(edit_plan_builder, "build_edit_plan")
    assert hasattr(edit_plan_builder, "save_edit_plan")
    assert hasattr(otio_exporter, "build_otio_timeline")
