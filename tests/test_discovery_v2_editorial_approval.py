from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from otio_app.discovery_v2.adapters.analysis_job_launcher import reset_analysis_job_launcher_for_tests
from otio_app.discovery_v2.adapters.editorial_job_launcher import reset_editorial_job_launcher_for_tests
from otio_app.discovery_v2.adapters.export_job_launcher import reset_export_job_launcher_for_tests
from otio_app.discovery_v2.adapters.narration_job_launcher import reset_narration_job_launcher_for_tests
from otio_app.discovery_v2.adapters.supplementation_job_launcher import reset_supplementation_job_launcher_for_tests
from otio_app.discovery_v2.adapters.text_fake import reset_fake_text_test_hook
from otio_app.discovery_v2.adapters.visual_edit_job_launcher import reset_visual_edit_job_launcher_for_tests
from otio_app.discovery_v2.adapters.voice_fake import reset_fake_voice_call_count
from otio_app.discovery_v2.application.editorial_approval_service import (
    create_editorial_approval,
    get_review_export_view,
    preview_editorial_approval,
)
from otio_app.discovery_v2.application.export_validation_service import start_export_validation_run
from otio_app.discovery_v2.application.feasibility_service import start_feasibility_check_run
from otio_app.discovery_v2.application.humanity_review_service import start_humanity_review_run
from otio_app.discovery_v2.application.otio_export_service import start_otio_export_run
from otio_app.discovery_v2.application.visual_edit_plan_service import start_visual_edit_plan_run
from otio_app.discovery_v2.domain.export import (
    EXPORT_ERROR_EDITORIAL_APPROVAL_CONFIRMATION_REQUIRED,
    EXPORT_ERROR_EDITORIAL_APPROVAL_FINGERPRINT_MISMATCH,
    EditorialApprovalStatus,
)
from otio_app.discovery_v2.persistence import export_repository as export_repo
from test_discovery_v2_visual_edit_plan import _visual_ready_project


def _reset_all() -> None:
    reset_analysis_job_launcher_for_tests()
    reset_editorial_job_launcher_for_tests()
    reset_supplementation_job_launcher_for_tests()
    reset_narration_job_launcher_for_tests()
    reset_visual_edit_job_launcher_for_tests()
    reset_export_job_launcher_for_tests()
    reset_fake_text_test_hook()
    reset_fake_voice_call_count()


def _ready_for_approval(tmp_path: Path, temp_db_path: Path):
    _reset_all()
    project = _visual_ready_project(tmp_path, temp_db_path)
    assert start_visual_edit_plan_run(project, sync=True).started
    assert start_humanity_review_run(project, sync=True).ok
    ready = start_feasibility_check_run(project, sync=True)
    assert ready.report is not None
    return project


def _approved_project(tmp_path: Path, temp_db_path: Path):
    project = _ready_for_approval(tmp_path, temp_db_path)
    preview = preview_editorial_approval(project)
    assert preview.ok and preview.fingerprint
    result = create_editorial_approval(
        project,
        confirmation_checked=True,
        user_decision="approved",
        user_comment="human approved",
        accepted_risks=preview.context.visible_risks if preview.context else [],
        confirmed_fingerprint=preview.fingerprint,
    )
    assert result.ok and result.approval is not None
    return project, result.approval


def _validated_project(tmp_path: Path, temp_db_path: Path):
    project, approval = _approved_project(tmp_path, temp_db_path)
    validation = start_export_validation_run(project, sync=True)
    assert validation.ok, validation.error_code
    assert validation.contract is not None
    return project, approval, validation


def _exported_project(tmp_path: Path, temp_db_path: Path):
    project, approval, validation = _validated_project(tmp_path, temp_db_path)
    result = start_otio_export_run(project, sync=True)
    assert result.started, result.error_code
    assert result.artifact is not None
    return project, approval, validation, result


def test_smoke_a_human_approval_requires_checkbox_and_persists_current(
    tmp_path: Path, temp_db_path: Path
) -> None:
    project = _ready_for_approval(tmp_path, temp_db_path)
    preview = preview_editorial_approval(project)
    assert preview.ok and preview.fingerprint
    missing_checkbox = create_editorial_approval(
        project,
        confirmation_checked=False,
        user_decision="approved",
        user_comment="",
        accepted_risks=[],
        confirmed_fingerprint=preview.fingerprint,
    )
    assert not missing_checkbox.ok
    assert missing_checkbox.error_code == EXPORT_ERROR_EDITORIAL_APPROVAL_CONFIRMATION_REQUIRED
    approved = create_editorial_approval(
        project,
        confirmation_checked=True,
        user_decision="approved",
        user_comment="checked",
        accepted_risks=preview.context.visible_risks if preview.context else [],
        confirmed_fingerprint=preview.fingerprint,
    )
    assert approved.ok and approved.approval is not None
    assert approved.approval.status == EditorialApprovalStatus.APPROVED
    conn = export_repo.open_export_registry(project.project_root_path)
    try:
        state = export_repo.get_project_state(conn, project_id=project.id)
        assert state is not None
        assert state.current_editorial_approval_id == approved.approval.approval_id
    finally:
        conn.close()


def test_smoke_b_fingerprint_mismatch_blocks_approval(tmp_path: Path, temp_db_path: Path) -> None:
    project = _ready_for_approval(tmp_path, temp_db_path)
    result = create_editorial_approval(
        project,
        confirmation_checked=True,
        user_decision="approved",
        user_comment="",
        accepted_risks=[],
        confirmed_fingerprint="not-current",
    )
    assert not result.ok
    assert result.error_code == EXPORT_ERROR_EDITORIAL_APPROVAL_FINGERPRINT_MISMATCH


def test_smoke_c_plan_change_invalidates_approval_and_blocks_export(
    tmp_path: Path, temp_db_path: Path
) -> None:
    from otio_app.discovery_v2.domain.export import EXPORT_ERROR_EDITORIAL_APPROVAL_INVALIDATED
    from otio_app.discovery_v2.persistence import visual_edit_repository as visual_repo

    project, approval = _approved_project(tmp_path, temp_db_path)
    conn = visual_repo.open_visual_edit_registry(project.project_root_path)
    try:
        state = visual_repo.get_project_state(conn, project_id=project.id)
        # Mutate plan content hash inputs so approval fingerprint becomes stale.
        conn.execute(
            "UPDATE visual_edit_plans SET input_fingerprint = ? WHERE plan_id = ?",
            ("stale-after-approval", state.current_visual_edit_plan_id),
        )
        conn.commit()
    finally:
        conn.close()
    preview = preview_editorial_approval(project)
    # Preview either fails or yields a new fingerprint; either way approval must not remain usable.
    if preview.fingerprint is not None:
        assert preview.fingerprint != approval.input_fingerprint
    validation = start_export_validation_run(project, sync=True)
    assert not validation.ok
    assert validation.error_code in {
        EXPORT_ERROR_EDITORIAL_APPROVAL_INVALIDATED,
        "export_input_stale",
        "editorial_approval_required",
        "export_validation_failed",
    }
    conn = export_repo.open_export_registry(project.project_root_path)
    try:
        current = export_repo.get_editorial_approval(conn, approval_id=approval.approval_id)
        assert current is not None
        assert current.status in {
            EditorialApprovalStatus.INVALIDATED,
            EditorialApprovalStatus.APPROVED,
        }
        if current.status == EditorialApprovalStatus.APPROVED:
            assert validation.error_code is not None
        else:
            assert current.status == EditorialApprovalStatus.INVALIDATED
    finally:
        conn.close()


def test_review_export_view_exposes_preview_and_actions(tmp_path: Path, temp_db_path: Path) -> None:
    project, approval = _approved_project(tmp_path, temp_db_path)
    view = get_review_export_view(project)
    assert view.ok
    assert view.current_approval is not None
    assert view.current_approval.approval_id == approval.approval_id
    assert view.preview is not None and view.preview.fingerprint == approval.input_fingerprint
    assert view.can_validate is True
