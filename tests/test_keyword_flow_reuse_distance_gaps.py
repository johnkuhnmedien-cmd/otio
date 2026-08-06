"""Keyword Flow: Früh-Reuse / Max-Usage → Coverage-Gap statt stiller Verletzung."""

from __future__ import annotations

from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
    CutPlanOptions,
    format_shot_constraints_for_prompt,
)
from otio_app.services.without_voiceover_enhanced.models import (
    CutBoundary,
    CutSlot,
    UnifiedCutPlanDocument,
)
from otio_app.services.without_voiceover_enhanced.script_prompts import (
    build_keyword_flow_unified_cut_prompt,
)
from otio_app.services.without_voiceover_enhanced.unified_cut_plan import (
    enforce_asset_reuse_as_coverage_gaps,
    unified_to_rough,
)


def _bound(cut_id: str, sentence_id: str, position: str) -> CutBoundary:
    return CutBoundary(
        cut_id=cut_id,
        sentence_id=sentence_id,
        position=position,
        alignment="sentence_boundary",
    )


def _slot(slot_id: str, asset_id: str | None, fit: str = "strong") -> CutSlot:
    return CutSlot(
        slot_id=slot_id,
        local_asset_id=asset_id,
        asset_fit=fit,  # type: ignore[arg-type]
        visual_intent=f"visual for {slot_id}",
        narrative_function="orientation",
    )


def _plan(assets: list[str | None]) -> UnifiedCutPlanDocument:
    n = len(assets)
    bounds = [
        _bound(f"cut_{i:03d}", "Chap_seg__s001", "start" if i == 0 else "middle")
        for i in range(n)
    ]
    bounds.append(_bound(f"cut_{n:03d}", "Chap_seg__s001", "end"))
    slots = [
        _slot(f"slot_{i:03d}", asset) if asset else _slot(f"slot_{i:03d}", None, "none")
        for i, asset in enumerate(assets)
    ]
    return UnifiedCutPlanDocument(
        script_version="v1",
        boundaries=bounds,
        slots=slots,
        closing_fallback_asset_id="fb_01",
        closing_fallback_asset_fit="strong",
        closing_fallback_asset_fit_reason="reserve closer",
        closing_fallback_visual_intent="closing landscape",
    )


def test_consecutive_reuse_becomes_coverage_gap() -> None:
    plan = _plan(["a_01", "a_01", "b_01"])
    out, notes = enforce_asset_reuse_as_coverage_gaps(
        plan,
        max_asset_usage=2,
        min_asset_reuse_distance_shots=4,
    )
    assert out.slots[0].local_asset_id == "a_01"
    assert out.slots[1].local_asset_id is None
    assert out.slots[1].asset_fit == "none"
    assert out.slots[1].coverage_gap_id
    assert out.slots[1].search_concepts
    assert out.slots[2].local_asset_id == "b_01"
    assert notes
    _, coverage = unified_to_rough(out)
    assert any(g.gap_id == out.slots[1].coverage_gap_id for g in coverage.gaps)


def test_early_reuse_within_distance_becomes_gap() -> None:
    # a … b … a with only 1 shot between; min distance 4 → second a demoted
    plan = _plan(["a_01", "b_01", "a_01", "c_01", "d_01", "e_01"])
    out, notes = enforce_asset_reuse_as_coverage_gaps(
        plan,
        max_asset_usage=3,
        min_asset_reuse_distance_shots=4,
    )
    assert out.slots[0].local_asset_id == "a_01"
    assert out.slots[2].local_asset_id is None
    assert out.slots[2].asset_fit == "none"
    assert any("min Abstand 4" in n for n in notes)


def test_reuse_after_enough_distance_kept() -> None:
    plan = _plan(["a_01", "b_01", "c_01", "d_01", "e_01", "a_01"])
    out, notes = enforce_asset_reuse_as_coverage_gaps(
        plan,
        max_asset_usage=2,
        min_asset_reuse_distance_shots=4,
    )
    assert out.slots[5].local_asset_id == "a_01"
    assert out.slots[5].asset_fit == "strong"
    assert notes == []


def test_open_gap_counts_as_distance_separator() -> None:
    # a, gap, a — one separator; with min=1 consecutive ban only, second a OK
    plan = _plan(["a_01", None, "a_01"])
    out, notes = enforce_asset_reuse_as_coverage_gaps(
        plan,
        max_asset_usage=2,
        min_asset_reuse_distance_shots=0,  # still enforces min_gap=1 (no direct)
    )
    assert out.slots[2].local_asset_id == "a_01"
    assert notes == []

    # with min=4, one separator is not enough (disable closing fallback swap)
    out2, notes2 = enforce_asset_reuse_as_coverage_gaps(
        plan,
        max_asset_usage=2,
        min_asset_reuse_distance_shots=4,
        prefer_closing_fallback=False,
    )
    assert out2.slots[2].local_asset_id is None
    assert notes2


def test_max_usage_becomes_gap() -> None:
    plan = _plan(["a_01", "b_01", "c_01", "d_01", "e_01", "a_01"])
    out, notes = enforce_asset_reuse_as_coverage_gaps(
        plan,
        max_asset_usage=1,
        min_asset_reuse_distance_shots=4,
        prefer_closing_fallback=False,
    )
    assert out.slots[5].local_asset_id is None
    assert out.slots[5].asset_fit == "none"
    assert any("max_asset_usage=1" in n for n in notes)


def test_prior_chapter_usage_and_distance_seed() -> None:
    plan = _plan(["a_01", "b_01"])
    out, notes = enforce_asset_reuse_as_coverage_gaps(
        plan,
        max_asset_usage=1,
        min_asset_reuse_distance_shots=4,
        prior_usage_counts={"a_01": 1},
        prior_editorial_asset_ids=["a_01", "x_01"],
    )
    assert out.slots[0].local_asset_id is None
    assert any("max_asset_usage" in n for n in notes)


def test_closing_fallback_preferred_over_gap() -> None:
    plan = _plan(["a_01", "b_01", "a_01"])
    plan = plan.model_copy(
        update={
            "closing_fallback_asset_id": "fb_01",
            "closing_fallback_asset_fit": "strong",
        }
    )
    out, notes = enforce_asset_reuse_as_coverage_gaps(
        plan,
        max_asset_usage=2,
        min_asset_reuse_distance_shots=4,
        prefer_closing_fallback=True,
    )
    assert out.slots[2].local_asset_id == "fb_01"
    assert out.slots[2].asset_fit == "strong"
    assert any("Fallback" in n for n in notes)


def test_keyword_flow_prompt_mentions_reuse_gap_demote() -> None:
    prompt = build_keyword_flow_unified_cut_prompt(
        locked_script_json="{}",
        segment_timings_json="{}",
        local_assets_json="[]",
        style_profile_text="style",
        dramaturgy_text="dram",
        folder_name="Cliffs",
        folder_slug="Cliffs",
        shot_constraints_text=format_shot_constraints_for_prompt(CutPlanOptions()),
    )
    assert "honest coverage gap" in prompt or "Coverage-Gap" in prompt or "coverage gap" in prompt.lower()
    assert "demote" in prompt.lower() or "illegal early reuses" in prompt.lower()
    constraints = format_shot_constraints_for_prompt(CutPlanOptions())
    assert "emit asset_fit" in constraints or "coverage_gap" in constraints
