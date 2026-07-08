"""Phase 3: Dramaturgieplanung — Service-Tests inkl. Traceability und Confirm-Flow."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import (
    get_dramaturgy_plan_confirmed_path,
    get_dramaturgy_plan_draft_path,
    get_folder_inventory_path,
    get_llm_runs_dir,
)
from otio_app.services.plan_llm_client import PlanLlmNotConfiguredError, PlanLlmResponse
from otio_app.services.voiceover_generation.dramaturgy_service import (
    build_dramaturgy_plan,
    confirm_dramaturgy_plan,
    load_confirmed_dramaturgy,
    load_dramaturgy_draft,
    save_dramaturgy_draft,
    update_dramaturgy_order,
)
from otio_app.services.voiceover_generation.llm_trace_service import (
    STATUS_FAIL,
    STATUS_PARSE_FAILED,
    STATUS_PASS,
)
from otio_app.services.voiceover_generation.models import DramaturgyFolderEntry, DramaturgyPlan

_SERVICE_MODULE = "otio_app.services.voiceover_generation.dramaturgy_service"


def _make_project(tmp_path: Path, folders: list[str]) -> Project:
    project_root = tmp_path / "USA"
    project_root.mkdir()
    for folder in folders:
        (project_root / folder).mkdir()
    project = Project(
        id="dram-service-project",
        name="Dramaturgy Service Test",
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
            assets=[
                AssetMediaAnalysis(
                    path=f"{folder}/clip1.mp4", description=f"Eindrucksvolle Aufnahme von {folder}."
                ),
                AssetMediaAnalysis(
                    path=f"{folder}/clip2.mp4", description=f"Weiterer Blick auf {folder} bei Tag."
                ),
            ],
        )
        path.write_text(analysis.model_dump_json(indent=2), encoding="utf-8")
    return project


VALID_DRAMATURGY_RESPONSE = json.dumps(
    {
        "project_title": "Wunder der Wüste",
        "core_promise": "Eine Reise durch die eindrucksvollsten Naturwunder.",
        "narrative_arc": "Von ruhig zu überwältigend.",
        "global_transition_strategy": "Kontraste zwischen Weite und Detail nutzen.",
        "recommended_folder_order": [
            {
                "folder_name": "Grand Canyon",
                "order_index": 2,
                "enabled": True,
                "dramaturgy_role": "climax",
                "reason": "Stärkstes visuelles Material.",
                "visual_strength_score": 0.9,
                "asset_diversity_score": 0.8,
                "hook_potential_score": 0.85,
                "recommended_word_count": 140,
                "recommended_min_words": 126,
                "recommended_max_words": 154,
                "transition_goal_to_next": "Ruhiger Ausklang danach.",
                "transition_from_previous_hint": "",
                "contrast_or_commonality_hint": "",
                "risks": [],
            },
            {
                "folder_name": "Yellowstone",
                "order_index": 1,
                "enabled": True,
                "dramaturgy_role": "opener",
                "reason": "Guter Einstieg mit offener Frage.",
                "visual_strength_score": 0.6,
                "asset_diversity_score": 0.5,
                "hook_potential_score": 0.7,
                "recommended_word_count": 90,
                "recommended_min_words": 80,
                "recommended_max_words": 100,
                "transition_goal_to_next": "Steigerung zum Grand Canyon.",
                "transition_from_previous_hint": "",
                "contrast_or_commonality_hint": "",
                "risks": [],
            },
        ],
        "risks": [],
    }
)


def _fake_response(raw_text: str = VALID_DRAMATURGY_RESPONSE) -> PlanLlmResponse:
    return PlanLlmResponse(provider="anthropic", model="claude-sonnet-5", raw_text=raw_text)


def test_build_dramaturgy_plan_writes_draft(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["Grand Canyon", "Yellowstone"])
    with patch(f"{_SERVICE_MODULE}.generate_plan_text_with_metadata", return_value=_fake_response()):
        result = build_dramaturgy_plan(project, provider="anthropic", model="claude-sonnet-5")

    assert result.status == STATUS_PASS
    assert result.plan is not None
    path = get_dramaturgy_plan_draft_path(project.work_dir_path)
    assert path.is_file()

    loaded = load_dramaturgy_draft(project)
    assert loaded is not None
    assert loaded.project_title == "Wunder der Wüste"
    assert {entry.folder_name for entry in loaded.recommended_folder_order} == {
        "Grand Canyon",
        "Yellowstone",
    }


def test_build_dramaturgy_plan_draft_contains_llm_run_id(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["Grand Canyon", "Yellowstone"])
    with patch(f"{_SERVICE_MODULE}.generate_plan_text_with_metadata", return_value=_fake_response()):
        result = build_dramaturgy_plan(project, provider="anthropic", model="claude-sonnet-5")

    assert result.plan.llm_run_id == result.llm_run_id
    run_dir = get_llm_runs_dir(project.work_dir_path) / result.llm_run_id
    assert (run_dir / "prompt.txt").is_file()
    assert (run_dir / "raw_llm_response.json").is_file()
    assert (run_dir / "parsed_llm_response.json").is_file()
    assert (run_dir / "llm_request_manifest.json").is_file()


def test_build_dramaturgy_plan_filters_hallucinated_folder_names(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["Grand Canyon"])
    response_with_extra_folder = json.dumps(
        {
            "project_title": "Test",
            "recommended_folder_order": [
                {"folder_name": "Grand Canyon", "order_index": 1},
                {"folder_name": "Nonexistent Folder", "order_index": 2},
            ],
        }
    )
    with patch(
        f"{_SERVICE_MODULE}.generate_plan_text_with_metadata",
        return_value=_fake_response(response_with_extra_folder),
    ):
        result = build_dramaturgy_plan(project, provider="anthropic", model="claude-sonnet-5")

    assert result.status == STATUS_PASS
    folder_names = {entry.folder_name for entry in result.plan.recommended_folder_order}
    assert folder_names == {"Grand Canyon"}


def test_build_dramaturgy_plan_invalid_json_does_not_overwrite_existing_draft(
    tmp_path: Path,
) -> None:
    project = _make_project(tmp_path, ["Grand Canyon", "Yellowstone"])
    existing_draft = save_dramaturgy_draft(
        project,
        DramaturgyPlan(project_id=project.id, project_title="ORIGINAL DRAFT"),
    )

    with patch(
        f"{_SERVICE_MODULE}.generate_plan_text_with_metadata",
        return_value=_fake_response("not valid json {{"),
    ):
        result = build_dramaturgy_plan(project, provider="anthropic", model="claude-sonnet-5")

    assert result.status == STATUS_PARSE_FAILED
    assert result.plan is None

    reloaded = load_dramaturgy_draft(project)
    assert reloaded is not None
    assert reloaded.project_title == "ORIGINAL DRAFT"
    assert reloaded.generated_at == existing_draft.generated_at


def test_build_dramaturgy_plan_missing_api_key_returns_fail(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["Grand Canyon"])
    with patch(
        f"{_SERVICE_MODULE}.generate_plan_text_with_metadata",
        side_effect=PlanLlmNotConfiguredError("ANTHROPIC_API_KEY ist nicht gesetzt."),
    ):
        result = build_dramaturgy_plan(project, provider="anthropic", model="claude-sonnet-5")

    assert result.status == STATUS_FAIL
    assert result.plan is None
    assert load_dramaturgy_draft(project) is None


def test_confirm_dramaturgy_plan_writes_confirmed_file(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["Grand Canyon", "Yellowstone"])
    draft = DramaturgyPlan(
        project_id=project.id,
        recommended_folder_order=[
            DramaturgyFolderEntry(folder_name="Grand Canyon", order_index=1, enabled=True),
            DramaturgyFolderEntry(folder_name="Yellowstone", order_index=2, enabled=True),
        ],
    )
    confirmed = confirm_dramaturgy_plan(project, draft)

    assert confirmed.status == "CONFIRMED"
    assert confirmed.confirmed_at is not None
    path = get_dramaturgy_plan_confirmed_path(project.work_dir_path)
    assert path.is_file()

    loaded = load_confirmed_dramaturgy(project)
    assert loaded is not None
    assert loaded.status == "CONFIRMED"


def test_confirm_normalizes_order_index_to_1_n(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["A", "B", "C"])
    draft = DramaturgyPlan(
        project_id=project.id,
        recommended_folder_order=[
            DramaturgyFolderEntry(folder_name="A", order_index=10, enabled=True),
            DramaturgyFolderEntry(folder_name="B", order_index=5, enabled=True),
            DramaturgyFolderEntry(folder_name="C", order_index=99, enabled=True),
        ],
    )
    confirmed = confirm_dramaturgy_plan(project, draft)
    order_indices = sorted(entry.order_index for entry in confirmed.recommended_folder_order)
    assert order_indices == [1, 2, 3]
    # Relative Reihenfolge (nach ursprünglichem order_index) bleibt erhalten.
    ordered_names = [
        entry.folder_name
        for entry in sorted(confirmed.recommended_folder_order, key=lambda e: e.order_index)
    ]
    assert ordered_names == ["B", "A", "C"]


def test_confirm_keeps_disabled_folders_but_marks_them_disabled(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["A", "B"])
    draft = DramaturgyPlan(
        project_id=project.id,
        recommended_folder_order=[
            DramaturgyFolderEntry(folder_name="A", order_index=1, enabled=True),
            DramaturgyFolderEntry(folder_name="B", order_index=2, enabled=False),
        ],
    )
    confirmed = confirm_dramaturgy_plan(project, draft)
    disabled = [e for e in confirmed.recommended_folder_order if not e.enabled]
    enabled = [e for e in confirmed.recommended_folder_order if e.enabled]
    assert len(disabled) == 1
    assert disabled[0].folder_name == "B"
    assert len(enabled) == 1
    assert enabled[0].folder_name == "A"
    # order_index bleibt eindeutig und ab 1 sortierbar für ALLE Einträge.
    all_indices = sorted(e.order_index for e in confirmed.recommended_folder_order)
    assert all_indices == [1, 2]


def test_replanning_does_not_overwrite_confirmed_plan(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["Grand Canyon", "Yellowstone"])
    original_confirmed = confirm_dramaturgy_plan(
        project,
        DramaturgyPlan(
            project_id=project.id,
            project_title="CONFIRMED ORIGINAL",
            recommended_folder_order=[
                DramaturgyFolderEntry(folder_name="Grand Canyon", order_index=1, enabled=True),
            ],
        ),
    )

    with patch(f"{_SERVICE_MODULE}.generate_plan_text_with_metadata", return_value=_fake_response()):
        result = build_dramaturgy_plan(project, provider="anthropic", model="claude-sonnet-5")

    assert result.status == STATUS_PASS
    # Neuer Draft wurde geschrieben...
    new_draft = load_dramaturgy_draft(project)
    assert new_draft.project_title == "Wunder der Wüste"
    # ...aber der bestätigte Plan bleibt unverändert.
    still_confirmed = load_confirmed_dramaturgy(project)
    assert still_confirmed.project_title == "CONFIRMED ORIGINAL"
    assert still_confirmed.confirmed_at == original_confirmed.confirmed_at


def test_confirmed_plan_can_be_explicitly_replaced(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["Grand Canyon", "Yellowstone"])
    confirm_dramaturgy_plan(
        project,
        DramaturgyPlan(project_id=project.id, project_title="OLD CONFIRMED"),
    )
    new_draft = DramaturgyPlan(project_id=project.id, project_title="NEW CONFIRMED")
    replaced = confirm_dramaturgy_plan(project, new_draft)

    assert replaced.project_title == "NEW CONFIRMED"
    loaded = load_confirmed_dramaturgy(project)
    assert loaded.project_title == "NEW CONFIRMED"


def test_update_dramaturgy_order_applies_edits_to_draft(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["A", "B"])
    save_dramaturgy_draft(
        project,
        DramaturgyPlan(
            project_id=project.id,
            recommended_folder_order=[
                DramaturgyFolderEntry(folder_name="A", order_index=1, enabled=True, dramaturgy_role="opener"),
                DramaturgyFolderEntry(folder_name="B", order_index=2, enabled=True, dramaturgy_role="setup"),
            ],
        ),
    )
    edited_rows = [
        {"folder_name": "A", "order_index": 2, "enabled": False, "dramaturgy_role": "resolution"},
        {"folder_name": "B", "order_index": 1, "enabled": True, "dramaturgy_role": "opener"},
    ]
    updated = update_dramaturgy_order(project, edited_rows)

    entries_by_name = {entry.folder_name: entry for entry in updated.recommended_folder_order}
    assert entries_by_name["A"].order_index == 2
    assert entries_by_name["A"].enabled is False
    assert entries_by_name["A"].dramaturgy_role == "resolution"
    assert entries_by_name["B"].order_index == 1
    assert updated.status == "DRAFT"

    # Wurde tatsächlich auf Disk gespeichert.
    reloaded = load_dramaturgy_draft(project)
    assert reloaded.recommended_folder_order == updated.recommended_folder_order


def test_update_dramaturgy_order_raises_without_existing_draft(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["A"])
    with pytest.raises(ValueError):
        update_dramaturgy_order(project, [{"folder_name": "A", "order_index": 1}])


def test_load_dramaturgy_draft_returns_none_when_missing(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["A"])
    assert load_dramaturgy_draft(project) is None


def test_load_confirmed_dramaturgy_returns_none_when_missing(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["A"])
    assert load_confirmed_dramaturgy(project) is None


def test_dramaturgy_writes_no_edit_plan_documents(tmp_path: Path) -> None:
    """Dramaturgie darf keine EditPlanDocuments schreiben."""
    project = _make_project(tmp_path, ["Grand Canyon", "Yellowstone"])
    with patch(f"{_SERVICE_MODULE}.generate_plan_text_with_metadata", return_value=_fake_response()):
        result = build_dramaturgy_plan(project, provider="anthropic", model="claude-sonnet-5")
    confirm_dramaturgy_plan(project, result.plan)

    assert not (project.work_dir_path / "edit_plan").exists()
    assert not (project.work_dir_path / "exports").exists()
