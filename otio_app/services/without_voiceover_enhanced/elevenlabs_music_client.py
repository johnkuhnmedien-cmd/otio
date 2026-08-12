"""ElevenLabs Music HTTP client — reuses ELEVENLABS_API_KEY (no parallel secrets)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import requests

from otio_app.defaults import ELEVENLABS_API_BASE_URL
from otio_app.services.api_keys import get_api_key
from otio_app.services.voiceover_generation.elevenlabs_client import (
    is_elevenlabs_configured,
)

__all__ = [
    "ElevenLabsMusicError",
    "ElevenLabsMusicResult",
    "is_elevenlabs_music_configured",
    "compose_music",
    "MUSIC_MODEL_ID",
    "MUSIC_LENGTH_MS_MIN",
    "MUSIC_LENGTH_MS_MAX",
]

MUSIC_MODEL_ID = "music_v2"
MUSIC_LENGTH_MS_MIN = 3000
MUSIC_LENGTH_MS_MAX = 600_000
# Transport format; project artefact is always converted to WAV PCM.
MUSIC_TRANSPORT_OUTPUT_FORMAT = "mp3_48000_192"

_CONNECT_TIMEOUT_SEC = 30.0
_READ_TIMEOUT_SEC = 600.0


class ElevenLabsMusicError(RuntimeError):
    """Music API failure — never includes the API key."""


@dataclass
class ElevenLabsMusicResult:
    audio_bytes: bytes
    content_type: str = ""
    song_id: str | None = None
    response_headers: dict[str, str] = field(default_factory=dict)


def is_elevenlabs_music_configured() -> bool:
    return is_elevenlabs_configured()


def compose_music(
    *,
    prompt: str,
    music_length_ms: int,
    model_id: str = MUSIC_MODEL_ID,
    force_instrumental: bool = True,
    output_format: str = MUSIC_TRANSPORT_OUTPUT_FORMAT,
    timeout_seconds: float | None = None,
) -> ElevenLabsMusicResult:
    """POST /v1/music — prompt + exact duration. No composition plans."""
    api_key = get_api_key("ELEVENLABS_API_KEY")
    if not api_key:
        raise ElevenLabsMusicError(
            "ELEVENLABS_API_KEY ist nicht gesetzt. Bitte unter 🔑 API-Schlüssel eintragen."
        )
    length = int(music_length_ms)
    if length < MUSIC_LENGTH_MS_MIN or length > MUSIC_LENGTH_MS_MAX:
        raise ElevenLabsMusicError(
            f"music_length_ms={length} außerhalb {MUSIC_LENGTH_MS_MIN}–{MUSIC_LENGTH_MS_MAX}."
        )
    url = f"{ELEVENLABS_API_BASE_URL.rstrip('/')}/music"
    params = {"output_format": output_format}
    body: dict[str, Any] = {
        "prompt": prompt,
        "music_length_ms": length,
        "model_id": model_id,
        "force_instrumental": bool(force_instrumental),
    }
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "audio/*",
    }
    read_timeout = float(timeout_seconds) if timeout_seconds else _READ_TIMEOUT_SEC
    try:
        response = requests.post(
            url,
            params=params,
            json=body,
            headers=headers,
            timeout=(_CONNECT_TIMEOUT_SEC, read_timeout),
        )
    except requests.Timeout as exc:
        raise ElevenLabsMusicError("ElevenLabs Music Timeout.") from exc
    except requests.RequestException as exc:
        raise ElevenLabsMusicError(f"ElevenLabs Music Netzwerkfehler: {exc}") from exc

    if response.status_code in {401, 403}:
        raise ElevenLabsMusicError(
            f"ElevenLabs Music Auth-Fehler HTTP {response.status_code}."
        )
    if response.status_code == 422:
        raise ElevenLabsMusicError(
            f"ElevenLabs Music ungültige Anfrage HTTP 422: {_safe_error_text(response)}"
        )
    if response.status_code == 429:
        raise ElevenLabsMusicError("ElevenLabs Music Rate-Limit HTTP 429.")
    if response.status_code >= 500:
        raise ElevenLabsMusicError(
            f"ElevenLabs Music Serverfehler HTTP {response.status_code}."
        )
    if response.status_code != 200:
        raise ElevenLabsMusicError(
            f"ElevenLabs Music HTTP {response.status_code}: {_safe_error_text(response)}"
        )
    audio = response.content or b""
    if not audio:
        raise ElevenLabsMusicError("ElevenLabs Music lieferte leere Audio-Antwort.")
    song_id = (
        response.headers.get("song-id")
        or response.headers.get("Song-Id")
        or response.headers.get("x-song-id")
    )
    return ElevenLabsMusicResult(
        audio_bytes=audio,
        content_type=str(response.headers.get("Content-Type") or ""),
        song_id=str(song_id) if song_id else None,
        response_headers={
            k: v
            for k, v in response.headers.items()
            if k.lower() not in {"xi-api-key", "authorization"}
        },
    )


def _safe_error_text(response: requests.Response) -> str:
    text = (response.text or "")[:400]
    # Never echo accidental key material.
    return text.replace(get_api_key("ELEVENLABS_API_KEY") or "", "***")
