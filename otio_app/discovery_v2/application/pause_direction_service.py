"""Application service for Discovery V2 Phase 11 pause direction planning."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from otio_app.discovery_v2.adapters.narration_job_launcher import get_narration_job_launcher
from otio_app.discovery_v2.adapters.text_config import load_text_config
from otio_app.discovery_v2.adapters.text_gateway import DiscoveryTextGateway, TextGatewayError
from otio_app.discovery_v2.application.inventory_service import require_discovery_project
from otio_app.discovery_v2.application.voice_generation_service import (
    NarrationServiceError,
    NarrationStartResult,
    _active_blocker,
    require_effective_lock_for_narration,
)
from otio_app.discovery_v2.domain.editorial import TextGatewayRequest
from otio_app.discovery_v2.domain.narration import (
    NARRATION_ERROR_INPUT_STALE,
    NARRATION_ERROR_PAUSE_GATEWAY_UNCONFIGURED,
    NARRATION_ERROR_PAUSE_RESPONSE_INVALID,
    NARRATION_ERROR_RUN_ALREADY_ACTIVE,
    NARRATION_ERROR_VOICE_SEGMENT_MISSING,
    NARRATION_RUN_SCOPE_PAUSE,
    PROMPT_VERSION_PAUSE_DIRECTION,
    RESPONSE_SCHEMA_PAUSE_DIRECTION,
    TEXT_REQUEST_KIND_PAUSE_DIRECTION,
    NarrationRunStatus,
    PauseDirectionPlanStatus,
    VoiceGenerationRun,
    pause_plan_input_fingerprint,
)
from otio_app.discovery_v2.persistence import narration_repository as repo
from otio_app.models import Project


@dataclass(frozen=True)
class PauseDirectionResult:
    ok: bool
    message: str
    run: VoiceGenerationRun | None = None
    pause_plan_id: str | None = None
    error_code: str | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def start_pause_direction_run(project: Project, *, sync: bool = False) -> NarrationStartResult:
    project = require_discovery_project(project)
    try:
        lock_input = require_effective_lock_for_narration(project)
    except NarrationServiceError as exc:
        return NarrationStartResult(False, str(exc), error_code=exc.code)
    conn = repo.open_narration_registry(project.project_root_path)
    try:
        blocked = _active_blocker(conn, project_id=project.id)
        if blocked is not None:
            code, message = blocked
            return NarrationStartResult(False, message, error_code=code)
        state = repo.get_project_state(conn, project_id=project.id)
        if state is None or state.current_voice_run_id is None:
            return NarrationStartResult(
                False,
                "Completed Voice-Run fehlt.",
                error_code=NARRATION_ERROR_INPUT_STALE,
            )
        voice_run = repo.get_voice_run(conn, run_id=state.current_voice_run_id)
        if voice_run is None or voice_run.status != NarrationRunStatus.COMPLETED:
            return NarrationStartResult(
                False,
                "Completed Voice-Run fehlt.",
                error_code=NARRATION_ERROR_INPUT_STALE,
            )
        # L3: historical voice for another lock is never current for Pause.
        if voice_run.script_lock_id != lock_input.lock.lock_id:
            from otio_app.discovery_v2.domain.script_lock_current_state import (
                NARRATION_VOICE_NOT_CURRENT,
            )

            return NarrationStartResult(
                False,
                "Voice-Run gehoert nicht zum wirksamen Script Lock.",
                error_code=NARRATION_VOICE_NOT_CURRENT,
            )
        segments = repo.list_voice_segments_for_run(conn, run_id=voice_run.run_id)
        if len(segments) < len(lock_input.sentences):
            return NarrationStartResult(
                False,
                "Voice-Segmente fehlen.",
                error_code=NARRATION_ERROR_VOICE_SEGMENT_MISSING,
            )
        config = load_text_config()
        fingerprint = pause_plan_input_fingerprint(
            script_lock_id=lock_input.lock.lock_id,
            voice_run_id=voice_run.run_id,
            segments=segments,
            gateway_version=config.gateway_version,
            provider=config.provider,
            model_identifier=config.model_identifier,
        )
        run = VoiceGenerationRun(
            run_id=repo.new_voice_run_id(),
            project_id=project.id,
            script_lock_id=lock_input.lock.lock_id,
            script_id=lock_input.script.script_id,
            voice_profile_id=voice_run.voice_profile_id,
            input_fingerprint=fingerprint,
            provider=config.provider,
            adapter_version=config.gateway_version,
            scope=NARRATION_RUN_SCOPE_PAUSE,
            status=NarrationRunStatus.QUEUED,
            sentence_count=len(lock_input.sentences),
            created_at=_now(),
        )
        repo.insert_voice_run(conn, run)
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        raise NarrationServiceError(str(exc)) from exc
    finally:
        conn.close()
    launched = get_narration_job_launcher().launch(
        project_id=project.id,
        project_root=project.project_root_path,
        run_id=run.run_id,
        worker="narration_pause",
        sync=sync,
    )
    if not launched and not sync:
        return NarrationStartResult(
            False,
            "Narration-Worker konnte nicht gestartet werden (bereits aktiv).",
            run=run,
            error_code=NARRATION_ERROR_RUN_ALREADY_ACTIVE,
        )
    if sync:
        conn = repo.open_narration_registry(project.project_root_path)
        try:
            final = repo.get_voice_run(conn, run_id=run.run_id) or run
        finally:
            conn.close()
        return NarrationStartResult(True, "Pausenregie abgeschlossen.", run=final)
    return NarrationStartResult(True, "Pausenregie gestartet.", run=run)


def process_pause_direction_run(project_root: Path, run_id: str) -> None:
    root = Path(project_root).expanduser().resolve()
    conn = repo.open_narration_registry(root)
    try:
        run = repo.get_voice_run(conn, run_id=run_id)
        if run is None:
            return
        run = run.model_copy(
            update={"status": NarrationRunStatus.RUNNING, "started_at": run.started_at or _now()}
        )
        repo.update_voice_run(conn, run)
        conn.commit()
        project = _project_stub(root, run.project_id)
        lock_input = require_effective_lock_for_narration(project)
        state = repo.get_project_state(conn, project_id=run.project_id)
        voice_run = (
            None
            if state is None or state.current_voice_run_id is None
            else repo.get_voice_run(conn, run_id=state.current_voice_run_id)
        )
        if voice_run is None or voice_run.status != NarrationRunStatus.COMPLETED:
            raise NarrationServiceError(NARRATION_ERROR_INPUT_STALE)
        segments = repo.list_voice_segments_for_run(conn, run_id=voice_run.run_id)
        segment_by_sentence = {segment.sentence_id: segment for segment in segments}
        config = load_text_config()
        request = TextGatewayRequest(
            project_id=run.project_id,
            run_id=voice_run.run_id,
            request_kind=TEXT_REQUEST_KIND_PAUSE_DIRECTION,
            prompt="Plan pause directions for locked narration. Do not return frames.",
            provider=config.provider,
            model_identifier=config.model_identifier,
            gateway_version=config.gateway_version,
            prompt_version=PROMPT_VERSION_PAUSE_DIRECTION,
            response_schema_version=RESPONSE_SCHEMA_PAUSE_DIRECTION,
            selected_hook_id=lock_input.lock.lock_id,
            sentences=lock_input.sentences,
            pause_voice_segments=[
                {
                    "segment_id": segment.segment_id,
                    "sentence_id": segment.sentence_id,
                    "duration_seconds": segment.duration_seconds,
                }
                for segment in segments
            ],
            input_fingerprint=run.input_fingerprint,
        )
        try:
            response = DiscoveryTextGateway(config=config).generate(request)
        except TextGatewayError as exc:
            raise NarrationServiceError(exc.code) from exc
        if response.pause_direction is None:
            raise NarrationServiceError(NARRATION_ERROR_PAUSE_RESPONSE_INVALID)
        payload = response.pause_direction
        directions = payload.directions
        for direction in directions:
            if direction.sentence_id is not None and direction.sentence_id not in segment_by_sentence:
                raise NarrationServiceError(NARRATION_ERROR_PAUSE_RESPONSE_INVALID)
        plan = payload.pause_plan.model_copy(update={"status": PauseDirectionPlanStatus.COMPLETED})
        relative = repo.save_pause_plan_json(root, plan, directions)
        conn.execute("BEGIN IMMEDIATE")
        repo.insert_pause_plan(conn, plan, directions, relative)
        final = run.model_copy(
            update={"status": NarrationRunStatus.COMPLETED, "finished_at": _now()}
        )
        repo.update_voice_run(conn, final)
        repo.mark_current_pause_plan(
            conn,
            project_id=run.project_id,
            script_lock_id=run.script_lock_id,
            pause_plan_id=plan.pause_plan_id,
        )
        repo.write_latest_pause_pointer(root, plan)
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        failed = (run if "run" in locals() and run is not None else None)
        if failed is not None:
            code = getattr(exc, "code", str(exc) or NARRATION_ERROR_PAUSE_GATEWAY_UNCONFIGURED)
            repo.update_voice_run(
                conn,
                failed.model_copy(
                    update={
                        "status": NarrationRunStatus.FAILED,
                        "error_code": code,
                        "error_message": "Pause direction failed.",
                        "finished_at": _now(),
                    }
                ),
            )
            conn.commit()
    finally:
        conn.close()


def _project_stub(root: Path, project_id: str) -> Project:
    from otio_app.models import ProjectMode, ProjectStatus

    return Project(
        id=project_id,
        name="Narration pause worker project",
        project_root=str(root),
        work_dir=str(root / "_otio"),
        project_mode=ProjectMode.DISCOVERY_V2,
        language="de",
        fps=25.0,
        status=ProjectStatus.DRAFT,
        asset_subdir_names=[],
        selected_asset_subdirs=[],
    )


__all__ = [
    "PauseDirectionResult",
    "process_pause_direction_run",
    "start_pause_direction_run",
]
