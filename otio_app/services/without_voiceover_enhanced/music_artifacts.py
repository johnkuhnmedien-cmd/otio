"""ElevenLabs Music artefacts: request/result JSON, fingerprints, staleness."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from otio_app.models import Project
from otio_app.services.without_voiceover_enhanced.io_utils import write_json
from otio_app.services.without_voiceover_enhanced.paths import (
    music_chapter_dir,
    music_intro_dir,
    music_request_path,
    music_result_path,
    music_wav_path,
)

MusicScope = Literal["intro", "chapter"]
MusicStatus = Literal["completed", "failed", "stale", "unavailable", "missing"]

OUTPUT_CONTRACT = "wav_pcm_s16le_48000_stereo"


def ensure_music_dir(project: Project, *, scope: MusicScope, folder_name: str = "") -> Path:
    if scope == "intro":
        path = music_intro_dir(project)
    else:
        path = music_chapter_dir(project, folder_name)
    path.mkdir(parents=True, exist_ok=True)
    return path


def fingerprint_text(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()[:32]


def fingerprint_duration(target_duration_seconds: float) -> str:
    # Millisecond-stable fingerprint so float noise does not flip stale.
    ms = int(round(float(target_duration_seconds) * 1000.0))
    return fingerprint_text(f"duration_ms:{ms}")


def resolved_timing_fingerprint(
    *,
    script_version: str,
    target_duration_seconds: float,
) -> str:
    return fingerprint_text(
        f"{script_version}|{int(round(float(target_duration_seconds) * 1000.0))}"
    )


def load_json_if_present(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def save_music_request(project: Project, payload: dict[str, Any], **scope_kw: Any) -> Path:
    ensure_music_dir(project, scope=payload.get("scope") or scope_kw.get("scope") or "chapter", folder_name=str(payload.get("chapter_id") or scope_kw.get("folder_name") or ""))
    scope = str(payload.get("scope") or "chapter")
    folder = str(payload.get("chapter_id") or "")
    path = music_request_path(project, scope=scope, folder_name=folder)
    # Strip any accidental secrets.
    safe = {k: v for k, v in payload.items() if "key" not in str(k).lower() and "secret" not in str(k).lower() and "authorization" not in str(k).lower()}
    write_json(path, safe)
    return path


def save_music_result(project: Project, payload: dict[str, Any]) -> Path:
    scope = str(payload.get("scope") or "chapter")
    folder = str(payload.get("chapter_id") or "")
    ensure_music_dir(project, scope=scope if scope in {"intro", "chapter"} else "chapter", folder_name=folder)
    path = music_result_path(project, scope=scope, folder_name=folder)
    safe = {k: v for k, v in payload.items() if "key" not in str(k).lower() and "secret" not in str(k).lower()}
    write_json(path, safe)
    return path


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def music_status_for_scope(
    project: Project,
    *,
    scope: MusicScope,
    folder_name: str = "",
    script_fingerprint: str,
    resolved_timing_fingerprint: str,
    api_key_present: bool,
) -> dict[str, Any]:
    """UI-facing status without mutating artefacts."""
    wav = music_wav_path(project, scope=scope, folder_name=folder_name)
    result = load_json_if_present(music_result_path(project, scope=scope, folder_name=folder_name))
    if not api_key_present:
        return {
            "status": "unavailable",
            "message": "ElevenLabs Music nicht verfügbar – API-Key fehlt.",
            "music_path": str(wav) if wav.is_file() else "",
            "actual_duration_seconds": None,
        }
    if result is None and not wav.is_file():
        return {
            "status": "missing",
            "message": "Music fehlt",
            "music_path": "",
            "actual_duration_seconds": None,
        }
    stored_script = str((result or {}).get("script_fingerprint") or "")
    stored_timing = str((result or {}).get("resolved_timing_fingerprint") or "")
    status = str((result or {}).get("status") or "")
    if wav.is_file() and status == "completed":
        if stored_script != script_fingerprint or stored_timing != resolved_timing_fingerprint:
            return {
                "status": "stale",
                "message": (
                    "⚠ Music veraltet — Python Timing oder Skript hat sich geändert. "
                    "Bitte ElevenLabs Music neu erzeugen."
                ),
                "music_path": str(wav),
                "actual_duration_seconds": (result or {}).get("actual_duration_seconds"),
            }
        return {
            "status": "completed",
            "message": f"✅ ElevenLabs Music · {float((result or {}).get('actual_duration_seconds') or 0):.2f}s WAV",
            "music_path": str(wav),
            "actual_duration_seconds": (result or {}).get("actual_duration_seconds"),
        }
    if status == "failed":
        return {
            "status": "failed",
            "message": str((result or {}).get("message") or "Music fehlgeschlagen"),
            "music_path": str(wav) if wav.is_file() else "",
            "actual_duration_seconds": (result or {}).get("actual_duration_seconds"),
        }
    if wav.is_file():
        return {
            "status": "stale",
            "message": "⚠ Music veraltet — bitte neu erzeugen.",
            "music_path": str(wav),
            "actual_duration_seconds": (result or {}).get("actual_duration_seconds"),
        }
    return {
        "status": "missing",
        "message": "Music fehlt",
        "music_path": "",
        "actual_duration_seconds": None,
    }


def usable_music_wav_path(
    project: Project,
    *,
    scope: MusicScope,
    folder_name: str = "",
    script_fingerprint: str,
    resolved_timing_fingerprint: str,
) -> Path | None:
    """Return local completed non-stale WAV path, else None."""
    status = music_status_for_scope(
        project,
        scope=scope,
        folder_name=folder_name,
        script_fingerprint=script_fingerprint,
        resolved_timing_fingerprint=resolved_timing_fingerprint,
        api_key_present=True,
    )
    if status.get("status") != "completed":
        return None
    path = Path(str(status.get("music_path") or ""))
    return path if path.is_file() else None
