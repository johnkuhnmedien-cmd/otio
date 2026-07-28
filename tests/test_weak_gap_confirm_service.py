"""Weak-Asset-Bestätigung schließt Upgrade-Gaps ohne Supplement."""

from __future__ import annotations

from pathlib import Path

from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.services.without_voiceover_enhanced.gap_status_service import (
    summarize_gap_status,
)
from otio_app.services.without_voiceover_enhanced.io_utils import write_json
from otio_app.services.without_voiceover_enhanced.models import (
    CoverageGap,
    CoverageGapsDocument,
    CutBoundary,
    CutSlot,
    EnhancedScriptDocument,
    ScriptSegment,
    UnifiedCutPlanDocument,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    coverage_gaps_path,
    unified_cut_plan_path,
)
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    lock_script,
    save_script_draft,
)
from otio_app.services.without_voiceover_enhanced.unified_cut_plan import unified_to_rough
from otio_app.services.without_voiceover_enhanced.weak_gap_confirm_service import (
    WeakGapConfirmError,
    confirm_weak_local_asset_for_gap,
    list_confirmable_weak_gaps,
)
import pytest


def _project(tmp_path: Path) -> Project:
    root = tmp_path / "proj"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    return Project(
        id="weak-confirm",
        name="weak-confirm",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        asset_subdir_names=["A"],
        selected_asset_subdirs=["A"],
    )


def _lock(project: Project) -> None:
    save_script_draft(
        project,
        EnhancedScriptDocument(
            narration_full="Text.",
            segments=[
                ScriptSegment(segment_id="a__s001", text="Text.", sequence_index=1)
            ],
        ),
    )
    lock_script(project)


def _plan_with_weak_and_none() -> UnifiedCutPlanDocument:
    return UnifiedCutPlanDocument(
        script_version="script-v1",
        boundaries=[
            CutBoundary(cut_id="b0", sentence_id="a__s001", position="start"),
            CutBoundary(cut_id="b1", sentence_id="a__s001", position="middle"),
            CutBoundary(cut_id="b2", sentence_id="a__s001", position="end"),
        ],
        slots=[
            CutSlot(
                slot_id="A_slot_weak",
                local_asset_id="loc_weak",
                asset_fit="weak",
                coverage_gap_id="gap_weak",
                needed_visual="better light",
            ),
            CutSlot(
                slot_id="A_slot_none",
                local_asset_id=None,
                asset_fit="none",
                coverage_gap_id="gap_none",
                needed_visual="street",
            ),
        ],
    )


def test_confirm_weak_closes_gap_and_unblocks_status(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _lock(project)
    plan = _plan_with_weak_and_none()
    write_json(unified_cut_plan_path(project), plan)
    _rough, coverage = unified_to_rough(plan)
    write_json(coverage_gaps_path(project), coverage)

    before = summarize_gap_status(project)
    assert "gap_weak" in before.open_gap_ids
    assert "gap_none" in before.open_gap_ids

    confirmable = list_confirmable_weak_gaps(project)
    assert [g.gap_id for g in confirmable] == ["gap_weak"]

    result = confirm_weak_local_asset_for_gap(project, "gap_weak")
    assert result.local_asset_id == "loc_weak"

    after = summarize_gap_status(project)
    assert "gap_weak" not in after.open_gap_ids
    assert "gap_weak" in after.filled_gap_ids
    assert "gap_none" in after.open_gap_ids


def test_confirm_weak_rejects_none_gap(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _lock(project)
    plan = _plan_with_weak_and_none()
    write_json(unified_cut_plan_path(project), plan)
    _rough, coverage = unified_to_rough(plan)
    write_json(coverage_gaps_path(project), coverage)

    with pytest.raises(WeakGapConfirmError, match="Weak-Upgrade"):
        confirm_weak_local_asset_for_gap(project, "gap_none")


def test_confirm_weak_requires_local_asset(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _lock(project)
    write_json(
        coverage_gaps_path(project),
        CoverageGapsDocument(
            script_version="script-v1",
            cut_plan_run_id="run_x",
            gaps=[
                CoverageGap(
                    gap_id="gap_orphan_weak",
                    related_shot_ids=["missing_slot"],
                    priority="medium",
                    needed_visual="x",
                )
            ],
        ),
    )
    with pytest.raises(WeakGapConfirmError, match="kein lokales Asset"):
        confirm_weak_local_asset_for_gap(project, "gap_orphan_weak")
