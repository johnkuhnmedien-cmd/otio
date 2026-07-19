"""Worker dispatch for Discovery V2 Phase 12 visual edit jobs."""

from __future__ import annotations

from pathlib import Path


def process_visual_edit_run(project_root: Path, run_id: str, *, worker: str) -> None:
    if worker == "visual_edit_plan":
        from otio_app.discovery_v2.application.visual_edit_plan_service import (
            process_visual_edit_plan_run,
        )

        process_visual_edit_plan_run(project_root, run_id)
        return
    if worker == "humanity_review":
        from otio_app.discovery_v2.application.humanity_review_service import (
            process_humanity_review_run,
        )

        process_humanity_review_run(project_root, run_id)
        return
    if worker == "feasibility_check":
        from otio_app.discovery_v2.application.feasibility_service import (
            process_feasibility_check_run,
        )

        process_feasibility_check_run(project_root, run_id)
        return
    raise ValueError(f"Unsupported visual edit worker: {worker}")


__all__ = ["process_visual_edit_run"]
