"""ElevenLabs Sound Effects HTTP client — reuses ELEVENLABS_API_KEY."""

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
    "ElevenLabsSfxError",
    "ElevenLabsSfxResult",
    "is_elevenlabs_sfx_configured",
    "generate_sound_effect",
    "SFX_MODEL_ID",
    "SFX_DURATION_SECONDS_MIN",
    "SFX_DURATION_SECONDS_MAX",
    "SFX_PROMPT_INFLUENCE_DEFAULT",
    "SFX_PROMPT_MAX_CHARS",
]

SFX_MODEL_ID = "eleven_text_to_sound_v2"
SFX_DURATION_SECONDS_MIN = 0.5
SFX_DURATION_SECONDS_MAX = 30.0
SFX_PROMPT_INFLUENCE_DEFAULT = 0.3
SFX_PROMPT_MAX_CHARS = 450

_CONNECT_TIMEOUT_SEC = 30.0
_READ_TIMEOUT_SEC = 300.0


class ElevenLabsSfxError(RuntimeError):
    """SFX API failure — never includes the API key."""


@dataclass
class ElevenLabsSfxResult:
    audio_bytes: bytes
    content_type: str = ""
    response_headers: dict[str, str] = field(default_factory=dict)


def is_elevenlabs_sfx_configured() -> bool:
    return is_elevenlabs_configured()


def generate_sound_effect(
    *,
    text: str,
    duration_seconds: float,
    model_id: str = SFX_MODEL_ID,
    loop: bool = False,
    prompt_influence: float = SFX_PROMPT_INFLUENCE_DEFAULT,
    timeout_seconds: float | None = None,
) -> ElevenLabsSfxResult:
    """POST /v1/sound-generation — single non-looping SFX clip."""
    api_key = get_api_key("ELEVENLABS_API_KEY")
    if not api_key:
        raise ElevenLabsSfxError(
            "ELEVENLABS_API_KEY ist nicht gesetzt. Bitte unter 🔑 API-Schlüssel eintragen."
        )
    prompt = str(text or "").strip()
    if not prompt:
        raise ElevenLabsSfxError("SFX-Prompt ist leer.")
    if len(prompt) > SFX_PROMPT_MAX_CHARS:
        raise ElevenLabsSfxError(
            f"SFX-Prompt länger als {SFX_PROMPT_MAX_CHARS} Zeichen "
            f"({len(prompt)})."
        )
    duration = float(duration_seconds)
    if duration < SFX_DURATION_SECONDS_MIN or duration > SFX_DURATION_SECONDS_MAX:
        raise ElevenLabsSfxError(
            f"duration_seconds={duration} außerhalb "
            f"{SFX_DURATION_SECONDS_MIN}–{SFX_DURATION_SECONDS_MAX}."
        )
    url = f"{ELEVENLABS_API_BASE_URL.rstrip('/')}/sound-generation"
    body: dict[str, Any] = {
        "text": prompt,
        "model_id": model_id,
        "duration_seconds": duration,
        "loop": bool(loop),
        "prompt_influence": float(prompt_influence),
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
            json=body,
            headers=headers,
            timeout=(_CONNECT_TIMEOUT_SEC, read_timeout),
        )
    except requests.Timeout as exc:
        raise ElevenLabsSfxError("ElevenLabs SFX Timeout.") from exc
    except requests.RequestException as exc:
        raise ElevenLabsSfxError(f"ElevenLabs SFX Netzwerkfehler: {exc}") from exc

    if response.status_code in {401, 403}:
        raise ElevenLabsSfxError(
            f"ElevenLabs SFX Auth-Fehler HTTP {response.status_code}."
        )
    if response.status_code == 422:
        raise ElevenLabsSfxError(
            f"ElevenLabs SFX ungültige Anfrage HTTP 422: {_safe_error_text(response)}"
        )
    if response.status_code == 429:
        raise ElevenLabsSfxError("ElevenLabs SFX Rate-Limit HTTP 429.")
    if response.status_code >= 500:
        raise ElevenLabsSfxError(
            f"ElevenLabs SFX Serverfehler HTTP {response.status_code}."
        )
    if response.status_code != 200:
        raise ElevenLabsSfxError(
            f"ElevenLabs SFX HTTP {response.status_code}: {_safe_error_text(response)}"
        )
    audio = response.content or b""
    if not audio:
        raise ElevenLabsSfxError("ElevenLabs SFX lieferte leere Audio-Antwort.")
    return ElevenLabsSfxResult(
        audio_bytes=audio,
        content_type=str(response.headers.get("Content-Type") or ""),
        response_headers={
            k: v
            for k, v in response.headers.items()
            if k.lower() not in {"xi-api-key", "authorization"}
        },
    )


def _safe_error_text(response: requests.Response) -> str:
    text = (response.text or "")[:400]
    return text.replace(get_api_key("ELEVENLABS_API_KEY") or "", "***")
