"""Nutzervorgabe (Juli 2026): eigenständiger Correction-Loop für die
Asset-Allokations-Diagnose (siehe folder_asset_allocation_correction_service.py)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis
from otio_app.defaults import MAX_ASSET_ALLOCATION_CORRECTION_ATTEMPTS
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import get_folder_inventory_path
from otio_app.services.plan_llm_client import PlanLlmResponse
from otio_app.services.voiceover_generation.dramaturgy_service import save_confirmed_dramaturgy
from otio_app.services.voiceover_generation.folder_asset_allocation_correction_service import (
    ASSET_ALLOCATION_CORRECTION_STATUS_FAILED,
    ASSET_ALLOCATION_CORRECTION_STATUS_NEEDS_USER_REVIEW,
    ASSET_ALLOCATION_CORRECTION_STATUS_PASS,
    apply_asset_allocation_correction,
    run_asset_allocation_correction,
)
from otio_app.services.voiceover_generation.folder_voiceover_settings_service import (
    build_default_folder_voiceover_settings,
    save_folder_voiceover_settings,
)
from otio_app.services.voiceover_generation.models import (
    ClosingVisualPlan,
    DramaturgyFolderEntry,
    DramaturgyPlan,
    FolderVoiceoverDraft,
    SentenceItem,
)
from otio_app.services.voiceover_generation.voiceover_author_service import (
    get_folder_voiceover_draft,
    upsert_folder_voiceover_draft_item,
)

_SERVICE_MODULE = "otio_app.services.voiceover_generation.folder_asset_allocation_correction_service"

FOLDER_A = "Grand Canyon"


def _make_project(tmp_path: Path) -> Project:
    project_root = tmp_path / "USA"
    (project_root / FOLDER_A).mkdir(parents=True)
    project = Project(
        id="allocation-correction-project",
        name="Allocation Correction Test",
        project_root=str(project_root),
        work_dir=str(project_root / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=[FOLDER_A],
        selected_asset_subdirs=[FOLDER_A],
    )
    inv_path = get_folder_inventory_path(project.work_dir_path, FOLDER_A)
    inv_path.parent.mkdir(parents=True, exist_ok=True)
    analysis = AssetFolderAnalysis(
        folder=FOLDER_A,
        assets=[
            AssetMediaAnalysis(path=f"{FOLDER_A}/clip1.mp4", description="Weite Aufnahme."),
            AssetMediaAnalysis(path=f"{FOLDER_A}/clip2.mp4", description="Luftaufnahme."),
            AssetMediaAnalysis(path=f"{FOLDER_A}/clip3.mp4", description="Nahaufnahme."),
        ],
    )
    inv_path.write_text(analysis.model_dump_json(indent=2), encoding="utf-8")

    plan = DramaturgyPlan(
        project_id=project.id,
        recommended_folder_order=[DramaturgyFolderEntry(folder_name=FOLDER_A, order_index=1, enabled=True)],
    )
    save_confirmed_dramaturgy(project, plan)
    save_folder_voiceover_settings(project, build_default_folder_voiceover_settings(project))
    return project


def _seed_draft(
    project: Project, *, sentence_items: list[SentenceItem], closing_visual_plan: ClosingVisualPlan | None = None
) -> FolderVoiceoverDraft:
    draft = FolderVoiceoverDraft(
        project_id=project.id,
        folder_name=FOLDER_A,
        voiceover_text_full="Ein Text.",
        word_count=2,
        sentence_items=sentence_items,
        closing_visual_plan=closing_visual_plan or ClosingVisualPlan(),
    )
    upsert_folder_voiceover_draft_item(project, draft)
    return draft


def _fake_response(raw_text: str) -> PlanLlmResponse:
    return PlanLlmResponse(provider="anthropic", model="claude-sonnet-5", raw_text=raw_text)


def _correction_response(*, closing_asset_id: str, needs_supplement: bool = False) -> str:
    return json.dumps(
        {
            "voiceover_text_full": "Ein Text.",
            "sentence_items": [
                {
                    "sentence_id": "sentence_001",
                    "text": "Erster Satz.",
                    "primary_asset_id": "asset_clip1",
                    "backup_asset_ids": [],
                },
                {
                    "sentence_id": "sentence_002",
                    "text": "Letzter Satz.",
                    "primary_asset_id": "asset_clip2",
                    "backup_asset_ids": [],
                },
            ],
            "closing_visual_plan": {
                "visual_intent": "aerial establishing",
                "primary_asset_id": closing_asset_id,
                "backup_asset_ids": [],
                "needs_supplement_asset": needs_supplement,
                "supplement_reason": "Kein passendes Motiv." if needs_supplement else "",
            },
        }
    )


# --- run_asset_allocation_correction: happy paths ---


def test_returns_pass_immediately_when_no_issues_and_never_calls_llm(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _seed_draft(
        project,
        sentence_items=[
            SentenceItem(sentence_id="sentence_001", text="Erster Satz.", primary_asset_id="asset_clip1"),
            SentenceItem(sentence_id="sentence_002", text="Letzter Satz.", primary_asset_id="asset_clip2"),
        ],
        closing_visual_plan=ClosingVisualPlan(primary_asset_id="asset_clip3"),
    )
    with patch(f"{_SERVICE_MODULE}.generate_plan_text_with_metadata") as mock_llm:
        result = run_asset_allocation_correction(
            project, FOLDER_A, provider="anthropic", model="claude-sonnet-5"
        )
    mock_llm.assert_not_called()
    assert result.status == ASSET_ALLOCATION_CORRECTION_STATUS_PASS
    assert result.attempt_count == 0


def test_fixes_missing_closing_shot_in_one_attempt(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _seed_draft(
        project,
        sentence_items=[
            SentenceItem(sentence_id="sentence_001", text="Erster Satz.", primary_asset_id="asset_clip1"),
            SentenceItem(sentence_id="sentence_002", text="Letzter Satz.", primary_asset_id="asset_clip2"),
        ],
    )
    fake_response = _fake_response(_correction_response(closing_asset_id="asset_clip3"))
    with patch(f"{_SERVICE_MODULE}.generate_plan_text_with_metadata", return_value=fake_response):
        result = run_asset_allocation_correction(
            project, FOLDER_A, provider="anthropic", model="claude-sonnet-5"
        )

    assert result.status == ASSET_ALLOCATION_CORRECTION_STATUS_PASS
    assert result.attempt_count == 1
    assert result.draft.closing_visual_plan.primary_asset_id == "asset_clip3"
    assert len(result.correction_run_ids) == 1

    persisted = get_folder_voiceover_draft(project, FOLDER_A)
    assert persisted.closing_visual_plan.primary_asset_id == "asset_clip3"
    assert persisted.correction_run_ids == result.correction_run_ids


def test_fixes_missing_closing_shot_via_supplement_when_llm_requests_it(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _seed_draft(
        project,
        sentence_items=[
            SentenceItem(sentence_id="sentence_001", text="Erster Satz.", primary_asset_id="asset_clip1"),
            SentenceItem(sentence_id="sentence_002", text="Letzter Satz.", primary_asset_id="asset_clip2"),
        ],
    )
    fake_response = _fake_response(_correction_response(closing_asset_id="", needs_supplement=True))
    with patch(f"{_SERVICE_MODULE}.generate_plan_text_with_metadata", return_value=fake_response):
        result = run_asset_allocation_correction(
            project, FOLDER_A, provider="anthropic", model="claude-sonnet-5"
        )

    assert result.status == ASSET_ALLOCATION_CORRECTION_STATUS_PASS
    assert result.draft.closing_visual_plan.needs_supplement_asset is True
    assert result.draft.closing_visual_plan.supplement_reason


# --- run_asset_allocation_correction: exhausted attempts ---


def test_needs_user_review_after_exhausting_attempts_without_fix(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _seed_draft(
        project,
        sentence_items=[
            SentenceItem(sentence_id="sentence_001", text="Erster Satz.", primary_asset_id="asset_clip1"),
            SentenceItem(sentence_id="sentence_002", text="Letzter Satz.", primary_asset_id="asset_clip2"),
        ],
    )
    # Antwort ohne closing_visual_plan -> Closing bleibt fehlend, Issue bleibt bestehen.
    unhelpful_response = _fake_response(
        json.dumps(
            {
                "voiceover_text_full": "Ein Text.",
                "sentence_items": [
                    {"sentence_id": "sentence_001", "text": "Erster Satz.", "primary_asset_id": "asset_clip1"},
                    {"sentence_id": "sentence_002", "text": "Letzter Satz.", "primary_asset_id": "asset_clip2"},
                ],
            }
        )
    )
    with patch(f"{_SERVICE_MODULE}.generate_plan_text_with_metadata", return_value=unhelpful_response):
        result = run_asset_allocation_correction(
            project, FOLDER_A, provider="anthropic", model="claude-sonnet-5"
        )

    assert result.status == ASSET_ALLOCATION_CORRECTION_STATUS_NEEDS_USER_REVIEW
    assert result.attempt_count == MAX_ASSET_ALLOCATION_CORRECTION_ATTEMPTS
    assert len(result.correction_run_ids) == MAX_ASSET_ALLOCATION_CORRECTION_ATTEMPTS
    assert result.remaining_issues


# --- run_asset_allocation_correction: failure handling ---


def test_returns_failed_on_llm_exception_and_keeps_prior_draft(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    original = _seed_draft(
        project,
        sentence_items=[
            SentenceItem(sentence_id="sentence_001", text="Erster Satz.", primary_asset_id="asset_clip1"),
            SentenceItem(sentence_id="sentence_002", text="Letzter Satz.", primary_asset_id="asset_clip2"),
        ],
    )
    with patch(f"{_SERVICE_MODULE}.generate_plan_text_with_metadata", side_effect=RuntimeError("boom")):
        result = run_asset_allocation_correction(
            project, FOLDER_A, provider="anthropic", model="claude-sonnet-5"
        )

    assert result.status == ASSET_ALLOCATION_CORRECTION_STATUS_FAILED
    assert "boom" in result.error
    assert result.draft.closing_visual_plan == original.closing_visual_plan


def test_returns_failed_on_invalid_json_and_keeps_prior_draft(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    original = _seed_draft(
        project,
        sentence_items=[
            SentenceItem(sentence_id="sentence_001", text="Erster Satz.", primary_asset_id="asset_clip1"),
            SentenceItem(sentence_id="sentence_002", text="Letzter Satz.", primary_asset_id="asset_clip2"),
        ],
    )
    with patch(
        f"{_SERVICE_MODULE}.generate_plan_text_with_metadata",
        return_value=_fake_response("not valid json"),
    ):
        result = run_asset_allocation_correction(
            project, FOLDER_A, provider="anthropic", model="claude-sonnet-5"
        )

    assert result.status == ASSET_ALLOCATION_CORRECTION_STATUS_FAILED
    assert result.draft.closing_visual_plan == original.closing_visual_plan


def test_raises_when_no_draft_exists(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    with pytest.raises(ValueError, match="Kein Voice-over-Entwurf"):
        run_asset_allocation_correction(project, FOLDER_A, provider="anthropic", model="claude-sonnet-5")


# --- apply_asset_allocation_correction (pure parse/sanitize/persist step) ---


def test_apply_correction_sanitizes_hallucinated_closing_asset_id(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    draft = _seed_draft(
        project,
        sentence_items=[
            SentenceItem(sentence_id="sentence_001", text="Erster Satz.", primary_asset_id="asset_clip1"),
        ],
    )
    raw_text = json.dumps(
        {
            "voiceover_text_full": "Ein Text.",
            "sentence_items": [
                {"sentence_id": "sentence_001", "text": "Erster Satz.", "primary_asset_id": "asset_clip1"},
            ],
            "closing_visual_plan": {"primary_asset_id": "asset_made_up"},
        }
    )
    updated = apply_asset_allocation_correction(
        project, FOLDER_A, draft, raw_text, correction_run_id="run_1"
    )
    assert updated.closing_visual_plan.primary_asset_id == ""
    assert updated.correction_run_ids == ["run_1"]


def test_apply_correction_appends_to_existing_correction_run_ids(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    draft = _seed_draft(
        project,
        sentence_items=[
            SentenceItem(sentence_id="sentence_001", text="Erster Satz.", primary_asset_id="asset_clip1"),
        ],
    )
    draft = draft.model_copy(update={"correction_run_ids": ["run_0"]})
    raw_text = json.dumps(
        {
            "voiceover_text_full": "Ein Text.",
            "sentence_items": [
                {"sentence_id": "sentence_001", "text": "Erster Satz.", "primary_asset_id": "asset_clip1"},
            ],
            "closing_visual_plan": {"primary_asset_id": "asset_clip3"},
        }
    )
    updated = apply_asset_allocation_correction(
        project, FOLDER_A, draft, raw_text, correction_run_id="run_1"
    )
    assert updated.correction_run_ids == ["run_0", "run_1"]
