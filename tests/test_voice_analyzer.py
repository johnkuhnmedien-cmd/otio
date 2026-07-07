"""Tests für Voice-over-Analyse."""

from __future__ import annotations

from pathlib import Path

import pytest

from otio_app.defaults import VOICE_BACKEND_GEMINI, VOICE_BACKEND_WHISPER
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


def test_analyze_voice_over_uses_whisper_backend(
    temp_project_layout: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_transcribe(
        audio_path: Path,
        language: str,
        *,
        model_size: str | None = None,
    ) -> list[dict[str, float | str]]:
        calls.append(audio_path.name)
        return [
            {
                "start_sec": 0.0,
                "end_sec": 2.5,
                "text": "Hallo Florida Keys.",
            }
        ]

    monkeypatch.setattr(
        "otio_app.services.voice_analyzer.transcribe_audio_file",
        fake_transcribe,
    )

    project = _sample_project(temp_project_layout)
    document, report = analyze_voice_over(
        project,
        use_api=True,
        backend=VOICE_BACKEND_WHISPER,
        whisper_model="small",
    )

    assert calls == ["voiceover.wav"]
    assert document.files[0].segments[0].text == "Hallo Florida Keys."
    assert report.files_analyzed == 1


def test_analyze_voice_over_whisper_cache_skips_second_run(
    temp_project_layout: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_transcribe(
        audio_path: Path,
        language: str,
        *,
        model_size: str | None = None,
    ) -> list[dict[str, float | str]]:
        calls.append(audio_path.name)
        return [{"start_sec": 0.0, "end_sec": 1.0, "text": "Test"}]

    monkeypatch.setattr(
        "otio_app.services.voice_analyzer.transcribe_audio_file",
        fake_transcribe,
    )

    project = _sample_project(temp_project_layout)
    analyze_voice_over(
        project,
        use_api=True,
        backend=VOICE_BACKEND_WHISPER,
        whisper_model="small",
    )
    analyze_voice_over(
        project,
        use_api=True,
        backend=VOICE_BACKEND_WHISPER,
        whisper_model="small",
    )

    assert calls == ["voiceover.wav"]


def test_analyze_voice_over_gemini_backend(
    temp_project_layout: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_gemini(
        audio_path: Path,
        language: str,
        *,
        model: str | None = None,
    ) -> dict:
        return {
            "segments": [
                {"start_sec": 0.0, "end_sec": 3.0, "text": "Gemini Transkript."}
            ]
        }

    monkeypatch.setattr(
        "otio_app.services.voice_analyzer.analyze_voice_over_file",
        fake_gemini,
    )

    project = _sample_project(temp_project_layout)
    document, _ = analyze_voice_over(
        project,
        use_api=True,
        backend=VOICE_BACKEND_GEMINI,
        model="gemini-3.1-flash-lite",
    )

    assert document.files[0].segments[0].text == "Gemini Transkript."
