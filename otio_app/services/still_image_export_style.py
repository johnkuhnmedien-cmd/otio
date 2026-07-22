"""Still-Image-Styling unmittelbar vor OTIO-Export.

Komponiert JPEG/PNG auf Zielauflösung: Zoom (Default 0.8, fit) auf
Vintage-Hintergrund. Kein Cut-Plan-Rebuild nötig — greift nur beim Export.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from otio_app.models import Project
from otio_app.project_layout import get_folder_clean_output_dir, safe_folder_slug
from otio_app.services.media_utils import is_image_media

__all__ = [
    "STILL_BACKGROUND_VINTAGE",
    "STILL_BACKGROUND_NONE",
    "DEFAULT_STILL_IMAGE_ZOOM",
    "VINTAGE_BACKGROUND_RGB",
    "still_style_needed",
    "styled_still_output_path",
    "render_styled_still_image",
    "ensure_styled_still_for_export",
]

STILL_BACKGROUND_VINTAGE = "vintage"
STILL_BACKGROUND_NONE = "none"
DEFAULT_STILL_IMAGE_ZOOM = 0.8

# Warmes Pergament / Vintage-Papier
VINTAGE_BACKGROUND_RGB = (196, 168, 130)
VINTAGE_VIGNETTE_RGB = (120, 96, 70)


def still_style_needed(
    *,
    enabled: bool,
    zoom: float,
    background_style: str,
) -> bool:
    """True wenn Export ein Still neu komponieren soll."""
    if not enabled:
        return False
    style = (background_style or "").strip().lower()
    if style == STILL_BACKGROUND_VINTAGE:
        return True
    if style in ("", STILL_BACKGROUND_NONE) and abs(float(zoom) - 1.0) < 0.001:
        return False
    return abs(float(zoom) - 1.0) >= 0.001


def styled_still_output_path(
    work_dir: Path,
    folder_name: str,
    original_path: Path,
    *,
    width: int,
    height: int,
    zoom: float,
    background_style: str,
) -> Path:
    stem = safe_folder_slug(original_path.stem) or "still"
    style = (background_style or STILL_BACKGROUND_NONE).strip().lower() or STILL_BACKGROUND_NONE
    zoom_tag = f"{float(zoom):.2f}".replace(".", "p")
    name = f"{stem}_{width}x{height}_still_{style}_z{zoom_tag}.jpg"
    return get_folder_clean_output_dir(work_dir, folder_name) / name


def _cache_token(source: Path, *, width: int, height: int, zoom: float, background_style: str) -> str:
    try:
        stat = source.stat()
        payload = f"{source.resolve()}|{stat.st_mtime_ns}|{stat.st_size}|{width}x{height}|{zoom}|{background_style}"
    except OSError:
        payload = f"{source}|{width}x{height}|{zoom}|{background_style}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


def _draw_vintage_background(img, width: int, height: int) -> None:
    """Füllt img mit Vintage-Pergament + leichter Vignette (in-place)."""
    from PIL import Image, ImageDraw, ImageFilter

    base = Image.new("RGB", (width, height), VINTAGE_BACKGROUND_RGB)
    vignette = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(vignette)
    margin = max(1, min(width, height) // 8)
    draw.ellipse(
        (-margin, -margin, width + margin, height + margin),
        fill=255,
    )
    vignette = vignette.filter(ImageFilter.GaussianBlur(radius=max(8, min(width, height) // 10)))
    dark = Image.new("RGB", (width, height), VINTAGE_VIGNETTE_RGB)
    blended = Image.composite(base, dark, vignette)
    img.paste(blended)


def _fit_size(src_w: int, src_h: int, max_w: int, max_h: int) -> tuple[int, int]:
    if src_w <= 0 or src_h <= 0 or max_w <= 0 or max_h <= 0:
        return max(1, max_w), max(1, max_h)
    scale = min(max_w / src_w, max_h / src_h)
    return max(1, int(round(src_w * scale))), max(1, int(round(src_h * scale)))


def render_styled_still_image(
    source: Path,
    output: Path,
    *,
    width: int,
    height: int,
    zoom: float = DEFAULT_STILL_IMAGE_ZOOM,
    background_style: str = STILL_BACKGROUND_VINTAGE,
) -> Path:
    """Komponiert Still auf Zielrahmen; schreibt JPEG nach output."""
    from PIL import Image, ImageOps

    width = max(2, int(width))
    height = max(2, int(height))
    zoom = max(0.05, min(1.0, float(zoom)))
    style = (background_style or STILL_BACKGROUND_NONE).strip().lower()

    with Image.open(source) as opened:
        foreground = ImageOps.exif_transpose(opened).convert("RGBA")

    canvas = Image.new("RGB", (width, height), VINTAGE_BACKGROUND_RGB if style == STILL_BACKGROUND_VINTAGE else (0, 0, 0))
    if style == STILL_BACKGROUND_VINTAGE:
        _draw_vintage_background(canvas, width, height)
    elif style not in ("", STILL_BACKGROUND_NONE):
        # Unbekannter Style → Vintage als sicherer Default
        _draw_vintage_background(canvas, width, height)
        style = STILL_BACKGROUND_VINTAGE

    box_w = max(1, int(round(width * zoom)))
    box_h = max(1, int(round(height * zoom)))
    fitted_w, fitted_h = _fit_size(foreground.width, foreground.height, box_w, box_h)
    resized = foreground.resize((fitted_w, fitted_h), Image.Resampling.LANCZOS)
    offset = ((width - fitted_w) // 2, (height - fitted_h) // 2)
    canvas.paste(resized, offset, resized)

    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_suffix(output.suffix + ".tmp")
    canvas.save(tmp, format="JPEG", quality=92, optimize=True)
    tmp.replace(output)
    return output


def ensure_styled_still_for_export(
    project: Project,
    folder_name: str,
    original_path: Path,
    *,
    enabled: bool = True,
    zoom: float = DEFAULT_STILL_IMAGE_ZOOM,
    background_style: str = STILL_BACKGROUND_VINTAGE,
    notes: list[str] | None = None,
) -> Path:
    """Liefert gestyltes Still (Cache) oder Original bei Fehler/Disabled."""
    source = original_path.expanduser()
    try:
        source = source.resolve()
    except OSError:
        pass

    if not is_image_media(source):
        return source
    if not still_style_needed(enabled=enabled, zoom=zoom, background_style=background_style):
        return source
    if not source.is_file():
        if notes is not None:
            notes.append(f"{original_path.name}: Still-Style übersprungen — Datei fehlt.")
        return source

    width = max(2, int(project.width or 1920))
    height = max(2, int(project.height or 1080))
    style = (background_style or STILL_BACKGROUND_VINTAGE).strip().lower() or STILL_BACKGROUND_VINTAGE
    output = styled_still_output_path(
        project.work_dir_path,
        folder_name,
        source,
        width=width,
        height=height,
        zoom=zoom,
        background_style=style,
    )
    token_path = output.with_suffix(output.suffix + ".token")
    token = _cache_token(source, width=width, height=height, zoom=zoom, background_style=style)

    if output.is_file() and token_path.is_file():
        try:
            if token_path.read_text(encoding="utf-8").strip() == token and output.stat().st_size > 0:
                if notes is not None:
                    notes.append(
                        f"{original_path.name}: Still-Style Cache "
                        f"(zoom={zoom:.2f}, background={style}) → `{output.name}`"
                    )
                return output
        except OSError:
            pass

    try:
        render_styled_still_image(
            source,
            output,
            width=width,
            height=height,
            zoom=zoom,
            background_style=style,
        )
        token_path.write_text(token, encoding="utf-8")
        if notes is not None:
            notes.append(
                f"{original_path.name}: Still-Style gerendert "
                f"(zoom={zoom:.2f}, background={style}) → `{output.name}`"
            )
        return output
    except Exception as exc:  # noqa: BLE001 — Export soll Original behalten
        if notes is not None:
            notes.append(f"{original_path.name}: Still-Style fehlgeschlagen ({exc}) — Original verwendet.")
        return source
