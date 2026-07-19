"""Deterministic fixtures for Script-Lock Current-State L1 (reproduction only).

Documents today's deadlock / fallback behaviour. Does NOT implement an L2
effective-lock resolver.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fixtures.coverage_stability_c1 import assert_schema_20, install_no_media_io_guards
from otio_app.discovery_v2.application.coverage_gap_service import (
    accept_gap_unresolved,
    escalate_gap,
    mark_gap_resolved_with_local_asset,
    materialize_gaps_from_current_coverage,
)
from otio_app.discovery_v2.application.editorial_service import (
    get_editorial_view,
    save_project_brief,
    save_user_script_edit,
    select_hook,
    start_coverage_run,
    start_narrative_run,
    start_script_run,
    start_structure_run,
)
from otio_app.discovery_v2.application.narration_timing_service import (
    start_narration_timing_run,
)
from otio_app.discovery_v2.application.observation_review_service import (
    list_editorial_ready_observations,
    submit_observation_review,
)
from otio_app.discovery_v2.application.pause_direction_service import (
    start_pause_direction_run,
)
from otio_app.discovery_v2.application.script_lock_service import (
    create_script_lock,
    get_effective_script_lock,
    preview_script_lock,
)
from otio_app.discovery_v2.application.supplementation_service import (
    record_candidate_decision,
    record_claim_decision,
    start_search_run,
)
from otio_app.discovery_v2.application.voice_generation_service import (
    start_voice_generation_run,
)
from otio_app.discovery_v2.adapters.text_config import load_text_config
from otio_app.discovery_v2.domain.editorial import (
    GATEWAY_VERSION,
    EditorialProjectStateStatus,
    ScriptDraft,
    ScriptDraftStatus,
    ScriptSourceKind,
    compute_observation_set_fingerprint,
    compute_text_sha256,
)
from otio_app.discovery_v2.domain.supplementation import (
    CoverageGapStatus,
    CoverageRiskFlag,
    EscalationStep,
    ScriptLockStatus,
    persisted_accepted_lock_risk_keys,
)
from otio_app.discovery_v2.persistence import editorial_repository as editorial_repo
from otio_app.discovery_v2.persistence import narration_repository as narration_repo
from otio_app.discovery_v2.persistence import supplementation_repository as supp_repo
from otio_app.discovery_v2.persistence.asset_registry_database import (
    REGISTRY_SCHEMA_VERSION,
)

from test_discovery_v2_script_lock import _script_coverage_project

RISK_CODE = CoverageRiskFlag.COVERAGE_EXACT_MATCH_NOT_VERIFIED

# Identity dimensions a historical lock can diverge on (diagnostic only).
LOCK_IDENTITY_FIELDS = (
    "script_id",
    "script_version",
    "narrative_plan_id",
    "selected_hook_id",
    "coverage_audit_id",
    "observation_fingerprint",
    "script_lock_fingerprint",
    "risk_confirmation_set",
)


@dataclass(frozen=True)
class EditorialIdentitySnapshot:
    """Current editorial identity — test diagnosis only, not a product resolver."""

    script_id: str | None
    script_version: int | None
    narrative_plan_id: str | None
    selected_hook_id: str | None
    coverage_audit_id: str | None
    observation_fingerprint: str | None
    script_lock_fingerprint: str | None
    risk_confirmation_set: tuple[str, ...]
    editorial_current_script_lock_id: str | None


@dataclass
class FixtureADeadlock:
    """USA_v2-shaped deadlock in a temporary project (no user registry)."""

    project: Any
    lock_a: Any
    script_a_id: str
    script_a_version: int
    narrative_a_id: str
    hook_a_id: str
    coverage_audit_a_id: str
    observation_fingerprint_a: str
    lock_fingerprint_a: str
    risk_keys_a: tuple[str, ...]
    script_b_id: str
    script_b_version: int
    narrative_b_id: str
    hook_b_id: str
    coverage_audit_b_id: str
    observation_fingerprint_b: str
    preview_fingerprint_b: str | None
    editorial_current_script_lock_id: str | None
    narration_current_script_lock_id: str | None
    voice_run_id: str | None = None
    pause_plan_id: str | None = None
    timeline_id: str | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class FixtureBLatestFallback:
    """Pointer NULL + matching latest locked row (unsafe fallback surface)."""

    project: Any
    lock: Any
    editorial_current_script_lock_id: str | None
    latest_locked_id: str


@dataclass
class FixtureCStaleNarrationPointer:
    """Editorial pointer cleared by invalidation; narration pointer stays stale."""

    project: Any
    lock: Any
    invalidation_path: str
    editorial_current_script_lock_id: str | None
    narration_current_script_lock_id: str | None
    lock_status_after: str


def _now() -> datetime:
    return datetime.now(timezone.utc)


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


def _resolve_all_gaps_locally(project) -> list:
    gaps = materialize_gaps_from_current_coverage(project).gaps
    for gap in gaps:
        if gap.status in {
            CoverageGapStatus.RESOLVED_WITH_LOCAL_ASSET,
            CoverageGapStatus.RESOLVED_WITH_SUPPLEMENT,
            CoverageGapStatus.RESOLVED_BY_SCRIPT_REVISION,
            CoverageGapStatus.RESOLVED_BY_GRAPHIC_PLAN,
            CoverageGapStatus.ACCEPTED_UNRESOLVED,
            CoverageGapStatus.SUPERSEDED,
        }:
            continue
        mark_gap_resolved_with_local_asset(
            project,
            gap_id=gap.gap_id,
            asset_id="asset-local",
        )
    return materialize_gaps_from_current_coverage(project).gaps


def _accept_one_gap_unresolved(project) -> str:
    """Escalate + search-reject + accept_unresolved on first gap; resolve others locally."""

    gaps = materialize_gaps_from_current_coverage(project).gaps
    assert gaps
    target = gaps[0]
    current = target
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
    for candidate in candidates:
        assert record_candidate_decision(
            project,
            candidate_id=candidate.candidate_id,
            decision="rejected",
            reason="L1 fixture reject",
        ).ok
    accepted = accept_gap_unresolved(
        project,
        gap_id=current.gap_id,
        confirmed_risks=[RISK_CODE.value],
        user_confirmed=True,
    )
    assert accepted.ok
    for gap in gaps[1:]:
        mark_gap_resolved_with_local_asset(
            project,
            gap_id=gap.gap_id,
            asset_id="asset-local",
        )
    risk_key = f"{current.gap_id}:{RISK_CODE.value}"
    return risk_key


def _create_lock(
    project,
    *,
    accepted_unresolved_risk_confirmations: dict[str, bool] | None = None,
):
    preview = preview_script_lock(project)
    assert preview.ok and preview.lock_fingerprint, preview.blockers
    result = create_script_lock(
        project,
        user_confirmed=True,
        confirmed_fingerprint=preview.lock_fingerprint,
        accepted_unresolved_risk_confirmations=accepted_unresolved_risk_confirmations,
    )
    assert result.ok and result.lock is not None, result.message
    assert result.lock.status == ScriptLockStatus.LOCKED
    return result.lock


def clear_editorial_current_script_lock_pointer(project) -> None:
    """Clear editorial pointer via public repository upsert — does not invalidate lock."""

    conn = supp_repo.open_supplementation_registry(project.project_root_path)
    try:
        state = editorial_repo.get_project_state(conn, project_id=project.id)
        assert state is not None
        editorial_repo.upsert_project_state(
            conn,
            state.model_copy(
                update={
                    "current_script_lock_id": None,
                    "updated_at": _now(),
                }
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _restamp_stale_narration_pointer(
    project,
    *,
    script_lock_id: str,
    voice_run_id: str | None = None,
    pause_plan_id: str | None = None,
    timeline_id: str | None = None,
) -> None:
    """Fixture-only: restore a stale Narration current pointer after L4 clears."""

    from otio_app.discovery_v2.domain.narration import NarrationProjectState

    conn = narration_repo.open_narration_registry(project.project_root_path)
    try:
        state = narration_repo.get_project_state(conn, project_id=project.id)
        if state is None:
            state = NarrationProjectState(project_id=project.id, updated_at=_now())
        narration_repo.upsert_project_state(
            conn,
            state.model_copy(
                update={
                    "current_script_lock_id": script_lock_id,
                    "current_voice_run_id": voice_run_id or state.current_voice_run_id,
                    "current_pause_plan_id": pause_plan_id or state.current_pause_plan_id,
                    "current_timeline_id": timeline_id or state.current_timeline_id,
                    "updated_at": _now(),
                }
            ),
        )
        conn.commit()
    finally:
        conn.close()


def restamp_editorial_current_script_lock_pointer(
    project,
    *,
    lock_id: str,
    restore_locked_status: bool = True,
) -> None:
    """Test-only: restore Editorial current pointer for read-only mismatch proofs.

    L4 fachlich invalidates the pointed lock (status → invalidated) and clears
    the pointer. Resolver identity/fingerprint proofs need the pointer back on
    a ``locked`` row; restore that status when ``restore_locked_status`` is set.
    """

    conn = editorial_repo.open_editorial_registry(project.project_root_path)
    try:
        state = editorial_repo.get_project_state(conn, project_id=project.id)
        assert state is not None
        editorial_repo.upsert_project_state(
            conn,
            state.model_copy(
                update={
                    "current_script_lock_id": lock_id,
                    "updated_at": _now(),
                }
            ),
        )
        if restore_locked_status:
            lock = supp_repo.get_script_lock(conn, lock_id=lock_id)
            if lock is not None and lock.status != ScriptLockStatus.LOCKED:
                supp_repo.update_script_lock_status(
                    conn,
                    lock_id=lock_id,
                    status=ScriptLockStatus.LOCKED,
                )
        conn.commit()
    finally:
        conn.close()


def read_editorial_current_script_lock_id(project) -> str | None:
    conn = editorial_repo.open_editorial_registry(project.project_root_path)
    try:
        state = editorial_repo.get_project_state(conn, project_id=project.id)
    finally:
        conn.close()
    return None if state is None else state.current_script_lock_id


def read_narration_current_script_lock_id(project) -> str | None:
    conn = narration_repo.open_narration_registry(project.project_root_path)
    try:
        state = narration_repo.get_project_state(conn, project_id=project.id)
    finally:
        conn.close()
    return None if state is None else state.current_script_lock_id


def read_narration_state(project):
    conn = narration_repo.open_narration_registry(project.project_root_path)
    try:
        return narration_repo.get_project_state(conn, project_id=project.id)
    finally:
        conn.close()


def read_latest_locked_script_lock(project):
    conn = supp_repo.open_supplementation_registry(project.project_root_path)
    try:
        return supp_repo.get_current_script_lock(conn, project_id=project.id)
    finally:
        conn.close()


def read_script_lock(project, *, lock_id: str):
    conn = supp_repo.open_supplementation_registry(project.project_root_path)
    try:
        return supp_repo.get_script_lock(conn, lock_id=lock_id)
    finally:
        conn.close()


def list_project_script_locks(project):
    conn = supp_repo.open_supplementation_registry(project.project_root_path)
    try:
        return supp_repo.list_script_locks(conn, project_id=project.id)
    finally:
        conn.close()


def current_observation_fingerprint(project) -> str:
    observations = list_editorial_ready_observations(project)
    return compute_observation_set_fingerprint(observations)


def snapshot_editorial_identity(project) -> EditorialIdentitySnapshot:
    view = get_editorial_view(project)
    preview = preview_script_lock(project)
    script = view.script
    state_lock_id = read_editorial_current_script_lock_id(project)
    risks: tuple[str, ...] = tuple(preview.accepted_open_risks or ())
    if not risks:
        conn = supp_repo.open_supplementation_registry(project.project_root_path)
        try:
            state = editorial_repo.get_project_state(conn, project_id=project.id)
            audit_id = None if state is None else state.active_coverage_audit_id
            gaps = (
                []
                if audit_id is None
                else supp_repo.list_gaps_for_audit(
                    conn,
                    project_id=project.id,
                    coverage_audit_id=audit_id,
                )
            )
            risks = tuple(persisted_accepted_lock_risk_keys(gaps))
        finally:
            conn.close()
    return EditorialIdentitySnapshot(
        script_id=None if script is None else script.script_id,
        script_version=None if script is None else script.script_version,
        narrative_plan_id=(
            None
            if view.narrative_plan is None
            else view.narrative_plan.narrative_plan_id
        ),
        selected_hook_id=view.selected_hook_id,
        coverage_audit_id=(
            None
            if view.coverage_audit is None
            else view.coverage_audit.coverage_audit_id
        ),
        observation_fingerprint=current_observation_fingerprint(project),
        script_lock_fingerprint=preview.lock_fingerprint,
        risk_confirmation_set=risks,
        editorial_current_script_lock_id=state_lock_id,
    )


def lock_identity_values(lock) -> dict[str, Any]:
    return {
        "script_id": lock.script_id,
        "script_version": lock.script_version,
        "narrative_plan_id": lock.narrative_plan_id,
        "selected_hook_id": lock.selected_hook_id,
        "coverage_audit_id": lock.coverage_audit_id,
        "observation_fingerprint": lock.observation_set_fingerprint,
        "script_lock_fingerprint": lock.lock_fingerprint,
        "risk_confirmation_set": tuple(lock.accepted_open_risks or ()),
    }


def identity_mismatches(lock, current: EditorialIdentitySnapshot) -> dict[str, tuple[Any, Any]]:
    """Compare lock row fields to current editorial snapshot (no resolver)."""

    lock_vals = lock_identity_values(lock)
    current_vals = {
        "script_id": current.script_id,
        "script_version": current.script_version,
        "narrative_plan_id": current.narrative_plan_id,
        "selected_hook_id": current.selected_hook_id,
        "coverage_audit_id": current.coverage_audit_id,
        "observation_fingerprint": current.observation_fingerprint,
        "script_lock_fingerprint": current.script_lock_fingerprint,
        "risk_confirmation_set": current.risk_confirmation_set,
    }
    out: dict[str, tuple[Any, Any]] = {}
    for field_name in LOCK_IDENTITY_FIELDS:
        left = lock_vals[field_name]
        right = current_vals[field_name]
        if left != right:
            out[field_name] = (left, right)
    return out


def _seed_structure_pending_script_for_selected_hook(project, *, full_text: str) -> ScriptDraft:
    """Mirror save_user_script_edit via public repo APIs for Narrative B / Hook B.

    FakeText always emits script_version=1 for gateway scripts, so a second
    start_script_run collides on UNIQUE(project_id, script_version). Seeding a
    structure_pending user-edit draft with next_script_version is the public
    application/repo equivalent used by save_user_script_edit.
    """

    config = load_text_config()
    conn = editorial_repo.open_editorial_registry(project.project_root_path)
    try:
        state = editorial_repo.get_project_state(conn, project_id=project.id)
        brief = editorial_repo.get_active_project_brief(conn, project_id=project.id)
        assert state is not None and brief is not None
        assert state.active_narrative_plan_id and state.selected_hook_id
        prior_ids = [
            draft.script_id
            for draft in editorial_repo.list_script_drafts(conn, project_id=project.id)
            if draft.status != ScriptDraftStatus.SUPERSEDED
        ]
        version = editorial_repo.next_script_version(conn, project_id=project.id)
        script = ScriptDraft(
            script_id=editorial_repo.new_script_id(),
            script_version=version,
            project_id=project.id,
            language=brief.language,
            full_text=full_text,
            sentence_order=[],
            narrative_plan_id=state.active_narrative_plan_id,
            selected_hook_id=state.selected_hook_id,
            project_brief_id=brief.project_brief_id,
            brief_version=brief.brief_version,
            prompt_version=config.prompts["structure"],
            gateway_version=GATEWAY_VERSION,
            model_identifier=config.model_identifier,
            provider=config.provider,
            source_kind=ScriptSourceKind.USER_EDIT,
            supersedes_script_id=prior_ids[0] if prior_ids else None,
            content_sha256=compute_text_sha256(full_text),
            status=ScriptDraftStatus.STRUCTURE_PENDING,
            created_at=_now(),
        )
        relative = editorial_repo.save_script_bundle_json(
            project.project_root_path,
            script=script,
            sentences=[],
            claims=[],
            visual_beats=[],
            visual_intents=[],
        )
        conn.execute("BEGIN IMMEDIATE")
        for script_id in prior_ids:
            editorial_repo.update_script_status(
                conn,
                script_id=script_id,
                status=ScriptDraftStatus.SUPERSEDED,
            )
        editorial_repo.insert_script_bundle(
            conn,
            script=script,
            sentences=[],
            claims=[],
            visual_beats=[],
            visual_intents=[],
            relative_json_path=relative,
        )
        editorial_repo.upsert_project_state(
            conn,
            state.model_copy(
                update={
                    "active_script_id": script.script_id,
                    "active_coverage_audit_id": None,
                    "status": EditorialProjectStateStatus.STALE,
                    "updated_at": _now(),
                }
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return script


def _advance_to_script_b_with_new_narrative(project) -> None:
    """Public editorial path: new brief → Narrative B → Hook B → Script B → Audit B.

    Narrative worker constructs EditorialProjectState without
    current_script_lock_id → pointer becomes NULL while Lock A stays locked
    (USA_v2 wipe surface). Gateway start_script_run cannot create Script B
    because FakeText always uses script_version=1; seed via public repo then
    structure/coverage.
    """

    saved = save_project_brief(
        project,
        language="de",
        topic="Andere Lokale Geschichte B",
        target_audience="Audience B",
        tone="direkt",
    )
    assert saved.ok, saved.message
    assert start_narrative_run(project, sync=True).started
    view = get_editorial_view(project)
    assert view.narrative_plan is not None
    assert view.hooks
    # Prefer a non-first hook when available so Hook B differs from Hook A.
    hook_id = view.hooks[-1].hook_id
    assert select_hook(project, hook_id=hook_id).ok
    seeded = _seed_structure_pending_script_for_selected_hook(
        project,
        full_text=(
            "Hook B eroeffnet die zweite Fassung. "
            "Andere Lokale Geschichte B wird neu eingeordnet. "
            "Nutzerentscheidungen bleiben fuer Claims erforderlich."
        ),
    )
    assert start_structure_run(project, sync=True).started
    # FakeText structure responses keep STRUCTURE_PENDING on the script object;
    # promote via public get_script_bundle + save/replace after structure exists.
    _promote_structured_script_to_review_requested(project, script_id=seeded.script_id)
    assert start_coverage_run(project, sync=True).started


def _promote_structured_script_to_review_requested(project, *, script_id: str) -> None:
    """After structure exists, set review_requested (FakeText leaves structure_pending)."""

    conn = editorial_repo.open_editorial_registry(project.project_root_path)
    try:
        bundle = editorial_repo.get_script_bundle(conn, script_id=script_id)
        assert bundle is not None
        script = ScriptDraft.model_validate(bundle["script"]).model_copy(
            update={"status": ScriptDraftStatus.REVIEW_REQUESTED}
        )
        from otio_app.discovery_v2.domain.editorial import (
            Claim,
            Sentence,
            VisualBeat,
            VisualIntent,
        )

        sentences = [Sentence.model_validate(item) for item in bundle.get("sentences") or []]
        claims = [Claim.model_validate(item) for item in bundle.get("claims") or []]
        beats = [VisualBeat.model_validate(item) for item in bundle.get("visual_beats") or []]
        intents = [
            VisualIntent.model_validate(item) for item in bundle.get("visual_intents") or []
        ]
        assert sentences, "structure must exist before promotion"
        relative = editorial_repo.save_script_bundle_json(
            project.project_root_path,
            script=script,
            sentences=sentences,
            claims=claims,
            visual_beats=beats,
            visual_intents=intents,
        )
        conn.execute("BEGIN IMMEDIATE")
        editorial_repo.replace_script_structure(
            conn,
            script=script,
            sentences=sentences,
            claims=claims,
            visual_beats=beats,
            visual_intents=intents,
            relative_json_path=relative,
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _mutate_observation_fingerprint(project) -> str:
    """Reject then re-accept to keep editorial ready while rotating review state.

    If fingerprint is unchanged (same observation content hashes), force a
    distinct fingerprint by rejecting without re-accept — callers that need a
    lockable stand should re-accept and re-run coverage.
    """

    observations = list_editorial_ready_observations(project)
    assert observations
    before = compute_observation_set_fingerprint(observations)
    obs = observations[0]
    assert submit_observation_review(
        project,
        observation_id=obs.observation_id,
        decision="rejected",
        reason_code="l1_fixture_obs_rotate",
    ).ok
    after_reject = current_observation_fingerprint(project)
    assert after_reject != before
    assert submit_observation_review(
        project,
        observation_id=obs.observation_id,
        decision="accepted",
        reason_code="l1_fixture_obs_restore",
    ).ok
    restored = current_observation_fingerprint(project)
    # Same observation content → fingerprint typically restores; return the
    # reject-time fingerprint for mismatch proofs that use reject-only.
    return after_reject if restored == before else restored


def build_fixture_a_usa_v2_deadlock(
    tmp_path: Path,
    temp_db_path: Path,
    *,
    with_pause_and_timeline: bool = True,
) -> FixtureADeadlock:
    """Build USA_v2-shaped deadlock using only public application/repo APIs."""

    project = _script_coverage_project(tmp_path, temp_db_path)
    assert_schema_20(project)

    view_a0 = get_editorial_view(project)
    assert view_a0.script is not None
    narrative_a_id = view_a0.script.narrative_plan_id
    hook_a_id = view_a0.script.selected_hook_id or view_a0.selected_hook_id
    assert narrative_a_id and hook_a_id

    risk_key_a = _accept_one_gap_unresolved(project)
    _decide_all_claims(project)
    lock_a = _create_lock(
        project,
        accepted_unresolved_risk_confirmations={risk_key_a: True},
    )
    script_a_id = lock_a.script_id
    script_a_version = lock_a.script_version
    coverage_audit_a_id = lock_a.coverage_audit_id
    observation_fingerprint_a = lock_a.observation_set_fingerprint
    lock_fingerprint_a = lock_a.lock_fingerprint
    risk_keys_a = tuple(lock_a.accepted_open_risks or ())

    voice = start_voice_generation_run(project, sync=True)
    assert voice.started and voice.run is not None
    voice_run_id = voice.run.run_id
    assert read_narration_current_script_lock_id(project) == lock_a.lock_id

    pause_plan_id = None
    timeline_id = None
    if with_pause_and_timeline:
        assert start_pause_direction_run(project, sync=True).started
        assert start_narration_timing_run(project, sync=True).started
        narr_state = read_narration_state(project)
        assert narr_state is not None
        pause_plan_id = narr_state.current_pause_plan_id
        timeline_id = narr_state.current_timeline_id
        assert pause_plan_id and timeline_id

    # Simulate USA_v2 editorial wipe BEFORE fachliche advance: clear Editorial
    # pointer without invalidating Lock A. L4 select_hook/structure/coverage then
    # clear Narration current state but do not mutate a non-current locked row.
    clear_editorial_current_script_lock_pointer(project)
    assert read_editorial_current_script_lock_id(project) is None

    _advance_to_script_b_with_new_narrative(project)
    _resolve_all_gaps_locally(project)
    _decide_all_claims(project)

    view_b = get_editorial_view(project)
    assert view_b.script is not None
    assert view_b.coverage_audit is not None
    script_b_id = view_b.script.script_id
    script_b_version = view_b.script.script_version
    narrative_b_id = view_b.script.narrative_plan_id
    hook_b_id = view_b.script.selected_hook_id or view_b.selected_hook_id
    coverage_audit_b_id = view_b.coverage_audit.coverage_audit_id
    observation_fingerprint_b = current_observation_fingerprint(project)
    assert script_b_id != script_a_id
    assert script_b_version != script_a_version
    assert narrative_b_id != narrative_a_id
    assert hook_b_id != hook_a_id
    assert coverage_audit_b_id != coverage_audit_a_id

    preview_b = preview_script_lock(project)
    # B is fachlich lock-ready (gaps terminal, claims decided).
    assert preview_b.ok and preview_b.lock_fingerprint, preview_b.blockers
    assert preview_b.lock_fingerprint != lock_fingerprint_a

    editorial_pointer = read_editorial_current_script_lock_id(project)
    assert editorial_pointer is None

    lock_row = read_script_lock(project, lock_id=lock_a.lock_id)
    assert lock_row is not None
    assert lock_row.status == ScriptLockStatus.LOCKED

    # Re-stamp stale Narration pointer for L1/L2/L3 deadlock proofs. L4 product
    # paths clear this; the stamp models the pre-L4 / wipe residue.
    _restamp_stale_narration_pointer(
        project,
        script_lock_id=lock_a.lock_id,
        voice_run_id=voice_run_id,
        pause_plan_id=pause_plan_id,
        timeline_id=timeline_id,
    )
    narration_pointer = read_narration_current_script_lock_id(project)
    assert narration_pointer == lock_a.lock_id

    notes = [
        "editorial_current_script_lock_id=NULL",
        "historical lock status remains locked",
        "narration_current_script_lock_id restamped to Lock A for deadlock proofs",
        "L4 product paths clear Narration current; restamp is fixture-only",
        "get_effective_script_lock not called during fixture build",
    ]
    return FixtureADeadlock(
        project=project,
        lock_a=lock_a,
        script_a_id=script_a_id,
        script_a_version=script_a_version,
        narrative_a_id=narrative_a_id,
        hook_a_id=hook_a_id,
        coverage_audit_a_id=coverage_audit_a_id,
        observation_fingerprint_a=observation_fingerprint_a,
        lock_fingerprint_a=lock_fingerprint_a,
        risk_keys_a=risk_keys_a,
        script_b_id=script_b_id,
        script_b_version=script_b_version,
        narrative_b_id=narrative_b_id,
        hook_b_id=hook_b_id,
        coverage_audit_b_id=coverage_audit_b_id,
        observation_fingerprint_b=observation_fingerprint_b,
        preview_fingerprint_b=preview_b.lock_fingerprint,
        editorial_current_script_lock_id=editorial_pointer,
        narration_current_script_lock_id=narration_pointer,
        voice_run_id=voice_run_id,
        pause_plan_id=pause_plan_id,
        timeline_id=timeline_id,
        notes=notes,
    )


def build_fixture_b_latest_locked_fallback(
    tmp_path: Path,
    temp_db_path: Path,
) -> FixtureBLatestFallback:
    """Pointer NULL while a matching locked row exists (fallback surface)."""

    project = _script_coverage_project(tmp_path, temp_db_path)
    assert_schema_20(project)
    _resolve_all_gaps_locally(project)
    _decide_all_claims(project)
    lock = _create_lock(project)
    assert read_editorial_current_script_lock_id(project) == lock.lock_id
    clear_editorial_current_script_lock_pointer(project)
    assert read_editorial_current_script_lock_id(project) is None
    latest = read_latest_locked_script_lock(project)
    assert latest is not None
    assert latest.lock_id == lock.lock_id
    assert latest.status == ScriptLockStatus.LOCKED
    return FixtureBLatestFallback(
        project=project,
        lock=lock,
        editorial_current_script_lock_id=None,
        latest_locked_id=latest.lock_id,
    )


def build_fixture_c_stale_narration_after_invalidation(
    tmp_path: Path,
    temp_db_path: Path,
) -> FixtureCStaleNarrationPointer:
    """L4: fachliche Invalidierung leert Editorial- und Narration-Current."""

    project = _script_coverage_project(tmp_path, temp_db_path)
    assert_schema_20(project)
    _resolve_all_gaps_locally(project)
    _decide_all_claims(project)
    lock = _create_lock(project)
    voice = start_voice_generation_run(project, sync=True)
    assert voice.started
    assert read_narration_current_script_lock_id(project) == lock.lock_id

    view = get_editorial_view(project)
    assert view.script is not None
    edited = save_user_script_edit(
        project,
        full_text=view.script.full_text + " L1 invalidation sentence.",
    )
    assert edited.ok

    invalidation_path = (
        "save_user_script_edit → apply_script_lock_context_invalidation "
        "(lock invalidated; editorial + narration current pointers NULL)"
    )
    editorial_pointer = read_editorial_current_script_lock_id(project)
    narration_pointer = read_narration_current_script_lock_id(project)
    narr_state = read_narration_state(project)
    lock_after = read_script_lock(project, lock_id=lock.lock_id)
    assert lock_after is not None
    assert editorial_pointer is None
    assert narration_pointer is None
    assert narr_state is not None
    assert narr_state.current_voice_run_id is None
    assert lock_after.status == ScriptLockStatus.INVALIDATED

    return FixtureCStaleNarrationPointer(
        project=project,
        lock=lock,
        invalidation_path=invalidation_path,
        editorial_current_script_lock_id=editorial_pointer,
        narration_current_script_lock_id=narration_pointer,
        lock_status_after=lock_after.status.value,
    )


def build_lock_ready_matching_project(tmp_path: Path, temp_db_path: Path):
    """Minimal locked project with editorial pointer set (helper for matrix cases)."""

    project = _script_coverage_project(tmp_path, temp_db_path)
    _resolve_all_gaps_locally(project)
    _decide_all_claims(project)
    lock = _create_lock(project)
    return project, lock


__all__ = [
    "LOCK_IDENTITY_FIELDS",
    "EditorialIdentitySnapshot",
    "FixtureADeadlock",
    "FixtureBLatestFallback",
    "FixtureCStaleNarrationPointer",
    "REGISTRY_SCHEMA_VERSION",
    "assert_schema_20",
    "build_fixture_a_usa_v2_deadlock",
    "build_fixture_b_latest_locked_fallback",
    "build_fixture_c_stale_narration_after_invalidation",
    "build_lock_ready_matching_project",
    "clear_editorial_current_script_lock_pointer",
    "current_observation_fingerprint",
    "identity_mismatches",
    "install_no_media_io_guards",
    "list_project_script_locks",
    "lock_identity_values",
    "read_editorial_current_script_lock_id",
    "read_latest_locked_script_lock",
    "read_narration_current_script_lock_id",
    "read_narration_state",
    "read_script_lock",
    "snapshot_editorial_identity",
]
