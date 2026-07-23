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
) -> Path:
    """Rendert ein lesbares Gap-/Bridge-Slate unter ``_otio_enhanced/placeholders/``."""
    duration = max(0.04, float(end_seconds) - float(start_seconds))
    rate = max(1.0, float(fps) or 25.0)
    gap = (gap_id or f"gap_{shot_id}").strip() or f"gap_{shot_id}"
    visual = (needed_visual or "").strip() or "(keine needed_visual)"
    key = _cache_key(
        shot_id,
        gap,
        visual,
        f"{start_seconds:.3f}",
        f"{end_seconds:.3f}",
        f"{duration:.3f}",
        f"{rate:.3f}",
        f"{width}x{height}",
        "slate_v1",
    )
    out = placeholders_dir(project) / f"placeholder_{shot_id}_{key}.mp4"
    if out.is_file() and out.stat().st_size > 0:
        return out

    lines = [
        "PLACEHOLDER / OPEN GAP",
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
        f"color=c=0x2b1d1d:s={width}x{height}:d={duration:.3f}:r={rate:.3f}",
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


def ensure_still_hold_video(
    project: Project,
    image_path: Path,
    *,
    duration_seconds: float,
    fps: float,
    width: int | None = None,
    height: int | None = None,
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
    vf = still_hold_video_filter(width=tw or None, height=th or None)
    key = _cache_key(
        str(source),
        f"{duration_seconds:.3f}",
        f"{rate:.3f}",
        f"{tw}x{th}",
        vf,
        "still_v2",
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
