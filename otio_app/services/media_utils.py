"""Medien-Dateien finden und technisch prüfen."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Optional

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".heic"}
AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}

MEDIA_EXTENSIONS = VIDEO_EXTENSIONS | IMAGE_EXTENSIONS | AUDIO_EXTENSIONS


def list_media_files(directory: Path) -> list[Path]:
    """Listet Mediendateien in einem Ordner (nur lesen)."""
    if not directory.is_dir():
        return []
    files: list[Path] = []
    try:
        for entry in sorted(directory.iterdir()):
            if entry.is_file() and entry.suffix.lower() in MEDIA_EXTENSIONS:
                files.append(entry)
    except OSError:
        return []
    return files


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def probe_duration_seconds(path: Path) -> Optional[float]:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return None
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout or "{}")
        duration = payload.get("format", {}).get("duration")
        return float(duration) if duration is not None else None
    except (json.JSONDecodeError, ValueError, TypeError):
        return None
