"""Tests für Gemini-Korrektur-Läufe nach Timing-Validierung."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from otio_app.analysis_models import (
    AssetFolderAnalysis,
    AssetMediaAnalysis,
    EditPlanSettings,
    VoiceAnalysisDocument,
    VoiceFileAnalysis,
    VoiceFolderMappingDocument,
    VoiceFolderMappingEntry,
    VoiceSegment,
)
from otio_app.defaults import MAX_GEMINI_PLAN_ATTEMPTS
from otio_app.models import Project
from otio_app.services.edit_plan_builder import (
    EditPlanBuildStatus,
    build_edit_plan,
    gemini_retry_report_path,
)
from otio_app.services.edit_plan_validator import (
    FinalPlanValidationResult,
    PlanValidationError,
    ValidationStatus,
    timing_validation_errors,
)
from otio_app.services.gemini_client import (
    build_plan_folder_correction_instructions,
    build_plan_folder_prompt,
)


def test_build_plan_folder_prompt_includes_audio_offset() -> None:
    prompt = build_plan_folder_prompt(
        folder_name="Folder",
        segment_lines='- beat_id="beat_001" start_sec=0.0 end_sec=5.0 text="Text."',
        asset_lines="- (keine)",
        language="de",
        audio_offset_sec=2.5,
    )
    assert "audio_offset_sec" in prompt
    assert "2.5" in prompt


def test_build_plan_folder_correction_instructions_include_errors_and_previous_plan() -> None:
    previous = {
        "beat_020": [
            {"text": "Teil A", "motif": "See", "match_quality": "gut"},
            {"text": "Teil B", "motif": "Wald", "match_quality": "gut"},
            {"text": "Teil C", "motif": "Ende", "match_quality": "mittel"},
        ]
    }
    prompt = build_plan_folder_correction_instructions(
        errors=["Voice-over wurde auf letztes Textsegment-Ende gekürzt (94.88s < 97.31s)."],
        previous_beats=previous,
        attempt=2,
        max_attempts=MAX_GEMINI_PLAN_ATTEMPTS,
        file_duration_sec=94.88,
        shot_min_sec=5.0,
        shot_max_sec=10.0,
    )
    assert "## Korrektur" in prompt
    assert "94.88s < 97.31s" in prompt
    assert "beat_020: 3 part(s)" in prompt
    assert "neuen vollständigen Plan" in prompt
    assert '"beat_id": "beat_020"' in prompt


def test_timing_validation_errors_filters_timing_markers() -> None:
    errors = [
        "item_001: duration_sec 2.0s < 5.0s",
        "item_002: kein resolved_media_path",
        "Voice-over wurde auf letztes Textsegment-Ende gekürzt (94.88s < 97.31s).",
    ]
    timing = timing_validation_errors(errors)
    assert len(timing) == 2
    assert any("Textsegment" in line for line in timing)
    assert not any("resolved_media_path" in line for line in timing)


def _sample_project(temp_project_layout: dict[str, Path]) -> Project:
    project = Project(
        id="retry-test",
        name="Test",
        project_root=str(temp_project_layout["project_root"]),
        work_dir=str(temp_project_layout["work_dir"]),
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )
    from otio_app.services.edit_plan_rules import RULE_MAX_ASSET_USES, default_rules, save_edit_plan_rules

    rules_doc = default_rules(project)
    for rule in rules_doc.rules:
        if rule.rule_type == RULE_MAX_ASSET_USES:
            rule.enabled = False
    save_edit_plan_rules(project, rules_doc)
    return project


def test_build_edit_plan_retries_gemini_on_timing_validation_failure(
    temp_project_layout: dict[str, Path],
) -> None:
    project = _sample_project(temp_project_layout)
    voice_path = str(temp_project_layout["voice_file"])
    media_path = str(temp_project_layout["project_root"] / "Grand Canyon" / "clip.mp4")

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
                duration_sec=6.0,
                segments=[VoiceSegment(start_sec=0.0, end_sec=6.0, text="Kurzer Text über den Canyon.")],
            )
        ],
    )
    project.voice_analysis_path.write_text(voice_doc.model_dump_json(indent=2), encoding="utf-8")
    Path(media_path).write_bytes(b"mp4")

    from otio_app.services.inventory_loader import save_folder_inventory

    save_folder_inventory(
        project.folder_inventory_path("Grand Canyon"),
        AssetFolderAnalysis(
            folder="Grand Canyon",
            assets=[
                AssetMediaAnalysis(
                    path=media_path,
                    description="Weite Canyon-Landschaft",
                    asset_id="asset_canyon",
                )
            ],
        ),
    )

    gemini_calls: list[dict] = []
    validation_calls = 0

    def fake_plan_folder_assets(**kwargs):
        gemini_calls.append(kwargs)
        return [
            {
                "beat_id": "beat_001",
                "parts": [
                    {
                        "text": "Kurzer Text über den Canyon.",
                        "motif": "Canyon",
                        "asset_path": media_path,
                        "match_quality": "gut",
                    }
                ],
            }
        ]

    def fake_validate_final_edit_plan(*args, **kwargs):
        nonlocal validation_calls
        validation_calls += 1
        if validation_calls == 1:
            return FinalPlanValidationResult(
                ok=False,
                status=ValidationStatus.BLOCKED,
                errors=[
                    PlanValidationError(
                        type="TIMELINE_VALIDATION",
                        message="Voice-over wurde auf letztes Textsegment-Ende gekürzt (94.88s < 97.31s).",
                    )
                ],
            )
        return FinalPlanValidationResult(ok=True, status=ValidationStatus.OK)

    with (
        patch(
            "otio_app.services.edit_plan_builder.plan_folder_assets",
            side_effect=fake_plan_folder_assets,
        ),
        patch(
            "otio_app.services.edit_plan_builder.validate_final_edit_plan",
            side_effect=fake_validate_final_edit_plan,
        ),
        patch(
            "otio_app.services.timeline_plan_builder.probe_duration_seconds",
            return_value=6.0,
        ),
    ):
        result = build_edit_plan(project, use_api=True)

    assert result.status == EditPlanBuildStatus.ACCEPTED
    document = result.document
    assert document is not None
    assert len(gemini_calls) == 2
    assert gemini_calls[0].get("correction_instructions", "") == ""
    assert "## Korrektur" in gemini_calls[1]["correction_instructions"]
    assert validation_calls == 2
    assert any("erneuter Gemini-Lauf" in note for note in document.plan_generation_notes)


def test_build_edit_plan_blocked_after_three_validation_failures(
    temp_project_layout: dict[str, Path],
) -> None:
    project = _sample_project(temp_project_layout)
    voice_path = str(temp_project_layout["voice_file"])
    media_path = str(temp_project_layout["project_root"] / "Grand Canyon" / "clip.mp4")

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
                duration_sec=6.0,
                segments=[VoiceSegment(start_sec=0.0, end_sec=6.0, text="Kurzer Text über den Canyon.")],
            )
        ],
    )
    project.voice_analysis_path.write_text(voice_doc.model_dump_json(indent=2), encoding="utf-8")
    Path(media_path).write_bytes(b"mp4")

    from otio_app.services.inventory_loader import save_folder_inventory

    save_folder_inventory(
        project.folder_inventory_path("Grand Canyon"),
        AssetFolderAnalysis(
            folder="Grand Canyon",
            assets=[
                AssetMediaAnalysis(
                    path=media_path,
                    description="Weite Canyon-Landschaft",
                    asset_id="asset_canyon",
                )
            ],
        ),
    )

    def fake_plan_folder_assets(**kwargs):
        return [
            {
                "beat_id": "beat_001",
                "parts": [
                    {
                        "text": "Kurzer Text über den Canyon.",
                        "motif": "Canyon",
                        "asset_path": media_path,
                        "match_quality": "gut",
                    }
                ],
            }
        ]

    def always_fail_validation(*args, **kwargs):
        return FinalPlanValidationResult(
            ok=False,
            status=ValidationStatus.BLOCKED,
            errors=[
                PlanValidationError(
                    type="ASSET_USAGE_LIMIT_EXCEEDED",
                    asset_id="asset_canyon",
                    usage_count=2,
                    max_allowed=1,
                )
            ],
        )

    with (
        patch(
            "otio_app.services.edit_plan_builder.plan_folder_assets",
            side_effect=fake_plan_folder_assets,
        ),
        patch(
            "otio_app.services.edit_plan_builder.validate_final_edit_plan",
            side_effect=always_fail_validation,
        ),
        patch(
            "otio_app.services.timeline_plan_builder.probe_duration_seconds",
            return_value=6.0,
        ),
    ):
        result = build_edit_plan(project, use_api=True)

    assert result.status == EditPlanBuildStatus.BLOCKED
    assert result.document is None
    assert result.retry_attempts == MAX_GEMINI_PLAN_ATTEMPTS
    assert gemini_retry_report_path(project.work_dir_path).is_file()
