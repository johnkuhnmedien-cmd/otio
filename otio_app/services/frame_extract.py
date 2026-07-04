"""Frame-Extraktion mit FFmpeg (Originale werden nicht verändert)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from otio_app.services.media_utils import IMAGE_EXTENSIONS, probe_duration_seconds


def extract_frames(
    media_path: Path,
    output_dir: Path,
    count: int,
) -> list[Path]:
    """Extrahiert count Frames aus Video oder kopiert Bild-Referenzen."""
    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = media_path.suffix.lower()

    if suffix in IMAGE_EXTENSIONS:
        target = output_dir / f"frame_001{suffix}"
        if not target.exists():
            target.write_bytes(media_path.read_bytes())
        return [target]

    duration = probe_duration_seconds(media_path)
    if duration is None or duration <= 0:
        timestamps = [0.0]
    else:
        if count == 1:
            timestamps = [duration / 2]
        else:
            step = duration / (count + 1)
            timestamps = [step * (index + 1) for index in range(count)]

    frames: list[Path] = []
    for index, timestamp in enumerate(timestamps, start=1):
        frame_path = output_dir / f"frame_{index:03d}.jpg"
        result = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                f"{timestamp:.3f}",
                "-i",
                str(media_path),
                "-frames:v",
                "1",
                "-q:v",
                "2",
                str(frame_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0 and frame_path.is_file():
            frames.append(frame_path)
    return frames
