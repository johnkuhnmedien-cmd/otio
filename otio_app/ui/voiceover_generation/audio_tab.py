"""ElevenLabs-Vertonung pro Ordner, Manifest, Alignment, Re-TTS-Versionierung (Phase 6)."""

from __future__ import annotations

from otio_app.project_layout import get_voiceover_audio_manifest_path
from otio_app.ui.voiceover_generation._shared import render_placeholder_page


def render_audio_page() -> None:
    render_placeholder_page(
        title="⑥ Audio / ElevenLabs",
        phase_hint="geplant für Phase 6 (Batch-Job-Fortschritt, Kosten-Transparenz vor TTS)",
        target_path_label="voiceover_audio_manifest.json",
        target_path_fn=get_voiceover_audio_manifest_path,
    )
