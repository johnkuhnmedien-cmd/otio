"""Discovery-V2 Image-Probe (Pillow, read-only) für persistierte Validation-Felder."""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageFile, UnidentifiedImageError


class ImageProbeError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ImageProbeResult:
    image_format: str | None
    image_mode: str | None
    width: int | None
    height: int | None
    image_frame_count: int | None
    has_alpha: bool | None
    has_icc_profile: bool | None
    exif_orientation: int | None
    image_bit_depth: int | None
    image_is_bigtiff: bool | None


_EXIF_ORIENTATION_TAG = 274

_MODE_BIT_DEPTH: dict[str, int] = {
    "1": 1,
    "L": 8,
    "LA": 8,
    "P": 8,
    "PA": 8,
    "RGB": 8,
    "RGBA": 8,
    # 16-Bit-Varianten werden erkannt, aber nicht konvertiert.
    "I;16": 16,
    "I;16L": 16,
    "I;16B": 16,
    "I;16N": 16,
}


def detect_bigtiff(path: Path) -> bool | None:
    """Erkennt BigTIFF anhand des Dateikopfs; None bei Lesefehler."""
    try:
        with path.open("rb") as handle:
            header = handle.read(4)
    except OSError:
        return None
    if len(header) < 4:
        return None
    if header in (b"II+\x00", b"MM\x00+"):
        return True
    if header in (b"II*\x00", b"MM\x00*"):
        return False
    return None


def _mode_has_alpha(image: Image.Image) -> bool:
    mode = image.mode
    if mode in {"RGBA", "LA", "PA", "RGBa"}:
        return True
    if mode == "P":
        transparency = image.info.get("transparency")
        return transparency is not None
    return False


def _read_exif_orientation(image: Image.Image) -> int | None:
    try:
        exif = image.getexif()
    except Exception:  # noqa: BLE001
        return None
    if not exif:
        return None
    raw = exif.get(_EXIF_ORIENTATION_TAG)
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    if 1 <= value <= 8:
        return value
    # Ungültige Orientierung als speziellen Marker speichern? Auftrag: null
    # für unbekannte Werte; Convert blockiert später bei ungültig erneut.
    return None


def _bit_depth_for_mode(mode: str) -> int | None:
    return _MODE_BIT_DEPTH.get(mode)


def probe_image_file(path: Path) -> ImageProbeResult:
    """Ein kontrollierter Pillow-Probe: open → load → schließen."""
    if not path.is_file() or path.is_symlink():
        raise ImageProbeError("invalid_source_path", f"Keine reguläre Datei: {path}")

    is_bigtiff = detect_bigtiff(path)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                image_format = (image.format or "").strip().upper() or None
                mode = image.mode
                width, height = image.size
                frame_count = int(getattr(image, "n_frames", 1) or 1)
                has_alpha = _mode_has_alpha(image)
                has_icc = bool(image.info.get("icc_profile"))
                orientation = _read_exif_orientation(image)
                bit_depth = _bit_depth_for_mode(mode)
                # Decode vollständig, damit Fehler erkannt werden.
                image.load()
    except Image.DecompressionBombError as exc:
        raise ImageProbeError(
            "insufficient_memory",
            f"Bild zu groß für sicheres Dekodieren: {exc}",
        ) from exc
    except Image.DecompressionBombWarning as exc:
        raise ImageProbeError(
            "insufficient_memory",
            f"Bild zu groß (DecompressionBomb): {exc}",
        ) from exc
    except MemoryError as exc:
        raise ImageProbeError(
            "insufficient_memory",
            f"Nicht genügend Speicher zum Dekodieren: {exc}",
        ) from exc
    except UnidentifiedImageError as exc:
        raise ImageProbeError(
            "image_decode_failed",
            f"Bild konnte nicht identifiziert werden: {exc}",
        ) from exc
    except OSError as exc:
        raise ImageProbeError(
            "image_decode_failed",
            f"Bilddekodierung fehlgeschlagen: {exc}",
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise ImageProbeError(
            "image_decode_failed",
            f"Unerwarteter Bildprobe-Fehler: {exc}",
        ) from exc

    return ImageProbeResult(
        image_format=image_format,
        image_mode=mode,
        width=width,
        height=height,
        image_frame_count=frame_count,
        has_alpha=has_alpha,
        has_icc_profile=has_icc,
        exif_orientation=orientation,
        image_bit_depth=bit_depth,
        image_is_bigtiff=is_bigtiff,
    )


# ImageFile bleibt importiert, damit Truncated-Images als Decode-Fehler greifen.
_ = ImageFile
