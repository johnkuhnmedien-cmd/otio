"""Synchronous Phase 10 script lock application service."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from otio_app.discovery_v2.application.coverage_gap_service import (
    materialize_gaps_from_current_coverage,
)
from otio_app.discovery_v2.application.inventory_service import (
    InventoryServiceError,
    require_discovery_project,
)
from otio_app.discovery_v2.application.observation_review_service import (
    list_editorial_ready_observations,
)
from otio_app.discovery_v2.domain.editorial import (
    ClaimStatus,
    CoverageAuditStatus,
    EditorialProjectStateStatus,
    ScriptDraftStatus,
    compute_observation_set_fingerprint,
    compute_text_sha256,
)
from otio_app.discovery_v2.domain.supplementation import (
    LOCK_COMPATIBLE_CLAIM_DECISIONS,
    SUPPLEMENTATION_ERROR_ANALYSIS_RUN_ALREADY_ACTIVE,
    SUPPLEMENTATION_ERROR_CLAIM_DECISION_REQUIRED,
    SUPPLEMENTATION_ERROR_CLAIM_DECISION_STALE,
    SUPPLEMENTATION_ERROR_EDITORIAL_RUN_ALREADY_ACTIVE,
    SUPPLEMENTATION_ERROR_RUN_ALREADY_ACTIVE,
    SUPPLEMENTATION_ERROR_SCRIPT_LOCK_CONFIRMATION_REQUIRED,
    SUPPLEMENTATION_ERROR_SCRIPT_LOCK_CONFLICT,
    SUPPLEMENTATION_ERROR_SCRIPT_LOCK_FINGERPRINT_MISMATCH,
    SUPPLEMENTATION_ERROR_SCRIPT_LOCK_INVALIDATED,
    SUPPLEMENTATION_ERROR_SCRIPT_LOCK_REQUIREMENTS_NOT_MET,
    CoverageGap,
    CoverageGapStatus,
    CoverageRiskFlag,
    ScriptLock,
    ScriptLockRisk,
    ScriptLockStatus,
    coverage_gap_fingerprint,
    make_lock_risk_confirmation_key,
    parse_lock_risk_confirmation_key,
    persisted_accepted_lock_risk_keys,
    script_lock_fingerprint,
    script_structure_fingerprint,
)
from otio_app.discovery_v2.persistence import editorial_repository as editorial_repo
from otio_app.discovery_v2.persistence import supplementation_repository as repo
from otio_app.discovery_v2.persistence.asset_analysis_repository import (
    find_active_analysis_run,
)
from otio_app.discovery_v2.persistence.asset_registry_database import RegistryDatabaseError
from otio_app.discovery_v2.persistence.editorial_repository import (
    find_active_editorial_run,
)
from otio_app.discovery_v2.persistence.narration_repository import (
    find_active_narration_run,
)
from otio_app.models import Project


class ScriptLockServiceError(InventoryServiceError):
    """Domain error for script lock operations."""


@dataclass(frozen=True)
class ScriptLockRequirement:
    code: str
    label: str
    ok: bool


@dataclass(frozen=True)
class ScriptLockPreview:
    ok: bool
    lock_fingerprint: str | None = None
    fingerprint_display: str | None = None
    blockers: list[str] = field(default_factory=list)
    confirmation_blockers: list[str] = field(default_factory=list)
    fulfilled_requirements: list[str] = field(default_factory=list)
    blocking_requirements: list[str] = field(default_factory=list)
    requirement_details: list[ScriptLockRequirement] = field(default_factory=list)
    accepted_open_risks: list[str] = field(default_factory=list)
    claim_snapshot: list[dict[str, object]] = field(default_factory=list)

    @property
    def can_lock(self) -> bool:
        """Lock is allowed only when fachlich ready and all UI confirmations match."""
        return bool(
            self.ok
            and self.lock_fingerprint
            and not self.confirmation_blockers
        )


@dataclass(frozen=True)
class ScriptLockResult:
    ok: bool
    message: str
    lock: ScriptLock | None = None
    preview: ScriptLockPreview | None = None
    error_code: str | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def preview_script_lock(project: Project) -> ScriptLockPreview:
    project = require_discovery_project(project)
    materialize_gaps_from_current_coverage(project)
    conn = repo.open_supplementation_registry(project.project_root_path)
    try:
        return _build_preview(conn, project)
    finally:
        conn.close()


def create_script_lock(
    project: Project,
    *,
    user_confirmed: bool,
    confirmed_fingerprint: str | None,
    accepted_unresolved_risk_confirmations: dict[str, bool] | None = None,
) -> ScriptLockResult:
    project = require_discovery_project(project)
    materialize_gaps_from_current_coverage(project)
    conn = repo.open_supplementation_registry(project.project_root_path)
    try:
        from otio_app.discovery_v2.persistence.export_repository import (
            find_active_export_run,
        )

        active_export = find_active_export_run(conn, project_id=project.id)
        if active_export is not None:
            return ScriptLockResult(
                ok=False,
                message="Export-Run ist aktiv.",
                error_code="export_run_already_active",
            )
        active_supp = repo.find_active_supplementation_run(conn, project_id=project.id)
        if active_supp is not None:
            return ScriptLockResult(
                ok=False,
                message="Supplementation-Run ist aktiv.",
                error_code=SUPPLEMENTATION_ERROR_RUN_ALREADY_ACTIVE,
            )
        active_analysis = find_active_analysis_run(conn, project_id=project.id)
        if active_analysis is not None:
            return ScriptLockResult(
                ok=False,
                message="Analysis-Run ist aktiv.",
                error_code=SUPPLEMENTATION_ERROR_ANALYSIS_RUN_ALREADY_ACTIVE,
            )
        active_editorial = find_active_editorial_run(conn, project_id=project.id)
        if active_editorial is not None:
            return ScriptLockResult(
                ok=False,
                message="Editorial-Run ist aktiv.",
                error_code=SUPPLEMENTATION_ERROR_EDITORIAL_RUN_ALREADY_ACTIVE,
            )
        active_narration = find_active_narration_run(conn, project_id=project.id)
        if active_narration is not None:
            return ScriptLockResult(
                ok=False,
                message="Narration-Run ist aktiv.",
                error_code="narration_run_already_active",
            )
        from otio_app.discovery_v2.persistence.visual_edit_repository import (
            find_active_visual_edit_run,
        )

        active_visual_edit = find_active_visual_edit_run(conn, project_id=project.id)
        if active_visual_edit is not None:
            return ScriptLockResult(
                ok=False,
                message="Visual-Edit-Run ist aktiv.",
                error_code="visual_edit_run_already_active",
            )
        preview = _build_preview(
            conn,
            project,
            accepted_unresolved_risk_confirmations=accepted_unresolved_risk_confirmations,
        )
        if preview.blockers:
            return ScriptLockResult(
                ok=False,
                message="Script Lock Anforderungen nicht erfuellt.",
                preview=preview,
                error_code=SUPPLEMENTATION_ERROR_SCRIPT_LOCK_REQUIREMENTS_NOT_MET,
            )
        if preview.confirmation_blockers:
            return ScriptLockResult(
                ok=False,
                message=(
                    "Risikobestaetigung fehlt oder ist ungueltig: "
                    + ", ".join(preview.confirmation_blockers)
                ),
                preview=preview,
                error_code=SUPPLEMENTATION_ERROR_SCRIPT_LOCK_REQUIREMENTS_NOT_MET,
            )
        if not user_confirmed:
            return ScriptLockResult(
                ok=False,
                message="Explizite Lock-Bestaetigung fehlt.",
                preview=preview,
                error_code=SUPPLEMENTATION_ERROR_SCRIPT_LOCK_CONFIRMATION_REQUIRED,
            )
        if confirmed_fingerprint != preview.lock_fingerprint:
            return ScriptLockResult(
                ok=False,
                message="Lock-Fingerprint wurde nicht bestaetigt.",
                preview=preview,
                error_code=SUPPLEMENTATION_ERROR_SCRIPT_LOCK_FINGERPRINT_MISMATCH,
            )
        state = editorial_repo.get_project_state(conn, project_id=project.id)
        script = editorial_repo.get_active_script(conn, project_id=project.id)
        brief = editorial_repo.get_active_project_brief(conn, project_id=project.id)
        coverage = (
            None
            if state is None or state.active_coverage_audit_id is None
            else editorial_repo.get_coverage_audit(
                conn,
                coverage_audit_id=state.active_coverage_audit_id,
            )
        )
        if state is None or script is None or brief is None or coverage is None:
            return ScriptLockResult(
                ok=False,
                message="Lock Input fehlt.",
                preview=preview,
                error_code=SUPPLEMENTATION_ERROR_SCRIPT_LOCK_REQUIREMENTS_NOT_MET,
            )
        existing = repo.get_current_script_lock(conn, project_id=project.id)
        lock = ScriptLock(
            lock_id=repo.new_script_lock_id(),
            project_id=project.id,
            script_id=script.script_id,
            script_version=script.script_version,
            project_brief_id=brief.project_brief_id,
            narrative_plan_id=script.narrative_plan_id,
            selected_hook_id=script.selected_hook_id or state.selected_hook_id or "",
            coverage_audit_id=coverage.coverage_audit_id,
            observation_set_fingerprint=coverage.input_observation_fingerprint,
            script_hash=script.content_sha256,
            structure_fingerprint=_structure_fingerprint(conn, script.script_id),
            coverage_fingerprint=_coverage_fingerprint(conn, project, coverage.coverage_audit_id),
            accepted_open_risks=preview.accepted_open_risks,
            claim_decision_snapshot=preview.claim_snapshot,
            user_confirmed=True,
            user_confirmed_at=_now(),
            confirmation_fingerprint=confirmed_fingerprint or "",
            lock_fingerprint=preview.lock_fingerprint or "",
            lock_version=repo.next_script_lock_version(conn, project_id=project.id),
            status=ScriptLockStatus.LOCKED,
            created_at=_now(),
        )
        relative = repo.save_script_lock_json(project.project_root_path, lock)
        conn.execute("BEGIN IMMEDIATE")
        if existing is not None:
            repo.update_script_lock_status(
                conn,
                lock_id=existing.lock_id,
                status=ScriptLockStatus.SUPERSEDED,
            )
        repo.insert_script_lock(conn, lock, relative)
        for risk in lock.accepted_open_risks:
            repo.insert_script_lock_risk(
                conn,
                ScriptLockRisk(
                    lock_id=lock.lock_id,
                    risk_key=risk,
                    confirmed_at=lock.user_confirmed_at or _now(),
                    confirmation_fingerprint=lock.confirmation_fingerprint,
                ),
            )
        repo.update_script_lock_status(conn, lock_id=lock.lock_id, status=ScriptLockStatus.LOCKED)
        repo_conn_state = state.model_copy(
            update={
                "current_script_lock_id": lock.lock_id,
                "status": EditorialProjectStateStatus.ACTIVE,
                "updated_at": _now(),
            }
        )
        editorial_repo.upsert_project_state(conn, repo_conn_state)
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        raise ScriptLockServiceError(str(exc)) from exc
    finally:
        conn.close()
    return ScriptLockResult(ok=True, message="Script Lock erstellt.", lock=lock, preview=preview)


def get_effective_script_lock(project: Project) -> ScriptLockResult:
    project = require_discovery_project(project)
    conn = repo.open_supplementation_registry(project.project_root_path)
    try:
        state = editorial_repo.get_project_state(conn, project_id=project.id)
        lock = None
        if state is not None and state.current_script_lock_id:
            lock = repo.get_script_lock(conn, lock_id=state.current_script_lock_id)
        if lock is None:
            lock = repo.get_current_script_lock(conn, project_id=project.id)
        if lock is None:
            return ScriptLockResult(ok=False, message="Kein aktiver Script Lock.")
        if lock.status != ScriptLockStatus.LOCKED:
            return ScriptLockResult(
                ok=False,
                message="Script Lock ist nicht wirksam.",
                error_code=SUPPLEMENTATION_ERROR_SCRIPT_LOCK_INVALIDATED,
            )
        preview = _build_preview(conn, project, allow_existing_lock=True)
        if preview.lock_fingerprint != lock.lock_fingerprint or preview.blockers:
            conn.execute("BEGIN IMMEDIATE")
            repo.update_script_lock_status(
                conn,
                lock_id=lock.lock_id,
                status=ScriptLockStatus.INVALIDATED,
            )
            if state is not None:
                editorial_repo.upsert_project_state(
                    conn,
                    state.model_copy(
                        update={"current_script_lock_id": None, "updated_at": _now()}
                    ),
                )
            conn.commit()
            return ScriptLockResult(
                ok=False,
                message="Script Lock ist invalidiert.",
                preview=preview,
                error_code=SUPPLEMENTATION_ERROR_SCRIPT_LOCK_INVALIDATED,
            )
        return ScriptLockResult(ok=True, message="Script Lock ist wirksam.", lock=lock, preview=preview)
    except RegistryDatabaseError as exc:
        raise ScriptLockServiceError(str(exc)) from exc
    finally:
        conn.close()


def _build_preview(
    conn,
    project: Project,
    *,
    accepted_unresolved_risk_confirmations: dict[str, bool] | None = None,
    allow_existing_lock: bool = False,
) -> ScriptLockPreview:
    blockers: list[str] = []
    state = editorial_repo.get_project_state(conn, project_id=project.id)
    brief = editorial_repo.get_active_project_brief(conn, project_id=project.id)
    script = editorial_repo.get_active_script(conn, project_id=project.id)
    if state is None or state.status == EditorialProjectStateStatus.STALE:
        blockers.append("editorial_state_stale")
    if brief is None:
        blockers.append("project_brief_missing")
    if state is None or not state.active_narrative_plan_id:
        blockers.append("narrative_plan_missing")
    if state is None or not state.selected_hook_id:
        blockers.append("selected_hook_missing")
    if script is None:
        blockers.append("script_missing")
    elif script.status == ScriptDraftStatus.STRUCTURE_PENDING:
        blockers.append("script_structure_pending")
    bundle = None if script is None else editorial_repo.get_script_bundle(conn, script_id=script.script_id)
    structure_ok = _bundle_has_full_structure(bundle)
    if not structure_ok:
        blockers.append("script_structure_incomplete")
    coverage = None
    if state is not None and state.active_coverage_audit_id:
        coverage = editorial_repo.get_coverage_audit(
            conn,
            coverage_audit_id=state.active_coverage_audit_id,
        )
    if coverage is None or coverage.status != CoverageAuditStatus.COMPLETED:
        blockers.append("coverage_audit_missing_or_stale")
    stale_inputs = False
    if script is not None and coverage is not None and coverage.script_id != script.script_id:
        blockers.append("coverage_audit_stale")
        stale_inputs = True
    observations = list_editorial_ready_observations(project)
    observation_fingerprint = compute_observation_set_fingerprint(
        [
            type(
                "Obs",
                (),
                {
                    "observation_id": item.observation_id,
                    "asset_id": item.asset_id,
                    "observation_sha256": item.observation_sha256,
                    "frame_set_fingerprint": item.frame_set_fingerprint,
                },
            )()
            for item in observations
        ]
    )
    if coverage is not None and coverage.input_observation_fingerprint != observation_fingerprint:
        blockers.append("coverage_audit_stale")
        stale_inputs = True
    gaps = (
        []
        if coverage is None
        else repo.list_gaps_for_audit(
            conn,
            project_id=project.id,
            coverage_audit_id=coverage.coverage_audit_id,
        )
    )
    open_gap_count = 0
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
        open_gap_count += 1
        blockers.append(f"coverage_gap_open:{gap.gap_id}")
    # Persisted accepted risks always contribute to the fachlichen Lock-Stand /
    # Fingerprint. UI checkboxes confirm that stand; they do not create it.
    accepted_risks = persisted_accepted_lock_risk_keys(gaps)
    confirmation_blockers = _confirmation_blockers(
        gaps=gaps,
        required_keys=accepted_risks,
        confirmations=accepted_unresolved_risk_confirmations or {},
        allow_existing_lock=allow_existing_lock,
    )
    claim_snapshot, claim_blockers = _claim_snapshot_and_blockers(conn, project, script, bundle)
    blockers.extend(claim_blockers)
    # Active runs are checked in create_script_lock; preview focuses on data readiness.
    details = _requirement_details(
        brief_ok=brief is not None,
        narrative_ok=bool(state and state.active_narrative_plan_id),
        hook_ok=bool(state and state.selected_hook_id),
        script_ok=script is not None,
        structure_ok=structure_ok,
        claims_ok=not any(
            item.startswith(SUPPLEMENTATION_ERROR_CLAIM_DECISION_REQUIRED)
            or item.startswith(SUPPLEMENTATION_ERROR_CLAIM_DECISION_STALE)
            or item.startswith("claim_revision_required:")
            for item in blockers
        ),
        coverage_ok=coverage is not None
        and coverage.status == CoverageAuditStatus.COMPLETED
        and not stale_inputs,
        gaps_terminal=open_gap_count == 0,
        open_gap_count=open_gap_count,
    )
    fulfilled = [item.label for item in details if item.ok]
    blocking = [item.label for item in details if not item.ok]
    fachlich_ready = (
        not blockers
        and script is not None
        and brief is not None
        and coverage is not None
        and state is not None
    )
    if not fachlich_ready:
        return ScriptLockPreview(
            ok=False,
            blockers=blockers,
            confirmation_blockers=confirmation_blockers,
            fulfilled_requirements=fulfilled,
            blocking_requirements=blocking,
            requirement_details=details,
            accepted_open_risks=accepted_risks,
            claim_snapshot=claim_snapshot,
        )
    structure_fingerprint = script_structure_fingerprint(bundle or {})
    coverage_fingerprint = coverage_gap_fingerprint(
        coverage_audit_id=coverage.coverage_audit_id,
        gaps=gaps,
        claim_decisions=repo.list_claim_decisions(
            conn,
            project_id=project.id,
            script_id=script.script_id,
        ),
    )
    fingerprint = script_lock_fingerprint(
        project_id=project.id,
        script_id=script.script_id,
        script_version=script.script_version,
        project_brief_id=brief.project_brief_id,
        narrative_plan_id=script.narrative_plan_id,
        selected_hook_id=script.selected_hook_id or state.selected_hook_id or "",
        coverage_audit_id=coverage.coverage_audit_id,
        observation_set_fingerprint=coverage.input_observation_fingerprint,
        script_hash=script.content_sha256,
        structure_fingerprint=structure_fingerprint,
        coverage_fingerprint=coverage_fingerprint,
        accepted_open_risks=accepted_risks,
        claim_decision_snapshot=claim_snapshot,
    )
    return ScriptLockPreview(
        ok=True,
        lock_fingerprint=fingerprint,
        fingerprint_display=fingerprint[:12],
        blockers=[],
        confirmation_blockers=confirmation_blockers,
        fulfilled_requirements=fulfilled,
        blocking_requirements=blocking,
        requirement_details=details,
        accepted_open_risks=accepted_risks,
        claim_snapshot=claim_snapshot,
    )


def _confirmation_blockers(
    *,
    gaps: list[CoverageGap],
    required_keys: list[str],
    confirmations: dict[str, bool],
    allow_existing_lock: bool,
) -> list[str]:
    """Validate UI risk confirmations against persisted accepted risks (gap_id+code)."""
    if allow_existing_lock:
        return []
    accepted_gaps = {
        gap.gap_id: gap
        for gap in gaps
        if gap.status == CoverageGapStatus.ACCEPTED_UNRESOLVED
    }
    required = set(required_keys)
    blockers: list[str] = []
    for key in sorted(required):
        if not confirmations.get(key, False):
            blockers.append(f"accepted_unresolved_risk_unconfirmed:{key}")
    for key, confirmed in confirmations.items():
        if not confirmed:
            continue
        try:
            gap_id, risk_code = parse_lock_risk_confirmation_key(key)
        except ValueError:
            blockers.append(f"accepted_unresolved_risk_invalid_key:{key}")
            continue
        gap = accepted_gaps.get(gap_id)
        if gap is None:
            # Reject visual_intent_id (or any non-gap identity) silently matching.
            blockers.append(f"accepted_unresolved_risk_unknown_gap:{key}")
            continue
        try:
            risk = CoverageRiskFlag(risk_code)
        except ValueError:
            blockers.append(f"accepted_unresolved_risk_unknown_code:{key}")
            continue
        if risk not in (gap.accepted_unresolved_risks or ()):
            blockers.append(f"accepted_unresolved_risk_not_on_gap:{key}")
            continue
        expected = make_lock_risk_confirmation_key(gap.gap_id, risk)
        if key != expected:
            blockers.append(f"accepted_unresolved_risk_invalid_key:{key}")
    # Deterministic unique order
    return sorted(set(blockers))


def _requirement_details(
    *,
    brief_ok: bool,
    narrative_ok: bool,
    hook_ok: bool,
    script_ok: bool,
    structure_ok: bool,
    claims_ok: bool,
    coverage_ok: bool,
    gaps_terminal: bool,
    open_gap_count: int,
) -> list[ScriptLockRequirement]:
    gap_label = (
        "alle Coverage Gaps terminal"
        if gaps_terminal
        else f"{open_gap_count} Coverage Gaps noch offen"
    )
    return [
        ScriptLockRequirement("project_brief", "aktueller Brief", brief_ok),
        ScriptLockRequirement("narrative_plan", "aktueller Narrative Plan", narrative_ok),
        ScriptLockRequirement("selected_hook", "ausgewählter Hook", hook_ok),
        ScriptLockRequirement("script", "aktuelles Script", script_ok),
        ScriptLockRequirement("structure", "Struktur aktuell", structure_ok),
        ScriptLockRequirement("claims", "Claims entschieden", claims_ok),
        ScriptLockRequirement("coverage_audit", "aktueller Coverage Audit", coverage_ok),
        ScriptLockRequirement("coverage_gaps", gap_label, gaps_terminal),
    ]


def _bundle_has_full_structure(bundle: dict | None) -> bool:
    if not bundle:
        return False
    return bool(
        bundle.get("sentences")
        and bundle.get("claims")
        and bundle.get("visual_beats")
        and bundle.get("visual_intents")
    )


def _claim_snapshot_and_blockers(conn, project: Project, script, bundle) -> tuple[list[dict[str, object]], list[str]]:
    if script is None or bundle is None:
        return [], []
    latest = repo.latest_claim_decisions_for_script(
        conn,
        project_id=project.id,
        script_id=script.script_id,
    )
    snapshot: list[dict[str, object]] = []
    blockers: list[str] = []
    for claim in bundle.get("claims", []):
        claim_id = str(claim["claim_id"])
        status = ClaimStatus(str(claim["status"]))
        if status == ClaimStatus.SUPPORTED:
            continue
        decision = latest.get(claim_id)
        claim_hash = compute_text_sha256(str(claim["statement"]))
        if decision is None:
            blockers.append(f"{SUPPLEMENTATION_ERROR_CLAIM_DECISION_REQUIRED}:{claim_id}")
            continue
        if decision.claim_content_sha256 != claim_hash:
            blockers.append(f"{SUPPLEMENTATION_ERROR_CLAIM_DECISION_STALE}:{claim_id}")
            continue
        if decision.decision not in LOCK_COMPATIBLE_CLAIM_DECISIONS:
            blockers.append(f"claim_revision_required:{claim_id}")
            continue
        snapshot.append(decision.model_dump(mode="json"))
    return snapshot, blockers


def _structure_fingerprint(conn, script_id: str) -> str:
    return script_structure_fingerprint(editorial_repo.get_script_bundle(conn, script_id=script_id) or {})


def _coverage_fingerprint(conn, project: Project, coverage_audit_id: str) -> str:
    gaps = repo.list_gaps_for_audit(
        conn,
        project_id=project.id,
        coverage_audit_id=coverage_audit_id,
    )
    state = editorial_repo.get_project_state(conn, project_id=project.id)
    decisions = (
        []
        if state is None or state.active_script_id is None
        else repo.list_claim_decisions(
            conn,
            project_id=project.id,
            script_id=state.active_script_id,
        )
    )
    return coverage_gap_fingerprint(
        coverage_audit_id=coverage_audit_id,
        gaps=gaps,
        claim_decisions=decisions,
    )


__all__ = [
    "ScriptLockPreview",
    "ScriptLockRequirement",
    "ScriptLockResult",
    "ScriptLockServiceError",
    "create_script_lock",
    "get_effective_script_lock",
    "preview_script_lock",
]
