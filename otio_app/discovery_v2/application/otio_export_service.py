"""OTIO export orchestration for Discovery V2 Phase 13."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from otio_app.discovery_v2.adapters.export_job_launcher import get_export_job_launcher
from otio_app.discovery_v2.adapters.otio_adapter import OTIO_LIBRARY_VERSION, write_timeline
from otio_app.discovery_v2.application.export_validation_service import (
    ExportValidationServiceError,
    build_export_contract_for_current_validation,
)
from otio_app.discovery_v2.application.inventory_service import InventoryServiceError, require_discovery_project
from otio_app.discovery_v2.application.otio_reparse_service import reparse_otio_file
from otio_app.discovery_v2.domain.export import (
    EXPORT_ERROR_ARTIFACT_CONFLICT,
    EXPORT_ERROR_INPUT_STALE,
    EXPORT_ERROR_OTIO_EXPORT_FAILED,
    EXPORT_ERROR_REPARSE_FAILED,
    EXPORT_ERROR_RUN_ALREADY_ACTIVE,
    EXPORT_ERROR_SEMANTIC_MISMATCH,
    EXPORT_ERROR_SERIALIZE_FAILED,
    EXPORT_PROFILE_VERSION,
    OtioExportArtifact,
    OtioExportRun,
    OtioExportRunStatus,
)
from otio_app.discovery_v2.export_paths import export_temp_dir
from otio_app.discovery_v2.persistence import export_repository as repo
from otio_app.discovery_v2.persistence.inventory_artifact_store import InventoryArtifactError
from otio_app.models import Project


class OtioExportServiceError(InventoryServiceError):
    """Domain error for OTIO export orchestration."""

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code


@dataclass(frozen=True)
class OtioExportResult:
    started: bool
    message: str
    run: OtioExportRun | None = None
    artifact: OtioExportArtifact | None = None
    reparse_report: object | None = None
    error_code: str | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def start_otio_export_run(project: Project, *, sync: bool = True) -> OtioExportResult:
    project = require_discovery_project(project)
    conn = repo.open_export_registry(project.project_root_path)
    run = None
    try:
        if repo.find_active_export_run(conn, project_id=project.id) is not None:
            return OtioExportResult(False, "Export-Run ist aktiv.", error_code=EXPORT_ERROR_RUN_ALREADY_ACTIVE)
        try:
            contract = build_export_contract_for_current_validation(project, conn=conn)
        except ExportValidationServiceError as exc:
            return OtioExportResult(False, "Export Validation fehlt oder ist stale.", error_code=exc.code)
        run = OtioExportRun(
            run_id=repo.new_otio_export_run_id(),
            project_id=project.id,
            approval_id=contract.approval_id,
            validation_report_id=contract.validation_report_id,
            visual_edit_plan_id=contract.visual_edit_plan_id,
            export_profile_version=EXPORT_PROFILE_VERSION,
            input_fingerprint=contract.input_fingerprint,
            status=OtioExportRunStatus.QUEUED,
            created_at=_now(),
        )
        repo.insert_otio_export_run(conn, run)
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        raise OtioExportServiceError(str(exc)) from exc
    finally:
        conn.close()
    if sync:
        return process_otio_export(project, run_id=run.run_id)
    launched = get_export_job_launcher().launch(
        project_id=project.id,
        project_root=project.project_root_path,
        run_id=run.run_id,
        worker="otio_export",
        sync=False,
    )
    if not launched:
        return OtioExportResult(False, "Export-Worker konnte nicht gestartet werden.", run=run, error_code=EXPORT_ERROR_RUN_ALREADY_ACTIVE)
    return OtioExportResult(True, "OTIO Export gestartet.", run=run)


def process_otio_export(project: Project, *, run_id: str) -> OtioExportResult:
    project = require_discovery_project(project)
    conn = repo.open_export_registry(project.project_root_path)
    run = None
    try:
        run = repo.get_otio_export_run(conn, run_id=run_id)
        if run is None:
            return OtioExportResult(False, "Export-Run fehlt.", error_code=EXPORT_ERROR_OTIO_EXPORT_FAILED)
        run = run.model_copy(update={"status": OtioExportRunStatus.RUNNING, "started_at": run.started_at or _now()})
        repo.update_otio_export_run(conn, run)
        conn.commit()
        contract = build_export_contract_for_current_validation(project, conn=conn)
        if (
            contract.approval_id != run.approval_id
            or contract.validation_report_id != run.validation_report_id
            or contract.input_fingerprint != run.input_fingerprint
        ):
            raise OtioExportServiceError(EXPORT_ERROR_INPUT_STALE)
        temp_dir = export_temp_dir(project.project_root_path, run.run_id)
        temp_dir.mkdir(parents=True, exist_ok=True)
        temp_otio = temp_dir / "timeline.otio"
        try:
            write_timeline(contract, temp_otio)
        except Exception as exc:  # noqa: BLE001
            raise OtioExportServiceError(EXPORT_ERROR_SERIALIZE_FAILED) from exc
        artifact_id = repo.new_otio_export_artifact_id()
        reparse = reparse_otio_file(
            path=temp_otio,
            contract=contract,
            export_run_id=run.run_id,
            artifact_id=artifact_id,
        )
        if not reparse.ok:
            raise OtioExportServiceError(EXPORT_ERROR_SEMANTIC_MISMATCH)
        final_relative = repo.default_otio_relative_path(run.run_id)
        try:
            published_relative, byte_size, sha256 = repo.publish_otio_file(
                project.project_root_path,
                temp_path=temp_otio,
                relative_path=final_relative,
            )
        except InventoryArtifactError as exc:
            raise OtioExportServiceError(EXPORT_ERROR_ARTIFACT_CONFLICT) from exc
        manifest = {
            "run": run.model_dump(mode="json"),
            "contract": contract.model_dump(mode="json"),
            "otio_relative_path": published_relative,
            "otio_sha256": sha256,
            "byte_size": byte_size,
            "reparse_report": reparse.report.model_dump(mode="json"),
        }
        manifest_relative = repo.save_export_manifest_json(project.project_root_path, run.run_id, manifest)
        artifact = OtioExportArtifact(
            artifact_id=artifact_id,
            run_id=run.run_id,
            relative_path=published_relative,
            byte_size=byte_size,
            sha256=sha256,
            otio_library_version=OTIO_LIBRARY_VERSION,
            track_count=2,
            clip_count=int(contract.metrics.get("clip_count", 0)),
            total_duration_seconds=contract.total_duration_seconds,
            total_frames=contract.total_frames,
            timebase=f"{contract.fps_numerator}/{contract.fps_denominator}",
            created_at=_now(),
        )
        reparse_relative = repo.save_reparse_report_json(project.project_root_path, reparse.report)
        run_report_relative = repo.save_run_report(
            project.project_root_path,
            run.run_id,
            {
                "run_id": run.run_id,
                "status": "completed",
                "manifest_relative_path": manifest_relative,
                "otio_relative_path": published_relative,
                "sha256": sha256,
            },
        )
        conn.execute("BEGIN IMMEDIATE")
        repo.insert_otio_export_artifact(conn, artifact)
        repo.insert_reparse_report(conn, reparse.report, reparse_relative)
        run = run.model_copy(
            update={
                "status": OtioExportRunStatus.COMPLETED,
                "finished_at": _now(),
                "output_relative_path": published_relative,
                "otio_sha256": sha256,
                "relative_report_path": run_report_relative,
            }
        )
        repo.update_otio_export_run(conn, run)
        repo.mark_current_otio_export(
            conn,
            project_id=project.id,
            run_id=run.run_id,
            artifact_id=artifact.artifact_id,
            reparse_report_id=reparse.report.report_id,
        )
        repo.write_latest_otio_export_pointer(project.project_root_path, run, artifact)
        repo.write_latest_reparse_pointer(project.project_root_path, reparse.report)
        conn.commit()
        return OtioExportResult(True, "OTIO Export abgeschlossen.", run=run, artifact=artifact, reparse_report=reparse.report)
    except OtioExportServiceError as exc:
        conn.rollback()
        run = _fail_run(conn, run, code=exc.code)
        return OtioExportResult(False, "OTIO Export fehlgeschlagen.", run=run, error_code=exc.code)
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        run = _fail_run(conn, run, code=EXPORT_ERROR_OTIO_EXPORT_FAILED)
        return OtioExportResult(False, "OTIO Export fehlgeschlagen.", run=run, error_code=EXPORT_ERROR_OTIO_EXPORT_FAILED)
    finally:
        conn.close()


def process_otio_export_run(project_root: Path, run_id: str) -> None:
    root = Path(project_root).expanduser().resolve()
    conn = repo.open_export_registry(root)
    try:
        run = repo.get_otio_export_run(conn, run_id=run_id)
    finally:
        conn.close()
    if run is None:
        return
    from otio_app.discovery_v2.application.visual_edit_plan_service import _project_stub

    process_otio_export(_project_stub(root, run.project_id), run_id=run_id)


def _fail_run(conn, run: OtioExportRun | None, *, code: str) -> OtioExportRun | None:
    if run is None:
        return None
    failed = run.model_copy(
        update={
            "status": OtioExportRunStatus.FAILED,
            "finished_at": _now(),
            "error_code": code,
        }
    )
    repo.update_otio_export_run(conn, failed)
    conn.commit()
    return failed


__all__ = ["OtioExportResult", "OtioExportServiceError", "process_otio_export", "process_otio_export_run", "start_otio_export_run"]
