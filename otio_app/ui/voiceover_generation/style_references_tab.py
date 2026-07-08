"""Style References & Style Profile — Beispielskripte, abgeleiteter Stil (Phase 2)."""

from __future__ import annotations

from otio_app.project_layout import get_voiceover_style_references_path
from otio_app.ui.voiceover_generation._shared import render_placeholder_page


def render_style_references_page() -> None:
    render_placeholder_page(
        title="② Style References",
        phase_hint="geplant für Phase 2 (inkl. „Style Profile erstellen“)",
        target_path_label="voiceover_style_references.json",
        target_path_fn=get_voiceover_style_references_path,
    )
