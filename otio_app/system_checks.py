"""Systemabhängigkeiten prüfen."""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    version: str | None
    message: str


def check_python() -> CheckResult:
    version = (
        f"{sys.version_info.major}."
        f"{sys.version_info.minor}."
        f"{sys.version_info.micro}"
    )
    ok = sys.version_info >= (3, 11)
    if ok:
        message = f"Python {version}"
    else:
        message = f"Python {version} — mindestens 3.11 erforderlich"
    return CheckResult(name="Python", ok=ok, version=version, message=message)


def _run_version_command(command: list[str]) -> tuple[bool, str | None, str]:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return False, None, f"{command[0]} nicht gefunden"

    if result.returncode != 0:
        return False, None, f"{command[0]} nicht verfügbar (Exit {result.returncode})"

    output = (result.stdout or result.stderr or "").strip()
    first_line = output.splitlines()[0] if output else ""
    match = re.search(r"(\d+\.\d+(?:\.\d+)?)", first_line)
    version = match.group(1) if match else None
    return True, version, first_line or command[0]


def check_ffmpeg() -> CheckResult:
    ok, version, message = _run_version_command(["ffmpeg", "-version"])
    return CheckResult(name="FFmpeg", ok=ok, version=version, message=message)


def check_ffprobe() -> CheckResult:
    ok, version, message = _run_version_command(["ffprobe", "-version"])
    return CheckResult(name="ffprobe", ok=ok, version=version, message=message)


def check_opentimelineio() -> CheckResult:
    try:
        import opentimelineio as otio
    except ImportError:
        return CheckResult(
            name="OpenTimelineIO",
            ok=False,
            version=None,
            message="opentimelineio nicht installiert",
        )

    version = getattr(otio, "__version__", None)
    message = f"OpenTimelineIO {version}" if version else "OpenTimelineIO installiert"
    return CheckResult(
        name="OpenTimelineIO",
        ok=True,
        version=version,
        message=message,
    )


def check_whisper() -> CheckResult:
    try:
        import faster_whisper
    except ImportError:
        return CheckResult(
            name="Whisper (faster-whisper)",
            ok=False,
            version=None,
            message="faster-whisper nicht installiert — `pip install -r requirements.txt`",
        )

    version = getattr(faster_whisper, "__version__", None)
    message = (
        f"faster-whisper {version} installiert (lokale Voice-over-Transkription)"
        if version
        else "faster-whisper installiert (lokale Voice-over-Transkription)"
    )
    return CheckResult(
        name="Whisper (faster-whisper)",
        ok=True,
        version=version,
        message=message,
    )


def run_all_checks() -> list[CheckResult]:
    return [
        check_python(),
        check_ffmpeg(),
        check_ffprobe(),
        check_opentimelineio(),
        check_whisper(),
    ]
