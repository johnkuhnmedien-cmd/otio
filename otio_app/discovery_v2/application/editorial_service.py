"""Application service for Discovery V2 Editorial Core (Phase 9)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from otio_app.discovery_v2.adapters.editorial_job_launcher import (
    get_editorial_job_launcher,
)
from otio_app.discovery_v2.adapters.text_config import load_text_config
from otio_app.discovery_v2.application.editorial_job_recovery import (
    reconcile_orphaned_editorial_run,
)
from otio_app.discovery_v2.application.inventory_service import (
    InventoryServiceError,
    require_discovery_project,
)
from otio_app.discovery_v2.application.observation_review_service import (
    list_editorial_ready_observations,
)
from otio_app.discovery_v2.domain.editorial import (
    EDITORIAL_ERROR_ANALYSIS_RUN_ALREADY_ACTIVE,
    EDITORIAL_ERROR_INPUT_STALE,
    EDITORIAL_ERROR_PROJECT_BRIEF_MISSING,
    EDITORIAL_ERROR_RUN_ALREADY_ACTIVE,
    EDITORIAL_RUN_SCOPE_COVERAGE,
    EDITORIAL_RUN_SCOPE_NARRATIVE,
    EDITORIAL_RUN_SCOPE_SCRIPT,
    EDITORIAL_RUN_SCOPE_STRUCTURE,
    EDITORIAL_SCHEMA_VERSION,
    GATEWAY_VERSION,
    RESPONSE_SCHEMA_SCRIPT,
    ScriptDraft,
    ScriptDraftStatus,
    ScriptSourceKind,
    EditorialProjectState,
    EditorialProjectStateStatus,
    EditorialReadyObservationInput,
    EditorialRun,
    EditorialRunStatus,
    HookVariant,
    NarrativePlan,
    ProjectBrief,
    ProjectBriefStatus,
    compute_observation_set_fingerprint,
    compute_text_sha256,
)
from otio_app.discovery_v2.persistence.asset_analysis_repository import (
    find_active_analysis_run,
)
from otio_app.discovery_v2.persistence.asset_registry_database import RegistryDatabaseError
from otio_app.discovery_v2.persistence import editorial_repository as repo
from otio_app.discovery_v2.persistence.supplementation_repository import (
    find_active_supplementation_run,
)
from otio_app.discovery_v2.persistence.narration_repository import (
    find_active_narration_run,
)
from otio_app.models import Project


class EditorialServiceError(InventoryServiceError):
    """Domain error for editorial service operations."""


@dataclass(frozen=True)
class EditorialStartResult:
    started: bool
    message: str
    run: EditorialRun | None = None
    error_code: str | None = None
    reused: bool = False
    coverage_audit_id: str | None = None
    canonical_input_fingerprint: str | None = None
    reuse_reason: str | None = None


@dataclass(frozen=True)
class BriefSaveResult:
    ok: bool
    message: str
    brief: ProjectBrief | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class HookSelectResult:
    ok: bool
    message: str
    selected_hook_id: str | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class ScriptEditResult:
    ok: bool
    message: str
    script: ScriptDraft | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class EditorialView:
    ok: bool
    message: str | None = None
    active_run: EditorialRun | None = None
    runs: list[EditorialRun] = field(default_factory=list)
    briefs: list[ProjectBrief] = field(default_factory=list)
    active_brief: ProjectBrief | None = None
    narrative_plan: NarrativePlan | None = None
    hooks: list[HookVariant] = field(default_factory=list)
    selected_hook_id: str | None = None
    script: ScriptDraft | None = None
    script_bundle: dict | None = None
    script_versions: list[ScriptDraft] = field(default_factory=list)
    coverage_audit: object | None = None
    editorial_ready_count: int = 0
    observation_fingerprint: str | None = None
    stale: bool = False
    can_start_narrative: bool = False
    can_start_script: bool = False
    can_start_structure: bool = False
    can_start_coverage: bool = False
    latest_claim_decisions: dict[str, object] = field(default_factory=dict)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def save_project_brief(
    project: Project,
    *,
    language: str,
    topic: str,
    target_audience: str,
    tone: str,
    desired_duration_seconds: int | None = None,
    geographic_frame: str | None = None,
    must_include: list[str] | None = None,
    must_exclude: list[str] | None = None,
    user_notes: str | None = None,
) -> BriefSaveResult:
    project = require_discovery_project(project)
    try:
        conn = repo.open_editorial_registry(project.project_root_path)
    except RegistryDatabaseError as exc:
        raise EditorialServiceError(str(exc)) from exc
    try:
        previous = repo.get_active_project_brief(conn, project_id=project.id)
        version = repo.next_brief_version(conn, project_id=project.id)
        content = {
            "language": language.strip(),
            "topic": topic.strip(),
            "target_audience": target_audience.strip(),
            "tone": tone.strip(),
            "desired_duration_seconds": desired_duration_seconds,
            "geographic_frame": _clean_optional(geographic_frame),
            "must_include": list(must_include or []),
            "must_exclude": list(must_exclude or []),
            "user_notes": _clean_optional(user_notes),
        }
        brief = ProjectBrief(
            project_brief_id=repo.new_project_brief_id(),
            project_id=project.id,
            brief_version=version,
            content_sha256=compute_text_sha256(content),
            status=ProjectBriefStatus.ACTIVE,
            created_at=_now(),
            supersedes_brief_id=(
                None if previous is None else previous.project_brief_id
            ),
            **content,
        )
        relative = repo.save_project_brief_json(project.project_root_path, brief)
        conn.execute("BEGIN IMMEDIATE")
        if previous is not None:
            repo.update_project_brief_status(
                conn,
                project_brief_id=previous.project_brief_id,
                status=ProjectBriefStatus.SUPERSEDED,
            )
        repo.insert_project_brief(conn, brief, relative)
        repo.mark_editorial_derivatives_stale(conn, project_id=project.id)
        state = _state_with(
            conn,
            project_id=project.id,
            active_brief_id=brief.project_brief_id,
            active_narrative_plan_id=None,
            selected_hook_id=None,
            active_script_id=None,
            active_coverage_audit_id=None,
        )
        repo.upsert_project_state(conn, state)
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        return BriefSaveResult(
            ok=False,
            message=f"Project Brief konnte nicht gespeichert werden: {exc}",
            error_code=EDITORIAL_ERROR_PROJECT_BRIEF_MISSING,
        )
    finally:
        conn.close()
    return BriefSaveResult(ok=True, message="Project Brief gespeichert.", brief=brief)


def list_project_briefs(project: Project) -> list[ProjectBrief]:
    project = require_discovery_project(project)
    conn = repo.open_editorial_registry(project.project_root_path)
    try:
        return repo.list_project_briefs(conn, project_id=project.id)
    finally:
        conn.close()


def get_active_project_brief(project: Project) -> ProjectBrief | None:
    project = require_discovery_project(project)
    conn = repo.open_editorial_registry(project.project_root_path)
    try:
        return repo.get_active_project_brief(conn, project_id=project.id)
    finally:
        conn.close()


def start_narrative_run(project: Project, *, sync: bool = False) -> EditorialStartResult:
    return _start_run(project, scope=EDITORIAL_RUN_SCOPE_NARRATIVE, sync=sync)


def start_script_run(project: Project, *, sync: bool = False) -> EditorialStartResult:
    return _start_run(project, scope=EDITORIAL_RUN_SCOPE_SCRIPT, sync=sync)


def start_structure_run(project: Project, *, sync: bool = False) -> EditorialStartResult:
    return _start_run(project, scope=EDITORIAL_RUN_SCOPE_STRUCTURE, sync=sync)


def start_coverage_run(
    project: Project,
    *,
    sync: bool = False,
    execution_mode: str = "normal",
) -> EditorialStartResult:
    """Start coverage or reuse an equivalent active run / completed current audit."""

    from otio_app.discovery_v2.application.coverage_idempotency_service import (
        build_current_canonical_coverage,
        find_active_equivalent_coverage_run,
        find_completed_equivalent_current_audit,
    )
    from otio_app.discovery_v2.domain.coverage_input import CoverageExecutionMode

    mode: CoverageExecutionMode
    if execution_mode in {"normal", "retry_failed", "force_recompute"}:
        mode = execution_mode  # type: ignore[assignment]
    else:
        return EditorialStartResult(
            started=False,
            message=f"Ungueltiger Coverage-Execution-Mode: {execution_mode}",
            error_code="coverage_canonical_input_invalid",
        )
    built = build_current_canonical_coverage(project, execution_mode=mode)
    if not built.ok or not built.fingerprint or not built.dedup_key:
        return EditorialStartResult(
            started=False,
            message=built.message,
            error_code=built.error_code,
            canonical_input_fingerprint=built.fingerprint,
        )
    if mode != "force_recompute":
        completed = find_completed_equivalent_current_audit(
            project, fingerprint=built.fingerprint
        )
        if completed.ok and completed.audit is not None:
            return EditorialStartResult(
                started=False,
                message=completed.message,
                reused=True,
                coverage_audit_id=completed.audit.coverage_audit_id,
                canonical_input_fingerprint=built.fingerprint,
                reuse_reason=completed.reuse_reason,
            )
        active = find_active_equivalent_coverage_run(
            project,
            fingerprint=built.fingerprint,
            dedup_key=built.dedup_key,
        )
        if active.ok and active.run is not None:
            return EditorialStartResult(
                started=True,
                message=active.message,
                run=active.run,
                reused=True,
                coverage_audit_id=None,
                canonical_input_fingerprint=built.fingerprint,
                reuse_reason=active.reuse_reason,
            )
        if active.conflict:
            return EditorialStartResult(
                started=False,
                message=active.message,
                run=active.run,
                error_code=active.error_code or EDITORIAL_ERROR_RUN_ALREADY_ACTIVE,
                canonical_input_fingerprint=built.fingerprint,
            )
    return _start_run(
        project,
        scope=EDITORIAL_RUN_SCOPE_COVERAGE,
        sync=sync,
        coverage_fingerprint=built.fingerprint,
        coverage_dedup_key=built.dedup_key,
        coverage_execution_mode=mode,
    )


def select_hook(project: Project, *, hook_id: str) -> HookSelectResult:
    project = require_discovery_project(project)
    conn = repo.open_editorial_registry(project.project_root_path)
    try:
        hook = repo.get_hook_variant(conn, hook_id=hook_id)
        if hook is None:
            return HookSelectResult(ok=False, message="Hook wurde nicht gefunden.")
        conn.execute("BEGIN IMMEDIATE")
        repo.set_selected_hook(
            conn,
            narrative_plan_id=hook.narrative_plan_id,
            hook_id=hook.hook_id,
        )
        repo.mark_coverage_stale_for_script(conn, script_id="")
        state = _state_with(
            conn,
            project_id=project.id,
            active_narrative_plan_id=hook.narrative_plan_id,
            selected_hook_id=hook.hook_id,
            active_script_id=None,
            active_coverage_audit_id=None,
        )
        repo.upsert_project_state(conn, state)
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        return HookSelectResult(ok=False, message=str(exc))
    finally:
        conn.close()
    return HookSelectResult(
        ok=True,
        message="Hook ausgewaehlt.",
        selected_hook_id=hook_id,
    )


def save_user_script_edit(
    project: Project,
    *,
    full_text: str,
) -> ScriptEditResult:
    project = require_discovery_project(project)
    config = load_text_config()
    conn = repo.open_editorial_registry(project.project_root_path)
    try:
        current = repo.get_active_script(conn, project_id=project.id)
        brief = repo.get_active_project_brief(conn, project_id=project.id)
        state = repo.get_project_state(conn, project_id=project.id)
        if current is None or brief is None:
            return ScriptEditResult(
                ok=False,
                message="Aktives Skript oder Brief fehlt.",
                error_code=EDITORIAL_ERROR_PROJECT_BRIEF_MISSING,
            )
        version = repo.next_script_version(conn, project_id=project.id)
        script = ScriptDraft(
            script_id=repo.new_script_id(),
            script_version=version,
            project_id=project.id,
            language=current.language,
            full_text=full_text,
            sentence_order=[],
            narrative_plan_id=current.narrative_plan_id,
            selected_hook_id=current.selected_hook_id,
            project_brief_id=brief.project_brief_id,
            brief_version=brief.brief_version,
            prompt_version=config.prompts["structure"],
            gateway_version=GATEWAY_VERSION,
            model_identifier=config.model_identifier,
            provider=config.provider,
            source_kind=ScriptSourceKind.USER_EDIT,
            supersedes_script_id=current.script_id,
            content_sha256=compute_text_sha256(full_text),
            status=ScriptDraftStatus.STRUCTURE_PENDING,
            created_at=_now(),
        )
        relative = repo.save_script_bundle_json(
            project.project_root_path,
            script=script,
            sentences=[],
            claims=[],
            visual_beats=[],
            visual_intents=[],
        )
        conn.execute("BEGIN IMMEDIATE")
        repo.update_script_status(
            conn,
            script_id=current.script_id,
            status=ScriptDraftStatus.SUPERSEDED,
        )
        repo.insert_script_bundle(
            conn,
            script=script,
            sentences=[],
            claims=[],
            visual_beats=[],
            visual_intents=[],
            relative_json_path=relative,
        )
        repo.mark_coverage_stale_for_script(conn, script_id=current.script_id)
        repo.upsert_project_state(
            conn,
            (state or _state_with(conn, project_id=project.id)).model_copy(
                update={
                    "active_script_id": script.script_id,
                    "active_coverage_audit_id": None,
                    "status": EditorialProjectStateStatus.STALE,
                    "updated_at": _now(),
                }
            ),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        return ScriptEditResult(ok=False, message=str(exc))
    finally:
        conn.close()
    return ScriptEditResult(ok=True, message="Skriptversion gespeichert.", script=script)


def get_editorial_view(project: Project) -> EditorialView:
    try:
        project = require_discovery_project(project)
    except InventoryServiceError as exc:
        return EditorialView(ok=False, message=str(exc))
    reconcile_orphaned_editorial_run(project)
    observations = _editorial_ready_inputs(project)
    fingerprint = compute_observation_set_fingerprint(observations)
    try:
        conn = repo.open_editorial_registry(project.project_root_path)
    except RegistryDatabaseError as exc:
        return EditorialView(ok=False, message=str(exc))
    try:
        active_run = repo.find_active_editorial_run(conn, project_id=project.id)
        runs = repo.list_editorial_runs(conn, project_id=project.id)
        briefs = repo.list_project_briefs(conn, project_id=project.id)
        active_brief = repo.get_active_project_brief(conn, project_id=project.id)
        state = repo.get_project_state(conn, project_id=project.id)
        narrative = repo.get_active_narrative_plan(conn, project_id=project.id)
        hooks = (
            []
            if narrative is None
            else repo.list_hook_variants(
                conn, narrative_plan_id=narrative.narrative_plan_id
            )
        )
        selected_hook_id = state.selected_hook_id if state else None
        script = repo.get_active_script(conn, project_id=project.id)
        script_bundle = None if script is None else repo.get_script_bundle(conn, script_id=script.script_id)
        scripts = repo.list_script_drafts(conn, project_id=project.id)
        coverage = repo.get_latest_coverage_audit(conn, project_id=project.id)
    finally:
        conn.close()
    latest_claim_decisions: dict[str, object] = {}
    if script is not None:
        from otio_app.discovery_v2.persistence import supplementation_repository as supp_repo

        try:
            supp_conn = supp_repo.open_supplementation_registry(project.project_root_path)
            try:
                latest_claim_decisions = dict(
                    supp_repo.latest_claim_decisions_for_script(
                        supp_conn,
                        project_id=project.id,
                        script_id=script.script_id,
                    )
                )
            finally:
                supp_conn.close()
        except RegistryDatabaseError:
            latest_claim_decisions = {}
    stale = bool(
        (narrative is not None and narrative.input_observation_fingerprint != fingerprint)
        or (coverage is not None and coverage.input_observation_fingerprint != fingerprint)
        or (state is not None and state.status == EditorialProjectStateStatus.STALE)
    )
    return EditorialView(
        ok=True,
        active_run=active_run,
        runs=runs,
        briefs=briefs,
        active_brief=active_brief,
        narrative_plan=narrative,
        hooks=hooks,
        selected_hook_id=selected_hook_id,
        script=script,
        script_bundle=script_bundle,
        script_versions=scripts,
        coverage_audit=coverage,
        editorial_ready_count=len(observations),
        observation_fingerprint=fingerprint,
        stale=stale,
        can_start_narrative=active_run is None and active_brief is not None,
        can_start_script=active_run is None and narrative is not None and bool(selected_hook_id),
        can_start_structure=active_run is None and script is not None,
        can_start_coverage=(
            active_run is None
            and script_bundle is not None
            and bool(script_bundle.get("visual_intents"))
        ),
        latest_claim_decisions=latest_claim_decisions,
    )


def _start_run(
    project: Project,
    *,
    scope: str,
    sync: bool,
    coverage_fingerprint: str | None = None,
    coverage_dedup_key: str | None = None,
    coverage_execution_mode: str = "normal",
) -> EditorialStartResult:
    project = require_discovery_project(project)
    reconcile_orphaned_editorial_run(project)
    conn = repo.open_editorial_registry(project.project_root_path)
    try:
        from otio_app.discovery_v2.persistence.export_repository import (
            find_active_export_run,
        )

        if find_active_export_run(conn, project_id=project.id) is not None:
            return EditorialStartResult(
                started=False,
                message="Es läuft bereits ein Export-Run.",
                error_code="export_run_already_active",
            )
        analysis_active = find_active_analysis_run(conn, project_id=project.id)
        if analysis_active is not None:
            return EditorialStartResult(
                started=False,
                message=(
                    f"Es laeuft bereits ein Analysis-Run "
                    f"({analysis_active.scope}/{analysis_active.status.value})."
                ),
                error_code=EDITORIAL_ERROR_ANALYSIS_RUN_ALREADY_ACTIVE,
            )
        supplementation_active = find_active_supplementation_run(conn, project_id=project.id)
        if supplementation_active is not None:
            return EditorialStartResult(
                started=False,
                message=(
                    f"Es laeuft bereits ein Supplementation-Run "
                    f"({supplementation_active.scope}/{supplementation_active.status.value})."
                ),
                error_code="supplementation_run_already_active",
            )
        narration_active = find_active_narration_run(conn, project_id=project.id)
        if narration_active is not None:
            return EditorialStartResult(
                started=False,
                message=(
                    f"Es laeuft bereits ein Narration-Run "
                    f"({narration_active.scope}/{narration_active.status.value})."
                ),
                error_code="narration_run_already_active",
            )
        from otio_app.discovery_v2.persistence.visual_edit_repository import (
            find_active_visual_edit_run,
        )

        visual_edit_active = find_active_visual_edit_run(conn, project_id=project.id)
        if visual_edit_active is not None:
            return EditorialStartResult(
                started=False,
                message=(
                    f"Es laeuft bereits ein Visual-Edit-Run "
                    f"({visual_edit_active.scope}/{visual_edit_active.status.value})."
                ),
                error_code="visual_edit_run_already_active",
            )
        active = repo.find_active_editorial_run(conn, project_id=project.id)
        if active is not None:
            return EditorialStartResult(
                started=False,
                message=(
                    f"Es laeuft bereits ein Editorial-Run "
                    f"({active.scope}/{active.status.value})."
                ),
                run=active,
                error_code=EDITORIAL_ERROR_RUN_ALREADY_ACTIVE,
            )
        brief = repo.get_active_project_brief(conn, project_id=project.id)
        if brief is None:
            return EditorialStartResult(
                started=False,
                message="Kein aktiver Project Brief vorhanden.",
                error_code=EDITORIAL_ERROR_PROJECT_BRIEF_MISSING,
            )
        state = repo.get_project_state(conn, project_id=project.id)
        script = repo.get_active_script(conn, project_id=project.id)
        narrative_id = state.active_narrative_plan_id if state else None
        if scope in {EDITORIAL_RUN_SCOPE_SCRIPT, EDITORIAL_RUN_SCOPE_COVERAGE} and not narrative_id:
            return EditorialStartResult(
                started=False,
                message="Narrative Plan fehlt oder ist stale.",
                error_code=EDITORIAL_ERROR_INPUT_STALE,
            )
        if scope == EDITORIAL_RUN_SCOPE_COVERAGE and script is None:
            return EditorialStartResult(
                started=False,
                message="Aktives Skript fehlt.",
                error_code=EDITORIAL_ERROR_INPUT_STALE,
            )
        if scope == EDITORIAL_RUN_SCOPE_STRUCTURE and script is None:
            return EditorialStartResult(
                started=False,
                message="Aktives Skript fehlt.",
                error_code=EDITORIAL_ERROR_INPUT_STALE,
            )
        run = EditorialRun(
            run_id=repo.new_editorial_run_id(),
            project_id=project.id,
            scope=scope,
            status=EditorialRunStatus.QUEUED,
            brief_id=brief.project_brief_id,
            brief_version=brief.brief_version,
            narrative_plan_id=narrative_id,
            script_id=None if script is None else script.script_id,
            created_at=_now(),
            schema_version=EDITORIAL_SCHEMA_VERSION,
        )
        repo.insert_editorial_run(conn, run)
        if (
            scope == EDITORIAL_RUN_SCOPE_COVERAGE
            and coverage_fingerprint
            and coverage_dedup_key
        ):
            repo.save_coverage_run_dedup_marker(
                project.project_root_path,
                run_id=run.run_id,
                canonical_coverage_input_fingerprint=coverage_fingerprint,
                dedup_key=coverage_dedup_key,
                coverage_scope=EDITORIAL_RUN_SCOPE_COVERAGE,
                execution_mode=coverage_execution_mode,
            )
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        raise EditorialServiceError(str(exc)) from exc
    finally:
        conn.close()
    worker = {
        EDITORIAL_RUN_SCOPE_NARRATIVE: "editorial_narrative",
        EDITORIAL_RUN_SCOPE_SCRIPT: "editorial_script",
        EDITORIAL_RUN_SCOPE_STRUCTURE: "editorial_structure",
        EDITORIAL_RUN_SCOPE_COVERAGE: "editorial_coverage",
    }[scope]
    launched = get_editorial_job_launcher().launch(
        project_id=project.id,
        project_root=project.project_root_path,
        run_id=run.run_id,
        worker=worker,  # type: ignore[arg-type]
        sync=sync,
    )
    if not launched and not sync:
        return EditorialStartResult(
            started=False,
            message="Editorial-Worker konnte nicht gestartet werden (bereits aktiv).",
            run=run,
            error_code=EDITORIAL_ERROR_RUN_ALREADY_ACTIVE,
            canonical_input_fingerprint=coverage_fingerprint,
        )
    if sync:
        conn = repo.open_editorial_registry(project.project_root_path)
        try:
            final = repo.get_editorial_run(conn, run_id=run.run_id) or run
        finally:
            conn.close()
        return EditorialStartResult(
            started=True,
            message="Editorial-Run abgeschlossen.",
            run=final,
            canonical_input_fingerprint=coverage_fingerprint,
        )
    return EditorialStartResult(
        started=True,
        message="Editorial-Run gestartet.",
        run=run,
        canonical_input_fingerprint=coverage_fingerprint,
    )


def _editorial_ready_inputs(project: Project) -> list[EditorialReadyObservationInput]:
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


def _state_with(conn, *, project_id: str, **updates) -> EditorialProjectState:
    existing = repo.get_project_state(conn, project_id=project_id)
    base = existing or EditorialProjectState(
        project_id=project_id,
        updated_at=_now(),
    )
    return base.model_copy(
        update={
            **updates,
            "updated_at": _now(),
            "status": updates.get("status", EditorialProjectStateStatus.ACTIVE),
        }
    )


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = [
    "BriefSaveResult",
    "EditorialServiceError",
    "EditorialStartResult",
    "EditorialView",
    "HookSelectResult",
    "ScriptEditResult",
    "get_active_project_brief",
    "get_editorial_view",
    "list_project_briefs",
    "save_project_brief",
    "save_user_script_edit",
    "select_hook",
    "start_coverage_run",
    "start_narrative_run",
    "start_script_run",
    "start_structure_run",
]
