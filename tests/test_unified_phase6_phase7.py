"""Phase 6–7: Rhythm-QS, Mini-Repair-Gate, Cut-Plan-Modus."""

from __future__ import annotations

from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
    CUT_PLAN_MODE_LEGACY,
    CUT_PLAN_MODE_UNIFIED,
    CutPlanOptions,
    default_cut_plan_options,
    load_cut_plan_options,
    save_cut_plan_options,
)
from otio_app.services.without_voiceover_enhanced.cut_rhythm_validator import (
    assess_unified_cut_quality,
    merge_report_repair_ratio,
    should_run_unified_mini_repair,
)
from otio_app.services.without_voiceover_enhanced.models import (
    CutBoundary,
    CutSlot,
    GapMergeReport,
    GapMergeSlotResult,
    ResolvedShot,
    ResolvedTimelineDocument,
    UnifiedCutPlanDocument,
)
from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode


def test_should_run_mini_repair_default_off_and_threshold() -> None:
    report = GapMergeReport(
        script_version="v1",
        open_none_gap_ids=["g1", "g2"],
        review_shot_ids=["s1"],
    )
    # 3/10 = 0.30 > 0.20
    assert merge_report_repair_ratio(report, total_slots=10) == 0.3
    assert should_run_unified_mini_repair(report, total_slots=10, enabled=False) is False
    assert should_run_unified_mini_repair(report, total_slots=10, enabled=True) is True
    # 2/10 = 0.20 → nicht größer als Schwellwert
    report2 = GapMergeReport(
        script_version="v1",
        open_none_gap_ids=["g1"],
        review_shot_ids=["s1"],
    )
    assert should_run_unified_mini_repair(report2, total_slots=10, enabled=True) is False
    assert should_run_unified_mini_repair(
        report2, total_slots=10, enabled=True, threshold=0.19
    ) is True


def test_assess_unified_cut_quality_notes_alignment_and_open_gaps() -> None:
    plan = UnifiedCutPlanDocument(
        script_version="v1",
        boundaries=[
            CutBoundary(
                cut_id="b0",
                sentence_id="seg_001__s001",
                position="start",
                alignment="sentence_boundary",
            ),
            CutBoundary(
                cut_id="b1",
                sentence_id="seg_001__s002",
                position="middle",
                alignment="sentence_boundary",
            ),
            CutBoundary(
                cut_id="b2",
                sentence_id="seg_001__s003",
                position="end",
                alignment="sentence_boundary",
            ),
            CutBoundary(
                cut_id="b3",
                sentence_id="seg_001__s004",
                position="end",
                alignment="sentence_boundary",
            ),
            CutBoundary(
                cut_id="b4",
                sentence_id="seg_001__s005",
                position="end",
                alignment="sentence_boundary",
            ),
        ],
        slots=[
            CutSlot(slot_id="s1", asset_fit="strong", local_asset_id="a"),
            CutSlot(slot_id="s2", asset_fit="strong", local_asset_id="b"),
            CutSlot(slot_id="s3", asset_fit="none", local_asset_id=None),
            CutSlot(slot_id="s4", asset_fit="strong", local_asset_id="c"),
        ],
    )
    resolved = ResolvedTimelineDocument(
        script_version="v1",
        fps=25.0,
        total_duration_seconds=40.0,
        shots=[
            ResolvedShot(
                shot_id="s1",
                asset_id="a",
                timeline_start_seconds=0.0,
                timeline_end_seconds=12.0,
                source_start_seconds=0.0,
                source_end_seconds=12.0,
            ),
            ResolvedShot(
                shot_id="s2",
                asset_id="b",
                timeline_start_seconds=12.0,
                timeline_end_seconds=24.0,
                source_start_seconds=0.0,
                source_end_seconds=12.0,
            ),
            ResolvedShot(
                shot_id="s3",
                asset_id="",
                timeline_start_seconds=24.0,
                timeline_end_seconds=30.0,
                source_start_seconds=0.0,
                source_end_seconds=6.0,
                open_gap=True,
                coverage_gap_id="g3",
                asset_fit="none",
            ),
            ResolvedShot(
                shot_id="s4",
                asset_id="c",
                timeline_start_seconds=30.0,
                timeline_end_seconds=40.0,
                source_start_seconds=0.0,
                source_end_seconds=10.0,
            ),
        ],
    )
    assessment = assess_unified_cut_quality(
        plan=plan,
        resolved=resolved,
        options=CutPlanOptions(shot_min_sec=5.0, shot_max_sec=17.0),
    )
    notes = assessment.all_notes()
    assert any("mid_sentence" in n for n in notes)
    assert any("offene Gap" in n for n in notes)


def test_cut_plan_mode_options_roundtrip(tmp_path) -> None:
    root = tmp_path / "proj"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    project = Project(
        id="p",
        name="p",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        asset_subdir_names=["A"],
        selected_asset_subdirs=["A"],
    )
    defaults = default_cut_plan_options()
    assert defaults.cut_plan_mode == CUT_PLAN_MODE_LEGACY
    assert defaults.enable_unified_mini_repair is False

    saved = save_cut_plan_options(
        project,
        CutPlanOptions(
            cut_plan_mode=CUT_PLAN_MODE_UNIFIED,
            enable_unified_mini_repair=True,
            unified_mini_repair_threshold=0.25,
        ),
    )
    loaded = load_cut_plan_options(project)
    assert loaded.cut_plan_mode == CUT_PLAN_MODE_UNIFIED
    assert loaded.enable_unified_mini_repair is True
    assert loaded.unified_mini_repair_threshold == 0.25
    from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
        CUT_PLAN_OPTIONS_SCHEMA_VERSION,
    )

    assert saved.schema_version == CUT_PLAN_OPTIONS_SCHEMA_VERSION


def test_merge_report_slot_result_shape() -> None:
    report = GapMergeReport(
        script_version="v1",
        slots=[
            GapMergeSlotResult(
                shot_id="s1",
                coverage_gap_id="g1",
                status="open_none",
            )
        ],
        open_none_gap_ids=["g1"],
    )
    assert should_run_unified_mini_repair(
        report, total_slots=4, enabled=True, threshold=0.20
    ) is True


def test_generate_unified_cut_plan_does_not_resolve_timeline(monkeypatch) -> None:
    """LLM-Schritt persistiert nur den Plan — kein Python-Timing."""
    from otio_app.services.without_voiceover_enhanced import cut_plan_service as svc

    plan = UnifiedCutPlanDocument(script_version="v1", slots=[], boundaries=[])
    calls = {"resolve": 0}

    monkeypatch.setattr(
        svc,
        "generate_all_unified_cuts",
        lambda *a, **k: ["ok"],
    )
    monkeypatch.setattr(
        svc,
        "merge_and_persist_unified_cuts",
        lambda project, results: plan,
    )

    def _boom(*_a, **_k):
        calls["resolve"] += 1
        raise AssertionError("resolve must not run in LLM-only step")

    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.unified_timeline_service."
        "resolve_unified_timeline",
        _boom,
    )

    out = svc.generate_unified_cut_plan(project=None)  # type: ignore[arg-type]
    assert out is plan
    assert calls["resolve"] == 0


def test_resolve_unified_cut_plan_timeline_requires_saved_plan(tmp_path) -> None:
    from otio_app.services.without_voiceover_enhanced.cut_plan_service import (
        CutPlanError,
        resolve_unified_cut_plan_timeline,
    )

    work = tmp_path / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    project = Project(
        id="p",
        name="p",
        project_root=str(tmp_path),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        asset_subdir_names=["A"],
        selected_asset_subdirs=["A"],
    )
    try:
        resolve_unified_cut_plan_timeline(project)
        raise AssertionError("expected CutPlanError")
    except CutPlanError as exc:
        assert "fehlt" in str(exc).lower()
