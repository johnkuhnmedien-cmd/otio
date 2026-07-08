"""Dramaturgieplanung über alle Ordner — Reihenfolge, Rollen, Bestätigung (Phase 3)."""

from __future__ import annotations

from otio_app.project_layout import get_dramaturgy_plan_draft_path
from otio_app.ui.voiceover_generation._shared import render_placeholder_page


def render_dramaturgy_page() -> None:
    render_placeholder_page(
        title="③ Dramaturgie",
        phase_hint="geplant für Phase 3",
        target_path_label="dramaturgy_plan.draft.json",
        target_path_fn=get_dramaturgy_plan_draft_path,
    )
