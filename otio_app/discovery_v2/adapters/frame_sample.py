"""Discovery-V2 representative frame sampling adapter: frame-sample-v1."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError

from otio_app.discovery_v2.adapters.ffmpeg_runner import (
    FFmpegRunnerError,
    run_ffmpeg,
)
from otio_app.discovery_v2.adapters.source_hash import compute_sha256_hex

FRAME_SAMPLE_PROFILE_VERSION = "frame-sample-v1"
FRAME_SAMPLE_PROFILE_NAME = FRAME_SAMPLE_PROFILE_VERSION
MAX_FRAMES_PER_VIDEO = 24
EDGE_MARGIN_SECONDS = 0.08
OVERVIEW_NEAR_EXISTING_SECONDS = 0.10
MAX_FRAME_LONG_EDGE = 1280
VIDEO_FRAME_TIMEOUT_SEC = 120


class FrameSampleError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class StillPreviewResult:
    output_path: Path
    output_format: str
    output_sha256: str
    width: int
    height: int
    has_alpha: bool


def select_representative_timestamps(
    shots: list[object],
) -> list[tuple[object | None, float]]:
    """Select at most 24 representative timestamps for the provided shots."""
    if not shots:
        return []

    sorted_shots = sorted(shots, key=_shot_ordinal)
    if len(sorted_shots) > MAX_FRAMES_PER_VIDEO:
        longest = sorted(
            sorted_shots,
            key=lambda shot: (-_shot_duration(shot), _shot_ordinal(shot)),
        )[:MAX_FRAMES_PER_VIDEO]
        return [
            (shot, representative_timestamp_for_shot(shot))
            for shot in sorted(longest, key=_shot_ordinal)
        ]

    selections: list[tuple[object | None, float]] = [
        (shot, representative_timestamp_for_shot(shot)) for shot in sorted_shots
    ]
    if len(sorted_shots) == MAX_FRAMES_PER_VIDEO:
        return selections

    duration = max(_shot_end(shot) for shot in sorted_shots)
    if math.isfinite(duration) and duration > 0.0:
        overview = duration / 2.0
        existing = [timestamp for _, timestamp in selections]
        if all(
            abs(overview - timestamp) > OVERVIEW_NEAR_EXISTING_SECONDS
            for timestamp in existing
        ):
            selections.append((None, overview))
    return selections


def representative_timestamp_for_shot(shot: object) -> float:
    start, end = _shot_start_end(shot)
    midpoint = start + (end - start) / 2.0
    return _clamp_with_edge_margin(midpoint, start=start, end=end)


def black_frame_candidate_timestamps(shot: object) -> list[float]:
    """Return candidate timestamps in midpoint, before-mid, after-mid order."""
    start, end = _shot_start_end(shot)
    duration = max(0.0, end - start)
    midpoint = representative_timestamp_for_shot(shot)
    delta = min(0.25, 0.2 * duration)
    ordered = [
        _clamp_inside_shot(midpoint, start=start, end=end),
        _clamp_inside_shot(midpoint - delta, start=start, end=end),
        _clamp_inside_shot(midpoint + delta, start=start, end=end),
    ]
    deduped: list[float] = []
    for value in ordered:
        if deduped and abs(value - deduped[-1]) <= 1e-9:
            continue
        if any(abs(value - existing) <= 1e-9 for existing in deduped):
            continue
        deduped.append(value)
    return deduped


def extract_video_frame_jpeg(
    input_path: Path | str,
    output_path: Path | str,
    timestamp: float,
    *,
    rotation_degrees: float | None = None,
    source_probe: object | None = None,
    timeout_sec: int = VIDEO_FRAME_TIMEOUT_SEC,
) -> list[str]:
    """Extract one visually upright JPEG frame and return the FFmpeg argv used."""
    output = Path(output_path)
    argv = build_extract_video_frame_jpeg_argv(
        input_path=Path(input_path),
        output_path=output,
        timestamp=timestamp,
        rotation_degrees=rotation_degrees,
        source_probe=source_probe,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = run_ffmpeg(argv, timeout_sec=timeout_sec)
    except FFmpegRunnerError as exc:
        raise FrameSampleError("frame_extraction_failed", exc.message) from exc

    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip()
        raise FrameSampleError(
            "frame_extraction_failed",
            message or f"FFmpeg exit {result.returncode}",
        )
    if not output.is_file() or output.stat().st_size < 1:
        raise FrameSampleError(
            "frame_extraction_failed",
            "FFmpeg lieferte kein JPEG-Frame.",
        )
    return result.argv


def build_extract_video_frame_jpeg_argv(
    *,
    input_path: Path,
    output_path: Path,
    timestamp: float,
    rotation_degrees: float | None = None,
    source_probe: object | None = None,
) -> list[str]:
    seconds = _finite_timestamp(timestamp)
    rotation = _resolve_rotation_degrees(
        rotation_degrees=rotation_degrees,
        source_probe=source_probe,
    )
    filters = [*_rotation_filters(rotation), _scale_filter()]
    argv = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-nostdin",
        "-y",
        "-noautorotate",
        "-ss",
        _format_seconds(seconds),
        "-i",
        str(input_path),
        "-frames:v",
        "1",
        "-q:v",
        "2",
        "-vf",
        ",".join(filters),
        str(output_path),
    ]
    if "-r" in argv:
        raise FrameSampleError(
            "frame_extraction_failed",
            "FFmpeg frame-sample argv darf keine Framerate erzwingen.",
        )
    return argv


def prepare_still_preview(
    input_path: Path | str,
    output_path: Path | str,
    *,
    max_long_edge: int = MAX_FRAME_LONG_EDGE,
) -> StillPreviewResult:
    """Prepare an image preview with Pillow, preserving alpha as PNG."""
    if max_long_edge <= 0:
        raise FrameSampleError(
            "still_preview_failed",
            f"Ungültige maximale Kantenlänge: {max_long_edge}",
        )

    source = Path(input_path)
    requested_output = Path(output_path)
    try:
        with Image.open(source) as image:
            working = ImageOps.exif_transpose(image)
            if working is None:
                raise FrameSampleError(
                    "still_preview_failed",
                    "EXIF-Transpose lieferte kein Bild.",
                )
            working.load()
            _resize_without_upscaling(working, max_long_edge=max_long_edge)
            has_alpha = _image_has_alpha(working)
            if has_alpha:
                encoded = working.convert("RGBA")
                output_format = "PNG"
                final_output = _output_path_for_format(requested_output, ".png")
                save_kwargs: dict[str, object] = {"format": "PNG"}
            else:
                encoded = working.convert("RGB")
                output_format = "JPEG"
                final_output = _output_path_for_format(requested_output, ".jpg")
                save_kwargs = {"format": "JPEG", "quality": 90, "optimize": False}

            final_output.parent.mkdir(parents=True, exist_ok=True)
            encoded.save(final_output, **save_kwargs)
    except FrameSampleError:
        raise
    except UnidentifiedImageError as exc:
        raise FrameSampleError(
            "still_preview_failed",
            f"Bild konnte nicht gelesen werden: {exc}",
        ) from exc
    except OSError as exc:
        raise FrameSampleError(
            "still_preview_failed",
            f"Still-Preview fehlgeschlagen: {exc}",
        ) from exc

    try:
        output_sha = compute_sha256_hex(final_output)
    except OSError as exc:
        raise FrameSampleError("still_preview_failed", str(exc)) from exc

    return StillPreviewResult(
        output_path=final_output,
        output_format=output_format,
        output_sha256=output_sha,
        width=encoded.size[0],
        height=encoded.size[1],
        has_alpha=has_alpha,
    )


def _shot_value(shot: object, *names: str) -> Any:
    last_exc: Exception | None = None
    for name in names:
        try:
            if isinstance(shot, dict):
                if name in shot:
                    return shot[name]
                raise KeyError(name)
            return getattr(shot, name)
        except (AttributeError, KeyError) as exc:
            last_exc = exc
            continue
    if last_exc is not None:
        raise last_exc
    raise KeyError(names[0] if names else "value")


def _shot_ordinal(shot: object) -> int:
    try:
        return int(_shot_value(shot, "ordinal"))
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise FrameSampleError(
            "invalid_shot",
            f"Shot ohne gültigen ordinal-Wert: {shot!r}",
        ) from exc


def _shot_start(shot: object) -> float:
    return _finite_shot_number(shot, "start_seconds", "start")


def _shot_end(shot: object) -> float:
    return _finite_shot_number(shot, "end_seconds", "end")


def _shot_duration(shot: object) -> float:
    try:
        duration = _finite_shot_number(shot, "duration_seconds", "duration")
    except FrameSampleError:
        start, end = _shot_start_end(shot)
        duration = end - start
    return max(0.0, duration)


def _shot_start_end(shot: object) -> tuple[float, float]:
    start = _shot_start(shot)
    end = _shot_end(shot)
    if end <= start:
        raise FrameSampleError(
            "invalid_shot",
            f"Shot-Ende muss nach Start liegen: {start}..{end}",
        )
    return start, end


def _finite_shot_number(shot: object, *names: str) -> float:
    try:
        value = float(_shot_value(shot, *names))
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        label = names[0] if names else "value"
        raise FrameSampleError(
            "invalid_shot",
            f"Shot ohne gültigen {label}-Wert: {shot!r}",
        ) from exc
    if not math.isfinite(value):
        label = names[0] if names else "value"
        raise FrameSampleError(
            "invalid_shot",
            f"Shot-{label} ist nicht finit: {value!r}",
        )
    return value


def _clamp_with_edge_margin(timestamp: float, *, start: float, end: float) -> float:
    if end - start >= EDGE_MARGIN_SECONDS * 2.0:
        return max(
            start + EDGE_MARGIN_SECONDS,
            min(end - EDGE_MARGIN_SECONDS, timestamp),
        )
    return _clamp_inside_shot(timestamp, start=start, end=end)


def _clamp_inside_shot(timestamp: float, *, start: float, end: float) -> float:
    return max(start, min(end, timestamp))


def _finite_timestamp(timestamp: float) -> float:
    try:
        seconds = float(timestamp)
    except (TypeError, ValueError) as exc:
        raise FrameSampleError(
            "frame_extraction_failed",
            f"Ungültiger Frame-Zeitstempel: {timestamp!r}",
        ) from exc
    if not math.isfinite(seconds) or seconds < 0.0:
        raise FrameSampleError(
            "frame_extraction_failed",
            f"Ungültiger Frame-Zeitstempel: {timestamp!r}",
        )
    return seconds


def _format_seconds(seconds: float) -> str:
    text = f"{seconds:.6f}".rstrip("0").rstrip(".")
    return text or "0"


def _rotation_filters(rotation_degrees: float | None) -> list[str]:
    rotation = _normalized_rotation(rotation_degrees)
    if rotation is None or abs(rotation) < 0.01:
        return []
    if abs(rotation - 90.0) < 0.5:
        return ["transpose=clock"]
    if abs(rotation + 90.0) < 0.5:
        return ["transpose=cclock"]
    if abs(abs(rotation) - 180.0) < 0.5:
        return ["transpose=clock", "transpose=clock"]

    radians = rotation * math.pi / 180.0
    angle = _format_float(radians)
    return [f"rotate={angle}:ow=rotw({angle}):oh=roth({angle})"]


def _normalized_rotation(rotation_degrees: float | None) -> float | None:
    if rotation_degrees is None:
        return None
    try:
        rotation = float(rotation_degrees)
    except (TypeError, ValueError) as exc:
        raise FrameSampleError(
            "frame_extraction_failed",
            f"Ungültige Rotation: {rotation_degrees!r}",
        ) from exc
    if not math.isfinite(rotation):
        raise FrameSampleError(
            "frame_extraction_failed",
            f"Ungültige Rotation: {rotation_degrees!r}",
        )
    rotation = math.fmod(rotation, 360.0)
    if rotation > 180.0:
        rotation -= 360.0
    if rotation <= -180.0:
        rotation += 360.0
    return rotation


def _resolve_rotation_degrees(
    *,
    rotation_degrees: float | None,
    source_probe: object | None,
) -> float | None:
    if rotation_degrees is not None or source_probe is None:
        return rotation_degrees
    if isinstance(source_probe, dict):
        value = source_probe.get("rotation_degrees")
    else:
        value = getattr(source_probe, "rotation_degrees", None)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise FrameSampleError(
            "frame_extraction_failed",
            f"Ungültige Probe-Rotation: {value!r}",
        ) from exc


def _scale_filter() -> str:
    return (
        "scale=w='min(1280,iw)':h='min(1280,ih)':"
        "force_original_aspect_ratio=decrease"
    )


def _format_float(value: float) -> str:
    return f"{value:.8f}".rstrip("0").rstrip(".")


def _resize_without_upscaling(image: Image.Image, *, max_long_edge: int) -> None:
    if max(image.size) <= max_long_edge:
        return
    image.thumbnail(
        (max_long_edge, max_long_edge),
        resample=Image.Resampling.LANCZOS,
    )


def _output_path_for_format(path: Path, suffix: str) -> Path:
    current = path.suffix.lower()
    if suffix == ".jpg" and current in {".jpg", ".jpeg"}:
        return path
    if current == suffix:
        return path
    return path.with_suffix(suffix)


def _image_has_alpha(image: Image.Image) -> bool:
    if image.mode in {"RGBA", "LA", "PA", "RGBa"}:
        return True
    if image.mode == "P":
        return image.info.get("transparency") is not None
    return False
