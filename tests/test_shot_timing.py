"""Tests für Shot-Timing und Ausklingen."""

from __future__ import annotations

from otio_app.analysis_models import EditPlanShot
from otio_app.services.shot_timing import append_folder_outro_shot


def test_append_folder_outro_shot_adds_marked_shot() -> None:
    shots = [
        EditPlanShot(
            voice_file="/voice.wav",
            folder="Canyon",
            voice_start_sec=0.0,
            voice_end_sec=6.0,
            duration_sec=6.0,
            asset_path="/a.mp4",
            motif="main",
            passage_text="text",
        )
    ]
    append_folder_outro_shot(
        shots,
        folder_name="Canyon",
        voice_file="/voice.wav",
        outro_sec=5.0,
        max_sec=8.0,
    )
    assert len(shots) == 2
    outro = shots[-1]
    assert outro.section_outro is True
    assert outro.duration_sec == 5.0
    assert outro.voice_start_sec == 6.0
    assert outro.motif == "Ausklingen"
