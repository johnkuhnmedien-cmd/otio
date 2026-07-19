"""Application service for Phase 10 local supplementation workflows."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from otio_app.discovery_v2.adapters.stock_config import load_stock_config
from otio_app.discovery_v2.adapters.stock_gateway import StockGatewayError, StockSearchGateway
from otio_app.discovery_v2.adapters.supplementation_job_launcher import (
    get_supplementation_job_launcher,
)
from otio_app.discovery_v2.application.coverage_gap_service import (
    materialize_gaps_from_current_coverage,
)
from otio_app.discovery_v2.application.inventory_service import (
    InventoryServiceError,
    require_discovery_project,
)
from otio_app.discovery_v2.domain.editorial import compute_text_sha256
from otio_app.discovery_v2.domain.supplementation import (
    FAKE_STOCK_ADAPTER_VERSION,
    MAX_SEARCH_ATTEMPTS_PER_GAP_VERSION,
    STOCK_PROVIDER_FAKE,
    SUPPLEMENTATION_ERROR_ANALYSIS_RUN_ALREADY_ACTIVE,
    SUPPLEMENTATION_ERROR_COVERAGE_GAP_MISSING,
    SUPPLEMENTATION_ERROR_EDITORIAL_RUN_ALREADY_ACTIVE,
    SUPPLEMENTATION_ERROR_RETRY_EXHAUSTED,
    SUPPLEMENTATION_ERROR_RUN_ALREADY_ACTIVE,
    SUPPLEMENTATION_ERROR_STOCK_CANDIDATE_NOT_ACCEPTED,
    SUPPLEMENTATION_RUN_SCOPE_CANDIDATE_VALIDATION,
    SUPPLEMENTATION_RUN_SCOPE_LOCAL_REVIEW,
    SUPPLEMENTATION_RUN_SCOPE_SEARCH,
    CandidateDecision,
    CandidateDecisionValue,
    ClaimDecision,
    ClaimDecisionValue,
    CoverageGap,
    CoverageGapStatus,
    GapEvent,
    GapEventType,
    GraphicPlan,
    GraphicPlanUserStatus,
    StockCandidate,
    StockCandidateUserStatus,
    StockDuplicateStatus,
    StockSearchAttempt,
    StockSearchAttemptStatus,
    StockSearchRequest,
    SupplementationAttempt,
    SupplementationAttemptStatus,
    SupplementationRequest,
    SupplementationRequestStatus,
    SupplementationRun,
    SupplementationRunScopeLiteral,
    SupplementationRunStatus,
    metadata_fingerprint,
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


class SupplementationServiceError(InventoryServiceError):
    """Domain error for supplementation operations."""


@dataclass(frozen=True)
class SupplementationStartResult:
    started: bool
    message: str
    run: SupplementationRun | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class SupplementationActionResult:
    ok: bool
    message: str
    gap: CoverageGap | None = None
    candidate: StockCandidate | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class SupplementationView:
    ok: bool
    message: str | None = None
    active_run: SupplementationRun | None = None
    runs: list[SupplementationRun] = field(default_factory=list)
    gaps: list[CoverageGap] = field(default_factory=list)
    candidates_by_gap: dict[str, list[StockCandidate]] = field(default_factory=dict)
    claim_decisions: list[ClaimDecision] = field(default_factory=list)
    script_locks: list[object] = field(default_factory=list)
    can_start_supplementation: bool = False


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_supplementation_view(project: Project) -> SupplementationView:
    try:
        project = require_discovery_project(project)
    except InventoryServiceError as exc:
        return SupplementationView(ok=False, message=str(exc))
    # Materialization is explicit elsewhere; rendering does not create gaps or hit gateways.
    try:
        conn = repo.open_supplementation_registry(project.project_root_path)
    except RegistryDatabaseError as exc:
        return SupplementationView(ok=False, message=str(exc))
    try:
        active = repo.find_active_supplementation_run(conn, project_id=project.id)
        runs = repo.list_supplementation_runs(conn, project_id=project.id)
        gaps = repo.list_coverage_gaps(conn, project_id=project.id)
        candidates = {
            gap.gap_id: repo.list_stock_candidates_for_gap(conn, gap_id=gap.gap_id)
            for gap in gaps
        }
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
        locks = repo.list_script_locks(conn, project_id=project.id)
    finally:
        conn.close()
    return SupplementationView(
        ok=True,
        active_run=active,
        runs=runs,
        gaps=gaps,
        candidates_by_gap=candidates,
        claim_decisions=decisions,
        script_locks=locks,
        can_start_supplementation=active is None and bool(gaps),
    )


def start_local_review_run(
    project: Project,
    *,
    gap_ids: list[str],
    sync: bool = False,
) -> SupplementationStartResult:
    return _start_run(
        project,
        gap_ids=gap_ids,
        scope=SUPPLEMENTATION_RUN_SCOPE_LOCAL_REVIEW,
        sync=sync,
    )


def start_search_run(
    project: Project,
    *,
    gap_ids: list[str],
    sync: bool = False,
) -> SupplementationStartResult:
    return _start_run(
        project,
        gap_ids=gap_ids,
        scope=SUPPLEMENTATION_RUN_SCOPE_SEARCH,
        sync=sync,
    )


def start_candidate_validation_run(
    project: Project,
    *,
    gap_ids: list[str],
    sync: bool = False,
) -> SupplementationStartResult:
    return _start_run(
        project,
        gap_ids=gap_ids,
        scope=SUPPLEMENTATION_RUN_SCOPE_CANDIDATE_VALIDATION,
        sync=sync,
    )


def perform_search_for_gap(project_root, *, project_id: str, run_id: str, gap_id: str) -> None:
    """Worker entry for fake-stock search. Gateway use is intentionally here."""

    conn = repo.open_supplementation_registry(project_root)
    try:
        gap = repo.get_coverage_gap(conn, gap_id=gap_id)
        if gap is None:
            raise SupplementationServiceError(SUPPLEMENTATION_ERROR_COVERAGE_GAP_MISSING)
        if (
            repo.count_search_attempts_for_gap_version(
                conn,
                gap_id=gap.gap_id,
                gap_version=gap.gap_version,
            )
            >= MAX_SEARCH_ATTEMPTS_PER_GAP_VERSION
        ):
            raise SupplementationServiceError(SUPPLEMENTATION_ERROR_RETRY_EXHAUSTED)
        request = _get_or_create_request(conn, project_root, project_id=project_id, gap=gap)
        attempt_number = repo.next_stock_attempt_number(conn, request_id=request.request_id)
        request_for_gateway = StockSearchRequest(
            project_id=project_id,
            request_id=request.request_id,
            gap_id=gap.gap_id,
            query_text=request.query_text,
            search_strategy="fake_phase10_gap_search",
            provider=STOCK_PROVIDER_FAKE,
        )
        config = load_stock_config()
        try:
            response = StockSearchGateway(config=config).search(request_for_gateway)
            attempt = StockSearchAttempt(
                attempt_id=repo.new_stock_search_attempt_id(),
                project_id=project_id,
                request_id=request.request_id,
                gap_id=gap.gap_id,
                query_text=request.query_text,
                search_strategy=request_for_gateway.search_strategy,
                provider=STOCK_PROVIDER_FAKE,
                adapter_version=config.adapter_version,
                attempt_number=attempt_number,
                result_count=len(response.candidates),
                status=StockSearchAttemptStatus.COMPLETED,
                created_at=_now(),
            )
            candidates = [
                candidate.model_copy(
                    update={
                        "attempt_id": attempt.attempt_id,
                        "metadata_fingerprint": metadata_fingerprint(candidate),
                    }
                )
                for candidate in response.candidates
            ]
            search_relative = repo.save_stock_search_attempt_json(
                project_root,
                attempt,
                candidates=candidates,
            )
            conn.execute("BEGIN IMMEDIATE")
            repo.insert_stock_search_attempt(conn, attempt, search_relative)
            for candidate in candidates:
                candidate_relative = repo.save_stock_candidate_json(project_root, candidate)
                repo.insert_stock_candidate(conn, candidate, candidate_relative)
            repo.update_supplementation_request(
                conn,
                request.model_copy(
                    update={
                        "status": SupplementationRequestStatus.AWAITING_DECISION,
                        "updated_at": _now(),
                    }
                ),
            )
            repo.update_coverage_gap(
                conn,
                gap.model_copy(
                    update={
                        "status": CoverageGapStatus.IN_PROGRESS,
                        "prior_attempt_summaries": [
                            *gap.prior_attempt_summaries,
                            f"fake search {attempt_number}: {len(candidates)} candidates",
                        ],
                        "updated_at": _now(),
                    }
                ),
            )
            conn.commit()
        except StockGatewayError as exc:
            attempt = StockSearchAttempt(
                attempt_id=repo.new_stock_search_attempt_id(),
                project_id=project_id,
                request_id=request.request_id,
                gap_id=gap.gap_id,
                query_text=request.query_text,
                search_strategy=request_for_gateway.search_strategy,
                provider=STOCK_PROVIDER_FAKE,
                adapter_version=FAKE_STOCK_ADAPTER_VERSION,
                attempt_number=attempt_number,
                result_count=0,
                status=StockSearchAttemptStatus.FAILED,
                error_code=exc.code,
                error_message=exc.message,
                created_at=_now(),
            )
            conn.execute("BEGIN IMMEDIATE")
            repo.insert_stock_search_attempt(conn, attempt)
            conn.commit()
            raise
    finally:
        conn.close()


def validate_candidates_for_gap(project_root, *, project_id: str, gap_id: str) -> None:
    conn = repo.open_supplementation_registry(project_root)
    try:
        candidates = repo.list_stock_candidates_for_gap(conn, gap_id=gap_id)
        seen: dict[str, str] = {}
        conn.execute("BEGIN IMMEDIATE")
        for candidate in candidates:
            fingerprint = candidate.metadata_fingerprint or metadata_fingerprint(candidate)
            duplicate = (
                StockDuplicateStatus.POSSIBLE_DUPLICATE
                if fingerprint in seen
                else StockDuplicateStatus.UNKNOWN
            )
            if fingerprint not in seen:
                seen[fingerprint] = candidate.candidate_id
            updated = candidate.model_copy(
                update={
                    "metadata_fingerprint": fingerprint,
                    "duplicate_status": duplicate,
                }
            )
            relative = repo.save_stock_candidate_json(project_root, updated)
            repo.update_stock_candidate(conn, updated, relative)
        conn.commit()
    finally:
        conn.close()


def record_candidate_decision(
    project: Project,
    *,
    candidate_id: str,
    decision: str,
    reason: str,
    user_note: str | None = None,
) -> SupplementationActionResult:
    project = require_discovery_project(project)
    conn = repo.open_supplementation_registry(project.project_root_path)
    try:
        candidate = repo.get_stock_candidate(conn, candidate_id=candidate_id)
        if candidate is None:
            return SupplementationActionResult(ok=False, message="Kandidat fehlt.")
        value = CandidateDecisionValue(decision)
        revision = repo.next_candidate_decision_revision(conn, candidate_id=candidate_id)
        record = CandidateDecision(
            decision_id=repo.new_candidate_decision_id(),
            project_id=project.id,
            gap_id=candidate.gap_id,
            candidate_id=candidate.candidate_id,
            revision=revision,
            decision=value,
            reason=reason,
            user_note=user_note,
            created_at=_now(),
        )
        status = StockCandidateUserStatus(value.value)
        updated_candidate = candidate.model_copy(update={"user_status": status})
        relative = repo.save_stock_candidate_json(project.project_root_path, updated_candidate)
        conn.execute("BEGIN IMMEDIATE")
        repo.append_candidate_decision(conn, record)
        repo.update_stock_candidate(conn, updated_candidate, relative)
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        raise SupplementationServiceError(str(exc)) from exc
    finally:
        conn.close()
    return SupplementationActionResult(
        ok=True,
        message="Kandidatenentscheidung gespeichert.",
        candidate=updated_candidate,
    )


def record_claim_decision(
    project: Project,
    *,
    script_id: str,
    claim_id: str,
    claim_text: str,
    decision: str,
    reason: str | None = None,
    user_note: str | None = None,
) -> ClaimDecision:
    project = require_discovery_project(project)
    conn = repo.open_supplementation_registry(project.project_root_path)
    try:
        revision = repo.next_claim_decision_revision(
            conn,
            script_id=script_id,
            claim_id=claim_id,
        )
        record = ClaimDecision(
            decision_id=repo.new_claim_decision_id(),
            project_id=project.id,
            script_id=script_id,
            claim_id=claim_id,
            claim_content_sha256=compute_text_sha256(claim_text),
            revision=revision,
            decision=ClaimDecisionValue(decision),
            reason=reason,
            user_note=user_note,
            created_at=_now(),
        )
        relative = repo.save_claim_decision_json(project.project_root_path, record)
        conn.execute("BEGIN IMMEDIATE")
        repo.append_claim_decision(conn, record, relative)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return record


@dataclass(frozen=True)
class ClaimBatchDecisionResult:
    ok: bool
    message: str
    batch_id: str | None = None
    decisions: list[ClaimDecision] = field(default_factory=list)
    failed_claim_ids: list[str] = field(default_factory=list)
    reused_existing_batch: bool = False
    error_code: str | None = None


def record_claim_decision_batch(
    project: Project,
    *,
    script_id: str,
    claims: list[dict[str, str]],
    decision: str,
    user_confirmed: bool = False,
    batch_id: str | None = None,
    reason: str | None = None,
) -> ClaimBatchDecisionResult:
    """Append-only per-claim decisions sharing one batch_id (Schema 20 via reason)."""
    from uuid import uuid4

    from otio_app.discovery_v2.domain.batch_decision import (
        encode_batch_marker,
        parse_batch_id,
    )

    project = require_discovery_project(project)
    items = [
        item
        for item in claims
        if str(item.get("claim_id", "")).strip() and str(item.get("claim_text", "")).strip()
    ]
    if not items:
        return ClaimBatchDecisionResult(
            ok=False,
            message="Keine Claims ausgewaehlt.",
            error_code="claim_batch_empty",
        )
    if not user_confirmed:
        return ClaimBatchDecisionResult(
            ok=False,
            message=f"Bitte bestaetigen: {len(items)} Claims werden entschieden.",
            error_code="claim_batch_confirmation_required",
        )
    resolved_batch_id = (batch_id or "").strip() or str(uuid4())
    conn = repo.open_supplementation_registry(project.project_root_path)
    try:
        latest = repo.latest_claim_decisions_for_script(
            conn, project_id=project.id, script_id=script_id
        )
        if items and all(
            (latest.get(str(item["claim_id"])) is not None)
            and parse_batch_id(latest[str(item["claim_id"])].reason) == resolved_batch_id
            for item in items
        ):
            return ClaimBatchDecisionResult(
                ok=True,
                message="Batch-Claim-Entscheidung bereits gespeichert.",
                batch_id=resolved_batch_id,
                decisions=[latest[str(item["claim_id"])] for item in items],
                reused_existing_batch=True,
            )
    finally:
        conn.close()

    marker_reason = encode_batch_marker(resolved_batch_id, trailing=reason)
    decisions: list[ClaimDecision] = []
    failed: list[str] = []
    for item in items:
        claim_id = str(item["claim_id"]).strip()
        try:
            decisions.append(
                record_claim_decision(
                    project,
                    script_id=script_id,
                    claim_id=claim_id,
                    claim_text=str(item["claim_text"]),
                    decision=decision,
                    reason=marker_reason,
                )
            )
        except Exception:
            failed.append(claim_id)
    ok = bool(decisions) and not failed
    return ClaimBatchDecisionResult(
        ok=ok,
        message=(
            f"Batch {resolved_batch_id}: {len(decisions)} Claim-Entscheidung(en)"
            + (f", {len(failed)} fehlgeschlagen" if failed else "")
            + "."
        ),
        batch_id=resolved_batch_id,
        decisions=decisions,
        failed_claim_ids=failed,
        error_code=None if ok else "claim_batch_partial_failure",
    )


def link_imported_completed_asset_to_gap(
    project: Project,
    *,
    gap_id: str,
    candidate_id: str,
    asset_id: str,
) -> SupplementationActionResult:
    project = require_discovery_project(project)
    conn = repo.open_supplementation_registry(project.project_root_path)
    try:
        gap = repo.get_coverage_gap(conn, gap_id=gap_id)
        candidate = repo.get_stock_candidate(conn, candidate_id=candidate_id)
        if gap is None or candidate is None:
            return SupplementationActionResult(
                ok=False,
                message="Gap oder Kandidat fehlt.",
                error_code=SUPPLEMENTATION_ERROR_COVERAGE_GAP_MISSING,
            )
        if candidate.user_status != StockCandidateUserStatus.ACCEPTED_FOR_IMPORT:
            return SupplementationActionResult(
                ok=False,
                message="Kandidat ist nicht fuer Import akzeptiert.",
                error_code=SUPPLEMENTATION_ERROR_STOCK_CANDIDATE_NOT_ACCEPTED,
            )
        asset = conn.execute(
            "SELECT asset_id FROM assets WHERE asset_id = ? AND project_id = ?",
            (asset_id, project.id),
        ).fetchone()
        if asset is None:
            return SupplementationActionResult(ok=False, message="Importiertes Asset fehlt.")
        updated = gap.model_copy(
            update={
                "status": CoverageGapStatus.RESOLVED_WITH_SUPPLEMENT,
                "resolved_asset_id": asset_id,
                "outcome": "Manuell importiertes Original verknuepft.",
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
                event_type=GapEventType.CANDIDATE_LINKED,
                message="Importiertes Working-Media-Asset mit Gap verknuepft.",
                payload={"candidate_id": candidate_id, "asset_id": asset_id},
                created_at=_now(),
            ),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        raise SupplementationServiceError(str(exc)) from exc
    finally:
        conn.close()
    return SupplementationActionResult(ok=True, message="Asset verknuepft.", gap=updated)


def create_graphic_plan(
    project: Project,
    *,
    gap_id: str,
    description: str,
    required_data: list[str] | None = None,
    geographic_scope: str | None = None,
) -> GraphicPlan:
    project = require_discovery_project(project)
    conn = repo.open_supplementation_registry(project.project_root_path)
    try:
        gap = repo.get_coverage_gap(conn, gap_id=gap_id)
        if gap is None:
            raise SupplementationServiceError(SUPPLEMENTATION_ERROR_COVERAGE_GAP_MISSING)
        plan = GraphicPlan(
            graphic_plan_id=repo.new_graphic_plan_id(),
            project_id=project.id,
            visual_intent_id=gap.visual_intent_id,
            gap_id=gap.gap_id,
            description=description,
            required_data=list(required_data or []),
            geographic_scope=geographic_scope,
            user_status=GraphicPlanUserStatus.PROPOSED,
            created_at=_now(),
            updated_at=_now(),
        )
        relative = repo.save_graphic_plan_json(project.project_root_path, plan)
        updated = gap.model_copy(
            update={
                "status": CoverageGapStatus.RESOLVED_BY_GRAPHIC_PLAN,
                "outcome": "GraphicPlan erstellt (keine Mediengenerierung).",
                "updated_at": _now(),
            }
        )
        gap_relative = repo.save_coverage_gap_json(project.project_root_path, updated)
        conn.execute("BEGIN IMMEDIATE")
        repo.insert_graphic_plan(conn, plan, relative)
        repo.update_coverage_gap(conn, updated, gap_relative)
        repo.append_gap_event(
            conn,
            GapEvent(
                event_id=repo.new_gap_event_id(),
                gap_id=gap.gap_id,
                project_id=project.id,
                event_type=GapEventType.GRAPHIC_PLAN_CREATED,
                message="GraphicPlan angelegt; keine Grafik erzeugt.",
                payload={"graphic_plan_id": plan.graphic_plan_id},
                created_at=_now(),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return plan


def _start_run(
    project: Project,
    *,
    gap_ids: list[str],
    scope: SupplementationRunScopeLiteral,
    sync: bool,
) -> SupplementationStartResult:
    project = require_discovery_project(project)
    materialize_gaps_from_current_coverage(project)
    conn = repo.open_supplementation_registry(project.project_root_path)
    try:
        from otio_app.discovery_v2.persistence.export_repository import (
            find_active_export_run,
        )

        if find_active_export_run(conn, project_id=project.id) is not None:
            return SupplementationStartResult(
                started=False,
                message="Es laeuft bereits ein Export-Run.",
                error_code="export_run_already_active",
            )
        active_analysis = find_active_analysis_run(conn, project_id=project.id)
        if active_analysis is not None:
            return SupplementationStartResult(
                started=False,
                message="Es laeuft bereits ein Analysis-Run.",
                error_code=SUPPLEMENTATION_ERROR_ANALYSIS_RUN_ALREADY_ACTIVE,
            )
        active_editorial = find_active_editorial_run(conn, project_id=project.id)
        if active_editorial is not None:
            return SupplementationStartResult(
                started=False,
                message="Es laeuft bereits ein Editorial-Run.",
                error_code=SUPPLEMENTATION_ERROR_EDITORIAL_RUN_ALREADY_ACTIVE,
            )
        active = repo.find_active_supplementation_run(conn, project_id=project.id)
        if active is not None:
            return SupplementationStartResult(
                started=False,
                message="Es laeuft bereits ein Supplementation-Run.",
                run=active,
                error_code=SUPPLEMENTATION_ERROR_RUN_ALREADY_ACTIVE,
            )
        active_narration = find_active_narration_run(conn, project_id=project.id)
        if active_narration is not None:
            return SupplementationStartResult(
                started=False,
                message="Es laeuft bereits ein Narration-Run.",
                error_code="narration_run_already_active",
            )
        from otio_app.discovery_v2.persistence.visual_edit_repository import (
            find_active_visual_edit_run,
        )

        active_visual_edit = find_active_visual_edit_run(conn, project_id=project.id)
        if active_visual_edit is not None:
            return SupplementationStartResult(
                started=False,
                message="Es laeuft bereits ein Visual-Edit-Run.",
                error_code="visual_edit_run_already_active",
            )
        selected = [
            repo.get_coverage_gap(conn, gap_id=gap_id)
            for gap_id in gap_ids
        ]
        if not gap_ids or any(gap is None for gap in selected):
            return SupplementationStartResult(
                started=False,
                message="Mindestens ein Coverage Gap fehlt.",
                error_code=SUPPLEMENTATION_ERROR_COVERAGE_GAP_MISSING,
            )
        run = SupplementationRun(
            run_id=repo.new_supplementation_run_id(),
            project_id=project.id,
            scope=scope,
            status=SupplementationRunStatus.QUEUED,
            selected_gap_ids=list(gap_ids),
            created_at=_now(),
        )
        repo.insert_supplementation_run(conn, run)
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        raise SupplementationServiceError(str(exc)) from exc
    finally:
        conn.close()

    worker = {
        SUPPLEMENTATION_RUN_SCOPE_LOCAL_REVIEW: "supplementation_local_review",
        SUPPLEMENTATION_RUN_SCOPE_SEARCH: "supplementation_search",
        SUPPLEMENTATION_RUN_SCOPE_CANDIDATE_VALIDATION: "supplementation_candidate_validation",
    }[scope]
    launched = get_supplementation_job_launcher().launch(
        project_id=project.id,
        project_root=project.project_root_path,
        run_id=run.run_id,
        worker=worker,  # type: ignore[arg-type]
        sync=sync,
    )
    if not launched and not sync:
        return SupplementationStartResult(
            started=False,
            message="Supplementation-Worker konnte nicht gestartet werden.",
            run=run,
            error_code=SUPPLEMENTATION_ERROR_RUN_ALREADY_ACTIVE,
        )
    if sync:
        conn = repo.open_supplementation_registry(project.project_root_path)
        try:
            final = repo.get_supplementation_run(conn, run_id=run.run_id) or run
        finally:
            conn.close()
        return SupplementationStartResult(
            started=True,
            message="Supplementation-Run abgeschlossen.",
            run=final,
        )
    return SupplementationStartResult(started=True, message="Supplementation-Run gestartet.", run=run)


def _get_or_create_request(
    conn,
    project_root,
    *,
    project_id: str,
    gap: CoverageGap,
) -> SupplementationRequest:
    existing = repo.get_latest_request_for_gap(conn, gap_id=gap.gap_id)
    if existing is not None and existing.status != SupplementationRequestStatus.STALE:
        return existing
    bundle = editorial_repo.get_script_bundle(conn, script_id=gap.script_id) or {}
    intents = {item["visual_intent_id"]: item for item in bundle.get("visual_intents", [])}
    intent = intents.get(gap.visual_intent_id, {})
    query_text = " ".join(
        str(part)
        for part in (
            intent.get("desired_motif") or gap.visual_intent_id,
            intent.get("action") or "",
            intent.get("setting") or "",
            intent.get("geographic_requirements") or "",
        )
        if str(part).strip()
    )
    request = SupplementationRequest(
        request_id=repo.new_supplementation_request_id(),
        project_id=project_id,
        gap_id=gap.gap_id,
        script_id=gap.script_id,
        visual_intent_id=gap.visual_intent_id,
        motif=str(intent.get("desired_motif") or gap.visual_intent_id),
        action=str(intent.get("action") or ""),
        setting=str(intent.get("setting") or ""),
        geographic_requirements=intent.get("geographic_requirements"),
        authenticity_requirements=list(intent.get("authenticity_requirements") or []),
        allowed_media_kinds=list(intent.get("allowed_media_kinds") or ["video", "image"]),
        query_text=query_text,
        search_version=repo.next_request_search_version(conn, gap_id=gap.gap_id),
        status=SupplementationRequestStatus.SEARCHING,
        created_at=_now(),
        updated_at=_now(),
    )
    relative = repo.save_supplementation_request_json(project_root, request)
    repo.insert_supplementation_request(conn, request, relative)
    conn.commit()
    return request


__all__ = [
    "ClaimBatchDecisionResult",
    "SupplementationActionResult",
    "SupplementationServiceError",
    "SupplementationStartResult",
    "SupplementationView",
    "create_graphic_plan",
    "get_supplementation_view",
    "link_imported_completed_asset_to_gap",
    "perform_search_for_gap",
    "record_candidate_decision",
    "record_claim_decision",
    "record_claim_decision_batch",
    "start_candidate_validation_run",
    "start_local_review_run",
    "start_search_run",
    "validate_candidates_for_gap",
]
