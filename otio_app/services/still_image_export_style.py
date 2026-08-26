"""Still-Image-Styling unmittelbar vor OTIO-Export.

Komponiert JPEG/PNG auf Zielauflösung: Zoom (Default 0.8, fit) auf
gewähltem Hintergrund. Kein Cut-Plan-Rebuild nötig — greift nur beim Export.

Hintergründe:
- ``vintage``: Vintage-Papiertextur (Cover-Fill)
- ``paper_edge``: dieselbe Textur + Foto mit unregelmäßigem Papierrand + Schatten
- ``none``: schwarzer Hintergrund, nur Zoom

Textur-Suche (erste Trefferdatei gewinnt):
1. ``still_vintage_paper.{jpg,jpeg,png,webp}`` im Projektordner
2. dieselbe Datei im Work-Dir
3. mitgelieferte Default-Textur unter ``otio_app/assets/``
"""

from __future__ import annotations

import hashlib
import math
from pathlib import Path

from otio_app.models import Project
from otio_app.project_layout import get_folder_clean_output_dir, safe_folder_slug
from otio_app.services.media_utils import is_image_media

__all__ = [
    "STILL_BACKGROUND_VINTAGE",
    "STILL_BACKGROUND_PAPER_EDGE",
    "STILL_BACKGROUND_NONE",
    "DEFAULT_STILL_IMAGE_ZOOM",
    "VINTAGE_BACKGROUND_RGB",
    "VINTAGE_PAPER_TEXTURE_NAMES",
    "bundled_vintage_paper_path",
    "resolve_vintage_paper_texture",
    "still_style_needed",
    "styled_still_output_path",
    "render_styled_still_image",
    "ensure_styled_still_for_export",
]

STILL_BACKGROUND_VINTAGE = "vintage"
STILL_BACKGROUND_PAPER_EDGE = "paper_edge"
STILL_BACKGROUND_NONE = "none"
DEFAULT_STILL_IMAGE_ZOOM = 0.8

# Fallback, falls keine Texturdatei gefunden wird.
VINTAGE_BACKGROUND_RGB = (196, 168, 130)
VINTAGE_VIGNETTE_RGB = (120, 96, 70)
PAPER_EDGE_SHADOW_RGB = (70, 55, 40)

VINTAGE_PAPER_TEXTURE_NAMES = (
    "still_vintage_paper.jpg",
    "still_vintage_paper.jpeg",
    "still_vintage_paper.png",
    "still_vintage_paper.webp",
)


def bundled_vintage_paper_path() -> Path:
    return Path(__file__).resolve().parent.parent / "assets" / "still_vintage_paper.jpg"


def resolve_vintage_paper_texture(
    search_dirs: list[Path] | None = None,
    *,
    texture_path: Path | None = None,
) -> Path | None:
    """Liefert die erste vorhandene Vintage-Papiertextur.

    Expliziter ``texture_path`` gewinnt, danach Dateien in ``search_dirs``,
    zuletzt die mitgelieferte Default-Textur.
    """
    if texture_path is not None:
        try:
            resolved = texture_path.expanduser()
            if resolved.is_file():
                return resolved.resolve()
        except OSError:
            pass

    candidates: list[Path] = []
    for folder in search_dirs or []:
        if folder is None:
            continue
        root = Path(folder)
        for name in VINTAGE_PAPER_TEXTURE_NAMES:
            candidates.append(root / name)
    candidates.append(bundled_vintage_paper_path())
    for path in candidates:
        try:
            if path.is_file():
                return path.resolve()
        except OSError:
            continue
    return None


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
    if style in {STILL_BACKGROUND_VINTAGE, STILL_BACKGROUND_PAPER_EDGE}:
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


def _cache_token(
    source: Path,
    *,
    width: int,
    height: int,
    zoom: float,
    background_style: str,
    texture_path: Path | None = None,
) -> str:
    try:
        stat = source.stat()
        payload = f"{source.resolve()}|{stat.st_mtime_ns}|{stat.st_size}|{width}x{height}|{zoom}|{background_style}"
    except OSError:
        payload = f"{source}|{width}x{height}|{zoom}|{background_style}"
    if texture_path is not None:
        try:
            tex_stat = texture_path.stat()
            payload += f"|tex:{texture_path.resolve()}|{tex_stat.st_mtime_ns}|{tex_stat.st_size}"
        except OSError:
            payload += f"|tex:{texture_path}"
    else:
        payload += "|tex:procedural"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


def _cover_resize(image, width: int, height: int):
    """Skaliert so, dass die Fläche vollständig gefüllt wird (Center-Crop)."""
    from PIL import Image

    src_w, src_h = image.size
    if src_w <= 0 or src_h <= 0:
        return image.resize((max(1, width), max(1, height)), Image.Resampling.LANCZOS)
    scale = max(width / src_w, height / src_h)
    new_w = max(width, int(round(src_w * scale)))
    new_h = max(height, int(round(src_h * scale)))
    resized = image.resize((new_w, new_h), Image.Resampling.LANCZOS)
    left = max(0, (new_w - width) // 2)
    top = max(0, (new_h - height) // 2)
    return resized.crop((left, top, left + width, top + height))


def _draw_procedural_vintage_background(img, width: int, height: int) -> None:
    """Fallback: flächiges Pergament + Vignette, wenn keine Textur da ist."""
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


def _draw_vintage_background(
    img,
    width: int,
    height: int,
    *,
    texture_path: Path | None = None,
) -> None:
    """Füllt img mit Vintage-Papiertextur (Cover-Fill) oder proceduralem Fallback."""
    from PIL import Image, ImageOps

    path = resolve_vintage_paper_texture(texture_path=texture_path)
    if path is None:
        _draw_procedural_vintage_background(img, width, height)
        return
    try:
        with Image.open(path) as opened:
            texture = ImageOps.exif_transpose(opened).convert("RGB")
        filled = _cover_resize(texture, width, height)
        img.paste(filled)
    except OSError:
        _draw_procedural_vintage_background(img, width, height)


def _fit_size(src_w: int, src_h: int, max_w: int, max_h: int) -> tuple[int, int]:
    if src_w <= 0 or src_h <= 0 or max_w <= 0 or max_h <= 0:
        return max(1, max_w), max(1, max_h)
    scale = min(max_w / src_w, max_h / src_h)
    return max(1, int(round(src_w * scale))), max(1, int(round(src_h * scale)))


def _torn_edge_mask(width: int, height: int, *, seed: int = 0) -> "Image.Image":
    """Unregelmäßige Papierrand-Maske (deckle/torn) für Still-Overlays."""
    from PIL import Image, ImageDraw, ImageFilter

    # Deterministisch aus Seed (Dateiname/Größe), kein Zufall zwischen Exports.
    rng = seed & 0xFFFFFFFF
    def _next() -> float:
        nonlocal rng
        rng = (1103515245 * rng + 12345) & 0x7FFFFFFF
        return rng / 0x7FFFFFFF

    mask = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(mask)
    amp = max(3, min(width, height) // 45)
    step = max(4, min(width, height) // 80)
    points: list[tuple[int, int]] = []

    # Top edge L→R
    for x in range(0, width + 1, step):
        y = int(round(amp * (0.55 + 0.45 * math.sin(x * 0.09 + _next() * 6.0))))
        points.append((min(width, x), max(0, y)))
    # Right edge T→B
    for y in range(0, height + 1, step):
        x = width - int(round(amp * (0.55 + 0.45 * math.sin(y * 0.11 + _next() * 5.0))))
        points.append((min(width, max(0, x)), min(height, y)))
    # Bottom edge R→L
    for x in range(width, -1, -step):
        y = height - int(round(amp * (0.55 + 0.45 * math.sin(x * 0.08 + _next() * 4.0))))
        points.append((max(0, min(width, x)), min(height, max(0, y))))
    # Left edge B→T
    for y in range(height, -1, -step):
        x = int(round(amp * (0.55 + 0.45 * math.sin(y * 0.10 + _next() * 7.0))))
        points.append((max(0, x), max(0, min(height, y))))

    if len(points) >= 3:
        draw.polygon(points, fill=255)
    else:
        draw.rectangle((amp, amp, width - amp, height - amp), fill=255)
    # Weicher Papierrand statt harter Scherenschnitt.
    blur = max(1, amp // 2)
    return mask.filter(ImageFilter.GaussianBlur(radius=blur))


def _paste_with_paper_edge(
    canvas,
    photo_rgba,
    *,
    offset: tuple[int, int],
) -> None:
    """Foto mit Papierrand + weichem Schatten auf Canvas legen."""
    from PIL import Image, ImageFilter

    fitted_w, fitted_h = photo_rgba.size
    seed = fitted_w * 131 + fitted_h * 17 + offset[0] * 3 + offset[1]
    edge_mask = _torn_edge_mask(fitted_w, fitted_h, seed=seed)
    photo = photo_rgba.copy()
    photo.putalpha(edge_mask)

    # Weicher Schlagschatten unter dem Zettel.
    shadow = Image.new("RGBA", (fitted_w + 24, fitted_h + 24), (0, 0, 0, 0))
    shadow_layer = Image.new("RGBA", (fitted_w, fitted_h), (*PAPER_EDGE_SHADOW_RGB, 140))
    shadow_layer.putalpha(edge_mask.point(lambda a: int(a * 0.55)))
    shadow.paste(shadow_layer, (8, 10), shadow_layer)
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=7))
    canvas_rgba = canvas.convert("RGBA")
    canvas_rgba.alpha_composite(shadow, dest=(offset[0] - 4, offset[1] - 2))
    canvas_rgba.alpha_composite(photo, dest=offset)
    canvas.paste(canvas_rgba.convert("RGB"))


def render_styled_still_image(
    source: Path,
    output: Path,
    *,
    width: int,
    height: int,
    zoom: float = DEFAULT_STILL_IMAGE_ZOOM,
    background_style: str = STILL_BACKGROUND_VINTAGE,
    texture_path: Path | None = None,
    search_dirs: list[Path] | None = None,
) -> Path:
    """Komponiert Still auf Zielrahmen; schreibt JPEG nach output."""
    from PIL import Image, ImageOps

    width = max(2, int(width))
    height = max(2, int(height))
    zoom = max(0.05, min(1.0, float(zoom)))
    style = (background_style or STILL_BACKGROUND_NONE).strip().lower()

    with Image.open(source) as opened:
        foreground = ImageOps.exif_transpose(opened).convert("RGBA")

    uses_parchment = style in {
        STILL_BACKGROUND_VINTAGE,
        STILL_BACKGROUND_PAPER_EDGE,
    }
    if style not in {
        "",
        STILL_BACKGROUND_NONE,
        STILL_BACKGROUND_VINTAGE,
        STILL_BACKGROUND_PAPER_EDGE,
    }:
        # Unbekannter Style → Vintage als sicherer Default
        style = STILL_BACKGROUND_VINTAGE
        uses_parchment = True

    canvas = Image.new(
        "RGB",
        (width, height),
        VINTAGE_BACKGROUND_RGB if uses_parchment else (0, 0, 0),
    )
    if uses_parchment:
        resolved_texture = resolve_vintage_paper_texture(
            search_dirs=search_dirs, texture_path=texture_path
        )
        _draw_vintage_background(
            canvas, width, height, texture_path=resolved_texture
        )

    # Paper-edge braucht etwas Rand für Schatten/Zacken — Zoom leicht begrenzen.
    effective_zoom = min(zoom, 0.92) if style == STILL_BACKGROUND_PAPER_EDGE else zoom
    box_w = max(1, int(round(width * effective_zoom)))
    box_h = max(1, int(round(height * effective_zoom)))
    fitted_w, fitted_h = _fit_size(foreground.width, foreground.height, box_w, box_h)
    resized = foreground.resize((fitted_w, fitted_h), Image.Resampling.LANCZOS)
    offset = ((width - fitted_w) // 2, (height - fitted_h) // 2)
    if style == STILL_BACKGROUND_PAPER_EDGE:
        _paste_with_paper_edge(canvas, resized, offset=offset)
    else:
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
    texture = resolve_vintage_paper_texture(
        search_dirs=[project.project_root_path, project.work_dir_path],
    )
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
    token = _cache_token(
        source,
        width=width,
        height=height,
        zoom=zoom,
        background_style=style,
        texture_path=texture,
    )

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
            texture_path=texture,
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
