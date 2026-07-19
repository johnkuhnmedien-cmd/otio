"""Application service for Phase 10 coverage gap materialization/escalation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from otio_app.discovery_v2.application.inventory_service import (
    InventoryServiceError,
    require_discovery_project,
)
from otio_app.discovery_v2.domain.editorial import CoverageStatus
from otio_app.discovery_v2.domain.supplementation import (
    ESCALATION_SEQUENCE,
    SUPPLEMENTATION_ERROR_COVERAGE_GAP_MISSING,
    TERMINAL_GAP_STATUSES,
    CoverageGap,
    CoverageGapStatus,
    CoverageLevel,
    CoverageRiskFlag,
    EscalationStep,
    GapEvent,
    GapEventType,
    StockCandidate,
    StockCandidateUserStatus,
    merge_gap_risk_flags,
)
from otio_app.discovery_v2.persistence import editorial_repository as editorial_repo
from otio_app.discovery_v2.persistence import supplementation_repository as repo
from otio_app.discovery_v2.persistence.asset_registry_database import RegistryDatabaseError
from otio_app.models import Project

SUPPLEMENTATION_ERROR_GAP_ACCEPT_BLOCKED = "coverage_gap_accept_blocked"
SUPPLEMENTATION_ERROR_GAP_ACCEPT_CONFIRMATION_REQUIRED = (
    "coverage_gap_accept_confirmation_required"
)


class CoverageGapServiceError(InventoryServiceError):
    """Domain error for coverage gap operations."""


@dataclass(frozen=True)
class CoverageGapResult:
    ok: bool
    message: str
    gaps: list[CoverageGap] = field(default_factory=list)
    error_code: str | None = None


@dataclass(frozen=True)
class GapActionResult:
    ok: bool
    message: str
    gap: CoverageGap | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class GapAcceptEligibility:
    ok: bool
    visible_risks: list[CoverageRiskFlag] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    message: str = ""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def materialize_gaps_from_current_coverage(project: Project) -> CoverageGapResult:
    project = require_discovery_project(project)
    try:
        conn = repo.open_supplementation_registry(project.project_root_path)
    except RegistryDatabaseError as exc:
        raise CoverageGapServiceError(str(exc)) from exc
    try:
        state = editorial_repo.get_project_state(conn, project_id=project.id)
        if state is None or state.active_coverage_audit_id is None:
            return CoverageGapResult(
                ok=False,
                message="Kein aktueller Coverage Audit vorhanden.",
                error_code=SUPPLEMENTATION_ERROR_COVERAGE_GAP_MISSING,
            )
        audit = editorial_repo.get_coverage_audit(
            conn,
            coverage_audit_id=state.active_coverage_audit_id,
        )
        if audit is None:
            return CoverageGapResult(
                ok=False,
                message="Coverage Audit fehlt.",
                error_code=SUPPLEMENTATION_ERROR_COVERAGE_GAP_MISSING,
            )
        existing = repo.list_gaps_for_audit(
            conn,
            project_id=project.id,
            coverage_audit_id=audit.coverage_audit_id,
        )
        if existing:
            normalized = _normalize_existing_gaps(conn, project, existing)
            return CoverageGapResult(
                ok=True,
                message="Coverage Gaps bereits materialisiert.",
                gaps=normalized,
            )

        created: list[CoverageGap] = []
        conn.execute("BEGIN IMMEDIATE")
        superseded = repo.supersede_gaps_not_in_audit(
            conn,
            project_id=project.id,
            coverage_audit_id=audit.coverage_audit_id,
        )
        for gap in superseded:
            repo.append_gap_event(
                conn,
                GapEvent(
                    event_id=repo.new_gap_event_id(),
                    gap_id=gap.gap_id,
                    project_id=project.id,
                    event_type=GapEventType.SUPERSEDED,
                    message="Neuer Coverage Audit superseded diesen Gap.",
                    created_at=_now(),
                ),
            )
        for result in audit.results:
            level, status_risks = _split_coverage(result.coverage_status)
            risks = merge_gap_risk_flags(status_risks, result.missing_properties)
            if level == CoverageLevel.COVERED and not risks:
                continue
            status = (
                CoverageGapStatus.USER_DECISION_REQUIRED
                if risks and level != CoverageLevel.PARTIALLY_COVERED
                else CoverageGapStatus.OPEN
            )
            gap = CoverageGap(
                gap_id=repo.new_gap_id(),
                project_id=project.id,
                script_id=audit.script_id,
                script_version=audit.script_version,
                coverage_audit_id=audit.coverage_audit_id,
                visual_intent_id=result.visual_intent_id,
                coverage_level=level,
                risk_flags=risks,
                missing_properties=list(result.missing_properties),
                status=status,
                gap_version=repo.next_gap_version(
                    conn,
                    project_id=project.id,
                    visual_intent_id=result.visual_intent_id,
                ),
                outcome=result.rationale,
                created_at=_now(),
                updated_at=_now(),
            )
            relative = repo.save_coverage_gap_json(project.project_root_path, gap)
            repo.insert_coverage_gap(conn, gap, relative)
            repo.append_gap_event(
                conn,
                GapEvent(
                    event_id=repo.new_gap_event_id(),
                    gap_id=gap.gap_id,
                    project_id=project.id,
                    event_type=GapEventType.MATERIALIZED,
                    to_step=gap.current_escalation_step,
                    message=result.recommended_next_action,
                    payload={
                        "coverage_status": result.coverage_status.value,
                        "coverage_level": level.value,
                        "risk_flags": [risk.value for risk in risks],
                    },
                    created_at=_now(),
                ),
            )
            created.append(gap)
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        raise CoverageGapServiceError(str(exc)) from exc
    finally:
        conn.close()
    return CoverageGapResult(ok=True, message="Coverage Gaps materialisiert.", gaps=created)


def list_current_gaps(project: Project) -> list[CoverageGap]:
    project = require_discovery_project(project)
    conn = repo.open_supplementation_registry(project.project_root_path)
    try:
        gaps = repo.list_coverage_gaps(conn, project_id=project.id)
        return _normalize_existing_gaps(conn, project, gaps)
    finally:
        conn.close()


def assign_local_deeper_review(project: Project, *, gap_id: str) -> GapActionResult:
    return _update_gap(
        project,
        gap_id=gap_id,
        event_type=GapEventType.LOCAL_REVIEW_ASSIGNED,
        message="Lokale Assets erneut pruefen.",
        updates={"status": CoverageGapStatus.IN_PROGRESS},
    )


def escalate_gap(project: Project, *, gap_id: str) -> GapActionResult:
    project = require_discovery_project(project)
    conn = repo.open_supplementation_registry(project.project_root_path)
    try:
        gap = repo.get_coverage_gap(conn, gap_id=gap_id)
        if gap is None:
            return GapActionResult(
                ok=False,
                message="Coverage Gap fehlt.",
                error_code=SUPPLEMENTATION_ERROR_COVERAGE_GAP_MISSING,
            )
        index = ESCALATION_SEQUENCE.index(gap.current_escalation_step)
        if index >= len(ESCALATION_SEQUENCE) - 1:
            normalized = _ensure_gap_risks(conn, project, gap)
            return GapActionResult(
                ok=True,
                message="Gap ist bereits bei Nutzerentscheidung.",
                gap=normalized,
            )
        next_step = ESCALATION_SEQUENCE[index + 1]
        risks = merge_gap_risk_flags(gap.risk_flags, gap.missing_properties)
        updated = gap.model_copy(
            update={
                "current_escalation_step": next_step,
                "status": CoverageGapStatus.IN_PROGRESS,
                "risk_flags": risks,
                "prior_attempt_summaries": [
                    *gap.prior_attempt_summaries,
                    f"{gap.current_escalation_step.value} -> {next_step.value}",
                ],
                "updated_at": _now(),
            }
        )
        relative = repo.save_coverage_gap_json(project.project_root_path, updated)
        conn.execute("BEGIN IMMEDIATE")
        repo.update_coverage_gap(conn, updated, relative)
        repo.append_gap_event(
            conn,
            GapEvent(
                event_id=repo.new_gap_event_id(),
                gap_id=gap.gap_id,
                project_id=project.id,
                event_type=GapEventType.ESCALATED,
                from_step=gap.current_escalation_step,
                to_step=next_step,
                message="Gap manuell eskaliert.",
                created_at=_now(),
            ),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        raise CoverageGapServiceError(str(exc)) from exc
    finally:
        conn.close()
    return GapActionResult(ok=True, message="Gap eskaliert.", gap=updated)


def mark_gap_resolved_with_local_asset(
    project: Project,
    *,
    gap_id: str,
    asset_id: str,
    outcome: str = "Lokales Asset bestaetigt.",
) -> GapActionResult:
    return _update_gap(
        project,
        gap_id=gap_id,
        event_type=GapEventType.RESOLVED,
        message=outcome,
        updates={
            "status": CoverageGapStatus.RESOLVED_WITH_LOCAL_ASSET,
            "resolved_asset_id": asset_id,
            "outcome": outcome,
        },
    )


def evaluate_gap_accept_unresolved_eligibility(
    project: Project,
    *,
    gap_id: str,
) -> GapAcceptEligibility:
    project = require_discovery_project(project)
    conn = repo.open_supplementation_registry(project.project_root_path)
    try:
        gap = repo.get_coverage_gap(conn, gap_id=gap_id)
        if gap is None:
            return GapAcceptEligibility(
                ok=False,
                blockers=["coverage_gap_missing"],
                message="Coverage Gap fehlt.",
            )
        gap = _ensure_gap_risks(conn, project, gap)
        candidates = repo.list_stock_candidates_for_gap(conn, gap_id=gap.gap_id)
        return _eligibility_for_gap(gap, candidates)
    finally:
        conn.close()


def accept_gap_unresolved(
    project: Project,
    *,
    gap_id: str,
    confirmed_risks: list[str],
    user_confirmed: bool = True,
) -> GapActionResult:
    project = require_discovery_project(project)
    if not user_confirmed:
        return GapActionResult(
            ok=False,
            message="Explizite Risikoannahme-Bestaetigung fehlt.",
            error_code=SUPPLEMENTATION_ERROR_GAP_ACCEPT_CONFIRMATION_REQUIRED,
        )
    conn = repo.open_supplementation_registry(project.project_root_path)
    try:
        gap = repo.get_coverage_gap(conn, gap_id=gap_id)
        if gap is None:
            return GapActionResult(
                ok=False,
                message="Coverage Gap fehlt.",
                error_code=SUPPLEMENTATION_ERROR_COVERAGE_GAP_MISSING,
            )
        gap = _ensure_gap_risks(conn, project, gap)
        candidates = repo.list_stock_candidates_for_gap(conn, gap_id=gap.gap_id)
        eligibility = _eligibility_for_gap(gap, candidates)
        if not eligibility.ok:
            return GapActionResult(
                ok=False,
                message=eligibility.message or "Risikoannahme nicht moeglich.",
                gap=gap,
                error_code=SUPPLEMENTATION_ERROR_GAP_ACCEPT_BLOCKED,
            )
        risks = [CoverageRiskFlag(item) for item in confirmed_risks]
        if not risks:
            risks = list(eligibility.visible_risks)
        visible = set(eligibility.visible_risks)
        if not risks or any(risk not in visible for risk in risks):
            return GapActionResult(
                ok=False,
                message="Bestaetigte Risiken stimmen nicht mit sichtbaren Risiken ueberein.",
                gap=gap,
                error_code=SUPPLEMENTATION_ERROR_GAP_ACCEPT_BLOCKED,
            )
        if (
            gap.status == CoverageGapStatus.ACCEPTED_UNRESOLVED
            and set(gap.accepted_unresolved_risks) == set(risks)
        ):
            return GapActionResult(
                ok=True,
                message="Gap ist bereits unaufgeloest akzeptiert.",
                gap=gap,
            )
        updated = gap.model_copy(
            update={
                "status": CoverageGapStatus.ACCEPTED_UNRESOLVED,
                # Persist only the explicitly accepted risks as the gap's risk set.
                "risk_flags": risks,
                "accepted_unresolved_risks": risks,
                "user_decision": "accepted_unresolved",
                "updated_at": _now(),
            }
        )
        relative = repo.save_coverage_gap_json(project.project_root_path, updated)
        conn.execute("BEGIN IMMEDIATE")
        repo.update_coverage_gap(conn, updated, relative)
        repo.append_gap_event(
            conn,
            GapEvent(
                event_id=repo.new_gap_event_id(),
                gap_id=gap.gap_id,
                project_id=project.id,
                event_type=GapEventType.USER_DECISION_RECORDED,
                message="Gap mit explizit akzeptierten Risiken offen akzeptiert.",
                payload={
                    "accepted_unresolved_risks": [risk.value for risk in risks],
                },
                created_at=_now(),
            ),
        )
        from otio_app.discovery_v2.application.script_lock_current_state_mutation_service import (
            apply_script_lock_context_invalidation,
        )

        apply_script_lock_context_invalidation(
            conn,
            project_id=project.id,
            reason_code="risk_confirmation_changed",
            source_operation_id="accept_gap_unresolved",
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        raise CoverageGapServiceError(str(exc)) from exc
    finally:
        conn.close()
    return GapActionResult(
        ok=True,
        message="Gap mit explizit akzeptierten Risiken offen akzeptiert.",
        gap=updated,
    )


def _update_gap(
    project: Project,
    *,
    gap_id: str,
    event_type: GapEventType,
    message: str,
    updates: dict,
) -> GapActionResult:
    project = require_discovery_project(project)
    conn = repo.open_supplementation_registry(project.project_root_path)
    try:
        gap = repo.get_coverage_gap(conn, gap_id=gap_id)
        if gap is None:
            return GapActionResult(
                ok=False,
                message="Coverage Gap fehlt.",
                error_code=SUPPLEMENTATION_ERROR_COVERAGE_GAP_MISSING,
            )
        updated = gap.model_copy(update={**updates, "updated_at": _now()})
        relative = repo.save_coverage_gap_json(project.project_root_path, updated)
        conn.execute("BEGIN IMMEDIATE")
        repo.update_coverage_gap(conn, updated, relative)
        repo.append_gap_event(
            conn,
            GapEvent(
                event_id=repo.new_gap_event_id(),
                gap_id=gap.gap_id,
                project_id=project.id,
                event_type=event_type,
                message=message,
                created_at=_now(),
            ),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        raise CoverageGapServiceError(str(exc)) from exc
    finally:
        conn.close()
    return GapActionResult(ok=True, message=message, gap=updated)


def _eligibility_for_gap(
    gap: CoverageGap,
    candidates: list[StockCandidate],
) -> GapAcceptEligibility:
    blockers: list[str] = []
    visible = merge_gap_risk_flags(gap.risk_flags, gap.missing_properties)
    if gap.status in TERMINAL_GAP_STATUSES and gap.status != CoverageGapStatus.ACCEPTED_UNRESOLVED:
        blockers.append("gap_already_terminal")
    if gap.status not in {
        CoverageGapStatus.OPEN,
        CoverageGapStatus.IN_PROGRESS,
        CoverageGapStatus.USER_DECISION_REQUIRED,
        CoverageGapStatus.ACCEPTED_UNRESOLVED,
    }:
        blockers.append(f"gap_status_not_accept_eligible:{gap.status.value}")
    if gap.current_escalation_step != EscalationStep.USER_DECISION:
        blockers.append("escalation_not_user_decision")
    if not visible:
        blockers.append("no_visible_acceptable_risk")
    for candidate in candidates:
        status = candidate.user_status
        if status == StockCandidateUserStatus.PROPOSED:
            blockers.append(f"candidate_undecided:{candidate.candidate_id}")
        elif status == StockCandidateUserStatus.NEEDS_REVIEW:
            blockers.append(f"candidate_needs_review:{candidate.candidate_id}")
        elif status == StockCandidateUserStatus.ACCEPTED_FOR_IMPORT:
            blockers.append(f"candidate_accepted_for_import:{candidate.candidate_id}")
    # Deduplicate while preserving order
    seen: set[str] = set()
    unique_blockers: list[str] = []
    for item in blockers:
        if item in seen:
            continue
        seen.add(item)
        unique_blockers.append(item)
    if unique_blockers:
        return GapAcceptEligibility(
            ok=False,
            visible_risks=visible,
            blockers=unique_blockers,
            message="; ".join(unique_blockers),
        )
    return GapAcceptEligibility(ok=True, visible_risks=visible, message="Risikoannahme moeglich.")


def _normalize_existing_gaps(
    conn,
    project: Project,
    gaps: list[CoverageGap],
) -> list[CoverageGap]:
    return [_ensure_gap_risks(conn, project, gap) for gap in gaps]


def _ensure_gap_risks(conn, project: Project, gap: CoverageGap) -> CoverageGap:
    if gap.status == CoverageGapStatus.ACCEPTED_UNRESOLVED:
        # Keep the explicitly accepted risk set stable.
        return gap
    merged = merge_gap_risk_flags(gap.risk_flags, gap.missing_properties)
    if list(gap.risk_flags) == merged:
        return gap
    if gap.status in TERMINAL_GAP_STATUSES:
        return gap.model_copy(update={"risk_flags": merged})
    updated = gap.model_copy(update={"risk_flags": merged, "updated_at": _now()})
    relative = repo.save_coverage_gap_json(project.project_root_path, updated)
    repo.update_coverage_gap(conn, updated, relative)
    conn.commit()
    return updated


def _split_coverage(status: CoverageStatus) -> tuple[CoverageLevel, list[CoverageRiskFlag]]:
    if status == CoverageStatus.COVERED:
        return CoverageLevel.COVERED, []
    if status == CoverageStatus.PARTIALLY_COVERED:
        return CoverageLevel.PARTIALLY_COVERED, []
    if status == CoverageStatus.NOT_COVERED:
        return CoverageLevel.NOT_COVERED, []
    mapping = {
        CoverageStatus.GEOGRAPHICALLY_UNCERTAIN: CoverageRiskFlag.GEOGRAPHICALLY_UNCERTAIN,
        CoverageStatus.TOO_GENERIC: CoverageRiskFlag.TOO_GENERIC,
        CoverageStatus.REPETITION_RISK: CoverageRiskFlag.REPETITION_RISK,
        CoverageStatus.POSSIBLE_SYNTHETIC_RISK: CoverageRiskFlag.POSSIBLE_SYNTHETIC_RISK,
        CoverageStatus.USER_DECISION_REQUIRED: CoverageRiskFlag.USER_DECISION_REQUIRED,
    }
    return CoverageLevel.COVERED, [mapping[status]]


__all__ = [
    "CoverageGapResult",
    "CoverageGapServiceError",
    "GapAcceptEligibility",
    "GapActionResult",
    "SUPPLEMENTATION_ERROR_GAP_ACCEPT_BLOCKED",
    "SUPPLEMENTATION_ERROR_GAP_ACCEPT_CONFIRMATION_REQUIRED",
    "accept_gap_unresolved",
    "assign_local_deeper_review",
    "escalate_gap",
    "evaluate_gap_accept_unresolved_eligibility",
    "list_current_gaps",
    "mark_gap_resolved_with_local_asset",
    "materialize_gaps_from_current_coverage",
]
