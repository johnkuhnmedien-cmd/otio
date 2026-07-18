"""Deterministic fixtures for Coverage Stability C3.1 (root-cause reproduction).

Documents today's Gap Instance behaviour only — not a product Semantic Gap Key.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fixtures.coverage_stability_c1 import (
    assert_schema_20,
    build_script_ready_project,
    current_audit_id,
    gaps_for_audit,
    install_no_media_io_guards,
    list_all_gaps,
    load_audit,
    run_manual_coverage,
)
from otio_app.discovery_v2.application.coverage_gap_service import (
    accept_gap_unresolved,
    escalate_gap,
    materialize_gaps_from_current_coverage,
)
from otio_app.discovery_v2.application.coverage_idempotency_service import (
    build_current_canonical_coverage,
)
from otio_app.discovery_v2.application.editorial_service import get_editorial_view
from otio_app.discovery_v2.application.script_lock_service import (
    create_script_lock,
    preview_script_lock,
)
from otio_app.discovery_v2.application.supplementation_service import (
    create_graphic_plan,
    record_candidate_decision,
    record_claim_decision,
    start_search_run,
)
from otio_app.discovery_v2.domain.supplementation import (
    CoverageGapStatus,
    CoverageRiskFlag,
    EscalationStep,
    make_lock_risk_confirmation_key,
    persisted_accepted_lock_risk_keys,
)
from otio_app.discovery_v2.persistence import asset_analysis_repository as analysis_repo
from otio_app.discovery_v2.persistence import supplementation_repository as supp_repo
from otio_app.discovery_v2.persistence.supplementation_repository import new_gap_id


RISK_CODE = CoverageRiskFlag.COVERAGE_EXACT_MATCH_NOT_VERIFIED


@dataclass(frozen=True)
class NormalizedGapSemantics:
    """Test-only diagnostic signature — not a released product semantic key."""

    desired_motif: str
    action: str
    setting: str
    geographic_requirements: str | None
    authenticity_requirements: tuple[str, ...]
    allowed_media_kinds: tuple[str, ...]
    coverage_level: str
    missing_properties: tuple[str, ...]
    risk_set: tuple[str, ...]


@dataclass
class MatchShapeFixture:
    """Controlled match-shape documentation without a product match engine."""

    shape: str
    source_gap_ids: list[str]
    target_gap_ids: list[str]
    shared_coarse_signature: dict[str, Any]
    note: str


@dataclass
class GapAHistory:
    gap_id: str
    visual_intent_id: str
    event_ids: list[str]
    event_types: list[str]
    candidate_decision_ids: list[str]
    accepted_unresolved: bool
    graphic_plan_id: str | None
    escalation_step: str
    risk_key: str
    semantics: NormalizedGapSemantics


@dataclass
class AuditPairFixture:
    project: Any
    audit_a_id: str
    audit_b_id: str
    fingerprint_a: str
    fingerprint_b: str
    gap_a: GapAHistory
    gap_b_id: str
    gap_b_semantics: NormalizedGapSemantics
    gaps_a: list[Any]
    gaps_b: list[Any]
    lock_fingerprint_a: str | None
    lock_fingerprint_b: str | None
    shapes: dict[str, MatchShapeFixture] = field(default_factory=dict)
    gap_b_initial_status: Any = None
    gap_b_initial_user_decision: str | None = None
    gap_b_initial_accepted_risks: list[Any] = field(default_factory=list)


def _intent_map(project) -> dict[str, dict[str, Any]]:
    view = get_editorial_view(project)
    assert view.script_bundle is not None
    return {
        str(item["visual_intent_id"]): item
        for item in (view.script_bundle.get("visual_intents") or [])
    }


def normalized_gap_semantics(project, gap) -> NormalizedGapSemantics:
    """Diagnose-only signature (tests/fixtures). Not a product Semantic Gap Key."""

    intent = _intent_map(project).get(gap.visual_intent_id, {})
    risks = sorted(
        {
            *(risk.value for risk in gap.risk_flags),
            *(risk.value for risk in gap.accepted_unresolved_risks),
        }
    )
    return NormalizedGapSemantics(
        desired_motif=str(intent.get("desired_motif") or ""),
        action=str(intent.get("action") or ""),
        setting=str(intent.get("setting") or ""),
        geographic_requirements=(
            None
            if intent.get("geographic_requirements") in (None, "")
            else str(intent.get("geographic_requirements"))
        ),
        authenticity_requirements=tuple(
            sorted(str(v) for v in (intent.get("authenticity_requirements") or []))
        ),
        allowed_media_kinds=tuple(
            sorted(str(v) for v in (intent.get("allowed_media_kinds") or []))
        ),
        coverage_level=gap.coverage_level.value,
        missing_properties=tuple(sorted(str(v) for v in gap.missing_properties)),
        risk_set=tuple(risks),
    )


def coarse_result_signature(semantics: NormalizedGapSemantics) -> tuple[Any, ...]:
    """Intent-agnostic Result/Risk signature (for ambiguity shape fixtures)."""

    return (
        semantics.coverage_level,
        semantics.missing_properties,
        semantics.risk_set,
    )


def coarse_result_signature_dict(semantics: NormalizedGapSemantics) -> dict[str, Any]:
    level, missing, risks = coarse_result_signature(semantics)
    return {
        "coverage_level": level,
        "missing_properties": list(missing),
        "risk_set": list(risks),
    }


def assert_no_semantic_or_predecessor_fields(gap) -> None:
    dumped = gap.model_dump(mode="json")
    for forbidden in (
        "semantic_gap_key",
        "predecessor_gap_id",
        "successor_gap_id",
        "coverage_gap_semantic_key",
    ):
        assert forbidden not in dumped


def assert_gap_id_is_uuid4_generator() -> None:
    source = inspect.getsource(new_gap_id)
    assert "uuid4" in source
    a = new_gap_id()
    b = new_gap_id()
    assert a != b
    assert len(a) == 36 and len(b) == 36


def _mutate_observation_fingerprint(project) -> None:
    """Change Canonical Coverage Input without changing script/structure/intents."""

    conn = analysis_repo.open_analysis_registry(project.project_root_path)
    try:
        observations = analysis_repo.list_visual_observations_for_project(
            conn, project_id=project.id
        )
        assert observations
        obs = observations[0]
        payload = obs.observation_json or "{}"
        mutated = (
            payload[:-1] + ', "c3_1_marker": true}'
            if payload.endswith("}")
            else payload + "c3_1"
        )
        conn.execute(
            """
            UPDATE visual_observations
            SET observation_json = ?
            WHERE observation_id = ?
            """,
            (mutated, obs.observation_id),
        )
        conn.commit()
    finally:
        conn.close()


def _decide_all_claims(project) -> None:
    view = get_editorial_view(project)
    assert view.script is not None and view.script_bundle is not None
    for claim in view.script_bundle["claims"]:
        record_claim_decision(
            project,
            script_id=view.script.script_id,
            claim_id=claim["claim_id"],
            claim_text=claim["statement"],
            decision="confirmed",
        )


def _progress_gap_a(project, gap) -> GapAHistory:
    """Escalate, search, reject candidates, accept_unresolved; optional graphic on sibling."""

    current = gap
    for _ in range(len(EscalationStep)):
        result = escalate_gap(project, gap_id=current.gap_id)
        assert result.ok and result.gap is not None
        current = result.gap
        if current.current_escalation_step == EscalationStep.USER_DECISION:
            break
    assert current.current_escalation_step == EscalationStep.USER_DECISION

    assert start_search_run(project, gap_ids=[current.gap_id], sync=True).started
    conn = supp_repo.open_supplementation_registry(project.project_root_path)
    try:
        candidates = supp_repo.list_stock_candidates_for_gap(conn, gap_id=current.gap_id)
    finally:
        conn.close()
    assert candidates
    decision_ids: list[str] = []
    for candidate in candidates:
        decided = record_candidate_decision(
            project,
            candidate_id=candidate.candidate_id,
            decision="rejected",
            reason="C3.1 fixture reject",
        )
        assert decided.ok
        conn = supp_repo.open_supplementation_registry(project.project_root_path)
        try:
            rows = supp_repo.list_candidate_decisions(conn, gap_id=current.gap_id)
        finally:
            conn.close()
        decision_ids = [row.decision_id for row in rows]

    accepted = accept_gap_unresolved(
        project,
        gap_id=current.gap_id,
        confirmed_risks=[RISK_CODE.value],
        user_confirmed=True,
    )
    assert accepted.ok and accepted.gap is not None
    assert accepted.gap.status == CoverageGapStatus.ACCEPTED_UNRESOLVED

    # Optional graphic plan on a different open gap (does not alter gap A).
    graphic_plan_id: str | None = None
    siblings = [
        item
        for item in materialize_gaps_from_current_coverage(project).gaps
        if item.gap_id != current.gap_id
        and item.status
        not in {
            CoverageGapStatus.ACCEPTED_UNRESOLVED,
            CoverageGapStatus.SUPERSEDED,
            CoverageGapStatus.RESOLVED_BY_GRAPHIC_PLAN,
        }
    ]
    if siblings:
        plan = create_graphic_plan(
            project,
            gap_id=siblings[0].gap_id,
            description="C3.1 fixture graphic plan",
            required_data=["caption"],
            geographic_scope="fixture",
        )
        graphic_plan_id = plan.graphic_plan_id

    conn = supp_repo.open_supplementation_registry(project.project_root_path)
    try:
        events = supp_repo.list_gap_events(conn, gap_id=current.gap_id)
        refreshed = supp_repo.get_coverage_gap(conn, gap_id=current.gap_id)
    finally:
        conn.close()
    assert refreshed is not None
    assert events
    semantics = normalized_gap_semantics(project, refreshed)
    return GapAHistory(
        gap_id=refreshed.gap_id,
        visual_intent_id=refreshed.visual_intent_id,
        event_ids=[event.event_id for event in events],
        event_types=[event.event_type.value for event in events],
        candidate_decision_ids=decision_ids,
        accepted_unresolved=True,
        graphic_plan_id=graphic_plan_id,
        escalation_step=refreshed.current_escalation_step.value,
        risk_key=make_lock_risk_confirmation_key(refreshed.gap_id, RISK_CODE),
        semantics=semantics,
    )


def _lock_fingerprint_if_possible(project) -> str | None:
    preview = preview_script_lock(project)
    return preview.lock_fingerprint


def build_match_shapes(
    *,
    gaps_a: list[Any],
    gaps_b: list[Any],
    semantics_a: dict[str, NormalizedGapSemantics],
    semantics_b: dict[str, NormalizedGapSemantics],
    focus_gap_a_id: str,
) -> dict[str, MatchShapeFixture]:
    """Build 1:1 / 1:N / N:1 / none diagnostic shapes from coarse signatures."""

    coarse_a = {
        gap.gap_id: coarse_result_signature(semantics_a[gap.gap_id]) for gap in gaps_a
    }
    coarse_b = {
        gap.gap_id: coarse_result_signature(semantics_b[gap.gap_id]) for gap in gaps_b
    }
    focus_sig = coarse_a[focus_gap_a_id]
    matching_targets = [
        gap_id for gap_id, sig in coarse_b.items() if sig == focus_sig
    ]
    matching_sources = [
        gap_id for gap_id, sig in coarse_a.items() if sig == focus_sig
    ]
    # Exact intent-aware 1:1: same visual_intent_id across audits.
    intent_pairs = []
    for source in gaps_a:
        for target in gaps_b:
            if source.visual_intent_id == target.visual_intent_id:
                intent_pairs.append((source.gap_id, target.gap_id))
    focus_sig_dict = {
        "coverage_level": focus_sig[0],
        "missing_properties": list(focus_sig[1]),
        "risk_set": list(focus_sig[2]),
    }
    one_to_one = MatchShapeFixture(
        shape="one_to_one",
        source_gap_ids=[intent_pairs[0][0]] if intent_pairs else [focus_gap_a_id],
        target_gap_ids=[intent_pairs[0][1]] if intent_pairs else [],
        shared_coarse_signature=focus_sig_dict,
        note=(
            "Same visual_intent_id across audits with comparable result signature; "
            "product stores no predecessor_gap_id / semantic_gap_key."
        ),
    )
    one_to_many = MatchShapeFixture(
        shape="one_to_many",
        source_gap_ids=[focus_gap_a_id],
        target_gap_ids=matching_targets,
        shared_coarse_signature=focus_sig_dict,
        note=(
            "Coarse Result/Risk signature of one source matches multiple target gaps; "
            "automatic decision would be ambiguous."
        ),
    )
    many_to_one = MatchShapeFixture(
        shape="many_to_one",
        source_gap_ids=matching_sources,
        target_gap_ids=[matching_targets[0]] if matching_targets else [],
        shared_coarse_signature=focus_sig_dict,
        note=(
            "Multiple source gaps share one coarse signature with a target gap; "
            "decisions may conflict — no persisted assignment."
        ),
    )
    # Unmatched: invent a synthetic target signature not present in A.
    signatures_a = set(coarse_a.values())
    unmatched_targets = [
        gap.gap_id for gap in gaps_b if coarse_b[gap.gap_id] not in signatures_a
    ]
    # If FakeText makes all coarse signatures identical, use a dedicated helper gap
    # by picking a target and documenting "no predecessor field" regardless.
    none_targets = unmatched_targets or ([gaps_b[-1].gap_id] if gaps_b else [])
    no_predecessor = MatchShapeFixture(
        shape="none",
        source_gap_ids=[],
        target_gap_ids=none_targets[:1],
        shared_coarse_signature={},
        note=(
            "New gap instance has no persisted predecessor relation; "
            "treated as a normal new instance."
        ),
    )
    return {
        "one_to_one": one_to_one,
        "one_to_many": one_to_many,
        "many_to_one": many_to_one,
        "none": no_predecessor,
    }


def build_audit_pair_fixture(tmp_path: Path, temp_db_path: Path) -> AuditPairFixture:
    """Audit A (progressed) → observation change → Audit B (new gaps, same intent semantics)."""

    project = build_script_ready_project(tmp_path, temp_db_path)
    assert_schema_20(project)
    first = run_manual_coverage(project, sync=True)
    assert first.started and not first.reused
    audit_a_id = current_audit_id(project)
    fingerprint_a = build_current_canonical_coverage(project).fingerprint
    assert fingerprint_a

    materialize = materialize_gaps_from_current_coverage(project)
    assert materialize.ok and materialize.gaps
    gaps_a_initial = sorted(materialize.gaps, key=lambda gap: gap.visual_intent_id)
    assert len(gaps_a_initial) >= 3

    history = _progress_gap_a(project, gaps_a_initial[0])
    gaps_a = gaps_for_audit(project, audit_a_id)
    assert any(gap.gap_id == history.gap_id for gap in gaps_a)

    # Lock fingerprint while Audit A is current (after accept on gap A).
    # Other open gaps may block lock; capture fingerprint only when preview yields one.
    _decide_all_claims(project)
    # Resolve remaining non-terminal gaps locally so lock preview can succeed.
    from otio_app.discovery_v2.application.coverage_gap_service import (
        mark_gap_resolved_with_local_asset,
    )

    for gap in materialize_gaps_from_current_coverage(project).gaps:
        if gap.gap_id == history.gap_id:
            continue
        if gap.status in {
            CoverageGapStatus.ACCEPTED_UNRESOLVED,
            CoverageGapStatus.RESOLVED_BY_GRAPHIC_PLAN,
            CoverageGapStatus.SUPERSEDED,
        }:
            continue
        assert mark_gap_resolved_with_local_asset(
            project, gap_id=gap.gap_id, asset_id="asset-local-c3-1"
        ).ok
    preview_a = preview_script_lock(project)
    lock_fingerprint_a = preview_a.lock_fingerprint
    if preview_a.ok and lock_fingerprint_a and preview_a.accepted_open_risks:
        confirmations = {key: True for key in preview_a.accepted_open_risks}
        locked = create_script_lock(
            project,
            user_confirmed=True,
            confirmed_fingerprint=lock_fingerprint_a,
            accepted_unresolved_risk_confirmations=confirmations,
        )
        assert locked.ok, locked.message

    # Audit B: fachlich neuer Canonical Input (Observation), gleiche Intent-Struktur.
    _mutate_observation_fingerprint(project)
    fingerprint_b = build_current_canonical_coverage(project).fingerprint
    assert fingerprint_b
    assert fingerprint_a != fingerprint_b

    second = run_manual_coverage(project, sync=True)
    assert second.started and not second.reused
    audit_b_id = current_audit_id(project)
    assert audit_b_id != audit_a_id

    materialize_b = materialize_gaps_from_current_coverage(project)
    assert materialize_b.ok and materialize_b.gaps
    gaps_b = sorted(
        gaps_for_audit(project, audit_b_id), key=lambda gap: gap.visual_intent_id
    )
    assert gaps_b

    # Successor candidate: same visual_intent_id as gap A.
    gap_b = next(
        gap for gap in gaps_b if gap.visual_intent_id == history.visual_intent_id
    )
    gap_b_id = gap_b.gap_id
    gap_b_semantics = normalized_gap_semantics(project, gap_b)
    assert gap_b_semantics.coverage_level == history.semantics.coverage_level
    assert gap_b_semantics.missing_properties == history.semantics.missing_properties
    assert gap_b_semantics.desired_motif == history.semantics.desired_motif

    semantics_a = {gap.gap_id: normalized_gap_semantics(project, gap) for gap in gaps_a}
    semantics_b = {gap.gap_id: normalized_gap_semantics(project, gap) for gap in gaps_b}
    shapes = build_match_shapes(
        gaps_a=gaps_a,
        gaps_b=gaps_b,
        semantics_a=semantics_a,
        semantics_b=semantics_b,
        focus_gap_a_id=history.gap_id,
    )

    # Snapshot B instance state before terminalization (for accepted_unresolved proof).
    gap_b_initial_status = gap_b.status
    gap_b_initial_user_decision = gap_b.user_decision
    gap_b_initial_accepted_risks = list(gap_b.accepted_unresolved_risks)

    # Terminalize Audit B gaps locally so a Lock-Fingerprint exists for comparison.
    # Resolve (not accept_unresolved) so successor stays free of A's decision type.
    from otio_app.discovery_v2.application.coverage_gap_service import (
        mark_gap_resolved_with_local_asset,
    )

    for gap in gaps_for_audit(project, audit_b_id):
        if gap.status in {
            CoverageGapStatus.ACCEPTED_UNRESOLVED,
            CoverageGapStatus.RESOLVED_BY_GRAPHIC_PLAN,
            CoverageGapStatus.SUPERSEDED,
            CoverageGapStatus.RESOLVED_WITH_LOCAL_ASSET,
        }:
            continue
        assert mark_gap_resolved_with_local_asset(
            project, gap_id=gap.gap_id, asset_id="asset-local-c3-1-b"
        ).ok
    lock_fingerprint_b = _lock_fingerprint_if_possible(project)
    assert lock_fingerprint_a and lock_fingerprint_b
    assert lock_fingerprint_a != lock_fingerprint_b

    return AuditPairFixture(
        project=project,
        audit_a_id=audit_a_id,
        audit_b_id=audit_b_id,
        fingerprint_a=fingerprint_a or "",
        fingerprint_b=fingerprint_b or "",
        gap_a=history,
        gap_b_id=gap_b_id,
        gap_b_semantics=gap_b_semantics,
        gaps_a=gaps_a,
        gaps_b=gaps_b,  # pre-terminalization instances for identity proofs
        lock_fingerprint_a=lock_fingerprint_a,
        lock_fingerprint_b=lock_fingerprint_b,
        shapes=shapes,
        gap_b_initial_status=gap_b_initial_status,
        gap_b_initial_user_decision=gap_b_initial_user_decision,
        gap_b_initial_accepted_risks=gap_b_initial_accepted_risks,
    )


__all__ = [
    "AuditPairFixture",
    "GapAHistory",
    "MatchShapeFixture",
    "NormalizedGapSemantics",
    "RISK_CODE",
    "assert_gap_id_is_uuid4_generator",
    "assert_no_semantic_or_predecessor_fields",
    "build_audit_pair_fixture",
    "build_match_shapes",
    "coarse_result_signature",
    "install_no_media_io_guards",
    "normalized_gap_semantics",
]
