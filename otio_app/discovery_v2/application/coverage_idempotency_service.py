"""Canonical Coverage Input build, fingerprint, and reuse lookup (C2)."""

from __future__ import annotations

from dataclasses import dataclass

from otio_app.discovery_v2.adapters.text_config import load_text_config
from otio_app.discovery_v2.application.inventory_service import require_discovery_project
from otio_app.discovery_v2.application.observation_review_service import (
    list_editorial_ready_observations,
)
from otio_app.discovery_v2.domain.coverage_input import (
    COVERAGE_INPUT_SCHEMA_VERSION,
    CanonicalCoverageInput,
    CoverageExecutionMode,
    EDITORIAL_ERROR_COVERAGE_ACTIVE_RUN_INPUT_UNAVAILABLE,
    EDITORIAL_ERROR_COVERAGE_CANONICAL_INPUT_INVALID,
    EDITORIAL_ERROR_COVERAGE_COMPLETED_AUDIT_REUSE_UNSAFE,
    EDITORIAL_ERROR_COVERAGE_CURRENT_AUDIT_INPUT_UNAVAILABLE,
    REUSE_REASON_ACTIVE_EQUIVALENT_RUN,
    REUSE_REASON_COMPLETED_CURRENT_AUDIT,
    audit_has_stored_canonical_fingerprint,
    build_canonical_coverage_input,
    build_coverage_run_dedup_key,
    compute_canonical_coverage_fingerprint,
)
from otio_app.discovery_v2.domain.editorial import (
    ACTIVE_EDITORIAL_RUN_STATUSES,
    EDITORIAL_RUN_SCOPE_COVERAGE,
    CoverageAudit,
    CoverageAuditStatus,
    EditorialRun,
    compute_observation_set_fingerprint,
)
from otio_app.discovery_v2.persistence import editorial_repository as repo
from otio_app.models import Project


@dataclass(frozen=True)
class CanonicalCoverageBuildResult:
    ok: bool
    coverage_input: CanonicalCoverageInput | None = None
    fingerprint: str | None = None
    dedup_key: str | None = None
    message: str = ""
    error_code: str | None = None


@dataclass(frozen=True)
class CompletedAuditReuseMatch:
    ok: bool
    audit: CoverageAudit | None = None
    fingerprint: str | None = None
    reuse_reason: str | None = None
    message: str = ""
    error_code: str | None = None
    unsafe_legacy: bool = False


@dataclass(frozen=True)
class ActiveRunReuseMatch:
    ok: bool
    run: EditorialRun | None = None
    fingerprint: str | None = None
    reuse_reason: str | None = None
    message: str = ""
    error_code: str | None = None
    conflict: bool = False


def build_current_canonical_coverage(
    project: Project,
    *,
    execution_mode: CoverageExecutionMode = "normal",
) -> CanonicalCoverageBuildResult:
    project = require_discovery_project(project)
    config = load_text_config()
    observations = list_editorial_ready_observations(project)
    observation_fingerprint = compute_observation_set_fingerprint(observations)
    conn = repo.open_editorial_registry(project.project_root_path)
    try:
        brief = repo.get_active_project_brief(conn, project_id=project.id)
        state = repo.get_project_state(conn, project_id=project.id)
        narrative = repo.get_active_narrative_plan(conn, project_id=project.id)
        script = repo.get_active_script(conn, project_id=project.id)
        if brief is None or state is None or narrative is None or script is None:
            return CanonicalCoverageBuildResult(
                ok=False,
                message="Coverage-Inputs unvollstaendig.",
                error_code=EDITORIAL_ERROR_COVERAGE_CANONICAL_INPUT_INVALID,
            )
        if not state.selected_hook_id:
            return CanonicalCoverageBuildResult(
                ok=False,
                message="Ausgewaehlter Hook fehlt.",
                error_code=EDITORIAL_ERROR_COVERAGE_CANONICAL_INPUT_INVALID,
            )
        hook = repo.get_hook_variant(conn, hook_id=state.selected_hook_id)
        bundle = repo.get_script_bundle(conn, script_id=script.script_id)
        if hook is None or bundle is None:
            return CanonicalCoverageBuildResult(
                ok=False,
                message="Hook oder Script-Struktur fehlt.",
                error_code=EDITORIAL_ERROR_COVERAGE_CANONICAL_INPUT_INVALID,
            )
        try:
            coverage_input = build_canonical_coverage_input(
                project_id=project.id,
                brief=brief,
                narrative=narrative,
                hook=hook,
                script=script,
                script_bundle=bundle,
                observation_fingerprint=observation_fingerprint,
                provider=config.provider,
                model_identifier=config.model_identifier,
                gateway_version=config.gateway_version,
                prompt_version=config.prompts["coverage"],
                response_schema_version=config.response_schemas["coverage"],
            )
        except Exception as exc:  # noqa: BLE001
            return CanonicalCoverageBuildResult(
                ok=False,
                message=f"Canonical Coverage Input ungueltig: {exc}",
                error_code=EDITORIAL_ERROR_COVERAGE_CANONICAL_INPUT_INVALID,
            )
    finally:
        conn.close()
    fingerprint = compute_canonical_coverage_fingerprint(coverage_input)
    dedup_key = build_coverage_run_dedup_key(
        project_id=project.id,
        canonical_coverage_input_fingerprint=fingerprint,
        coverage_scope=EDITORIAL_RUN_SCOPE_COVERAGE,
        execution_mode=execution_mode,
    )
    return CanonicalCoverageBuildResult(
        ok=True,
        coverage_input=coverage_input,
        fingerprint=fingerprint,
        dedup_key=dedup_key,
        message="Canonical Coverage Input berechnet.",
    )


def find_active_equivalent_coverage_run(
    project: Project,
    *,
    fingerprint: str,
    dedup_key: str,
) -> ActiveRunReuseMatch:
    project = require_discovery_project(project)
    conn = repo.open_editorial_registry(project.project_root_path)
    try:
        active = repo.find_active_editorial_run(conn, project_id=project.id)
    finally:
        conn.close()
    if active is None:
        return ActiveRunReuseMatch(ok=False, message="Kein aktiver Editorial-Run.")
    if active.scope != EDITORIAL_RUN_SCOPE_COVERAGE:
        return ActiveRunReuseMatch(
            ok=False,
            run=active,
            conflict=True,
            message=(
                f"Es laeuft bereits ein Editorial-Run "
                f"({active.scope}/{active.status.value})."
            ),
            error_code="editorial_run_already_active",
        )
    if active.status not in ACTIVE_EDITORIAL_RUN_STATUSES:
        return ActiveRunReuseMatch(ok=False, message="Aktiver Run nicht queued/running.")
    marker = repo.load_coverage_run_dedup_marker(
        project.project_root_path, run_id=active.run_id
    )
    if marker is None:
        return ActiveRunReuseMatch(
            ok=False,
            run=active,
            conflict=True,
            message="Dedup-Marker des aktiven Coverage-Runs fehlt.",
            error_code=EDITORIAL_ERROR_COVERAGE_ACTIVE_RUN_INPUT_UNAVAILABLE,
        )
    marker_key = str(marker.get("dedup_key") or "")
    marker_fp = str(marker.get("canonical_coverage_input_fingerprint") or "")
    if marker_key == dedup_key and marker_fp == fingerprint:
        return ActiveRunReuseMatch(
            ok=True,
            run=active,
            fingerprint=fingerprint,
            reuse_reason=REUSE_REASON_ACTIVE_EQUIVALENT_RUN,
            message="Aktiver aequivalenter Coverage-Run wiederverwendet.",
        )
    return ActiveRunReuseMatch(
        ok=False,
        run=active,
        conflict=True,
        message=(
            f"Es laeuft bereits ein Editorial-Run "
            f"({active.scope}/{active.status.value})."
        ),
        error_code="editorial_run_already_active",
    )


def reconstruct_legacy_canonical_fingerprint(
    project: Project,
    audit: CoverageAudit,
) -> tuple[str | None, str | None]:
    """Return (fingerprint, error_code). error_code set when reconstruction unsafe."""

    conn = repo.open_editorial_registry(project.project_root_path)
    try:
        brief = repo.get_project_brief_by_version(
            conn, project_id=project.id, brief_version=audit.brief_version
        )
        narrative = repo.get_narrative_plan(
            conn, narrative_plan_id=audit.narrative_plan_id
        )
        script = repo.get_script_draft(conn, script_id=audit.script_id)
        if brief is None or narrative is None or script is None:
            return None, EDITORIAL_ERROR_COVERAGE_COMPLETED_AUDIT_REUSE_UNSAFE
        if script.script_version != audit.script_version:
            return None, EDITORIAL_ERROR_COVERAGE_COMPLETED_AUDIT_REUSE_UNSAFE
        if not script.selected_hook_id:
            return None, EDITORIAL_ERROR_COVERAGE_COMPLETED_AUDIT_REUSE_UNSAFE
        hook = repo.get_hook_variant(conn, hook_id=script.selected_hook_id)
        if hook is None or hook.narrative_plan_id != audit.narrative_plan_id:
            return None, EDITORIAL_ERROR_COVERAGE_COMPLETED_AUDIT_REUSE_UNSAFE
        bundle = repo.get_script_bundle(conn, script_id=script.script_id)
        if bundle is None:
            return None, EDITORIAL_ERROR_COVERAGE_COMPLETED_AUDIT_REUSE_UNSAFE
        # Model routing from the audit itself (reconstructable legacy components).
        # response_schema_version is audit.schema_version for coverage-audit-v1.
        try:
            coverage_input = build_canonical_coverage_input(
                project_id=audit.project_id,
                brief=brief,
                narrative=narrative,
                hook=hook,
                script=script,
                script_bundle=bundle,
                observation_fingerprint=audit.input_observation_fingerprint,
                provider=audit.provider,
                model_identifier=audit.model_identifier,
                gateway_version=audit.gateway_version,
                prompt_version=audit.prompt_version,
                response_schema_version=audit.schema_version,
            )
        except Exception:
            return None, EDITORIAL_ERROR_COVERAGE_COMPLETED_AUDIT_REUSE_UNSAFE
    finally:
        conn.close()
    return compute_canonical_coverage_fingerprint(coverage_input), None


def find_completed_equivalent_current_audit(
    project: Project,
    *,
    fingerprint: str,
) -> CompletedAuditReuseMatch:
    project = require_discovery_project(project)
    conn = repo.open_editorial_registry(project.project_root_path)
    try:
        state = repo.get_project_state(conn, project_id=project.id)
        if state is None or not state.active_coverage_audit_id:
            return CompletedAuditReuseMatch(
                ok=False,
                message="Kein Current Coverage Audit.",
                error_code=EDITORIAL_ERROR_COVERAGE_CURRENT_AUDIT_INPUT_UNAVAILABLE,
            )
        audit = repo.get_coverage_audit(
            conn, coverage_audit_id=state.active_coverage_audit_id
        )
    finally:
        conn.close()
    if audit is None:
        return CompletedAuditReuseMatch(
            ok=False,
            message="Current Coverage Audit fehlt.",
            error_code=EDITORIAL_ERROR_COVERAGE_CURRENT_AUDIT_INPUT_UNAVAILABLE,
        )
    if audit.status != CoverageAuditStatus.COMPLETED:
        return CompletedAuditReuseMatch(
            ok=False,
            audit=audit,
            message="Current Coverage Audit ist nicht completed.",
        )
    if audit_has_stored_canonical_fingerprint(audit):
        stored = str(audit.canonical_coverage_input_fingerprint)
        if stored == fingerprint:
            return CompletedAuditReuseMatch(
                ok=True,
                audit=audit,
                fingerprint=fingerprint,
                reuse_reason=REUSE_REASON_COMPLETED_CURRENT_AUDIT,
                message="Completed Current Audit wiederverwendet.",
            )
        return CompletedAuditReuseMatch(
            ok=False,
            audit=audit,
            fingerprint=stored,
            message="Canonical Fingerprint weicht ab.",
            error_code="coverage_input_fingerprint_mismatch",
        )
    legacy_fp, unsafe = reconstruct_legacy_canonical_fingerprint(project, audit)
    if unsafe or legacy_fp is None:
        return CompletedAuditReuseMatch(
            ok=False,
            audit=audit,
            message="Legacy-Audit nicht sicher rekonstruierbar.",
            error_code=EDITORIAL_ERROR_COVERAGE_COMPLETED_AUDIT_REUSE_UNSAFE,
            unsafe_legacy=True,
        )
    if legacy_fp == fingerprint:
        return CompletedAuditReuseMatch(
            ok=True,
            audit=audit,
            fingerprint=fingerprint,
            reuse_reason=REUSE_REASON_COMPLETED_CURRENT_AUDIT,
            message="Legacy Completed Current Audit sicher wiederverwendet.",
        )
    return CompletedAuditReuseMatch(
        ok=False,
        audit=audit,
        fingerprint=legacy_fp,
        message="Legacy-Fingerprint weicht ab.",
        error_code="coverage_input_fingerprint_mismatch",
    )


__all__ = [
    "ActiveRunReuseMatch",
    "CanonicalCoverageBuildResult",
    "CompletedAuditReuseMatch",
    "COVERAGE_INPUT_SCHEMA_VERSION",
    "build_current_canonical_coverage",
    "find_active_equivalent_coverage_run",
    "find_completed_equivalent_current_audit",
    "reconstruct_legacy_canonical_fingerprint",
]
