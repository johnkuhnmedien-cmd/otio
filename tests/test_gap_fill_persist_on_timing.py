"""Gap-Fills dürfen durch Python Timing / Coverage-Rebuild nicht zurückspringen."""

from __future__ import annotations

from pathlib import Path

from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.services.without_voiceover_enhanced.gap_merge_service import (
    _write_merge_rejection_to_funnel,
    merge_export_ready_gaps_into_timeline,
    merge_gap_merge_reports,
)
from otio_app.services.without_voiceover_enhanced.gap_status_service import (
    carry_over_user_confirmed_weak,
    summarize_gap_status,
)
from otio_app.services.without_voiceover_enhanced.io_utils import load_model, write_json
from otio_app.services.without_voiceover_enhanced.models import (
    AcceptedSupplementsDocument,
    CoverageGap,
    CoverageGapsDocument,
    EnhancedScriptDocument,
    GapMergeReport,
    GapMergeSlotResult,
    ResolvedShot,
    ResolvedTimelineDocument,
    ScriptSegment,
    StockCandidate,
    SupplementFunnelGapReport,
    SupplementFunnelReport,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    accepted_supplements_path,
    coverage_gaps_path,
    gap_merge_report_path,
    resolved_timeline_path,
    supplement_funnel_report_path,
)
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    lock_script,
    save_script_draft,
)


def _project(tmp_path: Path) -> Project:
    root = tmp_path / "proj"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    project = Project(
        id="gap-persist",
        name="gap-persist",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        language="en",
        asset_subdir_names=["Dublin"],
        selected_asset_subdirs=["Dublin"],
        fps=25.0,
    )
    save_script_draft(
        project,
        EnhancedScriptDocument(
            narration_full="Hello Dublin.",
            segments=[
                ScriptSegment(
                    segment_id="Dublin_segment_001",
                    text="Hello Dublin.",
                    sequence_index=1,
                    folder_name="Dublin",
                    folder_order_index=1,
                )
            ],
        ),
    )
    lock_script(project)
    return project


def test_merge_rejection_keeps_funnel_filled_when_manual_accepted(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    write_json(
        accepted_supplements_path(project),
        AcceptedSupplementsDocument(
            script_version="script-v1",
            supplements=[
                StockCandidate(
                    candidate_id="manual_keep",
                    provider="manual",
                    assign_status="manual",
                    gap_id="gap_m",
                    media_validation_status="export_ready",
                    local_media_path="/tmp/m.jpg",
                    cut_plan_run_id="run1",
                )
            ],
        ),
    )
    write_json(
        supplement_funnel_report_path(project),
        SupplementFunnelReport(
            script_version="script-v1",
            cut_plan_run_id="run1",
            filled_gap_ids=["gap_m"],
            gaps=[
                SupplementFunnelGapReport(
                    gap_id="gap_m",
                    filled=True,
                    export_ready_candidate_id="manual_keep",
                )
            ],
        ),
    )
    _write_merge_rejection_to_funnel(
        project,
        gap_id="gap_m",
        rejected_candidate_ids=["manual_keep", "stock_drop"],
        cut_plan_run_id="run1",
        message="zu kurz",
    )
    funnel = load_model(
        supplement_funnel_report_path(project), SupplementFunnelReport
    )
    assert funnel is not None
    assert "gap_m" in funnel.filled_gap_ids
    gap_rep = next(g for g in funnel.gaps if g.gap_id == "gap_m")
    assert gap_rep.filled is True
    assert gap_rep.export_ready_candidate_id == "manual_keep"


def test_merge_gap_merge_reports_keeps_other_chapter_slots() -> None:
    existing = GapMergeReport(
        script_version="v1",
        cut_plan_run_id="run1",
        slots=[
            GapMergeSlotResult(
                shot_id="Achill_slot_001",
                coverage_gap_id="gap_achill",
                status="merged",
                new_asset_id="sup_a",
            ),
            GapMergeSlotResult(
                shot_id="Dublin_slot_002",
                coverage_gap_id="gap_dublin",
                status="open_none",
                message="old",
            ),
        ],
    )
    incoming = GapMergeReport(
        script_version="v1",
        cut_plan_run_id="run1",
        slots=[
            GapMergeSlotResult(
                shot_id="Dublin_slot_002",
                coverage_gap_id="gap_dublin",
                status="merged",
                new_asset_id="sup_d",
            )
        ],
    )
    merged = merge_gap_merge_reports(existing, incoming)
    by_gap = {s.coverage_gap_id: s for s in merged.slots}
    assert by_gap["gap_achill"].status == "merged"
    assert by_gap["gap_dublin"].status == "merged"
    assert "Achill_slot_001" in merged.merged_shot_ids
    assert "Dublin_slot_002" in merged.merged_shot_ids
    assert "gap_dublin" not in merged.open_none_gap_ids


def test_carry_over_user_confirmed_weak() -> None:
    previous = CoverageGapsDocument(
        script_version="v1",
        cut_plan_run_id="run1",
        gaps=[
            CoverageGap(
                gap_id="gap_w",
                needed_visual="x",
                user_confirmed_weak=True,
            )
        ],
    )
    rebuilt = CoverageGapsDocument(
        script_version="v1",
        cut_plan_run_id="run2",
        gaps=[
            CoverageGap(gap_id="gap_w", needed_visual="x", user_confirmed_weak=False),
            CoverageGap(gap_id="gap_new", needed_visual="y"),
        ],
    )
    out = carry_over_user_confirmed_weak(rebuilt, previous)
    by_id = {g.gap_id: g for g in out.gaps}
    assert by_id["gap_w"].user_confirmed_weak is True
    assert by_id["gap_new"].user_confirmed_weak is False


def test_timing_merge_keeps_manual_fill_status(tmp_path: Path) -> None:
    """Merge ohne platzierbaren Kandidaten: Manual bleibt erfüllt in der UI."""
    project = _project(tmp_path)
    write_json(
        coverage_gaps_path(project),
        CoverageGapsDocument(
            script_version="script-v1",
            cut_plan_run_id="run1",
            gaps=[
                CoverageGap(
                    gap_id="gap_m",
                    related_shot_ids=["Dublin_slot_001"],
                    needed_visual="street",
                    priority="high",
                )
            ],
        ),
    )
    write_json(
        accepted_supplements_path(project),
        AcceptedSupplementsDocument(
            script_version="script-v1",
            supplements=[
                StockCandidate(
                    candidate_id="manual_keep",
                    provider="manual",
                    assign_status="manual",
                    gap_id="gap_m",
                    media_validation_status="export_ready",
                    local_media_path="/tmp/missing-on-purpose.jpg",
                    cut_plan_run_id="run1",
                )
            ],
        ),
    )
    write_json(
        supplement_funnel_report_path(project),
        SupplementFunnelReport(
            script_version="script-v1",
            cut_plan_run_id="run1",
            filled_gap_ids=["gap_m"],
            gaps=[
                SupplementFunnelGapReport(
                    gap_id="gap_m",
                    filled=True,
                    export_ready_candidate_id="manual_keep",
                )
            ],
        ),
    )
    # Vorheriger Kapitel-Merge (Achill) — darf nicht verschwinden.
    write_json(
        gap_merge_report_path(project),
        GapMergeReport(
            script_version="script-v1",
            cut_plan_run_id="run1",
            slots=[
                GapMergeSlotResult(
                    shot_id="Achill_slot_001",
                    coverage_gap_id="gap_achill",
                    status="merged",
                    new_asset_id="sup_a",
                )
            ],
            merged_shot_ids=["Achill_slot_001"],
        ),
    )
    timeline = ResolvedTimelineDocument(
        script_version="script-v1",
        fps=25.0,
        total_duration_seconds=2.0,
        shots=[
            ResolvedShot(
                shot_id="Dublin_slot_001",
                asset_id="",
                timeline_start_seconds=0.0,
                timeline_end_seconds=2.0,
                source_start_seconds=0.0,
                source_end_seconds=2.0,
                asset_fit="none",
                coverage_gap_id="gap_m",
                open_gap=True,
            )
        ],
    )
    write_json(resolved_timeline_path(project), timeline)

    _merged, report = merge_export_ready_gaps_into_timeline(
        project,
        timeline=timeline,
        require_closed_none=False,
        persist=False,
        persist_report=True,
    )
    assert report.slots
    assert report.slots[0].status == "skipped"
    assert "gap_m" not in report.open_none_gap_ids

    funnel = load_model(
        supplement_funnel_report_path(project), SupplementFunnelReport
    )
    assert funnel is not None
    assert "gap_m" in funnel.filled_gap_ids

    persisted = load_model(gap_merge_report_path(project), GapMergeReport)
    assert persisted is not None
    gap_ids = {s.coverage_gap_id for s in persisted.slots}
    assert "gap_achill" in gap_ids
    assert "gap_m" in gap_ids

    summary = summarize_gap_status(project)
    assert "gap_m" in summary.filled_gap_ids
    assert "gap_m" not in summary.open_gap_ids
