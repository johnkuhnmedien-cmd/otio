"""Tests für Medien-Timecode/PTS-Erkennung."""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path
from unittest.mock import patch

from otio_app.services.media_utils import (
    MediaTiming,
    parse_r_frame_rate,
    parse_smpte_timecode,
    probe_media_timing,
    smpte_ndf_frames,
    smpte_nominal_fps,
)


def test_parse_smpte_timecode_at_25fps() -> None:
    assert parse_smpte_timecode("00:00:15:01", 25.0) == 15.04
    assert parse_smpte_timecode("00:00:00:00", 25.0) == 0.0


def test_parse_smpte_timecode_ntsc_23_976_uses_nominal_24_ndf() -> None:
    """01:00:00:00 @23.976 ist Frame 86400, nicht 3600 Wall-Clock-Sekunden."""
    rate = float(Fraction(24000, 1001))
    assert smpte_nominal_fps(rate) == 24
    assert smpte_ndf_frames(1, 0, 0, 0, nominal=24) == 86400
    parsed = parse_smpte_timecode("01:00:00:00", rate)
    assert parsed is not None
    assert abs(parsed - (86400 / rate)) < 1e-9
    assert abs(parsed - 3603.6) < 1e-6
    # Alte Wall-Clock-Formel wäre 3600s — genau der Resolve-Drift.
    assert abs(parsed - 3600.0) > 3.0


def test_parse_smpte_timecode_ntsc_29_97_ndf_hour() -> None:
    rate = float(Fraction(30000, 1001))
    parsed = parse_smpte_timecode("01:00:00:00", rate)
    assert parsed is not None
    assert abs(parsed - (108000 / rate)) < 1e-9


def test_yosemite_asset05_offline_head_regression_math() -> None:
    """Regression: erste ~2.5s Media Offline bei Yosemite_Asset05.

    OTIO hatte available_start=3600s (falsch) und source=3601.152s.
    Resolve mappt Datei-TC 01:00:00:00 @23.976 auf Frame 86400.
    Source-Frame lag ~59 Frames davor → ~2.45s Offline-Kopf.
    """
    rate = float(Fraction(24000, 1001))
    wrong_avail_sec = 3600.0
    content_offset = 1.151979
    wrong_source_sec = wrong_avail_sec + content_offset
    wrong_source_frames = wrong_source_sec * rate
    resolve_avail_frames = 86400
    offline_sec = (resolve_avail_frames - wrong_source_frames) / rate
    assert 2.4 < offline_sec < 2.6

    fixed_avail_sec = parse_smpte_timecode("01:00:00:00", rate)
    assert fixed_avail_sec is not None
    fixed_source_sec = fixed_avail_sec + content_offset
    fixed_source_frames = fixed_source_sec * rate
    assert fixed_source_frames >= resolve_avail_frames - 1e-6
    assert abs((fixed_source_frames - resolve_avail_frames) / rate - content_offset) < 1e-9


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


def test_probe_media_timing_yosemite_asset05_23_976_ndf(tmp_path: Path) -> None:
    """Echte ffprobe-Struktur von Yosemite_Asset05: tmcd+video TC 01:00:00:00 @23.976."""
    media = tmp_path / "Yosemite_Asset05.mp4"
    media.write_bytes(b"fake")
    payload = {
        "streams": [
            {
                "index": 0,
                "codec_type": "video",
                "codec_tag_string": "avc1",
                "r_frame_rate": "24000/1001",
                "avg_frame_rate": "24000/1001",
                "start_time": "0.000000",
                "tags": {"timecode": "01:00:00:00"},
            },
            {
                "index": 1,
                "codec_type": "data",
                "codec_tag_string": "tmcd",
                "r_frame_rate": "0/0",
                "avg_frame_rate": "24000/1001",
                "start_time": "0.000000",
                "tags": {"timecode": "01:00:00:00"},
            },
        ],
        "format": {"duration": "12.303958", "tags": {}},
    }
    with patch(
        "otio_app.services.media_utils.subprocess.run",
        return_value=type(
            "R", (), {"returncode": 0, "stdout": __import__("json").dumps(payload).encode()}
        )(),
    ):
        timing = probe_media_timing(media, default_rate=25.0)

    rate = float(Fraction(24000, 1001))
    assert abs(timing.rate - rate) < 1e-9
    assert timing.duration_sec == 12.303958
    assert timing.start_sec is not None
    assert abs(timing.start_sec - 3603.6) < 1e-6


def test_probe_media_timing_prefers_tmcd_over_format_tags(tmp_path: Path) -> None:
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"fake")
    payload = {
        "format": {"duration": "10.0", "tags": {"timecode": "00:00:00:00"}},
        "streams": [
            {"codec_type": "video", "r_frame_rate": "25/1", "start_time": "0.0"},
            {
                "codec_type": "data",
                "codec_tag_string": "tmcd",
                "tags": {"timecode": "01:00:00:00"},
            },
        ],
    }
    with patch(
        "otio_app.services.media_utils.subprocess.run",
        return_value=type(
            "R", (), {"returncode": 0, "stdout": __import__("json").dumps(payload).encode()}
        )(),
    ):
        timing = probe_media_timing(media, default_rate=25.0)
    assert timing.start_sec == 3600.0
