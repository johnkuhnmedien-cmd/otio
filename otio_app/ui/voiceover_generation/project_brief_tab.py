"""Project Brief — Video-Titel, Sprache, Ton, globale Negativregeln (Phase 2)."""

from __future__ import annotations

from otio_app.project_layout import get_project_brief_path
from otio_app.ui.voiceover_generation._shared import render_placeholder_page


def render_project_brief_page() -> None:
    render_placeholder_page(
        title="① Project Brief",
        phase_hint="geplant für Phase 2",
        target_path_label="project_brief.json",
        target_path_fn=get_project_brief_path,
    )
