"""E2E-2.1: search_concepts Keywords, nie Prosa."""

from __future__ import annotations

from pathlib import Path

from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.services.without_voiceover_enhanced.gap_search_concepts import (
    enrich_coverage_search_concepts,
    filter_keyword_concepts,
    heuristic_stock_concepts,
    is_prose_search_concept,
    search_concepts_need_regen,
)
from otio_app.services.without_voiceover_enhanced.models import (
    CoverageGap,
    CoverageGapsDocument,
    CutBoundary,
    CutSlot,
    UnifiedCutPlanDocument,
)
from otio_app.services.without_voiceover_enhanced.unified_cut_plan import unified_to_rough


def _project(tmp_path: Path) -> Project:
    root = tmp_path / "proj"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    return Project(
        id="p",
        name="p",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        asset_subdir_names=["Yosemite"],
        selected_asset_subdirs=["Yosemite"],
    )


def test_is_prose_search_concept_rules() -> None:
    assert is_prose_search_concept("")
    assert is_prose_search_concept(
        "a wide establishing shot of the valley at dusk with mist"
    )
    assert is_prose_search_concept("Valley at dusk.")
    assert not is_prose_search_concept("yosemite granite cliff")
    assert not is_prose_search_concept("waterfall mist")


def test_unified_to_rough_drops_prose_concepts() -> None:
    plan = UnifiedCutPlanDocument(
        script_version="v1",
        boundaries=[
            CutBoundary(cut_id="b0", sentence_id="a__s001", position="start"),
            CutBoundary(cut_id="b1", sentence_id="a__s002", position="end"),
        ],
        slots=[
            CutSlot(
                slot_id="slot_none",
                asset_fit="none",
                coverage_gap_id="gap_1",
                needed_visual=(
                    "A cinematic wide shot of Yosemite Valley with morning mist "
                    "rolling over the trees"
                ),
                search_concepts=[
                    "A cinematic wide shot of Yosemite Valley with morning mist",
                    "yosemite valley mist",
                ],
            )
        ],
    )
    _rough, coverage = unified_to_rough(plan)
    assert coverage.gaps[0].needed_visual.startswith("A cinematic")
    assert coverage.gaps[0].search_concepts == ["yosemite valley mist"]
    assert coverage.gaps[0].search_queries == ["yosemite valley mist"]


def test_enrich_replaces_prose_via_query_llm(tmp_path: Path) -> None:
    project = _project(tmp_path)
    plan = UnifiedCutPlanDocument(
        script_version="v1",
        boundaries=[
            CutBoundary(cut_id="b0", sentence_id="a__s001", position="start"),
            CutBoundary(cut_id="b1", sentence_id="a__s002", position="end"),
        ],
        slots=[
            CutSlot(
                slot_id="Yosemite_slot_011",
                asset_fit="weak",
                local_asset_id="loc",
                coverage_gap_id="gap_Yosemite_slot_011",
                needed_visual="Better light on the granite face near the falls",
                search_concepts=[
                    "Better light on the granite face near the falls please"
                ],
            )
        ],
    )
    _rough, coverage = unified_to_rough(plan)
    assert search_concepts_need_regen(coverage.gaps[0].search_concepts)

    def fake_llm(gap, *, folder_name, text):
        return ["yosemite granite face", "yosemite waterfall cliff", "granite sunlit wall"]

    enriched = enrich_coverage_search_concepts(
        project, coverage, plan=plan, query_llm=fake_llm
    )
    assert enriched.gaps[0].search_concepts == [
        "yosemite granite face",
        "yosemite waterfall cliff",
        "granite sunlit wall",
    ]
    assert plan.slots[0].search_concepts == enriched.gaps[0].search_concepts
    # Zweiter Lauf: keine Regen nötig.
    assert not search_concepts_need_regen(enriched.gaps[0].search_concepts)


def test_heuristic_fallback_is_keywordish() -> None:
    concepts = heuristic_stock_concepts(
        needed_visual="Wide aerial of misty forest ridges at dawn.",
        folder_name="Yosemite",
    )
    assert concepts
    assert all(not is_prose_search_concept(c) for c in concepts)
    assert filter_keyword_concepts(concepts) == concepts
