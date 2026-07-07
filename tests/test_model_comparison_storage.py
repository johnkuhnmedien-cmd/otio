"""Tests für Modellvergleich-Speicherpfade und Artefakte."""

from __future__ import annotations

import json
from pathlib import Path

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
    ModelComparisonSummary,
    ModelComparisonSummaryRunEntry,
    ParsedLlmCandidate,
    RawLlmResponseDocument,
)
from otio_app.services.model_comparison_storage import (
    ensure_run_dir,
    list_comparison_ids_for_folder,
    load_comparison_summary,
    write_comparison_summary,
    write_effective_rules,
    write_llm_vs_python_delta,
    write_parsed_llm_candidate,
    write_raw_llm_response,
    write_run_manifest,
)
from otio_app.services.model_comparison_models import ModelComparisonRunManifest


def _project(tmp_path: Path) -> Project:
    root = tmp_path / "USA"
    root.mkdir()
    return Project(
        id="cmp-storage",
        name="Test",
        project_root=str(root),
        work_dir=str(root / "_otio"),
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )


def test_model_comparison_paths(tmp_path: Path) -> None:
    project = _project(tmp_path)
    work_dir = project.work_dir_path
    comparison_id = "batch-1"
    run_id = "run-1"
    assert get_model_comparison_runs_dir(work_dir).name == "model_comparison_runs"
    assert get_model_comparison_batch_dir(work_dir, comparison_id).name == comparison_id
    assert get_model_comparison_run_dir(work_dir, comparison_id, run_id).name == run_id
    assert get_model_comparison_summary_path(work_dir, comparison_id).name == (
        "model_comparison_summary.json"
    )


def test_write_run_artifacts_roundtrip(tmp_path: Path) -> None:
    project = _project(tmp_path)
    comparison_id = "cmp-abc"
    run_id = "run-xyz"
    run_dir = ensure_run_dir(project, comparison_id, run_id)

    write_run_manifest(
        run_dir,
        ModelComparisonRunManifest(
            run_id=run_id,
            comparison_id=comparison_id,
            provider="openai",
            model="openai:gpt-5.5",
            folder_name="Grand Canyon",
        ),
    )
    write_raw_llm_response(
        run_dir,
        RawLlmResponseDocument(provider="openai", model="gpt-5.5", raw_text='{"beats":[]}'),
    )
    write_parsed_llm_candidate(run_dir, ParsedLlmCandidate(proposed_part_count=0))
    write_effective_rules(run_dir, ModelComparisonEffectiveRules(shot_min_sec=3.0, shot_max_sec=8.0))
    write_llm_vs_python_delta(
        run_dir,
        LlmVsPythonDeltaDocument(changes_count=0, note="No Python changes detected"),
    )

    assert (run_dir / "run_manifest.json").is_file()
    assert (run_dir / "raw_llm_response.json").is_file()
    assert (run_dir / "parsed_llm_candidate.json").is_file()
    assert (run_dir / "effective_rules.json").is_file()
    assert (run_dir / "llm_vs_python_delta.json").is_file()
    delta = json.loads((run_dir / "llm_vs_python_delta.json").read_text(encoding="utf-8"))
    assert delta["changes_count"] == 0


def test_comparison_summary_and_history(tmp_path: Path) -> None:
    project = _project(tmp_path)
    comparison_id = "cmp-history"
    summary = ModelComparisonSummary(
        comparison_id=comparison_id,
        folder_name="Grand Canyon",
        runs=[
            ModelComparisonSummaryRunEntry(
                run_id="r1",
                provider="gemini",
                model="gemini-3.1-pro-preview",
                raw_part_count=3,
            )
        ],
    )
    write_comparison_summary(project, comparison_id, summary)
    loaded = load_comparison_summary(project, comparison_id)
    assert loaded is not None
    assert loaded.folder_name == "Grand Canyon"
    ids = list_comparison_ids_for_folder(project, "Grand Canyon")
    assert comparison_id in ids
