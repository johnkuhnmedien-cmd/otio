"""Application service for Discovery V2 Phase 11 fake voice generation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from otio_app.discovery_v2.adapters.narration_job_launcher import (
    get_narration_job_launcher,
)
from otio_app.discovery_v2.adapters.voice_config import load_voice_config
from otio_app.discovery_v2.adapters.voice_gateway import (
    VoiceGatewayError,
    VoiceGatewayRequest,
    VoiceGenerationGateway,
    validate_fake_wav,
)
from otio_app.discovery_v2.application.inventory_service import (
    InventoryServiceError,
    require_discovery_project,
)
from otio_app.discovery_v2.application.script_lock_service import get_effective_script_lock
from otio_app.discovery_v2.domain.narration import (
    MAX_SEGMENTS,
    MAX_TEXT_LEN,
    NARRATION_ERROR_ANALYSIS_RUN_ALREADY_ACTIVE,
    NARRATION_ERROR_EDITORIAL_RUN_ALREADY_ACTIVE,
    NARRATION_ERROR_INPUT_STALE,
    NARRATION_ERROR_RUN_ALREADY_ACTIVE,
    NARRATION_ERROR_SCRIPT_LOCK_INVALIDATED,
    NARRATION_ERROR_SCRIPT_LOCK_MISSING,
    NARRATION_ERROR_SUPPLEMENTATION_RUN_ALREADY_ACTIVE,
    NARRATION_ERROR_VOICE_GENERATION_FAILED,
    NARRATION_ERROR_VOICE_PROFILE_INVALID,
    NARRATION_ERROR_VOICE_SEGMENT_HASH_MISMATCH,
    NARRATION_ERROR_VOICE_SEGMENT_INVALID,
    NARRATION_RUN_SCOPE_VOICE,
    VOICE_ADAPTER_VERSION_FAKE,
    VOICE_AUDIO_FORMAT,
    VOICE_CHANNELS,
    VOICE_PROVIDER_FAKE,
    VOICE_SAMPLE_RATE_HZ,
    NarrationAttemptStatus,
    NarrationProjectState,
    NarrationRunStatus,
    VoiceGenerationAttempt,
    VoiceGenerationRun,
    VoiceOutputProfile,
    VoiceProfile,
    VoiceProfileStatus,
    VoiceSegment,
    VoiceSegmentStatus,
    compute_sha256,
    normalize_sentence_text,
    sentence_text_hash,
    voice_run_input_fingerprint,
    voice_segment_cache_identity,
)
from otio_app.discovery_v2.narration_paths import (
    narration_audio_relative_path,
    narration_temp_dir,
    resolve_narration_relative_path,
)
from otio_app.discovery_v2.persistence import editorial_repository as editorial_repo
from otio_app.discovery_v2.persistence import narration_repository as repo
from otio_app.discovery_v2.persistence.asset_analysis_repository import find_active_analysis_run
from otio_app.discovery_v2.persistence.asset_registry_database import RegistryDatabaseError
from otio_app.discovery_v2.persistence.editorial_repository import find_active_editorial_run
from otio_app.discovery_v2.persistence.supplementation_repository import (
    find_active_supplementation_run,
)
from otio_app.models import Project


class NarrationServiceError(InventoryServiceError):
    """Domain error for narration operations."""

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code


@dataclass(frozen=True)
class NarrationStartResult:
    started: bool
    message: str
    run: VoiceGenerationRun | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class EffectiveNarrationLock:
    lock: object
    script: object
    sentences: list[object]


@dataclass(frozen=True)
class NarrationView:
    ok: bool
    message: str | None = None
    state: NarrationProjectState | None = None
    effective_lock: object | None = None
    voice_profile: VoiceProfile | None = None
    active_run: VoiceGenerationRun | None = None
    voice_runs: list[VoiceGenerationRun] = field(default_factory=list)
    voice_segments: list[VoiceSegment] = field(default_factory=list)
    pause_plans: list[object] = field(default_factory=list)
    timelines: list[object] = field(default_factory=list)
    can_start_voice: bool = False
    can_start_pause: bool = False
    can_resolve_timing: bool = False


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_narration_view(project: Project) -> NarrationView:
    try:
        project = require_discovery_project(project)
    except InventoryServiceError as exc:
        return NarrationView(ok=False, message=str(exc))
    try:
        conn = repo.open_narration_registry(project.project_root_path)
    except RegistryDatabaseError as exc:
        return NarrationView(ok=False, message=str(exc))
    try:
        active = repo.find_active_narration_run(conn, project_id=project.id)
        state = repo.get_project_state(conn, project_id=project.id)
        profile = (
            None
            if state is None or state.current_voice_profile_id is None
            else repo.get_voice_profile(conn, voice_profile_id=state.current_voice_profile_id)
        ) or repo.get_active_voice_profile(conn, project_id=project.id)
        runs = repo.list_voice_runs(conn, project_id=project.id)
        segments = (
            []
            if state is None or state.current_voice_run_id is None
            else repo.list_voice_segments_for_run(conn, run_id=state.current_voice_run_id)
        )
        pause_plans = repo.list_pause_plans(conn, project_id=project.id)
        timelines = repo.list_timelines(conn, project_id=project.id)
    finally:
        conn.close()
    effective = get_effective_script_lock(project)
    can_voice = effective.ok and active is None
    can_pause = bool(state and state.current_voice_run_id) and active is None
    can_timing = bool(state and state.current_pause_plan_id) and active is None
    return NarrationView(
        ok=True,
        state=state,
        effective_lock=effective.lock,
        voice_profile=profile,
        active_run=active,
        voice_runs=runs,
        voice_segments=segments,
        pause_plans=pause_plans,
        timelines=timelines,
        can_start_voice=can_voice,
        can_start_pause=can_pause,
        can_resolve_timing=can_timing,
    )


def require_effective_lock_for_narration(project: Project) -> EffectiveNarrationLock:
    project = require_discovery_project(project)
    result = get_effective_script_lock(project)
    if not result.ok or result.lock is None:
        code = result.error_code or NARRATION_ERROR_SCRIPT_LOCK_MISSING
        if code == "script_lock_invalidated":
            code = NARRATION_ERROR_SCRIPT_LOCK_INVALIDATED
        raise NarrationServiceError(code)
    conn = repo.open_narration_registry(project.project_root_path)
    try:
        script = editorial_repo.get_active_script(conn, project_id=project.id)
        bundle = (
            None
            if script is None
            else editorial_repo.get_script_bundle(conn, script_id=script.script_id)
        )
    finally:
        conn.close()
    if script is None or bundle is None or script.script_id != result.lock.script_id:
        raise NarrationServiceError(NARRATION_ERROR_INPUT_STALE)
    sentences = sorted(bundle.get("sentences", []), key=lambda item: int(item["ordinal"]))
    lock_sentence_ids = [str(item["sentence_id"]) for item in sentences]
    if len(lock_sentence_ids) > MAX_SEGMENTS:
        raise NarrationServiceError(NARRATION_ERROR_VOICE_GENERATION_FAILED)
    return EffectiveNarrationLock(lock=result.lock, script=script, sentences=sentences)


def ensure_default_voice_profile(project: Project) -> VoiceProfile:
    project = require_discovery_project(project)
    config = load_voice_config()
    conn = repo.open_narration_registry(project.project_root_path)
    try:
        existing = repo.get_active_voice_profile(conn, project_id=project.id)
        if existing is not None:
            return existing
        profile = VoiceProfile(
            voice_profile_id=repo.new_voice_profile_id(),
            project_id=project.id,
            language=project.language,
            provider=config.provider,
            voice_identifier=config.voice_identifier,
            voice_settings_version=config.voice_settings_version,
            output_profile=VoiceOutputProfile(
                sample_rate_hz=config.sample_rate_hz,
                channels=config.channels,
            ),
            version=1,
            adapter_version=config.adapter_version,
            audio_format=VOICE_AUDIO_FORMAT,
            sample_rate=VOICE_SAMPLE_RATE_HZ,
            channels=VOICE_CHANNELS,
            supersedes_voice_profile_id=None,
            status=VoiceProfileStatus.ACTIVE,
            created_at=_now(),
        )
        relative = repo.save_voice_profile_json(project.project_root_path, profile)
        conn.execute("BEGIN IMMEDIATE")
        repo.insert_voice_profile(conn, profile, relative)
        state = repo.get_project_state(conn, project_id=project.id) or NarrationProjectState(
            project_id=project.id,
            updated_at=_now(),
        )
        repo.upsert_project_state(
            conn,
            state.model_copy(
                update={
                    "current_voice_profile_id": profile.voice_profile_id,
                    "updated_at": _now(),
                }
            ),
        )
        conn.commit()
        return profile
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        raise NarrationServiceError(str(exc)) from exc
    finally:
        conn.close()


def rotate_fake_voice_profile(
    project: Project,
    *,
    voice_settings_version: str,
) -> VoiceProfile:
    """Supersede the active Fake VoiceProfile and activate a new settings version."""
    project = require_discovery_project(project)
    config = load_voice_config()
    if not voice_settings_version or voice_settings_version == config.voice_settings_version:
        # Allow explicit new version strings; reject empty / identical-to-default only when
        # no prior profile exists with a different version — callers must pass a new version.
        pass
    conn = repo.open_narration_registry(project.project_root_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing = repo.get_active_voice_profile(conn, project_id=project.id)
        if existing is not None:
            if existing.voice_settings_version == voice_settings_version:
                conn.commit()
                return existing
            repo.update_voice_profile_status(
                conn,
                voice_profile_id=existing.voice_profile_id,
                status=VoiceProfileStatus.SUPERSEDED,
            )
        profile = VoiceProfile(
            voice_profile_id=repo.new_voice_profile_id(),
            project_id=project.id,
            language=project.language,
            provider=config.provider,
            voice_identifier=config.voice_identifier,
            voice_settings_version=voice_settings_version,
            output_profile=VoiceOutputProfile(
                sample_rate_hz=config.sample_rate_hz,
                channels=config.channels,
            ),
            version=1 if existing is None else existing.version + 1,
            adapter_version=config.adapter_version,
            audio_format=VOICE_AUDIO_FORMAT,
            sample_rate=VOICE_SAMPLE_RATE_HZ,
            channels=VOICE_CHANNELS,
            supersedes_voice_profile_id=(
                None if existing is None else existing.voice_profile_id
            ),
            status=VoiceProfileStatus.ACTIVE,
            created_at=_now(),
        )
        relative = repo.save_voice_profile_json(project.project_root_path, profile)
        repo.insert_voice_profile(conn, profile, relative)
        state = repo.get_project_state(conn, project_id=project.id) or NarrationProjectState(
            project_id=project.id,
            updated_at=_now(),
        )
        # New profile invalidates current pause/timeline selection (stale Current State).
        repo.upsert_project_state(
            conn,
            state.model_copy(
                update={
                    "current_voice_profile_id": profile.voice_profile_id,
                    "current_voice_run_id": None,
                    "current_pause_plan_id": None,
                    "current_timeline_id": None,
                    "updated_at": _now(),
                }
            ),
        )
        conn.commit()
        return profile
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        raise NarrationServiceError(str(exc)) from exc
    finally:
        conn.close()


def start_voice_generation_run(project: Project, *, sync: bool = False) -> NarrationStartResult:
    project = require_discovery_project(project)
    try:
        lock_input = require_effective_lock_for_narration(project)
    except NarrationServiceError as exc:
        return NarrationStartResult(False, str(exc), error_code=exc.code)
    profile = ensure_default_voice_profile(project)
    conn = repo.open_narration_registry(project.project_root_path)
    try:
        blocked = _active_blocker(conn, project_id=project.id)
        if blocked is not None:
            code, message = blocked
            return NarrationStartResult(False, message, error_code=code)
        if profile.provider != VOICE_PROVIDER_FAKE:
            return NarrationStartResult(
                False,
                "Voice profile ist ungueltig.",
                error_code=NARRATION_ERROR_VOICE_PROFILE_INVALID,
            )
        fingerprint = voice_run_input_fingerprint(
            script_lock_id=lock_input.lock.lock_id,
            lock_fingerprint=lock_input.lock.lock_fingerprint,
            voice_profile=profile,
            sentences=lock_input.sentences,
        )
        run = VoiceGenerationRun(
            run_id=repo.new_voice_run_id(),
            project_id=project.id,
            script_lock_id=lock_input.lock.lock_id,
            script_id=lock_input.script.script_id,
            voice_profile_id=profile.voice_profile_id,
            input_fingerprint=fingerprint,
            provider=profile.provider,
            adapter_version=VOICE_ADAPTER_VERSION_FAKE,
            scope=NARRATION_RUN_SCOPE_VOICE,
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
        worker="narration_voice",
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
        return NarrationStartResult(True, "Voice-Run abgeschlossen.", run=final)
    return NarrationStartResult(True, "Voice-Run gestartet.", run=run)


def process_voice_generation_run(project_root: Path, run_id: str) -> None:
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
        project_stub = _project_stub_from_run(root, run)
        profile = repo.get_voice_profile(conn, voice_profile_id=run.voice_profile_id)
        if profile is None:
            raise NarrationServiceError(NARRATION_ERROR_VOICE_PROFILE_INVALID)
        lock_input = require_effective_lock_for_narration(project_stub)
        gateway = VoiceGenerationGateway()
        created = reused = failed = 0
        for sentence in lock_input.sentences:
            sentence_id = str(sentence["sentence_id"])
            ordinal = int(sentence["ordinal"])
            text = normalize_sentence_text(str(sentence["text"]))
            if not text or len(text) > MAX_TEXT_LEN:
                failed += 1
                _record_failed_attempt(conn, run, sentence_id, NARRATION_ERROR_VOICE_SEGMENT_INVALID)
                continue
            identity = voice_segment_cache_identity(
                script_lock_id=run.script_lock_id,
                sentence_id=sentence_id,
                sentence_text=text,
                voice_profile=profile,
            )
            cached = repo.find_cached_voice_segment(conn, segment_id=identity.segment_id)
            if cached is not None and _segment_artifact_valid(root, cached):
                reused += 1
                attempt = _attempt(run, sentence_id, identity.cache_key)
                repo.insert_voice_attempt(conn, attempt)
                relative = repo.save_voice_attempt_json(
                    root,
                    attempt,
                    {"segment_id": cached.segment_id, "cache": "reused"},
                )
                repo.update_voice_attempt(
                    conn,
                    attempt.model_copy(
                        update={
                            "segment_id": cached.segment_id,
                            "status": NarrationAttemptStatus.REUSED,
                            "relative_json_path": relative,
                            "completed_at": _now(),
                        }
                    ),
                )
                conn.commit()
                continue
            attempt = _attempt(run, sentence_id, identity.cache_key)
            repo.insert_voice_attempt(conn, attempt)
            conn.commit()
            temp_path = narration_temp_dir(root, run.run_id) / f"{identity.segment_id}.wav"
            try:
                response = gateway.generate(
                    VoiceGatewayRequest(
                        text=text,
                        cache_key=identity.cache_key,
                        output_path=temp_path,
                    )
                )
                relative_path = narration_audio_relative_path(run.run_id, identity.segment_id)
                repo.publish_voice_wav(
                    root,
                    temp_path=response.path,
                    relative_path=relative_path,
                    expected_sha256=response.audio_sha256,
                )
                segment = VoiceSegment(
                    segment_id=identity.segment_id,
                    run_id=run.run_id,
                    script_lock_id=run.script_lock_id,
                    script_id=run.script_id,
                    sentence_id=sentence_id,
                    sentence_ordinal=ordinal,
                    text_hash=sentence_text_hash(text),
                    voice_profile_id=profile.voice_profile_id,
                    provider=profile.provider,
                    voice_identifier=profile.voice_identifier,
                    voice_settings_version=profile.voice_settings_version,
                    adapter_version=profile.adapter_version,
                    audio_format=profile.audio_format,
                    sample_count=response.sample_count,
                    duration_seconds=response.duration_seconds,
                    byte_size=response.byte_size,
                    audio_sha256=response.audio_sha256,
                    relative_path=relative_path,
                    status=VoiceSegmentStatus.PUBLISHED,
                    created_at=_now(),
                )
                repo.insert_voice_segment(conn, segment)
                relative = repo.save_voice_attempt_json(
                    root,
                    attempt,
                    {"segment": segment.model_dump(mode="json")},
                )
                repo.update_voice_attempt(
                    conn,
                    attempt.model_copy(
                        update={
                            "segment_id": segment.segment_id,
                            "status": NarrationAttemptStatus.COMPLETED,
                            "relative_json_path": relative,
                            "completed_at": _now(),
                        }
                    ),
                )
                created += 1
                conn.commit()
            except (VoiceGatewayError, OSError, ValueError) as exc:
                failed += 1
                code = getattr(exc, "code", NARRATION_ERROR_VOICE_GENERATION_FAILED)
                repo.update_voice_attempt(
                    conn,
                    attempt.model_copy(
                        update={
                            "status": NarrationAttemptStatus.FAILED,
                            "error_code": code,
                            "error_message": "Voice segment generation failed.",
                            "completed_at": _now(),
                        }
                    ),
                )
                conn.commit()
        status = NarrationRunStatus.COMPLETED if failed == 0 else NarrationRunStatus.FAILED
        final = run.model_copy(
            update={
                "status": status,
                "segments_created": created,
                "segments_reused": reused,
                "segments_failed": failed,
                "error_code": None if failed == 0 else NARRATION_ERROR_VOICE_GENERATION_FAILED,
                "error_message": None if failed == 0 else "One or more voice segments failed.",
                "finished_at": _now(),
            }
        )
        report = {
            "run": final.model_dump(mode="json"),
            "attempts": [
                attempt.model_dump(mode="json")
                for attempt in repo.list_voice_attempts(conn, run_id=run.run_id)
            ],
            "segments": [
                segment.model_dump(mode="json")
                for segment in repo.list_voice_segments_for_run(conn, run_id=run.run_id)
            ],
        }
        relative_report = repo.save_run_report(root, run.run_id, report)
        final = final.model_copy(update={"relative_report_path": relative_report})
        repo.update_voice_run(conn, final)
        if status == NarrationRunStatus.COMPLETED:
            repo.mark_current_voice_run(
                conn,
                project_id=run.project_id,
                script_lock_id=run.script_lock_id,
                voice_profile_id=profile.voice_profile_id,
                voice_run_id=run.run_id,
            )
            repo.write_latest_voice_pointer(root, final)
        conn.commit()
    finally:
        conn.close()
        repo.cleanup_narration_temp(root, run_id=run_id)


def _attempt(run: VoiceGenerationRun, sentence_id: str, cache_key: str) -> VoiceGenerationAttempt:
    return VoiceGenerationAttempt(
        attempt_id=repo.new_voice_attempt_id(),
        run_id=run.run_id,
        project_id=run.project_id,
        scope=run.scope,
        sentence_id=sentence_id,
        cache_key=cache_key,
        provider=run.provider,
        adapter_version=run.adapter_version,
        input_fingerprint=run.input_fingerprint,
        status=NarrationAttemptStatus.RUNNING,
        created_at=_now(),
    )


def _record_failed_attempt(conn, run: VoiceGenerationRun, sentence_id: str, code: str) -> None:
    attempt = VoiceGenerationAttempt(
        attempt_id=repo.new_voice_attempt_id(),
        run_id=run.run_id,
        project_id=run.project_id,
        scope=run.scope,
        sentence_id=sentence_id,
        provider=run.provider,
        adapter_version=run.adapter_version,
        input_fingerprint=run.input_fingerprint,
        status=NarrationAttemptStatus.FAILED,
        error_code=code,
        error_message="Voice segment input is invalid.",
        created_at=_now(),
        completed_at=_now(),
    )
    repo.insert_voice_attempt(conn, attempt)
    conn.commit()


def _segment_artifact_valid(root: Path, segment: VoiceSegment) -> bool:
    try:
        path = resolve_narration_relative_path(root, segment.relative_path)
        response = validate_fake_wav(path)
    except Exception:  # noqa: BLE001
        return False
    if response.audio_sha256 != segment.audio_sha256:
        raise NarrationServiceError(NARRATION_ERROR_VOICE_SEGMENT_HASH_MISMATCH)
    return True


def _active_blocker(conn, *, project_id: str) -> tuple[str, str] | None:
    from otio_app.discovery_v2.persistence.visual_edit_repository import (
        find_active_visual_edit_run,
    )

    if find_active_analysis_run(conn, project_id=project_id) is not None:
        return NARRATION_ERROR_ANALYSIS_RUN_ALREADY_ACTIVE, "Analysis-Run ist aktiv."
    if find_active_editorial_run(conn, project_id=project_id) is not None:
        return NARRATION_ERROR_EDITORIAL_RUN_ALREADY_ACTIVE, "Editorial-Run ist aktiv."
    if find_active_supplementation_run(conn, project_id=project_id) is not None:
        return NARRATION_ERROR_SUPPLEMENTATION_RUN_ALREADY_ACTIVE, "Supplementation-Run ist aktiv."
    if find_active_visual_edit_run(conn, project_id=project_id) is not None:
        return "visual_edit_run_already_active", "Visual-Edit-Run ist aktiv."
    if repo.find_active_narration_run(conn, project_id=project_id) is not None:
        return NARRATION_ERROR_RUN_ALREADY_ACTIVE, "Narration-Run ist aktiv."
    return None


def _project_stub_from_run(root: Path, run: VoiceGenerationRun) -> Project:
    from otio_app.models import ProjectMode, ProjectStatus

    return Project(
        id=run.project_id,
        name="Narration worker project",
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
    "EffectiveNarrationLock",
    "NarrationServiceError",
    "NarrationStartResult",
    "NarrationView",
    "ensure_default_voice_profile",
    "get_narration_view",
    "process_voice_generation_run",
    "require_effective_lock_for_narration",
    "rotate_fake_voice_profile",
    "start_voice_generation_run",
]
