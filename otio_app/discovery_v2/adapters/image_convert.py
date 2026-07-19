"""Discovery-V2 TIFF→PNG (image-png-v1): ausschließlich Pillow-Datei-I/O."""

from __future__ import annotations

import hashlib
import os
import shutil
import warnings
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from otio_app.discovery_v2.adapters.image_probe import detect_bigtiff
from otio_app.discovery_v2.adapters.source_hash import compute_sha256_hex
from otio_app.discovery_v2.domain.media_intake import IMAGE_PNG_PROFILE_VERSION
from otio_app.discovery_v2.paths import assert_path_is_under_discovery_v2

IMAGE_PNG_PROFILE_NAME = IMAGE_PNG_PROFILE_VERSION
_MIN_FREE_BYTES_FLOOR = 256 * 1024 * 1024
_EXIF_ORIENTATION_TAG = 274
_ALLOWED_SOURCE_MODES = frozenset({"1", "L", "LA", "RGB", "RGBA", "P", "PA"})
_TIFF_EXTENSIONS = frozenset({".tif", ".tiff"})


class ImageConvertError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ImageConvertMeta:
    source_format: str
    source_mode: str
    source_width: int
    source_height: int
    source_frame_count: int
    source_has_alpha: bool
    source_has_icc: bool
    source_exif_orientation: int | None
    source_bit_depth: int | None
    output_format: str
    output_mode: str
    output_width: int
    output_height: int
    output_has_alpha: bool
    output_exif_orientation: int | None
    pixel_digest: str
    orientation_applied: bool


@dataclass(frozen=True)
class ImageConvertPublishResult:
    source_sha256: str
    output_sha256: str
    working_path: Path
    meta: ImageConvertMeta


def _bytes_per_pixel(mode: str) -> int:
    return {
        "1": 1,
        "L": 1,
        "LA": 2,
        "P": 1,
        "PA": 2,
        "RGB": 3,
        "RGBA": 4,
    }.get(mode, 4)


def _mode_has_alpha(image: Image.Image) -> bool:
    if image.mode in {"RGBA", "LA", "PA", "RGBa"}:
        return True
    if image.mode == "P":
        return image.info.get("transparency") is not None
    return False


def _read_orientation(image: Image.Image) -> int | None:
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
    except (TypeError, ValueError) as exc:
        raise ImageConvertError(
            "image_orientation_failed",
            f"EXIF-Orientierung nicht lesbar: {raw!r}",
        ) from exc
    if value < 1 or value > 8:
        raise ImageConvertError(
            "image_orientation_failed",
            f"Ungültige EXIF-Orientierung: {value}",
        )
    return value


def _pixel_digest(image: Image.Image) -> str:
    # Vollständige Pixelbytes — Nachweis jenseits Datei-SHA.
    payload = image.tobytes()
    return hashlib.sha256(payload).hexdigest()


def _expected_alpha_for_palette_index(
    index: int, transparency: object
) -> int | None:
    """Erwarteter Alpha-Wert für einen Palette-Index; None = nicht kontrollierbar."""
    if isinstance(transparency, int):
        if transparency < 0 or transparency > 255:
            return None
        return 0 if index == transparency else 255
    if isinstance(transparency, (bytes, bytearray)):
        if len(transparency) == 0 or len(transparency) > 256:
            return None
        if index < 0 or index > 255:
            return None
        if index < len(transparency):
            return int(transparency[index])
        return 255
    if isinstance(transparency, tuple) and transparency:
        if not all(isinstance(v, int) and 0 <= v <= 255 for v in transparency):
            return None
        if index < 0 or index > 255:
            return None
        if index < len(transparency):
            return int(transparency[index])
        return 255
    return None


def _convert_palette_preserving_alpha(image: Image.Image) -> Image.Image:
    """P mit transparency → RGBA, nur bei nachweisbarer Alpha-Erhaltung."""
    transparency = image.info.get("transparency")
    if transparency is None:
        return image.convert("RGB")

    if isinstance(transparency, int):
        if transparency < 0 or transparency > 255:
            raise ImageConvertError(
                "image_alpha_preservation_failed",
                f"Palette-Transparenz-Index ungültig: {transparency}",
            )
    elif isinstance(transparency, (bytes, bytearray)):
        if len(transparency) == 0 or len(transparency) > 256:
            raise ImageConvertError(
                "image_alpha_preservation_failed",
                "Palette-Transparenz-Map hat ungültige Länge.",
            )
    elif isinstance(transparency, tuple):
        if (
            not transparency
            or len(transparency) > 256
            or not all(
                isinstance(v, int) and 0 <= v <= 255 for v in transparency
            )
        ):
            raise ImageConvertError(
                "image_alpha_preservation_failed",
                "Palette-Transparenz-Tuple ist nicht kontrollierbar.",
            )
    else:
        raise ImageConvertError(
            "image_alpha_preservation_failed",
            (
                "Palette-Transparenz nicht kontrollierbar: "
                f"{type(transparency).__name__}"
            ),
        )

    width, height = image.size
    expected_alphas: list[int] = []
    for y in range(height):
        for x in range(width):
            raw = image.getpixel((x, y))
            try:
                index = int(raw)  # type: ignore[arg-type]
            except (TypeError, ValueError) as exc:
                raise ImageConvertError(
                    "image_alpha_preservation_failed",
                    f"Palette-Index nicht lesbar: {raw!r}",
                ) from exc
            alpha = _expected_alpha_for_palette_index(index, transparency)
            if alpha is None:
                raise ImageConvertError(
                    "image_alpha_preservation_failed",
                    "Palette-Transparenz konnte nicht deterministisch abgebildet werden.",
                )
            expected_alphas.append(alpha)

    rgba = image.convert("RGBA")
    if rgba.size != image.size:
        raise ImageConvertError(
            "image_alpha_preservation_failed",
            "Palette→RGBA änderte die Bildgröße.",
        )
    idx = 0
    for y in range(height):
        for x in range(width):
            pixel = rgba.getpixel((x, y))
            if not isinstance(pixel, tuple) or len(pixel) < 4:
                raise ImageConvertError(
                    "image_alpha_preservation_failed",
                    "Unerwartetes Pixeldatenformat nach Palette→RGBA.",
                )
            if int(pixel[3]) != expected_alphas[idx]:
                raise ImageConvertError(
                    "image_alpha_preservation_failed",
                    (
                        f"Alpha an Pixel {idx} nicht erhalten "
                        f"(ist {pixel[3]}, erwartet {expected_alphas[idx]})."
                    ),
                )
            idx += 1
    return rgba


def _normalize_source(image: Image.Image) -> tuple[Image.Image, bool]:
    """Modus normalisieren + EXIF-Transpose (vor load()).

    Pillow kann beim ``load()`` von TIFF die Orientierung still anwenden und
    den Tag entfernen. Daher muss die Orientierung vorher gelesen und
    deterministisch per ``exif_transpose`` behandelt werden.
    """
    mode = image.mode
    if mode not in _ALLOWED_SOURCE_MODES:
        raise ImageConvertError(
            "image_mode_unsupported",
            f"Bildmodus nicht freigegeben: {mode}",
        )

    orientation = _read_orientation(image)
    orientation_applied = False
    working = image
    source_had_alpha = _mode_has_alpha(working)

    if orientation is None or orientation == 1:
        pass
    else:
        try:
            transposed = ImageOps.exif_transpose(working)
        except Exception as exc:  # noqa: BLE001
            raise ImageConvertError(
                "image_orientation_failed",
                f"EXIF-Transpose fehlgeschlagen: {exc}",
            ) from exc
        if transposed is None:
            raise ImageConvertError(
                "image_orientation_failed",
                "EXIF-Transpose lieferte kein Bild.",
            )
        working = transposed
        orientation_applied = True

    # Decode nach Orientierungsnormalisierung erzwingen.
    try:
        working.load()
    except Image.DecompressionBombError as exc:
        raise ImageConvertError(
            "insufficient_memory",
            f"Bild zu groß für sicheres Dekodieren: {exc}",
        ) from exc
    except MemoryError as exc:
        raise ImageConvertError(
            "insufficient_memory",
            f"Nicht genügend Speicher: {exc}",
        ) from exc
    except OSError as exc:
        raise ImageConvertError(
            "image_decode_failed",
            f"TIFF-Dekodierung fehlgeschlagen: {exc}",
        ) from exc

    # Orientation-Tag entfernen / normalisieren.
    if hasattr(working, "getexif"):
        try:
            exif = working.getexif()
            if exif is not None and _EXIF_ORIENTATION_TAG in exif:
                del exif[_EXIF_ORIENTATION_TAG]
                working.info.pop("exif", None)
        except Exception:  # noqa: BLE001
            pass

    if working.mode == "1":
        working = working.convert("L")
    elif working.mode == "PA":
        working = working.convert("RGBA")
    elif working.mode == "P":
        working = _convert_palette_preserving_alpha(working)
    elif working.mode not in {"L", "LA", "RGB", "RGBA"}:
        raise ImageConvertError(
            "image_mode_unsupported",
            f"Normalisierter Modus unerwartet: {working.mode}",
        )

    if source_had_alpha and working.mode not in {"LA", "RGBA"}:
        raise ImageConvertError(
            "image_alpha_preservation_failed",
            "Alpha ging bei der Modusnormalisierung verloren.",
        )

    return working, orientation_applied


def _assert_disk_space(
    *,
    target_dir: Path,
    source_size: int,
    width: int,
    height: int,
    mode: str,
) -> None:
    needed = max(
        source_size * 2,
        width * height * _bytes_per_pixel(mode) * 2,
        _MIN_FREE_BYTES_FLOOR,
    )
    try:
        usage = shutil.disk_usage(str(target_dir if target_dir.exists() else target_dir.parent))
    except OSError as exc:
        raise ImageConvertError(
            "insufficient_disk_space",
            f"Freier Speicherplatz nicht prüfbar: {exc}",
        ) from exc
    if usage.free < needed:
        raise ImageConvertError(
            "insufficient_disk_space",
            f"Zu wenig freier Speicher: {usage.free} < {needed} Bytes.",
        )


def _open_tiff_for_convert(path: Path) -> Image.Image:
    """Öffnet TIFF ohne sofortiges load(), damit EXIF-Orientierung erhalten bleibt."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            return Image.open(path)
    except Image.DecompressionBombError as exc:
        raise ImageConvertError(
            "insufficient_memory",
            f"Bild zu groß für sicheres Dekodieren: {exc}",
        ) from exc
    except Image.DecompressionBombWarning as exc:
        raise ImageConvertError(
            "insufficient_memory",
            f"Bild zu groß (DecompressionBomb): {exc}",
        ) from exc
    except MemoryError as exc:
        raise ImageConvertError(
            "insufficient_memory",
            f"Nicht genügend Speicher: {exc}",
        ) from exc
    except UnidentifiedImageError as exc:
        raise ImageConvertError(
            "image_decode_failed",
            f"TIFF konnte nicht gelesen werden: {exc}",
        ) from exc
    except OSError as exc:
        raise ImageConvertError(
            "image_decode_failed",
            f"TIFF-Dekodierung fehlgeschlagen: {exc}",
        ) from exc


def evaluate_tiff_source_policy(
    image: Image.Image,
    *,
    path: Path,
    source_extension: str,
) -> None:
    """Gates vor Normalisierung/Encode."""
    ext = (source_extension or path.suffix or "").strip().lower()
    if not ext.startswith("."):
        ext = f".{ext}" if ext else ""
    if ext not in _TIFF_EXTENSIONS:
        raise ImageConvertError(
            "image_format_unsupported",
            f"Extension ist kein TIFF: {ext or '—'}",
        )

    is_bigtiff = detect_bigtiff(path)
    if is_bigtiff is True:
        raise ImageConvertError(
            "image_format_unsupported",
            "BigTIFF wird von image-png-v1 nicht unterstützt.",
        )

    fmt = (image.format or "").strip().upper()
    if fmt != "TIFF":
        raise ImageConvertError(
            "image_format_unsupported",
            f"Tatsächliches Format ist nicht TIFF: {fmt or 'unbekannt'}",
        )

    frames = int(getattr(image, "n_frames", 1) or 1)
    if frames > 1 or bool(getattr(image, "is_animated", False)):
        raise ImageConvertError(
            "multipage_tiff_unsupported",
            f"Mehrseiten-TIFF ({frames} Seiten) wird nicht unterstützt.",
        )

    if image.info.get("icc_profile"):
        raise ImageConvertError(
            "image_color_profile_preservation_failed",
            "ICC-Profil vorhanden — image-png-v1 erhält keine Farbprofile.",
        )

    mode = image.mode
    if mode in {"I;16", "I;16L", "I;16B", "I;16N", "I", "F"}:
        raise ImageConvertError(
            "image_bit_depth_unsupported",
            f"Bit-Tiefe/Modus nicht freigegeben: {mode}",
        )
    if mode in {"CMYK", "LAB", "YCbCr", "HSV"}:
        raise ImageConvertError(
            "image_mode_unsupported",
            f"Bildmodus nicht freigegeben: {mode}",
        )
    if mode not in _ALLOWED_SOURCE_MODES:
        raise ImageConvertError(
            "image_mode_unsupported",
            f"Bildmodus nicht freigegeben: {mode}",
        )

    # Orientierung prüfen (wirft bei ungültig).
    _read_orientation(image)


def validate_png_output_policy(
    *,
    output: Image.Image,
    expected_mode: str,
    expected_size: tuple[int, int],
    expected_digest: str,
    expected_has_alpha: bool,
) -> ImageConvertMeta:
    fmt = (output.format or "").strip().upper()
    if fmt != "PNG":
        raise ImageConvertError(
            "invalid_output",
            f"Ausgabe ist kein PNG: {fmt or 'unbekannt'}",
        )
    frames = int(getattr(output, "n_frames", 1) or 1)
    if frames != 1 or bool(getattr(output, "is_animated", False)):
        raise ImageConvertError(
            "output_policy_mismatch",
            f"Ausgabe hat unerwartete Frame-Anzahl: {frames}",
        )
    if output.mode != expected_mode:
        raise ImageConvertError(
            "output_policy_mismatch",
            f"Output-Modus {output.mode} ≠ erwartet {expected_mode}",
        )
    if output.size != expected_size:
        raise ImageConvertError(
            "output_policy_mismatch",
            f"Output-Größe {output.size} ≠ erwartet {expected_size}",
        )
    out_has_alpha = _mode_has_alpha(output)
    if out_has_alpha != expected_has_alpha:
        raise ImageConvertError(
            "image_alpha_preservation_failed",
            f"Alpha-Ergebnis {out_has_alpha} ≠ erwartet {expected_has_alpha}",
        )
    digest = _pixel_digest(output)
    if digest != expected_digest:
        raise ImageConvertError(
            "output_policy_mismatch",
            "Pixel-Digest der Ausgabe weicht von der Erwartung ab.",
        )
    try:
        out_orient = _read_orientation(output)
    except ImageConvertError:
        out_orient = None
    if out_orient not in (None, 1):
        raise ImageConvertError(
            "image_orientation_failed",
            f"Output trägt noch Orientierung {out_orient}.",
        )
    return ImageConvertMeta(
        source_format="TIFF",
        source_mode=expected_mode,  # filled by caller below for report
        source_width=expected_size[0],
        source_height=expected_size[1],
        source_frame_count=1,
        source_has_alpha=expected_has_alpha,
        source_has_icc=False,
        source_exif_orientation=None,
        source_bit_depth=8 if expected_mode != "1" else 1,
        output_format="PNG",
        output_mode=output.mode,
        output_width=output.size[0],
        output_height=output.size[1],
        output_has_alpha=out_has_alpha,
        output_exif_orientation=out_orient,
        pixel_digest=digest,
        orientation_applied=False,
    )


def publish_image_png_v1(
    *,
    project_root: Path,
    source_path: Path,
    temp_path: Path,
    working_path: Path,
    expected_source_sha256: str,
    source_extension: str,
) -> ImageConvertPublishResult:
    """TIFF → Temp-PNG → Policy → os.replace. Kein Überschreiben von Final."""
    assert_path_is_under_discovery_v2(temp_path, project_root)
    assert_path_is_under_discovery_v2(working_path, project_root)

    if working_path.exists():
        raise ImageConvertError(
            "working_media_conflict",
            "Final-Pfad existiert bereits und wurde nicht überschrieben.",
        )

    try:
        source_sha = compute_sha256_hex(source_path)
    except OSError as exc:
        raise ImageConvertError("source_hash_mismatch", str(exc)) from exc
    if source_sha != expected_source_sha256.lower():
        raise ImageConvertError(
            "source_hash_mismatch",
            "SHA-256 der Quelle weicht vom erwarteten Wert ab.",
        )

    try:
        source_size = source_path.stat().st_size
    except OSError as exc:
        raise ImageConvertError("source_missing", str(exc)) from exc

    image = _open_tiff_for_convert(source_path)
    try:
        evaluate_tiff_source_policy(
            image, path=source_path, source_extension=source_extension
        )
        source_mode = image.mode
        source_size_wh = image.size
        source_frames = int(getattr(image, "n_frames", 1) or 1)
        source_has_alpha = _mode_has_alpha(image)
        source_orient = _read_orientation(image)

        normalized, orientation_applied = _normalize_source(image)
        expected_mode = normalized.mode
        expected_size = normalized.size
        expected_has_alpha = _mode_has_alpha(normalized)
        expected_digest = _pixel_digest(normalized)

        temp_path.parent.mkdir(parents=True, exist_ok=True)
        _assert_disk_space(
            target_dir=temp_path.parent,
            source_size=source_size,
            width=expected_size[0],
            height=expected_size[1],
            mode=expected_mode,
        )

        try:
            # Kein ICC schreiben; kein Orientation-Tag.
            save_kwargs: dict = {"format": "PNG", "optimize": False}
            normalized.save(temp_path, **save_kwargs)
        except OSError as exc:
            raise ImageConvertError(
                "image_encode_failed",
                f"PNG-Schreiben fehlgeschlagen: {exc}",
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise ImageConvertError(
                "image_encode_failed",
                f"PNG-Encode fehlgeschlagen: {exc}",
            ) from exc
    finally:
        try:
            image.close()
        except Exception:  # noqa: BLE001
            pass

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(temp_path) as out_image:
                out_image.load()
                if (out_image.format or "").strip().upper() != "PNG":
                    raise ImageConvertError(
                        "invalid_output",
                        f"Temp-Ausgabe ist kein PNG: {out_image.format}",
                    )
                if out_image.mode != expected_mode:
                    raise ImageConvertError(
                        "output_policy_mismatch",
                        f"Temp-Modus {out_image.mode} ≠ {expected_mode}",
                    )
                if out_image.size != expected_size:
                    raise ImageConvertError(
                        "output_policy_mismatch",
                        f"Temp-Größe {out_image.size} ≠ {expected_size}",
                    )
                out_has_alpha = _mode_has_alpha(out_image)
                if out_has_alpha != expected_has_alpha:
                    raise ImageConvertError(
                        "image_alpha_preservation_failed",
                        "Alpha im Temp-PNG nicht erhalten.",
                    )
                digest = _pixel_digest(out_image)
                if digest != expected_digest:
                    raise ImageConvertError(
                        "output_policy_mismatch",
                        "Temp-PNG Pixel-Digest weicht ab.",
                    )
                try:
                    out_orient = _read_orientation(out_image)
                except ImageConvertError as exc:
                    raise ImageConvertError(
                        "image_orientation_failed", exc.message
                    ) from exc
                if out_orient not in (None, 1):
                    raise ImageConvertError(
                        "image_orientation_failed",
                        f"Temp-PNG Orientierung nicht normal: {out_orient}",
                    )
    except ImageConvertError:
        temp_path.unlink(missing_ok=True)
        raise
    except Exception as exc:  # noqa: BLE001
        temp_path.unlink(missing_ok=True)
        raise ImageConvertError(
            "invalid_output",
            f"Temp-PNG konnte nicht erneut validiert werden: {exc}",
        ) from exc

    try:
        output_sha = compute_sha256_hex(temp_path)
    except OSError as exc:
        temp_path.unlink(missing_ok=True)
        raise ImageConvertError("invalid_output", str(exc)) from exc

    if working_path.exists():
        temp_path.unlink(missing_ok=True)
        raise ImageConvertError(
            "working_media_conflict",
            "Final-Pfad wurde vor Replace belegt.",
        )

    working_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(temp_path, working_path)
    except OSError as exc:
        temp_path.unlink(missing_ok=True)
        raise ImageConvertError(
            "image_encode_failed",
            f"Atomisches Ersetzen fehlgeschlagen: {exc}",
        ) from exc

    meta = ImageConvertMeta(
        source_format="TIFF",
        source_mode=source_mode,
        source_width=source_size_wh[0],
        source_height=source_size_wh[1],
        source_frame_count=source_frames,
        source_has_alpha=source_has_alpha,
        source_has_icc=False,
        source_exif_orientation=source_orient,
        source_bit_depth=8 if source_mode != "1" else 1,
        output_format="PNG",
        output_mode=expected_mode,
        output_width=expected_size[0],
        output_height=expected_size[1],
        output_has_alpha=expected_has_alpha,
        output_exif_orientation=out_orient if out_orient is not None else 1,
        pixel_digest=expected_digest,
        orientation_applied=orientation_applied,
    )
    return ImageConvertPublishResult(
        source_sha256=source_sha,
        output_sha256=output_sha,
        working_path=working_path,
        meta=meta,
    )


def validate_existing_png_against_source(
    *,
    source_path: Path,
    output_path: Path,
    source_extension: str,
    expected_output_sha256: str,
) -> ImageConvertMeta:
    """Reuse-Prüfung: Output-Policy + Pixel-Digest gegen normalisierte Source."""
    try:
        digest = compute_sha256_hex(output_path)
    except OSError as exc:
        raise ImageConvertError("working_media_conflict", str(exc)) from exc
    if digest != expected_output_sha256.lower():
        raise ImageConvertError(
            "working_media_conflict",
            "Output-SHA stimmt nicht mit Registry überein.",
        )

    image = _open_tiff_for_convert(source_path)
    try:
        evaluate_tiff_source_policy(
            image, path=source_path, source_extension=source_extension
        )
        source_mode = image.mode
        source_size_wh = image.size
        source_frames = int(getattr(image, "n_frames", 1) or 1)
        source_has_alpha = _mode_has_alpha(image)
        source_orient = _read_orientation(image)
        normalized, orientation_applied = _normalize_source(image)
        expected_mode = normalized.mode
        expected_size = normalized.size
        expected_has_alpha = _mode_has_alpha(normalized)
        expected_digest = _pixel_digest(normalized)
    finally:
        try:
            image.close()
        except Exception:  # noqa: BLE001
            pass

    try:
        with Image.open(output_path) as out_image:
            out_image.load()
            if (out_image.format or "").strip().upper() != "PNG":
                raise ImageConvertError(
                    "working_media_conflict", "Vorhandene Ausgabe ist kein PNG."
                )
            if out_image.mode != expected_mode or out_image.size != expected_size:
                raise ImageConvertError(
                    "working_media_conflict",
                    "Vorhandene Ausgabe widerspricht erwarteter Geometrie/Modus.",
                )
            if _mode_has_alpha(out_image) != expected_has_alpha:
                raise ImageConvertError(
                    "working_media_conflict",
                    "Vorhandene Ausgabe widerspricht Alpha-Erwartung.",
                )
            if _pixel_digest(out_image) != expected_digest:
                raise ImageConvertError(
                    "working_media_conflict",
                    "Vorhandene Ausgabe widerspricht Pixel-Digest.",
                )
            try:
                out_orient = _read_orientation(out_image)
            except ImageConvertError as exc:
                raise ImageConvertError(
                    "working_media_conflict", exc.message
                ) from exc
            if out_orient not in (None, 1):
                raise ImageConvertError(
                    "working_media_conflict",
                    "Vorhandene Ausgabe hat unnormale Orientierung.",
                )
    except ImageConvertError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ImageConvertError(
            "working_media_conflict",
            f"Vorhandene PNG-Ausgabe ungültig: {exc}",
        ) from exc

    return ImageConvertMeta(
        source_format="TIFF",
        source_mode=source_mode,
        source_width=source_size_wh[0],
        source_height=source_size_wh[1],
        source_frame_count=source_frames,
        source_has_alpha=source_has_alpha,
        source_has_icc=False,
        source_exif_orientation=source_orient,
        source_bit_depth=8 if source_mode != "1" else 1,
        output_format="PNG",
        output_mode=expected_mode,
        output_width=expected_size[0],
        output_height=expected_size[1],
        output_has_alpha=expected_has_alpha,
        output_exif_orientation=out_orient if out_orient is not None else 1,
        pixel_digest=expected_digest,
        orientation_applied=orientation_applied,
    )
