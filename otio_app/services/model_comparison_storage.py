"""Persistenz für LLM-Modellvergleichs-Runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from otio_app.models import Project
from otio_app.project_layout import (
    get_model_comparison_batch_dir,
    get_model_comparison_run_dir,
    get_model_comparison_runs_dir,
    get_model_comparison_summary_path,
)
from otio_app.services.model_comparison_models import (
    LlmVsPythonDeltaDocument,
    ModelComparisonEffectiveRules,
    ModelComparisonRunManifest,
    ModelComparisonSummary,
    ParsedLlmCandidate,
    RawLlmResponseDocument,
)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(payload, "model_dump"):
        text = json.dumps(payload.model_dump(mode="json"), indent=2, ensure_ascii=False)
    else:
        text = json.dumps(payload, indent=2, ensure_ascii=False)
    path.write_text(text, encoding="utf-8")


def run_artifact_path(run_dir: Path, name: str) -> Path:
    return run_dir / name


def write_run_manifest(run_dir: Path, manifest: ModelComparisonRunManifest) -> Path:
    path = run_artifact_path(run_dir, "run_manifest.json")
    _write_json(path, manifest)
    return path


def write_raw_llm_response(run_dir: Path, document: RawLlmResponseDocument) -> Path:
    path = run_artifact_path(run_dir, "raw_llm_response.json")
    _write_json(path, document)
    return path


def write_parsed_llm_candidate(run_dir: Path, candidate: ParsedLlmCandidate) -> Path:
    path = run_artifact_path(run_dir, "parsed_llm_candidate.json")
    _write_json(path, candidate)
    return path


def write_conformed_preview_candidate(run_dir: Path, payload: dict[str, Any]) -> Path:
    path = run_artifact_path(run_dir, "conformed_preview_candidate.json")
    _write_json(path, payload)
    return path


def write_llm_vs_python_delta(run_dir: Path, delta: LlmVsPythonDeltaDocument) -> Path:
    path = run_artifact_path(run_dir, "llm_vs_python_delta.json")
    _write_json(path, delta)
    return path


def write_effective_rules(run_dir: Path, rules: ModelComparisonEffectiveRules) -> Path:
    path = run_artifact_path(run_dir, "effective_rules.json")
    _write_json(path, rules)
    return path


def write_validation_report(run_dir: Path, payload: dict[str, Any]) -> Path:
    path = run_artifact_path(run_dir, "validation_report.json")
    _write_json(path, payload)
    return path


def write_comparison_summary(project: Project, comparison_id: str, summary: ModelComparisonSummary) -> Path:
    path = get_model_comparison_summary_path(project.work_dir_path, comparison_id)
    _write_json(path, summary)
    return path


def load_comparison_summary(project: Project, comparison_id: str) -> ModelComparisonSummary | None:
    path = get_model_comparison_summary_path(project.work_dir_path, comparison_id)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return ModelComparisonSummary.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None


def list_comparison_ids_for_folder(project: Project, folder_name: str) -> list[str]:
    """Listet comparison_id-Werte mit Summary für einen Ordner (neueste zuerst)."""
    root = get_model_comparison_runs_dir(project.work_dir_path)
    if not root.is_dir():
        return []
    matches: list[tuple[float, str]] = []
    for batch_dir in root.iterdir():
        if not batch_dir.is_dir():
            continue
        summary_path = batch_dir / "model_comparison_summary.json"
        if not summary_path.is_file():
            continue
        try:
            payload = json.loads(summary_path.read_text(encoding="utf-8"))
            if payload.get("folder_name") != folder_name:
                continue
            mtime = summary_path.stat().st_mtime
            matches.append((mtime, batch_dir.name))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
    matches.sort(key=lambda entry: entry[0], reverse=True)
    return [comparison_id for _, comparison_id in matches]


def load_run_artifact(run_dir: Path, filename: str) -> dict[str, Any] | None:
    path = run_dir / filename
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def comparison_run_dir(project: Project, comparison_id: str, run_id: str) -> Path:
    return get_model_comparison_run_dir(project.work_dir_path, comparison_id, run_id)


def ensure_run_dir(project: Project, comparison_id: str, run_id: str) -> Path:
    path = comparison_run_dir(project, comparison_id, run_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def list_runs_in_comparison(project: Project, comparison_id: str) -> list[str]:
    batch_dir = get_model_comparison_batch_dir(project.work_dir_path, comparison_id)
    if not batch_dir.is_dir():
        return []
    run_ids: list[str] = []
    for entry in batch_dir.iterdir():
        if entry.is_dir() and (entry / "run_manifest.json").is_file():
            run_ids.append(entry.name)
    return sorted(run_ids)
