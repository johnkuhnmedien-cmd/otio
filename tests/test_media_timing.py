"""Tests für Medien-Timecode/PTS-Erkennung."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from otio_app.services.media_utils import (
    MediaTiming,
    parse_r_frame_rate,
    parse_smpte_timecode,
    probe_media_timing,
)


def test_parse_smpte_timecode_at_25fps() -> None:
    assert parse_smpte_timecode("00:00:15:01", 25.0) == 15.04
    assert parse_smpte_timecode("00:00:00:00", 25.0) == 0.0


def test_parse_r_frame_rate() -> None:
    assert parse_r_frame_rate("25/1") == 25.0
    assert parse_r_frame_rate("24000/1001") is not None


def test_probe_media_timing_uses_embedded_timecode(tmp_path: Path) -> None:
    media = tmp_path / "Arches_National_Park_Asset03.mp4"
    media.write_bytes(b"fake")

    payload = {
        "format": {"duration": "15.0", "tags": {"timecode": "00:00:15:01"}},
        "streams": [
            {
                "codec_type": "video",
                "r_frame_rate": "25/1",
                "start_time": "15.04",
                "tags": {"timecode": "00:00:15:01"},
            }
        ],
    }

    with patch(
        "otio_app.services.media_utils.subprocess.run",
        return_value=type("R", (), {"returncode": 0, "stdout": __import__("json").dumps(payload).encode()})(),
    ):
        timing = probe_media_timing(media, default_rate=25.0)

    assert timing.start_sec == 15.04
    assert timing.duration_sec == 15.0
    assert timing.rate == 25.0
