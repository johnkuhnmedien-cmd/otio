"""Tests für Abbruch der Voice-over-Analyse."""

from __future__ import annotations

from pathlib import Path

import pytest

from otio_app.defaults import VOICE_BACKEND_WHISPER
from otio_app.models import Project
from otio_app.services.voice_analyzer import analyze_voice_over


def _sample_project(layout: dict[str, Path]) -> Project:
    return Project(
        id="test-project",
        name="Test",
        project_root=str(layout["project_root"]),
        work_dir=str(layout["work_dir"]),
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )


def test_analyze_voice_over_can_be_cancelled(
    temp_project_layout: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    voice_dir = temp_project_layout["voice_over_dir"]
    (voice_dir / "part2.wav").write_bytes(b"wav2")

    calls: list[str] = []

    def fake_transcribe(
        audio_path: Path,
        language: str,
        *,
        model_size: str | None = None,
    ) -> list[dict[str, float | str]]:
        calls.append(audio_path.name)
        return [{"start_sec": 0.0, "end_sec": 1.0, "text": "Test"}]

    def should_cancel() -> bool:
        return len(calls) >= 1

    monkeypatch.setattr(
        "otio_app.services.voice_analyzer.transcribe_audio_file",
        fake_transcribe,
    )

    project = _sample_project(temp_project_layout)
    phases: list[str] = []

    def on_progress(phase: str, _data: dict) -> None:
        phases.append(phase)

    _, report = analyze_voice_over(
        project,
        use_api=True,
        backend=VOICE_BACKEND_WHISPER,
        whisper_model="small",
        on_progress=on_progress,
        should_cancel=should_cancel,
    )

    assert len(calls) == 1
    assert report.cancelled is True
    assert report.output_written is True
    assert project.voice_analysis_path.is_file()
    assert "cancelled" in phases
