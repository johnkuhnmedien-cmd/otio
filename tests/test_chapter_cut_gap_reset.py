"""Ein neuer Kapitel-Cut räumt die offenen Gaps seines Kapitels selbst."""

from __future__ import annotations

from pathlib import Path

import pytest

from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.services.without_voiceover_enhanced.chapter_cut_service import (
    persist_chapter_unified_plan,
    reset_open_gaps_for_chapter,
)
from otio_app.services.without_voiceover_enhanced.io_utils import load_model, write_json
from otio_app.services.without_voiceover_enhanced.models import (
    AcceptedSupplementsDocument,
    CoverageGap,
    CoverageGapsDocument,
    CutBoundary,
    CutSlot,
    FunnelCandidateRecord,
    StockCandidate,
    StockSearchResultsDocument,
    SupplementFunnelGapReport,
    SupplementFunnelReport,
    UnifiedCutPlanDocument,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    accepted_supplements_path,
    chapter_unified_cut_plan_path,
    coverage_gaps_path,
    stock_search_results_path,
    supplement_funnel_report_path,
)

ATHENS_OPEN = "gap_slot_003"
ATHENS_FILLED = "gap_slot_004"
CORFU_OPEN = "gap_slot_011"
RUN_ID = "run0001"


def _project(tmp_path: Path) -> Project:
    root = tmp_path / "Griechenland"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    return Project(
        id="chapter-reset",
        name="Griechenland",
        project_root=str(root),
        work_dir=str(work),
        language="en",
        mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        asset_subdir_names=["Athens", "Corfu"],
        selected_asset_subdirs=["Athens", "Corfu"],
    )


def _plan(gap_ids: list[str], *, prefix: str = "ath") -> UnifiedCutPlanDocument:
    """Kapitelplan mit je einem Slot ohne passendes Asset pro Gap-ID.

    ``prefix`` hält Boundary-IDs über Kapitel hinweg eindeutig — der Merge
    besteht darauf.
    """
    slots = [
        CutSlot(
            slot_id=f"{prefix}_slot_{index:03d}",
            asset_fit="none",
            coverage_gap_id=gap_id,
        )
        for index, gap_id in enumerate(gap_ids, start=1)
    ]
    return UnifiedCutPlanDocument(
        script_version="v1",
        boundaries=[
            CutBoundary(
                cut_id=f"{prefix}_cut_{index:03d}",
                sentence_id=f"{prefix}_s{index}",
                offset_seconds=float(index),
            )
            for index in range(1, len(slots) + 2)
        ],
        slots=slots,
    )


@pytest.fixture
def project_with_two_chapters(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Project:
    project = _project(tmp_path)
    media = project.work_dir_path / "clean" / "Athens" / "pexels_1.mov"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"\x00" * 1024)

    write_json(
        chapter_unified_cut_plan_path(project, "Athens"),
        _plan([ATHENS_OPEN, ATHENS_FILLED]),
    )
    write_json(
        chapter_unified_cut_plan_path(project, "Corfu"),
        _plan([CORFU_OPEN], prefix="cor"),
    )
    write_json(
        coverage_gaps_path(project),
        CoverageGapsDocument(
            script_version="v1",
            cut_plan_run_id=RUN_ID,
            gaps=[
                CoverageGap(gap_id=ATHENS_OPEN, needed_visual="Akropolis bei Nacht"),
                CoverageGap(gap_id=ATHENS_FILLED, needed_visual="Agora Detail"),
                CoverageGap(gap_id=CORFU_OPEN, needed_visual="Promenade"),
            ],
        ),
    )
    write_json(
        stock_search_results_path(project),
        StockSearchResultsDocument(
            script_version="v1",
            candidates=[
                StockCandidate(
                    candidate_id="c_athens_open", provider="pexels", gap_id=ATHENS_OPEN
                ),
                StockCandidate(
                    candidate_id="c_athens_filled",
                    provider="pexels",
                    gap_id=ATHENS_FILLED,
                ),
                StockCandidate(
                    candidate_id="c_corfu_open", provider="pexels", gap_id=CORFU_OPEN
                ),
            ],
        ),
    )
    write_json(
        supplement_funnel_report_path(project),
        SupplementFunnelReport(
            cut_plan_run_id=RUN_ID,
            gaps=[
                SupplementFunnelGapReport(gap_id=ATHENS_OPEN),
                SupplementFunnelGapReport(
                    gap_id=ATHENS_FILLED,
                    filled=True,
                    export_ready_candidate_id="c_athens_filled",
                    candidates=[
                        FunnelCandidateRecord(
                            candidate_id="c_athens_filled",
                            funnel_status="export_ready",
                            local_media_path=str(media),
                        )
                    ],
                ),
                SupplementFunnelGapReport(gap_id=CORFU_OPEN),
            ],
            open_gap_ids=[ATHENS_OPEN, CORFU_OPEN],
            filled_gap_ids=[ATHENS_FILLED],
        ),
    )
    write_json(
        accepted_supplements_path(project),
        AcceptedSupplementsDocument(
            script_version="v1",
            supplements=[
                StockCandidate(
                    candidate_id="c_athens_filled",
                    provider="pexels",
                    gap_id=ATHENS_FILLED,
                    local_media_path=str(media),
                    media_validation_status="export_ready",
                    cut_plan_run_id=RUN_ID,
                ),
                StockCandidate(
                    candidate_id="c_athens_open",
                    provider="pexels",
                    gap_id=ATHENS_OPEN,
                    cut_plan_run_id=RUN_ID,
                ),
            ],
        ),
    )

    from otio_app.services.without_voiceover_enhanced.gap_status_service import (
        GapStatusSummary,
    )

    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.gap_status_service"
        ".summarize_gap_status",
        lambda _project: GapStatusSummary(
            total=3,
            open_gap_ids=[ATHENS_OPEN, CORFU_OPEN],
            filled_gap_ids=[ATHENS_FILLED],
            cut_plan_run_id=RUN_ID,
        ),
    )
    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.coverage_gap_external_export"
        ".refresh_coverage_gaps_external_export",
        lambda project, coverage=None: None,
    )
    return project


def test_chapter_reset_touches_only_its_own_chapter(project_with_two_chapters):
    project = project_with_two_chapters

    removed = reset_open_gaps_for_chapter(project, "Athens")

    assert removed == [ATHENS_OPEN]
    coverage = load_model(coverage_gaps_path(project), CoverageGapsDocument)
    assert [gap.gap_id for gap in coverage.gaps] == [ATHENS_FILLED, CORFU_OPEN]

    search = load_model(stock_search_results_path(project), StockSearchResultsDocument)
    assert sorted(c.candidate_id for c in search.candidates) == [
        "c_athens_filled",
        "c_corfu_open",
    ]
    funnel = load_model(supplement_funnel_report_path(project), SupplementFunnelReport)
    assert sorted(g.gap_id for g in funnel.gaps) == [ATHENS_FILLED, CORFU_OPEN]


def test_chapter_reset_keeps_the_paid_asset_of_the_same_chapter(project_with_two_chapters):
    project = project_with_two_chapters

    reset_open_gaps_for_chapter(project, "Athens")

    accepted = load_model(
        accepted_supplements_path(project), AcceptedSupplementsDocument
    )
    kept = [s.candidate_id for s in accepted.supplements]
    assert kept == ["c_athens_filled"]
    assert accepted.supplements[0].gap_id == ATHENS_FILLED
    assert Path(accepted.supplements[0].local_media_path).is_file()


def test_persisting_a_new_chapter_plan_resets_open_gaps(project_with_two_chapters, monkeypatch):
    """Der Regelfall: neuer LLM Cut für ein Kapitel räumt automatisch."""
    project = project_with_two_chapters
    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.chapter_cut_service"
        ".refresh_merged_unified_cut_plan",
        lambda _project: None,
    )

    persist_chapter_unified_plan(
        project, "Athens", _plan(["gap_slot_003", "gap_slot_009"])
    )

    coverage = load_model(coverage_gaps_path(project), CoverageGapsDocument)
    assert [gap.gap_id for gap in coverage.gaps] == [ATHENS_FILLED, CORFU_OPEN]
    search = load_model(stock_search_results_path(project), StockSearchResultsDocument)
    assert "c_athens_open" not in [c.candidate_id for c in search.candidates]
    # Der neue Kapitelplan ist geschrieben.
    stored = load_model(
        chapter_unified_cut_plan_path(project, "Athens"), UnifiedCutPlanDocument
    )
    assert [s.coverage_gap_id for s in stored.slots] == [
        "gap_ath_slot_001",
        "gap_ath_slot_002",
    ]


def test_new_cut_still_creates_gaps_after_the_reset(project_with_two_chapters, monkeypatch):
    """Der Reset räumt nur Altzustand — Gaps entstehen weiter aus dem neuen Plan.

    Ohne diese Zusage wäre das Aufräumen schlimmer als das Problem: der Funnel
    hätte nichts mehr zu tun, obwohl Slots ohne passendes Asset existieren.
    """
    from otio_app.services.without_voiceover_enhanced.chapter_cut_service import (
        refresh_merged_unified_cut_plan,
    )
    from otio_app.services.without_voiceover_enhanced.models import (
        EnhancedScriptDocument,
        ScriptSegment,
    )

    project = project_with_two_chapters
    locked = EnhancedScriptDocument(
        script_version="v1",
        script_status="locked",
        segments=[
            ScriptSegment(
                segment_id="s1", folder_name="Athens", text="Athen.", sequence_index=1
            ),
            ScriptSegment(
                segment_id="s2", folder_name="Corfu", text="Korfu.", sequence_index=2
            ),
        ],
    )
    for module in (
        "chapter_cut_service",
        "cut_plan_service",
    ):
        monkeypatch.setattr(
            f"otio_app.services.without_voiceover_enhanced.{module}"
            ".require_locked_script",
            lambda _project, locked=locked: locked,
        )
    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.chapter_cut_service"
        ".list_body_chapter_names",
        lambda _project: ["Athens", "Corfu"],
    )
    # Keine LLM-Runde für Suchbegriffe im Test.
    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.gap_search_concepts"
        ".enrich_coverage_search_concepts",
        lambda project, coverage, plan=None, query_llm=None: coverage,
    )

    # Neuer Athens-Plan: ein neuer Gap und einer mit wiederkehrender ID.
    persist_chapter_unified_plan(
        project,
        "Athens",
        _plan(["gap_slot_001", ATHENS_OPEN]),
        refresh_merged=False,
    )
    refresh_merged_unified_cut_plan(project)

    coverage = load_model(coverage_gaps_path(project), CoverageGapsDocument)
    gap_ids = [gap.gap_id for gap in coverage.gaps]

    # Nach dem Merge heißen Gaps nach dem Slot, nicht nach LLM-Zählern.
    assert "gap_ath_slot_001" in gap_ids
    assert "gap_ath_slot_002" in gap_ids
    assert "gap_cor_slot_001" in gap_ids
    # Corfu-Funnel-Zustand bleibt am alten Eintrag; Coverage folgt der Slot-ID.
    assert CORFU_OPEN not in gap_ids

    search = load_model(stock_search_results_path(project), StockSearchResultsDocument)
    assert "c_athens_open" not in [c.candidate_id for c in search.candidates]
    funnel = load_model(supplement_funnel_report_path(project), SupplementFunnelReport)
    assert ATHENS_OPEN not in [g.gap_id for g in funnel.gaps]
    accepted = load_model(
        accepted_supplements_path(project), AcceptedSupplementsDocument
    )
    assert "c_athens_open" not in [s.candidate_id for s in accepted.supplements]
    # Das bezahlte Asset des erfüllten Gaps bleibt.
    assert "c_athens_filled" in [s.candidate_id for s in accepted.supplements]


def test_reset_can_be_switched_off(project_with_two_chapters, monkeypatch):
    project = project_with_two_chapters
    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.chapter_cut_service"
        ".refresh_merged_unified_cut_plan",
        lambda _project: None,
    )

    persist_chapter_unified_plan(
        project,
        "Athens",
        _plan([ATHENS_OPEN, ATHENS_FILLED]),
        reset_open_gaps=False,
    )

    coverage = load_model(coverage_gaps_path(project), CoverageGapsDocument)
    assert [gap.gap_id for gap in coverage.gaps] == [
        ATHENS_OPEN,
        ATHENS_FILLED,
        CORFU_OPEN,
    ]
