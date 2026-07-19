"""Human-only editorial approval service for Discovery V2 Phase 13 export."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from otio_app.discovery_v2.application.inventory_service import InventoryServiceError, require_discovery_project
from otio_app.discovery_v2.application.script_lock_service import get_effective_script_lock
from otio_app.discovery_v2.domain.export import (
    EDITORIAL_APPROVAL_SCHEMA_VERSION,
    EXPORT_ERROR_ANALYSIS_RUN_ALREADY_ACTIVE,
    EXPORT_ERROR_BLOCKING_ISSUE,
    EXPORT_ERROR_EDITORIAL_APPROVAL_CONFIRMATION_REQUIRED,
    EXPORT_ERROR_EDITORIAL_APPROVAL_FINGERPRINT_MISMATCH,
    EXPORT_ERROR_EDITORIAL_APPROVAL_INVALIDATED,
    EXPORT_ERROR_EDITORIAL_RUN_ALREADY_ACTIVE,
    EXPORT_ERROR_INPUT_STALE,
    EXPORT_ERROR_NARRATION_RUN_ALREADY_ACTIVE,
    EXPORT_ERROR_PLANNED_GRAPHIC,
    EXPORT_ERROR_RUN_ALREADY_ACTIVE,
    EXPORT_ERROR_SUPPLEMENTATION_RUN_ALREADY_ACTIVE,
    EXPORT_ERROR_VALIDATION_FAILED,
    EXPORT_ERROR_VISUAL_EDIT_RUN_ALREADY_ACTIVE,
    AcceptedExportRisk,
    EditorialApproval,
    EditorialApprovalStatus,
    canonical_fingerprint,
)
from otio_app.discovery_v2.domain.media_intake import WorkingMediaStatus
from otio_app.discovery_v2.domain.narration import NarrationTimelineStatus
from otio_app.discovery_v2.persistence import (
    asset_analysis_repository,
    copy_intake_repository,
    editorial_repository,
    export_repository as repo,
    narration_repository,
    supplementation_repository,
    visual_edit_repository,
)
from otio_app.models import Project


class EditorialApprovalServiceError(InventoryServiceError):
    """Domain error for Phase 13 approval operations."""

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code


@dataclass(frozen=True)
class ExportInputContext:
    project: Project
    lock: object
    narration_timeline: object
    visual_bundle: object
    humanity_bundle: object
    feasibility_bundle: object
    working_media: list[object]
    approval_fingerprint: str
    visible_risks: list[AcceptedExportRisk] = field(default_factory=list)


@dataclass(frozen=True)
class EditorialApprovalPreview:
    ok: bool
    fingerprint: str | None = None
    blockers: list[str] = field(default_factory=list)
    context: ExportInputContext | None = None


@dataclass(frozen=True)
class EditorialApprovalResult:
    ok: bool
    message: str
    approval: EditorialApproval | None = None
    preview: EditorialApprovalPreview | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class ReviewExportView:
    ok: bool
    message: str | None = None
    preview: EditorialApprovalPreview | None = None
    current_approval: EditorialApproval | None = None
    validation_report: object | None = None
    export_run: object | None = None
    artifact: object | None = None
    reparse_report: object | None = None
    active_export_run: object | None = None
    can_approve: bool = False
    can_validate: bool = False
    can_export: bool = False


def _now() -> datetime:
    return datetime.now(timezone.utc)


def preview_editorial_approval(project: Project) -> EditorialApprovalPreview:
    project = require_discovery_project(project)
    conn = repo.open_export_registry(project.project_root_path)
    try:
        blocker = _active_blocker(conn, project_id=project.id)
        blockers: list[str] = []
        if blocker is not None:
            blockers.append(blocker[0])
        try:
            context = build_export_input_context(project, conn=conn)
        except EditorialApprovalServiceError as exc:
            blockers.append(exc.code)
            return EditorialApprovalPreview(False, blockers=blockers)
        invalidate_stale_current_approval(conn, project_id=project.id, fingerprint=context.approval_fingerprint)
        conn.commit()
        if blockers:
            return EditorialApprovalPreview(False, fingerprint=context.approval_fingerprint, blockers=blockers, context=context)
        return EditorialApprovalPreview(True, fingerprint=context.approval_fingerprint, context=context)
    finally:
        conn.close()


def get_review_export_view(project: Project) -> ReviewExportView:
    project = require_discovery_project(project)
    preview = preview_editorial_approval(project)
    conn = repo.open_export_registry(project.project_root_path)
    try:
        state = repo.get_project_state(conn, project_id=project.id)
        current_approval = (
            None
            if state is None or not state.current_editorial_approval_id
            else repo.get_editorial_approval(conn, approval_id=state.current_editorial_approval_id)
        )
        validation_report = (
            None
            if state is None or not state.current_export_validation_report_id
            else repo.get_validation_report(conn, report_id=state.current_export_validation_report_id)
        )
        export_run = (
            None
            if state is None or not state.current_otio_export_run_id
            else repo.get_otio_export_run(conn, run_id=state.current_otio_export_run_id)
        )
        artifact = (
            None
            if state is None or not state.current_otio_artifact_id
            else repo.get_otio_export_artifact(conn, artifact_id=state.current_otio_artifact_id)
        )
        reparse_report = (
            None
            if state is None or not state.current_reparse_report_id
            else repo.get_reparse_report(conn, report_id=state.current_reparse_report_id)
        )
        active = repo.find_active_export_run(conn, project_id=project.id)
        approved_current = (
            current_approval is not None
            and current_approval.status == EditorialApprovalStatus.APPROVED
            and preview.fingerprint == current_approval.input_fingerprint
        )
        validation_current = (
            validation_report is not None
            and str(validation_report.status.value) == "completed"
            and not any(issue.blocks_export for issue in validation_report.issues)
            and current_approval is not None
            and validation_report.approval_id == current_approval.approval_id
            and validation_report.input_fingerprint == current_approval.input_fingerprint
        )
        return ReviewExportView(
            ok=True,
            preview=preview,
            current_approval=current_approval,
            validation_report=validation_report,
            export_run=export_run,
            artifact=artifact,
            reparse_report=reparse_report,
            active_export_run=active,
            can_approve=preview.ok,
            can_validate=approved_current and active is None,
            can_export=approved_current and validation_current and active is None,
        )
    finally:
        conn.close()


def create_editorial_approval(
    project: Project,
    *,
    confirmation_checked: bool,
    user_decision: str,
    user_comment: str,
    accepted_risks: list[AcceptedExportRisk | dict[str, object]],
    confirmed_fingerprint: str | None,
) -> EditorialApprovalResult:
    project = require_discovery_project(project)
    conn = repo.open_export_registry(project.project_root_path)
    try:
        preview = preview_editorial_approval(project)
        if not preview.ok or preview.context is None or preview.fingerprint is None:
            return EditorialApprovalResult(False, "Export-Inputs sind nicht freigabefaehig.", preview=preview, error_code=EXPORT_ERROR_INPUT_STALE)
        decision = str(user_decision)
        if decision not in {"approved", "rejected"}:
            return EditorialApprovalResult(False, "Approval-Entscheidung ist ungueltig.", preview=preview, error_code=EXPORT_ERROR_INPUT_STALE)
        if decision == "approved" and not confirmation_checked:
            return EditorialApprovalResult(
                False,
                "Explizite Editorial-Freigabe fehlt.",
                preview=preview,
                error_code=EXPORT_ERROR_EDITORIAL_APPROVAL_CONFIRMATION_REQUIRED,
            )
        if confirmed_fingerprint != preview.fingerprint:
            return EditorialApprovalResult(
                False,
                "Approval-Fingerprint wurde nicht bestaetigt.",
                preview=preview,
                error_code=EXPORT_ERROR_EDITORIAL_APPROVAL_FINGERPRINT_MISMATCH,
            )
        risks = [
            item if isinstance(item, AcceptedExportRisk) else AcceptedExportRisk.model_validate(item)
            for item in accepted_risks
        ]
        context = preview.context
        status = EditorialApprovalStatus.APPROVED if decision == "approved" else EditorialApprovalStatus.REJECTED
        approval = EditorialApproval(
            approval_id=repo.new_editorial_approval_id(),
            project_id=project.id,
            visual_edit_plan_id=context.visual_bundle.plan.plan_id,
            humanity_review_id=context.humanity_bundle.review.review_id,
            feasibility_report_id=context.feasibility_bundle.report.report_id,
            script_lock_id=context.lock.lock_id,
            narration_timeline_id=context.narration_timeline.timeline_id,
            input_fingerprint=preview.fingerprint,
            user_decision=decision,
            user_comment=user_comment or "",
            accepted_visible_risks=risks,
            confirmation_checked=bool(confirmation_checked),
            status=status,
            revision=repo.next_approval_revision(conn, project_id=project.id),
            created_at=_now(),
        )
        relative = repo.save_editorial_approval_json(project.project_root_path, approval)
        conn.execute("BEGIN IMMEDIATE")
        state = repo.get_project_state(conn, project_id=project.id)
        if state is not None and state.current_editorial_approval_id:
            existing = repo.get_editorial_approval(conn, approval_id=state.current_editorial_approval_id)
            if existing is not None and existing.status == EditorialApprovalStatus.APPROVED:
                repo.update_editorial_approval_status(
                    conn,
                    approval_id=existing.approval_id,
                    status=EditorialApprovalStatus.SUPERSEDED,
                )
        repo.insert_editorial_approval(conn, approval, relative)
        repo.mark_current_approval(
            conn,
            project_id=project.id,
            approval_id=approval.approval_id,
            visual_edit_plan_id=approval.visual_edit_plan_id,
            narration_timeline_id=approval.narration_timeline_id,
        )
        repo.write_latest_approval_pointer(project.project_root_path, approval)
        conn.commit()
        return EditorialApprovalResult(True, "Editorial Approval gespeichert.", approval=approval, preview=preview)
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        raise EditorialApprovalServiceError(str(exc)) from exc
    finally:
        conn.close()


def build_export_input_context(project: Project, *, conn) -> ExportInputContext:
    project = require_discovery_project(project)
    lock_result = get_effective_script_lock(project)
    if not lock_result.ok or lock_result.lock is None:
        raise EditorialApprovalServiceError(EXPORT_ERROR_INPUT_STALE, lock_result.error_code or "script_lock_missing")
    lock = lock_result.lock
    narration_state = narration_repository.get_project_state(conn, project_id=project.id)
    if narration_state is None or not narration_state.current_timeline_id:
        raise EditorialApprovalServiceError(EXPORT_ERROR_INPUT_STALE, "Narration Timeline fehlt.")
    timeline = narration_repository.get_timeline(conn, timeline_id=narration_state.current_timeline_id)
    if timeline is None or timeline.status != NarrationTimelineStatus.COMPLETED:
        raise EditorialApprovalServiceError(EXPORT_ERROR_INPUT_STALE, "Narration Timeline ist nicht completed.")
    if timeline.script_lock_id != lock.lock_id:
        raise EditorialApprovalServiceError(EXPORT_ERROR_INPUT_STALE, "Narration Timeline ist stale.")
    visual_state = visual_edit_repository.get_project_state(conn, project_id=project.id)
    if visual_state is None or not visual_state.current_visual_edit_plan_id:
        raise EditorialApprovalServiceError(EXPORT_ERROR_INPUT_STALE, "Visual Edit Plan fehlt.")
    bundle = visual_edit_repository.get_plan_bundle(conn, plan_id=visual_state.current_visual_edit_plan_id)
    if bundle is None or bundle.plan.status != "ready_for_editorial_review":
        raise EditorialApprovalServiceError(EXPORT_ERROR_INPUT_STALE, "Visual Edit Plan ist nicht ready_for_editorial_review.")
    if bundle.plan.script_lock_id != lock.lock_id or bundle.plan.narration_timeline_id != timeline.timeline_id:
        raise EditorialApprovalServiceError(EXPORT_ERROR_INPUT_STALE, "Visual Edit Plan ist stale.")
    if any(shot.media_strategy == "planned_graphic" for shot in bundle.shots):
        raise EditorialApprovalServiceError(EXPORT_ERROR_PLANNED_GRAPHIC, "Planned Graphics blockieren Export.")
    if not visual_state.current_humanity_review_id or not visual_state.current_feasibility_report_id:
        raise EditorialApprovalServiceError(EXPORT_ERROR_INPUT_STALE, "Humanity oder Feasibility fehlt.")
    humanity = visual_edit_repository.get_humanity_review_bundle(
        conn,
        review_id=visual_state.current_humanity_review_id,
    )
    feasibility = visual_edit_repository.get_feasibility_report_bundle(
        conn,
        report_id=visual_state.current_feasibility_report_id,
    )
    if humanity is None or feasibility is None:
        raise EditorialApprovalServiceError(EXPORT_ERROR_INPUT_STALE, "Reviews fehlen.")
    if humanity.review.visual_edit_plan_id != bundle.plan.plan_id or humanity.review.status != "completed":
        raise EditorialApprovalServiceError(EXPORT_ERROR_INPUT_STALE, "Humanity Review ist stale.")
    if feasibility.report.plan_id != bundle.plan.plan_id or feasibility.report.status != "completed":
        raise EditorialApprovalServiceError(EXPORT_ERROR_INPUT_STALE, "Feasibility Report ist stale.")
    if any(f.severity == "blocking" and f.user_status == "open" for f in humanity.findings):
        raise EditorialApprovalServiceError(EXPORT_ERROR_BLOCKING_ISSUE, "Open blocking Humanity Finding.")
    if feasibility.report.overall_technical_assessment == "fail" or any(i.severity == "blocking" for i in feasibility.issues):
        raise EditorialApprovalServiceError(EXPORT_ERROR_VALIDATION_FAILED, "Blocking Feasibility Issue.")
    working_media = [
        item
        for item in copy_intake_repository.list_working_media(conn, project_id=project.id)
        if item.status == WorkingMediaStatus.COMPLETED
    ]
    visible_risks = _visible_risks(bundle, humanity, feasibility)
    fingerprint = _approval_fingerprint(
        project_id=project.id,
        lock=lock,
        timeline=timeline,
        bundle=bundle,
        humanity=humanity,
        feasibility=feasibility,
        repair_run_id=visual_state.current_repair_run_id,
        working_media=working_media,
        visible_risks=visible_risks,
    )
    return ExportInputContext(
        project=project,
        lock=lock,
        narration_timeline=timeline,
        visual_bundle=bundle,
        humanity_bundle=humanity,
        feasibility_bundle=feasibility,
        working_media=working_media,
        approval_fingerprint=fingerprint,
        visible_risks=visible_risks,
    )


def invalidate_stale_current_approval(conn, *, project_id: str, fingerprint: str) -> EditorialApproval | None:
    state = repo.get_project_state(conn, project_id=project_id)
    if state is None or not state.current_editorial_approval_id:
        return None
    approval = repo.get_editorial_approval(conn, approval_id=state.current_editorial_approval_id)
    if approval is None:
        return None
    if approval.status == EditorialApprovalStatus.APPROVED and approval.input_fingerprint != fingerprint:
        repo.update_editorial_approval_status(
            conn,
            approval_id=approval.approval_id,
            status=EditorialApprovalStatus.INVALIDATED,
        )
        repo.upsert_project_state(
            conn,
            state.model_copy(
                update={
                    "current_editorial_approval_id": approval.approval_id,
                    "current_export_validation_report_id": None,
                    "current_otio_export_run_id": None,
                    "current_otio_artifact_id": None,
                    "current_reparse_report_id": None,
                    "updated_at": _now(),
                }
            ),
        )
        return approval.model_copy(update={"status": EditorialApprovalStatus.INVALIDATED})
    return approval


def require_current_approved_approval(project: Project, *, conn) -> tuple[EditorialApproval, ExportInputContext]:
    context = build_export_input_context(project, conn=conn)
    stale = invalidate_stale_current_approval(conn, project_id=project.id, fingerprint=context.approval_fingerprint)
    if stale is not None and stale.status == EditorialApprovalStatus.INVALIDATED:
        raise EditorialApprovalServiceError(EXPORT_ERROR_EDITORIAL_APPROVAL_INVALIDATED)
    state = repo.get_project_state(conn, project_id=project.id)
    if state is None or not state.current_editorial_approval_id:
        raise EditorialApprovalServiceError(EXPORT_ERROR_INPUT_STALE)
    approval = repo.get_editorial_approval(conn, approval_id=state.current_editorial_approval_id)
    if approval is None or approval.status != EditorialApprovalStatus.APPROVED:
        raise EditorialApprovalServiceError(EXPORT_ERROR_EDITORIAL_APPROVAL_INVALIDATED)
    if approval.input_fingerprint != context.approval_fingerprint:
        repo.update_editorial_approval_status(
            conn,
            approval_id=approval.approval_id,
            status=EditorialApprovalStatus.INVALIDATED,
        )
        raise EditorialApprovalServiceError(EXPORT_ERROR_EDITORIAL_APPROVAL_INVALIDATED)
    return approval, context


def _active_blocker(conn, *, project_id: str) -> tuple[str, str] | None:
    if repo.find_active_export_run(conn, project_id=project_id) is not None:
        return EXPORT_ERROR_RUN_ALREADY_ACTIVE, "Export-Run ist aktiv."
    if asset_analysis_repository.find_active_analysis_run(conn, project_id=project_id) is not None:
        return EXPORT_ERROR_ANALYSIS_RUN_ALREADY_ACTIVE, "Analysis-Run ist aktiv."
    if editorial_repository.find_active_editorial_run(conn, project_id=project_id) is not None:
        return EXPORT_ERROR_EDITORIAL_RUN_ALREADY_ACTIVE, "Editorial-Run ist aktiv."
    if supplementation_repository.find_active_supplementation_run(conn, project_id=project_id) is not None:
        return EXPORT_ERROR_SUPPLEMENTATION_RUN_ALREADY_ACTIVE, "Supplementation-Run ist aktiv."
    if narration_repository.find_active_narration_run(conn, project_id=project_id) is not None:
        return EXPORT_ERROR_NARRATION_RUN_ALREADY_ACTIVE, "Narration-Run ist aktiv."
    if visual_edit_repository.find_active_visual_edit_run(conn, project_id=project_id) is not None:
        return EXPORT_ERROR_VISUAL_EDIT_RUN_ALREADY_ACTIVE, "Visual-Edit-Run ist aktiv."
    return None


def _visible_risks(bundle, humanity, feasibility) -> list[AcceptedExportRisk]:
    risks: list[AcceptedExportRisk] = []
    for item in bundle.plan.accepted_risks:
        risks.append(
            AcceptedExportRisk(
                risk_id=item.risk_id,
                category=item.category,
                description=item.rationale or item.category,
                source_ref=f"visual_edit_plan:{bundle.plan.plan_id}",
            )
        )
    for finding in humanity.findings:
        if finding.severity in {"warning", "blocking"}:
            risks.append(
                AcceptedExportRisk(
                    risk_id=finding.finding_id,
                    category=f"humanity:{finding.category}",
                    description=finding.rationale,
                    source_ref=f"humanity_review:{humanity.review.review_id}",
                )
            )
    for issue in feasibility.issues:
        if issue.severity == "warning":
            risks.append(
                AcceptedExportRisk(
                    risk_id=issue.issue_id,
                    category=f"feasibility:{issue.error_code}",
                    description=issue.technical_details,
                    source_ref=f"feasibility_report:{feasibility.report.report_id}",
                )
            )
    return risks


def _approval_fingerprint(
    *,
    project_id: str,
    lock,
    timeline,
    bundle,
    humanity,
    feasibility,
    repair_run_id: str | None,
    working_media: list[object],
    visible_risks: list[AcceptedExportRisk],
) -> str:
    return canonical_fingerprint(
        {
            "schema_version": EDITORIAL_APPROVAL_SCHEMA_VERSION,
            "project_id": project_id,
            "script_lock": {
                "lock_id": lock.lock_id,
                "lock_fingerprint": lock.lock_fingerprint,
            },
            "narration_timeline": {
                "timeline_id": timeline.timeline_id,
                "input_fingerprint": timeline.input_fingerprint,
                "total_frames": timeline.total_frames,
                "timebase": timeline.timebase.model_dump(mode="json"),
                "entries": [
                    {
                        "entry_id": entry.entry_id,
                        "ordinal": entry.ordinal,
                        "entry_type": entry.entry_type.value,
                        "start_frame": entry.start_frame,
                        "end_frame": entry.end_frame,
                        "sentence_id": entry.sentence_id,
                        "voice_segment_id": entry.voice_segment_id,
                    }
                    for entry in timeline.entries
                ],
            },
            "visual_edit_plan": {
                "plan_id": bundle.plan.plan_id,
                "plan_version": bundle.plan.plan_version,
                "input_fingerprint": bundle.plan.input_fingerprint,
                "shots": [shot.model_dump(mode="json") for shot in bundle.shots],
                "assignments": [assignment.model_dump(mode="json") for assignment in bundle.assignments],
                "transitions": [transition.model_dump(mode="json") for transition in bundle.transitions],
            },
            "humanity_review": {
                "review_id": humanity.review.review_id,
                "input_fingerprint": humanity.review.input_fingerprint,
                "findings": [finding.model_dump(mode="json") for finding in humanity.findings],
            },
            "feasibility_report": {
                "report_id": feasibility.report.report_id,
                "input_fingerprint": feasibility.report.input_fingerprint,
                "issues": [issue.model_dump(mode="json") for issue in feasibility.issues],
                "metrics": feasibility.report.metrics,
            },
            "repair_history": {"current_repair_run_id": repair_run_id},
            "visible_risks": [risk.model_dump(mode="json") for risk in visible_risks],
            "working_media": [
                {
                    "working_media_id": item.working_media_id,
                    "asset_id": item.asset_id,
                    "working_relative_path": item.working_relative_path,
                    "output_sha256": item.output_sha256,
                    "media_kind": item.media_kind,
                    "status": item.status.value,
                }
                for item in sorted(working_media, key=lambda wm: wm.working_media_id)
            ],
        }
    )


__all__ = [name for name in globals() if not name.startswith("_")]
