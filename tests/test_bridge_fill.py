"""E2E-4: Bridges entfernt — Legacy-Erkennung + keine Coverage-Gaps."""

from __future__ import annotations

from otio_app.services.without_voiceover_enhanced.gap_merge_service import (
    fill_chapter_bridges,
    is_bridge_shot,
)
from otio_app.services.without_voiceover_enhanced.models import (
    CutBoundary,
    CutSlot,
    GapMergeReport,
    ResolvedShot,
    ResolvedTimelineDocument,
    UnifiedCutPlanDocument,
)
from otio_app.services.without_voiceover_enhanced.unified_cut_plan import unified_to_rough


def test_unified_to_rough_excludes_legacy_bridge_from_coverage_gaps() -> None:
    plan = UnifiedCutPlanDocument(
        script_version="v1",
        boundaries=[
            CutBoundary(cut_id="b0", sentence_id="a__s001", position="start"),
            CutBoundary(cut_id="b1", sentence_id="a__s002", position="end"),
            CutBoundary(cut_id="b2", sentence_id="b__s001", position="start"),
            CutBoundary(cut_id="b3", sentence_id="b__s002", position="end"),
        ],
        slots=[
            CutSlot(
                slot_id="A_slot_001",
                local_asset_id="asset_a",
                asset_fit="strong",
            ),
            CutSlot(
                slot_id="bridge_001",
                asset_fit="none",
                narrative_function="chapter_transition",
                needed_visual="chapter transition",
            ),
            CutSlot(
                slot_id="B_slot_001",
                local_asset_id=None,
                asset_fit="none",
                coverage_gap_id="gap_b",
                needed_visual="lake detail",
            ),
        ],
    )
    _rough, coverage = unified_to_rough(plan)
    gap_ids = [g.gap_id for g in coverage.gaps]
    assert "gap_b" in gap_ids
    assert not any("bridge" in g for g in gap_ids)


def test_is_bridge_shot_detection() -> None:
    assert is_bridge_shot(
        ResolvedShot(
            shot_id="bridge_001",
            asset_id="",
            timeline_start_seconds=0,
            timeline_end_seconds=1,
            source_start_seconds=0,
            source_end_seconds=1,
        )
    )
    assert is_bridge_shot(
        ResolvedShot(
            shot_id="x",
            asset_id="",
            timeline_start_seconds=0,
            timeline_end_seconds=1,
            source_start_seconds=0,
            source_end_seconds=1,
            editorial_function="chapter_transition",
        )
    )
    assert not is_bridge_shot(
        ResolvedShot(
            shot_id="A_slot_001",
            asset_id="a",
            timeline_start_seconds=0,
            timeline_end_seconds=1,
            source_start_seconds=0,
            source_end_seconds=1,
        )
    )


def test_fill_chapter_bridges_is_noop_strips_bridges() -> None:
    """E2E-4: Bridge-Fill-Pfad entfernt — Funktion filtert nur noch Legacy-Slots."""
    timeline = ResolvedTimelineDocument(
        script_version="v1",
        fps=25.0,
        total_duration_seconds=10.0,
        shots=[
            ResolvedShot(
                shot_id="A_slot_001",
                asset_id="a",
                timeline_start_seconds=0,
                timeline_end_seconds=5,
                source_start_seconds=0,
                source_end_seconds=5,
            ),
            ResolvedShot(
                shot_id="bridge_001",
                asset_id="",
                timeline_start_seconds=5,
                timeline_end_seconds=10,
                source_start_seconds=0,
                source_end_seconds=0,
                editorial_function="chapter_transition",
                open_gap=True,
            ),
        ],
    )
    report = GapMergeReport(script_version="v1")
    out = fill_chapter_bridges(
        project=None,  # type: ignore[arg-type]
        timeline=timeline,
        unified=None,
        catalog=None,
        options=None,
        repairs=[],
        report=report,
    )
    assert [s.shot_id for s in out] == ["A_slot_001"]
