"""ElevenLabs-TTS-Client — reiner HTTP-Client (Phase 6).

Wichtig: Der API-Key wird NIEMALS in Logs, Exceptions oder JSON-Dateien
geschrieben — nur als HTTP-Header `xi-api-key` übertragen. Fällt der
`with-timestamps`-Endpoint aus, wird NICHT still auf normales TTS ohne
Timestamps zurückgefallen — der Fehler wird sichtbar gemacht.
"""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass, field
from typing import Any

import requests

from otio_app.defaults import (
    ELEVENLABS_API_BASE_URL,
    normalize_elevenlabs_output_format,
)
from otio_app.services.api_keys import get_api_key
from otio_app.services.voiceover_generation.models import ElevenLabsSettings

__all__ = [
    "ElevenLabsTtsError",
    "ElevenLabsTtsResult",
    "is_elevenlabs_configured",
    "build_tts_request_metadata",
    "synthesize_speech_with_timestamps",
    "audio_extension_for_output_format",
    "tts_request_timeout_seconds",
]

# Connect kurz, Read länger: with-timestamps + wav_* liefert großes JSON.
_CONNECT_TIMEOUT_SEC = 30.0
_MIN_READ_TIMEOUT_SEC = 180.0
_MAX_READ_TIMEOUT_SEC = 600.0
_TTS_MAX_ATTEMPTS = 3
_TTS_RETRY_BACKOFF_SEC = (2.0, 5.0, 10.0)


class ElevenLabsTtsError(RuntimeError):
    """Fehler beim ElevenLabs-TTS-Aufruf. Enthält niemals den API-Key."""


@dataclass
class ElevenLabsTtsResult:
    audio_bytes: bytes
    alignment: dict[str, Any] = field(default_factory=dict)
    normalized_alignment: dict[str, Any] = field(default_factory=dict)
    response_metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def audio_base64_present(self) -> bool:
        return bool(self.audio_bytes)


def is_elevenlabs_configured() -> bool:
    return bool(get_api_key("ELEVENLABS_API_KEY"))


def audio_extension_for_output_format(output_format: str) -> tuple[str, bool]:
    """Leitet die Dateiendung aus dem ElevenLabs output_format ab.

    Gibt (extension, is_uncertain) zurück — is_uncertain=True bedeutet, dass
    die Endung nur geraten wurde (§5: 'sonst .mp3 mit Warnung').

    ``wav_*`` liefert von ElevenLabs einen echten WAV-Container (empfohlen,
    Default ``wav_48000``). ``pcm_*`` ist raw PCM ohne Header — wird hier
    ebenfalls als ``.wav`` abgelegt (Legacy); für Resolve ``wav_*`` nutzen.
    """
    fmt = (output_format or "").strip().lower()
    if fmt.startswith("mp3"):
        return ".mp3", False
    if fmt.startswith("opus"):
        return ".opus", False
    if fmt.startswith(("pcm", "wav", "ulaw", "alaw")):
        return ".wav", False
    return ".mp3", True


def _voice_settings_payload(settings: ElevenLabsSettings) -> dict[str, Any]:
    return {
        "stability": settings.stability,
        "similarity_boost": settings.similarity_boost,
        "style": settings.style,
        "use_speaker_boost": settings.use_speaker_boost,
        "speed": settings.speed,
    }


def build_tts_request_metadata(text: str, settings: ElevenLabsSettings) -> dict[str, Any]:
    """Öffentliche Request-Metadaten OHNE API-Key/Header — sicher zum Speichern.

    Der Volltext wird bewusst NICHT hier gespeichert (das übernimmt der
    Aufrufer ggf. separat) — hier geht es nur um technische Parameter."""
    metadata: dict[str, Any] = {
        "voice_id": settings.voice_id,
        "model_id": settings.model_id,
        "output_format": normalize_elevenlabs_output_format(settings.output_format),
        "voice_settings": _voice_settings_payload(settings),
        "text_length": len(text),
    }
    if settings.language_code.strip():
        metadata["language_code"] = settings.language_code.strip()
    return metadata


def tts_request_timeout_seconds(text: str, *, output_format: str = "") -> float:
    """Read-Timeout für with-timestamps — skaliert mit Textlänge und WAV."""
    chars = max(0, len(text or ""))
    # Grobe Heuristik: ~0.05s/Zeichen Generation + Transferpuffer.
    timeout = _MIN_READ_TIMEOUT_SEC + chars * 0.05
    fmt = (output_format or "").strip().lower()
    if fmt.startswith("wav") or fmt.startswith("pcm"):
        timeout *= 1.5
    return min(_MAX_READ_TIMEOUT_SEC, max(_MIN_READ_TIMEOUT_SEC, timeout))


def _is_retryable_tts_exception(exc: BaseException) -> bool:
    if isinstance(
        exc,
        (
            requests.Timeout,
            requests.ConnectionError,
            requests.exceptions.ChunkedEncodingError,
        ),
    ):
        return True
    text = str(exc).lower()
    return any(
        token in text
        for token in (
            "remotedisconnected",
            "connection aborted",
            "connection reset",
            "timed out",
            "temporarily unavailable",
        )
    )


def _format_tts_transport_error(exc: BaseException, *, attempts: int, text_len: int) -> str:
    detail = str(exc).strip() or exc.__class__.__name__
    hint = ""
    lowered = detail.lower()
    if "remotedisconnected" in lowered or "connection aborted" in lowered:
        hint = (
            " Häufig bei langen Segmenten mit wav_*/with-timestamps "
            "(große JSON-Antwort). Bitte erneut versuchen; bei Wiederholung "
            "Segment kürzen oder kurzzeitig wav_24000 testen."
        )
    return (
        f"ElevenLabs-Request fehlgeschlagen nach {attempts} Versuch(en) "
        f"({text_len} Zeichen): {detail}.{hint}"
    )


def synthesize_speech_with_timestamps(
    text: str, settings: ElevenLabsSettings
) -> ElevenLabsTtsResult:
    """POST /v1/text-to-speech/{voice_id}/with-timestamps.

    Wirft IMMER ElevenLabsTtsError bei Problemen (fehlender Key, fehlende
    Voice-ID, HTTP-Fehler, fehlende Audiodaten) — niemals ein stiller
    Fallback auf TTS ohne Timestamps und niemals Fake-Alignment-Daten.

    Bei Connection-/Timeout-Fehlern (z. B. RemoteDisconnected) wird mit
    Backoff bis zu ``_TTS_MAX_ATTEMPTS`` mal erneut versucht.
    """
    api_key = get_api_key("ELEVENLABS_API_KEY")
    if not api_key:
        raise ElevenLabsTtsError(
            "ELEVENLABS_API_KEY ist nicht gesetzt. Bitte unter 🔑 API-Schlüssel eintragen."
        )
    voice_id = settings.voice_id.strip()
    if not voice_id:
        raise ElevenLabsTtsError("Keine ElevenLabs Voice-ID konfiguriert.")
    if not text.strip():
        raise ElevenLabsTtsError("Kein Text zum Vertonen vorhanden.")

    output_format = normalize_elevenlabs_output_format(settings.output_format)
    url = f"{ELEVENLABS_API_BASE_URL}/text-to-speech/{voice_id}/with-timestamps"
    body: dict[str, Any] = {
        "text": text,
        "model_id": settings.model_id,
        "voice_settings": _voice_settings_payload(settings),
    }
    if settings.language_code.strip():
        body["language_code"] = settings.language_code.strip()

    # Immer setzen — ohne Query-Param fällt die API auf mp3_44100_128 zurück.
    params: dict[str, str] = {"output_format": output_format}
    read_timeout = tts_request_timeout_seconds(text, output_format=output_format)
    timeout = (_CONNECT_TIMEOUT_SEC, read_timeout)

    last_transport_error: BaseException | None = None
    for attempt in range(1, _TTS_MAX_ATTEMPTS + 1):
        try:
            response = requests.post(
                url,
                params=params,
                json=body,
                headers={"xi-api-key": api_key, "Content-Type": "application/json"},
                timeout=timeout,
            )
        except requests.RequestException as exc:
            last_transport_error = exc
            if attempt < _TTS_MAX_ATTEMPTS and _is_retryable_tts_exception(exc):
                time.sleep(_TTS_RETRY_BACKOFF_SEC[min(attempt - 1, len(_TTS_RETRY_BACKOFF_SEC) - 1)])
                continue
            raise ElevenLabsTtsError(
                _format_tts_transport_error(
                    exc, attempts=attempt, text_len=len(text)
                )
            ) from exc

        if response.status_code >= 400:
            detail = (response.text or "")[:500]
            # 429 / 503 kurz retryen.
            if (
                response.status_code in {429, 502, 503, 504}
                and attempt < _TTS_MAX_ATTEMPTS
            ):
                time.sleep(_TTS_RETRY_BACKOFF_SEC[min(attempt - 1, len(_TTS_RETRY_BACKOFF_SEC) - 1)])
                continue
            raise ElevenLabsTtsError(
                f"ElevenLabs antwortete mit Status {response.status_code}: {detail}"
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise ElevenLabsTtsError(
                f"ElevenLabs-Antwort ist kein gültiges JSON: {exc}"
            ) from exc

        audio_base64 = payload.get("audio_base64")
        if not audio_base64:
            raise ElevenLabsTtsError(
                "ElevenLabs-Antwort enthält keine Audiodaten (audio_base64 fehlt)."
            )

        try:
            audio_bytes = base64.b64decode(audio_base64)
        except (ValueError, TypeError) as exc:
            raise ElevenLabsTtsError(
                f"audio_base64 konnte nicht dekodiert werden: {exc}"
            ) from exc

        if not audio_bytes:
            raise ElevenLabsTtsError("ElevenLabs-Antwort enthält leere Audiodaten.")

        alignment = payload.get("alignment") or {}
        normalized_alignment = payload.get("normalized_alignment") or {}
        if not isinstance(alignment, dict):
            alignment = {}
        if not isinstance(normalized_alignment, dict):
            normalized_alignment = {}

        # Original-Metadaten ohne Audio-Base64 und ohne Secrets — sicher zum Speichern.
        response_metadata = {
            key: value
            for key, value in payload.items()
            if key not in {"audio_base64", "alignment", "normalized_alignment"}
        }
        response_metadata["status_code"] = response.status_code
        response_metadata["tts_attempts"] = attempt
        response_metadata["tts_read_timeout_sec"] = read_timeout

        return ElevenLabsTtsResult(
            audio_bytes=audio_bytes,
            alignment=alignment,
            normalized_alignment=normalized_alignment,
            response_metadata=response_metadata,
        )

    assert last_transport_error is not None
    raise ElevenLabsTtsError(
        _format_tts_transport_error(
            last_transport_error,
            attempts=_TTS_MAX_ATTEMPTS,
            text_len=len(text),
        )
    ) from last_transport_error
