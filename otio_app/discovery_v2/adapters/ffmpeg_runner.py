"""Eng begrenzter FFmpeg-Aufruf für Discovery V2 (Argumentliste, kein shell)."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass


class FFmpegRunnerError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class FFmpegRunResult:
    argv: list[str]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def ffmpeg_encoder_available(encoder: str) -> bool:
    """True, wenn ``ffmpeg -encoders`` den Encoder auflistet."""
    if not ffmpeg_available():
        return False
    try:
        completed = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
            shell=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    if completed.returncode != 0:
        return False
    token = f" {encoder} "
    text = f" {completed.stdout or ''} "
    return token in text


def run_ffmpeg(
    argv: list[str],
    *,
    timeout_sec: int,
) -> FFmpegRunResult:
    """Führt FFmpeg als Argumentliste aus (nie ``shell=True``)."""
    if not argv or argv[0] != "ffmpeg":
        raise FFmpegRunnerError(
            "ffmpeg_failed",
            "FFmpeg-Befehl muss mit 'ffmpeg' beginnen.",
        )
    if not ffmpeg_available():
        raise FFmpegRunnerError("ffmpeg_not_found", "ffmpeg ist nicht vorhanden.")
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_sec,
            shell=False,
        )
    except FileNotFoundError as exc:
        raise FFmpegRunnerError("ffmpeg_not_found", "ffmpeg ist nicht vorhanden.") from exc
    except subprocess.TimeoutExpired as exc:
        stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
        raise FFmpegRunnerError(
            "ffmpeg_timeout",
            f"FFmpeg-Timeout nach {timeout_sec}s.",
        ) from exc

    return FFmpegRunResult(
        argv=list(argv),
        returncode=int(completed.returncode),
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )
