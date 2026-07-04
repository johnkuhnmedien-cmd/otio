"""Frame-Extraktion mit FFmpeg (Originale werden nicht verändert)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from otio_app.services.media_utils import IMAGE_EXTENSIONS, probe_duration_seconds

_FALLBACK_OFFSETS_SEC = (0.0, 2.0, 5.0, 10.0, 20.0, 40.0, 60.0)


def compute_frame_timestamps(duration: float | None, count: int) -> list[float]:
    """Zeitpunkte für die Frame-Extraktion (robust bei unbekannter Dauer / iCloud)."""
    if count <= 0:
        return []

    if duration is None or duration <= 0:
        return [_FALLBACK_OFFSETS_SEC[index] for index in range(min(count, len(_FALLBACK_OFFSETS_SEC)))]

    if count == 1:
        return [duration / 2]

    step = duration / (count + 1)
    return [step * (index + 1) for index in range(count)]


def _extract_frame_at(
    media_path: Path,
    output_dir: Path,
    *,
    timestamp: float,
    frame_index: int,
) -> Path | None:
    frame_path = output_dir / f"frame_{frame_index:03d}.jpg"
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
    if result.returncode == 0 and frame_path.is_file() and frame_path.stat().st_size > 0:
        return frame_path
    try:
        frame_path.unlink(missing_ok=True)
    except OSError:
        pass
    return None


def extract_frames(
    media_path: Path,
    output_dir: Path,
    count: int,
) -> list[Path]:
    """Extrahiert count Frames aus Video oder kopiert Bild-Referenzen."""
    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = media_path.suffix.lower()
    per_file = max(1, count)

    if suffix in IMAGE_EXTENSIONS:
        target = output_dir / f"frame_001{suffix}"
        if not target.exists():
            target.write_bytes(media_path.read_bytes())
        return [target]

    duration = probe_duration_seconds(media_path)
    timestamps = compute_frame_timestamps(duration, per_file)
    frames: list[Path] = []
    used_timestamps: set[float] = set()

    for index, timestamp in enumerate(timestamps, start=1):
        used_timestamps.add(timestamp)
        frame_path = _extract_frame_at(
            media_path,
            output_dir,
            timestamp=timestamp,
            frame_index=index,
        )
        if frame_path is not None:
            frames.append(frame_path)

    if len(frames) < per_file:
        next_index = len(frames) + 1
        for offset in _FALLBACK_OFFSETS_SEC:
            if len(frames) >= per_file:
                break
            if offset in used_timestamps:
                continue
            used_timestamps.add(offset)
            frame_path = _extract_frame_at(
                media_path,
                output_dir,
                timestamp=offset,
                frame_index=next_index,
            )
            if frame_path is not None:
                frames.append(frame_path)
                next_index += 1

    return frames
