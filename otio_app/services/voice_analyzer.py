"""Voice-over-Analyse."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from otio_app.analysis_models import (
    VoiceAnalysisDocument,
    VoiceFileAnalysis,
    VoiceSegment,
)
from otio_app.defaults import (
    VOICE_BACKEND_GEMINI,
    VOICE_BACKEND_WHISPER,
    resolve_voice_backend,
)
from otio_app.models import Project
from otio_app.project_layout import safe_path_is_dir
from otio_app.services.gemini_client import (
    GeminiNotConfiguredError,
    analyze_voice_over_file,
)
from otio_app.services.media_utils import list_media_files, probe_duration_seconds
from otio_app.services.whisper_transcriber import (
    WhisperNotAvailableError,
    transcribe_audio_file,
)


def _safe_cache_name(value: str) -> str:
    return value.replace(" ", "_").replace("/", "_")


def _cache_path(
    project: Project,
    audio_path: Path,
    backend: str,
    engine_model: str,
) -> Path:
    cache_dir = (
        project.work_dir_path
        / "cache"
        / "voice"
        / backend
        / _safe_cache_name(engine_model)
    )
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"{_safe_cache_name(audio_path.name)}.json"


def _load_cached_voice(cache_file: Path) -> Optional[VoiceFileAnalysis]:
    if not cache_file.is_file():
        return None
    try:
        payload = json.loads(cache_file.read_text(encoding="utf-8"))
        return VoiceFileAnalysis.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        try:
            cache_file.unlink(missing_ok=True)
        except OSError:
            pass
        return None


def _is_completed_voice(entry: VoiceFileAnalysis) -> bool:
    if entry.segments:
        return True
    return bool(entry.error)


def _save_cached_voice(cache_file: Path, entry: VoiceFileAnalysis) -> None:
    cache_file.write_text(entry.model_dump_json(indent=2), encoding="utf-8")


def _segments_from_payload(payload: dict) -> list[VoiceSegment]:
    segments = payload.get("segments", [])
    return [
        VoiceSegment(
            start_sec=float(item.get("start_sec", 0.0)),
            end_sec=float(item.get("end_sec", 0.0)),
            text=str(item.get("text", "")).strip(),
        )
        for item in segments
        if str(item.get("text", "")).strip()
    ]


def _analyze_single_voice_file(
    project: Project,
    audio_path: Path,
    *,
    backend: str,
    gemini_model: Optional[str],
    whisper_model: Optional[str],
    use_api: bool,
) -> VoiceFileAnalysis:
    resolved_backend = resolve_voice_backend(backend)
    engine_model = whisper_model or "small"
    if resolved_backend == VOICE_BACKEND_GEMINI:
        engine_model = gemini_model or "gemini-2.0-flash"

    cache_file = _cache_path(project, audio_path, resolved_backend, engine_model)
    cached = _load_cached_voice(cache_file)
    if cached is not None and _is_completed_voice(cached):
        return cached

    duration = probe_duration_seconds(audio_path)
    entry = VoiceFileAnalysis(path=str(audio_path), duration_sec=duration)

    if not use_api:
        entry.error = "Analyse nicht bestätigt."
        _save_cached_voice(cache_file, entry)
        return entry

    try:
        if resolved_backend == VOICE_BACKEND_WHISPER:
            payload = {
                "segments": transcribe_audio_file(
                    audio_path,
                    project.language,
                    model_size=whisper_model,
                )
            }
        else:
            payload = analyze_voice_over_file(
                audio_path,
                project.language,
                model=gemini_model,
            )
        entry.segments = _segments_from_payload(payload)
        entry.error = None
    except (GeminiNotConfiguredError, WhisperNotAvailableError):
        raise
    except Exception as exc:  # noqa: BLE001
        entry.error = str(exc)

    _save_cached_voice(cache_file, entry)
    return entry


def analyze_voice_over(
    project: Project,
    *,
    use_api: bool = True,
    backend: str = VOICE_BACKEND_WHISPER,
    model: Optional[str] = None,
    whisper_model: Optional[str] = None,
) -> VoiceAnalysisDocument:
    """Analysiert alle Audios im Voice-over-Ordner."""
    voice_dir = project.voice_over_dir
    if not safe_path_is_dir(voice_dir):
        raise FileNotFoundError(f"Voice-over-Ordner nicht gefunden: {voice_dir}")

    audio_files = list_media_files(voice_dir)
    if not audio_files:
        raise FileNotFoundError(f"Keine Audiodateien in {voice_dir}")

    results: list[VoiceFileAnalysis] = []
    for audio_path in audio_files:
        try:
            results.append(
                _analyze_single_voice_file(
                    project,
                    audio_path,
                    backend=backend,
                    gemini_model=model,
                    whisper_model=whisper_model,
                    use_api=use_api,
                )
            )
        except (GeminiNotConfiguredError, WhisperNotAvailableError):
            raise
        except Exception as exc:  # noqa: BLE001
            results.append(
                VoiceFileAnalysis(
                    path=str(audio_path),
                    duration_sec=probe_duration_seconds(audio_path),
                    error=str(exc),
                )
            )

    document = VoiceAnalysisDocument(
        project_id=project.id,
        language=project.language,
        files=results,
    )
    output_path = project.voice_analysis_path
    output_path.write_text(
        document.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return document
