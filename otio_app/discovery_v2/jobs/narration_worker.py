"""Worker dispatch for Discovery V2 Phase 11 narration jobs."""

from __future__ import annotations

from pathlib import Path


def process_narration_run(project_root: Path, run_id: str, *, worker: str) -> None:
    if worker == "narration_voice":
        from otio_app.discovery_v2.application.voice_generation_service import (
            process_voice_generation_run,
        )

        process_voice_generation_run(project_root, run_id)
        return
    if worker == "narration_pause":
        from otio_app.discovery_v2.application.pause_direction_service import (
            process_pause_direction_run,
        )

        process_pause_direction_run(project_root, run_id)
        return
    if worker == "narration_timing":
        from otio_app.discovery_v2.application.narration_timing_service import (
            process_narration_timing_run,
        )

        process_narration_timing_run(project_root, run_id)
        return
    raise ValueError(f"Unsupported narration worker: {worker}")


__all__ = ["process_narration_run"]
