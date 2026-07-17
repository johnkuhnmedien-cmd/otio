"""Persistence for Discovery V2 editorial artifacts and registry rows."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from otio_app.discovery_v2.domain.editorial import (
    ACTIVE_EDITORIAL_RUN_STATUSES,
    EDITORIAL_SCHEMA_VERSION,
    CoverageAudit,
    CoverageAuditStatus,
    CoverageIntentResult,
    EditorialAttempt,
    EditorialAttemptStatus,
    EditorialProjectState,
    EditorialProjectStateStatus,
    EditorialRun,
    EditorialRunStatus,
    HookVariant,
    NarrativePlan,
    NarrativePlanStatus,
    ProjectBrief,
    ProjectBriefStatus,
    ScriptDraft,
    ScriptDraftStatus,
    Sentence,
    VisualBeat,
    VisualIntent,
)
from otio_app.discovery_v2.editorial_paths import (
    assert_editorial_relative_path,
    editorial_attempt_json_relative_path,
    editorial_brief_json_relative_path,
    editorial_coverage_json_relative_path,
    editorial_hook_json_relative_path,
    editorial_latest_brief_relative_path,
    editorial_latest_coverage_relative_path,
    editorial_latest_narrative_relative_path,
    editorial_latest_script_relative_path,
    editorial_narrative_json_relative_path,
    editorial_run_json_relative_path,
    editorial_script_json_relative_path,
    editorial_temp_dir,
    resolve_editorial_relative_path,
)
from otio_app.discovery_v2.persistence.asset_registry_database import (
    get_registry_connection,
)
from otio_app.discovery_v2.persistence.inventory_artifact_store import (
    InventoryArtifactError,
)


def open_editorial_registry(project_root: Path) -> sqlite3.Connection:
    bind_project_root_for_json_reads(project_root)
    return get_registry_connection(project_root)


def new_editorial_run_id() -> str:
    return str(uuid4())


def new_editorial_attempt_id() -> str:
    return str(uuid4())


def new_project_brief_id() -> str:
    return str(uuid4())


def new_narrative_plan_id() -> str:
    return str(uuid4())


def new_hook_id() -> str:
    return str(uuid4())


def new_script_id() -> str:
    return str(uuid4())


def new_sentence_id() -> str:
    return str(uuid4())


def new_claim_id() -> str:
    return str(uuid4())


def new_visual_beat_id() -> str:
    return str(uuid4())


def new_visual_intent_id() -> str:
    return str(uuid4())


def new_coverage_audit_id() -> str:
    return str(uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: object | None) -> datetime | None:
    if value is None:
        return None
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)


def insert_editorial_run(conn: sqlite3.Connection, run: EditorialRun) -> None:
    conn.execute(
        """
        INSERT INTO editorial_runs (
            run_id, project_id, scope, status, brief_id, brief_version,
            narrative_plan_id, script_id, error_code, error_message,
            relative_report_path, created_at, started_at, finished_at, schema_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        _run_values(run),
    )


def update_editorial_run(conn: sqlite3.Connection, run: EditorialRun) -> None:
    conn.execute(
        """
        UPDATE editorial_runs SET
            scope = ?, status = ?, brief_id = ?, brief_version = ?,
            narrative_plan_id = ?, script_id = ?, error_code = ?, error_message = ?,
            relative_report_path = ?, started_at = ?, finished_at = ?,
            schema_version = ?
        WHERE run_id = ?
        """,
        (
            run.scope,
            run.status.value,
            run.brief_id,
            run.brief_version,
            run.narrative_plan_id,
            run.script_id,
            run.error_code,
            run.error_message,
            run.relative_report_path,
            None if run.started_at is None else run.started_at.isoformat(),
            None if run.finished_at is None else run.finished_at.isoformat(),
            run.schema_version,
            run.run_id,
        ),
    )


def get_editorial_run(conn: sqlite3.Connection, *, run_id: str) -> EditorialRun | None:
    row = conn.execute(
        "SELECT * FROM editorial_runs WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    return None if row is None else _row_to_run(row)


def list_editorial_runs(conn: sqlite3.Connection, *, project_id: str) -> list[EditorialRun]:
    rows = conn.execute(
        """
        SELECT * FROM editorial_runs
        WHERE project_id = ?
        ORDER BY created_at DESC, run_id DESC
        """,
        (project_id,),
    ).fetchall()
    return [_row_to_run(row) for row in rows]


def find_active_editorial_run(
    conn: sqlite3.Connection, *, project_id: str
) -> EditorialRun | None:
    rows = conn.execute(
        """
        SELECT * FROM editorial_runs
        WHERE project_id = ?
          AND status IN (?, ?)
        ORDER BY created_at DESC, run_id DESC
        LIMIT 1
        """,
        (
            project_id,
            EditorialRunStatus.QUEUED.value,
            EditorialRunStatus.RUNNING.value,
        ),
    ).fetchall()
    return None if not rows else _row_to_run(rows[0])


def insert_editorial_attempt(conn: sqlite3.Connection, attempt: EditorialAttempt) -> None:
    conn.execute(
        """
        INSERT INTO editorial_attempts (
            attempt_id, run_id, project_id, request_kind, provider, model_identifier,
            gateway_version, prompt_version, response_schema_version,
            input_fingerprint, status, relative_json_path, error_code, error_message,
            created_at, completed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        _attempt_values(attempt),
    )


def update_editorial_attempt(conn: sqlite3.Connection, attempt: EditorialAttempt) -> None:
    conn.execute(
        """
        UPDATE editorial_attempts SET
            status = ?, relative_json_path = ?, error_code = ?, error_message = ?,
            completed_at = ?
        WHERE attempt_id = ?
        """,
        (
            attempt.status.value,
            attempt.relative_json_path,
            attempt.error_code,
            attempt.error_message,
            None if attempt.completed_at is None else attempt.completed_at.isoformat(),
            attempt.attempt_id,
        ),
    )


def find_completed_editorial_attempt(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    request_kind: str,
    provider: str,
    model_identifier: str,
    gateway_version: str,
    prompt_version: str,
    response_schema_version: str,
    input_fingerprint: str,
) -> EditorialAttempt | None:
    row = conn.execute(
        """
        SELECT *
        FROM editorial_attempts
        WHERE project_id = ?
          AND request_kind = ?
          AND provider = ?
          AND model_identifier = ?
          AND gateway_version = ?
          AND prompt_version = ?
          AND response_schema_version = ?
          AND input_fingerprint = ?
          AND status = 'completed'
        ORDER BY completed_at DESC, created_at DESC
        LIMIT 1
        """,
        (
            project_id,
            request_kind,
            provider,
            model_identifier,
            gateway_version,
            prompt_version,
            response_schema_version,
            input_fingerprint,
        ),
    ).fetchone()
    return None if row is None else _row_to_attempt(row)


def list_editorial_attempts(
    conn: sqlite3.Connection,
    *,
    run_id: str | None = None,
    project_id: str | None = None,
) -> list[EditorialAttempt]:
    sql = "SELECT * FROM editorial_attempts"
    params: list[object] = []
    if run_id is not None:
        sql += " WHERE run_id = ?"
        params.append(run_id)
    elif project_id is not None:
        sql += " WHERE project_id = ?"
        params.append(project_id)
    sql += " ORDER BY created_at, attempt_id"
    return [_row_to_attempt(row) for row in conn.execute(sql, params).fetchall()]


def next_brief_version(conn: sqlite3.Connection, *, project_id: str) -> int:
    row = conn.execute(
        "SELECT MAX(brief_version) AS max_version FROM project_briefs WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    return int(row["max_version"] or 0) + 1


def insert_project_brief(conn: sqlite3.Connection, brief: ProjectBrief, relative_json_path: str) -> None:
    assert_editorial_relative_path(relative_json_path)
    conn.execute(
        """
        INSERT INTO project_briefs (
            project_brief_id, project_id, language, topic, target_audience,
            desired_duration_seconds, tone, geographic_frame, brief_version,
            content_sha256, status, relative_json_path, created_at, supersedes_brief_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            brief.project_brief_id,
            brief.project_id,
            brief.language,
            brief.topic,
            brief.target_audience,
            brief.desired_duration_seconds,
            brief.tone,
            brief.geographic_frame,
            brief.brief_version,
            brief.content_sha256,
            brief.status.value,
            relative_json_path,
            brief.created_at.isoformat(),
            brief.supersedes_brief_id,
        ),
    )


def update_project_brief_status(
    conn: sqlite3.Connection, *, project_brief_id: str, status: ProjectBriefStatus
) -> None:
    conn.execute(
        "UPDATE project_briefs SET status = ? WHERE project_brief_id = ?",
        (status.value, project_brief_id),
    )


def get_project_brief(conn: sqlite3.Connection, *, project_brief_id: str) -> ProjectBrief | None:
    row = conn.execute(
        "SELECT * FROM project_briefs WHERE project_brief_id = ?",
        (project_brief_id,),
    ).fetchone()
    return None if row is None else _row_to_brief(row)


def get_active_project_brief(conn: sqlite3.Connection, *, project_id: str) -> ProjectBrief | None:
    row = conn.execute(
        """
        SELECT * FROM project_briefs
        WHERE project_id = ? AND status = 'active'
        ORDER BY brief_version DESC
        LIMIT 1
        """,
        (project_id,),
    ).fetchone()
    return None if row is None else _row_to_brief(row)


def list_project_briefs(conn: sqlite3.Connection, *, project_id: str) -> list[ProjectBrief]:
    rows = conn.execute(
        """
        SELECT * FROM project_briefs
        WHERE project_id = ?
        ORDER BY brief_version DESC
        """,
        (project_id,),
    ).fetchall()
    return [_row_to_brief(row) for row in rows]


def insert_narrative_plan(conn: sqlite3.Connection, plan: NarrativePlan, relative_json_path: str) -> None:
    assert_editorial_relative_path(relative_json_path)
    conn.execute(
        """
        INSERT INTO narrative_plans (
            narrative_plan_id, project_id, project_brief_id, brief_version, status,
            input_observation_fingerprint, provider, model_identifier, gateway_version,
            prompt_version, response_schema_version, relative_json_path, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            plan.narrative_plan_id,
            plan.project_id,
            plan.project_brief_id,
            plan.brief_version,
            plan.status.value,
            plan.input_observation_fingerprint,
            plan.provider,
            plan.model_identifier,
            plan.gateway_version,
            plan.prompt_version,
            plan.schema_version,
            relative_json_path,
            plan.created_at.isoformat(),
        ),
    )


def update_narrative_plan_status(
    conn: sqlite3.Connection, *, narrative_plan_id: str, status: NarrativePlanStatus
) -> None:
    conn.execute(
        "UPDATE narrative_plans SET status = ? WHERE narrative_plan_id = ?",
        (status.value, narrative_plan_id),
    )


def get_narrative_plan(conn: sqlite3.Connection, *, narrative_plan_id: str) -> NarrativePlan | None:
    row = conn.execute(
        "SELECT relative_json_path FROM narrative_plans WHERE narrative_plan_id = ?",
        (narrative_plan_id,),
    ).fetchone()
    if row is None:
        return None
    payload = _read_json_from_relative(row["relative_json_path"])
    return NarrativePlan.model_validate(payload["narrative_plan"])


def get_active_narrative_plan(conn: sqlite3.Connection, *, project_id: str) -> NarrativePlan | None:
    row = conn.execute(
        """
        SELECT relative_json_path FROM narrative_plans
        WHERE project_id = ? AND status = 'active'
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (project_id,),
    ).fetchone()
    if row is None:
        return None
    payload = _read_json_from_relative(row["relative_json_path"])
    return NarrativePlan.model_validate(payload["narrative_plan"])


def insert_hook_variant(conn: sqlite3.Connection, hook: HookVariant, relative_json_path: str) -> None:
    assert_editorial_relative_path(relative_json_path)
    conn.execute(
        """
        INSERT INTO hook_variants (
            hook_id, narrative_plan_id, hook_text, hook_type, intended_effect,
            user_status, relative_json_path, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            hook.hook_id,
            hook.narrative_plan_id,
            hook.hook_text,
            hook.hook_type,
            hook.intended_effect,
            hook.user_status.value,
            relative_json_path,
            hook.created_at.isoformat(),
        ),
    )


def get_hook_variant(conn: sqlite3.Connection, *, hook_id: str) -> HookVariant | None:
    row = conn.execute(
        "SELECT relative_json_path FROM hook_variants WHERE hook_id = ?",
        (hook_id,),
    ).fetchone()
    if row is None:
        return None
    return _read_model_json(conn, row["relative_json_path"], HookVariant)


def list_hook_variants(conn: sqlite3.Connection, *, narrative_plan_id: str) -> list[HookVariant]:
    rows = conn.execute(
        """
        SELECT relative_json_path FROM hook_variants
        WHERE narrative_plan_id = ?
        ORDER BY created_at, hook_id
        """,
        (narrative_plan_id,),
    ).fetchall()
    return [_read_model_json(conn, row["relative_json_path"], HookVariant) for row in rows]


def set_selected_hook(conn: sqlite3.Connection, *, narrative_plan_id: str, hook_id: str) -> None:
    conn.execute(
        """
        UPDATE hook_variants
        SET user_status = CASE WHEN hook_id = ? THEN 'selected' ELSE 'proposed' END
        WHERE narrative_plan_id = ?
        """,
        (hook_id, narrative_plan_id),
    )


def next_script_version(conn: sqlite3.Connection, *, project_id: str) -> int:
    row = conn.execute(
        "SELECT MAX(script_version) AS max_version FROM script_drafts WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    return int(row["max_version"] or 0) + 1


def insert_script_bundle(
    conn: sqlite3.Connection,
    *,
    script: ScriptDraft,
    sentences: list[Sentence],
    claims: list,
    visual_beats: list[VisualBeat],
    visual_intents: list[VisualIntent],
    relative_json_path: str,
) -> None:
    assert_editorial_relative_path(relative_json_path)
    conn.execute(
        """
        INSERT INTO script_drafts (
            script_id, script_version, project_id, language, narrative_plan_id,
            selected_hook_id, project_brief_id, brief_version, status, source_kind,
            supersedes_script_id, content_sha256, provider, model_identifier,
            gateway_version, prompt_version, response_schema_version,
            relative_json_path, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            script.script_id,
            script.script_version,
            script.project_id,
            script.language,
            script.narrative_plan_id,
            script.selected_hook_id,
            script.project_brief_id,
            script.brief_version,
            script.status.value,
            script.source_kind.value,
            script.supersedes_script_id,
            script.content_sha256,
            script.provider,
            script.model_identifier,
            script.gateway_version,
            script.prompt_version,
            script.schema_version,
            relative_json_path,
            script.created_at.isoformat(),
        ),
    )
    for sentence in sentences:
        conn.execute(
            """
            INSERT INTO script_sentences (
                sentence_id, script_id, ordinal, text, narrative_function,
                claim_ids_json, visual_beat_ids_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sentence.sentence_id,
                sentence.script_id,
                sentence.ordinal,
                sentence.text,
                sentence.narrative_function,
                _json(sentence.claim_ids),
                _json(sentence.visual_beat_ids),
            ),
        )
    for claim in claims:
        conn.execute(
            """
            INSERT INTO script_claims (
                claim_id, script_id, statement, claim_type, confidence,
                evidence_refs_json, user_note, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                claim.claim_id,
                claim.script_id,
                claim.statement,
                claim.claim_type,
                claim.confidence,
                _json([ref.model_dump(mode="json") for ref in claim.evidence_refs]),
                claim.user_note,
                claim.status.value,
            ),
        )
    for beat in visual_beats:
        conn.execute(
            """
            INSERT INTO visual_beats (
                visual_beat_id, script_id, function, description, rhythm_function,
                continuity_requirements_json, intended_duration_hint_seconds
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                beat.visual_beat_id,
                beat.script_id,
                beat.function,
                beat.description,
                beat.rhythm_function,
                _json(beat.continuity_requirements),
                beat.intended_duration_hint_seconds,
            ),
        )
        for sentence_id in beat.sentence_ids:
            conn.execute(
                """
                INSERT INTO visual_beat_sentences (visual_beat_id, sentence_id)
                VALUES (?, ?)
                """,
                (beat.visual_beat_id, sentence_id),
            )
    for intent in visual_intents:
        conn.execute(
            """
            INSERT INTO visual_intents (
                visual_intent_id, visual_beat_id, desired_motif, action, setting,
                geographic_requirements, authenticity_requirements_json,
                allowed_media_kinds_json, priority
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                intent.visual_intent_id,
                intent.visual_beat_id,
                intent.desired_motif,
                intent.action,
                intent.setting,
                intent.geographic_requirements,
                _json(intent.authenticity_requirements),
                _json(intent.allowed_media_kinds),
                intent.priority,
            ),
        )


def replace_script_structure(
    conn: sqlite3.Connection,
    *,
    script: ScriptDraft,
    sentences: list[Sentence],
    claims: list,
    visual_beats: list[VisualBeat],
    visual_intents: list[VisualIntent],
    relative_json_path: str,
) -> None:
    assert_editorial_relative_path(relative_json_path)
    old_beats = [
        str(row["visual_beat_id"])
        for row in conn.execute(
            "SELECT visual_beat_id FROM visual_beats WHERE script_id = ?",
            (script.script_id,),
        ).fetchall()
    ]
    old_sentences = [
        str(row["sentence_id"])
        for row in conn.execute(
            "SELECT sentence_id FROM script_sentences WHERE script_id = ?",
            (script.script_id,),
        ).fetchall()
    ]
    if old_beats:
        conn.execute(
            f"DELETE FROM visual_intents WHERE visual_beat_id IN ({','.join('?' for _ in old_beats)})",
            old_beats,
        )
        conn.execute(
            f"DELETE FROM visual_beat_sentences WHERE visual_beat_id IN ({','.join('?' for _ in old_beats)})",
            old_beats,
        )
    if old_sentences:
        conn.execute(
            f"DELETE FROM visual_beat_sentences WHERE sentence_id IN ({','.join('?' for _ in old_sentences)})",
            old_sentences,
        )
    conn.execute("DELETE FROM visual_beats WHERE script_id = ?", (script.script_id,))
    conn.execute("DELETE FROM script_claims WHERE script_id = ?", (script.script_id,))
    conn.execute("DELETE FROM script_sentences WHERE script_id = ?", (script.script_id,))
    conn.execute(
        """
        UPDATE script_drafts SET
            status = ?, content_sha256 = ?, provider = ?, model_identifier = ?,
            gateway_version = ?, prompt_version = ?, response_schema_version = ?,
            relative_json_path = ?
        WHERE script_id = ?
        """,
        (
            script.status.value,
            script.content_sha256,
            script.provider,
            script.model_identifier,
            script.gateway_version,
            script.prompt_version,
            script.schema_version,
            relative_json_path,
            script.script_id,
        ),
    )
    # Reuse insert row logic for dependent structures without touching script_drafts.
    for sentence in sentences:
        conn.execute(
            """
            INSERT INTO script_sentences (
                sentence_id, script_id, ordinal, text, narrative_function,
                claim_ids_json, visual_beat_ids_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sentence.sentence_id,
                sentence.script_id,
                sentence.ordinal,
                sentence.text,
                sentence.narrative_function,
                _json(sentence.claim_ids),
                _json(sentence.visual_beat_ids),
            ),
        )
    for claim in claims:
        conn.execute(
            """
            INSERT INTO script_claims (
                claim_id, script_id, statement, claim_type, confidence,
                evidence_refs_json, user_note, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                claim.claim_id,
                claim.script_id,
                claim.statement,
                claim.claim_type,
                claim.confidence,
                _json([ref.model_dump(mode="json") for ref in claim.evidence_refs]),
                claim.user_note,
                claim.status.value,
            ),
        )
    for beat in visual_beats:
        conn.execute(
            """
            INSERT INTO visual_beats (
                visual_beat_id, script_id, function, description, rhythm_function,
                continuity_requirements_json, intended_duration_hint_seconds
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                beat.visual_beat_id,
                beat.script_id,
                beat.function,
                beat.description,
                beat.rhythm_function,
                _json(beat.continuity_requirements),
                beat.intended_duration_hint_seconds,
            ),
        )
        for sentence_id in beat.sentence_ids:
            conn.execute(
                "INSERT INTO visual_beat_sentences (visual_beat_id, sentence_id) VALUES (?, ?)",
                (beat.visual_beat_id, sentence_id),
            )
    for intent in visual_intents:
        conn.execute(
            """
            INSERT INTO visual_intents (
                visual_intent_id, visual_beat_id, desired_motif, action, setting,
                geographic_requirements, authenticity_requirements_json,
                allowed_media_kinds_json, priority
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                intent.visual_intent_id,
                intent.visual_beat_id,
                intent.desired_motif,
                intent.action,
                intent.setting,
                intent.geographic_requirements,
                _json(intent.authenticity_requirements),
                _json(intent.allowed_media_kinds),
                intent.priority,
            ),
        )


def update_script_status(conn: sqlite3.Connection, *, script_id: str, status: ScriptDraftStatus) -> None:
    conn.execute(
        "UPDATE script_drafts SET status = ? WHERE script_id = ?",
        (status.value, script_id),
    )


def get_script_draft(conn: sqlite3.Connection, *, script_id: str) -> ScriptDraft | None:
    row = conn.execute(
        "SELECT relative_json_path FROM script_drafts WHERE script_id = ?",
        (script_id,),
    ).fetchone()
    if row is None:
        return None
    payload = _read_json_from_relative(row["relative_json_path"])
    return ScriptDraft.model_validate(payload["script"])


def get_active_script(conn: sqlite3.Connection, *, project_id: str) -> ScriptDraft | None:
    row = conn.execute(
        """
        SELECT relative_json_path FROM script_drafts
        WHERE project_id = ? AND status IN ('draft', 'review_requested', 'user_edited')
        ORDER BY script_version DESC
        LIMIT 1
        """,
        (project_id,),
    ).fetchone()
    if row is None:
        return None
    payload = _read_json_from_relative(row["relative_json_path"])
    return ScriptDraft.model_validate(payload["script"])


def list_script_drafts(conn: sqlite3.Connection, *, project_id: str) -> list[ScriptDraft]:
    rows = conn.execute(
        """
        SELECT relative_json_path FROM script_drafts
        WHERE project_id = ?
        ORDER BY script_version DESC
        """,
        (project_id,),
    ).fetchall()
    result = []
    for row in rows:
        payload = _read_json_from_relative(row["relative_json_path"])
        result.append(ScriptDraft.model_validate(payload["script"]))
    return result


def get_script_bundle(conn: sqlite3.Connection, *, script_id: str) -> dict | None:
    row = conn.execute(
        "SELECT relative_json_path FROM script_drafts WHERE script_id = ?",
        (script_id,),
    ).fetchone()
    if row is None:
        return None
    return _read_json_from_relative(row["relative_json_path"])


def insert_coverage_audit(conn: sqlite3.Connection, audit: CoverageAudit, relative_json_path: str) -> None:
    assert_editorial_relative_path(relative_json_path)
    conn.execute(
        """
        INSERT INTO coverage_audits (
            coverage_audit_id, project_id, script_id, script_version, brief_version,
            narrative_plan_id, input_observation_fingerprint, status, provider,
            model_identifier, gateway_version, prompt_version, response_schema_version,
            relative_json_path, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            audit.coverage_audit_id,
            audit.project_id,
            audit.script_id,
            audit.script_version,
            audit.brief_version,
            audit.narrative_plan_id,
            audit.input_observation_fingerprint,
            audit.status.value,
            audit.provider,
            audit.model_identifier,
            audit.gateway_version,
            audit.prompt_version,
            audit.schema_version,
            relative_json_path,
            audit.created_at.isoformat(),
        ),
    )
    for result in audit.results:
        conn.execute(
            """
            INSERT INTO coverage_intent_results (
                coverage_audit_id, visual_intent_id, coverage_status,
                candidate_asset_ids_json, accepted_observation_ids_json, rationale,
                confidence, missing_properties_json, recommended_next_action
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                audit.coverage_audit_id,
                result.visual_intent_id,
                result.coverage_status.value,
                _json(result.candidate_asset_ids),
                _json(result.accepted_observation_ids),
                result.rationale,
                result.confidence,
                _json(result.missing_properties),
                result.recommended_next_action,
            ),
        )


def get_coverage_audit(conn: sqlite3.Connection, *, coverage_audit_id: str) -> CoverageAudit | None:
    row = conn.execute(
        "SELECT relative_json_path FROM coverage_audits WHERE coverage_audit_id = ?",
        (coverage_audit_id,),
    ).fetchone()
    if row is None:
        return None
    payload = _read_json_from_relative(row["relative_json_path"])
    return CoverageAudit.model_validate(payload["coverage_audit"])


def get_latest_coverage_audit(conn: sqlite3.Connection, *, project_id: str) -> CoverageAudit | None:
    row = conn.execute(
        """
        SELECT relative_json_path FROM coverage_audits
        WHERE project_id = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (project_id,),
    ).fetchone()
    if row is None:
        return None
    payload = _read_json_from_relative(row["relative_json_path"])
    return CoverageAudit.model_validate(payload["coverage_audit"])


def mark_editorial_derivatives_stale(conn: sqlite3.Connection, *, project_id: str) -> None:
    conn.execute(
        """
        UPDATE narrative_plans SET status = 'stale'
        WHERE project_id = ? AND status = 'active'
        """,
        (project_id,),
    )
    conn.execute(
        """
        UPDATE coverage_audits SET status = 'stale'
        WHERE project_id = ? AND status = 'completed'
        """,
        (project_id,),
    )


def mark_coverage_stale_for_script(conn: sqlite3.Connection, *, script_id: str) -> None:
    conn.execute(
        """
        UPDATE coverage_audits SET status = 'stale'
        WHERE script_id = ? AND status = 'completed'
        """,
        (script_id,),
    )


def get_project_state(conn: sqlite3.Connection, *, project_id: str) -> EditorialProjectState | None:
    row = conn.execute(
        "SELECT * FROM editorial_project_state WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    return None if row is None else _row_to_state(row)


def upsert_project_state(conn: sqlite3.Connection, state: EditorialProjectState) -> None:
    conn.execute(
        """
        INSERT INTO editorial_project_state (
            project_id, active_brief_id, active_narrative_plan_id, selected_hook_id,
            active_script_id, active_coverage_audit_id, observation_fingerprint,
            status, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(project_id) DO UPDATE SET
            active_brief_id = excluded.active_brief_id,
            active_narrative_plan_id = excluded.active_narrative_plan_id,
            selected_hook_id = excluded.selected_hook_id,
            active_script_id = excluded.active_script_id,
            active_coverage_audit_id = excluded.active_coverage_audit_id,
            observation_fingerprint = excluded.observation_fingerprint,
            status = excluded.status,
            updated_at = excluded.updated_at
        """,
        (
            state.project_id,
            state.active_brief_id,
            state.active_narrative_plan_id,
            state.selected_hook_id,
            state.active_script_id,
            state.active_coverage_audit_id,
            state.observation_fingerprint,
            state.status.value,
            state.updated_at.isoformat(),
        ),
    )


def save_project_brief_json(project_root: Path, brief: ProjectBrief) -> str:
    return _save_json(
        project_root,
        editorial_brief_json_relative_path(brief.project_brief_id),
        brief.model_dump(mode="json"),
        latest_relative=editorial_latest_brief_relative_path(),
    )


def save_narrative_json(project_root: Path, plan: NarrativePlan, hooks: list[HookVariant]) -> str:
    relative = _save_json(
        project_root,
        editorial_narrative_json_relative_path(plan.narrative_plan_id),
        {"narrative_plan": plan.model_dump(mode="json"), "hooks": [h.model_dump(mode="json") for h in hooks]},
        latest_relative=editorial_latest_narrative_relative_path(),
    )
    for hook in hooks:
        _save_json(
            project_root,
            editorial_hook_json_relative_path(plan.narrative_plan_id, hook.hook_id),
            hook.model_dump(mode="json"),
        )
    return relative


def save_hook_json(project_root: Path, hook: HookVariant) -> str:
    return _save_json(
        project_root,
        editorial_hook_json_relative_path(hook.narrative_plan_id, hook.hook_id),
        hook.model_dump(mode="json"),
    )


def save_script_bundle_json(
    project_root: Path,
    *,
    script: ScriptDraft,
    sentences: list[Sentence],
    claims: list,
    visual_beats: list[VisualBeat],
    visual_intents: list[VisualIntent],
) -> str:
    return _save_json(
        project_root,
        editorial_script_json_relative_path(script.script_id),
        {
            "script": script.model_dump(mode="json"),
            "sentences": [item.model_dump(mode="json") for item in sentences],
            "claims": [item.model_dump(mode="json") for item in claims],
            "visual_beats": [item.model_dump(mode="json") for item in visual_beats],
            "visual_intents": [item.model_dump(mode="json") for item in visual_intents],
        },
        latest_relative=editorial_latest_script_relative_path(),
    )


def save_coverage_json(project_root: Path, audit: CoverageAudit) -> str:
    return _save_json(
        project_root,
        editorial_coverage_json_relative_path(audit.coverage_audit_id),
        {"coverage_audit": audit.model_dump(mode="json")},
        latest_relative=editorial_latest_coverage_relative_path(),
    )


def save_editorial_run_report(project_root: Path, run: EditorialRun, payload: dict) -> str:
    relative = editorial_run_json_relative_path(run.run_id)
    return _save_json(project_root, relative, payload)


def save_editorial_attempt_json(project_root: Path, attempt: EditorialAttempt, payload: dict) -> str:
    relative = editorial_attempt_json_relative_path(attempt.attempt_id)
    return _save_json(project_root, relative, payload)


def cleanup_editorial_temp(project_root: Path, *, run_id: str) -> None:
    temp_dir = editorial_temp_dir(project_root, run_id)
    if not temp_dir.exists():
        return
    for child in sorted(temp_dir.rglob("*"), reverse=True):
        try:
            if child.is_file() or child.is_symlink():
                child.unlink(missing_ok=True)
            elif child.is_dir():
                child.rmdir()
        except OSError:
            pass
    try:
        temp_dir.rmdir()
    except OSError:
        pass


def editorial_table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type='table' AND name IN (
            'editorial_project_state', 'editorial_runs', 'editorial_attempts',
            'project_briefs', 'narrative_plans', 'hook_variants',
            'script_drafts', 'script_sentences', 'script_claims',
            'visual_beats', 'visual_beat_sentences', 'visual_intents',
            'coverage_audits', 'coverage_intent_results'
        )
        """
    ).fetchall()
    return {str(row[0]) for row in rows}


def _save_json(
    project_root: Path,
    relative_path: str,
    payload: dict,
    *,
    latest_relative: str | None = None,
) -> str:
    assert_editorial_relative_path(relative_path)
    _assert_no_absolute_paths(payload)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    path = resolve_editorial_relative_path(project_root, relative_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
        if latest_relative is not None:
            latest = resolve_editorial_relative_path(project_root, latest_relative)
            latest.parent.mkdir(parents=True, exist_ok=True)
            latest_tmp = latest.with_suffix(latest.suffix + ".tmp")
            latest_tmp.write_text(text, encoding="utf-8")
            latest_tmp.replace(latest)
    except OSError as exc:
        raise InventoryArtifactError(
            f"Editorial JSON could not be written: {exc}"
        ) from exc
    return relative_path


def _read_json_from_relative(relative_path: str) -> dict:
    # Tests and services call this only for paths previously resolved and persisted.
    # The project root is intentionally stored in a per-connection temp table below.
    root = _CURRENT_PROJECT_ROOT
    if root is None:
        raise InventoryArtifactError("Editorial project root is not bound for JSON read.")
    path = resolve_editorial_relative_path(root, relative_path)
    return json.loads(path.read_text(encoding="utf-8"))


def bind_project_root_for_json_reads(project_root: Path) -> None:
    global _CURRENT_PROJECT_ROOT
    _CURRENT_PROJECT_ROOT = Path(project_root).expanduser().resolve()


_CURRENT_PROJECT_ROOT: Path | None = None


def _read_model_json(conn: sqlite3.Connection, relative_path: str, model_type):
    del conn
    return model_type.model_validate(_read_json_from_relative(relative_path))


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _assert_no_absolute_paths(node: object) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(value, str) and (
                value.startswith("/")
                or (len(value) > 2 and value[1] == ":" and value[2] in "\\/")
            ):
                if "path" in str(key).lower() or value.startswith(("/", "\\")):
                    raise ValueError(f"Absolute paths in Editorial JSON are forbidden: {key}={value}")
            _assert_no_absolute_paths(value)
    elif isinstance(node, list):
        for item in node:
            _assert_no_absolute_paths(item)


def _run_values(run: EditorialRun) -> tuple[object, ...]:
    return (
        run.run_id,
        run.project_id,
        run.scope,
        run.status.value,
        run.brief_id,
        run.brief_version,
        run.narrative_plan_id,
        run.script_id,
        run.error_code,
        run.error_message,
        run.relative_report_path,
        run.created_at.isoformat(),
        None if run.started_at is None else run.started_at.isoformat(),
        None if run.finished_at is None else run.finished_at.isoformat(),
        run.schema_version,
    )


def _attempt_values(attempt: EditorialAttempt) -> tuple[object, ...]:
    return (
        attempt.attempt_id,
        attempt.run_id,
        attempt.project_id,
        attempt.request_kind,
        attempt.provider,
        attempt.model_identifier,
        attempt.gateway_version,
        attempt.prompt_version,
        attempt.response_schema_version,
        attempt.input_fingerprint,
        attempt.status.value,
        attempt.relative_json_path,
        attempt.error_code,
        attempt.error_message,
        attempt.created_at.isoformat(),
        None if attempt.completed_at is None else attempt.completed_at.isoformat(),
    )


def _row_to_run(row: sqlite3.Row) -> EditorialRun:
    return EditorialRun(
        schema_version=str(row["schema_version"] or EDITORIAL_SCHEMA_VERSION),
        run_id=str(row["run_id"]),
        project_id=str(row["project_id"]),
        scope=str(row["scope"]),
        status=EditorialRunStatus(str(row["status"])),
        brief_id=row["brief_id"],
        brief_version=None if row["brief_version"] is None else int(row["brief_version"]),
        narrative_plan_id=row["narrative_plan_id"],
        script_id=row["script_id"],
        error_code=row["error_code"],
        error_message=row["error_message"],
        relative_report_path=row["relative_report_path"],
        created_at=_parse_dt(row["created_at"]) or _now(),
        started_at=_parse_dt(row["started_at"]),
        finished_at=_parse_dt(row["finished_at"]),
    )


def _row_to_attempt(row: sqlite3.Row) -> EditorialAttempt:
    return EditorialAttempt(
        attempt_id=str(row["attempt_id"]),
        run_id=str(row["run_id"]),
        project_id=str(row["project_id"]),
        request_kind=str(row["request_kind"]),
        provider=str(row["provider"]),
        model_identifier=str(row["model_identifier"]),
        gateway_version=str(row["gateway_version"]),
        prompt_version=str(row["prompt_version"]),
        response_schema_version=str(row["response_schema_version"]),
        input_fingerprint=str(row["input_fingerprint"]),
        status=EditorialAttemptStatus(str(row["status"])),
        relative_json_path=row["relative_json_path"],
        error_code=row["error_code"],
        error_message=row["error_message"],
        created_at=_parse_dt(row["created_at"]) or _now(),
        completed_at=_parse_dt(row["completed_at"]),
    )


def _row_to_brief(row: sqlite3.Row) -> ProjectBrief:
    keys = set(row.keys())
    if "relative_json_path" in keys and _CURRENT_PROJECT_ROOT is not None:
        try:
            brief = ProjectBrief.model_validate(_read_json_from_relative(row["relative_json_path"]))
            return brief.model_copy(update={"status": ProjectBriefStatus(str(row["status"]))})
        except Exception:
            pass
    return ProjectBrief(
        project_brief_id=str(row["project_brief_id"]),
        project_id=str(row["project_id"]),
        language=str(row["language"]),
        topic=str(row["topic"]),
        target_audience=str(row["target_audience"]),
        desired_duration_seconds=(
            None
            if row["desired_duration_seconds"] is None
            else int(row["desired_duration_seconds"])
        ),
        tone=str(row["tone"]),
        geographic_frame=row["geographic_frame"],
        must_include=[],
        must_exclude=[],
        user_notes=None,
        brief_version=int(row["brief_version"]),
        content_sha256=str(row["content_sha256"]),
        status=ProjectBriefStatus(str(row["status"])),
        created_at=_parse_dt(row["created_at"]) or _now(),
        supersedes_brief_id=row["supersedes_brief_id"],
    )


def _row_to_state(row: sqlite3.Row) -> EditorialProjectState:
    return EditorialProjectState(
        project_id=str(row["project_id"]),
        active_brief_id=row["active_brief_id"],
        active_narrative_plan_id=row["active_narrative_plan_id"],
        selected_hook_id=row["selected_hook_id"],
        active_script_id=row["active_script_id"],
        active_coverage_audit_id=row["active_coverage_audit_id"],
        observation_fingerprint=row["observation_fingerprint"],
        status=EditorialProjectStateStatus(str(row["status"])),
        updated_at=_parse_dt(row["updated_at"]) or _now(),
    )


__all__ = [
    "ACTIVE_EDITORIAL_RUN_STATUSES",
    "bind_project_root_for_json_reads",
    "cleanup_editorial_temp",
    "editorial_table_names",
    "find_active_editorial_run",
    "find_completed_editorial_attempt",
    "get_active_narrative_plan",
    "get_active_project_brief",
    "get_active_script",
    "get_coverage_audit",
    "get_editorial_run",
    "get_hook_variant",
    "get_latest_coverage_audit",
    "get_narrative_plan",
    "get_project_brief",
    "get_project_state",
    "get_script_bundle",
    "get_script_draft",
    "insert_coverage_audit",
    "insert_editorial_attempt",
    "insert_editorial_run",
    "insert_hook_variant",
    "insert_narrative_plan",
    "insert_project_brief",
    "insert_script_bundle",
    "list_editorial_attempts",
    "list_editorial_runs",
    "list_hook_variants",
    "list_project_briefs",
    "list_script_drafts",
    "mark_coverage_stale_for_script",
    "mark_editorial_derivatives_stale",
    "new_coverage_audit_id",
    "new_editorial_attempt_id",
    "new_editorial_run_id",
    "new_hook_id",
    "new_narrative_plan_id",
    "new_project_brief_id",
    "new_script_id",
    "new_sentence_id",
    "new_visual_beat_id",
    "new_visual_intent_id",
    "next_brief_version",
    "next_script_version",
    "open_editorial_registry",
    "replace_script_structure",
    "save_coverage_json",
    "save_editorial_attempt_json",
    "save_editorial_run_report",
    "save_hook_json",
    "save_narrative_json",
    "save_project_brief_json",
    "save_script_bundle_json",
    "set_selected_hook",
    "update_editorial_attempt",
    "update_editorial_run",
    "update_narrative_plan_status",
    "update_project_brief_status",
    "update_script_status",
    "upsert_project_state",
]
