"""Tests: Manuelles Bestätigen trotz fehlender Assets (nächstbestes Asset)."""

from __future__ import annotations

from otio_app.analysis_models import (
    EditPlanRule,
    EditPlanRulesDocument,
    TimelineItem,
    TimelineItemTransform,
)
from otio_app.services.edit_plan_gap_fill import fill_missing_timeline_assets
from otio_app.services.edit_plan_rules import RULE_MAX_ASSET_USES


def _item(item_id: str, *, folder: str, passage: str, resolved_path: str = "") -> TimelineItem:
    return TimelineItem(
        timeline_item_id=item_id,
        type="video_shot",
        section_id="section_test",
        folder_name=folder,
        voice_file="/tmp/voice.wav",
        resolved_media_path=resolved_path,
        duration_sec=5.0,
        final_duration_sec=5.0,
        timeline_in_sec=0.0,
        timeline_out_sec=5.0,
        source_in_sec=0.0,
        source_out_sec=5.0,
        passage_text=passage,
        transform=TimelineItemTransform(),
    )


def _rules(max_count: int = 10) -> EditPlanRulesDocument:
    return EditPlanRulesDocument(
        project_id="test",
        rules=[
            EditPlanRule(
                id="r1",
                rule_type=RULE_MAX_ASSET_USES,
                enabled=True,
                params={"max_count": max_count},
            )
        ],
    )


def test_fill_missing_timeline_assets_picks_best_content_match() -> None:
    item = _item("item_001", folder="Folder", passage="Ein schmaler Slot Canyon mit Licht")
    folder_assets = {
        "Folder": [
            {"path": "/media/irrelevant.mp4", "description": "Parkplatz Souvenirshop", "asset_id": "a_irrelevant"},
            {"path": "/media/matching.mp4", "description": "Schmaler Slot Canyon mit Licht", "asset_id": "a_matching"},
        ]
    }
    filled, notes = fill_missing_timeline_assets(
        [item], folder_assets=folder_assets, rules_doc=_rules()
    )
    assert filled[0].resolved_media_path == "/media/matching.mp4"
    assert filled[0].asset_id == "a_matching"
    assert notes and "nächstbestes Asset" in notes[0]
    assert filled[0].warnings


def test_fill_missing_timeline_assets_leaves_filled_items_untouched() -> None:
    item = _item("item_001", folder="Folder", passage="Text", resolved_path="/media/existing.mp4")
    folder_assets = {"Folder": [{"path": "/media/other.mp4", "description": "", "asset_id": "a_other"}]}
    filled, notes = fill_missing_timeline_assets(
        [item], folder_assets=folder_assets, rules_doc=_rules()
    )
    assert filled[0].resolved_media_path == "/media/existing.mp4"
    assert not notes


def test_fill_missing_timeline_assets_no_candidates_leaves_item_missing() -> None:
    item = _item("item_001", folder="EmptyFolder", passage="Text")
    filled, notes = fill_missing_timeline_assets(
        [item], folder_assets={}, rules_doc=_rules()
    )
    assert filled[0].resolved_media_path == ""
    assert not notes


def test_fill_missing_timeline_assets_respects_max_asset_usage_when_possible() -> None:
    already_used = _item("item_001", folder="Folder", passage="A", resolved_path="/media/a.mp4")
    already_used = already_used.model_copy(update={"asset_id": "a_id"})
    missing = _item("item_002", folder="Folder", passage="B")
    folder_assets = {
        "Folder": [
            {"path": "/media/a.mp4", "description": "A", "asset_id": "a_id"},
            {"path": "/media/b.mp4", "description": "B", "asset_id": "b_id"},
        ]
    }
    filled, notes = fill_missing_timeline_assets(
        [already_used, missing], folder_assets=folder_assets, rules_doc=_rules(max_count=1)
    )
    # a_id ist bereits 1x verwendet (max_count=1) -> darf für den Fallback nicht erneut gewählt werden,
    # obwohl es inhaltlich evtl. besser passen würde. b_id muss gewählt werden.
    assert filled[1].resolved_media_path == "/media/b.mp4"
    assert "überschreitet" not in notes[0]


def test_fill_missing_timeline_assets_uses_last_resort_when_all_options_exhausted() -> None:
    missing_1 = _item("item_001", folder="Folder", passage="A")
    missing_2 = _item("item_002", folder="Folder", passage="B")
    folder_assets = {"Folder": [{"path": "/media/only.mp4", "description": "Only", "asset_id": "only_id"}]}
    filled, notes = fill_missing_timeline_assets(
        [missing_1, missing_2], folder_assets=folder_assets, rules_doc=_rules(max_count=1)
    )
    assert filled[0].resolved_media_path == "/media/only.mp4"
    assert filled[1].resolved_media_path == "/media/only.mp4"
    assert any("überschreitet max_asset_usage" in note for note in notes)
