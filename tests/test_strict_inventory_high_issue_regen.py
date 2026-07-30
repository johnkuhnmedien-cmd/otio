"""Bulk: Ordner mit ≥4 Asset-Readiness-Issues → strict_inventory + Regen + Allokation."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis
from otio_app.defaults import (
    FACTUALITY_MODE_NORMAL_SAFE_GENERAL_KNOWLEDGE,
    FACTUALITY_MODE_STRICT_INVENTORY_ONLY,
    FOLDER_ASSET_READINESS_HIGH_ISSUE_REGEN_THRESHOLD,
)
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import get_folder_inventory_path
from otio_app.services.plan_llm_client import PlanLlmResponse
from otio_app.services.voiceover_generation.dramaturgy_service import save_confirmed_dramaturgy
from otio_app.services.voiceover_generation.folder_asset_readiness import (
    FolderAssetReadinessReport,
    SentenceAssetReadinessIssue,
)
from otio_app.services.voiceover_generation.folder_voiceover_settings_service import (
    apply_strict_inventory_factuality_to_folders,
    build_default_folder_voiceover_settings,
    load_folder_voiceover_settings,
    save_folder_voiceover_settings,
)
from otio_app.services.voiceover_generation.llm_trace_service import STATUS_PASS
from otio_app.services.voiceover_generation.models import (
    ClosingVisualPlan,
    DramaturgyFolderEntry,
    DramaturgyPlan,
    FolderVoiceoverDraft,
    SentenceItem,
)
from otio_app.services.voiceover_generation.voiceover_author_service import (
    regenerate_high_issue_folders_with_strict_inventory,
    upsert_folder_voiceover_draft_item,
)

FOLDER_A = "Grand Canyon"
FOLDER_B = "Yellowstone"
_AUTHOR = "otio_app.services.voiceover_generation.voiceover_author_service"
_ALLOC = "otio_app.services.voiceover_generation.folder_asset_allocation_correction_service"


def _make_project(tmp_path: Path) -> Project:
    project_root = tmp_path / "USA"
    for folder in (FOLDER_A, FOLDER_B):
        (project_root / folder).mkdir(parents=True)
    project = Project(
        id="strict-high-issue-project",
        name="Strict High Issue",
        project_root=str(project_root),
        work_dir=str(project_root / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=[FOLDER_A, FOLDER_B],
        selected_asset_subdirs=[FOLDER_A, FOLDER_B],
    )
    for folder in (FOLDER_A, FOLDER_B):
        inv = get_folder_inventory_path(project.work_dir_path, folder)
        inv.parent.mkdir(parents=True, exist_ok=True)
        analysis = AssetFolderAnalysis(
            folder=folder,
            assets=[
                AssetMediaAnalysis(path=f"{folder}/clip1.mp4", description="Weite."),
                AssetMediaAnalysis(path=f"{folder}/clip2.mp4", description="Luft."),
                AssetMediaAnalysis(path=f"{folder}/clip3.mp4", description="Detail."),
            ],
        )
        inv.write_text(analysis.model_dump_json(indent=2), encoding="utf-8")
    save_confirmed_dramaturgy(
        project,
        DramaturgyPlan(
            project_id=project.id,
            recommended_folder_order=[
                DramaturgyFolderEntry(folder_name=FOLDER_A, order_index=1, enabled=True),
                DramaturgyFolderEntry(folder_name=FOLDER_B, order_index=2, enabled=True),
            ],
        ),
    )
    save_folder_voiceover_settings(project, build_default_folder_voiceover_settings(project))
    return project


def test_apply_strict_inventory_only_touches_named_folders(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    before = load_folder_voiceover_settings(project)
    assert all(
        s.factuality_mode == FACTUALITY_MODE_NORMAL_SAFE_GENERAL_KNOWLEDGE for s in before.settings
    )

    apply_strict_inventory_factuality_to_folders(project, [FOLDER_A])
    after = load_folder_voiceover_settings(project)
    by_name = {s.folder_name: s for s in after.settings}
    assert by_name[FOLDER_A].factuality_mode == FACTUALITY_MODE_STRICT_INVENTORY_ONLY
    assert by_name[FOLDER_B].factuality_mode == FACTUALITY_MODE_NORMAL_SAFE_GENERAL_KNOWLEDGE
    assert by_name[FOLDER_A].target_words == by_name[FOLDER_B].target_words


def test_regenerate_high_issue_sets_strict_generates_and_runs_allocation(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    upsert_folder_voiceover_draft_item(
        project,
        FolderVoiceoverDraft(
            project_id=project.id,
            folder_name=FOLDER_A,
            sentence_items=[
                SentenceItem(sentence_id="s1", text="Satz.", primary_asset_id="asset_clip1")
            ],
        ),
    )
    upsert_folder_voiceover_draft_item(
        project,
        FolderVoiceoverDraft(
            project_id=project.id,
            folder_name=FOLDER_B,
            sentence_items=[
                SentenceItem(sentence_id="s1", text="Satz.", primary_asset_id="asset_clip1")
            ],
            closing_visual_plan=ClosingVisualPlan(primary_asset_id="asset_clip3"),
        ),
    )

    fake_reports = [
        FolderAssetReadinessReport(
            folder_name=FOLDER_A,
            issues=[
                SentenceAssetReadinessIssue(sentence_id=f"s{i}", issue_type="X", message="m")
                for i in range(FOLDER_ASSET_READINESS_HIGH_ISSUE_REGEN_THRESHOLD)
            ],
        ),
        FolderAssetReadinessReport(folder_name=FOLDER_B, issues=[]),
    ]

    gen_response = PlanLlmResponse(
        provider="anthropic",
        model="claude-sonnet-5",
        raw_text=json.dumps(
            {
                "voiceover_text_full": "Neuer Text.",
                "sentence_items": [
                    {
                        "sentence_id": "sentence_001",
                        "text": "Neuer Text.",
                        "primary_asset_id": "asset_clip1",
                    }
                ],
                "closing_visual_plan": {"primary_asset_id": "asset_clip2"},
            }
        ),
    )

    with patch(f"{_AUTHOR}.generate_plan_text_with_metadata", return_value=gen_response):
        with patch(f"{_ALLOC}.generate_plan_text_with_metadata", return_value=gen_response):
            folders, gen_results, alloc_results, readiness = (
                regenerate_high_issue_folders_with_strict_inventory(
                    project,
                    provider="anthropic",
                    model="claude-sonnet-5",
                    reports=fake_reports,
                )
            )

    assert folders == [FOLDER_A]
    assert len(gen_results) == 1
    assert gen_results[0].status == STATUS_PASS
    assert len(alloc_results) == 1
    assert len(readiness) == 1
    assert readiness[0].folder_name == FOLDER_A

    settings = load_folder_voiceover_settings(project)
    by_name = {s.folder_name: s for s in settings.settings}
    assert by_name[FOLDER_A].factuality_mode == FACTUALITY_MODE_STRICT_INVENTORY_ONLY
    assert by_name[FOLDER_B].factuality_mode == FACTUALITY_MODE_NORMAL_SAFE_GENERAL_KNOWLEDGE


def test_regenerate_high_issue_noop_when_no_folder_meets_threshold(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    fake_reports = [
        FolderAssetReadinessReport(
            folder_name=FOLDER_A,
            issues=[SentenceAssetReadinessIssue(sentence_id="s1", issue_type="X", message="m")],
        )
    ]
    folders, gen_results, alloc_results, readiness = regenerate_high_issue_folders_with_strict_inventory(
        project,
        provider="anthropic",
        model="claude-sonnet-5",
        reports=fake_reports,
    )
    assert folders == []
    assert gen_results == []
    assert alloc_results == []
    assert readiness == []


def test_regenerate_respects_custom_min_issues(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    fake_reports = [
        FolderAssetReadinessReport(
            folder_name=FOLDER_A,
            issues=[
                SentenceAssetReadinessIssue(sentence_id=f"s{i}", issue_type="X", message="m")
                for i in range(2)
            ],
        ),
        FolderAssetReadinessReport(
            folder_name=FOLDER_B,
            issues=[
                SentenceAssetReadinessIssue(sentence_id=f"s{i}", issue_type="X", message="m")
                for i in range(5)
            ],
        ),
    ]
    with patch(f"{_AUTHOR}.generate_folder_voiceover") as gen_mock:
        folders, *_rest = regenerate_high_issue_folders_with_strict_inventory(
            project,
            provider="anthropic",
            model="claude-sonnet-5",
            min_issues=5,
            reports=fake_reports,
        )
    assert folders == [FOLDER_B]
    assert gen_mock.call_count == 1
