"""LLM-Traceability für die Voice-over-Generierungs-Pipeline.

Speichert pro LLM-Aufruf einen eigenen, nachvollziehbaren Run-Ordner unter
_otio/voiceover_generation/llm_runs/{run_id}/:

- prompt.txt                     — der exakte Prompt
- raw_llm_response.json          — Rohantwort + Provider/Modell/Latenz/Tokens
- parsed_llm_response.json       — geparstes Ergebnis oder parse_error
- llm_request_manifest.json      — Zusammenfassung (Pflichtfelder siehe unten)

Niemals API-Keys, Header oder andere Secrets in diesen Dateien.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from otio_app.models import Project
from otio_app.project_layout import get_llm_run_dir
from otio_app.services.voiceover_generation.models import LlmRunManifest

STAGE_STYLE_PROFILE = "style_profile"
STAGE_DRAMATURGY = "dramaturgy"
STAGE_FOLDER_VOICEOVER = "folder_voiceover"
STAGE_VOICEOVER_REVIEW = "voiceover_review"
STAGE_VOICEOVER_CORRECTION = "voiceover_correction"
STAGE_ASSET_ALLOCATION_CORRECTION = "asset_allocation_correction"
STAGE_INTRO_HOOK = "intro_hook"
STAGE_PROJECT_BRIEF_TITLE = "project_brief_title"
STAGE_CUT_PLAN_SUPPLEMENT_QUERY = "cut_plan_supplement_query"
STAGE_YOUTUBE_PUBLISH = "youtube_publish"
STAGE_YOUTUBE_QUIZ = "youtube_quiz"

STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_PARSE_FAILED = "PARSE_FAILED"


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def content_hash_of_model(model: Any, *, exclude: set[str] | None = None) -> str:
    """Wie content_hash(), aber schließt volatile Felder (z. B. generated_at)
    aus. Wichtig für Staleness-Vergleiche (§13): Ohne diesen Ausschluss würde
    ein nie gespeichertes Default-Objekt (z. B. ProjectBrief ohne project_brief
    .json) bei jedem Laden einen neuen Zeitstempel und damit fälschlich einen
    neuen Hash bekommen, obwohl sich der Inhalt nicht geändert hat."""
    if model is None:
        return ""
    exclude_fields = {"generated_at"} | (exclude or set())
    payload = model.model_dump(mode="json", exclude=exclude_fields)
    return content_hash(json.dumps(payload, sort_keys=True, ensure_ascii=False))


def create_llm_run_dir(project: Project, stage: str) -> tuple[str, Path]:
    """Erzeugt einen eindeutigen run_id-Ordner. `stage` wird nur für Logging/Debug
    genutzt — die Verzeichnisstruktur selbst enthält keine Stage-Unterordner,
    das Manifest im Run-Ordner dokumentiert die Stage."""
    run_id = str(uuid.uuid4())
    run_dir = get_llm_run_dir(project.language_work_dir_path, run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_id, run_dir


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(payload, "model_dump"):
        text = json.dumps(payload.model_dump(mode="json"), indent=2, ensure_ascii=False)
    else:
        text = json.dumps(payload, indent=2, ensure_ascii=False)
    path.write_text(text, encoding="utf-8")


def write_llm_prompt(run_dir: Path, prompt: str) -> Path:
    path = run_dir / "prompt.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(prompt, encoding="utf-8")
    return path


def write_llm_raw_response(
    run_dir: Path,
    *,
    raw_text: str,
    provider: str,
    model: str,
    latency_ms: int = 0,
    token_usage: dict[str, int] | None = None,
) -> Path:
    path = run_dir / "raw_llm_response.json"
    _write_json(
        path,
        {
            "raw_text": raw_text,
            "provider": provider,
            "model": model,
            "latency_ms": latency_ms,
            "token_usage": token_usage or {},
        },
    )
    return path


def write_llm_parsed_response(run_dir: Path, parsed: dict[str, Any]) -> Path:
    path = run_dir / "parsed_llm_response.json"
    _write_json(path, parsed)
    return path


def write_llm_manifest(run_dir: Path, manifest: LlmRunManifest) -> Path:
    path = run_dir / "llm_request_manifest.json"
    _write_json(path, manifest)
    return path
