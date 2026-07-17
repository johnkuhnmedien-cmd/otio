"""Worker dispatch for Discovery V2 Phase 13 export jobs."""

from __future__ import annotations

from pathlib import Path


def process_export_run(project_root: Path, run_id: str, *, worker: str) -> None:
    if worker == "otio_export":
        from otio_app.discovery_v2.application.otio_export_service import process_otio_export_run

        process_otio_export_run(project_root, run_id)
        return
    raise ValueError(f"Unsupported export worker: {worker}")


__all__ = ["process_export_run"]
