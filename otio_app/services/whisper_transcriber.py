"""Lokale Voice-over-Transkription mit faster-whisper."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from otio_app.config import get_whisper_model_from_env
from otio_app.defaults import WHISPER_MODEL_CHOICES, resolve_whisper_model


class WhisperNotAvailableError(RuntimeError):
    """faster-whisper ist nicht installiert."""


_model_cache: dict[str, Any] = {}


def is_whisper_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return False
    return True


def _get_model(model_size: Optional[str] = None):
    if not is_whisper_available():
        raise WhisperNotAvailableError(
            "faster-whisper ist nicht installiert. "
            "Bitte `pip install -r requirements.txt` ausführen."
        )

    from faster_whisper import WhisperModel

    resolved = resolve_whisper_model(model_size)
    if resolved not in _model_cache:
        _model_cache[resolved] = WhisperModel(
            resolved,
            device="cpu",
            compute_type="int8",
        )
    return _model_cache[resolved]


def transcribe_audio_file(
    audio_path: Path,
    language: str,
    *,
    model_size: Optional[str] = None,
) -> list[dict[str, float | str]]:
    """Transkribiert eine Audiodatei lokal und liefert Segmente mit Zeitstempeln."""
    model = _get_model(model_size)
    lang = language.strip().lower() or None
    segments, _info = model.transcribe(
        str(audio_path),
        language=lang,
        vad_filter=True,
    )
    results: list[dict[str, float | str]] = []
    for segment in segments:
        text = segment.text.strip()
        if not text:
            continue
        results.append(
            {
                "start_sec": float(segment.start),
                "end_sec": float(segment.end),
                "text": text,
            }
        )
    return results


def get_default_whisper_model() -> str:
    return get_whisper_model_from_env()
