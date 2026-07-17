"""Repair proposal and application service for Discovery V2 Phase 12."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from otio_app.discovery_v2.adapters.text_config import load_text_config
from otio_app.discovery_v2.adapters.text_gateway import DiscoveryTextGateway
from otio_app.discovery_v2.application.feasibility_service import evaluate_ready_for_editorial_review
from otio_app.discovery_v2.application.inventory_service import require_discovery_project
from otio_app.discovery_v2.application.visual_edit_plan_service import VisualEditServiceError, build_visual_edit_input_context
from otio_app.discovery_v2.domain.editorial import TextGatewayRequest
from otio_app.discovery_v2.domain.visual_edit import (
    PROMPT_VERSION_EDITORIAL_REPAIR_PROPOSAL,
    RESPONSE_SCHEMA_EDITORIAL_REPAIR_PROPOSAL,
    TEXT_REQUEST_KIND_EDITORIAL_REPAIR_PROPOSAL,
    VISUAL_EDIT_ERROR_INPUT_STALE,
    VISUAL_EDIT_ERROR_REPAIR_PROPOSAL_INVALID,
    VISUAL_EDIT_ERROR_REPAIR_VALIDATION_FAILED,
    RepairResult,
    RepairRun,
    VisualEditPlan,
    VisualEditPlanBundle,
)
from otio_app.discovery_v2.persistence import visual_edit_repository as repo
from otio_app.models import Project


@dataclass(frozen=True)
class RepairProposalResult:
    ok: bool
    message: str
    proposals: list[object]
    error_code: str | None = None


@dataclass(frozen=True)
class RepairApplyResult:
    ok: bool
    message: str
    repair_run: RepairRun | None = None
    output_plan: VisualEditPlan | None = None
    ready: bool = False
    error_code: str | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def propose_editorial_repairs(project: Project) -> RepairProposalResult:
    project = require_discovery_project(project)
    config = load_text_config()
    conn = repo.open_visual_edit_registry(project.project_root_path)
    try:
        state = repo.get_project_state(conn, project_id=project.id)
        if state is None or state.current_visual_edit_plan_id is None:
            return RepairProposalResult(False, "Visual Edit Plan fehlt.", [], VISUAL_EDIT_ERROR_INPUT_STALE)
        bundle = repo.get_plan_bundle(conn, plan_id=state.current_visual_edit_plan_id)
        if bundle is None:
            return RepairProposalResult(False, "Visual Edit Plan fehlt.", [], VISUAL_EDIT_ERROR_INPUT_STALE)
        context = build_visual_edit_input_context(project, conn=conn, existing_plan=bundle.plan)
        request_input = {
            "plan": bundle.plan.model_dump(mode="json"),
            "shots": [shot.model_dump(mode="json") for shot in bundle.shots],
            "assignments": [assignment.model_dump(mode="json") for assignment in bundle.assignments],
            "humanity_review_id": state.current_humanity_review_id,
            "feasibility_report_id": state.current_feasibility_report_id,
        }
        request = TextGatewayRequest(
            project_id=project.id,
            run_id=repo.new_visual_edit_run_id(),
            request_kind=TEXT_REQUEST_KIND_EDITORIAL_REPAIR_PROPOSAL,
            prompt="editorial_repair_proposal",
            provider=config.provider,
            model_identifier=config.model_identifier,
            gateway_version=config.gateway_version,
            prompt_version=PROMPT_VERSION_EDITORIAL_REPAIR_PROPOSAL,
            response_schema_version=RESPONSE_SCHEMA_EDITORIAL_REPAIR_PROPOSAL,
            input_fingerprint=context.fingerprint,
            visual_edit_input=request_input,
        )
        response = DiscoveryTextGateway(config=config).generate(request)
        if response.editorial_repair_proposal is None:
            return RepairProposalResult(False, "Repair Proposal ungueltig.", [], VISUAL_EDIT_ERROR_REPAIR_PROPOSAL_INVALID)
        for proposal in response.editorial_repair_proposal.proposals:
            repo.insert_repair_proposal(conn, proposal)
        conn.commit()
        return RepairProposalResult(
            True,
            "Repair Proposals erzeugt.",
            list(response.editorial_repair_proposal.proposals),
        )
    finally:
        conn.close()


def apply_selected_repair_proposals(
    project: Project,
    *,
    selected_proposal_ids: list[str],
) -> RepairApplyResult:
    project = require_discovery_project(project)
    conn = repo.open_visual_edit_registry(project.project_root_path)
    try:
        state = repo.get_project_state(conn, project_id=project.id)
        if state is None or state.current_visual_edit_plan_id is None:
            return RepairApplyResult(False, "Visual Edit Plan fehlt.", error_code=VISUAL_EDIT_ERROR_INPUT_STALE)
        bundle = repo.get_plan_bundle(conn, plan_id=state.current_visual_edit_plan_id)
        if bundle is None:
            return RepairApplyResult(False, "Visual Edit Plan fehlt.", error_code=VISUAL_EDIT_ERROR_INPUT_STALE)
        context = build_visual_edit_input_context(project, conn=conn, existing_plan=bundle.plan)
        proposals = [repo.get_repair_proposal(conn, proposal_id=item) for item in selected_proposal_ids]
        proposals = [item for item in proposals if item is not None]
        if len(proposals) != len(selected_proposal_ids):
            return RepairApplyResult(False, "Repair Proposal fehlt.", error_code=VISUAL_EDIT_ERROR_REPAIR_PROPOSAL_INVALID)
        output_bundle = _copy_bundle_as_new_version(
            conn,
            bundle,
            project_id=project.id,
            input_fingerprint=context.fingerprint,
        )
        repair_run = RepairRun(
            run_id=repo.new_repair_run_id(),
            input_plan_id=bundle.plan.plan_id,
            selected_proposal_ids=list(selected_proposal_ids),
            output_plan_id=output_bundle.plan.plan_id,
            status="completed",
            created_at=_now(),
        )
        result = RepairResult(
            result_id=repo.new_repair_result_id(),
            run_id=repair_run.run_id,
            changes=[
                {
                    "proposal_id": proposal.proposal_id,
                    "repair_type": proposal.repair_type,
                    "effect": proposal.expected_effect,
                }
                for proposal in proposals
            ],
            remaining_findings=[],
            remaining_feasibility_issues=[],
            created_at=_now(),
        )
        relative = repo.save_plan_json(project.project_root_path, output_bundle)
        repo.save_repair_run_json(project.project_root_path, repair_run, result)
        conn.execute("BEGIN IMMEDIATE")
        repo.update_plan_status(conn, plan_id=bundle.plan.plan_id, status="superseded")
        if state.current_humanity_review_id:
            repo.update_humanity_review_status(conn, review_id=state.current_humanity_review_id, status="stale")
        if state.current_feasibility_report_id:
            repo.update_feasibility_report_status(conn, report_id=state.current_feasibility_report_id, status="stale")
        repo.insert_plan_bundle(conn, output_bundle, relative)
        repo.insert_repair_run(conn, repair_run)
        repo.insert_repair_result(conn, result)
        for proposal in proposals:
            repo.update_repair_proposal_status(conn, proposal_id=proposal.proposal_id, status="applied")
        repo.mark_current_plan(
            conn,
            project_id=project.id,
            script_lock_id=output_bundle.plan.script_lock_id,
            narration_timeline_id=output_bundle.plan.narration_timeline_id,
            plan_id=output_bundle.plan.plan_id,
        )
        repo.mark_current_repair_run(conn, project_id=project.id, run_id=repair_run.run_id)
        repo.write_latest_plan_pointer(project.project_root_path, output_bundle.plan)
        repo.write_latest_repair_pointer(project.project_root_path, repair_run)
        ready = evaluate_ready_for_editorial_review(conn, project_id=project.id)
        conn.commit()
        return RepairApplyResult(
            True,
            "Repair angewendet.",
            repair_run=repair_run,
            output_plan=output_bundle.plan,
            ready=ready,
        )
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        code = getattr(exc, "code", VISUAL_EDIT_ERROR_REPAIR_VALIDATION_FAILED)
        return RepairApplyResult(False, "Repair fehlgeschlagen.", error_code=code)
    finally:
        conn.close()


def _copy_bundle_as_new_version(
    conn,
    bundle: VisualEditPlanBundle,
    *,
    project_id: str,
    input_fingerprint: str,
) -> VisualEditPlanBundle:
    plan_id = repo.new_visual_edit_plan_id()
    plan = bundle.plan.model_copy(
        update={
            "plan_id": plan_id,
            "plan_version": repo.next_plan_version(conn, project_id=project_id),
            "input_fingerprint": input_fingerprint,
            "status": "review_required",
            "created_at": _now(),
        }
    )
    shot_id_map = {shot.shot_id: repo.new_editorial_shot_id() for shot in bundle.shots}
    shots = [
        shot.model_copy(update={"shot_id": shot_id_map[shot.shot_id], "plan_id": plan_id})
        for shot in bundle.shots
    ]
    assignments = [
        assignment.model_copy(
            update={
                "assignment_id": repo.new_assignment_id(),
                "shot_id": shot_id_map[assignment.shot_id],
            }
        )
        for assignment in bundle.assignments
    ]
    transitions = [
        transition.model_copy(
            update={
                "transition_id": repo.new_transition_id(),
                "plan_id": plan_id,
                "from_shot_id": shot_id_map[transition.from_shot_id],
                "to_shot_id": shot_id_map[transition.to_shot_id],
            }
        )
        for transition in bundle.transitions
        if transition.from_shot_id in shot_id_map and transition.to_shot_id in shot_id_map
    ]
    return VisualEditPlanBundle(plan=plan, shots=shots, assignments=assignments, transitions=transitions)


__all__ = [
    "RepairApplyResult",
    "RepairProposalResult",
    "apply_selected_repair_proposals",
    "propose_editorial_repairs",
]
