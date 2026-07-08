"""Intro-Hook aus allen Ordner-Voice-overs — Top-5-Vorschläge, Bestätigung (Phase 5)."""

from __future__ import annotations

from otio_app.project_layout import get_intro_hook_candidates_path
from otio_app.ui.voiceover_generation._shared import render_placeholder_page


def render_intro_page() -> None:
    render_placeholder_page(
        title="⑤ Intro",
        phase_hint="geplant für Phase 5",
        target_path_label="intro_hook_candidates.json",
        target_path_fn=get_intro_hook_candidates_path,
    )
