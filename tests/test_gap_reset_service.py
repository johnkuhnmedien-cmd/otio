"""Offene Coverage Gaps vor einem neuen LLM-Cut räumen."""

from __future__ import annotations

from pathlib import Path

import pytest

from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.services.without_voiceover_enhanced.gap_reset_service import (
    preview_open_gap_reset,
    reset_open_coverage_gaps,
)
from otio_app.services.without_voiceover_enhanced.io_utils import load_model, write_json
from otio_app.services.without_voiceover_enhanced.models import (
    AcceptedSupplementsDocument,
    CoverageGap,
    CoverageGapsDocument,
    FunnelCandidateRecord,
    StockCandidate,
    StockSearchResultsDocument,
    SupplementFunnelGapReport,
    SupplementFunnelReport,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    accepted_supplements_path,
    coverage_gaps_path,
    stock_search_results_path,
    supplement_funnel_report_path,
)

OPEN_GAP = "Cliffs_of_Moher_gap_slot_003"
FILLED_GAP = "Cliffs_of_Moher_gap_slot_007"
RUN_ID = "run0001"


def _project(tmp_path: Path) -> Project:
    root = tmp_path / "Irland"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    return Project(
        id="reset",
        name="Irland",
        project_root=str(root),
        work_dir=str(work),
        language="de",
        mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        asset_subdir_names=["Cliffs of Moher"],
        selected_asset_subdirs=["Cliffs of Moher"],
    )


@pytest.fixture
def project_with_gaps(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Project:
    """Ein offener und ein erfüllter Gap, dazu Such- und Funnel-Zustand."""
    project = _project(tmp_path)
    media = project.work_dir_path / "clean" / "Cliffs_of_Moher" / "pexels_1.mp4"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"\x00" * 1024)

    write_json(
        coverage_gaps_path(project),
        CoverageGapsDocument(
            script_version="v1",
            cut_plan_run_id=RUN_ID,
            gaps=[
                CoverageGap(gap_id=OPEN_GAP, needed_visual="Küste aus der Luft"),
                CoverageGap(gap_id=FILLED_GAP, needed_visual="Detail Fels"),
            ],
        ),
    )
    write_json(
        stock_search_results_path(project),
        StockSearchResultsDocument(
            script_version="v1",
            candidates=[
                StockCandidate(candidate_id="c_open_1", provider="pexels", gap_id=OPEN_GAP),
                StockCandidate(candidate_id="c_open_2", provider="pexels", gap_id=OPEN_GAP),
                StockCandidate(candidate_id="c_filled", provider="pexels", gap_id=FILLED_GAP),
            ],
        ),
    )
    write_json(
        supplement_funnel_report_path(project),
        SupplementFunnelReport(
            cut_plan_run_id=RUN_ID,
            gaps=[
                SupplementFunnelGapReport(
                    gap_id=OPEN_GAP,
                    candidates=[FunnelCandidateRecord(candidate_id="c_open_1")],
                ),
                SupplementFunnelGapReport(
                    gap_id=FILLED_GAP,
                    filled=True,
                    export_ready_candidate_id="c_filled",
                    candidates=[
                        FunnelCandidateRecord(
                            candidate_id="c_filled",
                            funnel_status="export_ready",
                            local_media_path=str(media),
                        )
                    ],
                ),
            ],
            requested_gap_ids=[OPEN_GAP, FILLED_GAP],
            open_gap_ids=[OPEN_GAP],
            filled_gap_ids=[FILLED_GAP],
        ),
    )
    write_json(
        accepted_supplements_path(project),
        AcceptedSupplementsDocument(
            script_version="v1",
            supplements=[
                StockCandidate(
                    candidate_id="c_filled",
                    provider="pexels",
                    gap_id=FILLED_GAP,
                    local_media_path=str(media),
                    media_validation_status="export_ready",
                    cut_plan_run_id=RUN_ID,
                ),
                StockCandidate(
                    candidate_id="c_open_1",
                    provider="pexels",
                    gap_id=OPEN_GAP,
                    media_validation_status="",
                    cut_plan_run_id=RUN_ID,
                ),
            ],
        ),
    )

    # Gap-Status ohne kompletten Cut-Plan-Kontext deterministisch halten.
    from otio_app.services.without_voiceover_enhanced.gap_status_service import (
        GapStatusSummary,
    )

    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.gap_status_service"
        ".summarize_gap_status",
        lambda _project: GapStatusSummary(
            total=2,
            open_gap_ids=[OPEN_GAP],
            filled_gap_ids=[FILLED_GAP],
            cut_plan_run_id=RUN_ID,
        ),
    )
    # Externe Spiegel-JSON braucht den Locked-Script-Kontext nicht.
    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.coverage_gap_external_export"
        ".refresh_coverage_gaps_external_export",
        lambda project, coverage=None: None,
    )
    return project


def test_preview_reports_scope_without_changing_anything(project_with_gaps):
    project = project_with_gaps

    preview = preview_open_gap_reset(project)

    assert preview.open_gap_ids == [OPEN_GAP]
    assert preview.filled_gap_ids == [FILLED_GAP]
    assert preview.search_candidates == 2
    assert preview.funnel_gap_reports == 1
    assert preview.accepted_pending == 1
    assert preview.accepted_export_ready == 1
    assert preview.has_work

    coverage = load_model(coverage_gaps_path(project), CoverageGapsDocument)
    assert [gap.gap_id for gap in coverage.gaps] == [OPEN_GAP, FILLED_GAP]


def test_reset_removes_open_gap_and_its_state(project_with_gaps):
    project = project_with_gaps

    report = reset_open_coverage_gaps(project)

    assert report.removed_gap_ids == [OPEN_GAP]
    assert report.kept_gap_ids == [FILLED_GAP]
    assert report.removed_search_candidates == 2
    assert report.removed_funnel_gap_reports == 1
    assert report.removed_accepted_pending == 1

    coverage = load_model(coverage_gaps_path(project), CoverageGapsDocument)
    assert [gap.gap_id for gap in coverage.gaps] == [FILLED_GAP]

    search = load_model(stock_search_results_path(project), StockSearchResultsDocument)
    assert [c.candidate_id for c in search.candidates] == ["c_filled"]

    funnel = load_model(supplement_funnel_report_path(project), SupplementFunnelReport)
    assert [g.gap_id for g in funnel.gaps] == [FILLED_GAP]
    assert funnel.requested_gap_ids == [FILLED_GAP]
    assert funnel.open_gap_ids == []


def test_reset_keeps_paid_assets_and_their_binding(project_with_gaps):
    project = project_with_gaps

    reset_open_coverage_gaps(project)

    accepted = load_model(
        accepted_supplements_path(project), AcceptedSupplementsDocument
    )
    assert [s.candidate_id for s in accepted.supplements] == ["c_filled"]
    kept = accepted.supplements[0]
    assert kept.gap_id == FILLED_GAP
    assert kept.cut_plan_run_id == RUN_ID
    assert Path(kept.local_media_path).is_file()


def test_unbind_filled_keeps_asset_but_drops_gap_binding(project_with_gaps):
    project = project_with_gaps

    report = reset_open_coverage_gaps(project, unbind_filled=True)

    assert report.unbound_accepted_export_ready == 1
    accepted = load_model(
        accepted_supplements_path(project), AcceptedSupplementsDocument
    )
    kept = accepted.supplements[0]
    assert kept.candidate_id == "c_filled"
    assert kept.gap_id == ""
    assert kept.cut_plan_run_id == ""
    # Das bezahlte Medium bleibt in jedem Fall erhalten.
    assert Path(kept.local_media_path).is_file()
    assert kept.media_validation_status == "export_ready"

    funnel = load_model(supplement_funnel_report_path(project), SupplementFunnelReport)
    filled_row = next(g for g in funnel.gaps if g.gap_id == FILLED_GAP)
    assert filled_row.filled is False
    assert filled_row.export_ready_candidate_id is None
    assert FILLED_GAP not in (funnel.filled_gap_ids or [])
    assert FILLED_GAP in (funnel.open_gap_ids or [])


def test_reset_never_deletes_media_files(project_with_gaps):
    project = project_with_gaps
    media_files = sorted(
        path for path in (project.work_dir_path / "clean").rglob("*") if path.is_file()
    )

    reset_open_coverage_gaps(project, unbind_filled=True)

    assert (
        sorted(
            path
            for path in (project.work_dir_path / "clean").rglob("*")
            if path.is_file()
        )
        == media_files
    )


def test_reset_without_open_gaps_is_a_no_op(project_with_gaps, monkeypatch):
    project = project_with_gaps
    from otio_app.services.without_voiceover_enhanced.gap_status_service import (
        GapStatusSummary,
    )

    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.gap_status_service"
        ".summarize_gap_status",
        lambda _project: GapStatusSummary(
            total=2, open_gap_ids=[], filled_gap_ids=[OPEN_GAP, FILLED_GAP]
        ),
    )

    report = reset_open_coverage_gaps(project)

    assert report.removed_gap_ids == []
    assert sorted(report.kept_gap_ids) == sorted([OPEN_GAP, FILLED_GAP])
    coverage = load_model(coverage_gaps_path(project), CoverageGapsDocument)
    assert len(coverage.gaps) == 2


def test_new_cut_rebinds_fill_to_recurring_gap_id(project_with_gaps):
    """Begründung für den Reset: Gap-IDs wiederholen sich über Läufe hinweg.

    ``gap_{slot_id}`` ist deterministisch. Ein neuer Cut mit neuer Run-ID biegt
    ein fertiges Fill deshalb auf denselben Gap um — auch wenn dort redaktionell
    inzwischen etwas anderes gebraucht wird.
    """
    from otio_app.services.without_voiceover_enhanced.gap_status_service import (
        rebind_gap_fills_to_current_run,
    )

    project = project_with_gaps
    new_run = "run0002"
    coverage = load_model(coverage_gaps_path(project), CoverageGapsDocument)
    write_json(
        coverage_gaps_path(project),
        coverage.model_copy(update={"cut_plan_run_id": new_run}),
    )

    rebind_gap_fills_to_current_run(project)

    accepted = load_model(
        accepted_supplements_path(project), AcceptedSupplementsDocument
    )
    rebound = next(s for s in accepted.supplements if s.candidate_id == "c_filled")
    assert rebound.cut_plan_run_id == new_run
    assert rebound.gap_id == FILLED_GAP


def test_unbound_fill_is_not_rebound_by_a_new_cut(project_with_gaps):
    """Nach dem Lösen der Bindung muss der neue Cut die Zuweisung neu verdienen."""
    from otio_app.services.without_voiceover_enhanced.gap_status_service import (
        rebind_gap_fills_to_current_run,
    )

    project = project_with_gaps
    reset_open_coverage_gaps(project, unbind_filled=True)
    coverage = load_model(coverage_gaps_path(project), CoverageGapsDocument)
    write_json(
        coverage_gaps_path(project),
        coverage.model_copy(update={"cut_plan_run_id": "run0002"}),
    )

    rebind_gap_fills_to_current_run(project)

    accepted = load_model(
        accepted_supplements_path(project), AcceptedSupplementsDocument
    )
    kept = next(s for s in accepted.supplements if s.candidate_id == "c_filled")
    assert kept.gap_id == ""
    assert kept.cut_plan_run_id == ""


def test_reset_scoped_to_gap_ids_leaves_other_chapters_alone(project_with_gaps):
    """Kapitel-Reset: nur die übergebenen Gap-IDs werden geräumt."""
    project = project_with_gaps

    report = reset_open_coverage_gaps(project, gap_ids=[FILLED_GAP])

    # FILLED_GAP ist nicht offen → nichts zu tun, OPEN_GAP bleibt unberührt.
    assert report.removed_gap_ids == []
    coverage = load_model(coverage_gaps_path(project), CoverageGapsDocument)
    assert [gap.gap_id for gap in coverage.gaps] == [OPEN_GAP, FILLED_GAP]
    search = load_model(stock_search_results_path(project), StockSearchResultsDocument)
    assert len(search.candidates) == 3


def test_reset_scoped_to_the_open_gap_removes_only_its_state(project_with_gaps):
    project = project_with_gaps

    report = reset_open_coverage_gaps(project, gap_ids=[OPEN_GAP, "fremdes_kapitel_gap"])

    assert report.removed_gap_ids == [OPEN_GAP]
    search = load_model(stock_search_results_path(project), StockSearchResultsDocument)
    assert [c.candidate_id for c in search.candidates] == ["c_filled"]


def test_chapter_gap_ids_reads_the_slots(tmp_path):
    """Kapitelzugehörigkeit kommt aus den Slots, nicht aus dem Namensmuster."""
    from otio_app.services.without_voiceover_enhanced.chapter_cut_service import (
        chapter_gap_ids,
    )
    from otio_app.services.without_voiceover_enhanced.models import (
        CutBoundary,
        CutSlot,
        UnifiedCutPlanDocument,
    )

    plan = UnifiedCutPlanDocument(
        script_version="v1",
        boundaries=[
            CutBoundary(
                cut_id=f"cut_{index:03d}",
                sentence_id=f"s{index}",
                offset_seconds=float(index),
            )
            for index in range(1, 6)
        ],
        slots=[
            CutSlot(slot_id="slot_001", asset_fit="strong"),
            CutSlot(slot_id="slot_002", asset_fit="none", coverage_gap_id="gap_slot_002"),
            CutSlot(slot_id="slot_003", asset_fit="weak", coverage_gap_id="gap_slot_003"),
            CutSlot(slot_id="slot_004", asset_fit="weak", coverage_gap_id="gap_slot_003"),
        ],
    )

    assert chapter_gap_ids(plan) == [
        "gap_slot_002",
        "gap_slot_003",
        "gap_slot_004",
    ]
    assert chapter_gap_ids(None) == []


def test_preview_on_project_without_gaps(tmp_path):
    project = _project(tmp_path)

    preview = preview_open_gap_reset(project)

    assert not preview.has_work
    assert preview.open_gap_ids == []
