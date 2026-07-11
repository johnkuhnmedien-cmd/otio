"""Tests für Abbruch während Frame-Extraktion."""

from __future__ import annotations

from pathlib import Path

import pytest

from otio_app.services.analysis_cancel import AnalysisCancelledError
from otio_app.services.frame_extract import extract_frames


def test_extract_frames_raises_when_cancelled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    media_path = tmp_path / "clip.mp4"
    media_path.write_bytes(b"video")
    output_dir = tmp_path / "frames"
    calls = {"count": 0}

    def fake_extract_at(*_args, **_kwargs) -> Path | None:
        calls["count"] += 1
        return None

    monkeypatch.setattr(
        "otio_app.services.frame_extract.probe_duration_seconds",
        lambda _path: 120.0,
    )
    monkeypatch.setattr(
        "otio_app.services.frame_extract._extract_frame_at",
        fake_extract_at,
    )

    def should_cancel() -> bool:
        return calls["count"] >= 1

    with pytest.raises(AnalysisCancelledError):
        extract_frames(media_path, output_dir, 3, should_cancel=should_cancel)

    assert calls["count"] == 1
