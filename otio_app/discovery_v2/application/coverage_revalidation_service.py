"""Trigger at most one Coverage-only run after relevant observation reviews."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from otio_app.discovery_v2.application.coverage_gap_service import (
    mark_gap_resolved_with_local_asset,
    materialize_gaps_from_current_coverage,
)
from otio_app.discovery_v2.application.editorial_service import (
    get_editorial_view,
    start_coverage_run,
)
from otio_app.discovery_v2.application.inventory_service import require_discovery_project
from otio_app.discovery_v2.application.observation_review_service import (
    list_editorial_ready_observations,
)
from otio_app.discovery_v2.domain.editorial import CoverageStatus
from otio_app.discovery_v2.domain.supplementation import (
    CoverageGapStatus,
    GapEvent,
    GapEventType,
    StockCandidateUserStatus,
)
from otio_app.discovery_v2.persistence import editorial_repository as editorial_repo
from otio_app.discovery_v2.persistence import supplementation_repository as supp_repo
from otio_app.models import Project


@dataclass(frozen=True)
class CoverageRevalidationResult:
    ok: bool
    message: str
    coverage_started: bool = False
    run_id: str | None = None
    gaps_resolved: int = 0
    error_code: str | None = None


def revalidate_coverage_after_accepted_reviews(
    project: Project,
    *,
    sync: bool = False,
) -> CoverageRevalidationResult:
    """Start exactly one coverage run when accepted observations may change coverage."""
    project = require_discovery_project(project)
    ready = list_editorial_ready_observations(project)
    if not ready:
        return CoverageRevalidationResult(
            ok=True,
            message="Keine akzeptierten Observations fuer Coverage.",
            coverage_started=False,
        )
    view = get_editorial_view(project)
    if not view.ok or not view.can_start_coverage:
        return CoverageRevalidationResult(
            ok=True,
            message="Coverage-Gate noch nicht erfuellt; Review gespeichert.",
            coverage_started=False,
        )
    started = start_coverage_run(project, sync=sync)
    if started.reused and started.coverage_audit_id:
        # Completed-audit reuse: no worker, no gap rematerialization.
        return CoverageRevalidationResult(
            ok=True,
            message=started.message,
            coverage_started=False,
            run_id=None,
            gaps_resolved=0,
        )
    if started.reused and started.run is not None:
        return CoverageRevalidationResult(
            ok=True,
            message=started.message,
            coverage_started=True,
            run_id=started.run.run_id,
            gaps_resolved=0,
        )
    if not started.started:
        return CoverageRevalidationResult(
            ok=False,
            message=started.message,
            coverage_started=False,
            error_code=started.error_code,
        )
    resolved = 0
    if sync:
        resolved = _resolve_gaps_from_current_coverage(project)
    return CoverageRevalidationResult(
        ok=True,
        message=started.message,
        coverage_started=True,
        run_id=started.run.run_id if started.run else None,
        gaps_resolved=resolved,
    )


def _resolve_gaps_from_current_coverage(project: Project) -> int:
    """Terminalize gaps when current coverage confirms an accepted-asset match."""
    materialize_gaps_from_current_coverage(project)
    conn = editorial_repo.open_editorial_registry(project.project_root_path)
    try:
        state = editorial_repo.get_project_state(conn, project_id=project.id)
        audit_id = state.active_coverage_audit_id if state is not None else None
        if not audit_id:
            return 0
        audit = editorial_repo.get_coverage_audit(conn, coverage_audit_id=audit_id)
        if audit is None:
            return 0
        results = list(audit.results)
    finally:
        conn.close()

    accepted_assets = {
        item.asset_id for item in list_editorial_ready_observations(project)
    }
    materialize = materialize_gaps_from_current_coverage(project)
    gaps = {gap.visual_intent_id: gap for gap in materialize.gaps}
    resolved = 0
    for result in results:
        if result.coverage_status != CoverageStatus.COVERED:
            continue
        gap = gaps.get(result.visual_intent_id)
        if gap is None or gap.status in {
            CoverageGapStatus.RESOLVED_WITH_LOCAL_ASSET,
            CoverageGapStatus.RESOLVED_WITH_SUPPLEMENT,
            CoverageGapStatus.ACCEPTED_UNRESOLVED,
            CoverageGapStatus.SUPERSEDED,
        }:
            continue
        candidates = [
            asset_id
            for asset_id in (result.candidate_asset_ids or [])
            if asset_id in accepted_assets
        ]
        if not candidates:
            continue
        asset_id = candidates[0]
        action = mark_gap_resolved_with_local_asset(
            project, gap_id=gap.gap_id, asset_id=asset_id
        )
        if action.ok:
            try:
                _maybe_promote_to_supplement(
                    project, gap_id=gap.gap_id, asset_id=asset_id
                )
            except Exception:
                # Local resolve already persisted; supplement promotion is best-effort.
                pass
            resolved += 1
    return resolved


def _maybe_promote_to_supplement(
    project: Project, *, gap_id: str, asset_id: str
) -> None:
    conn = supp_repo.open_supplementation_registry(project.project_root_path)
    try:
        gap = supp_repo.get_coverage_gap(conn, gap_id=gap_id)
        if gap is None:
            return
        candidates = supp_repo.list_stock_candidates_for_gap(conn, gap_id=gap_id)
        if not any(
            candidate.user_status == StockCandidateUserStatus.ACCEPTED_FOR_IMPORT
            for candidate in candidates
        ):
            return
        updated = gap.model_copy(
            update={
                "status": CoverageGapStatus.RESOLVED_WITH_SUPPLEMENT,
                "resolved_asset_id": asset_id,
                "outcome": "Coverage-Match mit akzeptiertem Supplement Working Media.",
                "updated_at": datetime.now(timezone.utc),
            }
        )
        relative = supp_repo.save_coverage_gap_json(project.project_root_path, updated)
        conn.execute("BEGIN IMMEDIATE")
        supp_repo.update_coverage_gap(conn, updated, relative)
        supp_repo.append_gap_event(
            conn,
            GapEvent(
                event_id=supp_repo.new_gap_event_id(),
                gap_id=gap.gap_id,
                project_id=project.id,
                event_type=GapEventType.RESOLVED,
                message="Gap durch Coverage-Revalidierung terminal (supplement).",
                payload={"asset_id": asset_id, "status": "resolved_with_supplement"},
                created_at=datetime.now(timezone.utc),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


__all__ = [
    "CoverageRevalidationResult",
    "revalidate_coverage_after_accepted_reviews",
]
