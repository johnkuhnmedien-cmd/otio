"""Voice-over-Analyse."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Optional

from otio_app.analysis_models import (
    VoiceAnalysisDocument,
    VoiceFileAnalysis,
    VoiceSegment,
)
from otio_app.defaults import (
    DEFAULT_GEMINI_MODEL,
    VOICE_BACKEND_GEMINI,
    VOICE_BACKEND_WHISPER,
    resolve_voice_backend,
)
from otio_app.models import Project
from otio_app.project_layout import safe_path_is_dir
from otio_app.services.analysis_cancel import AnalysisCancelledError
from otio_app.services.analysis_progress import (
    ProgressCallback,
    VoiceAnalysisRunReport,
    noop_progress,
)
from otio_app.services.gemini_client import (
    GeminiNotConfiguredError,
    analyze_voice_over_file,
)
from otio_app.services.media_utils import list_media_files, probe_duration_seconds
from otio_app.services.whisper_transcriber import (
    WhisperNotAvailableError,
    transcribe_audio_file,
)

ShouldCancel = Callable[[], bool]


def _safe_cache_name(value: str) -> str:
    return value.replace(" ", "_").replace("/", "_")


def _cache_path(
    project: Project,
    audio_path: Path,
    backend: str,
    engine_model: str,
) -> Path:
    cache_dir = (
        project.language_work_dir_path
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


def _is_cancelled(should_cancel: ShouldCancel | None) -> bool:
    return bool(should_cancel and should_cancel())


def _write_voice_document(project: Project, results: list[VoiceFileAnalysis]) -> bool:
    if not results:
        return False
    document = VoiceAnalysisDocument(
        project_id=project.id,
        language=project.language,
        files=results,
    )
    output_path = project.voice_analysis_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        document.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return True


def _analyze_single_voice_file(
    project: Project,
    audio_path: Path,
    *,
    backend: str,
    gemini_model: Optional[str],
    whisper_model: Optional[str],
    use_api: bool,
    should_cancel: ShouldCancel | None = None,
) -> tuple[VoiceFileAnalysis, str]:
    resolved_backend = resolve_voice_backend(backend)
    engine_model = whisper_model or "small"
    if resolved_backend == VOICE_BACKEND_GEMINI:
        engine_model = gemini_model or DEFAULT_GEMINI_MODEL

    cache_file = _cache_path(project, audio_path, resolved_backend, engine_model)
    cached = _load_cached_voice(cache_file)
    if cached is not None and _is_completed_voice(cached):
        return cached, "cache"

    if _is_cancelled(should_cancel):
        raise AnalysisCancelledError()

    duration = probe_duration_seconds(audio_path)
    entry = VoiceFileAnalysis(path=str(audio_path), duration_sec=duration)

    if not use_api:
        entry.error = "Analyse nicht bestätigt."
        _save_cached_voice(cache_file, entry)
        return entry, "fehler"

    if _is_cancelled(should_cancel):
        raise AnalysisCancelledError()

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
        outcome = "neu"
    except (GeminiNotConfiguredError, WhisperNotAvailableError):
        raise
    except Exception as exc:  # noqa: BLE001
        entry.error = str(exc)
        outcome = "fehler"

    _save_cached_voice(cache_file, entry)
    return entry, outcome


def analyze_voice_over(
    project: Project,
    *,
    use_api: bool = True,
    backend: str = VOICE_BACKEND_WHISPER,
    model: Optional[str] = None,
    whisper_model: Optional[str] = None,
    on_progress: ProgressCallback = noop_progress,
    should_cancel: ShouldCancel | None = None,
) -> tuple[VoiceAnalysisDocument, VoiceAnalysisRunReport]:
    """Analysiert alle Audios im Voice-over-Ordner."""
    report = VoiceAnalysisRunReport()
    voice_dir = project.voice_over_dir
    if not safe_path_is_dir(voice_dir):
        raise FileNotFoundError(f"Voice-over-Ordner nicht gefunden: {voice_dir}")

    audio_files = list_media_files(voice_dir)
    if not audio_files:
        raise FileNotFoundError(f"Keine Audiodateien in {voice_dir}")

    on_progress(
        "start",
        {
            "total_files": len(audio_files),
            "backend": backend,
        },
    )

    results: list[VoiceFileAnalysis] = []
    cancelled = False

    for file_index, audio_path in enumerate(audio_files, start=1):
        if _is_cancelled(should_cancel):
            cancelled = True
            break
        on_progress(
            "file_start",
            {
                "file_name": audio_path.name,
                "file_index": file_index,
                "file_count": len(audio_files),
            },
        )
        try:
            entry, outcome = _analyze_single_voice_file(
                project,
                audio_path,
                backend=backend,
                gemini_model=model,
                whisper_model=whisper_model,
                use_api=use_api,
                should_cancel=should_cancel,
            )
            results.append(entry)
            if outcome == "cache":
                report.files_cached += 1
            elif outcome == "neu":
                report.files_analyzed += 1
            else:
                report.files_failed += 1
                if entry.error:
                    report.failures.append(f"{audio_path.name}: {entry.error}")
            on_progress(
                "file_done",
                {
                    "file_name": audio_path.name,
                    "file_index": file_index,
                    "file_count": len(audio_files),
                    "outcome": outcome,
                    "error": entry.error,
                },
            )
        except AnalysisCancelledError:
            cancelled = True
            break
        except (GeminiNotConfiguredError, WhisperNotAvailableError):
            raise
        except Exception as exc:  # noqa: BLE001
            entry = VoiceFileAnalysis(
                path=str(audio_path),
                duration_sec=probe_duration_seconds(audio_path),
                error=str(exc),
            )
            results.append(entry)
            report.files_failed += 1
            report.failures.append(f"{audio_path.name}: {exc}")
            on_progress(
                "file_done",
                {
                    "file_name": audio_path.name,
                    "file_index": file_index,
                    "file_count": len(audio_files),
                    "outcome": "fehler",
                    "error": str(exc),
                },
            )

    report.cancelled = cancelled
    report.output_written = _write_voice_document(project, results)

    if cancelled:
        on_progress(
            "cancelled",
            {
                "total_files": len(audio_files),
                "done": len(results),
            },
        )
    else:
        on_progress(
            "complete",
            {
                "total_files": len(audio_files),
                "done": len(results),
            },
        )

    document = VoiceAnalysisDocument(
        project_id=project.id,
        language=project.language,
        files=results,
    )
    return document, report
