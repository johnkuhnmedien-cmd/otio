"""Tests für Modellvergleich-Runner (Diagnose only)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from otio_app.analysis_models import (
    AssetFolderAnalysis,
    AssetMediaAnalysis,
    VoiceAnalysisDocument,
    VoiceFileAnalysis,
    VoiceFolderMappingDocument,
    VoiceFolderMappingEntry,
    VoiceSegment,
)
from otio_app.models import Project
from otio_app.project_layout import get_folder_edit_plan_path
from otio_app.services.model_comparison_runner import (
    ModelComparisonSpec,
    run_model_comparison_batch,
    run_single_model_comparison,
)
from otio_app.services.model_comparison_storage import (
    comparison_run_dir,
    load_comparison_summary,
)
from otio_app.services.plan_llm_client import PlanLlmResponse


def _sample_project(layout: dict[str, Path]) -> Project:
    project = Project(
        id="cmp-runner",
        name="Test",
        project_root=str(layout["project_root"]),
        work_dir=str(layout["work_dir"]),
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )
    from otio_app.services.edit_plan_rules import default_rules, save_edit_plan_rules

    save_edit_plan_rules(project, default_rules(project))
    return project


def _setup_folder(project: Project, layout: dict[str, Path]) -> str:
    voice_path = str(layout["voice_file"])
    media_path = str(layout["project_root"] / "Grand Canyon" / "clip.mp4")
    Path(media_path).write_bytes(b"mp4")

    mapping = VoiceFolderMappingDocument(
        project_id=project.id,
        confirmed=True,
        entries=[
            VoiceFolderMappingEntry(voice_file=voice_path, folder="Grand Canyon", confirmed=True)
        ],
    )
    project.voice_folder_mapping_path.write_text(mapping.model_dump_json(indent=2), encoding="utf-8")

    voice_doc = VoiceAnalysisDocument(
        project_id=project.id,
        language="de",
        files=[
            VoiceFileAnalysis(
                path=voice_path,
                duration_sec=12.0,
                segments=[
                    VoiceSegment(start_sec=0.0, end_sec=6.0, text="Erster Abschnitt."),
                    VoiceSegment(start_sec=6.0, end_sec=12.0, text="Zweiter Abschnitt."),
                ],
            )
        ],
    )
    project.voice_analysis_path.write_text(voice_doc.model_dump_json(indent=2), encoding="utf-8")

    from otio_app.services.inventory_loader import save_folder_inventory

    save_folder_inventory(
        project.folder_inventory_path("Grand Canyon"),
        AssetFolderAnalysis(
            folder="Grand Canyon",
            assets=[
                AssetMediaAnalysis(
                    path=media_path,
                    description="Landschaft",
                    asset_id="asset_1",
                )
            ],
        ),
    )
    return media_path


VALID_BEATS_JSON = json.dumps(
    {
        "beats": [
            {
                "beat_id": "beat_001",
                "parts": [
                    {
                        "text": "Erster",
                        "motif": "Wide",
                        "asset_path": None,
                        "match_quality": "mittel",
                        "desired_duration_sec": 4.0,
                    },
                    {
                        "text": "Teil zwei",
                        "motif": "Detail",
                        "asset_path": None,
                        "match_quality": "gut",
                    },
                ],
            },
            {
                "beat_id": "beat_002",
                "parts": [
                    {
                        "text": "Zweiter",
                        "motif": "River",
                        "asset_path": None,
                        "match_quality": "gut",
                    }
                ],
            },
        ]
    }
)


def test_model_comparison_batch_writes_all_artifacts(
    temp_project_layout: dict[str, Path],
) -> None:
    project = _sample_project(temp_project_layout)
    media_path = _setup_folder(project, temp_project_layout)

    def fake_llm(**kwargs):
        model = kwargs.get("model", "")
        text = VALID_BEATS_JSON.replace("null", f'"{media_path}"')
        return PlanLlmResponse(
            provider="gemini" if not str(model).startswith("openai") else "openai",
            model=str(model),
            raw_text=text,
            latency_ms=120,
            token_usage={"input_tokens": 10, "output_tokens": 20},
            resolved_model_id=str(model),
        )

    with patch(
        "otio_app.services.model_comparison_runner.generate_plan_text_with_metadata",
        side_effect=fake_llm,
    ):
        result = run_model_comparison_batch(
            project,
            folder_name="Grand Canyon",
            model_specs=[
                ModelComparisonSpec(model_id="gemini-3.1-pro-preview", provider="gemini"),
                ModelComparisonSpec(model_id="openai:gpt-5.5", provider="openai"),
            ],
        )

    assert len(result.runs) == 2
    assert result.runs[0].run_id != result.runs[1].run_id
    summary = load_comparison_summary(project, result.comparison_id)
    assert summary is not None
    assert len(summary.runs) == 2

    for run in result.runs:
        run_dir = comparison_run_dir(project, result.comparison_id, run.run_id)
        assert (run_dir / "raw_llm_response.json").is_file()
        assert (run_dir / "parsed_llm_candidate.json").is_file()
        assert (run_dir / "conformed_preview_candidate.json").is_file()
        assert (run_dir / "llm_vs_python_delta.json").is_file()
        assert (run_dir / "effective_rules.json").is_file()
        assert (run_dir / "run_manifest.json").is_file()

    edit_plan_path = get_folder_edit_plan_path(project.work_dir_path, "Grand Canyon")
    assert not edit_plan_path.is_file()


def test_parse_failure_does_not_abort_batch(temp_project_layout: dict[str, Path]) -> None:
    project = _sample_project(temp_project_layout)
    _setup_folder(project, temp_project_layout)
    calls = {"n": 0}

    def fake_llm(**_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return PlanLlmResponse(
                provider="gemini",
                model="gemini-3.1-pro-preview",
                raw_text="not valid json {{{",
                latency_ms=50,
                resolved_model_id="gemini-3.1-pro-preview",
            )
        return PlanLlmResponse(
            provider="openai",
            model="gpt-5.5",
            raw_text=VALID_BEATS_JSON,
            latency_ms=80,
            resolved_model_id="openai:gpt-5.5",
        )

    with patch(
        "otio_app.services.model_comparison_runner.generate_plan_text_with_metadata",
        side_effect=fake_llm,
    ):
        result = run_model_comparison_batch(
            project,
            folder_name="Grand Canyon",
            model_specs=[
                ModelComparisonSpec(model_id="gemini-3.1-pro-preview", provider="gemini"),
                ModelComparisonSpec(model_id="openai:gpt-5.5", provider="openai"),
            ],
        )

    assert len(result.runs) == 2
    assert result.runs[0].preview_status == "PARSE_FAILED"
    assert result.runs[0].parse_error
    assert result.runs[1].raw_part_count >= 1


def test_delta_detects_part_count_difference(temp_project_layout: dict[str, Path]) -> None:
    project = _sample_project(temp_project_layout)
    media_path = _setup_folder(project, temp_project_layout)
    comparison_id = "cmp-single"
    run_id = "run-single"

    llm_json = VALID_BEATS_JSON.replace("null", f'"{media_path}"')

    with patch(
        "otio_app.services.model_comparison_runner.generate_plan_text_with_metadata",
        return_value=PlanLlmResponse(
            provider="gemini",
            model="gemini-3.1-pro-preview",
            raw_text=llm_json,
            latency_ms=33,
            resolved_model_id="gemini-3.1-pro-preview",
        ),
    ):
        entry = run_single_model_comparison(
            project,
            folder_name="Grand Canyon",
            comparison_id=comparison_id,
            run_id=run_id,
            model_id="gemini-3.1-pro-preview",
            editor_hint="",
            shot_min_sec=3.0,
            shot_max_sec=8.0,
            rules_doc=__import__(
                "otio_app.services.edit_plan_rules", fromlist=["default_rules"]
            ).default_rules(project),
        )

    assert entry.raw_part_count == 3
    run_dir = comparison_run_dir(project, comparison_id, run_id)
    delta = json.loads((run_dir / "llm_vs_python_delta.json").read_text(encoding="utf-8"))
    assert "beat_summaries" in delta
    effective = json.loads((run_dir / "effective_rules.json").read_text(encoding="utf-8"))
    assert effective["pipeline"]["normalize_parts"] is False
    assert effective["shot_rules_enabled"] is False


def test_production_build_edit_plan_still_works(
    temp_project_layout: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from otio_app.services.edit_plan_builder import (
        EditPlanBuildStatus,
        EditPlanValidationMode,
        build_edit_plan,
    )

    project = _sample_project(temp_project_layout)
    media_path = _setup_folder(project, temp_project_layout)

    def fake_plan(**_kwargs):
        return [
            {
                "beat_id": "beat_001",
                "parts": [
                    {
                        "text": "Erster Abschnitt.",
                        "motif": "Landschaft",
                        "asset_path": media_path,
                        "match_quality": "gut",
                    }
                ],
            },
            {
                "beat_id": "beat_002",
                "parts": [
                    {
                        "text": "Zweiter Abschnitt.",
                        "motif": "Fluss",
                        "asset_path": media_path,
                        "match_quality": "gut",
                    }
                ],
            },
        ]

    monkeypatch.setattr(
        "otio_app.services.edit_plan_builder.plan_folder_assets",
        fake_plan,
    )
    monkeypatch.setattr(
        "otio_app.services.timeline_plan_builder.probe_duration_seconds",
        lambda _path: 12.0,
    )

    result = build_edit_plan(
        project,
        use_api=True,
        folder_names=["Grand Canyon"],
        validation_mode=EditPlanValidationMode.SKIP,
    )
    assert result.status == EditPlanBuildStatus.ACCEPTED
    assert result.document is not None
