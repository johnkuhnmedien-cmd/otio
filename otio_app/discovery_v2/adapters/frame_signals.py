"""Discovery-V2 local frame signals computed with Pillow only."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps, ImageStat, UnidentifiedImageError

from otio_app.discovery_v2.adapters.source_hash import compute_sha256_hex

BLACK_LUMINANCE_THRESHOLD = 8
BLACK_FRACTION_THRESHOLD = 0.98


class FrameSignalsError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class FrameSignals:
    brightness_mean: float
    black_fraction: float
    is_black: bool
    sharpness_score: float
    pixel_sha256: str
    frame_sha256: str


def compute_frame_signals(path: Path | str) -> FrameSignals:
    """Compute deterministic local technical signals for a decoded frame file."""
    frame_path = Path(path)
    try:
        frame_sha = compute_sha256_hex(frame_path)
    except OSError as exc:
        raise FrameSignalsError("frame_hash_failed", str(exc)) from exc

    try:
        with Image.open(frame_path) as image:
            working = ImageOps.exif_transpose(image)
            if working is None:
                raise FrameSignalsError(
                    "frame_decode_failed",
                    "EXIF-Transpose lieferte kein Bild.",
                )
            working.load()
            grayscale = working.convert("L")
            stats = ImageStat.Stat(grayscale)
            brightness_mean = float(stats.mean[0]) / 255.0
            histogram = grayscale.histogram()
            pixel_count = max(1, grayscale.size[0] * grayscale.size[1])
            black_pixels = sum(histogram[: BLACK_LUMINANCE_THRESHOLD + 1])
            black_fraction = float(black_pixels) / float(pixel_count)
            edges = grayscale.filter(ImageFilter.FIND_EDGES)
            edge_stats = ImageStat.Stat(edges)
            sharpness_score = float(edge_stats.var[0])
            pixel_sha = _pixel_sha256(working)
    except FrameSignalsError:
        raise
    except UnidentifiedImageError as exc:
        raise FrameSignalsError(
            "frame_decode_failed",
            f"Frame konnte nicht gelesen werden: {exc}",
        ) from exc
    except OSError as exc:
        raise FrameSignalsError(
            "frame_decode_failed",
            f"Frame-Dekodierung fehlgeschlagen: {exc}",
        ) from exc

    return FrameSignals(
        brightness_mean=max(0.0, min(1.0, brightness_mean)),
        black_fraction=max(0.0, min(1.0, black_fraction)),
        is_black=black_fraction >= BLACK_FRACTION_THRESHOLD,
        sharpness_score=sharpness_score,
        pixel_sha256=pixel_sha,
        frame_sha256=frame_sha,
    )


def is_frame_black(path: Path | str) -> bool:
    return compute_frame_signals(path).is_black


def _pixel_sha256(image: Image.Image) -> str:
    normalized = _normalize_rgb_or_rgba(image)
    digest = hashlib.sha256()
    digest.update(normalized.mode.encode("ascii"))
    digest.update(b"\0")
    digest.update(str(normalized.size[0]).encode("ascii"))
    digest.update(b"x")
    digest.update(str(normalized.size[1]).encode("ascii"))
    digest.update(b"\0")
    digest.update(normalized.tobytes())
    return digest.hexdigest().lower()


def _normalize_rgb_or_rgba(image: Image.Image) -> Image.Image:
    if _image_has_alpha(image):
        return image.convert("RGBA")
    return image.convert("RGB")


def _image_has_alpha(image: Image.Image) -> bool:
    if image.mode in {"RGBA", "LA", "PA", "RGBa"}:
        return True
    if image.mode == "P":
        return image.info.get("transparency") is not None
    return False
