"""Tests für System-Checks."""

from __future__ import annotations

import subprocess

from otio_app.system_checks import (
    CheckResult,
    check_ffmpeg,
    check_python,
    run_all_checks,
)


def test_check_python() -> None:
    result = check_python()
    assert isinstance(result, CheckResult)
    assert result.name == "Python"
    assert result.ok is True
    assert result.version is not None


def test_check_ffmpeg_not_found(monkeypatch) -> None:
    def fake_run(*_args, **_kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = check_ffmpeg()
    assert result.ok is False
    assert "nicht gefunden" in result.message


def test_run_all_checks_returns_five() -> None:
    results = run_all_checks()
    assert len(results) == 5
    names = {r.name for r in results}
    assert names == {
        "Python",
        "FFmpeg",
        "ffprobe",
        "OpenTimelineIO",
        "Whisper (faster-whisper)",
    }
