"""Worker: Discovery V2 editorial text pipeline via central text gateway."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from otio_app.discovery_v2.adapters.text_config import load_text_config
from otio_app.discovery_v2.adapters.text_gateway import (
    DiscoveryTextGateway,
    TextGatewayError,
)
from otio_app.discovery_v2.application.observation_review_service import (
    list_editorial_ready_observations,
)
from otio_app.discovery_v2.domain.editorial import (
    EDITORIAL_ERROR_COVERAGE_ARTIFACT_PUBLISH_FAILED,
    EDITORIAL_ERROR_COVERAGE_AUDIT_PERSIST_FAILED,
    EDITORIAL_ERROR_COVERAGE_CURRENT_STATE_UPDATE_FAILED,
    EDITORIAL_ERROR_INPUT_STALE,
    EDITORIAL_ERROR_PROJECT_BRIEF_MISSING,
    EDITORIAL_ERROR_REGISTRY_WRITE_FAILED,
    EDITORIAL_ERROR_STRUCTURE_BEATS_MISSING,
    EDITORIAL_ERROR_STRUCTURE_INCOMPLETE,
    EDITORIAL_ERROR_STRUCTURE_SENTENCES_INCOMPLETE,
    EDITORIAL_ERROR_STRUCTURE_VISUAL_INTENTS_MISSING,
    EDITORIAL_RUN_SCOPE_COVERAGE,
    EDITORIAL_RUN_SCOPE_NARRATIVE,
    EDITORIAL_RUN_SCOPE_SCRIPT,
    EDITORIAL_RUN_SCOPE_STRUCTURE,
    CoverageAudit,
    EditorialAttempt,
    EditorialAttemptStatus,
    EditorialProjectState,
    EditorialProjectStateStatus,
    EditorialReadyObservationInput,
    EditorialRun,
    EditorialRunStatus,
    HookUserStatus,
    HookVariant,
    NarrativePlanStatus,
    ProjectBrief,
    ScriptDraft,
    ScriptDraftStatus,
    Sentence,
    Claim,
    VisualBeat,
    VisualIntent,
    TextGatewayRequest,
    compute_observation_set_fingerprint,
)
from otio_app.discovery_v2.persistence import editorial_repository as repo
from otio_app.models import Project, ProjectMode


class EditorialWorkerError(Exception):
    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message or code


def _now() -> datetime:
    return datetime.now(timezone.utc)


def process_editorial_run(project_root: Path, run_id: str) -> None:
    root = Path(project_root).expanduser().resolve()
    conn = repo.open_editorial_registry(root)
    try:
        run = repo.get_editorial_run(conn, run_id=run_id)
        if run is None:
            return
        run = run.model_copy(
            update={
                "status": EditorialRunStatus.RUNNING,
                "started_at": run.started_at or _now(),
            }
        )
        repo.update_editorial_run(conn, run)
        conn.commit()
        try:
            if run.scope == EDITORIAL_RUN_SCOPE_NARRATIVE:
                run = _process_narrative(conn, root, run)
            elif run.scope == EDITORIAL_RUN_SCOPE_SCRIPT:
                run = _process_script(conn, root, run)
            elif run.scope == EDITORIAL_RUN_SCOPE_STRUCTURE:
                run = _process_structure(conn, root, run)
            elif run.scope == EDITORIAL_RUN_SCOPE_COVERAGE:
                run = _process_coverage(conn, root, run)
            else:
                raise EditorialWorkerError("unsupported_editorial_scope", run.scope)
        except TextGatewayError as exc:
            run = _fail_run(conn, run, exc.code, exc.message)
        except EditorialWorkerError as exc:
            run = _fail_run(conn, run, exc.code, exc.message)
        except Exception as exc:  # noqa: BLE001
            run = _fail_run(conn, run, EDITORIAL_ERROR_REGISTRY_WRITE_FAILED, str(exc))
        _write_report(conn, root, run)
    finally:
        conn.close()
        repo.cleanup_editorial_temp(root, run_id=run_id)


def _process_narrative(conn, root: Path, run: EditorialRun) -> EditorialRun:
    config = load_text_config()
    brief = _require_brief(conn, run)
    observations = _observations(root, run.project_id)
    observation_fingerprint = compute_observation_set_fingerprint(observations)
    hooks_existing: list[HookVariant] = []
    cached_plan = repo.get_active_narrative_plan(conn, project_id=run.project_id)
    if (
        cached_plan is not None
        and cached_plan.project_brief_id == brief.project_brief_id
        and cached_plan.input_observation_fingerprint == observation_fingerprint
    ):
        hooks_existing = repo.list_hook_variants(
            conn, narrative_plan_id=cached_plan.narrative_plan_id
        )
        _record_reused_attempt(
            conn,
            root,
            run,
            request_kind="narrative",
            prompt_version=config.prompts["narrative"],
            response_schema_version=config.response_schemas["narrative"],
            input_fingerprint=observation_fingerprint,
            payload={"narrative_plan_id": cached_plan.narrative_plan_id, "reused": True},
        )
        return _complete_run(
            conn,
            run.model_copy(update={"narrative_plan_id": cached_plan.narrative_plan_id}),
        )
    request = TextGatewayRequest(
        project_id=run.project_id,
        run_id=run.run_id,
        request_kind="narrative",
        prompt=config.prompts["narrative"],
        provider=config.provider,
        model_identifier=config.model_identifier,
        gateway_version=config.gateway_version,
        prompt_version=config.prompts["narrative"],
        response_schema_version=config.response_schemas["narrative"],
        project_brief=brief,
        observations=observations,
        candidate_asset_ids=[obs.asset_id for obs in observations],
        input_fingerprint=observation_fingerprint,
    )
    attempt = _start_attempt(conn, run, request)
    try:
        response = DiscoveryTextGateway(config=config).generate(request)
        if response.narrative is None:
            raise EditorialWorkerError(EDITORIAL_ERROR_INPUT_STALE, "Narrative response missing.")
        plan = response.narrative.narrative_plan
        hooks = response.narrative.hooks
        relative = repo.save_narrative_json(root, plan, hooks)
        conn.execute("BEGIN IMMEDIATE")
        active = repo.get_active_narrative_plan(conn, project_id=run.project_id)
        if active is not None:
            repo.update_narrative_plan_status(
                conn,
                narrative_plan_id=active.narrative_plan_id,
                status=NarrativePlanStatus.SUPERSEDED,
            )
        repo.insert_narrative_plan(conn, plan, relative)
        for hook in hooks:
            hook_relative = repo.save_hook_json(root, hook)
            repo.insert_hook_variant(conn, hook, hook_relative)
        repo.upsert_project_state(
            conn,
            EditorialProjectState(
                project_id=run.project_id,
                active_brief_id=brief.project_brief_id,
                active_narrative_plan_id=plan.narrative_plan_id,
                selected_hook_id=None,
                active_script_id=None,
                active_coverage_audit_id=None,
                observation_fingerprint=observation_fingerprint,
                status=EditorialProjectStateStatus.ACTIVE,
                updated_at=_now(),
            ),
        )
        _complete_attempt(conn, root, attempt, {"narrative_plan": plan.model_dump(mode="json"), "hooks": [h.model_dump(mode="json") for h in hooks]})
        run = run.model_copy(update={"narrative_plan_id": plan.narrative_plan_id})
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    del hooks_existing
    return _complete_run(conn, run)


def _process_script(conn, root: Path, run: EditorialRun) -> EditorialRun:
    config = load_text_config()
    brief = _require_brief(conn, run)
    narrative = repo.get_active_narrative_plan(conn, project_id=run.project_id)
    state = repo.get_project_state(conn, project_id=run.project_id)
    if narrative is None or state is None or not state.selected_hook_id:
        raise EditorialWorkerError(EDITORIAL_ERROR_INPUT_STALE, "Narrative plan or selected hook missing.")
    hooks = repo.list_hook_variants(conn, narrative_plan_id=narrative.narrative_plan_id)
    observations = _observations(root, run.project_id)
    observation_fingerprint = compute_observation_set_fingerprint(observations)
    request = TextGatewayRequest(
        project_id=run.project_id,
        run_id=run.run_id,
        request_kind="script",
        prompt=config.prompts["script"],
        provider=config.provider,
        model_identifier=config.model_identifier,
        gateway_version=config.gateway_version,
        prompt_version=config.prompts["script"],
        response_schema_version=config.response_schemas["script"],
        project_brief=brief,
        narrative_plan=narrative,
        hooks=hooks,
        selected_hook_id=state.selected_hook_id,
        observations=observations,
        candidate_asset_ids=[obs.asset_id for obs in observations],
        input_fingerprint=observation_fingerprint,
    )
    attempt = _start_attempt(conn, run, request)
    response = DiscoveryTextGateway(config=config).generate(request)
    if response.script is None:
        raise EditorialWorkerError(EDITORIAL_ERROR_INPUT_STALE, "Script response missing.")
    bundle = response.script
    relative = repo.save_script_bundle_json(
        root,
        script=bundle.script,
        sentences=bundle.sentences,
        claims=bundle.claims,
        visual_beats=bundle.visual_beats,
        visual_intents=bundle.visual_intents,
    )
    try:
        conn.execute("BEGIN IMMEDIATE")
        active_script = repo.get_active_script(conn, project_id=run.project_id)
        if active_script is not None:
            repo.update_script_status(
                conn,
                script_id=active_script.script_id,
                status=ScriptDraftStatus.SUPERSEDED,
            )
        repo.insert_script_bundle(
            conn,
            script=bundle.script,
            sentences=bundle.sentences,
            claims=bundle.claims,
            visual_beats=bundle.visual_beats,
            visual_intents=bundle.visual_intents,
            relative_json_path=relative,
        )
        repo.upsert_project_state(
            conn,
            (state.model_copy(
                update={
                    "active_script_id": bundle.script.script_id,
                    "active_coverage_audit_id": None,
                    "observation_fingerprint": observation_fingerprint,
                    "status": EditorialProjectStateStatus.ACTIVE,
                    "updated_at": _now(),
                }
            )),
        )
        _complete_attempt(conn, root, attempt, _bundle_payload(bundle))
        run = run.model_copy(update={"script_id": bundle.script.script_id})
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return _complete_run(conn, run)


def _process_structure(conn, root: Path, run: EditorialRun) -> EditorialRun:
    config = load_text_config()
    script = _require_script(conn, run)
    brief = repo.get_project_brief(conn, project_brief_id=script.project_brief_id)
    narrative = repo.get_narrative_plan(conn, narrative_plan_id=script.narrative_plan_id)
    observations = _observations(root, run.project_id)
    observation_fingerprint = compute_observation_set_fingerprint(observations)
    request = TextGatewayRequest(
        project_id=run.project_id,
        run_id=run.run_id,
        request_kind="structure",
        prompt=config.prompts["structure"],
        provider=config.provider,
        model_identifier=config.model_identifier,
        gateway_version=config.gateway_version,
        prompt_version=config.prompts["structure"],
        response_schema_version=config.response_schemas["structure"],
        project_brief=brief,
        narrative_plan=narrative,
        script=script,
        observations=observations,
        candidate_asset_ids=[obs.asset_id for obs in observations],
        input_fingerprint=observation_fingerprint,
    )
    attempt = _start_attempt(conn, run, request)
    response = DiscoveryTextGateway(config=config).generate(request)
    if response.script is None:
        raise EditorialWorkerError(EDITORIAL_ERROR_INPUT_STALE, "Structure response missing.")
    bundle = response.script
    structure_error = _structure_completeness_error(
        sentences=bundle.sentences,
        claims=bundle.claims,
        visual_beats=bundle.visual_beats,
        visual_intents=bundle.visual_intents,
    )
    if structure_error is not None:
        # Fail-closed: do not persist an incomplete structure or clear pending.
        raise EditorialWorkerError(
            structure_error,
            "Structure response is incomplete; script remains structure_pending.",
        )
    # Canonical transition after a complete structure payload.
    finalized_script = bundle.script.model_copy(
        update={"status": ScriptDraftStatus.REVIEW_REQUESTED}
    )
    relative = repo.save_script_bundle_json(
        root,
        script=finalized_script,
        sentences=bundle.sentences,
        claims=bundle.claims,
        visual_beats=bundle.visual_beats,
        visual_intents=bundle.visual_intents,
    )
    try:
        conn.execute("BEGIN IMMEDIATE")
        repo.replace_script_structure(
            conn,
            script=finalized_script,
            sentences=bundle.sentences,
            claims=bundle.claims,
            visual_beats=bundle.visual_beats,
            visual_intents=bundle.visual_intents,
            relative_json_path=relative,
        )
        state = repo.get_project_state(conn, project_id=run.project_id)
        if state is not None:
            repo.upsert_project_state(
                conn,
                state.model_copy(
                    update={
                        "active_script_id": finalized_script.script_id,
                        "active_coverage_audit_id": None,
                        "observation_fingerprint": observation_fingerprint,
                        "status": EditorialProjectStateStatus.ACTIVE,
                        "updated_at": _now(),
                    }
                ),
            )
        finalized_payload = _bundle_payload(bundle)
        finalized_payload["script"] = finalized_script.model_dump(mode="json")
        _complete_attempt(conn, root, attempt, finalized_payload)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return _complete_run(
        conn, run.model_copy(update={"script_id": finalized_script.script_id})
    )


def _process_coverage(conn, root: Path, run: EditorialRun) -> EditorialRun:
    config = load_text_config()
    script = _require_script(conn, run)
    bundle = repo.get_script_bundle(conn, script_id=script.script_id)
    if bundle is None:
        raise EditorialWorkerError(EDITORIAL_ERROR_INPUT_STALE, "Script bundle missing.")
    sentences = [Sentence.model_validate(item) for item in bundle.get("sentences", [])]
    claims = [Claim.model_validate(item) for item in bundle.get("claims", [])]
    beats = [VisualBeat.model_validate(item) for item in bundle.get("visual_beats", [])]
    intents = [VisualIntent.model_validate(item) for item in bundle.get("visual_intents", [])]
    observations = _observations(root, run.project_id)
    observation_fingerprint = compute_observation_set_fingerprint(observations)
    request = TextGatewayRequest(
        project_id=run.project_id,
        run_id=run.run_id,
        request_kind="coverage",
        prompt=config.prompts["coverage"],
        provider=config.provider,
        model_identifier=config.model_identifier,
        gateway_version=config.gateway_version,
        prompt_version=config.prompts["coverage"],
        response_schema_version=config.response_schemas["coverage"],
        script=script,
        sentences=sentences,
        claims=claims,
        visual_beats=beats,
        visual_intents=intents,
        observations=observations,
        candidate_asset_ids=[obs.asset_id for obs in observations],
        input_fingerprint=observation_fingerprint,
    )
    attempt = _start_attempt(conn, run, request)
    response = DiscoveryTextGateway(config=config).generate(request)
    if response.coverage is None:
        raise EditorialWorkerError(EDITORIAL_ERROR_INPUT_STALE, "Coverage response missing.")
    audit = response.coverage.coverage_audit
    dedup_marker = repo.load_coverage_run_dedup_marker(
        root, run_id=run.run_id
    )
    canonical_fp = None
    if isinstance(dedup_marker, dict):
        raw_fp = dedup_marker.get("canonical_coverage_input_fingerprint")
        if isinstance(raw_fp, str) and raw_fp.strip():
            canonical_fp = raw_fp.strip()
    if canonical_fp:
        audit = audit.model_copy(
            update={"canonical_coverage_input_fingerprint": canonical_fp}
        )

    # Preserve prior current audit until the new audit is fully persisted.
    prior_state = repo.get_project_state(conn, project_id=run.project_id)
    prior_audit_id = None if prior_state is None else prior_state.active_coverage_audit_id

    existing = repo.get_coverage_audit(conn, coverage_audit_id=audit.coverage_audit_id)
    if existing is not None:
        # Idempotent reuse of an already-persisted audit identity.
        relative = repo.save_coverage_json(root, existing)
        try:
            conn.execute("BEGIN IMMEDIATE")
            state = repo.get_project_state(conn, project_id=run.project_id)
            if state is not None:
                repo.upsert_project_state(
                    conn,
                    state.model_copy(
                        update={
                            "active_coverage_audit_id": existing.coverage_audit_id,
                            "observation_fingerprint": observation_fingerprint,
                            "status": EditorialProjectStateStatus.ACTIVE,
                            "updated_at": _now(),
                        }
                    ),
                )
            _complete_attempt(
                conn,
                root,
                attempt,
                {"coverage_audit": existing.model_dump(mode="json"), "reused": True},
            )
            conn.commit()
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            raise EditorialWorkerError(
                EDITORIAL_ERROR_COVERAGE_CURRENT_STATE_UPDATE_FAILED,
                _sanitize_stage_error(exc),
            ) from exc
        return _complete_run(conn, run)

    try:
        relative = repo.save_coverage_json(root, audit)
    except Exception as exc:  # noqa: BLE001
        raise EditorialWorkerError(
            EDITORIAL_ERROR_COVERAGE_ARTIFACT_PUBLISH_FAILED,
            _sanitize_stage_error(exc),
        ) from exc

    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            repo.insert_coverage_audit(conn, audit, relative)
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            # Race / deterministic-id collision: never promote a failed write.
            if prior_state is not None and prior_audit_id:
                # Ensure current pointer is unchanged after rollback.
                pass
            raise EditorialWorkerError(
                EDITORIAL_ERROR_COVERAGE_AUDIT_PERSIST_FAILED,
                _sanitize_stage_error(exc),
            ) from exc
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            raise EditorialWorkerError(
                EDITORIAL_ERROR_COVERAGE_AUDIT_PERSIST_FAILED,
                _sanitize_stage_error(exc),
            ) from exc
        try:
            state = repo.get_project_state(conn, project_id=run.project_id)
            if state is not None:
                repo.upsert_project_state(
                    conn,
                    state.model_copy(
                        update={
                            "active_coverage_audit_id": audit.coverage_audit_id,
                            "observation_fingerprint": observation_fingerprint,
                            "status": EditorialProjectStateStatus.ACTIVE,
                            "updated_at": _now(),
                        }
                    ),
                )
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            raise EditorialWorkerError(
                EDITORIAL_ERROR_COVERAGE_CURRENT_STATE_UPDATE_FAILED,
                _sanitize_stage_error(exc),
            ) from exc
        _complete_attempt(conn, root, attempt, {"coverage_audit": audit.model_dump(mode="json")})
        conn.commit()
    except EditorialWorkerError:
        raise
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        raise EditorialWorkerError(
            EDITORIAL_ERROR_REGISTRY_WRITE_FAILED,
            _sanitize_stage_error(exc),
        ) from exc
    return _complete_run(conn, run)


def _sanitize_stage_error(exc: BaseException) -> str:
    text = " ".join(str(exc).split())
    return text[:500] if text else "coverage_persist_failed"


def _start_attempt(conn, run: EditorialRun, request: TextGatewayRequest) -> EditorialAttempt:
    attempt = EditorialAttempt(
        attempt_id=repo.new_editorial_attempt_id(),
        run_id=run.run_id,
        project_id=run.project_id,
        request_kind=request.request_kind,
        provider=request.provider,
        model_identifier=request.model_identifier,
        gateway_version=request.gateway_version,
        prompt_version=request.prompt_version,
        response_schema_version=request.response_schema_version,
        input_fingerprint=request.input_fingerprint,
        status=EditorialAttemptStatus.RUNNING,
        created_at=_now(),
    )
    repo.insert_editorial_attempt(conn, attempt)
    conn.commit()
    return attempt


def _record_reused_attempt(
    conn,
    root: Path,
    run: EditorialRun,
    *,
    request_kind: str,
    prompt_version: str,
    response_schema_version: str,
    input_fingerprint: str,
    payload: dict,
) -> None:
    config = load_text_config()
    attempt = EditorialAttempt(
        attempt_id=repo.new_editorial_attempt_id(),
        run_id=run.run_id,
        project_id=run.project_id,
        request_kind=request_kind,
        provider=config.provider,
        model_identifier=config.model_identifier,
        gateway_version=config.gateway_version,
        prompt_version=prompt_version,
        response_schema_version=response_schema_version,
        input_fingerprint=input_fingerprint,
        status=EditorialAttemptStatus.REUSED,
        error_code="reused",
        error_message="Vorhandenes Editorial-Artefakt wiederverwendet.",
        created_at=_now(),
        completed_at=_now(),
    )
    relative = repo.save_editorial_attempt_json(root, attempt, payload)
    repo.insert_editorial_attempt(conn, attempt.model_copy(update={"relative_json_path": relative}))
    conn.commit()


def _complete_attempt(conn, root: Path, attempt: EditorialAttempt, payload: dict) -> None:
    relative = repo.save_editorial_attempt_json(root, attempt, payload)
    repo.update_editorial_attempt(
        conn,
        attempt.model_copy(
            update={
                "status": EditorialAttemptStatus.COMPLETED,
                "relative_json_path": relative,
                "completed_at": _now(),
            }
        ),
    )


def _complete_run(conn, run: EditorialRun) -> EditorialRun:
    final = run.model_copy(
        update={"status": EditorialRunStatus.COMPLETED, "finished_at": _now()}
    )
    repo.update_editorial_run(conn, final)
    conn.commit()
    return final


def _fail_run(conn, run: EditorialRun, code: str, message: str) -> EditorialRun:
    for attempt in repo.list_editorial_attempts(conn, run_id=run.run_id):
        if attempt.status == EditorialAttemptStatus.RUNNING:
            repo.update_editorial_attempt(
                conn,
                attempt.model_copy(
                    update={
                        "status": EditorialAttemptStatus.FAILED,
                        "error_code": code,
                        "error_message": message,
                        "completed_at": _now(),
                    }
                ),
            )
    failed = run.model_copy(
        update={
            "status": EditorialRunStatus.FAILED,
            "error_code": code,
            "error_message": message,
            "finished_at": _now(),
        }
    )
    repo.update_editorial_run(conn, failed)
    conn.commit()
    return failed


def _write_report(conn, root: Path, run: EditorialRun) -> None:
    payload = {
        "run": run.model_dump(mode="json"),
        "attempts": [
            attempt.model_dump(mode="json")
            for attempt in repo.list_editorial_attempts(conn, run_id=run.run_id)
        ],
    }
    relative = repo.save_editorial_run_report(root, run, payload)
    repo.update_editorial_run(conn, run.model_copy(update={"relative_report_path": relative}))
    conn.commit()


def _require_brief(conn, run: EditorialRun) -> ProjectBrief:
    if run.brief_id:
        brief = repo.get_project_brief(conn, project_brief_id=run.brief_id)
    else:
        brief = repo.get_active_project_brief(conn, project_id=run.project_id)
    if brief is None:
        raise EditorialWorkerError(EDITORIAL_ERROR_PROJECT_BRIEF_MISSING)
    return brief


def _require_script(conn, run: EditorialRun) -> ScriptDraft:
    script = None
    if run.script_id:
        script = repo.get_script_draft(conn, script_id=run.script_id)
    if script is None:
        script = repo.get_active_script(conn, project_id=run.project_id)
    if script is None:
        raise EditorialWorkerError(EDITORIAL_ERROR_INPUT_STALE, "Active script missing.")
    return script


def _observations(root: Path, project_id: str) -> list[EditorialReadyObservationInput]:
    project = Project(
        id=project_id,
        name="Editorial Worker",
        project_root=str(root),
        work_dir=str(root / "_otio"),
        project_mode=ProjectMode.DISCOVERY_V2,
        asset_subdir_names=[],
        selected_asset_subdirs=[],
    )
    return [
        EditorialReadyObservationInput(
            observation_id=item.observation_id,
            asset_id=item.asset_id,
            analysis_identity_id=item.analysis_identity_id,
            working_media_id=item.working_media_id,
            summary=item.summary,
            evidence_frame_ids=list(item.evidence_frame_ids),
            geographic_confidence=item.geographic_confidence,
            synthetic_confidence=item.synthetic_confidence,
            uncertainty_notes=list(item.uncertainty_notes),
            observation_sha256=item.observation_sha256,
            frame_set_fingerprint=item.frame_set_fingerprint,
        )
        for item in list_editorial_ready_observations(project)
    ]


def _structure_completeness_error(
    *,
    sentences: list,
    claims: list,
    visual_beats: list,
    visual_intents: list,
) -> str | None:
    """Return a fail-closed error code when structure payload is incomplete."""

    if not sentences:
        return EDITORIAL_ERROR_STRUCTURE_SENTENCES_INCOMPLETE
    if not visual_beats:
        return EDITORIAL_ERROR_STRUCTURE_BEATS_MISSING
    if not visual_intents:
        return EDITORIAL_ERROR_STRUCTURE_VISUAL_INTENTS_MISSING
    if not claims:
        return EDITORIAL_ERROR_STRUCTURE_INCOMPLETE
    return None


def _bundle_payload(bundle) -> dict:
    return {
        "script": bundle.script.model_dump(mode="json"),
        "sentences": [item.model_dump(mode="json") for item in bundle.sentences],
        "claims": [item.model_dump(mode="json") for item in bundle.claims],
        "visual_beats": [item.model_dump(mode="json") for item in bundle.visual_beats],
        "visual_intents": [item.model_dump(mode="json") for item in bundle.visual_intents],
    }


__all__ = ["process_editorial_run"]
