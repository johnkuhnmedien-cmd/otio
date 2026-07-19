"""Tests für strukturiertes OTIO-Export-Merge-Debugging."""

from __future__ import annotations

from otio_app.analysis_models import EditPlanSettings
from otio_app.services.otio_export_debug import (
    build_otio_export_merge_debug_report,
    categorize_merge_warning,
)
from otio_app.services.otio_exporter import MergedEditPlanResult


def test_categorize_merge_warning_shot_max() -> None:
    folder, category, body = categorize_merge_warning(
        "Validierung: Antelope Canyon: edit_cut_001_seg_01: final_duration_sec "
        "(10.0s) ist länger als erlaubt (8.0s)."
    )
    assert folder == "Antelope Canyon"
    assert category == "shot_max_duration"
    assert "final_duration_sec" in body


def test_categorize_merge_warning_duration_source() -> None:
    folder, category, _body = categorize_merge_warning(
        "Validierung: Antelope Canyon: voiceover.duration_source muss ffprobe sein "
        "(ist 'bridge_audio_plan')."
    )
    assert folder == "Antelope Canyon"
    assert category == "voiceover_duration_source"


def test_build_debug_report_groups_and_notes_relaxed_mode() -> None:
    merged = MergedEditPlanResult(
        timeline_items=[],
        shots=[],
        settings=EditPlanSettings(),
        warnings=[
            "Validierung: Antelope Canyon: section_outro_sec (5.0s) nicht vollständig "
            "als Outro-Elemente geplant (0.0s).",
            "Validierung: Antelope Canyon: Ordner-Titel-Regel aktiv, aber kein opening_title.",
        ],
        validation_status="BLOCKED",
        cut_plan_relaxed_folders=["Antelope Canyon"],
        included_folders=["Antelope Canyon"],
    )
    report = build_otio_export_merge_debug_report(merged)
    assert report.issue_count == 2
    assert "section_outro" in report.issues_by_category
    assert "opening_title" in report.issues_by_category
    assert report.cut_plan_relaxed_folders == ["Antelope Canyon"]
    assert any("Cut-Plan-Validierungsmodus aktiv" in note for note in report.analysis_notes)
