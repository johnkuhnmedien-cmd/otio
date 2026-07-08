"""Voice-over pro Ordner: Settings, Generierung, Review-Loop, Bestätigung (Phase 4)."""

from __future__ import annotations

from otio_app.project_layout import get_folder_voiceovers_draft_path
from otio_app.ui.voiceover_generation._shared import render_placeholder_page


def render_folder_voiceovers_page() -> None:
    render_placeholder_page(
        title="④ Folder Voice-overs",
        phase_hint="geplant für Phase 4 (Autor + Review-/Correction-Loop)",
        target_path_label="folder_voiceovers.draft.json",
        target_path_fn=get_folder_voiceovers_draft_path,
    )
