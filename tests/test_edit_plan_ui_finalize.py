"""Regressionstest für _finalize_plan_for_confirm (Bestätigen & speichern)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from otio_app.analysis_models import (
    EditPlanDocument,
    EditPlanRulesDocument,
    EditPlanSettings,
    TimelineItem,
    TimelineItemTransform,
)
from otio_app.models import Project
from otio_app.services.edit_plan_validator import TimelineValidationResult, ValidationStatus
from otio_app.ui.edit_plan import _finalize_plan_for_confirm


def _project(tmp_path: Path) -> Project:
    root = tmp_path / "USA"
    root.mkdir()
    return Project(
        id="finalize-test",
        name="Test",
        project_root=str(root),
        work_dir=str(root / "_otio"),
        asset_subdir_names=["Antelope Canyon"],
        selected_asset_subdirs=["Antelope Canyon"],
    )


def test_finalize_plan_for_confirm_does_not_raise_nameerror(tmp_path: Path) -> None:
    """Regression: _finalize_plan_for_confirm referenzierte fälschlich eine
    nicht existierende Variable 'plan' statt 'document' beim Stale-Hash-Check."""
    project = _project(tmp_path)
    media_path = tmp_path / "clip.mp4"
    media_path.write_bytes(b"video")

    draft = EditPlanDocument(
        project_id=project.id,
        folder_name="Antelope Canyon",
        confirmed=False,
        settings=EditPlanSettings(),
        timeline_items=[
            TimelineItem(
                timeline_item_id="item_001",
                type="video_shot",
                section_id="section_antelope_canyon",
                folder_name="Antelope Canyon",
                voice_file=str(tmp_path / "voice.wav"),
                resolved_media_path=str(media_path),
                duration_sec=5.0,
                final_duration_sec=5.0,
                timeline_in_sec=0.0,
                timeline_out_sec=5.0,
                source_in_sec=0.0,
                source_out_sec=5.0,
                transform=TimelineItemTransform(),
            )
        ],
        inventory_hash_at_plan_time="abc123",
    )

    with patch(
        "otio_app.ui.edit_plan.get_edit_plan_rules_for_project",
        return_value=EditPlanRulesDocument(project_id=project.id, rules=[]),
    ), patch(
        "otio_app.ui.edit_plan.ensure_opening_titles_rendered",
        side_effect=lambda _project, items: (items, []),
    ), patch(
        "otio_app.ui.edit_plan.inventory_hash_is_stale",
        return_value=False,
    ), patch(
        "otio_app.ui.edit_plan.validate_timeline_items",
        return_value=TimelineValidationResult(status=ValidationStatus.OK),
    ):
        document, notes = _finalize_plan_for_confirm(project, draft, "Antelope Canyon")

    assert document.confirmed is True
    assert isinstance(notes, list)


def test_finalize_plan_for_confirm_blocks_when_inventory_stale(tmp_path: Path) -> None:
    project = _project(tmp_path)
    media_path = tmp_path / "clip.mp4"
    media_path.write_bytes(b"video")

    draft = EditPlanDocument(
        project_id=project.id,
        folder_name="Antelope Canyon",
        confirmed=False,
        settings=EditPlanSettings(),
        timeline_items=[
            TimelineItem(
                timeline_item_id="item_001",
                type="video_shot",
                section_id="section_antelope_canyon",
                folder_name="Antelope Canyon",
                voice_file=str(tmp_path / "voice.wav"),
                resolved_media_path=str(media_path),
                duration_sec=5.0,
                final_duration_sec=5.0,
                timeline_in_sec=0.0,
                timeline_out_sec=5.0,
                source_in_sec=0.0,
                source_out_sec=5.0,
                transform=TimelineItemTransform(),
            )
        ],
        inventory_hash_at_plan_time="abc123",
    )

    with patch(
        "otio_app.ui.edit_plan.get_edit_plan_rules_for_project",
        return_value=EditPlanRulesDocument(project_id=project.id, rules=[]),
    ), patch(
        "otio_app.ui.edit_plan.ensure_opening_titles_rendered",
        side_effect=lambda _project, items: (items, []),
    ), patch(
        "otio_app.ui.edit_plan.inventory_hash_is_stale",
        return_value=True,
    ):
        try:
            _finalize_plan_for_confirm(project, draft, "Antelope Canyon")
            assert False, "Erwartete ValueError bei stale inventory_hash"
        except ValueError as exc:
            assert "Inventory" in str(exc)


def test_finalize_plan_for_confirm_fills_missing_asset_instead_of_blocking(
    tmp_path: Path,
) -> None:
    """Regression: Fehlt einem Shot ein Asset (kein Supplement gefunden), darf
    das manuelle Bestätigen nicht blockiert werden — stattdessen wird
    automatisch das nächstbeste verfügbare Asset aus dem Ordner zugewiesen."""
    from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis
    from otio_app.services.inventory_loader import save_folder_inventory

    project = _project(tmp_path)
    fallback_media = tmp_path / "Antelope Canyon" / "fallback.mp4"
    fallback_media.parent.mkdir(parents=True, exist_ok=True)
    fallback_media.write_bytes(b"video")

    save_folder_inventory(
        project.folder_inventory_path("Antelope Canyon"),
        AssetFolderAnalysis(
            folder="Antelope Canyon",
            assets=[
                AssetMediaAnalysis(
                    path=str(fallback_media),
                    description="Schmaler Slot Canyon",
                    asset_id="asset_fallback",
                ),
            ],
        ),
    )

    draft = EditPlanDocument(
        project_id=project.id,
        folder_name="Antelope Canyon",
        confirmed=False,
        settings=EditPlanSettings(
            video_head_trim_sec=0.0,
            video_head_trim_policy="disabled",
            section_outro_sec=0.0,
        ),
        timeline_items=[
            TimelineItem(
                timeline_item_id="item_missing",
                type="video_shot",
                section_id="section_antelope_canyon",
                folder_name="Antelope Canyon",
                voice_file=str(tmp_path / "voice.wav"),
                resolved_media_path="",
                duration_sec=5.0,
                final_duration_sec=5.0,
                timeline_in_sec=0.0,
                timeline_out_sec=5.0,
                source_in_sec=0.0,
                source_out_sec=5.0,
                passage_text="Ein schmaler Slot Canyon",
                transform=TimelineItemTransform(),
            )
        ],
        inventory_hash_at_plan_time="abc123",
    )

    with patch(
        "otio_app.ui.edit_plan.get_edit_plan_rules_for_project",
        return_value=EditPlanRulesDocument(project_id=project.id, rules=[]),
    ), patch(
        "otio_app.ui.edit_plan.ensure_opening_titles_rendered",
        side_effect=lambda _project, items: (items, []),
    ), patch(
        "otio_app.ui.edit_plan.inventory_hash_is_stale",
        return_value=False,
    ):
        document, notes = _finalize_plan_for_confirm(project, draft, "Antelope Canyon")

    assert document.confirmed is True
    assert document.timeline_items[0].resolved_media_path == str(fallback_media)
    assert any("nächstbestes Asset" in note for note in notes)
