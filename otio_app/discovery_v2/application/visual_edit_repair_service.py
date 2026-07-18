"""Repair proposal, selection, and application service for Discovery V2 Phase 12."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from otio_app.discovery_v2.adapters.text_config import load_text_config
from otio_app.discovery_v2.adapters.text_gateway import DiscoveryTextGateway
from otio_app.discovery_v2.application.feasibility_service import (
    evaluate_feasibility,
    evaluate_ready_for_editorial_review,
)
from otio_app.discovery_v2.application.inventory_service import require_discovery_project
from otio_app.discovery_v2.application.visual_edit_plan_service import (
    VisualEditServiceError,
    build_visual_edit_input_context,
)
from otio_app.discovery_v2.domain.editorial import TextGatewayRequest
from otio_app.discovery_v2.domain.visual_edit import (
    ASSET_REUSE_MAX,
    PROMPT_VERSION_EDITORIAL_REPAIR_PROPOSAL,
    RESPONSE_SCHEMA_EDITORIAL_REPAIR_PROPOSAL,
    SOURCE_RANGE_OVERLAP_RATIO_MAX,
    TEXT_REQUEST_KIND_EDITORIAL_REPAIR_PROPOSAL,
    VISUAL_EDIT_ERROR_INPUT_STALE,
    VISUAL_EDIT_ERROR_REPAIR_APPLY_PERSIST_FAILED,
    VISUAL_EDIT_ERROR_REPAIR_CONFLICT,
    VISUAL_EDIT_ERROR_REPAIR_OPERATION_NO_EFFECT,
    VISUAL_EDIT_ERROR_REPAIR_OPERATION_TARGET_MISSING,
    VISUAL_EDIT_ERROR_REPAIR_PROPOSAL_INVALID,
    VISUAL_EDIT_ERROR_REPAIR_PROPOSAL_NOT_EXECUTABLE,
    VISUAL_EDIT_ERROR_REPAIR_PROPOSAL_NOT_SELECTED,
    VISUAL_EDIT_ERROR_REPAIR_PROPOSAL_STALE,
    VISUAL_EDIT_ERROR_REPAIR_REPLACEMENT_ASSET_INVALID,
    VISUAL_EDIT_ERROR_REPAIR_REPLACEMENT_OBSERVATION_NOT_ACCEPTED,
    VISUAL_EDIT_ERROR_REPAIR_SOURCE_RANGE_INVALID,
    VISUAL_EDIT_ERROR_REPAIR_VALIDATION_FAILED,
    RepairOperation,
    RepairProposal,
    RepairProposalDecision,
    RepairProposalOpsArtifact,
    RepairResult,
    RepairRun,
    RepairSourceRangeSpec,
    ReplaceAssignmentAssetOperation,
    ReplaceAssignmentSourceRangeOperation,
    ShotMediaAssignment,
    VisualEditPlan,
    VisualEditPlanBundle,
    repair_apply_idempotency_key,
    repair_decision_fingerprint,
    visual_edit_plan_content_fingerprint,
)
from otio_app.discovery_v2.persistence import visual_edit_repository as repo
from otio_app.discovery_v2.persistence.inventory_artifact_store import InventoryArtifactError
from otio_app.models import Project


@dataclass(frozen=True)
class RepairProposalResult:
    ok: bool
    message: str
    proposals: list[object]
    error_code: str | None = None


@dataclass(frozen=True)
class RepairDecisionResult:
    ok: bool
    message: str
    proposals: list[RepairProposal]
    error_code: str | None = None


@dataclass(frozen=True)
class RepairApplyResult:
    ok: bool
    message: str
    repair_run: RepairRun | None = None
    output_plan: VisualEditPlan | None = None
    ready: bool = False
    error_code: str | None = None
    idempotent: bool = False


@dataclass(frozen=True)
class RepairProposalView:
    proposal: RepairProposal
    artifact: RepairProposalOpsArtifact | None
    selectable: bool
    selected: bool
    current_asset_id: str | None
    proposed_asset_id: str | None
    current_range: RepairSourceRangeSpec | None
    proposed_range: RepairSourceRangeSpec | None
    affected_shot_id: str | None
    expected_effects: list[str]


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
        plan_fingerprint = visual_edit_plan_content_fingerprint(bundle)
        humanity_bundle = None
        if state.current_humanity_review_id:
            humanity_bundle = repo.get_humanity_review_bundle(
                conn, review_id=state.current_humanity_review_id
            )
        feasibility_bundle = None
        if state.current_feasibility_report_id:
            feasibility_bundle = repo.get_feasibility_report_bundle(
                conn, report_id=state.current_feasibility_report_id
            )
        request_input = {
            "plan": bundle.plan.model_dump(mode="json"),
            "shots": [shot.model_dump(mode="json") for shot in bundle.shots],
            "assignments": [assignment.model_dump(mode="json") for assignment in bundle.assignments],
            "candidates": list(context.package.get("candidates", [])),
            "humanity_review_id": state.current_humanity_review_id,
            "feasibility_report_id": state.current_feasibility_report_id,
            "source_plan_fingerprint": plan_fingerprint,
            "findings": []
            if humanity_bundle is None
            else [item.model_dump(mode="json") for item in humanity_bundle.findings],
            "feasibility_issues": []
            if feasibility_bundle is None
            else [item.model_dump(mode="json") for item in feasibility_bundle.issues],
            "provider": config.provider,
            "model_identifier": config.model_identifier,
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
            return RepairProposalResult(
                False, "Repair Proposal ungueltig.", [], VISUAL_EDIT_ERROR_REPAIR_PROPOSAL_INVALID
            )
        payload = response.editorial_repair_proposal
        artifacts_by_id = {item.proposal_id: item for item in payload.executable_artifacts}
        for proposal in payload.proposals:
            artifact = artifacts_by_id.get(proposal.proposal_id)
            if artifact is not None:
                if artifact.operations:
                    code = _validate_artifact_against_plan(
                        artifact,
                        bundle=bundle,
                        package=context.package,
                        expect_current=True,
                    )
                    if code is not None:
                        return RepairProposalResult(
                            False,
                            "Repair Proposal Operation ungueltig.",
                            [],
                            code,
                        )
                repo.save_repair_proposal_ops_json(project.project_root_path, artifact)
            repo.insert_repair_proposal(conn, proposal)
        conn.commit()
        return RepairProposalResult(
            True,
            "Repair Proposals erzeugt.",
            list(payload.proposals),
        )
    finally:
        conn.close()


def select_repair_proposals(
    project: Project,
    *,
    proposal_ids: list[str],
    actor: str = "user",
) -> RepairDecisionResult:
    return _decide_repair_proposals(
        project,
        proposal_ids=proposal_ids,
        decision="selected",
        actor=actor,
    )


def reject_repair_proposals(
    project: Project,
    *,
    proposal_ids: list[str],
    actor: str = "user",
) -> RepairDecisionResult:
    return _decide_repair_proposals(
        project,
        proposal_ids=proposal_ids,
        decision="rejected",
        actor=actor,
    )


def list_repair_proposal_views(project: Project) -> list[RepairProposalView]:
    project = require_discovery_project(project)
    conn = repo.open_visual_edit_registry(project.project_root_path)
    try:
        state = repo.get_project_state(conn, project_id=project.id)
        if state is None or state.current_visual_edit_plan_id is None:
            return []
        bundle = repo.get_plan_bundle(conn, plan_id=state.current_visual_edit_plan_id)
        if bundle is None:
            return []
        proposals = repo.list_repair_proposals(conn, plan_id=bundle.plan.plan_id)
        context = build_visual_edit_input_context(project, conn=conn, existing_plan=bundle.plan)
        plan_fp = visual_edit_plan_content_fingerprint(bundle)
    finally:
        conn.close()
    views: list[RepairProposalView] = []
    for proposal in proposals:
        artifact = repo.load_repair_proposal_ops_json(
            project.project_root_path, proposal_id=proposal.proposal_id
        )
        selectable = _proposal_is_selectable(
            proposal,
            artifact=artifact,
            bundle=bundle,
            package=context.package,
            current_plan_fingerprint=plan_fp,
        )
        selected = proposal.user_status == "selected" and selectable
        current_asset = None
        proposed_asset = None
        current_range = None
        proposed_range = None
        shot_id = None
        effects: list[str] = []
        if artifact and artifact.operations:
            first = artifact.operations[0]
            shot_id = first.source_shot_id
            effects = [str(item) for item in first.expected_effects]
            assignment = next(
                (
                    item
                    for item in bundle.assignments
                    if item.assignment_id == first.source_assignment_id
                ),
                None,
            )
            if assignment is not None:
                current_asset = assignment.asset_id
                if assignment.working_media_id and assignment.visual_observation_id:
                    current_range = RepairSourceRangeSpec(
                        working_media_id=assignment.working_media_id,
                        observation_id=assignment.visual_observation_id,
                        technical_shot_id=assignment.technical_shot_id,
                        in_seconds=assignment.technical_source_in_seconds,
                        out_seconds=assignment.technical_source_out_seconds,
                        in_frame=assignment.technical_source_in_frame,
                        out_frame=assignment.technical_source_out_frame,
                    )
            if isinstance(first, ReplaceAssignmentAssetOperation):
                proposed_asset = first.target_asset_id
                proposed_range = first.target_source_range
            elif isinstance(first, ReplaceAssignmentSourceRangeOperation):
                proposed_asset = first.asset_id
                proposed_range = first.source_range_after
        views.append(
            RepairProposalView(
                proposal=proposal,
                artifact=artifact,
                selectable=selectable,
                selected=selected,
                current_asset_id=current_asset,
                proposed_asset_id=proposed_asset,
                current_range=current_range,
                proposed_range=proposed_range,
                affected_shot_id=shot_id,
                expected_effects=effects,
            )
        )
    return views


def apply_selected_repair_proposals(
    project: Project,
    *,
    selected_proposal_ids: list[str] | None = None,
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
        plan_fp = visual_edit_plan_content_fingerprint(bundle)
        if selected_proposal_ids is None:
            selected_proposal_ids = [
                item.proposal_id
                for item in repo.list_repair_proposals(conn, plan_id=bundle.plan.plan_id)
                if item.user_status == "selected"
            ]
        if not selected_proposal_ids:
            return RepairApplyResult(
                False,
                "Keine Reparatur ausgewaehlt.",
                error_code=VISUAL_EDIT_ERROR_REPAIR_PROPOSAL_NOT_SELECTED,
            )
        proposals: list[RepairProposal] = []
        artifacts: list[RepairProposalOpsArtifact] = []
        decision_fps: list[str] = []
        for proposal_id in selected_proposal_ids:
            proposal = repo.get_repair_proposal(conn, proposal_id=proposal_id)
            if proposal is None:
                return RepairApplyResult(
                    False,
                    "Repair Proposal fehlt.",
                    error_code=VISUAL_EDIT_ERROR_REPAIR_PROPOSAL_INVALID,
                )
            if proposal.user_status == "applied":
                return RepairApplyResult(
                    False,
                    "Repair Proposal bereits angewendet.",
                    error_code=VISUAL_EDIT_ERROR_REPAIR_PROPOSAL_STALE,
                )
            if proposal.user_status != "selected":
                return RepairApplyResult(
                    False,
                    "Repair Proposal nicht ausgewaehlt.",
                    error_code=VISUAL_EDIT_ERROR_REPAIR_PROPOSAL_NOT_SELECTED,
                )
            artifact = repo.load_repair_proposal_ops_json(
                project.project_root_path, proposal_id=proposal_id
            )
            if artifact is None or not artifact.operations:
                return RepairApplyResult(
                    False,
                    "Repair Proposal ist nicht ausfuehrbar.",
                    error_code=VISUAL_EDIT_ERROR_REPAIR_PROPOSAL_NOT_EXECUTABLE,
                )
            stale_code = _stale_code(
                proposal,
                artifact=artifact,
                bundle=bundle,
                package=context.package,
                current_plan_fingerprint=plan_fp,
            )
            if stale_code is not None:
                repo.update_repair_proposal_status(
                    conn, proposal_id=proposal.proposal_id, status="superseded"
                )
                conn.commit()
                return RepairApplyResult(False, "Repair Proposal ist veraltet.", error_code=stale_code)
            decisions = repo.list_repair_proposal_decisions(
                project.project_root_path, proposal_id=proposal_id
            )
            selected_decisions = [item for item in decisions if item.decision == "selected"]
            if not selected_decisions:
                return RepairApplyResult(
                    False,
                    "Repair-Auswahlentscheidung fehlt.",
                    error_code=VISUAL_EDIT_ERROR_REPAIR_PROPOSAL_NOT_SELECTED,
                )
            decision_fps.append(selected_decisions[-1].decision_fingerprint)
            proposals.append(proposal)
            artifacts.append(artifact)

        apply_key = repair_apply_idempotency_key(
            source_plan_id=bundle.plan.plan_id,
            source_plan_fingerprint=plan_fp,
            selected_proposal_ids=list(selected_proposal_ids),
            decision_fingerprints=decision_fps,
        )
        existing_apply = repo.load_repair_apply_idempotency_json(
            project.project_root_path, idempotency_key=apply_key
        )
        if existing_apply is not None:
            run_id = str(existing_apply.get("repair_run_id") or "")
            output_plan_id = str(existing_apply.get("output_plan_id") or "")
            run = repo.get_repair_run(conn, run_id=run_id) if run_id else None
            output = repo.get_plan(conn, plan_id=output_plan_id) if output_plan_id else None
            return RepairApplyResult(
                True,
                "Repair bereits angewendet.",
                repair_run=run,
                output_plan=output,
                ready=evaluate_ready_for_editorial_review(conn, project_id=project.id),
                idempotent=True,
            )

        conflict = _detect_operation_conflicts(artifacts)
        if conflict is not None:
            return RepairApplyResult(False, conflict, error_code=VISUAL_EDIT_ERROR_REPAIR_CONFLICT)

        working = _clone_bundle_same_ids(bundle)
        before_fp = visual_edit_plan_content_fingerprint(working)
        changes: list[dict[str, object]] = []
        for artifact in artifacts:
            for operation in artifact.operations:
                applied_change = _apply_operation(working, operation, package=context.package)
                if isinstance(applied_change, str):
                    return RepairApplyResult(False, "Repair-Operation ungueltig.", error_code=applied_change)
                changes.append(applied_change)

        after_fp = visual_edit_plan_content_fingerprint(working)
        if after_fp == before_fp:
            return RepairApplyResult(
                False,
                "Repair aendert den Planinhalt nicht.",
                error_code=VISUAL_EDIT_ERROR_REPAIR_OPERATION_NO_EFFECT,
            )

        if not _plan_respects_e3_e4(working):
            return RepairApplyResult(
                False,
                "Repair erzeugt technische E3/E4-Blocker.",
                error_code=VISUAL_EDIT_ERROR_REPAIR_VALIDATION_FAILED,
            )
        feasibility = evaluate_feasibility(working, context.package)
        tech_blockers = [
            issue
            for issue in feasibility.issues
            if issue.severity == "blocking"
            and ("E3" in issue.technical_details or "E4" in issue.technical_details)
        ]
        if tech_blockers:
            return RepairApplyResult(
                False,
                "Repair erzeugt technische Blocker.",
                error_code=VISUAL_EDIT_ERROR_REPAIR_VALIDATION_FAILED,
            )

        output_bundle = _copy_bundle_as_new_version(
            conn,
            working,
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
            changes=changes,
            remaining_findings=[],
            remaining_feasibility_issues=[],
            created_at=_now(),
        )
        try:
            relative = repo.save_plan_json(project.project_root_path, output_bundle)
            repo.save_repair_run_json(project.project_root_path, repair_run, result)
            repo.save_repair_apply_idempotency_json(
                project.project_root_path,
                idempotency_key=apply_key,
                payload={
                    "idempotency_key": apply_key,
                    "repair_run_id": repair_run.run_id,
                    "output_plan_id": output_bundle.plan.plan_id,
                    "source_plan_id": bundle.plan.plan_id,
                    "source_plan_fingerprint": plan_fp,
                    "selected_proposal_ids": list(selected_proposal_ids),
                },
            )
        except InventoryArtifactError:
            return RepairApplyResult(
                False,
                "Repair Persistenz fehlgeschlagen.",
                error_code=VISUAL_EDIT_ERROR_REPAIR_APPLY_PERSIST_FAILED,
            )
        conn.execute("BEGIN IMMEDIATE")
        repo.update_plan_status(conn, plan_id=bundle.plan.plan_id, status="superseded")
        if state.current_humanity_review_id:
            repo.update_humanity_review_status(
                conn, review_id=state.current_humanity_review_id, status="stale"
            )
        if state.current_feasibility_report_id:
            repo.update_feasibility_report_status(
                conn, report_id=state.current_feasibility_report_id, status="stale"
            )
        repo.insert_plan_bundle(conn, output_bundle, relative)
        repo.insert_repair_run(conn, repair_run)
        repo.insert_repair_result(conn, result)
        for proposal in proposals:
            repo.update_repair_proposal_status(
                conn, proposal_id=proposal.proposal_id, status="applied"
            )
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


def _decide_repair_proposals(
    project: Project,
    *,
    proposal_ids: list[str],
    decision: str,
    actor: str,
) -> RepairDecisionResult:
    project = require_discovery_project(project)
    conn = repo.open_visual_edit_registry(project.project_root_path)
    try:
        state = repo.get_project_state(conn, project_id=project.id)
        if state is None or state.current_visual_edit_plan_id is None:
            return RepairDecisionResult(
                False, "Visual Edit Plan fehlt.", [], VISUAL_EDIT_ERROR_INPUT_STALE
            )
        bundle = repo.get_plan_bundle(conn, plan_id=state.current_visual_edit_plan_id)
        if bundle is None:
            return RepairDecisionResult(
                False, "Visual Edit Plan fehlt.", [], VISUAL_EDIT_ERROR_INPUT_STALE
            )
        context = build_visual_edit_input_context(project, conn=conn, existing_plan=bundle.plan)
        plan_fp = visual_edit_plan_content_fingerprint(bundle)
        updated: list[RepairProposal] = []
        for proposal_id in proposal_ids:
            proposal = repo.get_repair_proposal(conn, proposal_id=proposal_id)
            if proposal is None:
                return RepairDecisionResult(
                    False,
                    "Repair Proposal fehlt.",
                    [],
                    VISUAL_EDIT_ERROR_REPAIR_PROPOSAL_INVALID,
                )
            artifact = repo.load_repair_proposal_ops_json(
                project.project_root_path, proposal_id=proposal_id
            )
            if decision == "selected":
                if not _proposal_is_selectable(
                    proposal,
                    artifact=artifact,
                    bundle=bundle,
                    package=context.package,
                    current_plan_fingerprint=plan_fp,
                ):
                    return RepairDecisionResult(
                        False,
                        "Repair Proposal ist nicht auswaehlbar.",
                        [],
                        VISUAL_EDIT_ERROR_REPAIR_PROPOSAL_NOT_EXECUTABLE
                        if artifact is None or not artifact.operations
                        else VISUAL_EDIT_ERROR_REPAIR_PROPOSAL_STALE,
                    )
            proposal_fp = (
                artifact.artifact_fingerprint
                if artifact is not None
                else uuid5(NAMESPACE_URL, f"repair-meta:{proposal.proposal_id}").hex
            )
            decision_fp = repair_decision_fingerprint(
                proposal_id=proposal.proposal_id,
                decision=decision,
                proposal_fingerprint=proposal_fp,
                source_plan_fingerprint=plan_fp,
            )
            record = RepairProposalDecision(
                decision_id=str(uuid5(NAMESPACE_URL, f"repair-decision:{decision_fp}")),
                proposal_id=proposal.proposal_id,
                decision=decision,  # type: ignore[arg-type]
                actor=actor,
                created_at=_now(),
                proposal_fingerprint=proposal_fp,
                source_plan_fingerprint=plan_fp,
                decision_fingerprint=decision_fp,
            )
            _path, created = repo.append_repair_proposal_decision(
                project.project_root_path, record
            )
            del _path
            if created or proposal.user_status != decision:
                status = "selected" if decision == "selected" else "rejected"
                repo.update_repair_proposal_status(
                    conn, proposal_id=proposal.proposal_id, status=status
                )
            refreshed = repo.get_repair_proposal(conn, proposal_id=proposal.proposal_id)
            if refreshed is not None:
                updated.append(refreshed)
        conn.commit()
        label = "ausgewaehlt" if decision == "selected" else "abgelehnt"
        return RepairDecisionResult(True, f"Repair Proposals {label}.", updated)
    finally:
        conn.close()


def _proposal_is_selectable(
    proposal: RepairProposal,
    *,
    artifact: RepairProposalOpsArtifact | None,
    bundle: VisualEditPlanBundle,
    package: dict[str, object],
    current_plan_fingerprint: str,
) -> bool:
    if proposal.user_status in {"applied", "rejected", "superseded"}:
        return False
    if artifact is None or not artifact.operations:
        return False
    return (
        _stale_code(
            proposal,
            artifact=artifact,
            bundle=bundle,
            package=package,
            current_plan_fingerprint=current_plan_fingerprint,
        )
        is None
    )


def _stale_code(
    proposal: RepairProposal,
    *,
    artifact: RepairProposalOpsArtifact,
    bundle: VisualEditPlanBundle,
    package: dict[str, object],
    current_plan_fingerprint: str,
) -> str | None:
    if proposal.plan_id != bundle.plan.plan_id:
        return VISUAL_EDIT_ERROR_REPAIR_PROPOSAL_STALE
    if artifact.source_plan_id != bundle.plan.plan_id:
        return VISUAL_EDIT_ERROR_REPAIR_PROPOSAL_STALE
    if artifact.source_plan_fingerprint != current_plan_fingerprint:
        return VISUAL_EDIT_ERROR_REPAIR_PROPOSAL_STALE
    return _validate_artifact_against_plan(
        artifact,
        bundle=bundle,
        package=package,
        expect_current=True,
    )


def _validate_artifact_against_plan(
    artifact: RepairProposalOpsArtifact,
    *,
    bundle: VisualEditPlanBundle,
    package: dict[str, object],
    expect_current: bool,
) -> str | None:
    del expect_current
    if not artifact.source_issue_ids:
        return VISUAL_EDIT_ERROR_REPAIR_PROPOSAL_NOT_EXECUTABLE
    if not artifact.operations:
        return VISUAL_EDIT_ERROR_REPAIR_PROPOSAL_NOT_EXECUTABLE
    assignment_by_id = {item.assignment_id: item for item in bundle.assignments}
    candidates = _candidate_index(package)
    for operation in artifact.operations:
        assignment = assignment_by_id.get(operation.source_assignment_id)
        if assignment is None:
            return VISUAL_EDIT_ERROR_REPAIR_OPERATION_TARGET_MISSING
        if assignment.shot_id != operation.source_shot_id:
            return VISUAL_EDIT_ERROR_REPAIR_PROPOSAL_STALE
        if isinstance(operation, ReplaceAssignmentAssetOperation):
            if assignment.asset_id != operation.source_asset_id:
                return VISUAL_EDIT_ERROR_REPAIR_PROPOSAL_STALE
            target = candidates.get(operation.target_asset_id)
            if target is None:
                return VISUAL_EDIT_ERROR_REPAIR_REPLACEMENT_ASSET_INVALID
            if target.get("observation_id") != operation.target_source_range.observation_id:
                return VISUAL_EDIT_ERROR_REPAIR_REPLACEMENT_OBSERVATION_NOT_ACCEPTED
            if target.get("working_media_id") != operation.target_source_range.working_media_id:
                return VISUAL_EDIT_ERROR_REPAIR_REPLACEMENT_ASSET_INVALID
            range_code = _validate_target_range(
                operation.target_source_range, candidate=target, package=package
            )
            if range_code is not None:
                return range_code
        elif isinstance(operation, ReplaceAssignmentSourceRangeOperation):
            if assignment.asset_id != operation.asset_id:
                return VISUAL_EDIT_ERROR_REPAIR_PROPOSAL_STALE
            target = candidates.get(operation.asset_id)
            if target is None:
                return VISUAL_EDIT_ERROR_REPAIR_REPLACEMENT_ASSET_INVALID
            range_code = _validate_target_range(
                operation.source_range_after, candidate=target, package=package
            )
            if range_code is not None:
                return range_code
    return None


def _validate_target_range(
    spec: RepairSourceRangeSpec,
    *,
    candidate: dict[str, object],
    package: dict[str, object],
) -> str | None:
    del package
    if spec.observation_id != candidate.get("observation_id"):
        return VISUAL_EDIT_ERROR_REPAIR_REPLACEMENT_OBSERVATION_NOT_ACCEPTED
    if spec.working_media_id != candidate.get("working_media_id"):
        return VISUAL_EDIT_ERROR_REPAIR_REPLACEMENT_ASSET_INVALID
    media_kind = str(candidate.get("media_kind") or "")
    if media_kind == "image":
        if spec.in_seconds is not None or spec.out_seconds is not None:
            return VISUAL_EDIT_ERROR_REPAIR_SOURCE_RANGE_INVALID
        return None
    if spec.in_seconds is None or spec.out_seconds is None:
        return VISUAL_EDIT_ERROR_REPAIR_SOURCE_RANGE_INVALID
    tech_shots = candidate.get("technical_shots", [])
    tech_shots = [item for item in tech_shots if isinstance(item, dict)] if isinstance(tech_shots, list) else []
    if spec.technical_shot_id:
        tech = next(
            (
                item
                for item in tech_shots
                if str(item.get("technical_shot_id")) == spec.technical_shot_id
            ),
            None,
        )
        if tech is None:
            return VISUAL_EDIT_ERROR_REPAIR_SOURCE_RANGE_INVALID
        start = float(tech["start_seconds"])
        end = float(tech["end_seconds"])
        if float(spec.in_seconds) < start - 1e-6 or float(spec.out_seconds) > end + 1e-6:
            return VISUAL_EDIT_ERROR_REPAIR_SOURCE_RANGE_INVALID
    if float(spec.out_seconds) <= float(spec.in_seconds):
        return VISUAL_EDIT_ERROR_REPAIR_SOURCE_RANGE_INVALID
    return None


def _candidate_index(package: dict[str, object]) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for candidate in package.get("candidates", []):
        if isinstance(candidate, dict) and candidate.get("asset_id"):
            result[str(candidate["asset_id"])] = candidate
    return result


def _detect_operation_conflicts(artifacts: list[RepairProposalOpsArtifact]) -> str | None:
    by_assignment: dict[str, list[RepairOperation]] = {}
    issue_owners: dict[str, set[str]] = {}
    for artifact in artifacts:
        for operation in artifact.operations:
            by_assignment.setdefault(operation.source_assignment_id, []).append(operation)
            for issue_id in operation.addressed_issue_ids:
                issue_owners.setdefault(issue_id, set()).add(artifact.proposal_id)
    for assignment_id, ops in by_assignment.items():
        del assignment_id
        if len(ops) <= 1:
            continue
        fingerprints = {op.operation_fingerprint for op in ops}
        if len(fingerprints) > 1:
            return "Konfliktiertende Repair-Operationen fuer dasselbe Assignment."
    for issue_id, owners in issue_owners.items():
        del issue_id
        if len(owners) > 1:
            # Compatible only if operations are identical fingerprints across proposals.
            related = [
                op
                for artifact in artifacts
                if artifact.proposal_id in owners
                for op in artifact.operations
            ]
            if len({op.operation_fingerprint for op in related}) > 1:
                return "Konfliktiertende Repair-Proposals fuer dasselbe Issue."
    return None


def _apply_operation(
    bundle: VisualEditPlanBundle,
    operation: RepairOperation,
    *,
    package: dict[str, object],
) -> dict[str, object] | str:
    assignment_by_id = {item.assignment_id: item for item in bundle.assignments}
    assignment = assignment_by_id.get(operation.source_assignment_id)
    if assignment is None:
        return VISUAL_EDIT_ERROR_REPAIR_OPERATION_TARGET_MISSING
    if isinstance(operation, ReplaceAssignmentAssetOperation):
        target = _candidate_index(package).get(operation.target_asset_id)
        if target is None:
            return VISUAL_EDIT_ERROR_REPAIR_REPLACEMENT_ASSET_INVALID
        updated = assignment.model_copy(
            update={
                "asset_id": operation.target_asset_id,
                "working_media_id": operation.target_source_range.working_media_id,
                "visual_observation_id": operation.target_source_range.observation_id,
                "technical_shot_id": operation.target_source_range.technical_shot_id,
                "technical_source_in_seconds": operation.target_source_range.in_seconds,
                "technical_source_out_seconds": operation.target_source_range.out_seconds,
                "technical_source_in_frame": operation.target_source_range.in_frame,
                "technical_source_out_frame": operation.target_source_range.out_frame,
                "status": "resolved",
            }
        )
        _replace_assignment(bundle, updated)
        return {
            "proposal_operation_id": operation.operation_id,
            "operation_type": operation.operation_type,
            "assignment_id": operation.source_assignment_id,
            "from_asset_id": operation.source_asset_id,
            "to_asset_id": operation.target_asset_id,
        }
    if isinstance(operation, ReplaceAssignmentSourceRangeOperation):
        updated = assignment.model_copy(
            update={
                "working_media_id": operation.source_range_after.working_media_id,
                "visual_observation_id": operation.source_range_after.observation_id,
                "technical_shot_id": operation.source_range_after.technical_shot_id,
                "technical_source_in_seconds": operation.source_range_after.in_seconds,
                "technical_source_out_seconds": operation.source_range_after.out_seconds,
                "technical_source_in_frame": operation.source_range_after.in_frame,
                "technical_source_out_frame": operation.source_range_after.out_frame,
                "status": "resolved",
            }
        )
        _replace_assignment(bundle, updated)
        return {
            "proposal_operation_id": operation.operation_id,
            "operation_type": operation.operation_type,
            "assignment_id": operation.source_assignment_id,
            "asset_id": operation.asset_id,
            "from_range": [
                operation.source_range_before.in_seconds,
                operation.source_range_before.out_seconds,
            ],
            "to_range": [
                operation.source_range_after.in_seconds,
                operation.source_range_after.out_seconds,
            ],
        }
    return VISUAL_EDIT_ERROR_REPAIR_PROPOSAL_NOT_EXECUTABLE


def _replace_assignment(bundle: VisualEditPlanBundle, updated: ShotMediaAssignment) -> None:
    bundle.assignments = [
        updated if item.assignment_id == updated.assignment_id else item
        for item in bundle.assignments
    ]


def _plan_respects_e3_e4(bundle: VisualEditPlanBundle) -> bool:
    counts: dict[str, int] = {}
    for assignment in bundle.assignments:
        if not assignment.asset_id:
            continue
        counts[assignment.asset_id] = counts.get(assignment.asset_id, 0) + 1
        if counts[assignment.asset_id] > ASSET_REUSE_MAX:
            return False
    video = [
        item
        for item in bundle.assignments
        if item.technical_source_in_seconds is not None
        and item.technical_source_out_seconds is not None
    ]
    for index, left in enumerate(video):
        for right in video[index + 1 :]:
            if left.asset_id != right.asset_id or left.working_media_id != right.working_media_id:
                continue
            overlap = max(
                0.0,
                min(float(left.technical_source_out_seconds), float(right.technical_source_out_seconds))
                - max(float(left.technical_source_in_seconds), float(right.technical_source_in_seconds)),
            )
            shortest = min(
                float(left.technical_source_out_seconds) - float(left.technical_source_in_seconds),
                float(right.technical_source_out_seconds) - float(right.technical_source_in_seconds),
            )
            ratio = 0.0 if shortest <= 0 else overlap / shortest
            if ratio >= SOURCE_RANGE_OVERLAP_RATIO_MAX:
                return False
    return True


def _clone_bundle_same_ids(bundle: VisualEditPlanBundle) -> VisualEditPlanBundle:
    return VisualEditPlanBundle(
        plan=bundle.plan.model_copy(deep=True),
        shots=[shot.model_copy(deep=True) for shot in bundle.shots],
        assignments=[item.model_copy(deep=True) for item in bundle.assignments],
        transitions=[item.model_copy(deep=True) for item in bundle.transitions],
    )


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
    return VisualEditPlanBundle(
        plan=plan, shots=shots, assignments=assignments, transitions=transitions
    )


__all__ = [
    "RepairApplyResult",
    "RepairDecisionResult",
    "RepairProposalResult",
    "RepairProposalView",
    "apply_selected_repair_proposals",
    "list_repair_proposal_views",
    "propose_editorial_repairs",
    "reject_repair_proposals",
    "select_repair_proposals",
]
