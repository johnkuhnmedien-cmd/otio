"""Discovery-V2 shot detection adapter: shot-detect-v1."""

from __future__ import annotations

import math
import re
from pathlib import Path

from otio_app.discovery_v2.adapters.ffmpeg_runner import (
    FFmpegRunnerError,
    run_ffmpeg,
)

SHOT_DETECT_PROFILE_VERSION = "shot-detect-v1"
SHOT_DETECT_PROFILE_NAME = SHOT_DETECT_PROFILE_VERSION
SHOT_DETECT_TIMEOUT_SEC = 1800
SCENE_CUT_THRESHOLD = 0.35
MIN_SHOT_SEGMENT_SECONDS = 0.40
MAX_SHOT_SEGMENT_SECONDS = 30.0
SHORT_VIDEO_SECONDS = 0.80
CUT_DEDUPE_SECONDS = 0.04

_PTS_TIME_RE = re.compile(r"\bpts_time:([+-]?(?:\d+(?:\.\d*)?|\.\d+))\b")


class ShotDetectError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def detect_scene_cut_seconds(
    working_media_path: Path | str,
    *,
    duration_seconds: float,
    timeout_sec: int = SHOT_DETECT_TIMEOUT_SEC,
) -> list[float]:
    """Detect scene-cut timestamps with FFmpeg's scene score filter.

    The FFmpeg invocation intentionally keeps source timing intact: no ``-r`` and
    no constant-frame-rate conversion are used.
    """
    duration = _finite_positive_duration(duration_seconds)
    path = Path(working_media_path)
    argv = [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-i",
        str(path),
        "-map",
        "0:v:0",
        "-an",
        "-vf",
        f"select='gt(scene,{SCENE_CUT_THRESHOLD:g})',showinfo",
        "-f",
        "null",
        "-",
    ]
    if "-r" in argv:
        raise ShotDetectError(
            "shot_detection_failed",
            "FFmpeg shot-detect argv darf keine Framerate erzwingen.",
        )

    try:
        result = run_ffmpeg(argv, timeout_sec=timeout_sec)
    except FFmpegRunnerError as exc:
        raise ShotDetectError("shot_detection_failed", exc.message) from exc

    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip()
        raise ShotDetectError(
            "shot_detection_failed",
            message or f"FFmpeg exit {result.returncode}",
        )

    return _parse_scene_cut_seconds(
        "\n".join(part for part in (result.stderr, result.stdout) if part),
        duration_seconds=duration,
    )


def normalize_shot_boundaries(
    duration_seconds: float,
    cut_seconds: list[float],
) -> list[tuple[float, float]]:
    """Normalize raw cut timestamps into Discovery-V2 shot boundaries."""
    duration = _finite_positive_duration(duration_seconds)
    cuts = _sorted_deduped_cuts(duration, cut_seconds)

    if duration < SHORT_VIDEO_SECONDS or not cuts:
        return _validate_boundaries(_split_long_segments([(0.0, duration)], duration))

    segments = _segments_from_cuts(duration, cuts)
    segments = _merge_short_segments(segments)
    segments = _split_long_segments(segments, duration)
    return _validate_boundaries(segments)


def _parse_scene_cut_seconds(text: str, *, duration_seconds: float) -> list[float]:
    values: list[float] = []
    for match in _PTS_TIME_RE.finditer(text):
        try:
            seconds = float(match.group(1))
        except ValueError:
            continue
        if math.isfinite(seconds) and 0.0 < seconds < duration_seconds:
            values.append(seconds)
    return _dedupe_sorted(values)


def _finite_positive_duration(duration_seconds: float) -> float:
    try:
        duration = float(duration_seconds)
    except (TypeError, ValueError) as exc:
        raise ShotDetectError(
            "invalid_shot_boundaries",
            f"Ungültige Videodauer: {duration_seconds!r}",
        ) from exc
    if not math.isfinite(duration) or duration <= 0.0:
        raise ShotDetectError(
            "invalid_shot_boundaries",
            f"Ungültige Videodauer: {duration_seconds!r}",
        )
    return duration


def _sorted_deduped_cuts(
    duration_seconds: float,
    cut_seconds: list[float],
) -> list[float]:
    valid: list[float] = []
    for raw in cut_seconds:
        try:
            cut = float(raw)
        except (TypeError, ValueError):
            continue
        # NaN/Infinity sind ungültige Grenzen und blockieren deterministisch.
        if not math.isfinite(cut):
            raise ShotDetectError(
                "invalid_shot_boundaries",
                f"Nicht-finite Cut-Zeit: {raw!r}",
            )
        # Werte außerhalb (0, duration) werden entfernt (nicht als Grenze genutzt).
        if 0.0 < cut < duration_seconds:
            valid.append(cut)
    return _dedupe_sorted(valid)


def _dedupe_sorted(values: list[float]) -> list[float]:
    deduped: list[float] = []
    for value in sorted(values):
        if deduped and abs(value - deduped[-1]) <= CUT_DEDUPE_SECONDS + 1e-9:
            continue
        deduped.append(value)
    return deduped


def _segments_from_cuts(
    duration_seconds: float,
    cuts: list[float],
) -> list[tuple[float, float]]:
    points = [0.0, *cuts, duration_seconds]
    return [(points[index], points[index + 1]) for index in range(len(points) - 1)]


def _merge_short_segments(
    segments: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    merged = list(segments)
    while len(merged) > 1:
        short_index = next(
            (
                index
                for index, (start, end) in enumerate(merged)
                if end - start < MIN_SHOT_SEGMENT_SECONDS
            ),
            None,
        )
        if short_index is None:
            break

        if short_index == 0:
            start = merged[0][0]
            end = merged[1][1]
            merged[0:2] = [(start, end)]
        else:
            start = merged[short_index - 1][0]
            end = merged[short_index][1]
            merged[short_index - 1 : short_index + 1] = [(start, end)]
    return merged


def _split_long_segments(
    segments: list[tuple[float, float]],
    duration_seconds: float,
) -> list[tuple[float, float]]:
    split: list[tuple[float, float]] = []
    for start, end in segments:
        segment_duration = end - start
        if segment_duration <= MAX_SHOT_SEGMENT_SECONDS:
            split.append((start, end))
            continue

        count = int(math.ceil(segment_duration / MAX_SHOT_SEGMENT_SECONDS))
        step = segment_duration / count
        current = start
        for index in range(count):
            next_end = end if index == count - 1 else start + step * (index + 1)
            split.append((current, next_end))
            current = next_end

    if split:
        split[0] = (0.0, split[0][1])
        split[-1] = (split[-1][0], duration_seconds)
    return split


def _validate_boundaries(
    segments: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    if not segments:
        raise ShotDetectError("invalid_shot_boundaries", "Keine Shot-Segmente erzeugt.")

    previous_end: float | None = None
    for index, (start, end) in enumerate(segments):
        if not all(math.isfinite(value) for value in (start, end)):
            raise ShotDetectError(
                "invalid_shot_boundaries",
                "Shot-Grenzen enthalten nicht-finite Werte.",
            )
        if end <= start:
            raise ShotDetectError(
                "invalid_shot_boundaries",
                f"Ungültiges Shot-Segment: {start}..{end}",
            )
        if end - start > MAX_SHOT_SEGMENT_SECONDS + 1e-6:
            raise ShotDetectError(
                "invalid_shot_boundaries",
                f"Shot-Segment überschreitet {MAX_SHOT_SEGMENT_SECONDS}s.",
            )
        if index == 0 and abs(start) > 1e-9:
            raise ShotDetectError(
                "invalid_shot_boundaries",
                "Erstes Shot-Segment startet nicht bei 0.",
            )
        if previous_end is not None and abs(start - previous_end) > 1e-6:
            raise ShotDetectError(
                "invalid_shot_boundaries",
                "Shot-Segmente sind nicht lückenlos.",
            )
        previous_end = end
    return segments
