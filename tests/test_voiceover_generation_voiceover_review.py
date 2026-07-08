"""Phase 4: Review-/Correction-Loop, Confirm/Unconfirm, LLM-Traceability."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

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
from otio_app.services.voiceover_generation.models import DramaturgyFolderEntry, DramaturgyPlan
from otio_app.services.voiceover_generation.voiceover_author_service import (
    generate_folder_voiceover,
    load_folder_voiceovers_confirmed,
    load_folder_voiceovers_draft,
)
from otio_app.services.voiceover_generation.voiceover_review_service import (
    confirm_folder_voiceover,
    load_validation_reports,
    run_folder_voiceover_review_loop,
    unconfirm_folder_voiceover,
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

    run_dir = get_llm_runs_dir(project.work_dir_path) / result.llm_run_id
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
    run_dir = get_llm_runs_dir(project.work_dir_path) / report.review_run_ids[0]
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
    run_dir = get_llm_runs_dir(project.work_dir_path) / report.correction_run_ids[0]
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
