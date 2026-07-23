"""Phase 1: Unified-Cut-Modelle + unified_to_rough-Kompat."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from otio_app.services.without_voiceover_enhanced.models import (
    CutBoundary,
    CutSlot,
    PauseDirective,
    UnifiedCutPlanDocument,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    UNIFIED_CUT_PLAN_FILENAME,
    unified_cut_plan_path,
)
from otio_app.services.without_voiceover_enhanced.unified_cut_plan import (
    segment_id_from_sentence_id,
    unified_to_rough,
)


def _boundary(
    cut_id: str,
    sentence_id: str,
    *,
    position: str | None = "start",
    offset_seconds: float | None = None,
    alignment: str = "sentence_boundary",
) -> CutBoundary:
    return CutBoundary(
        cut_id=cut_id,
        sentence_id=sentence_id,
        position=position,  # type: ignore[arg-type]
        offset_seconds=offset_seconds,
        alignment=alignment,  # type: ignore[arg-type]
    )


def _slot(
    slot_id: str,
    *,
    asset_fit: str = "acceptable",
    local_asset_id: str | None = "asset_1",
    coverage_gap_id: str | None = None,
    needed_visual: str = "",
    target_duration_seconds: float | None = None,
) -> CutSlot:
    return CutSlot(
        slot_id=slot_id,
        local_asset_id=local_asset_id,
        asset_fit=asset_fit,  # type: ignore[arg-type]
        asset_fit_reason="test",
        visual_intent="wide establishing",
        narrative_function="orientation",
        coverage_gap_id=coverage_gap_id,
        needed_visual=needed_visual,
        search_concepts=["cliff village"],
        desired_motion="drone",
        desired_framing="wide",
        target_duration_seconds=target_duration_seconds,
    )


def test_segment_id_from_sentence_id() -> None:
    assert (
        segment_id_from_sentence_id("Rocamadour_segment_001__s011")
        == "Rocamadour_segment_001"
    )


def test_unified_plan_requires_boundary_slot_invariant() -> None:
    with pytest.raises(ValidationError, match="Invariante"):
        UnifiedCutPlanDocument(
            script_version="v1",
            boundaries=[
                _boundary("c1", "A_segment_001__s001", position="start"),
                _boundary("c2", "A_segment_001__s002", position="end"),
            ],
            slots=[],
        )


def test_boundary_requires_position_or_offset() -> None:
    with pytest.raises(ValidationError, match="position oder offset_seconds"):
        CutBoundary(cut_id="c1", sentence_id="A_segment_001__s001")


def test_unified_to_rough_emits_gaps_only_for_weak_and_none() -> None:
    plan = UnifiedCutPlanDocument(
        script_version="script-v1",
        pause_directives=[
            PauseDirective(
                after_segment_id="A_segment_001",
                after_sentence_id="A_segment_001__s003",
                pause_function="chapter_transition",
                duration_class="long",
            )
        ],
        boundaries=[
            _boundary("b0", "A_segment_001__s001", position="start"),
            _boundary("b1", "A_segment_001__s002", position="middle"),
            _boundary("b2", "A_segment_001__s003", position="end"),
            _boundary(
                "b3",
                "A_segment_001__s003",
                position=None,
                offset_seconds=4.5,
                alignment="in_pause",
            ),
        ],
        slots=[
            _slot("A_slot_001", asset_fit="strong", local_asset_id="loc_a"),
            _slot(
                "A_slot_002",
                asset_fit="weak",
                local_asset_id="loc_b",
                coverage_gap_id="A_gap_weak",
                needed_visual="better cliff light",
                target_duration_seconds=8.0,
            ),
            _slot(
                "A_slot_003",
                asset_fit="none",
                local_asset_id=None,
                needed_visual="street detail",
            ),
        ],
    )

    rough, coverage = unified_to_rough(plan)
    assert rough.script_version == "script-v1"
    assert len(rough.shots) == 3
    assert len(rough.pause_directives) == 1
    assert rough.shots[0].coverage_gap_id is None
    assert rough.shots[0].asset_fit == "strong"
    assert rough.shots[0].start_anchor.type == "sentence"
    assert rough.shots[0].start_anchor.sentence_id == "A_segment_001__s001"
    assert rough.shots[1].coverage_gap_id == "A_gap_weak"
    assert rough.shots[2].coverage_gap_id == "gap_A_slot_003"

    # offset gewinnt auf Narration-Bridge
    assert rough.shots[2].narration_end_anchor.offset_seconds == pytest.approx(4.5)
    assert rough.shots[2].start_cut_alignment == "sentence_boundary"
    assert rough.shots[2].end_anchor.position in {"start", "middle", "end", "early", "late"}

    assert len(coverage.gaps) == 2
    by_id = {g.gap_id: g for g in coverage.gaps}
    assert "A_gap_weak" in by_id
    assert "gap_A_slot_003" in by_id
    assert by_id["A_gap_weak"].priority == "medium"
    assert "8.00s" in by_id["A_gap_weak"].reason
    assert by_id["A_gap_weak"].desired_motion == "drone"
    assert by_id["gap_A_slot_003"].priority == "high"
    assert by_id["gap_A_slot_003"].related_shot_ids == ["A_slot_003"]
    assert "A_segment_001__s002" in by_id["A_gap_weak"].covered_sentence_ids


def test_unified_cut_plan_path_name(tmp_path) -> None:
    assert UNIFIED_CUT_PLAN_FILENAME == "unified_cut_plan.json"
    from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
    from otio_app.models import Project, ProjectMode

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
    path = unified_cut_plan_path(project)
    assert path.name == "unified_cut_plan.json"
    assert "cut" in path.parts
