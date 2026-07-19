"""Application service for Discovery-V2 observation review (Phase 8D)."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone

from pydantic import ValidationError

from otio_app.discovery_v2.adapters.vision_config import load_vision_config
from otio_app.discovery_v2.application.asset_analysis_eligibility_service import (
    get_analysis_eligibility_view,
)
from otio_app.discovery_v2.application.inventory_service import (
    InventoryServiceError,
    require_discovery_project,
)
from otio_app.discovery_v2.domain.asset_analysis import (
    ANALYSIS_RUN_SCOPE_MODEL,
    ANALYSIS_RUN_SCOPE_PREPARE_ONLY,
    AnalysisPrepareAssetStatus,
)
from uuid import uuid4

from otio_app.discovery_v2.domain.batch_decision import (
    encode_batch_marker,
    parse_batch_id,
)
from otio_app.discovery_v2.domain.observation_review import (
    OBSERVATION_REVIEW_ERROR_CONFLICT,
    OBSERVATION_REVIEW_ERROR_REASON_REQUIRED,
    OBSERVATION_REVIEW_ERROR_REGISTRY_WRITE_FAILED,
    OBSERVATION_REVIEW_ERROR_VISUAL_OBSERVATION_HASH_MISMATCH,
    OBSERVATION_REVIEW_ERROR_VISUAL_OBSERVATION_INVALID,
    OBSERVATION_REVIEW_ERROR_VISUAL_OBSERVATION_MISSING,
    OBSERVATION_REVIEW_ERROR_VISUAL_OBSERVATION_STALE,
    OBSERVATION_REVIEW_STATUS_UNREVIEWED,
    PHASE8_ASSET_STATUS_BLOCKED,
    PHASE8_ASSET_STATUS_ELIGIBLE_NOT_PREPARED,
    PHASE8_ASSET_STATUS_MODEL_FAILED,
    PHASE8_ASSET_STATUS_MODEL_NOT_STARTED,
    PHASE8_ASSET_STATUS_NOT_ELIGIBLE,
    PHASE8_ASSET_STATUS_OBSERVATION_ACCEPTED,
    PHASE8_ASSET_STATUS_OBSERVATION_REJECTED,
    PHASE8_ASSET_STATUS_OBSERVATION_UNREVIEWED,
    PHASE8_ASSET_STATUS_REANALYSIS_REQUESTED,
    PHASE8_ASSET_STATUS_STALE,
    ObservationReviewDecision,
    ObservationReviewRecord,
    compute_observation_sha256,
)
from otio_app.discovery_v2.domain.visual_observation import (
    AnalysisModelAssetStatus,
    VisualObservation,
    VisualObservationRecord,
)
from otio_app.discovery_v2.persistence.asset_analysis_repository import (
    get_analysis_identity,
    get_current_observation_review,
    get_visual_observation,
    insert_observation_review,
    list_analysis_run_assets,
    list_analysis_runs,
    list_model_analysis_attempts,
    list_observation_reviews,
    list_representative_frames,
    list_visual_observations_for_project,
    new_observation_review_id,
    next_observation_review_revision,
    open_analysis_registry,
)
from otio_app.discovery_v2.persistence.asset_registry_database import (
    RegistryDatabaseError,
)
from otio_app.models import Project


class ObservationReviewServiceError(InventoryServiceError):
    """Domain error for observation-review operations."""


@dataclass(frozen=True)
class ReviewSubmitResult:
    ok: bool
    message: str
    review: ObservationReviewRecord | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class BatchReviewSubmitResult:
    ok: bool
    message: str
    batch_id: str | None = None
    reviews: list[ObservationReviewRecord] = field(default_factory=list)
    failed_observation_ids: list[str] = field(default_factory=list)
    error_code: str | None = None
    reused_existing_batch: bool = False
    coverage_started: bool = False


@dataclass(frozen=True)
class EditorialReadyObservationView:
    observation_id: str
    asset_id: str
    analysis_identity_id: str
    working_media_id: str
    summary: str
    evidence_frame_ids: list[str]
    geographic_confidence: float
    synthetic_confidence: float
    uncertainty_notes: list[str]
    review: ObservationReviewRecord
    observation_sha256: str
    frame_set_fingerprint: str


@dataclass(frozen=True)
class ObservationReviewItemView:
    observation_id: str
    asset_id: str
    analysis_identity_id: str
    working_media_id: str
    provider: str
    model_identifier: str
    gateway_version: str
    prompt_version: str
    response_schema_version: str
    frame_set_fingerprint: str
    observation_sha256: str
    created_at: datetime
    summary: str = "—"
    evidence_frame_ids: list[str] = field(default_factory=list)
    geographic_confidence: float | None = None
    synthetic_confidence: float | None = None
    uncertainty_notes: list[str] = field(default_factory=list)
    current_review_decision: str = OBSERVATION_REVIEW_STATUS_UNREVIEWED
    review_history: list[ObservationReviewRecord] = field(default_factory=list)
    is_current_identity: bool = False
    is_editorial_ready: bool = False
    is_valid: bool = False
    is_stale: bool = False
    status: str = OBSERVATION_REVIEW_STATUS_UNREVIEWED
    error_code: str | None = None


@dataclass(frozen=True)
class ObservationReviewProjectView:
    ok: bool
    message: str | None = None
    observations: list[ObservationReviewItemView] = field(default_factory=list)


@dataclass(frozen=True)
class Phase8AssetSummary:
    asset_id: str
    display_name: str | None
    status: str
    reason_code: str | None = None
    media_kind: str | None = None
    analysis_identity_id: str | None = None
    observation_id: str | None = None
    review_decision: str | None = None
    not_applicable: bool = False


@dataclass(frozen=True)
class Phase8ProjectSummary:
    ok: bool
    message: str | None = None
    status_counts: dict[str, int] = field(default_factory=dict)
    not_applicable_count: int = 0
    total_assets: int = 0
    assets: list[Phase8AssetSummary] = field(default_factory=list)


@dataclass(frozen=True)
class _PreparedAsset:
    asset_id: str
    analysis_identity_id: str
    working_media_id: str
    media_kind: str
    frame_set_fingerprint: str
    frame_count: int


def submit_observation_review(
    project: Project,
    *,
    observation_id: str,
    decision: str | ObservationReviewDecision,
    reason_code: str | None = None,
    review_note: str | None = None,
    trigger_coverage: bool = True,
    coverage_sync: bool = False,
) -> ReviewSubmitResult:
    project = require_discovery_project(project)
    try:
        decision_value = (
            decision.value
            if isinstance(decision, ObservationReviewDecision)
            else ObservationReviewDecision(str(decision)).value
        )
    except ValueError:
        return ReviewSubmitResult(
            ok=False,
            message="Ungültige Review-Entscheidung.",
            error_code=OBSERVATION_REVIEW_ERROR_REGISTRY_WRITE_FAILED,
        )

    cleaned_reason = _clean_optional_text(reason_code)
    if decision_value in {
        ObservationReviewDecision.REANALYZE_REQUESTED.value,
        ObservationReviewDecision.REJECTED.value,
    } and not cleaned_reason:
        return ReviewSubmitResult(
            ok=False,
            message="Für Ablehnung oder Reanalyse ist ein Grund erforderlich.",
            error_code=OBSERVATION_REVIEW_ERROR_REASON_REQUIRED,
        )

    try:
        conn = open_analysis_registry(project.project_root_path)
    except RegistryDatabaseError as exc:
        raise ObservationReviewServiceError(str(exc)) from exc

    try:
        observation = get_visual_observation(conn, observation_id=observation_id)
        if observation is None or observation.project_id != project.id:
            return ReviewSubmitResult(
                ok=False,
                message="Visual Observation wurde nicht gefunden.",
                error_code=OBSERVATION_REVIEW_ERROR_VISUAL_OBSERVATION_MISSING,
            )

        identity = get_analysis_identity(
            conn,
            analysis_identity_id=observation.analysis_identity_id,
        )
        if (
            identity is None
            or identity.project_id != project.id
            or identity.asset_id != observation.asset_id
        ):
            return ReviewSubmitResult(
                ok=False,
                message="Visual Observation ist nicht mehr an eine gültige Analyse gebunden.",
                error_code=OBSERVATION_REVIEW_ERROR_VISUAL_OBSERVATION_STALE,
            )

        parsed = _parse_visual_observation(observation)
        if parsed is None:
            return ReviewSubmitResult(
                ok=False,
                message="Visual Observation JSON ist ungültig.",
                error_code=OBSERVATION_REVIEW_ERROR_VISUAL_OBSERVATION_INVALID,
            )

        frames = list_representative_frames(
            conn,
            analysis_identity_id=observation.analysis_identity_id,
        )
        if not frames:
            return ReviewSubmitResult(
                ok=False,
                message="Visual Observation ist nicht mehr an Frames gebunden.",
                error_code=OBSERVATION_REVIEW_ERROR_VISUAL_OBSERVATION_STALE,
            )
        frame_set_fingerprint = _frame_set_fingerprint_from_frames(frames)
        if not _frame_fingerprint_matches_prepared(
            observation.frame_hash_fingerprint,
            frame_set_fingerprint,
        ):
            return ReviewSubmitResult(
                ok=False,
                message="Frame-Fingerprint der Visual Observation passt nicht mehr.",
                error_code=OBSERVATION_REVIEW_ERROR_VISUAL_OBSERVATION_HASH_MISMATCH,
            )

        observation_sha256 = compute_observation_sha256(observation.observation_json)
        try:
            conn.execute("BEGIN IMMEDIATE")
            previous = get_current_observation_review(
                conn,
                observation_id=observation.observation_id,
            )
            revision = next_observation_review_revision(
                conn,
                observation_id=observation.observation_id,
            )
            review = ObservationReviewRecord(
                review_id=new_observation_review_id(),
                observation_id=observation.observation_id,
                analysis_identity_id=observation.analysis_identity_id,
                project_id=project.id,
                asset_id=observation.asset_id,
                working_media_id=identity.working_media_id,
                observation_sha256=observation_sha256,
                frame_set_fingerprint=frame_set_fingerprint,
                review_revision=revision,
                decision=decision_value,
                reason_code=cleaned_reason,
                review_note=_clean_optional_text(review_note),
                created_at=_now(),
                supersedes_review_id=(
                    None if previous is None else previous.review_id
                ),
            )
            insert_observation_review(conn, review)
            conn.commit()
        except sqlite3.IntegrityError:
            conn.rollback()
            return ReviewSubmitResult(
                ok=False,
                message="Review-Konflikt: Bitte Ansicht neu laden.",
                error_code=OBSERVATION_REVIEW_ERROR_CONFLICT,
            )
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            return ReviewSubmitResult(
                ok=False,
                message=f"Review konnte nicht gespeichert werden: {exc}",
                error_code=OBSERVATION_REVIEW_ERROR_REGISTRY_WRITE_FAILED,
            )
    finally:
        conn.close()

    coverage_started = False
    if (
        trigger_coverage
        and decision_value == ObservationReviewDecision.ACCEPTED.value
    ):
        from otio_app.discovery_v2.application.coverage_revalidation_service import (
            revalidate_coverage_after_accepted_reviews,
        )

        revalidate = revalidate_coverage_after_accepted_reviews(
            project, sync=coverage_sync
        )
        coverage_started = revalidate.coverage_started

    from otio_app.discovery_v2.application.script_lock_current_state_mutation_service import (
        invalidate_current_script_lock_context,
    )

    invalidate_current_script_lock_context(
        project,
        reason_code="observation_fingerprint_changed",
        source_operation_id="submit_observation_review",
    )

    message = "Observation Review gespeichert."
    if coverage_started:
        message = "Observation Review gespeichert; Coverage-Neuberechnung gestartet."

    return ReviewSubmitResult(
        ok=True,
        message=message,
        review=review,
    )


def submit_observation_review_batch(
    project: Project,
    *,
    observation_ids: list[str],
    decision: str | ObservationReviewDecision,
    reason_code: str | None = None,
    review_note: str | None = None,
    user_confirmed: bool = False,
    batch_id: str | None = None,
    trigger_coverage: bool = True,
    coverage_sync: bool = False,
) -> BatchReviewSubmitResult:
    """Append-only per-observation decisions sharing one batch_id (Schema 20)."""
    project = require_discovery_project(project)
    ids = [str(item).strip() for item in observation_ids if str(item).strip()]
    if not ids:
        return BatchReviewSubmitResult(
            ok=False,
            message="Keine Observations ausgewaehlt.",
            error_code=OBSERVATION_REVIEW_ERROR_REGISTRY_WRITE_FAILED,
        )
    if not user_confirmed:
        return BatchReviewSubmitResult(
            ok=False,
            message=f"Bitte bestaetigen: {len(ids)} Observations werden entschieden.",
            error_code=OBSERVATION_REVIEW_ERROR_REASON_REQUIRED,
        )

    resolved_batch_id = (batch_id or "").strip() or str(uuid4())
    # Idempotent replay: if every target already has this batch_id as latest decision.
    existing = _existing_batch_reviews(project, observation_ids=ids, batch_id=resolved_batch_id)
    if existing is not None:
        return BatchReviewSubmitResult(
            ok=True,
            message="Batch-Entscheidung bereits gespeichert.",
            batch_id=resolved_batch_id,
            reviews=existing,
            reused_existing_batch=True,
        )

    marker_note = encode_batch_marker(resolved_batch_id, trailing=review_note)
    reviews: list[ObservationReviewRecord] = []
    failed: list[str] = []
    for observation_id in ids:
        result = submit_observation_review(
            project,
            observation_id=observation_id,
            decision=decision,
            reason_code=reason_code,
            review_note=marker_note,
            trigger_coverage=False,
        )
        if result.ok and result.review is not None:
            reviews.append(result.review)
        else:
            failed.append(observation_id)

    decision_value = (
        decision.value
        if isinstance(decision, ObservationReviewDecision)
        else str(decision)
    )
    coverage_started = False
    if (
        trigger_coverage
        and decision_value == ObservationReviewDecision.ACCEPTED.value
        and reviews
    ):
        from otio_app.discovery_v2.application.coverage_revalidation_service import (
            revalidate_coverage_after_accepted_reviews,
        )

        revalidate = revalidate_coverage_after_accepted_reviews(
            project, sync=coverage_sync
        )
        coverage_started = revalidate.coverage_started

    if (
        decision_value == ObservationReviewDecision.REANALYZE_REQUESTED.value
        and reviews
    ):
        asset_ids = sorted({review.asset_id for review in reviews})
        from otio_app.discovery_v2.application.model_analysis_service import (
            start_model_analysis,
        )

        start_model_analysis(
            project,
            asset_ids=asset_ids,
            consent_acknowledged=True,
            sync=coverage_sync,
        )

    ok = bool(reviews) and not failed
    return BatchReviewSubmitResult(
        ok=ok,
        message=(
            f"Batch {resolved_batch_id}: {len(reviews)} gespeichert"
            + (f", {len(failed)} fehlgeschlagen" if failed else "")
            + "."
        ),
        batch_id=resolved_batch_id,
        reviews=reviews,
        failed_observation_ids=failed,
        coverage_started=coverage_started,
        error_code=None if ok else OBSERVATION_REVIEW_ERROR_REGISTRY_WRITE_FAILED,
    )


def _existing_batch_reviews(
    project: Project,
    *,
    observation_ids: list[str],
    batch_id: str,
) -> list[ObservationReviewRecord] | None:
    conn = open_analysis_registry(project.project_root_path)
    try:
        found: list[ObservationReviewRecord] = []
        for observation_id in observation_ids:
            current = get_current_observation_review(
                conn, observation_id=observation_id
            )
            if current is None or parse_batch_id(current.review_note) != batch_id:
                return None
            found.append(current)
        return found
    finally:
        conn.close()


def list_editorial_ready_observations(
    project: Project,
) -> list[EditorialReadyObservationView]:
    project = require_discovery_project(project)
    config = load_vision_config()
    conn = open_analysis_registry(project.project_root_path)
    try:
        prepared_by_asset = _current_prepared_assets_by_asset(conn, project_id=project.id)
        views: list[EditorialReadyObservationView] = []
        for observation in list_visual_observations_for_project(
            conn, project_id=project.id
        ):
            prepared = prepared_by_asset.get(observation.asset_id)
            parsed = _parse_visual_observation(observation)
            review = get_current_observation_review(
                conn,
                observation_id=observation.observation_id,
            )
            if (
                prepared is None
                or parsed is None
                or review is None
                or review.decision != ObservationReviewDecision.ACCEPTED.value
                or observation.analysis_identity_id != prepared.analysis_identity_id
                or not _frame_fingerprint_matches_prepared(
                    observation.frame_hash_fingerprint,
                    prepared.frame_set_fingerprint,
                )
                or not _observation_matches_current_config(observation, config)
            ):
                continue
            views.append(
                EditorialReadyObservationView(
                    observation_id=observation.observation_id,
                    asset_id=observation.asset_id,
                    analysis_identity_id=observation.analysis_identity_id,
                    working_media_id=prepared.working_media_id,
                    summary=parsed.summary,
                    evidence_frame_ids=list(parsed.evidence_frame_ids),
                    geographic_confidence=parsed.geographic_confidence,
                    synthetic_confidence=parsed.synthetic_confidence,
                    uncertainty_notes=list(parsed.uncertainty_notes),
                    review=review,
                    observation_sha256=compute_observation_sha256(
                        observation.observation_json
                    ),
                    frame_set_fingerprint=prepared.frame_set_fingerprint,
                )
            )
        return views
    finally:
        conn.close()


def get_observation_review_view(project: Project) -> ObservationReviewProjectView:
    try:
        project = require_discovery_project(project)
    except InventoryServiceError as exc:
        return ObservationReviewProjectView(ok=False, message=str(exc))

    try:
        conn = open_analysis_registry(project.project_root_path)
    except RegistryDatabaseError as exc:
        return ObservationReviewProjectView(ok=False, message=str(exc))

    config = load_vision_config()
    try:
        prepared_by_asset = _current_prepared_assets_by_asset(conn, project_id=project.id)
        items = [
            _observation_item_view(
                conn,
                observation=observation,
                prepared=prepared_by_asset.get(observation.asset_id),
                config=config,
            )
            for observation in list_visual_observations_for_project(
                conn,
                project_id=project.id,
            )
        ]
        items.sort(
            key=lambda item: (
                not item.is_current_identity,
                item.asset_id,
                item.created_at,
                item.observation_id,
            )
        )
    finally:
        conn.close()

    return ObservationReviewProjectView(ok=True, observations=items)


def get_phase8_project_summary(project: Project) -> Phase8ProjectSummary:
    try:
        project = require_discovery_project(project)
    except InventoryServiceError as exc:
        return Phase8ProjectSummary(ok=False, message=str(exc))

    eligibility = get_analysis_eligibility_view(project)
    if not eligibility.ok:
        return Phase8ProjectSummary(
            ok=False,
            message=eligibility.message,
            status_counts={PHASE8_ASSET_STATUS_BLOCKED: 0},
        )

    try:
        conn = open_analysis_registry(project.project_root_path)
    except RegistryDatabaseError as exc:
        return Phase8ProjectSummary(ok=False, message=str(exc))

    try:
        active_run = next(
            (
                run
                for run in list_analysis_runs(conn, project_id=project.id)
                if run.status.value in {"queued", "running"}
            ),
            None,
        )
        prepared_by_asset = _current_prepared_assets_by_asset(conn, project_id=project.id)
        observations_by_identity = _observations_by_identity(conn, project_id=project.id)
        model_failed_identity_ids = _model_failed_identity_ids(conn, project_id=project.id)
        config = load_vision_config()
        assets = []
        for item in eligibility.items:
            prepared = prepared_by_asset.get(item.asset_id)
            not_applicable = item.reason_code == "not_applicable"
            if active_run is not None and item.eligible:
                assets.append(
                    Phase8AssetSummary(
                        asset_id=item.asset_id,
                        display_name=item.display_name,
                        status=PHASE8_ASSET_STATUS_BLOCKED,
                        reason_code=f"{active_run.scope}:{active_run.status.value}",
                        media_kind=item.media_kind,
                    )
                )
                continue
            if not item.eligible:
                assets.append(
                    Phase8AssetSummary(
                        asset_id=item.asset_id,
                        display_name=item.display_name,
                        status=PHASE8_ASSET_STATUS_NOT_ELIGIBLE,
                        reason_code=item.reason_code,
                        media_kind=item.media_kind,
                        not_applicable=not_applicable,
                    )
                )
                continue
            if prepared is None:
                assets.append(
                    Phase8AssetSummary(
                        asset_id=item.asset_id,
                        display_name=item.display_name,
                        status=PHASE8_ASSET_STATUS_ELIGIBLE_NOT_PREPARED,
                        media_kind=item.media_kind,
                    )
                )
                continue
            current_observations = observations_by_identity.get(
                prepared.analysis_identity_id,
                [],
            )
            if current_observations:
                view = _first_non_stale_observation_view(
                    conn,
                    observations=current_observations,
                    prepared=prepared,
                    config=config,
                )
                if view is None:
                    assets.append(
                        Phase8AssetSummary(
                            asset_id=item.asset_id,
                            display_name=item.display_name,
                            status=PHASE8_ASSET_STATUS_STALE,
                            media_kind=item.media_kind,
                            analysis_identity_id=prepared.analysis_identity_id,
                        )
                    )
                    continue
                status = _phase8_status_for_review_decision(
                    view.current_review_decision
                )
                assets.append(
                    Phase8AssetSummary(
                        asset_id=item.asset_id,
                        display_name=item.display_name,
                        status=status,
                        media_kind=item.media_kind,
                        analysis_identity_id=prepared.analysis_identity_id,
                        observation_id=view.observation_id,
                        review_decision=view.current_review_decision,
                    )
                )
                continue
            if prepared.analysis_identity_id in model_failed_identity_ids:
                assets.append(
                    Phase8AssetSummary(
                        asset_id=item.asset_id,
                        display_name=item.display_name,
                        status=PHASE8_ASSET_STATUS_MODEL_FAILED,
                        media_kind=item.media_kind,
                        analysis_identity_id=prepared.analysis_identity_id,
                    )
                )
                continue
            assets.append(
                Phase8AssetSummary(
                    asset_id=item.asset_id,
                    display_name=item.display_name,
                    status=PHASE8_ASSET_STATUS_MODEL_NOT_STARTED,
                    media_kind=item.media_kind,
                    analysis_identity_id=prepared.analysis_identity_id,
                )
            )
    finally:
        conn.close()

    counts: dict[str, int] = {}
    for asset in assets:
        counts[asset.status] = counts.get(asset.status, 0) + 1
    return Phase8ProjectSummary(
        ok=True,
        status_counts=counts,
        not_applicable_count=sum(1 for asset in assets if asset.not_applicable),
        total_assets=len(assets),
        assets=assets,
    )


def _observation_item_view(
    conn,
    *,
    observation: VisualObservationRecord,
    prepared: _PreparedAsset | None,
    config=None,
) -> ObservationReviewItemView:
    parsed = _parse_visual_observation(observation)
    identity = get_analysis_identity(
        conn,
        analysis_identity_id=observation.analysis_identity_id,
    )
    reviews = list_observation_reviews(conn, observation_id=observation.observation_id)
    current = reviews[-1] if reviews else None
    current_decision = (
        OBSERVATION_REVIEW_STATUS_UNREVIEWED if current is None else current.decision
    )
    vision = config or load_vision_config()
    is_current = (
        prepared is not None
        and observation.analysis_identity_id == prepared.analysis_identity_id
        and _frame_fingerprint_matches_prepared(
            observation.frame_hash_fingerprint,
            prepared.frame_set_fingerprint,
        )
        and _observation_matches_current_config(observation, vision)
    )
    error_code = None
    if parsed is None:
        error_code = OBSERVATION_REVIEW_ERROR_VISUAL_OBSERVATION_INVALID
    elif identity is None or not is_current:
        error_code = OBSERVATION_REVIEW_ERROR_VISUAL_OBSERVATION_STALE
    is_stale = error_code == OBSERVATION_REVIEW_ERROR_VISUAL_OBSERVATION_STALE
    is_editorial_ready = (
        parsed is not None
        and is_current
        and current_decision == ObservationReviewDecision.ACCEPTED.value
    )
    return ObservationReviewItemView(
        observation_id=observation.observation_id,
        asset_id=observation.asset_id,
        analysis_identity_id=observation.analysis_identity_id,
        working_media_id=(
            identity.working_media_id
            if identity is not None
            else ("" if prepared is None else prepared.working_media_id)
        ),
        provider=observation.provider,
        model_identifier=observation.model_identifier,
        gateway_version=observation.gateway_version,
        prompt_version=observation.prompt_version,
        response_schema_version=observation.response_schema_version,
        frame_set_fingerprint=observation.frame_hash_fingerprint,
        observation_sha256=compute_observation_sha256(observation.observation_json),
        created_at=observation.created_at,
        summary="—" if parsed is None else parsed.summary,
        evidence_frame_ids=[] if parsed is None else list(parsed.evidence_frame_ids),
        geographic_confidence=None if parsed is None else parsed.geographic_confidence,
        synthetic_confidence=None if parsed is None else parsed.synthetic_confidence,
        uncertainty_notes=[] if parsed is None else list(parsed.uncertainty_notes),
        current_review_decision=current_decision,
        review_history=reviews,
        is_current_identity=is_current,
        is_editorial_ready=is_editorial_ready,
        is_valid=parsed is not None,
        is_stale=is_stale,
        status=current_decision if not is_stale else PHASE8_ASSET_STATUS_STALE,
        error_code=error_code,
    )


def _first_non_stale_observation_view(
    conn,
    *,
    observations: list[VisualObservationRecord],
    prepared: _PreparedAsset,
    config=None,
) -> ObservationReviewItemView | None:
    for observation in sorted(
        observations,
        key=lambda item: (item.created_at, item.observation_id),
        reverse=True,
    ):
        view = _observation_item_view(
            conn,
            observation=observation,
            prepared=prepared,
            config=config,
        )
        if view.is_valid and view.is_current_identity and not view.is_stale:
            return view
    return None


def _observation_matches_current_config(observation: VisualObservationRecord, config) -> bool:
    return (
        observation.provider == config.provider
        and observation.model_identifier == config.model_identifier
        and observation.gateway_version == config.gateway_version
        and observation.prompt_version == config.prompt_version
        and observation.response_schema_version == config.response_schema_version
    )


def _frame_fingerprint_matches_prepared(
    observation_fingerprint: str, prepared_fingerprint: str
) -> bool:
    from otio_app.discovery_v2.jobs.model_analysis_worker import (
        frame_fingerprint_matches_prepared,
    )

    return frame_fingerprint_matches_prepared(
        observation_fingerprint, prepared_fingerprint
    )


def filter_observation_review_items(
    items: list[ObservationReviewItemView],
    *,
    status_filter: str = "all",
) -> list[ObservationReviewItemView]:
    """Filter observation review rows for batch UI (no I/O)."""
    key = (status_filter or "all").strip().lower()
    if key in {"", "all", "*"}:
        return list(items)
    filtered: list[ObservationReviewItemView] = []
    for item in items:
        decision = (item.current_review_decision or "").lower()
        if key == "unreviewed" and decision == OBSERVATION_REVIEW_STATUS_UNREVIEWED:
            filtered.append(item)
        elif key == "accepted" and decision == ObservationReviewDecision.ACCEPTED.value:
            filtered.append(item)
        elif key == "rejected" and decision == ObservationReviewDecision.REJECTED.value:
            filtered.append(item)
        elif key in {"low_confidence", "geringe_confidence"}:
            geo = item.geographic_confidence
            syn = item.synthetic_confidence
            if (geo is not None and geo < 0.4) or (syn is not None and syn > 0.5):
                filtered.append(item)
        elif key in {"geographic_risk", "geografisches_risiko"}:
            if item.geographic_confidence is not None and item.geographic_confidence >= 0.5:
                filtered.append(item)
        elif key in {"possibly_synthetic", "moeglicherweise_synthetisch"}:
            if item.synthetic_confidence is not None and item.synthetic_confidence >= 0.4:
                filtered.append(item)
        elif key in {"technical_warning", "technische_warnung"}:
            notes = " ".join(item.uncertainty_notes).lower()
            if "warn" in notes or "fake_adapter" in notes or item.error_code:
                filtered.append(item)
    return filtered


def _current_prepared_assets_by_asset(
    conn,
    *,
    project_id: str,
) -> dict[str, _PreparedAsset]:
    by_asset: dict[str, _PreparedAsset] = {}
    for run in list_analysis_runs(conn, project_id=project_id):
        if run.scope != ANALYSIS_RUN_SCOPE_PREPARE_ONLY:
            continue
        for asset in list_analysis_run_assets(conn, run_id=run.run_id):
            if asset.asset_id in by_asset:
                continue
            if asset.status != AnalysisPrepareAssetStatus.PREPARED:
                continue
            if not asset.analysis_identity_id:
                continue
            frames = list_representative_frames(
                conn,
                analysis_identity_id=asset.analysis_identity_id,
            )
            by_asset[asset.asset_id] = _PreparedAsset(
                asset_id=asset.asset_id,
                analysis_identity_id=asset.analysis_identity_id,
                working_media_id=asset.working_media_id,
                media_kind=asset.media_kind,
                frame_set_fingerprint=_frame_set_fingerprint_from_frames(frames),
                frame_count=len(frames),
            )
    return by_asset


def _observations_by_identity(
    conn,
    *,
    project_id: str,
) -> dict[str, list[VisualObservationRecord]]:
    by_identity: dict[str, list[VisualObservationRecord]] = {}
    for observation in list_visual_observations_for_project(conn, project_id=project_id):
        by_identity.setdefault(observation.analysis_identity_id, []).append(observation)
    return by_identity


def _model_failed_identity_ids(conn, *, project_id: str) -> set[str]:
    failed: set[str] = set()
    for run in list_analysis_runs(conn, project_id=project_id):
        if run.scope != ANALYSIS_RUN_SCOPE_MODEL:
            continue
        for asset in list_analysis_run_assets(conn, run_id=run.run_id):
            if asset.analysis_identity_id and asset.status in {
                AnalysisModelAssetStatus.FAILED,
                AnalysisModelAssetStatus.INTERRUPTED,
            }:
                failed.add(asset.analysis_identity_id)
    for attempt in list_model_analysis_attempts(conn, project_id=project_id):
        if attempt.status in {"failed", "interrupted"}:
            failed.add(attempt.analysis_identity_id)
    return failed


def _parse_visual_observation(
    record: VisualObservationRecord,
) -> VisualObservation | None:
    try:
        payload = json.loads(record.observation_json)
        if not isinstance(payload, dict):
            return None
        return VisualObservation.model_validate(payload["observation"])
    except (KeyError, TypeError, ValueError, ValidationError):
        return None


def _frame_set_fingerprint_from_frames(frames) -> str:
    normalized = "\n".join(
        sorted(str(frame.frame_sha256).lower() for frame in frames)
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _phase8_status_for_review_decision(decision: str) -> str:
    if decision == ObservationReviewDecision.ACCEPTED.value:
        return PHASE8_ASSET_STATUS_OBSERVATION_ACCEPTED
    if decision == ObservationReviewDecision.REANALYZE_REQUESTED.value:
        return PHASE8_ASSET_STATUS_REANALYSIS_REQUESTED
    if decision == ObservationReviewDecision.REJECTED.value:
        return PHASE8_ASSET_STATUS_OBSERVATION_REJECTED
    return PHASE8_ASSET_STATUS_OBSERVATION_UNREVIEWED


def _clean_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _now() -> datetime:
    return datetime.now(timezone.utc)


__all__ = [
    "EditorialReadyObservationView",
    "BatchReviewSubmitResult",
    "ObservationReviewItemView",
    "ObservationReviewProjectView",
    "ObservationReviewServiceError",
    "submit_observation_review_batch",
    "Phase8AssetSummary",
    "Phase8ProjectSummary",
    "ReviewSubmitResult",
    "filter_observation_review_items",
    "get_observation_review_view",
    "get_phase8_project_summary",
    "list_editorial_ready_observations",
    "submit_observation_review",
]
