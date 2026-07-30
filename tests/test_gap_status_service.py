"""Gap-Status — Funnel/Accepted schließt Gaps; stale Run-IDs invalidieren."""

from __future__ import annotations

from pathlib import Path

from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.services.without_voiceover_enhanced.gap_status_service import (
    compute_cut_plan_run_id,
    is_weak_upgrade_gap,
    summarize_gap_status,
)
from otio_app.services.without_voiceover_enhanced.io_utils import load_model, write_json
from otio_app.services.without_voiceover_enhanced.models import (
    CoverageGap,
    CoverageGapsDocument,
    CutBoundary,
    CutSlot,
    GapMergeReport,
    GapMergeSlotResult,
    SupplementFunnelGapReport,
    SupplementFunnelReport,
    UnifiedCutPlanDocument,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    coverage_gaps_path,
    gap_merge_report_path,
    supplement_funnel_report_path,
    unified_cut_plan_path,
)
from otio_app.services.without_voiceover_enhanced.supplement_funnel_service import (
    list_open_funnel_gap_ids,
)
from otio_app.services.without_voiceover_enhanced.unified_cut_plan import unified_to_rough


def _project(tmp_path: Path) -> Project:
    root = tmp_path / "proj"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    return Project(
        id="gap-status",
        name="gap-status",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        asset_subdir_names=["A"],
        selected_asset_subdirs=["A"],
    )


def _plan() -> UnifiedCutPlanDocument:
    return UnifiedCutPlanDocument(
        script_version="script-v1",
        boundaries=[
            CutBoundary(cut_id="b0", sentence_id="a__s001", position="start"),
            CutBoundary(cut_id="b1", sentence_id="a__s002", position="middle"),
            CutBoundary(cut_id="b2", sentence_id="a__s003", position="end"),
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


def test_unified_to_rough_writes_cut_plan_run_id() -> None:
    plan = _plan()
    _rough, coverage = unified_to_rough(plan)
    assert coverage.cut_plan_run_id
    assert coverage.cut_plan_run_id == compute_cut_plan_run_id(plan)
    assert is_weak_upgrade_gap(coverage.gaps[0])
    assert not is_weak_upgrade_gap(coverage.gaps[1])


def test_weak_closes_when_funnel_export_ready(tmp_path: Path) -> None:
    """Download/export_ready schließt weak sofort in der UI (nicht erst Merge)."""
    project = _project(tmp_path)
    plan = _plan()
    write_json(unified_cut_plan_path(project), plan)
    _rough, coverage = unified_to_rough(plan)
    write_json(coverage_gaps_path(project), coverage)
    write_json(
        supplement_funnel_report_path(project),
        SupplementFunnelReport(
            run_id="funnel_x",
            script_version="script-v1",
            cut_plan_run_id=coverage.cut_plan_run_id,
            gaps=[
                SupplementFunnelGapReport(
                    gap_id="gap_weak",
                    filled=True,
                    export_ready_candidate_id="cand_weak",
                ),
                SupplementFunnelGapReport(
                    gap_id="gap_none",
                    filled=True,
                    export_ready_candidate_id="cand_none",
                ),
            ],
            filled_gap_ids=["gap_weak", "gap_none"],
        ),
    )

    status = summarize_gap_status(project)
    assert status.total == 2
    assert status.open_count == 0
    assert set(status.filled_gap_ids) == {"gap_weak", "gap_none"}


def test_accepted_export_ready_closes_gap_without_funnel_entry(
    tmp_path: Path,
) -> None:
    """Bereits akzeptierte Downloads zählen als erfüllt, auch ohne Funnel-filled."""
    from otio_app.services.without_voiceover_enhanced.models import (
        AcceptedSupplementsDocument,
        StockCandidate,
    )
    from otio_app.services.without_voiceover_enhanced.paths import (
        accepted_supplements_path,
    )

    project = _project(tmp_path)
    plan = _plan()
    write_json(unified_cut_plan_path(project), plan)
    _rough, coverage = unified_to_rough(plan)
    write_json(coverage_gaps_path(project), coverage)
    write_json(
        accepted_supplements_path(project),
        AcceptedSupplementsDocument(
            script_version="script-v1",
            supplements=[
                StockCandidate(
                    candidate_id="cand_prev",
                    provider="pexels",
                    gap_id="gap_weak",
                    media_validation_status="export_ready",
                    cut_plan_run_id=coverage.cut_plan_run_id,
                    local_media_path="/tmp/prev.mp4",
                )
            ],
        ),
    )

    status = summarize_gap_status(project)
    assert "gap_weak" in status.filled_gap_ids
    assert "gap_none" in status.open_gap_ids
    assert status.filled_count == 1
    assert status.open_count == 1


def test_weak_closes_only_after_merge_decision(tmp_path: Path) -> None:
    project = _project(tmp_path)
    plan = _plan()
    write_json(unified_cut_plan_path(project), plan)
    _rough, coverage = unified_to_rough(plan)
    write_json(coverage_gaps_path(project), coverage)
    write_json(
        gap_merge_report_path(project),
        GapMergeReport(
            script_version="script-v1",
            cut_plan_run_id=coverage.cut_plan_run_id,
            slots=[
                GapMergeSlotResult(
                    shot_id="A_slot_weak",
                    coverage_gap_id="gap_weak",
                    status="kept_local_weak",
                ),
                GapMergeSlotResult(
                    shot_id="A_slot_none",
                    coverage_gap_id="gap_none",
                    status="merged",
                ),
            ],
        ),
    )

    status = summarize_gap_status(project)
    assert status.open_count == 0
    assert set(status.filled_gap_ids) == {"gap_weak", "gap_none"}


def test_stale_funnel_without_accepted_does_not_count_as_filled(
    tmp_path: Path,
) -> None:
    """Nur Funnel-Report ohne Accepted → kein Auto-Rebind, bleibt offen."""
    project = _project(tmp_path)
    plan = _plan()
    write_json(unified_cut_plan_path(project), plan)
    _rough, coverage = unified_to_rough(plan)
    write_json(coverage_gaps_path(project), coverage)
    write_json(
        supplement_funnel_report_path(project),
        SupplementFunnelReport(
            run_id="funnel_old",
            script_version="script-v1",
            cut_plan_run_id="stale_run_id",
            gaps=[
                SupplementFunnelGapReport(
                    gap_id="gap_none",
                    filled=True,
                    export_ready_candidate_id="old_cand",
                )
            ],
            filled_gap_ids=["gap_none"],
        ),
    )

    status = summarize_gap_status(project)
    assert status.funnel_stale is True
    assert "gap_none" in status.open_gap_ids
    assert status.filled_count == 0
    assert list_open_funnel_gap_ids(project) == ["gap_weak", "gap_none"]


def test_restore_accepted_from_funnel_when_accepted_was_purged(
    tmp_path: Path,
) -> None:
    """Alte Migration leerte Accepted — Fills aus Funnel + Datei wiederherstellen."""
    from otio_app.services.without_voiceover_enhanced.models import (
        AcceptedSupplementsDocument,
        FunnelCandidateRecord,
    )
    from otio_app.services.without_voiceover_enhanced.paths import (
        accepted_supplements_path,
        stock_candidate_download_dir,
    )

    project = _project(tmp_path)
    plan = _plan()
    write_json(unified_cut_plan_path(project), plan)
    _rough, coverage = unified_to_rough(plan)
    write_json(coverage_gaps_path(project), coverage)

    media_dir = stock_candidate_download_dir(
        project, gap_id="gap_none", candidate_id="pexels_video_restore"
    )
    media_dir.mkdir(parents=True)
    media = media_dir / "pexels_video_restore.mp4"
    media.write_bytes(b"\x00" * 128)

    write_json(
        supplement_funnel_report_path(project),
        SupplementFunnelReport(
            run_id="funnel_old",
            script_version="script-v1",
            cut_plan_run_id="old_run",
            filled_gap_ids=["gap_none"],
            gaps=[
                SupplementFunnelGapReport(
                    gap_id="gap_none",
                    filled=True,
                    export_ready_candidate_id="pexels_video_restore",
                    candidates=[
                        FunnelCandidateRecord(
                            candidate_id="pexels_video_restore",
                            provider="pexels",
                            funnel_status="export_ready",
                            local_media_path=str(media),
                        )
                    ],
                )
            ],
        ),
    )

    status = summarize_gap_status(project)
    assert "gap_none" in status.filled_gap_ids
    assert "wiederhergestellt" in (status.message or "").lower() or status.filled_count >= 1
    accepted = load_model(
        accepted_supplements_path(project), AcceptedSupplementsDocument
    )
    assert accepted is not None
    assert any(s.candidate_id == "pexels_video_restore" for s in accepted.supplements)


def test_accepted_with_old_run_id_rebinds_to_current_plan(tmp_path: Path) -> None:
    """Nach neuem Cut-Plan-Lauf: Accepted mit gleicher Gap-ID wieder erfüllt."""
    from otio_app.services.without_voiceover_enhanced.models import (
        AcceptedSupplementsDocument,
        StockCandidate,
    )
    from otio_app.services.without_voiceover_enhanced.paths import (
        accepted_supplements_path,
    )

    project = _project(tmp_path)
    plan = _plan()
    write_json(unified_cut_plan_path(project), plan)
    _rough, coverage = unified_to_rough(plan)
    write_json(coverage_gaps_path(project), coverage)
    write_json(
        accepted_supplements_path(project),
        AcceptedSupplementsDocument(
            script_version="script-v1",
            supplements=[
                StockCandidate(
                    candidate_id="cand_manual",
                    provider="manual",
                    gap_id="gap_none",
                    media_validation_status="export_ready",
                    cut_plan_run_id="old_run_before_llm_recut",
                    local_media_path="/tmp/manual.mp4",
                ),
                StockCandidate(
                    candidate_id="cand_weak",
                    provider="pexels",
                    gap_id="gap_weak",
                    media_validation_status="export_ready",
                    cut_plan_run_id="old_run_before_llm_recut",
                    local_media_path="/tmp/weak.mp4",
                ),
            ],
        ),
    )
    write_json(
        supplement_funnel_report_path(project),
        SupplementFunnelReport(
            run_id="funnel_old",
            script_version="script-v1",
            cut_plan_run_id="old_run_before_llm_recut",
            filled_gap_ids=["gap_none", "gap_weak"],
            gaps=[
                SupplementFunnelGapReport(
                    gap_id="gap_none",
                    filled=True,
                    export_ready_candidate_id="cand_manual",
                ),
                SupplementFunnelGapReport(
                    gap_id="gap_weak",
                    filled=True,
                    export_ready_candidate_id="cand_weak",
                ),
            ],
        ),
    )

    status = summarize_gap_status(project)
    assert status.open_count == 0
    assert set(status.filled_gap_ids) == {"gap_weak", "gap_none"}
    assert status.funnel_stale is False
    assert "Accepted-Fill" in (status.message or "")

    accepted = load_model(
        accepted_supplements_path(project), AcceptedSupplementsDocument
    )
    assert accepted is not None
    assert all(
        s.cut_plan_run_id == coverage.cut_plan_run_id for s in accepted.supplements
    )


def test_missing_funnel_run_id_treated_as_stale_when_coverage_has_run(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    plan = _plan()
    write_json(unified_cut_plan_path(project), plan)
    _rough, coverage = unified_to_rough(plan)
    write_json(coverage_gaps_path(project), coverage)
    write_json(
        supplement_funnel_report_path(project),
        SupplementFunnelReport(
            run_id="funnel_legacy",
            script_version="script-v1",
            cut_plan_run_id="",  # alter Report
            filled_gap_ids=["gap_none"],
            gaps=[
                SupplementFunnelGapReport(
                    gap_id="gap_none",
                    filled=True,
                    export_ready_candidate_id="legacy",
                )
            ],
        ),
    )

    status = summarize_gap_status(project)
    assert status.funnel_stale is True
    assert "gap_none" in status.open_gap_ids
    assert list_open_funnel_gap_ids(project) == ["gap_weak", "gap_none"]


def test_legacy_coverage_without_run_id_still_lists_open_gaps(tmp_path: Path) -> None:
    """E2E-4: Funnel-filled allein reicht nicht — ohne merge-fähiges Accepted offen."""
    project = _project(tmp_path)
    write_json(
        coverage_gaps_path(project),
        CoverageGapsDocument(
            script_version="script-v1",
            cut_plan_run_id="",
            gaps=[
                CoverageGap(gap_id="gap_1", needed_visual="a", priority="high"),
                CoverageGap(gap_id="gap_2", needed_visual="b", priority="medium"),
            ],
        ),
    )
    write_json(
        supplement_funnel_report_path(project),
        SupplementFunnelReport(
            run_id="funnel_legacy",
            script_version="script-v1",
            cut_plan_run_id="",
            filled_gap_ids=["gap_1"],
            gaps=[
                SupplementFunnelGapReport(
                    gap_id="gap_1",
                    filled=True,
                    export_ready_candidate_id="c1",
                )
            ],
        ),
    )
    # Kein Accepted mit lokaler Datei → Gap bleibt für Funnel offen.
    assert list_open_funnel_gap_ids(project) == ["gap_1", "gap_2"]
    status = summarize_gap_status(project)
    # summarize_gap_status: Funnel-filled zählt für UI none weiterhin.
    assert "gap_1" in status.filled_gap_ids
    assert "gap_2" in status.open_gap_ids
