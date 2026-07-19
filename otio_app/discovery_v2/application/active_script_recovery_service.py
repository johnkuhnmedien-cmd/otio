"""Controlled recovery for missing editorial active_script_id pointers."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from otio_app.discovery_v2.application.inventory_service import (
    InventoryServiceError,
    require_discovery_project,
)
from otio_app.discovery_v2.domain.editorial import (
    EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERED,
    EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERY_AMBIGUOUS,
    EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERY_CANDIDATE_MISSING,
    EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERY_CONFIRMATION_REQUIRED,
    EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERY_COVERAGE_MISMATCH,
    EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERY_FAILED,
    EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERY_IDENTITY_MISMATCH,
    EditorialProjectStateStatus,
    ScriptDraft,
    ScriptDraftStatus,
)
from otio_app.discovery_v2.editorial_paths import (
    resolve_editorial_relative_path,
)
from otio_app.discovery_v2.persistence import editorial_repository as editorial_repo
from otio_app.discovery_v2.persistence.asset_registry_database import RegistryDatabaseError
from otio_app.discovery_v2.persistence.inventory_artifact_store import (
    InventoryArtifactError,
)
from otio_app.models import Project

_ACTIVE_SCRIPT_STATUSES = {
    ScriptDraftStatus.DRAFT.value,
    ScriptDraftStatus.REVIEW_REQUESTED.value,
    ScriptDraftStatus.USER_EDITED.value,
    ScriptDraftStatus.STRUCTURE_PENDING.value,
}


class ActiveScriptRecoveryServiceError(InventoryServiceError):
    """Domain error for active-script pointer recovery."""


@dataclass(frozen=True)
class ActiveScriptRecoveryCandidate:
    script_id: str
    script_version: int
    narrative_plan_id: str
    selected_hook_id: str | None
    status: str
    content_sha256: str


@dataclass(frozen=True)
class ActiveScriptRecoveryDiagnosis:
    ok: bool
    pointer_missing: bool
    candidate: ActiveScriptRecoveryCandidate | None = None
    blockers: list[str] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)
    message: str = ""
    error_code: str | None = None

    @property
    def candidate_script_id(self) -> str | None:
        return None if self.candidate is None else self.candidate.script_id

    @property
    def candidate_script_version(self) -> int | None:
        return None if self.candidate is None else self.candidate.script_version


@dataclass(frozen=True)
class ActiveScriptRecoveryResult:
    ok: bool
    message: str
    script_id: str | None = None
    script_version: int | None = None
    error_code: str | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def diagnose_active_script_recovery(project: Project) -> ActiveScriptRecoveryDiagnosis:
    """Read-only: offer recovery only when exactly one candidate is verified."""

    try:
        project = require_discovery_project(project)
    except InventoryServiceError as exc:
        return ActiveScriptRecoveryDiagnosis(
            ok=False,
            pointer_missing=False,
            blockers=[EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERY_FAILED],
            message=str(exc),
            error_code=EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERY_FAILED,
        )
    try:
        conn = editorial_repo.open_editorial_registry(project.project_root_path)
    except RegistryDatabaseError as exc:
        return ActiveScriptRecoveryDiagnosis(
            ok=False,
            pointer_missing=False,
            blockers=[EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERY_FAILED],
            message=str(exc),
            error_code=EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERY_FAILED,
        )
    try:
        state = editorial_repo.get_project_state(conn, project_id=project.id)
        if state is not None and state.active_script_id:
            return ActiveScriptRecoveryDiagnosis(
                ok=False,
                pointer_missing=False,
                blockers=[],
                diagnostics=["active_script_id already set"],
                message="Aktiver Script-Pointer ist bereits gesetzt.",
            )
        pointer_missing = state is None or not state.active_script_id
        rows = conn.execute(
            """
            SELECT script_id, script_version, project_id, narrative_plan_id,
                   selected_hook_id, status, content_sha256, relative_json_path
            FROM script_drafts
            WHERE project_id = ?
              AND status IN ('draft', 'review_requested', 'user_edited', 'structure_pending')
            ORDER BY script_version DESC, script_id ASC
            """,
            (project.id,),
        ).fetchall()
        if not rows:
            return ActiveScriptRecoveryDiagnosis(
                ok=False,
                pointer_missing=pointer_missing,
                blockers=[EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERY_CANDIDATE_MISSING],
                message="Kein Script-Kandidat in der Registry.",
                error_code=EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERY_CANDIDATE_MISSING,
            )

        verified: list[ActiveScriptRecoveryCandidate] = []
        diagnostics: list[str] = []
        blockers: list[str] = []
        coverage_ref = _active_coverage_script_ref(conn, state=state)

        for row in rows:
            candidate, reason = _verify_script_row(
                conn,
                project_root=project.project_root_path,
                project_id=project.id,
                row=row,
                coverage_ref=coverage_ref,
            )
            if candidate is not None:
                verified.append(candidate)
            else:
                diagnostics.append(f"{row['script_id']}:{reason}")
                if reason and reason not in blockers:
                    blockers.append(reason)

        if coverage_ref is not None:
            # Coverage pointer uniquely selects the script; ignore other rows.
            coverage_matches = [
                item
                for item in verified
                if item.script_id == coverage_ref[0]
                and item.script_version == coverage_ref[1]
            ]
            if not coverage_matches:
                return ActiveScriptRecoveryDiagnosis(
                    ok=False,
                    pointer_missing=pointer_missing,
                    blockers=[EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERY_COVERAGE_MISMATCH],
                    diagnostics=diagnostics,
                    message=(
                        "Aktueller Coverage Audit verweist nicht auf einen "
                        "verifizierten Script-Kandidaten."
                    ),
                    error_code=EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERY_COVERAGE_MISMATCH,
                )
            verified = coverage_matches

        if len(verified) == 0:
            code = (
                blockers[0]
                if blockers
                else EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERY_CANDIDATE_MISSING
            )
            return ActiveScriptRecoveryDiagnosis(
                ok=False,
                pointer_missing=pointer_missing,
                blockers=blockers
                or [EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERY_CANDIDATE_MISSING],
                diagnostics=diagnostics,
                message="Kein eindeutig verifizierter Script-Kandidat.",
                error_code=code,
            )
        if len(verified) > 1:
            return ActiveScriptRecoveryDiagnosis(
                ok=False,
                pointer_missing=pointer_missing,
                blockers=[EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERY_AMBIGUOUS],
                diagnostics=diagnostics
                + [f"candidate:{item.script_id}:v{item.script_version}" for item in verified],
                message="Mehrere gleichwertige Script-Kandidaten — keine Wiederherstellung.",
                error_code=EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERY_AMBIGUOUS,
            )

        only = verified[0]
        return ActiveScriptRecoveryDiagnosis(
            ok=True,
            pointer_missing=pointer_missing,
            candidate=only,
            blockers=[],
            diagnostics=diagnostics,
            message=(
                f"Verifizierter Wiederherstellungskandidat: Script v{only.script_version}."
            ),
            error_code=None,
        )
    finally:
        conn.close()


def recover_active_script_current_state(
    project: Project,
    *,
    script_id: str,
    user_confirmed: bool,
) -> ActiveScriptRecoveryResult:
    """Atomically restore active script/narrative/hook pointers after user confirm."""

    if not user_confirmed:
        return ActiveScriptRecoveryResult(
            ok=False,
            message="Wiederherstellung erfordert eine bewusste Bestaetigung.",
            error_code=EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERY_CONFIRMATION_REQUIRED,
        )
    try:
        project = require_discovery_project(project)
    except InventoryServiceError as exc:
        return ActiveScriptRecoveryResult(
            ok=False,
            message=str(exc),
            error_code=EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERY_FAILED,
        )

    diagnosis = diagnose_active_script_recovery(project)
    if not diagnosis.ok or diagnosis.candidate is None:
        return ActiveScriptRecoveryResult(
            ok=False,
            message=diagnosis.message or "Wiederherstellung nicht moeglich.",
            error_code=diagnosis.error_code
            or EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERY_FAILED,
        )
    if diagnosis.candidate.script_id != script_id:
        return ActiveScriptRecoveryResult(
            ok=False,
            message="Angeforderte Script-ID ist nicht der verifizierte Kandidat.",
            error_code=EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERY_IDENTITY_MISMATCH,
        )

    try:
        conn = editorial_repo.open_editorial_registry(project.project_root_path)
    except RegistryDatabaseError as exc:
        return ActiveScriptRecoveryResult(
            ok=False,
            message=str(exc),
            error_code=EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERY_FAILED,
        )
    try:
        # Re-verify inside the write transaction (fail-closed).
        conn.execute("BEGIN IMMEDIATE")
        state = editorial_repo.get_project_state(conn, project_id=project.id)
        if state is not None and state.active_script_id:
            conn.rollback()
            return ActiveScriptRecoveryResult(
                ok=False,
                message="Aktiver Script-Pointer wurde zwischenzeitlich gesetzt.",
                error_code=EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERY_FAILED,
            )
        row = conn.execute(
            """
            SELECT script_id, script_version, project_id, narrative_plan_id,
                   selected_hook_id, status, content_sha256, relative_json_path
            FROM script_drafts
            WHERE script_id = ? AND project_id = ?
            """,
            (script_id, project.id),
        ).fetchone()
        if row is None:
            conn.rollback()
            return ActiveScriptRecoveryResult(
                ok=False,
                message="Script-Zeile fehlt.",
                error_code=EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERY_CANDIDATE_MISSING,
            )
        coverage_ref = _active_coverage_script_ref(conn, state=state)
        candidate, reason = _verify_script_row(
            conn,
            project_root=project.project_root_path,
            project_id=project.id,
            row=row,
            coverage_ref=coverage_ref,
        )
        if candidate is None:
            conn.rollback()
            return ActiveScriptRecoveryResult(
                ok=False,
                message=reason or "Kandidat nicht mehr verifiziert.",
                error_code=reason
                or EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERY_FAILED,
            )
        if not candidate.narrative_plan_id or not candidate.selected_hook_id:
            conn.rollback()
            return ActiveScriptRecoveryResult(
                ok=False,
                message="Narrative-/Hook-Identitaet unvollstaendig.",
                error_code=EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERY_IDENTITY_MISMATCH,
            )

        from otio_app.discovery_v2.domain.editorial import EditorialProjectState

        if state is None:
            new_state = EditorialProjectState(
                project_id=project.id,
                active_brief_id=None,
                active_narrative_plan_id=candidate.narrative_plan_id,
                selected_hook_id=candidate.selected_hook_id,
                active_script_id=candidate.script_id,
                active_coverage_audit_id=None,
                current_script_lock_id=None,
                observation_fingerprint=None,
                status=EditorialProjectStateStatus.ACTIVE,
                updated_at=_now(),
            )
        else:
            new_state = state.model_copy(
                update={
                    "active_script_id": candidate.script_id,
                    "active_narrative_plan_id": candidate.narrative_plan_id,
                    "selected_hook_id": candidate.selected_hook_id,
                    # Preserve coverage / lock / brief / observation fingerprint.
                    "updated_at": _now(),
                }
            )
        editorial_repo.upsert_project_state(conn, new_state)
        # Align hook selection flag with recovered pointer (no script content rewrite).
        editorial_repo.set_selected_hook(
            conn,
            narrative_plan_id=candidate.narrative_plan_id,
            hook_id=candidate.selected_hook_id,
        )
        conn.commit()
        recovered_id = candidate.script_id
        recovered_version = candidate.script_version
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        return ActiveScriptRecoveryResult(
            ok=False,
            message=str(exc),
            error_code=EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERY_FAILED,
        )
    finally:
        conn.close()

    return ActiveScriptRecoveryResult(
        ok=True,
        message=(
            "Aktueller Script-Zustand wiederhergestellt. "
            "Jetzt „Struktur aktualisieren“ ausführen."
        ),
        script_id=recovered_id,
        script_version=recovered_version,
        error_code=EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERED,
    )


def _active_coverage_script_ref(conn, *, state) -> tuple[str, int] | None:
    """Return (script_id, script_version) from SQLite coverage pointer, if any."""

    if state is None or not state.active_coverage_audit_id:
        return None
    row = conn.execute(
        """
        SELECT script_id, script_version
        FROM coverage_audits
        WHERE coverage_audit_id = ?
        """,
        (state.active_coverage_audit_id,),
    ).fetchone()
    if row is None:
        return None
    return str(row["script_id"]), int(row["script_version"])


def _verify_script_row(
    conn,
    *,
    project_root: Path,
    project_id: str,
    row,
    coverage_ref: tuple[str, int] | None,
) -> tuple[ActiveScriptRecoveryCandidate | None, str | None]:
    """Return a verified candidate or (None, blocker_code)."""

    script_id = str(row["script_id"])
    db_version = int(row["script_version"])
    db_project_id = str(row["project_id"])
    db_narrative = str(row["narrative_plan_id"] or "")
    db_hook = row["selected_hook_id"]
    db_status = str(row["status"])
    db_sha = str(row["content_sha256"] or "")
    relative = str(row["relative_json_path"])

    if db_project_id != project_id:
        return None, EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERY_IDENTITY_MISMATCH
    if db_status not in _ACTIVE_SCRIPT_STATUSES:
        return None, EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERY_CANDIDATE_MISSING
    if not db_narrative:
        return None, EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERY_IDENTITY_MISMATCH

    narrative = editorial_repo.get_narrative_plan(conn, narrative_plan_id=db_narrative)
    if narrative is None:
        return None, EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERY_IDENTITY_MISMATCH
    if not db_hook:
        return None, EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERY_IDENTITY_MISMATCH
    hook = editorial_repo.get_hook_variant(conn, hook_id=str(db_hook))
    if hook is None or hook.narrative_plan_id != db_narrative:
        return None, EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERY_IDENTITY_MISMATCH

    try:
        path = resolve_editorial_relative_path(project_root, relative)
        if not path.is_file():
            return None, EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERY_IDENTITY_MISMATCH
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, InventoryArtifactError, json.JSONDecodeError, FileNotFoundError):
        return None, EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERY_IDENTITY_MISMATCH

    script_payload = payload.get("script") or {}
    try:
        script = ScriptDraft.model_validate(script_payload)
    except Exception:  # noqa: BLE001
        return None, EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERY_IDENTITY_MISMATCH

    if (
        script.script_id != script_id
        or int(script.script_version) != db_version
        or script.project_id != project_id
        or str(script.content_sha256 or "") != db_sha
        or str(script.narrative_plan_id or "") != db_narrative
        or (script.selected_hook_id or None) != (str(db_hook) if db_hook else None)
    ):
        return None, EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERY_IDENTITY_MISMATCH

    if coverage_ref is not None:
        if coverage_ref[0] != script_id or coverage_ref[1] != db_version:
            return None, EDITORIAL_ERROR_ACTIVE_SCRIPT_RECOVERY_COVERAGE_MISMATCH

    return (
        ActiveScriptRecoveryCandidate(
            script_id=script_id,
            script_version=db_version,
            narrative_plan_id=db_narrative,
            selected_hook_id=str(db_hook),
            status=db_status,
            content_sha256=db_sha,
        ),
        None,
    )


__all__ = [
    "ActiveScriptRecoveryCandidate",
    "ActiveScriptRecoveryDiagnosis",
    "ActiveScriptRecoveryResult",
    "ActiveScriptRecoveryServiceError",
    "diagnose_active_script_recovery",
    "recover_active_script_current_state",
]
