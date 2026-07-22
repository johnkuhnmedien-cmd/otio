"""Phase 4: Review-/Correction-Loop, Confirm/Unconfirm, LLM-Traceability."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import (
    get_folder_inventory_path,
    get_folder_voiceover_validation_report_path,
    get_folder_voiceovers_confirmed_path,
    get_llm_runs_dir,
)
from otio_app.services.plan_llm_client import PlanLlmResponse
from otio_app.services.voiceover_generation.dramaturgy_service import save_confirmed_dramaturgy
from otio_app.services.voiceover_generation.folder_voiceover_settings_service import (
    build_default_folder_voiceover_settings,
    save_folder_voiceover_settings,
)
from otio_app.defaults import VO_ERROR_MISSING_TRANSITION_TO_NEXT
from otio_app.services.voiceover_generation.models import (
    DramaturgyFolderEntry,
    DramaturgyPlan,
    FolderVoiceoverDraft,
    FolderVoiceoverSetting,
)
from otio_app.services.voiceover_generation.voiceover_author_service import (
    generate_folder_voiceover,
    load_folder_voiceovers_confirmed,
    load_folder_voiceovers_draft,
)
from otio_app.services.voiceover_generation.voiceover_review_service import (
    apply_corrected_voiceover,
    confirm_all_folder_voiceovers,
    confirm_folder_voiceover,
    load_validation_reports,
    run_deterministic_checks,
    run_folder_voiceover_review_loop,
    unconfirm_all_folder_voiceovers,
    unconfirm_folder_voiceover,
    validate_all_folder_voiceovers,
)

_AUTHOR_MODULE = "otio_app.services.voiceover_generation.voiceover_author_service"
_REVIEW_MODULE = "otio_app.services.voiceover_generation.voiceover_review_service"


def _make_project(tmp_path: Path) -> Project:
    project_root = tmp_path / "USA"
    project_root.mkdir()
    folder = "Grand Canyon"
    (project_root / folder).mkdir()
    project = Project(
        id="review-project",
        name="Review Test",
        project_root=str(project_root),
        work_dir=str(project_root / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=[folder],
        selected_asset_subdirs=[folder],
    )
    inv_path = get_folder_inventory_path(project.work_dir_path, folder)
    inv_path.parent.mkdir(parents=True, exist_ok=True)
    analysis = AssetFolderAnalysis(
        folder=folder,
        assets=[
            AssetMediaAnalysis(path=f"{folder}/clip1.mp4", description="Weite Aufnahme der Schlucht."),
            AssetMediaAnalysis(path=f"{folder}/clip2.mp4", description="Nahaufnahme roter Felsen."),
        ],
    )
    inv_path.write_text(analysis.model_dump_json(indent=2), encoding="utf-8")

    plan = DramaturgyPlan(
        project_id=project.id,
        recommended_folder_order=[
            DramaturgyFolderEntry(
                folder_name=folder, order_index=1, enabled=True,
                recommended_word_count=10, recommended_min_words=5, recommended_max_words=15,
            )
        ],
    )
    save_confirmed_dramaturgy(project, plan)
    save_folder_voiceover_settings(project, build_default_folder_voiceover_settings(project))
    return project


def _valid_author_response(text: str = "Zwischen den Felswänden scheint das Licht von innen zu leuchten heute.") -> str:
    return json.dumps(
        {
            "voiceover_text_full": text,
            "sentence_items": [
                {
                    "sentence_id": "sentence_001",
                    "text": text,
                    "primary_asset_id": "asset_clip1",
                    "backup_asset_ids": [],
                    "asset_confidence": 0.9,
                    "needs_supplement_asset": False,
                    "supplement_reason": "",
                    "source_inventory_asset_ids_considered": ["asset_clip1", "asset_clip2"],
                }
            ],
            "transition_from_previous_used": False,
            "callback_to_previous_used": False,
            "contrast_or_commonality_used": False,
            "risks": [],
        }
    )


def _generate_draft(project: Project, folder: str = "Grand Canyon") -> None:
    fake_response = PlanLlmResponse(
        provider="anthropic", model="claude-sonnet-5", raw_text=_valid_author_response()
    )
    with patch(f"{_AUTHOR_MODULE}.generate_plan_text_with_metadata", return_value=fake_response):
        generate_folder_voiceover(project, folder, provider="anthropic", model="claude-sonnet-5")


def _review_response(errors: list[dict]) -> PlanLlmResponse:
    return PlanLlmResponse(
        provider="anthropic", model="claude-sonnet-5", raw_text=json.dumps({"errors": errors})
    )


def _make_project_with_folders(tmp_path: Path, folders: list[str]) -> Project:
    """Mehrere aktivierte Ordner — Grundlage für die 'Alle X'-Sammel-Tests."""
    project_root = tmp_path / "USA"
    project_root.mkdir()
    for folder in folders:
        (project_root / folder).mkdir()
    project = Project(
        id="review-project-bulk",
        name="Review Bulk Test",
        project_root=str(project_root),
        work_dir=str(project_root / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=folders,
        selected_asset_subdirs=folders,
    )
    for folder in folders:
        inv_path = get_folder_inventory_path(project.work_dir_path, folder)
        inv_path.parent.mkdir(parents=True, exist_ok=True)
        analysis = AssetFolderAnalysis(
            folder=folder,
            assets=[
                AssetMediaAnalysis(path=f"{folder}/clip1.mp4", description=f"Weite Aufnahme von {folder}."),
                AssetMediaAnalysis(path=f"{folder}/clip2.mp4", description=f"Nahaufnahme in {folder}."),
            ],
        )
        inv_path.write_text(analysis.model_dump_json(indent=2), encoding="utf-8")

    plan = DramaturgyPlan(
        project_id=project.id,
        recommended_folder_order=[
            DramaturgyFolderEntry(
                folder_name=folder, order_index=index, enabled=True,
                recommended_word_count=10, recommended_min_words=5, recommended_max_words=15,
            )
            for index, folder in enumerate(folders, start=1)
        ],
    )
    save_confirmed_dramaturgy(project, plan)
    save_folder_voiceover_settings(project, build_default_folder_voiceover_settings(project))
    return project


def test_deterministic_check_flags_missing_transition_to_next(tmp_path: Path) -> None:
    """Nutzerfeedback: neue Spalte 'Übergang zum nächsten Kapitel' — wenn
    angefordert, aber vom Modell nicht verwendet, muss run_deterministic_checks
    das als WARNING melden (analog zu MISSING_TRANSITION für die rückwärtige
    Richtung)."""
    project = _make_project(tmp_path)
    setting = FolderVoiceoverSetting(
        folder_name="Grand Canyon",
        target_words=10,
        min_words=5,
        max_words=15,
        transition_to_next=True,
    )
    draft = FolderVoiceoverDraft(
        project_id=project.id,
        folder_name="Grand Canyon",
        voiceover_text_full="Ein Text ohne jeden Übergang.",
        word_count=5,
        transition_to_next_used=False,
    )
    errors = run_deterministic_checks(project, "Grand Canyon", draft, setting)
    matching = [error for error in errors if error.type == VO_ERROR_MISSING_TRANSITION_TO_NEXT]
    assert len(matching) == 1
    assert matching[0].severity == "WARNING"


def test_deterministic_check_passes_when_transition_to_next_used(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    setting = FolderVoiceoverSetting(
        folder_name="Grand Canyon",
        target_words=10,
        min_words=5,
        max_words=15,
        transition_to_next=True,
    )
    draft = FolderVoiceoverDraft(
        project_id=project.id,
        folder_name="Grand Canyon",
        voiceover_text_full="Ein Text mit einem Teaser auf den nächsten Ort.",
        word_count=8,
        transition_to_next_used=True,
    )
    errors = run_deterministic_checks(project, "Grand Canyon", draft, setting)
    assert not any(error.type == VO_ERROR_MISSING_TRANSITION_TO_NEXT for error in errors)


def test_deterministic_check_ignores_transition_to_next_when_not_requested(
    tmp_path: Path,
) -> None:
    project = _make_project(tmp_path)
    setting = FolderVoiceoverSetting(
        folder_name="Grand Canyon",
        target_words=10,
        min_words=5,
        max_words=15,
        transition_to_next=False,
    )
    draft = FolderVoiceoverDraft(
        project_id=project.id,
        folder_name="Grand Canyon",
        voiceover_text_full="Ein Text ohne jeden Übergang.",
        word_count=5,
        transition_to_next_used=False,
    )
    errors = run_deterministic_checks(project, "Grand Canyon", draft, setting)
    assert not any(error.type == VO_ERROR_MISSING_TRANSITION_TO_NEXT for error in errors)


def test_apply_corrected_voiceover_preserves_transition_to_next_used(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    original_draft = FolderVoiceoverDraft(
        project_id=project.id,
        folder_name="Grand Canyon",
        voiceover_text_full="Alter Text.",
        word_count=2,
        transition_to_next_used=False,
    )
    correction_response = _valid_author_response("Neuer Text mit Teaser auf den nächsten Ort.")
    payload = json.loads(correction_response)
    payload["transition_to_next_used"] = True
    updated = apply_corrected_voiceover(
        project,
        "Grand Canyon",
        original_draft,
        json.dumps(payload),
        correction_run_id="correction-run-1",
    )
    assert updated.transition_to_next_used is True


def test_review_loop_passes_with_no_errors(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _generate_draft(project)

    with patch(f"{_REVIEW_MODULE}.generate_plan_text_with_metadata", return_value=_review_response([])):
        report = run_folder_voiceover_review_loop(
            project, "Grand Canyon", provider="anthropic", model="claude-sonnet-5"
        )

    assert report.status == "PASS"
    assert report.attempt_count == 1


def test_review_loop_stops_after_three_attempts(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _generate_draft(project)

    blocker_error = [{"type": "TOO_GENERIC", "severity": "BLOCKER", "message": "Zu generisch."}]

    call_count = {"n": 0}

    def _side_effect(*, prompt: str, model: str):
        call_count["n"] += 1
        # Review-Calls liefern immer denselben Blocker-Fehler zurück (egal wie
        # oft correction versucht wird) -> Loop muss nach 3 Versuchen stoppen.
        if "issues that must be fixed" in prompt.lower():
            # Correction-Call: liefert einen (weiterhin unvollständigen) Draft zurück.
            return PlanLlmResponse(
                provider="anthropic", model="claude-sonnet-5", raw_text=_valid_author_response()
            )
        return _review_response(blocker_error)

    with patch(f"{_REVIEW_MODULE}.generate_plan_text_with_metadata", side_effect=_side_effect):
        report = run_folder_voiceover_review_loop(
            project, "Grand Canyon", provider="anthropic", model="claude-sonnet-5"
        )

    assert report.attempt_count == 3
    assert report.status == "NEEDS_USER_REVIEW"


def test_review_loop_generic_llm_exception_is_a_warning_not_a_crash(tmp_path: Path) -> None:
    """Ein unerwarteter LLM-/SDK-/Netzwerkfehler beim Review-Call darf die
    Streamlit-Seite nicht crashen. Er wird als nicht-blockierende Warnung
    (LLM_REVIEW_UNAVAILABLE) behandelt — deterministische Checks laufen
    unabhängig davon weiter."""
    project = _make_project(tmp_path)
    _generate_draft(project)

    with patch(
        f"{_REVIEW_MODULE}.generate_plan_text_with_metadata",
        side_effect=RuntimeError("Unerwarteter SDK-Fehler."),
    ):
        report = run_folder_voiceover_review_loop(
            project, "Grand Canyon", provider="anthropic", model="claude-sonnet-5"
        )

    assert report.status == "PASS"
    assert any(warning.type == "LLM_REVIEW_UNAVAILABLE" for warning in report.warnings)


def test_review_loop_sets_needs_user_review_after_three_failures(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _generate_draft(project)
    blocker_error = [{"type": "HALLUCINATED_FACT", "severity": "BLOCKER", "message": "Erfundene Tatsache."}]

    with patch(f"{_REVIEW_MODULE}.generate_plan_text_with_metadata", return_value=_review_response(blocker_error)):
        report = run_folder_voiceover_review_loop(
            project, "Grand Canyon", provider="anthropic", model="claude-sonnet-5"
        )

    assert report.status == "NEEDS_USER_REVIEW"
    document = load_folder_voiceovers_draft(project)
    draft = next(item for item in document.items if item.folder_name == "Grand Canyon")
    assert draft.status == "NEEDS_USER_REVIEW"


def test_pass_does_not_automatically_confirm(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _generate_draft(project)

    with patch(f"{_REVIEW_MODULE}.generate_plan_text_with_metadata", return_value=_review_response([])):
        run_folder_voiceover_review_loop(project, "Grand Canyon", provider="anthropic", model="claude-sonnet-5")

    confirmed_path = get_folder_voiceovers_confirmed_path(project.work_dir_path)
    assert not confirmed_path.is_file()
    confirmed_document = load_folder_voiceovers_confirmed(project)
    assert confirmed_document.items == []


def test_confirm_writes_confirmed_document(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _generate_draft(project)
    confirm_folder_voiceover(project, "Grand Canyon")

    path = get_folder_voiceovers_confirmed_path(project.work_dir_path)
    assert path.is_file()
    document = load_folder_voiceovers_confirmed(project)
    assert len(document.items) == 1
    assert document.items[0].folder_name == "Grand Canyon"
    assert document.items[0].status == "CONFIRMED"


def test_confirmed_document_contains_only_confirmed_folders(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _generate_draft(project)
    # Nicht bestätigen -> confirmed-Datei bleibt leer.
    document = load_folder_voiceovers_confirmed(project)
    assert document.items == []

    confirm_folder_voiceover(project, "Grand Canyon")
    document = load_folder_voiceovers_confirmed(project)
    assert {item.folder_name for item in document.items} == {"Grand Canyon"}


def test_unconfirm_removes_from_confirmed_document(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _generate_draft(project)
    confirm_folder_voiceover(project, "Grand Canyon")
    unconfirm_folder_voiceover(project, "Grand Canyon")

    document = load_folder_voiceovers_confirmed(project)
    assert document.items == []


def test_llm_traceability_writes_folder_voiceover_run(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    fake_response = PlanLlmResponse(
        provider="anthropic", model="claude-sonnet-5", raw_text=_valid_author_response()
    )
    with patch(f"{_AUTHOR_MODULE}.generate_plan_text_with_metadata", return_value=fake_response):
        result = generate_folder_voiceover(project, "Grand Canyon", provider="anthropic", model="claude-sonnet-5")

    run_dir = get_llm_runs_dir(project.language_work_dir_path) / result.llm_run_id
    manifest = json.loads((run_dir / "llm_request_manifest.json").read_text(encoding="utf-8"))
    assert manifest["stage"] == "folder_voiceover"


def test_llm_traceability_writes_voiceover_review_run(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _generate_draft(project)

    with patch(f"{_REVIEW_MODULE}.generate_plan_text_with_metadata", return_value=_review_response([])):
        report = run_folder_voiceover_review_loop(
            project, "Grand Canyon", provider="anthropic", model="claude-sonnet-5"
        )

    assert len(report.review_run_ids) == 1
    run_dir = get_llm_runs_dir(project.language_work_dir_path) / report.review_run_ids[0]
    manifest = json.loads((run_dir / "llm_request_manifest.json").read_text(encoding="utf-8"))
    assert manifest["stage"] == "voiceover_review"


def test_llm_traceability_writes_voiceover_correction_run(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _generate_draft(project)

    call_state = {"n": 0}

    def _side_effect(*, prompt: str, model: str):
        call_state["n"] += 1
        if call_state["n"] == 1:
            return _review_response([{"type": "TOO_GENERIC", "severity": "BLOCKER", "message": "x"}])
        if call_state["n"] == 2:
            return PlanLlmResponse(
                provider="anthropic", model="claude-sonnet-5", raw_text=_valid_author_response()
            )
        return _review_response([])

    with patch(f"{_REVIEW_MODULE}.generate_plan_text_with_metadata", side_effect=_side_effect):
        report = run_folder_voiceover_review_loop(
            project, "Grand Canyon", provider="anthropic", model="claude-sonnet-5"
        )

    assert report.status == "PASS"
    assert len(report.correction_run_ids) == 1
    run_dir = get_llm_runs_dir(project.language_work_dir_path) / report.correction_run_ids[0]
    manifest = json.loads((run_dir / "llm_request_manifest.json").read_text(encoding="utf-8"))
    assert manifest["stage"] == "voiceover_correction"


def test_unknown_llm_review_error_type_is_kept_as_warning(tmp_path: Path) -> None:
    """Hardening: ein unbekannter Fehlertyp vom Review-LLM darf nicht mehr
    stillschweigend verworfen werden — er wird als UNKNOWN_LLM_REVIEW_ERROR
    mit Severity WARNING gespeichert (kein Blocker, Loop kann trotzdem PASSen)."""
    project = _make_project(tmp_path)
    _generate_draft(project)

    unknown_error = [{"type": "SOME_MADE_UP_TYPE", "severity": "BLOCKER", "message": "x"}]
    with patch(f"{_REVIEW_MODULE}.generate_plan_text_with_metadata", return_value=_review_response(unknown_error)):
        report = run_folder_voiceover_review_loop(
            project, "Grand Canyon", provider="anthropic", model="claude-sonnet-5"
        )

    # Kein Silent Drop: der unbekannte Typ taucht in errors ODER warnings auf.
    all_seen_types = {error.type for error in report.errors} | {
        warning.type for warning in report.warnings
    }
    assert "UNKNOWN_LLM_REVIEW_ERROR" in all_seen_types
    # Da UNKNOWN_LLM_REVIEW_ERROR als WARNING (nicht BLOCKER) gespeichert wird,
    # blockiert es den PASS-Status nicht.
    assert report.status == "PASS"
    assert any("SOME_MADE_UP_TYPE" in warning.message for warning in report.warnings)


def test_validation_report_saved_to_disk(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _generate_draft(project)
    with patch(f"{_REVIEW_MODULE}.generate_plan_text_with_metadata", return_value=_review_response([])):
        run_folder_voiceover_review_loop(project, "Grand Canyon", provider="anthropic", model="claude-sonnet-5")

    path = get_folder_voiceover_validation_report_path(project.work_dir_path)
    assert path.is_file()
    reports = load_validation_reports(project)
    assert "Grand Canyon" in reports.reports


# --- Nutzerfeedback: 'Alle X'-Sammel-Aktionen unterhalb der Drafts-Liste
# (validate_all_folder_voiceovers, confirm_all_folder_voiceovers,
# unconfirm_all_folder_voiceovers) ---


def test_validate_all_folder_voiceovers_processes_only_folders_with_drafts(
    tmp_path: Path,
) -> None:
    folders = ["Grand Canyon", "Yellowstone", "Zion"]
    project = _make_project_with_folders(tmp_path, folders)
    # Nur zwei von drei Ordnern haben einen Entwurf.
    _generate_draft(project, "Grand Canyon")
    _generate_draft(project, "Yellowstone")

    with patch(f"{_REVIEW_MODULE}.generate_plan_text_with_metadata", return_value=_review_response([])):
        reports = validate_all_folder_voiceovers(
            project, provider="anthropic", model="claude-sonnet-5"
        )

    assert {report.folder_name for report in reports} == {"Grand Canyon", "Yellowstone"}
    assert all(report.status == "PASS" for report in reports)


def test_validate_all_folder_voiceovers_reports_progress(tmp_path: Path) -> None:
    folders = ["Grand Canyon", "Yellowstone"]
    project = _make_project_with_folders(tmp_path, folders)
    for folder in folders:
        _generate_draft(project, folder)

    progress_calls = []
    with patch(f"{_REVIEW_MODULE}.generate_plan_text_with_metadata", return_value=_review_response([])):
        validate_all_folder_voiceovers(
            project,
            provider="anthropic",
            model="claude-sonnet-5",
            progress_callback=lambda folder, index, total: progress_calls.append((folder, index, total)),
        )

    assert progress_calls == [("Grand Canyon", 1, 2), ("Yellowstone", 2, 2)]


def test_validate_all_folder_voiceovers_raises_without_confirmed_dramaturgy(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "USA"
    project_root.mkdir()
    project = Project(
        id="no-dramaturgy",
        name="No Dramaturgy",
        project_root=str(project_root),
        work_dir=str(project_root / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=[],
        selected_asset_subdirs=[],
    )
    with pytest.raises(ValueError):
        validate_all_folder_voiceovers(project, provider="anthropic", model="claude-sonnet-5")


def test_confirm_all_folder_voiceovers_confirms_without_requiring_pass_status(
    tmp_path: Path,
) -> None:
    """Nutzerfeedback: 'ich will auch ohne vorherige Validierung alle
    bestätigen können' — identisches Verhalten wie die einzelne
    'Bestätigen'-Schaltfläche pro Ordner (keine PASS-Pflicht)."""
    folders = ["Grand Canyon", "Yellowstone"]
    project = _make_project_with_folders(tmp_path, folders)
    for folder in folders:
        _generate_draft(project, folder)
    # Explizit KEINE Validierung durchgeführt.

    results = confirm_all_folder_voiceovers(project)

    assert {draft.folder_name for draft in results} == set(folders)
    confirmed = load_folder_voiceovers_confirmed(project)
    assert {item.folder_name for item in confirmed.items} == set(folders)


def test_confirm_all_folder_voiceovers_skips_folders_without_draft(tmp_path: Path) -> None:
    folders = ["Grand Canyon", "Yellowstone"]
    project = _make_project_with_folders(tmp_path, folders)
    _generate_draft(project, "Grand Canyon")  # Yellowstone bleibt ohne Entwurf.

    results = confirm_all_folder_voiceovers(project)

    assert [draft.folder_name for draft in results] == ["Grand Canyon"]


def test_confirm_all_folder_voiceovers_reports_progress(tmp_path: Path) -> None:
    folders = ["Grand Canyon", "Yellowstone"]
    project = _make_project_with_folders(tmp_path, folders)
    for folder in folders:
        _generate_draft(project, folder)

    progress_calls = []
    confirm_all_folder_voiceovers(
        project,
        progress_callback=lambda folder, index, total: progress_calls.append((folder, index, total)),
    )
    assert progress_calls == [("Grand Canyon", 1, 2), ("Yellowstone", 2, 2)]


def test_unconfirm_all_folder_voiceovers_reverts_only_confirmed_folders(
    tmp_path: Path,
) -> None:
    folders = ["Grand Canyon", "Yellowstone", "Zion"]
    project = _make_project_with_folders(tmp_path, folders)
    for folder in folders:
        _generate_draft(project, folder)
    confirm_folder_voiceover(project, "Grand Canyon")
    confirm_folder_voiceover(project, "Yellowstone")
    # Zion bleibt unbestätigt.

    results = unconfirm_all_folder_voiceovers(project)

    assert {draft.folder_name for draft in results} == {"Grand Canyon", "Yellowstone"}
    confirmed = load_folder_voiceovers_confirmed(project)
    assert confirmed.items == []


def test_unconfirm_all_folder_voiceovers_noop_when_nothing_confirmed(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _generate_draft(project)
    results = unconfirm_all_folder_voiceovers(project)
    assert results == []
