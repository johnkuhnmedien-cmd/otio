"""Finale Ansicht + Export der Übergabedatei für die spätere Schnittplan-Pipeline (Phase 7)."""

from __future__ import annotations

from otio_app.project_layout import get_confirmed_voiceover_project_plan_path
from otio_app.ui.voiceover_generation._shared import render_placeholder_page


def render_final_output_page() -> None:
    render_placeholder_page(
        title="⑦ Final Output",
        phase_hint="geplant für Phase 7",
        target_path_label="confirmed_voiceover_project_plan.json",
        target_path_fn=get_confirmed_voiceover_project_plan_path,
    )
