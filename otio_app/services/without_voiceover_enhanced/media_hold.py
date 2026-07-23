"""Resolve-taugliche Hold-Medien für Stills und Video-Nachlauf."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from otio_app.models import Project
from otio_app.services.without_voiceover_enhanced.paths import assert_enhanced_work_root


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


def ensure_still_hold_video(
    project: Project,
    image_path: Path,
    *,
    duration_seconds: float,
    fps: float,
) -> Path:
    """JPEG/PNG → kurzes H.264-Video der geplanten Haltedauer (Resolve-sicher)."""
    if duration_seconds <= 0:
        raise MediaHoldError("Still-Hold-Dauer muss positiv sein.")
    source = Path(image_path).expanduser().resolve()
    if not source.is_file():
        raise MediaHoldError(f"Still fehlt: {source}")
    rate = max(1.0, float(fps) or 25.0)
    key = _cache_key(str(source), f"{duration_seconds:.3f}", f"{rate:.3f}", "still")
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
