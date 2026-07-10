"""Medien-Dateien finden und technisch prüfen."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from functools import lru_cache
from pathlib import Path
from typing import Optional

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".heic"}
AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}

MEDIA_EXTENSIONS = VIDEO_EXTENSIONS | IMAGE_EXTENSIONS | AUDIO_EXTENSIONS

NO_ANALYZABLE_MEDIA_DESCRIPTION = "Keine analysierbaren Medien gefunden."

_TIMECODE_RE = re.compile(
    r"^(?P<h>\d{2}):(?P<m>\d{2}):(?P<s>\d{2})[:;](?P<f>\d{2})$"
)


@dataclass(frozen=True)
class MediaTiming:
    """Eingebetteter Medien-Start und Dauer für OTIO available/source_range."""

    start_sec: float = 0.0
    duration_sec: float | None = None
    rate: float = 25.0


def parse_r_frame_rate(value: str | None) -> float | None:
    if not value:
        return None
    try:
        if "/" in value:
            return float(Fraction(value))
        return float(value)
    except (ValueError, ZeroDivisionError):
        return None


def parse_smpte_timecode(value: str, rate: float) -> float | None:
    match = _TIMECODE_RE.match(value.strip())
    if not match:
        return None
    hours = int(match.group("h"))
    minutes = int(match.group("m"))
    seconds = int(match.group("s"))
    frames = int(match.group("f"))
    if rate <= 0:
        return None
    return hours * 3600 + minutes * 60 + seconds + frames / rate


def probe_media_timing(path: Path, *, default_rate: float = 25.0) -> MediaTiming:
    """Liest Start-Timecode/PTS und Dauer — für Resolve-konforme OTIO-Ranges."""
    rate = default_rate
    start_sec = 0.0
    duration_sec: float | None = None

    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=index,codec_type,codec_tag_string,r_frame_rate,start_time:"
                "stream_tags=timecode:format_tags=timecode",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            check=False,
            timeout=120,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        duration_sec = probe_duration_seconds(path)
        return MediaTiming(start_sec=0.0, duration_sec=duration_sec, rate=rate)

    if result.returncode != 0:
        duration_sec = probe_duration_seconds(path)
        return MediaTiming(start_sec=0.0, duration_sec=duration_sec, rate=rate)

    try:
        payload = json.loads((result.stdout or b"").decode("utf-8", errors="replace") or "{}")
    except json.JSONDecodeError:
        duration_sec = probe_duration_seconds(path)
        return MediaTiming(start_sec=0.0, duration_sec=duration_sec, rate=rate)

    format_info = payload.get("format", {})
    raw_duration = format_info.get("duration")
    if raw_duration is not None:
        try:
            duration_sec = float(raw_duration)
        except (TypeError, ValueError):
            duration_sec = None

    streams = payload.get("streams", [])
    preferred = [
        stream
        for stream in streams
        if (stream.get("codec_type") or "").lower() in {"video", "audio"}
    ] or streams

    embedded_tc: str | None = None
    stream_start: float | None = None

    for stream in preferred:
        stream_rate = parse_r_frame_rate(stream.get("r_frame_rate"))
        if stream_rate:
            rate = stream_rate
        raw_start = stream.get("start_time")
        if raw_start is not None:
            try:
                stream_start = float(raw_start)
            except (TypeError, ValueError):
                pass

    # SMPTE-Timecode-Metadaten liegen bei professionellen Kameras (Canon,
    # Sony, ARRI, ...) oft NICHT auf der Video-/Audiospur, sondern auf einer
    # eigenen "tmcd"-Datenspur (codec_type=data, codec_tag_string=tmcd). Die
    # vorherige Suche war auf video/audio-Streams beschränkt und hat diese
    # Spur ignoriert — dadurch wurde für viele Dateien fälschlich
    # start_sec=0.0 angenommen, obwohl die Datei einen von Null abweichenden
    # eingebetteten Timecode hat. Beim OTIO-Export entstand dadurch ein
    # Mismatch zwischen dem in der Timeline angenommenen (0-basierten)
    # Timecode und dem, was DaVinci Resolve beim Reconnect/Import
    # tatsächlich in der Datei findet ("Media Offline" / "No overlap
    # between specified target timecodes and located file timecodes").
    # Daher werden JETZT ALLE Streams durchsucht, mit Vorrang für die
    # dedizierte tmcd-Spur (die kanonische SMPTE-Timecode-Quelle).
    tmcd_tc: str | None = None
    other_stream_tc: str | None = None
    for stream in streams:
        tags = stream.get("tags") or {}
        tag_value = tags.get("timecode")
        if not tag_value:
            continue
        is_tmcd_track = (stream.get("codec_type") or "").lower() == "data" or (
            stream.get("codec_tag_string") or ""
        ).lower() == "tmcd"
        if is_tmcd_track and tmcd_tc is None:
            tmcd_tc = str(tag_value)
        elif other_stream_tc is None:
            other_stream_tc = str(tag_value)
    if tmcd_tc or other_stream_tc:
        embedded_tc = tmcd_tc or other_stream_tc

    format_tags = format_info.get("tags") or {}
    if format_tags.get("timecode"):
        embedded_tc = str(format_tags["timecode"])

    if embedded_tc:
        parsed = parse_smpte_timecode(embedded_tc, rate)
        if parsed is not None:
            start_sec = parsed
    elif stream_start is not None and stream_start > 0.001:
        start_sec = stream_start

    if duration_sec is None:
        duration_sec = probe_duration_seconds(path)

    return MediaTiming(start_sec=start_sec, duration_sec=duration_sec, rate=rate)


def is_video_media(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXTENSIONS


def is_image_media(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_EXTENSIONS


def _is_media_filename(name: str) -> bool:
    return Path(name).suffix.lower() in MEDIA_EXTENSIONS


def _list_media_names_iterdir(directory: Path) -> tuple[list[str], str | None]:
    try:
        return [entry.name for entry in directory.iterdir()], None
    except OSError as exc:
        return [], str(exc)


def _list_media_names_os_listdir(directory: Path) -> tuple[list[str], str | None]:
    try:
        return list(os.listdir(directory)), None
    except OSError as exc:
        return [], str(exc)


def _list_media_names_glob(directory: Path) -> tuple[list[str], str | None]:
    try:
        return [entry.name for entry in directory.glob("*")], None
    except OSError as exc:
        return [], str(exc)


def _list_media_names_subprocess(directory: Path) -> tuple[list[str], str | None]:
    """Fallback für macOS/iCloud: Dateinamen mit /bin/ls lesen."""
    try:
        result = subprocess.run(
            ["/bin/ls", "-1p", str(directory)],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return [], str(exc)

    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip()
        return [], message or f"ls exit {result.returncode}"

    names: list[str] = []
    for line in result.stdout.splitlines():
        entry = line.strip().rstrip("/")
        if entry and not entry.startswith("."):
            names.append(entry)
    return names, None


def _collect_media_filenames(directory: Path) -> tuple[list[str], bool]:
    """Sammelt Mediendateinamen mit mehreren Lese-Strategien (iCloud-tolerant)."""
    names: set[str] = set()
    used_subprocess = False

    for lister in (
        _list_media_names_iterdir,
        _list_media_names_os_listdir,
        _list_media_names_glob,
    ):
        found, _error = lister(directory)
        names.update(found)

    if directory.is_dir():
        for ext in MEDIA_EXTENSIONS:
            try:
                for entry in directory.glob(f"*{ext}"):
                    names.add(entry.name)
            except OSError:
                pass

    if not names:
        ls_names, _ls_error = _list_media_names_subprocess(directory)
        if ls_names:
            names.update(ls_names)
            used_subprocess = True

    media_names = sorted(
        (name for name in names if _is_media_filename(name)),
        key=str.casefold,
    )
    return media_names, used_subprocess


def list_media_files(directory: Path) -> list[Path]:
    """Listet Mediendateien in einem Ordner (nur lesen, iCloud-Fallbacks)."""
    if not directory.is_dir():
        return []

    media_names, _used_subprocess = _collect_media_filenames(directory)
    files: list[Path] = []
    for name in media_names:
        child = directory / name
        try:
            if child.is_file():
                files.append(child)
                continue
        except OSError:
            pass
        files.append(child)
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
            check=False,
        )
    except FileNotFoundError:
        return None
    if result.returncode != 0:
        return None
    try:
        stdout = (result.stdout or b"").decode("utf-8", errors="replace")
        payload = json.loads(stdout or "{}")
        duration = payload.get("format", {}).get("duration")
        return float(duration) if duration is not None else None
    except (json.JSONDecodeError, ValueError, TypeError):
        return None


def ffmpeg_has_drawtext() -> bool:
    """Prüft, ob die lokale ffmpeg-Installation den drawtext-Filter enthält."""
    return _probe_ffmpeg_drawtext()


@lru_cache(maxsize=1)
def _probe_ffmpeg_drawtext() -> bool:
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-filters"],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    output = f"{result.stdout}\n{result.stderr}"
    return " drawtext " in output or output.strip().endswith("drawtext")
