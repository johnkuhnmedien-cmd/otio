from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from otio_app.discovery_v2.application.export_validation_service import start_export_validation_run
from otio_app.discovery_v2.domain.export import EXPORT_ERROR_PLANNED_GRAPHIC, EXPORT_ERROR_VALIDATION_FAILED
from otio_app.discovery_v2.persistence import export_repository as export_repo
from otio_app.discovery_v2.persistence import visual_edit_repository as visual_repo
from test_discovery_v2_editorial_approval import _approved_project


def test_smoke_a_approved_ready_plan_validates_to_export_contract(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project, approval = _approved_project(tmp_path, temp_db_path)
    result = start_export_validation_run(project, sync=True)
    assert result.ok, result.error_code
    assert result.report is not None and result.contract is not None
    assert result.report.approval_id == approval.approval_id
    assert result.contract.timeline_name.startswith("discovery_v2_")
    assert len(result.contract.video_items) > 0
    assert len(result.contract.audio_items) > 0
    assert abs(sum(item.duration_frames for item in result.contract.video_items) - result.contract.total_frames) <= 1
    assert abs(sum(item.duration_frames for item in result.contract.audio_items) - result.contract.total_frames) <= 1
    assert all(ref.relative_path.startswith(("media/working/", "narration/audio/")) for ref in result.contract.media_references)


def test_smoke_d_forbidden_media_tokens_are_rejected_in_contract_paths(
    tmp_path: Path, temp_db_path: Path
) -> None:
    from otio_app.discovery_v2.application.export_validation_service import (
        _is_valid_narration_audio_path,
        _validate_working_relative_path,
    )
    from otio_app.discovery_v2.domain.export import (
        EXPORT_ERROR_INVALID_MEDIA_REFERENCE,
        ExportValidationIssue,
    )

    project, _approval = _approved_project(tmp_path, temp_db_path)
    issues: list[ExportValidationIssue] = []
    for bad in (
        "media/working/preview/clip.mp4",
        "media/temp/x.mp4",
        "media/original/x.mp4",
        "media/quarantine/x.mp4",
        "analysis/frames/x.jpg",
        "stock/candidate/x.mp4",
        "../escape.mp4",
        "_otio/legacy.mp4",
    ):
        issues.clear()
        assert (
            _validate_working_relative_path(project, bad, issues, "r1", None, None) is None
        ), bad
        assert any(issue.error_code == EXPORT_ERROR_INVALID_MEDIA_REFERENCE for issue in issues)
    assert _is_valid_narration_audio_path("narration/audio/run/seg.wav")
    assert not _is_valid_narration_audio_path("media/working/seg.wav")
    assert not _is_valid_narration_audio_path("narration/audio/../seg.wav")


def test_smoke_e_planned_graphic_blocks_validation(tmp_path: Path, temp_db_path: Path) -> None:
    project, _approval = _approved_project(tmp_path, temp_db_path)
    conn = visual_repo.open_visual_edit_registry(project.project_root_path)
    try:
        state = visual_repo.get_project_state(conn, project_id=project.id)
        bundle = visual_repo.get_plan_bundle(conn, plan_id=state.current_visual_edit_plan_id)
        first = bundle.shots[0]
        conn.execute(
            "UPDATE editorial_shots SET media_strategy = 'planned_graphic' WHERE shot_id = ?",
            (first.shot_id,),
        )
        conn.commit()
    finally:
        conn.close()
    result = start_export_validation_run(project, sync=True)
    assert not result.ok
    assert result.error_code in {
        EXPORT_ERROR_VALIDATION_FAILED,
        "editorial_approval_invalidated",
        EXPORT_ERROR_PLANNED_GRAPHIC,
    }
    conn = export_repo.open_export_registry(project.project_root_path)
    try:
        state = export_repo.get_project_state(conn, project_id=project.id)
        assert state is not None
    finally:
        conn.close()
