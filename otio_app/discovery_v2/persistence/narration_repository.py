"""Persistence for Discovery V2 Phase 11 narration artifacts and registry rows."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from otio_app.discovery_v2.domain.narration import (
    ACTIVE_NARRATION_RUN_STATUSES,
    NARRATION_ERROR_PAUSE_DIRECTION_CONFLICT,
    NARRATION_ERROR_VOICE_SEGMENT_INVALID,
    NarrationAttemptStatus,
    NarrationProjectState,
    NarrationRunStatus,
    NarrationTimebase,
    NarrationTimelineEntry,
    NarrationTimelineEntryType,
    NarrationTimelineStatus,
    PauseDirection,
    PauseDirectionPlan,
    PauseDirectionPlanStatus,
    PauseFunction,
    PauseHardness,
    PausePositionKind,
    PauseUncertainty,
    ResolvedNarrationTimeline,
    VoiceGenerationAttempt,
    VoiceGenerationRun,
    VoiceOutputProfile,
    VoiceProfile,
    VoiceProfileStatus,
    VoiceSegment,
    VoiceSegmentStatus,
    compute_sha256,
)
from otio_app.discovery_v2.narration_paths import (
    assert_narration_relative_path,
    narration_attempt_json_relative_path,
    narration_latest_pause_plan_relative_path,
    narration_latest_timeline_relative_path,
    narration_latest_voice_run_relative_path,
    narration_pause_plan_json_relative_path,
    narration_report_relative_path,
    narration_run_json_relative_path,
    narration_temp_dir,
    narration_timeline_json_relative_path,
    narration_voice_profile_json_relative_path,
    resolve_narration_relative_path,
)
from otio_app.discovery_v2.persistence.asset_registry_database import (
    get_registry_connection,
)
from otio_app.discovery_v2.persistence.editorial_repository import (
    bind_project_root_for_json_reads,
)
from otio_app.discovery_v2.persistence.inventory_artifact_store import (
    InventoryArtifactError,
)

_NARRATION_JSON_ROOT: Path | None = None


def bind_project_root_for_narration_json_reads(project_root: Path) -> None:
    global _NARRATION_JSON_ROOT
    _NARRATION_JSON_ROOT = Path(project_root).expanduser().resolve()


def open_narration_registry(project_root: Path) -> sqlite3.Connection:
    bind_project_root_for_json_reads(project_root)
    bind_project_root_for_narration_json_reads(project_root)
    return get_registry_connection(project_root)


def new_voice_profile_id() -> str:
    return str(uuid4())


def new_voice_run_id() -> str:
    return str(uuid4())


def new_voice_attempt_id() -> str:
    return str(uuid4())


def new_pause_plan_id() -> str:
    return str(uuid4())


def new_pause_direction_id() -> str:
    return str(uuid4())


def new_timeline_id() -> str:
    return str(uuid4())


def new_timeline_entry_id() -> str:
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


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load_json(relative_path: str) -> object:
    if _NARRATION_JSON_ROOT is None:
        raise InventoryArtifactError("Narration JSON root is not bound.")
    path = resolve_narration_relative_path(_NARRATION_JSON_ROOT, relative_path)
    return json.loads(path.read_text(encoding="utf-8"))


def save_json_artifact(project_root: Path, relative_path: str, payload: object) -> str:
    relative = assert_narration_relative_path(relative_path)
    target = resolve_narration_relative_path(project_root, relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    if target.exists():
        if target.read_bytes() != data:
            raise InventoryArtifactError(f"Narration artifact conflict: {relative}")
        return relative
    tmp = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        tmp.write_bytes(data)
        json.loads(tmp.read_text(encoding="utf-8"))
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            tmp.unlink()
    return relative


def save_pointer_json(project_root: Path, relative_path: str, payload: object) -> str:
    relative = assert_narration_relative_path(relative_path)
    target = resolve_narration_relative_path(project_root, relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    tmp = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        tmp.write_bytes(data)
        json.loads(tmp.read_text(encoding="utf-8"))
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            tmp.unlink()
    return relative


def save_voice_profile_json(project_root: Path, profile: VoiceProfile) -> str:
    return save_json_artifact(
        project_root,
        narration_voice_profile_json_relative_path(profile.voice_profile_id),
        profile.model_dump(mode="json"),
    )


def save_voice_run_json(project_root: Path, run: VoiceGenerationRun) -> str:
    return save_json_artifact(
        project_root,
        narration_run_json_relative_path(run.run_id),
        run.model_dump(mode="json"),
    )


def save_voice_attempt_json(project_root: Path, attempt: VoiceGenerationAttempt, payload: object) -> str:
    return save_json_artifact(
        project_root,
        narration_attempt_json_relative_path(attempt.attempt_id),
        {"attempt": attempt.model_dump(mode="json"), "payload": payload},
    )


def save_pause_plan_json(
    project_root: Path,
    plan: PauseDirectionPlan,
    directions: list[PauseDirection],
) -> str:
    return save_json_artifact(
        project_root,
        narration_pause_plan_json_relative_path(plan.pause_plan_id),
        {
            "pause_plan": plan.model_dump(mode="json"),
            "directions": [direction.model_dump(mode="json") for direction in directions],
        },
    )


def save_timeline_json(project_root: Path, timeline: ResolvedNarrationTimeline) -> str:
    return save_json_artifact(
        project_root,
        narration_timeline_json_relative_path(timeline.timeline_id),
        timeline.model_dump(mode="json"),
    )


def save_run_report(project_root: Path, run_id: str, payload: object) -> str:
    return save_pointer_json(project_root, narration_report_relative_path(run_id), payload)


def publish_voice_wav(
    project_root: Path,
    *,
    temp_path: Path,
    relative_path: str,
    expected_sha256: str,
) -> str:
    relative = assert_narration_relative_path(relative_path)
    target = resolve_narration_relative_path(project_root, relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = Path(temp_path).read_bytes()
    if compute_sha256(data) != expected_sha256:
        raise InventoryArtifactError("Narration WAV hash mismatch before publish.")
    if target.exists():
        if target.read_bytes() != data:
            raise InventoryArtifactError(f"Narration artifact conflict: {relative}")
        return relative
    tmp = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        tmp.write_bytes(data)
        if compute_sha256(tmp.read_bytes()) != expected_sha256:
            raise InventoryArtifactError("Narration WAV hash mismatch after temp write.")
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            tmp.unlink()
    return relative


def cleanup_narration_temp(project_root: Path, *, run_id: str) -> None:
    temp = narration_temp_dir(project_root, run_id)
    if temp.exists():
        shutil.rmtree(temp)


def get_project_state(conn: sqlite3.Connection, *, project_id: str) -> NarrationProjectState | None:
    row = conn.execute(
        "SELECT * FROM narration_project_state WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    return None if row is None else _row_to_project_state(row)


def upsert_project_state(conn: sqlite3.Connection, state: NarrationProjectState) -> None:
    conn.execute(
        """
        INSERT INTO narration_project_state (
            project_id, current_voice_profile_id, current_voice_run_id,
            current_pause_plan_id, current_timeline_id, current_script_lock_id,
            updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(project_id) DO UPDATE SET
            current_voice_profile_id = excluded.current_voice_profile_id,
            current_voice_run_id = excluded.current_voice_run_id,
            current_pause_plan_id = excluded.current_pause_plan_id,
            current_timeline_id = excluded.current_timeline_id,
            current_script_lock_id = excluded.current_script_lock_id,
            updated_at = excluded.updated_at
        """,
        (
            state.project_id,
            state.current_voice_profile_id,
            state.current_voice_run_id,
            state.current_pause_plan_id,
            state.current_timeline_id,
            state.current_script_lock_id,
            state.updated_at.isoformat(),
        ),
    )


def insert_voice_profile(conn: sqlite3.Connection, profile: VoiceProfile, relative_json_path: str) -> None:
    assert_narration_relative_path(relative_json_path)
    conn.execute(
        """
        INSERT INTO voice_profiles (
            voice_profile_id, project_id, language, provider, voice_identifier,
            voice_settings_version, output_profile_json, version, adapter_version,
            audio_format, sample_rate, channels, supersedes_voice_profile_id,
            status, relative_json_path, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            profile.voice_profile_id,
            profile.project_id,
            profile.language,
            profile.provider,
            profile.voice_identifier,
            profile.voice_settings_version,
            _json(profile.output_profile.model_dump(mode="json")),
            profile.version,
            profile.adapter_version,
            profile.audio_format,
            profile.sample_rate,
            profile.channels,
            profile.supersedes_voice_profile_id,
            profile.status.value,
            relative_json_path,
            profile.created_at.isoformat(),
        ),
    )


def update_voice_profile_status(
    conn: sqlite3.Connection,
    *,
    voice_profile_id: str,
    status: VoiceProfileStatus,
) -> None:
    conn.execute(
        "UPDATE voice_profiles SET status = ? WHERE voice_profile_id = ?",
        (status.value, voice_profile_id),
    )


def get_voice_profile(conn: sqlite3.Connection, *, voice_profile_id: str) -> VoiceProfile | None:
    row = conn.execute(
        "SELECT * FROM voice_profiles WHERE voice_profile_id = ?",
        (voice_profile_id,),
    ).fetchone()
    return None if row is None else _row_to_voice_profile(row)


def get_active_voice_profile(conn: sqlite3.Connection, *, project_id: str) -> VoiceProfile | None:
    row = conn.execute(
        """
        SELECT * FROM voice_profiles
        WHERE project_id = ? AND status = 'active'
        ORDER BY created_at DESC, voice_profile_id DESC
        LIMIT 1
        """,
        (project_id,),
    ).fetchone()
    return None if row is None else _row_to_voice_profile(row)


def list_voice_profiles(conn: sqlite3.Connection, *, project_id: str) -> list[VoiceProfile]:
    rows = conn.execute(
        "SELECT * FROM voice_profiles WHERE project_id = ? ORDER BY created_at DESC",
        (project_id,),
    ).fetchall()
    return [_row_to_voice_profile(row) for row in rows]


def insert_voice_run(conn: sqlite3.Connection, run: VoiceGenerationRun) -> None:
    conn.execute(
        """
        INSERT INTO voice_generation_runs (
            run_id, project_id, script_lock_id, script_id, voice_profile_id,
            input_fingerprint, provider, adapter_version, scope, status,
            sentence_count, segments_created, segments_reused, segments_failed,
            error_code, error_message, relative_report_path, created_at,
            started_at, finished_at, schema_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        _voice_run_values(run),
    )


def update_voice_run(conn: sqlite3.Connection, run: VoiceGenerationRun) -> None:
    conn.execute(
        """
        UPDATE voice_generation_runs SET
            status = ?, segments_created = ?, segments_reused = ?,
            segments_failed = ?, error_code = ?, error_message = ?,
            relative_report_path = ?, started_at = ?, finished_at = ?,
            schema_version = ?
        WHERE run_id = ?
        """,
        (
            run.status.value,
            run.segments_created,
            run.segments_reused,
            run.segments_failed,
            run.error_code,
            run.error_message,
            run.relative_report_path,
            None if run.started_at is None else run.started_at.isoformat(),
            None if run.finished_at is None else run.finished_at.isoformat(),
            run.schema_version,
            run.run_id,
        ),
    )


def get_voice_run(conn: sqlite3.Connection, *, run_id: str) -> VoiceGenerationRun | None:
    row = conn.execute(
        "SELECT * FROM voice_generation_runs WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    return None if row is None else _row_to_voice_run(row)


def list_voice_runs(conn: sqlite3.Connection, *, project_id: str) -> list[VoiceGenerationRun]:
    rows = conn.execute(
        """
        SELECT * FROM voice_generation_runs
        WHERE project_id = ?
        ORDER BY created_at DESC, run_id DESC
        """,
        (project_id,),
    ).fetchall()
    return [_row_to_voice_run(row) for row in rows]


def find_active_narration_run(
    conn: sqlite3.Connection,
    *,
    project_id: str,
) -> VoiceGenerationRun | None:
    row = conn.execute(
        """
        SELECT * FROM voice_generation_runs
        WHERE project_id = ? AND status IN (?, ?)
        ORDER BY created_at DESC, run_id DESC
        LIMIT 1
        """,
        (
            project_id,
            NarrationRunStatus.QUEUED.value,
            NarrationRunStatus.RUNNING.value,
        ),
    ).fetchone()
    return None if row is None else _row_to_voice_run(row)


def insert_voice_attempt(conn: sqlite3.Connection, attempt: VoiceGenerationAttempt) -> None:
    conn.execute(
        """
        INSERT INTO voice_generation_attempts (
            attempt_id, run_id, project_id, scope, sentence_id, segment_id,
            cache_key, provider, adapter_version, input_fingerprint, status,
            relative_json_path, error_code, error_message, created_at, completed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        _attempt_values(attempt),
    )


def update_voice_attempt(conn: sqlite3.Connection, attempt: VoiceGenerationAttempt) -> None:
    conn.execute(
        """
        UPDATE voice_generation_attempts SET
            segment_id = ?, status = ?, relative_json_path = ?, error_code = ?,
            error_message = ?, completed_at = ?
        WHERE attempt_id = ?
        """,
        (
            attempt.segment_id,
            attempt.status.value,
            attempt.relative_json_path,
            attempt.error_code,
            attempt.error_message,
            None if attempt.completed_at is None else attempt.completed_at.isoformat(),
            attempt.attempt_id,
        ),
    )


def list_voice_attempts(conn: sqlite3.Connection, *, run_id: str) -> list[VoiceGenerationAttempt]:
    rows = conn.execute(
        "SELECT * FROM voice_generation_attempts WHERE run_id = ? ORDER BY created_at, attempt_id",
        (run_id,),
    ).fetchall()
    return [_row_to_attempt(row) for row in rows]


def insert_voice_segment(conn: sqlite3.Connection, segment: VoiceSegment) -> None:
    row = conn.execute(
        "SELECT script_id FROM voice_generation_runs WHERE run_id = ?",
        (segment.run_id,),
    ).fetchone()
    if row is None or str(row["script_id"]) != segment.script_id:
        raise ValueError(NARRATION_ERROR_VOICE_SEGMENT_INVALID)
    conn.execute(
        """
        INSERT OR IGNORE INTO voice_segments (
            segment_id, run_id, script_lock_id, script_id, sentence_id,
            sentence_ordinal, text_hash, voice_profile_id, provider, voice_identifier,
            voice_settings_version, adapter_version, audio_format, sample_rate_hz,
            channels, sample_count, duration_seconds, byte_size, audio_sha256,
            relative_path, status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        _segment_values(segment),
    )


def get_voice_segment(conn: sqlite3.Connection, *, segment_id: str) -> VoiceSegment | None:
    row = conn.execute(
        "SELECT * FROM voice_segments WHERE segment_id = ?",
        (segment_id,),
    ).fetchone()
    return None if row is None else _row_to_segment(row)


def find_cached_voice_segment(
    conn: sqlite3.Connection,
    *,
    segment_id: str,
) -> VoiceSegment | None:
    row = conn.execute(
        """
        SELECT * FROM voice_segments
        WHERE segment_id = ? AND status = 'published'
        LIMIT 1
        """,
        (segment_id,),
    ).fetchone()
    return None if row is None else _row_to_segment(row)


def list_voice_segments_for_run(
    conn: sqlite3.Connection,
    *,
    run_id: str,
) -> list[VoiceSegment]:
    rows = conn.execute(
        """
        SELECT * FROM voice_segments
        WHERE run_id = ? OR segment_id IN (
            SELECT segment_id FROM voice_generation_attempts
            WHERE run_id = ? AND segment_id IS NOT NULL
        )
        ORDER BY sentence_ordinal, segment_id
        """,
        (run_id, run_id),
    ).fetchall()
    seen: set[str] = set()
    segments: list[VoiceSegment] = []
    for row in rows:
        segment = _row_to_segment(row)
        if segment.segment_id not in seen:
            segments.append(segment)
            seen.add(segment.segment_id)
    return segments


def list_voice_segments_for_lock(
    conn: sqlite3.Connection,
    *,
    script_lock_id: str,
) -> list[VoiceSegment]:
    rows = conn.execute(
        """
        SELECT * FROM voice_segments
        WHERE script_lock_id = ? AND status = 'published'
        ORDER BY sentence_ordinal, segment_id
        """,
        (script_lock_id,),
    ).fetchall()
    return [_row_to_segment(row) for row in rows]


def insert_pause_plan(
    conn: sqlite3.Connection,
    plan: PauseDirectionPlan,
    directions: list[PauseDirection],
    relative_json_path: str,
) -> None:
    assert_narration_relative_path(relative_json_path)
    conn.execute(
        """
        INSERT INTO pause_direction_plans (
            pause_plan_id, project_id, script_lock_id, voice_run_id,
            prompt_version, model_identifier, gateway_version,
            response_schema_version, provider, input_fingerprint,
            global_notes_json, status, relative_json_path, created_at, schema_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            plan.pause_plan_id,
            plan.project_id,
            plan.script_lock_id,
            plan.voice_run_id,
            plan.prompt_version,
            plan.model_identifier,
            plan.gateway_version,
            plan.response_schema_version,
            plan.provider,
            plan.input_fingerprint,
            _json(plan.global_notes),
            plan.status.value,
            relative_json_path,
            plan.created_at.isoformat(),
            plan.schema_version,
        ),
    )
    for direction in directions:
        insert_pause_direction(conn, direction)


def insert_pause_direction(conn: sqlite3.Connection, direction: PauseDirection) -> None:
    row = conn.execute(
        """
        SELECT direction_id FROM pause_directions
        WHERE pause_plan_id = ? AND ordinal = ?
        """,
        (direction.pause_plan_id, direction.ordinal),
    ).fetchone()
    if row is not None and str(row["direction_id"]) != direction.direction_id:
        raise ValueError(NARRATION_ERROR_PAUSE_DIRECTION_CONFLICT)
    conn.execute(
        """
        INSERT INTO pause_directions (
            direction_id, pause_plan_id, ordinal, position_kind, sentence_id,
            segment_id, anchor_ordinal, function, min_duration_intent_s,
            preferred_duration_intent_s, max_duration_intent_s, hardness,
            rationale, uncertainty
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            direction.direction_id,
            direction.pause_plan_id,
            direction.ordinal,
            direction.position_kind.value,
            direction.sentence_id,
            direction.segment_id,
            direction.anchor_ordinal,
            direction.function.value,
            direction.min_duration_intent_s,
            direction.preferred_duration_intent_s,
            direction.max_duration_intent_s,
            direction.hardness.value,
            direction.rationale,
            direction.uncertainty.value,
        ),
    )


def get_pause_plan(conn: sqlite3.Connection, *, pause_plan_id: str) -> PauseDirectionPlan | None:
    row = conn.execute(
        "SELECT * FROM pause_direction_plans WHERE pause_plan_id = ?",
        (pause_plan_id,),
    ).fetchone()
    return None if row is None else _row_to_pause_plan(row)


def list_pause_plans(conn: sqlite3.Connection, *, project_id: str) -> list[PauseDirectionPlan]:
    rows = conn.execute(
        """
        SELECT * FROM pause_direction_plans
        WHERE project_id = ?
        ORDER BY created_at DESC, pause_plan_id DESC
        """,
        (project_id,),
    ).fetchall()
    return [_row_to_pause_plan(row) for row in rows]


def list_pause_directions(conn: sqlite3.Connection, *, pause_plan_id: str) -> list[PauseDirection]:
    rows = conn.execute(
        """
        SELECT * FROM pause_directions
        WHERE pause_plan_id = ?
        ORDER BY ordinal, direction_id
        """,
        (pause_plan_id,),
    ).fetchall()
    return [_row_to_pause_direction(row) for row in rows]


def insert_timeline(
    conn: sqlite3.Connection,
    timeline: ResolvedNarrationTimeline,
    relative_json_path: str,
) -> None:
    assert_narration_relative_path(relative_json_path)
    conn.execute(
        """
        INSERT INTO narration_timelines (
            timeline_id, project_id, script_lock_id, voice_run_id, pause_plan_id,
            timing_profile_version, fps_numerator, fps_denominator, fps,
            total_duration_seconds, total_frames, input_fingerprint, status,
            relative_json_path, created_at, schema_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            timeline.timeline_id,
            timeline.project_id,
            timeline.script_lock_id,
            timeline.voice_run_id,
            timeline.pause_plan_id,
            timeline.timing_profile_version,
            timeline.timebase.fps_numerator,
            timeline.timebase.fps_denominator,
            timeline.timebase.fps,
            timeline.total_duration_seconds,
            timeline.total_frames,
            timeline.input_fingerprint,
            timeline.status.value,
            relative_json_path,
            timeline.created_at.isoformat(),
            timeline.schema_version,
        ),
    )
    for entry in timeline.entries:
        conn.execute(
            """
            INSERT INTO narration_timeline_entries (
                timeline_id, entry_id, ordinal, entry_type, sentence_id,
                voice_segment_id, pause_direction_id, start_seconds, end_seconds,
                duration_seconds, start_frame, end_frame, function,
                technical_notes_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                timeline.timeline_id,
                entry.entry_id,
                entry.ordinal,
                entry.entry_type.value,
                entry.sentence_id,
                entry.voice_segment_id,
                entry.pause_direction_id,
                entry.start_seconds,
                entry.end_seconds,
                entry.duration_seconds,
                entry.start_frame,
                entry.end_frame,
                entry.function,
                _json(entry.technical_notes),
            ),
        )


def get_timeline(conn: sqlite3.Connection, *, timeline_id: str) -> ResolvedNarrationTimeline | None:
    row = conn.execute(
        "SELECT * FROM narration_timelines WHERE timeline_id = ?",
        (timeline_id,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_timeline(row, list_timeline_entries(conn, timeline_id=timeline_id))


def list_timelines(conn: sqlite3.Connection, *, project_id: str) -> list[ResolvedNarrationTimeline]:
    rows = conn.execute(
        """
        SELECT * FROM narration_timelines
        WHERE project_id = ?
        ORDER BY created_at DESC, timeline_id DESC
        """,
        (project_id,),
    ).fetchall()
    return [
        _row_to_timeline(row, list_timeline_entries(conn, timeline_id=str(row["timeline_id"])))
        for row in rows
    ]


def list_timeline_entries(
    conn: sqlite3.Connection,
    *,
    timeline_id: str,
) -> list[NarrationTimelineEntry]:
    rows = conn.execute(
        """
        SELECT * FROM narration_timeline_entries
        WHERE timeline_id = ?
        ORDER BY ordinal
        """,
        (timeline_id,),
    ).fetchall()
    return [_row_to_timeline_entry(row) for row in rows]


def mark_current_voice_run(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    script_lock_id: str,
    voice_profile_id: str,
    voice_run_id: str,
) -> None:
    state = get_project_state(conn, project_id=project_id) or NarrationProjectState(
        project_id=project_id,
        updated_at=_now(),
    )
    upsert_project_state(
        conn,
        state.model_copy(
            update={
                "current_voice_profile_id": voice_profile_id,
                "current_voice_run_id": voice_run_id,
                "current_pause_plan_id": None,
                "current_timeline_id": None,
                "current_script_lock_id": script_lock_id,
                "updated_at": _now(),
            }
        ),
    )


def mark_current_pause_plan(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    script_lock_id: str,
    pause_plan_id: str,
) -> None:
    state = get_project_state(conn, project_id=project_id) or NarrationProjectState(
        project_id=project_id,
        updated_at=_now(),
    )
    upsert_project_state(
        conn,
        state.model_copy(
            update={
                "current_pause_plan_id": pause_plan_id,
                "current_timeline_id": None,
                "current_script_lock_id": script_lock_id,
                "updated_at": _now(),
            }
        ),
    )


def mark_current_timeline(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    script_lock_id: str,
    timeline_id: str,
) -> None:
    state = get_project_state(conn, project_id=project_id) or NarrationProjectState(
        project_id=project_id,
        updated_at=_now(),
    )
    upsert_project_state(
        conn,
        state.model_copy(
            update={
                "current_timeline_id": timeline_id,
                "current_script_lock_id": script_lock_id,
                "updated_at": _now(),
            }
        ),
    )


def write_latest_voice_pointer(project_root: Path, run: VoiceGenerationRun) -> str:
    return save_pointer_json(
        project_root,
        narration_latest_voice_run_relative_path(),
        {"run_id": run.run_id, "status": run.status.value},
    )


def write_latest_pause_pointer(project_root: Path, plan: PauseDirectionPlan) -> str:
    return save_pointer_json(
        project_root,
        narration_latest_pause_plan_relative_path(),
        {"pause_plan_id": plan.pause_plan_id, "status": plan.status.value},
    )


def write_latest_timeline_pointer(project_root: Path, timeline: ResolvedNarrationTimeline) -> str:
    return save_pointer_json(
        project_root,
        narration_latest_timeline_relative_path(),
        {"timeline_id": timeline.timeline_id, "status": timeline.status.value},
    )


def _row_to_project_state(row: sqlite3.Row) -> NarrationProjectState:
    return NarrationProjectState(
        project_id=str(row["project_id"]),
        current_voice_profile_id=row["current_voice_profile_id"],
        current_voice_run_id=row["current_voice_run_id"],
        current_pause_plan_id=row["current_pause_plan_id"],
        current_timeline_id=row["current_timeline_id"],
        current_script_lock_id=row["current_script_lock_id"],
        updated_at=_parse_dt(row["updated_at"]) or _now(),
    )


def _row_to_voice_profile(row: sqlite3.Row) -> VoiceProfile:
    return VoiceProfile(
        voice_profile_id=str(row["voice_profile_id"]),
        project_id=str(row["project_id"]),
        language=str(row["language"]),
        provider=str(row["provider"]),
        voice_identifier=str(row["voice_identifier"]),
        voice_settings_version=str(row["voice_settings_version"]),
        output_profile=VoiceOutputProfile.model_validate(json.loads(row["output_profile_json"])),
        version=int(row["version"]),
        adapter_version=str(row["adapter_version"]),
        audio_format=str(row["audio_format"]),
        sample_rate=int(row["sample_rate"]),
        channels=int(row["channels"]),
        supersedes_voice_profile_id=row["supersedes_voice_profile_id"],
        status=VoiceProfileStatus(str(row["status"])),
        created_at=_parse_dt(row["created_at"]) or _now(),
    )


def _voice_run_values(run: VoiceGenerationRun) -> tuple[object, ...]:
    return (
        run.run_id,
        run.project_id,
        run.script_lock_id,
        run.script_id,
        run.voice_profile_id,
        run.input_fingerprint,
        run.provider,
        run.adapter_version,
        run.scope,
        run.status.value,
        run.sentence_count,
        run.segments_created,
        run.segments_reused,
        run.segments_failed,
        run.error_code,
        run.error_message,
        run.relative_report_path,
        run.created_at.isoformat(),
        None if run.started_at is None else run.started_at.isoformat(),
        None if run.finished_at is None else run.finished_at.isoformat(),
        run.schema_version,
    )


def _row_to_voice_run(row: sqlite3.Row) -> VoiceGenerationRun:
    return VoiceGenerationRun(
        run_id=str(row["run_id"]),
        project_id=str(row["project_id"]),
        script_lock_id=str(row["script_lock_id"]),
        script_id=str(row["script_id"]),
        voice_profile_id=str(row["voice_profile_id"]),
        input_fingerprint=str(row["input_fingerprint"]),
        provider=str(row["provider"]),
        adapter_version=str(row["adapter_version"]),
        scope=str(row["scope"]),
        status=NarrationRunStatus(str(row["status"])),
        sentence_count=int(row["sentence_count"]),
        segments_created=int(row["segments_created"]),
        segments_reused=int(row["segments_reused"]),
        segments_failed=int(row["segments_failed"]),
        error_code=row["error_code"],
        error_message=row["error_message"],
        relative_report_path=row["relative_report_path"],
        created_at=_parse_dt(row["created_at"]) or _now(),
        started_at=_parse_dt(row["started_at"]),
        finished_at=_parse_dt(row["finished_at"]),
        schema_version=str(row["schema_version"]),
    )


def _attempt_values(attempt: VoiceGenerationAttempt) -> tuple[object, ...]:
    return (
        attempt.attempt_id,
        attempt.run_id,
        attempt.project_id,
        attempt.scope,
        attempt.sentence_id,
        attempt.segment_id,
        attempt.cache_key,
        attempt.provider,
        attempt.adapter_version,
        attempt.input_fingerprint,
        attempt.status.value,
        attempt.relative_json_path,
        attempt.error_code,
        attempt.error_message,
        attempt.created_at.isoformat(),
        None if attempt.completed_at is None else attempt.completed_at.isoformat(),
    )


def _row_to_attempt(row: sqlite3.Row) -> VoiceGenerationAttempt:
    return VoiceGenerationAttempt(
        attempt_id=str(row["attempt_id"]),
        run_id=str(row["run_id"]),
        project_id=str(row["project_id"]),
        scope=str(row["scope"]),
        sentence_id=row["sentence_id"],
        segment_id=row["segment_id"],
        cache_key=row["cache_key"],
        provider=str(row["provider"]),
        adapter_version=row["adapter_version"],
        input_fingerprint=row["input_fingerprint"],
        status=NarrationAttemptStatus(str(row["status"])),
        relative_json_path=row["relative_json_path"],
        error_code=row["error_code"],
        error_message=row["error_message"],
        created_at=_parse_dt(row["created_at"]) or _now(),
        completed_at=_parse_dt(row["completed_at"]),
    )


def _segment_values(segment: VoiceSegment) -> tuple[object, ...]:
    return (
        segment.segment_id,
        segment.run_id,
        segment.script_lock_id,
        segment.script_id,
        segment.sentence_id,
        segment.sentence_ordinal,
        segment.text_hash,
        segment.voice_profile_id,
        segment.provider,
        segment.voice_identifier,
        segment.voice_settings_version,
        segment.adapter_version,
        segment.audio_format,
        segment.sample_rate_hz,
        segment.channels,
        segment.sample_count,
        segment.duration_seconds,
        segment.byte_size,
        segment.audio_sha256,
        segment.relative_path,
        segment.status.value,
        segment.created_at.isoformat(),
    )


def _row_to_segment(row: sqlite3.Row) -> VoiceSegment:
    return VoiceSegment(
        segment_id=str(row["segment_id"]),
        run_id=str(row["run_id"]),
        script_lock_id=str(row["script_lock_id"]),
        script_id=str(row["script_id"]),
        sentence_id=str(row["sentence_id"]),
        sentence_ordinal=int(row["sentence_ordinal"]),
        text_hash=str(row["text_hash"]),
        voice_profile_id=str(row["voice_profile_id"]),
        provider=str(row["provider"]),
        voice_identifier=str(row["voice_identifier"]),
        voice_settings_version=str(row["voice_settings_version"]),
        adapter_version=str(row["adapter_version"]),
        audio_format=str(row["audio_format"]),
        sample_rate_hz=int(row["sample_rate_hz"]),
        channels=int(row["channels"]),
        sample_count=int(row["sample_count"]),
        duration_seconds=float(row["duration_seconds"]),
        byte_size=int(row["byte_size"]),
        audio_sha256=str(row["audio_sha256"]),
        relative_path=str(row["relative_path"]),
        status=VoiceSegmentStatus(str(row["status"])),
        created_at=_parse_dt(row["created_at"]) or _now(),
    )


def _row_to_pause_plan(row: sqlite3.Row) -> PauseDirectionPlan:
    return PauseDirectionPlan(
        pause_plan_id=str(row["pause_plan_id"]),
        project_id=str(row["project_id"]),
        script_lock_id=str(row["script_lock_id"]),
        voice_run_id=str(row["voice_run_id"]),
        prompt_version=str(row["prompt_version"]),
        model_identifier=str(row["model_identifier"]),
        gateway_version=str(row["gateway_version"]),
        response_schema_version=str(row["response_schema_version"]),
        provider=str(row["provider"]),
        input_fingerprint=str(row["input_fingerprint"]),
        global_notes=list(json.loads(row["global_notes_json"])),
        status=PauseDirectionPlanStatus(str(row["status"])),
        created_at=_parse_dt(row["created_at"]) or _now(),
        schema_version=str(row["schema_version"]),
    )


def _row_to_pause_direction(row: sqlite3.Row) -> PauseDirection:
    return PauseDirection(
        direction_id=str(row["direction_id"]),
        pause_plan_id=str(row["pause_plan_id"]),
        ordinal=int(row["ordinal"]),
        position_kind=PausePositionKind(str(row["position_kind"])),
        sentence_id=row["sentence_id"],
        segment_id=row["segment_id"],
        anchor_ordinal=None if row["anchor_ordinal"] is None else int(row["anchor_ordinal"]),
        function=PauseFunction(str(row["function"])),
        min_duration_intent_s=float(row["min_duration_intent_s"]),
        preferred_duration_intent_s=float(row["preferred_duration_intent_s"]),
        max_duration_intent_s=float(row["max_duration_intent_s"]),
        hardness=PauseHardness(str(row["hardness"])),
        rationale=str(row["rationale"]),
        uncertainty=PauseUncertainty(str(row["uncertainty"])),
    )


def _row_to_timeline(
    row: sqlite3.Row,
    entries: list[NarrationTimelineEntry],
) -> ResolvedNarrationTimeline:
    return ResolvedNarrationTimeline(
        timeline_id=str(row["timeline_id"]),
        project_id=str(row["project_id"]),
        script_lock_id=str(row["script_lock_id"]),
        voice_run_id=str(row["voice_run_id"]),
        pause_plan_id=str(row["pause_plan_id"]),
        timing_profile_version=str(row["timing_profile_version"]),
        timebase=NarrationTimebase(
            fps_numerator=int(row["fps_numerator"]),
            fps_denominator=int(row["fps_denominator"]),
            fps=float(row["fps"]),
        ),
        total_duration_seconds=float(row["total_duration_seconds"]),
        total_frames=int(row["total_frames"]),
        entries=entries,
        input_fingerprint=str(row["input_fingerprint"]),
        status=NarrationTimelineStatus(str(row["status"])),
        created_at=_parse_dt(row["created_at"]) or _now(),
        schema_version=str(row["schema_version"]),
    )


def _row_to_timeline_entry(row: sqlite3.Row) -> NarrationTimelineEntry:
    return NarrationTimelineEntry(
        entry_id=str(row["entry_id"]),
        ordinal=int(row["ordinal"]),
        entry_type=NarrationTimelineEntryType(str(row["entry_type"])),
        sentence_id=row["sentence_id"],
        voice_segment_id=row["voice_segment_id"],
        pause_direction_id=row["pause_direction_id"],
        start_seconds=float(row["start_seconds"]),
        end_seconds=float(row["end_seconds"]),
        duration_seconds=float(row["duration_seconds"]),
        start_frame=int(row["start_frame"]),
        end_frame=int(row["end_frame"]),
        function=str(row["function"]),
        technical_notes=list(json.loads(row["technical_notes_json"])),
    )


__all__ = [name for name in globals() if not name.startswith("_")]
