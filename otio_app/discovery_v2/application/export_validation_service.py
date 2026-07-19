"""Deterministic export validation for Discovery V2 Phase 13."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from otio_app.discovery_v2.application.editorial_approval_service import (
    EditorialApprovalServiceError,
    build_export_input_context,
    require_current_approved_approval,
)
from otio_app.discovery_v2.application.inventory_service import InventoryServiceError, require_discovery_project
from otio_app.discovery_v2.domain.export import (
    EXPORT_ERROR_BLOCKING_ISSUE,
    EXPORT_ERROR_INPUT_STALE,
    EXPORT_ERROR_INVALID_AUDIO_REFERENCE,
    EXPORT_ERROR_INVALID_MEDIA_REFERENCE,
    EXPORT_ERROR_INVALID_SOURCE_RANGE,
    EXPORT_ERROR_INVALID_TIMEBASE,
    EXPORT_ERROR_PLANNED_GRAPHIC,
    EXPORT_ERROR_RUN_ALREADY_ACTIVE,
    EXPORT_ERROR_VALIDATION_FAILED,
    EXPORT_PROFILE_VERSION,
    AcceptedExportRisk,
    ExportAudioItem,
    ExportContract,
    ExportMediaReference,
    ExportTransitionItem,
    ExportValidationIssue,
    ExportValidationReport,
    ExportValidationReportStatus,
    ExportVideoItem,
    compute_export_sha256,
    timeline_name_for,
)
from otio_app.discovery_v2.domain.media_intake import WorkingMediaStatus
from otio_app.discovery_v2.domain.narration import NarrationTimelineEntryType, VoiceSegmentStatus
from otio_app.discovery_v2.paths import assert_path_is_under_discovery_v2, get_discovery_v2_root
from otio_app.discovery_v2.persistence import (
    asset_analysis_repository,
    export_repository as repo,
    narration_repository,
)
from otio_app.models import Project


class ExportValidationServiceError(InventoryServiceError):
    """Domain error for export validation."""

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code


@dataclass(frozen=True)
class ExportValidationResult:
    ok: bool
    message: str
    report: ExportValidationReport | None = None
    contract: ExportContract | None = None
    error_code: str | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def start_export_validation_run(project: Project, *, sync: bool = True) -> ExportValidationResult:
    del sync
    project = require_discovery_project(project)
    conn = repo.open_export_registry(project.project_root_path)
    try:
        if repo.find_active_export_run(conn, project_id=project.id) is not None:
            return ExportValidationResult(False, "Export-Run ist aktiv.", error_code=EXPORT_ERROR_RUN_ALREADY_ACTIVE)
        try:
            approval, context = require_current_approved_approval(project, conn=conn)
        except EditorialApprovalServiceError as exc:
            conn.rollback()
            return ExportValidationResult(False, "Editorial Approval fehlt oder ist stale.", error_code=exc.code)
        report_id = repo.new_export_validation_report_id()
        issues: list[ExportValidationIssue] = []
        contract = _build_contract(project, conn=conn, context=context, approval=approval, report_id=report_id, issues=issues)
        blocking = [issue for issue in issues if issue.blocks_export]
        if blocking:
            contract = None
        report = ExportValidationReport(
            report_id=report_id,
            approval_id=approval.approval_id,
            visual_edit_plan_id=approval.visual_edit_plan_id,
            input_fingerprint=approval.input_fingerprint,
            otio_profile_version=EXPORT_PROFILE_VERSION,
            timebase=_timebase_text(context.narration_timeline.timebase),
            status=ExportValidationReportStatus.FAILED if blocking else ExportValidationReportStatus.COMPLETED,
            issues=issues,
            metrics={} if contract is None else contract.metrics,
            created_at=_now(),
        )
        relative = repo.save_validation_report_json(project.project_root_path, report)
        conn.execute("BEGIN IMMEDIATE")
        state = repo.get_project_state(conn, project_id=project.id)
        if state is not None and state.current_export_validation_report_id:
            prior = repo.get_validation_report(conn, report_id=state.current_export_validation_report_id)
            if prior is not None and prior.status == ExportValidationReportStatus.COMPLETED:
                repo.update_validation_report_status(
                    conn,
                    report_id=prior.report_id,
                    status=ExportValidationReportStatus.SUPERSEDED,
                )
        repo.insert_validation_report(conn, report, relative)
        repo.mark_current_validation_report(conn, project_id=project.id, report_id=report.report_id)
        repo.write_latest_validation_pointer(project.project_root_path, report)
        conn.commit()
        if blocking:
            return ExportValidationResult(False, "Export Validation blockiert OTIO.", report=report, error_code=EXPORT_ERROR_VALIDATION_FAILED)
        return ExportValidationResult(True, "Export Validation bestanden.", report=report, contract=contract)
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        raise ExportValidationServiceError(str(exc)) from exc
    finally:
        conn.close()


def build_export_contract_for_current_validation(project: Project, *, conn) -> ExportContract:
    approval, context = require_current_approved_approval(project, conn=conn)
    state = repo.get_project_state(conn, project_id=project.id)
    if state is None or not state.current_export_validation_report_id:
        raise ExportValidationServiceError(EXPORT_ERROR_VALIDATION_FAILED)
    report = repo.get_validation_report(conn, report_id=state.current_export_validation_report_id)
    if report is None or report.status != ExportValidationReportStatus.COMPLETED:
        raise ExportValidationServiceError(EXPORT_ERROR_VALIDATION_FAILED)
    if report.approval_id != approval.approval_id or report.input_fingerprint != approval.input_fingerprint:
        repo.update_validation_report_status(
            conn,
            report_id=report.report_id,
            status=ExportValidationReportStatus.STALE,
        )
        raise ExportValidationServiceError(EXPORT_ERROR_INPUT_STALE)
    issues: list[ExportValidationIssue] = []
    contract = _build_contract(project, conn=conn, context=context, approval=approval, report_id=report.report_id, issues=issues)
    blocking = [issue for issue in issues if issue.blocks_export]
    if blocking:
        raise ExportValidationServiceError(EXPORT_ERROR_VALIDATION_FAILED)
    return contract


def validate_export_contract(project: Project) -> ExportValidationResult:
    conn = repo.open_export_registry(project.project_root_path)
    try:
        try:
            contract = build_export_contract_for_current_validation(project, conn=conn)
        except ExportValidationServiceError as exc:
            return ExportValidationResult(False, "Export Contract ist nicht gueltig.", error_code=exc.code)
        report = repo.get_validation_report(conn, report_id=contract.validation_report_id)
        return ExportValidationResult(True, "Export Contract ist gueltig.", report=report, contract=contract)
    finally:
        conn.close()


def _build_contract(
    project: Project,
    *,
    conn,
    context,
    approval,
    report_id: str,
    issues: list[ExportValidationIssue],
) -> ExportContract:
    timeline = context.narration_timeline
    bundle = context.visual_bundle
    fps = timeline.timebase.fps
    assignment_by_shot = {assignment.shot_id: assignment for assignment in bundle.assignments}
    working_by_id = {item.working_media_id: item for item in context.working_media}
    technical_by_id = {
        item.shot_id: item
        for item in asset_analysis_repository.list_technical_shots_for_project(
            conn,
            project_id=project.id,
        )
    }
    media_refs: dict[str, ExportMediaReference] = {}
    video_items: list[ExportVideoItem] = []
    for previous, shot in zip([None, *bundle.shots[:-1]], bundle.shots):
        del previous
        if shot.timeline_start_frame < 0 or shot.timeline_end_frame <= shot.timeline_start_frame:
            _issue(issues, report_id, shot.shot_id, None, EXPORT_ERROR_INVALID_SOURCE_RANGE, "Shot timeline range is invalid.")
        if shot.media_strategy == "planned_graphic":
            _issue(issues, report_id, shot.shot_id, None, EXPORT_ERROR_PLANNED_GRAPHIC, "Planned graphics have no exportable working media.")
        metadata = _video_metadata(project.id, approval, shot, assignment_by_shot.get(shot.shot_id))
        duration_frames = shot.timeline_end_frame - shot.timeline_start_frame
        if shot.media_strategy == "intentional_visual_only":
            metadata["visual_only"] = True
            video_items.append(
                ExportVideoItem(
                    shot_id=shot.shot_id,
                    ordinal=shot.ordinal,
                    item_type="gap",
                    name=f"shot_{shot.ordinal}_{shot.shot_id[:8]}",
                    duration_frames=duration_frames,
                    duration_seconds=shot.duration_seconds,
                    timeline_start_frame=shot.timeline_start_frame,
                    timeline_end_frame=shot.timeline_end_frame,
                    media_strategy=shot.media_strategy,
                    sentence_ids=list(shot.sentence_ids),
                    visual_beat_ids=list(shot.visual_beat_ids),
                    visual_intent_ids=list(shot.visual_intent_ids),
                    narration_entry_ids=list(shot.narration_entry_ids),
                    metadata=metadata,
                )
            )
            continue
        assignment = assignment_by_shot.get(shot.shot_id)
        if assignment is None or assignment.status != "resolved" or not assignment.working_media_id:
            _issue(issues, report_id, shot.shot_id, None, EXPORT_ERROR_INVALID_MEDIA_REFERENCE, "Shot has no resolved working media assignment.")
            continue
        working = working_by_id.get(assignment.working_media_id)
        if working is None or working.status != WorkingMediaStatus.COMPLETED:
            _issue(issues, report_id, shot.shot_id, assignment.assignment_id, EXPORT_ERROR_INVALID_MEDIA_REFERENCE, "Working media is not completed/current.")
            continue
        if shot.media_strategy == "local_video" and working.media_kind != "video":
            _issue(issues, report_id, shot.shot_id, assignment.assignment_id, EXPORT_ERROR_INVALID_MEDIA_REFERENCE, "Video shot references non-video working media.")
        if shot.media_strategy == "local_photo" and working.media_kind not in {"image", "photo"}:
            _issue(issues, report_id, shot.shot_id, assignment.assignment_id, EXPORT_ERROR_INVALID_MEDIA_REFERENCE, "Photo shot references non-image working media.")
        rel = _validate_working_relative_path(project, working.working_relative_path, issues, report_id, shot.shot_id, assignment.assignment_id)
        ref_id = f"working:{working.working_media_id}"
        if rel and ref_id not in media_refs:
            media_refs[ref_id] = ExportMediaReference(
                media_id=ref_id,
                asset_id=working.asset_id,
                working_media_id=working.working_media_id,
                relative_path=rel,
                absolute_target_url=_absolute_v2_url(project, rel),
                media_kind="video" if working.media_kind == "video" else "photo",
                sha256=working.output_sha256,
            )
        source_in_frame = assignment.technical_source_in_frame
        source_out_frame = assignment.technical_source_out_frame
        source_in_seconds = assignment.technical_source_in_seconds
        source_out_seconds = assignment.technical_source_out_seconds
        if shot.media_strategy == "local_video":
            if (
                source_in_frame is None
                or source_out_frame is None
                or source_out_frame <= source_in_frame
                or (source_out_frame - source_in_frame) < duration_frames
            ):
                _issue(issues, report_id, shot.shot_id, assignment.assignment_id, EXPORT_ERROR_INVALID_SOURCE_RANGE, "Video source range is shorter than timeline shot duration.")
        video_items.append(
            ExportVideoItem(
                shot_id=shot.shot_id,
                ordinal=shot.ordinal,
                item_type="clip",
                name=f"shot_{shot.ordinal}_{shot.shot_id[:8]}",
                duration_frames=duration_frames,
                duration_seconds=duration_frames / fps,
                timeline_start_frame=shot.timeline_start_frame,
                timeline_end_frame=shot.timeline_end_frame,
                media_strategy=shot.media_strategy,
                media_reference_id=ref_id,
                asset_id=assignment.asset_id,
                working_media_id=assignment.working_media_id,
                assignment_id=assignment.assignment_id,
                source_in_frame=source_in_frame if shot.media_strategy == "local_video" else 0,
                source_out_frame=source_out_frame if shot.media_strategy == "local_video" else duration_frames,
                source_in_seconds=source_in_seconds if shot.media_strategy == "local_video" else 0.0,
                source_out_seconds=source_out_seconds if shot.media_strategy == "local_video" else duration_frames / fps,
                sentence_ids=list(shot.sentence_ids),
                visual_beat_ids=list(shot.visual_beat_ids),
                visual_intent_ids=list(shot.visual_intent_ids),
                narration_entry_ids=list(shot.narration_entry_ids),
                metadata=metadata,
            )
        )
    transition_items = _build_transitions(bundle, assignment_by_shot, technical_by_id, report_id, issues, fps, approval)
    audio_items = _build_audio_items(project, conn, context, approval, media_refs, report_id, issues)
    _validate_timeline_shape(bundle.shots, timeline, video_items, audio_items, report_id, issues)
    metrics = {
        "track_count": 2,
        "video_item_count": len(video_items),
        "audio_item_count": len(audio_items),
        "transition_count": len(transition_items),
        "clip_count": sum(1 for item in [*video_items, *audio_items] if item.item_type == "clip"),
        "total_frames": timeline.total_frames,
        "total_duration_seconds": timeline.total_duration_seconds,
        "otio_profile_version": EXPORT_PROFILE_VERSION,
    }
    return ExportContract(
        project_id=project.id,
        approval_id=approval.approval_id,
        validation_report_id=report_id,
        visual_edit_plan_id=bundle.plan.plan_id,
        humanity_review_id=context.humanity_bundle.review.review_id,
        feasibility_report_id=context.feasibility_bundle.report.report_id,
        script_lock_id=context.lock.lock_id,
        narration_timeline_id=timeline.timeline_id,
        timeline_name=timeline_name_for(project.id, bundle.plan.plan_version),
        input_fingerprint=approval.input_fingerprint,
        fps_numerator=timeline.timebase.fps_numerator,
        fps_denominator=timeline.timebase.fps_denominator,
        fps=timeline.timebase.fps,
        total_frames=timeline.total_frames,
        total_duration_seconds=timeline.total_duration_seconds,
        media_references=list(media_refs.values()),
        video_items=video_items,
        audio_items=audio_items,
        transitions=transition_items,
        metrics=metrics,
    )


def _build_audio_items(project: Project, conn, context, approval, media_refs, report_id: str, issues: list[ExportValidationIssue]) -> list[ExportAudioItem]:
    items: list[ExportAudioItem] = []
    for entry in context.narration_timeline.entries:
        duration_frames = entry.end_frame - entry.start_frame
        metadata = {
            "project_id": project.id,
            "visual_edit_plan_id": context.visual_bundle.plan.plan_id,
            "approval_id": approval.approval_id,
            "narration_entry_ids": [entry.entry_id],
            "voice_segment_id": entry.voice_segment_id,
            "export_profile_version": EXPORT_PROFILE_VERSION,
        }
        if entry.entry_type in {NarrationTimelineEntryType.PAUSE, NarrationTimelineEntryType.VISUAL_ONLY}:
            items.append(
                ExportAudioItem(
                    entry_id=entry.entry_id,
                    ordinal=entry.ordinal,
                    item_type="gap",
                    name=f"audio_gap_{entry.ordinal}",
                    duration_frames=duration_frames,
                    duration_seconds=duration_frames / context.narration_timeline.timebase.fps,
                    timeline_start_frame=entry.start_frame,
                    timeline_end_frame=entry.end_frame,
                    sentence_id=entry.sentence_id,
                    metadata=metadata,
                )
            )
            continue
        if not entry.voice_segment_id:
            _issue(issues, report_id, None, None, EXPORT_ERROR_INVALID_AUDIO_REFERENCE, "Voice entry has no voice segment.")
            continue
        segment = narration_repository.get_voice_segment(conn, segment_id=entry.voice_segment_id)
        if segment is None or segment.status != VoiceSegmentStatus.PUBLISHED:
            _issue(issues, report_id, None, None, EXPORT_ERROR_INVALID_AUDIO_REFERENCE, "Voice segment is not published.")
            continue
        if not _is_valid_narration_audio_path(segment.relative_path):
            _issue(issues, report_id, None, None, EXPORT_ERROR_INVALID_AUDIO_REFERENCE, "Voice segment path is not a narration audio WAV.")
            continue
        ref_id = f"voice:{segment.segment_id}"
        if ref_id not in media_refs:
            media_refs[ref_id] = ExportMediaReference(
                media_id=ref_id,
                voice_segment_id=segment.segment_id,
                relative_path=segment.relative_path,
                absolute_target_url=_absolute_v2_url(project, segment.relative_path),
                media_kind="audio",
                sha256=segment.audio_sha256,
            )
        items.append(
            ExportAudioItem(
                entry_id=entry.entry_id,
                ordinal=entry.ordinal,
                item_type="clip",
                name=f"voice_{entry.ordinal}_{segment.segment_id[:8]}",
                duration_frames=duration_frames,
                duration_seconds=duration_frames / context.narration_timeline.timebase.fps,
                timeline_start_frame=entry.start_frame,
                timeline_end_frame=entry.end_frame,
                media_reference_id=ref_id,
                sentence_id=entry.sentence_id,
                voice_segment_id=segment.segment_id,
                metadata=metadata,
            )
        )
    return items


def _build_transitions(bundle, assignment_by_shot, technical_by_id, report_id, issues, fps: float, approval) -> list[ExportTransitionItem]:
    del technical_by_id
    transitions: list[ExportTransitionItem] = []
    shot_by_id = {shot.shot_id: shot for shot in bundle.shots}
    for transition in bundle.transitions:
        if transition.technical_type == "cut":
            continue
        from_assignment = assignment_by_shot.get(transition.from_shot_id)
        to_assignment = assignment_by_shot.get(transition.to_shot_id)
        if transition.technical_type == "dissolve":
            duration_frames = max(1, int(round(transition.resolved_duration_seconds * fps)))
            half = max(1, int(round(duration_frames / 2)))
            from_shot = shot_by_id.get(transition.from_shot_id)
            to_shot = shot_by_id.get(transition.to_shot_id)
            if (
                from_assignment is None
                or to_assignment is None
                or from_shot is None
                or to_shot is None
                or _available_transition_frames(from_assignment, from_shot) < half
                or _available_transition_frames(to_assignment, to_shot) < half
            ):
                _issue(issues, report_id, transition.from_shot_id, None, EXPORT_ERROR_BLOCKING_ISSUE, "Dissolve lacks adjacent source handles.")
                continue
            transitions.append(
                ExportTransitionItem(
                    transition_id=transition.transition_id,
                    from_shot_id=transition.from_shot_id,
                    to_shot_id=transition.to_shot_id,
                    technical_type="dissolve",
                    duration_frames=duration_frames,
                    duration_seconds=duration_frames / fps,
                    metadata={
                        "project_id": approval.project_id,
                        "visual_edit_plan_id": approval.visual_edit_plan_id,
                        "approval_id": approval.approval_id,
                        "transition_id": transition.transition_id,
                        "from_shot_id": transition.from_shot_id,
                        "to_shot_id": transition.to_shot_id,
                        "export_profile_version": EXPORT_PROFILE_VERSION,
                    },
                )
            )
        elif transition.technical_type not in {"fade", "hold"}:
            _issue(issues, report_id, transition.from_shot_id, None, EXPORT_ERROR_BLOCKING_ISSUE, f"Unsupported transition: {transition.technical_type}")
    return transitions


def _available_transition_frames(assignment, shot) -> int:
    if (
        assignment.technical_source_in_frame is not None
        and assignment.technical_source_out_frame is not None
        and assignment.technical_source_out_frame > assignment.technical_source_in_frame
    ):
        return assignment.technical_source_out_frame - assignment.technical_source_in_frame
    return max(0, shot.timeline_end_frame - shot.timeline_start_frame)


def _validate_timeline_shape(shots, timeline, video_items, audio_items, report_id: str, issues: list[ExportValidationIssue]) -> None:
    previous = 0
    for shot in shots:
        if shot.timeline_start_frame != previous:
            _issue(issues, report_id, shot.shot_id, None, EXPORT_ERROR_INVALID_SOURCE_RANGE, "Video timeline has a gap or overlap.")
        previous = shot.timeline_end_frame
    video_frames = sum(item.duration_frames for item in video_items)
    audio_frames = sum(item.duration_frames for item in audio_items)
    if abs(video_frames - audio_frames) > 1:
        _issue(issues, report_id, None, None, EXPORT_ERROR_INVALID_TIMEBASE, "V1 and A1 durations differ by more than one frame.")
    if abs(video_frames - timeline.total_frames) > 1 or abs(audio_frames - timeline.total_frames) > 1:
        _issue(issues, report_id, None, None, EXPORT_ERROR_INVALID_TIMEBASE, "Track duration does not match narration total.")
    if timeline.timebase.fps_numerator <= 0 or timeline.timebase.fps_denominator <= 0:
        _issue(issues, report_id, None, None, EXPORT_ERROR_INVALID_TIMEBASE, "Invalid narration timebase.")


def _validate_working_relative_path(project: Project, relative_path: str, issues, report_id, shot_id, assignment_id) -> str | None:
    raw = str(relative_path or "").strip().replace("\\", "/")
    invalid_tokens = ("preview", "original", "temp", "quarantine", "analysis", "candidate")
    if (
        not raw
        or raw.startswith("/")
        or ".." in raw.split("/")
        or not raw.startswith("media/working/")
        or any(token in raw.split("/") for token in invalid_tokens)
    ):
        _issue(issues, report_id, shot_id, assignment_id, EXPORT_ERROR_INVALID_MEDIA_REFERENCE, "Working media path is not exportable.")
        return None
    try:
        _absolute_v2_url(project, raw)
    except ValueError:
        _issue(issues, report_id, shot_id, assignment_id, EXPORT_ERROR_INVALID_MEDIA_REFERENCE, "Working media path escapes _otio_v2.")
        return None
    return raw


def _is_valid_narration_audio_path(relative_path: str) -> bool:
    raw = str(relative_path or "").strip().replace("\\", "/")
    return raw.startswith("narration/audio/") and raw.endswith(".wav") and not raw.startswith("/") and ".." not in raw.split("/")


def _absolute_v2_url(project: Project, relative_path: str) -> str:
    parts = [part for part in str(relative_path).replace("\\", "/").split("/") if part]
    absolute = get_discovery_v2_root(project.project_root_path) / Path(*parts)
    assert_path_is_under_discovery_v2(absolute, project.project_root_path)
    return absolute.resolve().as_posix()


def _video_metadata(project_id: str, approval, shot, assignment) -> dict[str, object]:
    return {
        "project_id": project_id,
        "visual_edit_plan_id": approval.visual_edit_plan_id,
        "approval_id": approval.approval_id,
        "shot_id": shot.shot_id,
        "sentence_ids": list(shot.sentence_ids),
        "visual_beat_ids": list(shot.visual_beat_ids),
        "visual_intent_ids": list(shot.visual_intent_ids),
        "asset_id": None if assignment is None else assignment.asset_id,
        "working_media_id": None if assignment is None else assignment.working_media_id,
        "assignment_id": None if assignment is None else assignment.assignment_id,
        "narration_entry_ids": list(shot.narration_entry_ids),
        "transition_intent": shot.transition_intent,
        "export_profile_version": EXPORT_PROFILE_VERSION,
    }


def _issue(issues: list[ExportValidationIssue], report_id: str, shot_id: str | None, assignment_id: str | None, code: str, details: str) -> None:
    issues.append(
        ExportValidationIssue(
            issue_id=compute_export_sha256({"report_id": report_id, "n": len(issues), "code": code})[:32],
            report_id=report_id,
            shot_id=shot_id,
            assignment_id=assignment_id,
            error_code=code,
            severity="blocking",
            technical_details=details,
            blocks_export=True,
        )
    )


def _timebase_text(timebase) -> str:
    return f"{timebase.fps_numerator}/{timebase.fps_denominator}"


__all__ = [name for name in globals() if not name.startswith("_")]
