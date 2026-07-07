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


def test_probe_media_timing_finds_timecode_on_dedicated_tmcd_stream(tmp_path: Path) -> None:
    """Regression: Professionelle Kameras (Canon, Sony, ...) speichern den
    SMPTE-Timecode oft NICHT auf der Video-/Audiospur, sondern auf einer
    eigenen 'tmcd'-Datenspur (codec_type=data). Die Suche nach dem
    timecode-Tag war vorher auf video/audio-Streams beschränkt und hat
    diese Spur ignoriert — start_sec wurde dadurch fälschlich als 0.0
    angenommen. DaVinci Resolve erkennt beim Reconnect aber den echten,
    von Null abweichenden Timecode der Datei und meldet 'Media Offline' /
    einen Timecode-Mismatch beim OTIO-Import ('No overlap between
    specified target timecodes and located file timecodes')."""
    media = tmp_path / "Bisti_De_Na_Zin_Wilderness_Asset04.mp4"
    media.write_bytes(b"fake")

    payload = {
        "format": {"duration": "22.13", "tags": {}},
        "streams": [
            {
                "codec_type": "video",
                "r_frame_rate": "30000/1001",
                "start_time": "0.000000",
                "tags": {},
            },
            {
                "codec_type": "audio",
                "r_frame_rate": "0/0",
                "start_time": "0.000000",
                "tags": {},
            },
            {
                "codec_type": "data",
                "codec_tag_string": "tmcd",
                "tags": {"timecode": "06:56:38:03"},
            },
        ],
    }

    with patch(
        "otio_app.services.media_utils.subprocess.run",
        return_value=type("R", (), {"returncode": 0, "stdout": __import__("json").dumps(payload).encode()})(),
    ):
        timing = probe_media_timing(media, default_rate=25.0)

    rate = parse_r_frame_rate("30000/1001")
    expected = parse_smpte_timecode("06:56:38:03", rate)
    assert timing.start_sec is not None
    assert abs(timing.start_sec - expected) < 0.001
    assert timing.start_sec > 0, "Timecode auf tmcd-Spur wurde nicht erkannt (start_sec blieb 0.0)"


def test_probe_media_timing_prefers_tmcd_track_over_format_absence(tmp_path: Path) -> None:
    """Ohne format-level Timecode-Tag UND ohne Video-Stream-Tag muss der
    Wert von der tmcd-Spur übernommen werden, nicht auf 0.0 zurückfallen."""
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"fake")

    payload = {
        "format": {"duration": "10.0"},
        "streams": [
            {"codec_type": "video", "r_frame_rate": "25/1", "start_time": "0.0"},
            {"codec_tag_string": "tmcd", "tags": {"timecode": "01:00:00:00"}},
        ],
    }

    with patch(
        "otio_app.services.media_utils.subprocess.run",
        return_value=type("R", (), {"returncode": 0, "stdout": __import__("json").dumps(payload).encode()})(),
    ):
        timing = probe_media_timing(media, default_rate=25.0)

    assert timing.start_sec == 3600.0
