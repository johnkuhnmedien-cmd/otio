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
from otio_app.models import Project
from otio_app.project_layout import safe_path_is_dir
from otio_app.services.gemini_client import (
    GeminiNotConfiguredError,
    analyze_voice_over_file,
)
from otio_app.services.media_utils import list_media_files, probe_duration_seconds


def _cache_path(project: Project, audio_path: Path) -> Path:
    cache_dir = project.work_dir_path / "cache" / "voice"
    cache_dir.mkdir(parents=True, exist_ok=True)
    safe_name = audio_path.name.replace(" ", "_")
    return cache_dir / f"{safe_name}.json"


def analyze_voice_over(
    project: Project,
    *,
    use_api: bool = True,
    model: Optional[str] = None,
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
        cached = _cache_path(project, audio_path)
        if cached.is_file():
            payload = json.loads(cached.read_text(encoding="utf-8"))
            results.append(VoiceFileAnalysis.model_validate(payload))
            continue

        duration = probe_duration_seconds(audio_path)
        entry = VoiceFileAnalysis(path=str(audio_path), duration_sec=duration)
        if use_api:
            try:
                payload = analyze_voice_over_file(
                    audio_path, project.language, model=model
                )
                segments = payload.get("segments", [])
                entry.segments = [
                    VoiceSegment(
                        start_sec=float(item.get("start_sec", 0.0)),
                        end_sec=float(item.get("end_sec", 0.0)),
                        text=str(item.get("text", "")).strip(),
                    )
                    for item in segments
                    if str(item.get("text", "")).strip()
                ]
            except GeminiNotConfiguredError:
                raise
            except Exception as exc:  # noqa: BLE001 — Analyse-Fehler pro Datei
                entry.error = str(exc)
        else:
            entry.error = "API-Aufruf nicht bestätigt."

        cached.write_text(
            entry.model_dump_json(indent=2),
            encoding="utf-8",
        )
        results.append(entry)

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
