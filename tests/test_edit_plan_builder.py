"""Tests für Schnittplan-Erstellung."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from otio_app.analysis_models import (
    AssetFolderAnalysis,
    AssetMediaAnalysis,
    EditPlanRule,
    EditPlanRulesDocument,
    EditPlanSettings,
    InventoryDocument,
    VoiceAnalysisDocument,
    VoiceFileAnalysis,
    VoiceFolderMappingDocument,
    VoiceFolderMappingEntry,
    VoiceSegment,
)
from otio_app.models import Project
from otio_app.services.edit_plan_rules import RULE_MAX_ASSET_USES
from otio_app.services.edit_plan_builder import build_edit_plan
from otio_app.services.asset_usage import usage_count_by_asset_id_from_shots


def _sample_project(layout: dict[str, Path]) -> Project:
    return Project(
        id="plan-test",
        name="Test",
        project_root=str(layout["project_root"]),
        work_dir=str(layout["work_dir"]),
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )


def test_build_edit_plan_without_gemini(
    temp_project_layout: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _sample_project(temp_project_layout)
    voice_path = str(temp_project_layout["voice_file"])
    media_path = str(temp_project_layout["project_root"] / "Grand Canyon" / "clip.mp4")

    mapping = VoiceFolderMappingDocument(
        project_id=project.id,
        confirmed=True,
        entries=[
            VoiceFolderMappingEntry(
                voice_file=voice_path,
                folder="Grand Canyon",
                confirmed=True,
            )
        ],
    )
    project.voice_folder_mapping_path.write_text(
        mapping.model_dump_json(indent=2),
        encoding="utf-8",
    )

    voice_doc = VoiceAnalysisDocument(
        project_id=project.id,
        language="de",
        files=[
            VoiceFileAnalysis(
                path=voice_path,
                segments=[
                    VoiceSegment(
                        start_sec=0.0,
                        end_sec=10.0,
                        text="Der Canyon ist riesig, und der Fluss ist tief.",
                    )
                ],
            )
        ],
    )
    project.voice_analysis_path.write_text(
        voice_doc.model_dump_json(indent=2),
        encoding="utf-8",
    )

    (temp_project_layout["project_root"] / "Grand Canyon" / "broll.mp4").write_bytes(b"mp4")

    inventory = InventoryDocument(
        project_id=project.id,
        items=[
            AssetFolderAnalysis(
                folder="Grand Canyon",
                assets=[
                    AssetMediaAnalysis(
                        path=media_path,
                        description="Steile Felswand und Fluss",
                    ),
                    AssetMediaAnalysis(
                        path=str(temp_project_layout["project_root"] / "Grand Canyon" / "broll.mp4"),
                        description="Ruhige Landschaft establishing overview",
                    ),
                ],
            )
        ],
    )
    from otio_app.services.inventory_loader import save_folder_inventory

    save_folder_inventory(
        project.folder_inventory_path("Grand Canyon"),
        inventory.items[0],
    )

    document = build_edit_plan(project, use_api=False)
    assert document.shots
    assert document.shots[0].folder == "Grand Canyon"
    assert all(shot.duration_sec >= 3.0 for shot in document.shots if not shot.section_outro)
    assert document.shots[-1].section_outro is True
    assert document.shots[-1].duration_sec == 5.0
    assert document.shots[-1].motif == "Ausklingen"


def test_max_asset_usage_applies_after_timing_split(
    temp_project_layout: dict[str, Path],
) -> None:
    project = _sample_project(temp_project_layout)
    voice_path = str(temp_project_layout["voice_file"])
    media_path = str(temp_project_layout["project_root"] / "Grand Canyon" / "clip.mp4")

    mapping = VoiceFolderMappingDocument(
        project_id=project.id,
        confirmed=True,
        entries=[
            VoiceFolderMappingEntry(
                voice_file=voice_path,
                folder="Grand Canyon",
                confirmed=True,
            )
        ],
    )
    project.voice_folder_mapping_path.write_text(mapping.model_dump_json(indent=2), encoding="utf-8")

    voice_doc = VoiceAnalysisDocument(
        project_id=project.id,
        language="de",
        files=[
            VoiceFileAnalysis(
                path=voice_path,
                segments=[
                    VoiceSegment(
                        start_sec=0.0,
                        end_sec=10.0,
                        text="Ein langer Satz über Canyonwände und Licht im engen Durchgang.",
                    )
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
                    description="Canyonwände und Licht im engen Durchgang",
                    asset_id="asset_clip",
                )
            ],
        ),
    )

    rules = EditPlanRulesDocument(
        project_id=project.id,
        rules=[
            EditPlanRule(
                id="max1",
                rule_type=RULE_MAX_ASSET_USES,
                enabled=True,
                params={"max_count": 1},
                label="Max 1",
            )
        ],
    )
    settings = EditPlanSettings(
        shot_min_sec=3.0,
        shot_max_sec=5.0,
        section_outro_sec=0.0,
    )

    document = build_edit_plan(project, settings=settings, use_api=False, rules_doc=rules)
    counts = usage_count_by_asset_id_from_shots(
        [shot for shot in document.shots if not shot.section_outro]
    )
    assert counts.get("asset_clip", 0) == 1
    assert any(shot.asset_path is None for shot in document.shots)
