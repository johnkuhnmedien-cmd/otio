"""Resolve-taugliche Hold-Medien für Stills und Video-Nachlauf."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from otio_app.models import Project
from otio_app.services.media_utils import ffmpeg_has_drawtext
from otio_app.services.otio_media_transform import escape_drawtext_value
from otio_app.services.without_voiceover_enhanced.paths import (
    assert_enhanced_work_root,
    placeholders_dir,
)


class MediaHoldError(RuntimeError):
    pass


def _hold_cache_dir(project: Project) -> Path:
    root = assert_enhanced_work_root(project)
    path = root / "exports" / "hold_cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cache_key(*parts: str) -> str:
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]
    return digest


def _default_slate_font() -> Path | None:
    candidates = (
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
        Path("/Library/Fonts/Arial.ttf"),
    )
    for path in candidates:
        if path.is_file():
            return path
    return None


def ensure_gap_placeholder_slate(
    project: Project,
    *,
    shot_id: str,
    gap_id: str,
    needed_visual: str,
    start_seconds: float,
    end_seconds: float,
    fps: float,
    width: int = 1920,
    height: int = 1080,
    color: str = "0x2b1d1d",
    title: str = "PLACEHOLDER / OPEN GAP",
) -> Path:
    """Rendert ein lesbares Gap-/Bridge-Slate unter ``_otio_enhanced/placeholders/``.

    ``color``: ffmpeg lavfi color (z. B. ``0xCC0000`` für Shortfall-Rot).
    """
    duration = max(0.04, float(end_seconds) - float(start_seconds))
    rate = max(1.0, float(fps) or 25.0)
    gap = (gap_id or f"gap_{shot_id}").strip() or f"gap_{shot_id}"
    visual = (needed_visual or "").strip() or "(keine needed_visual)"
    color_key = str(color or "0x2b1d1d").strip() or "0x2b1d1d"
    title_text = (title or "PLACEHOLDER / OPEN GAP").strip() or "PLACEHOLDER / OPEN GAP"
    key = _cache_key(
        shot_id,
        gap,
        visual,
        f"{start_seconds:.3f}",
        f"{end_seconds:.3f}",
        f"{duration:.3f}",
        f"{rate:.3f}",
        f"{width}x{height}",
        color_key,
        title_text,
        "slate_v2",
    )
    out = placeholders_dir(project) / f"placeholder_{shot_id}_{key}.mp4"
    if out.is_file() and out.stat().st_size > 0:
        return out

    lines = [
        title_text,
        f"slot: {shot_id}",
        f"gap: {gap}",
        f"t: {start_seconds:.2f}s – {end_seconds:.2f}s ({duration:.2f}s)",
        f"need: {visual[:80]}",
    ]
    vf_parts: list[str] = []
    font = _default_slate_font()
    if ffmpeg_has_drawtext() and font is not None:
        safe_font = escape_drawtext_value(str(font.resolve()))
        y = 120
        for line in lines:
            safe = escape_drawtext_value(line)
            vf_parts.append(
                "drawtext="
                f"fontfile='{safe_font}':"
                f"text='{safe}':"
                "fontcolor=white:fontsize=36:"
                f"x=80:y={y}:shadowcolor=black@0.6:shadowx=2:shadowy=2"
            )
            y += 56

    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"color=c={color_key}:s={width}x{height}:d={duration:.3f}:r={rate:.3f}",
    ]
    if vf_parts:
        cmd.extend(["-vf", ",".join(vf_parts)])
    cmd.extend(
        [
            "-t",
            f"{duration:.3f}",
            "-r",
            f"{rate:.3f}",
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(out),
        ]
    )
    try:
        result = subprocess.run(cmd, capture_output=True, check=False, timeout=180)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise MediaHoldError(f"Placeholder-Slate fehlgeschlagen: {exc}") from exc
    if result.returncode != 0 or not out.is_file() or out.stat().st_size <= 0:
        err = (result.stderr or b"").decode("utf-8", errors="replace")[:400]
        raise MediaHoldError(f"Placeholder-Slate fehlgeschlagen: {err}")
    return out


def still_hold_video_filter(
    *,
    width: int | None = None,
    height: int | None = None,
) -> str:
    """libx264-sichere Scale/Pad-Kette (gerade Maße, Letterbox auf Projektauflösung).

    Mit Zielauflösung: scale+pad (Letterbox). Sonst mindestens
    ``scale=ceil(iw/2)*2:ceil(ih/2)*2``.
    """
    if width and height and int(width) > 0 and int(height) > 0:
        tw = max(2, (int(width) // 2) * 2)
        th = max(2, (int(height) // 2) * 2)
        return (
            f"scale={tw}:{th}:force_original_aspect_ratio=decrease,"
            f"pad={tw}:{th}:(ow-iw)/2:(oh-ih)/2:black,"
            "setsar=1,"
            "scale=ceil(iw/2)*2:ceil(ih/2)*2"
        )
    return "scale=ceil(iw/2)*2:ceil(ih/2)*2"


def probe_image_aspect_ratio(image_path: Path) -> float | None:
    """Breite/Höhe des Bildes (EXIF-orientiert) oder ``None``."""
    try:
        from PIL import Image, ImageOps
    except Exception:  # noqa: BLE001
        return None
    source = Path(image_path).expanduser()
    if not source.is_file():
        return None
    try:
        with Image.open(source) as opened:
            image = ImageOps.exif_transpose(opened)
            width, height = image.size
    except Exception:  # noqa: BLE001
        return None
    if width <= 0 or height <= 0:
        return None
    return float(width) / float(height)


def still_aspect_allows_cover_pan(
    image_path: Path,
    *,
    min_aspect: float = 1.50,
    max_aspect: float = 2.05,
) -> bool:
    """True wenn das Bild nahe Landscape-16:9 liegt (Cover+Pan sinnvoll)."""
    aspect = probe_image_aspect_ratio(image_path)
    if aspect is None:
        return False
    lo = min(float(min_aspect), float(max_aspect))
    hi = max(float(min_aspect), float(max_aspect))
    return lo <= aspect <= hi


def still_hold_dynamic_zoom_filter(
    *,
    duration_seconds: float,
    fps: float,
    zoom_factor: float = 1.12,
    width: int,
    height: int,
) -> str:
    """Ken-Burns Zoom-in über die Shot-Dauer (ffmpeg zoompan).

    Start bei 1.0, Ende bei ``zoom_factor`` (z. B. 1.12 = +12 %), zentriert.
    ``width``/``height`` müssen Projektauflösung (gerade) sein.
    """
    rate = max(1.0, float(fps) or 25.0)
    frames = max(2, int(round(max(0.01, float(duration_seconds)) * rate)))
    zf = max(1.02, min(1.35, float(zoom_factor)))
    denom = max(1, frames - 1)
    tw = max(2, (int(width) // 2) * 2)
    th = max(2, (int(height) // 2) * 2)
    # z wächst linear von 1 → zf; x/y halten die Bildmitte.
    z_expr = f"1+({zf:.4f}-1)*on/{denom}"
    return (
        f"scale=iw*2:ih*2,"
        f"zoompan=z='{z_expr}':"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d=1:s={tw}x{th}:fps={rate:.3f},"
        "setsar=1,"
        "format=yuv420p"
    )


def still_hold_cover_pan_filter(
    *,
    duration_seconds: float,
    fps: float,
    width: int,
    height: int,
    direction: str = "ltr",
    pan_travel: float = 0.12,
    end_zoom_factor: float = 1.0,
) -> str:
    """16:9 Cover-Fill + horizontaler Schwenk ohne schwarze Ränder.

    1. Foto füllt den Frame komplett (``increase`` + Crop, kein Letterbox).
    2. Extra-Zoom ``z = 1/(1-travel)`` schafft Spielraum für den Schwenk.
    3. ``x`` wandert L→R oder R→L über die Shot-Dauer.
    Optional leichtes zusätzliches Zoom-in über ``end_zoom_factor`` (>1).
    """
    rate = max(1.0, float(fps) or 25.0)
    frames = max(2, int(round(max(0.01, float(duration_seconds)) * rate)))
    denom = max(1, frames - 1)
    tw = max(2, (int(width) // 2) * 2)
    th = max(2, (int(height) // 2) * 2)
    travel = max(0.05, min(0.30, float(pan_travel)))
    # Sichtbarer Anteil = 1/z → z muss ≥ 1/(1-travel) sein, sonst Ränder.
    z_start = 1.0 / (1.0 - travel)
    end_mult = max(1.0, float(end_zoom_factor) or 1.0)
    z_end = z_start * end_mult
    if abs(z_end - z_start) < 1e-4:
        z_expr = f"{z_start:.4f}"
    else:
        z_expr = f"{z_start:.4f}+({z_end:.4f}-{z_start:.4f})*on/{denom}"
    direction_key = (direction or "ltr").strip().lower()
    if direction_key in {"rtl", "right_to_left", "rl"}:
        x_expr = f"(iw-iw/zoom)*(1-on/{denom})"
    else:
        x_expr = f"(iw-iw/zoom)*on/{denom}"
    y_expr = "ih/2-(ih/zoom/2)"
    return (
        f"scale={tw}:{th}:force_original_aspect_ratio=increase,"
        f"crop={tw}:{th},"
        f"scale=iw*2:ih*2,"
        f"zoompan=z='{z_expr}':"
        f"x='{x_expr}':y='{y_expr}':"
        f"d=1:s={tw}x{th}:fps={rate:.3f},"
        "setsar=1,"
        "format=yuv420p"
    )


def ensure_still_hold_video(
    project: Project,
    image_path: Path,
    *,
    duration_seconds: float,
    fps: float,
    width: int | None = None,
    height: int | None = None,
    dynamic_zoom: bool = False,
    zoom_factor: float = 1.12,
    pan_direction: str | None = None,
    pan_travel: float = 0.12,
) -> Path:
    """JPEG/PNG → kurzes H.264-Video der geplanten Haltedauer (Resolve-sicher)."""
    if duration_seconds <= 0:
        raise MediaHoldError("Still-Hold-Dauer muss positiv sein.")
    source = Path(image_path).expanduser().resolve()
    if not source.is_file():
        raise MediaHoldError(f"Still fehlt: {source}")
    rate = max(1.0, float(fps) or 25.0)
    tw = int(width) if width is not None else int(getattr(project, "width", 0) or 0)
    th = int(height) if height is not None else int(getattr(project, "height", 0) or 0)
    pan_dir = (pan_direction or "").strip().lower() or None
    if pan_dir in {"off", "none", "0"}:
        pan_dir = None
    use_pan = pan_dir in {"ltr", "rtl", "left_to_right", "right_to_left", "lr", "rl"}
    use_dynamic = (
        bool(dynamic_zoom) and float(zoom_factor) > 1.001 and tw > 0 and th > 0
    )
    if use_pan and tw > 0 and th > 0:
        # Cover+Pan hat Vorrang vor Letterbox/zentriertem Zoom.
        vf = still_hold_cover_pan_filter(
            duration_seconds=duration_seconds,
            fps=rate,
            width=tw,
            height=th,
            direction="rtl" if pan_dir in {"rtl", "right_to_left", "rl"} else "ltr",
            pan_travel=float(pan_travel),
            end_zoom_factor=float(zoom_factor) if use_dynamic else 1.0,
        )
        cache_tag = f"still_pan_v1_{pan_dir}_{float(pan_travel):.3f}"
    elif use_dynamic:
        vf = still_hold_dynamic_zoom_filter(
            duration_seconds=duration_seconds,
            fps=rate,
            zoom_factor=float(zoom_factor),
            width=tw,
            height=th,
        )
        cache_tag = "still_dyn_v1"
    else:
        vf = still_hold_video_filter(width=tw or None, height=th or None)
        cache_tag = "still_v2"
    key = _cache_key(
        str(source),
        f"{duration_seconds:.3f}",
        f"{rate:.3f}",
        f"{tw}x{th}",
        vf,
        (
            f"{cache_tag}_{float(zoom_factor):.3f}"
            if (use_dynamic or use_pan)
            else cache_tag
        ),
    )
    out = _hold_cache_dir(project) / f"still_hold_{key}.mp4"
    if out.is_file() and out.stat().st_size > 0:
        return out
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-loop",
        "1",
        "-i",
        str(source),
        "-vf",
        vf,
        "-t",
        f"{duration_seconds:.3f}",
        "-r",
        f"{rate:.3f}",
        "-pix_fmt",
        "yuv420p",
        "-an",
        str(out),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, check=False, timeout=180)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise MediaHoldError(f"Still-Hold-Video fehlgeschlagen: {exc}") from exc
    if result.returncode != 0 or not out.is_file():
        err = (result.stderr or b"").decode("utf-8", errors="replace")[:400]
        raise MediaHoldError(f"Still-Hold-Video fehlgeschlagen: {err}")
    return out


def ensure_video_padded_hold(
    project: Project,
    video_path: Path,
    *,
    target_duration_seconds: float,
    fps: float,
) -> Path:
    """Verlängert Video durch Klonen des letzten Frames (tpad) bis Ziel-Dauer."""
    if target_duration_seconds <= 0:
        raise MediaHoldError("Video-Hold-Dauer muss positiv sein.")
    source = Path(video_path).expanduser().resolve()
    if not source.is_file():
        raise MediaHoldError(f"Video fehlt: {source}")
    rate = max(1.0, float(fps) or 25.0)
    key = _cache_key(str(source), f"{target_duration_seconds:.3f}", f"{rate:.3f}", "tpad")
    out = _hold_cache_dir(project) / f"video_hold_{key}.mp4"
    if out.is_file() and out.stat().st_size > 0:
        return out
    # tpad stop_duration = zusätzliche Sekunden nach dem natürlichen Ende.
    # Wir kennen die Quelldauer nicht exakt hier — nutzen -t Ziel und tpad großzügig.
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-vf",
        f"tpad=stop_mode=clone:stop_duration={target_duration_seconds:.3f}",
        "-t",
        f"{target_duration_seconds:.3f}",
        "-r",
        f"{rate:.3f}",
        "-an",
        "-pix_fmt",
        "yuv420p",
        str(out),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, check=False, timeout=300)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise MediaHoldError(f"Video-Hold fehlgeschlagen: {exc}") from exc
    if result.returncode != 0 or not out.is_file():
        err = (result.stderr or b"").decode("utf-8", errors="replace")[:400]
        raise MediaHoldError(f"Video-Hold fehlgeschlagen: {err}")
    return out
