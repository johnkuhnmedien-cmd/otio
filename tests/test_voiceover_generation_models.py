"""Phase 4 (Asset-bewusste Cut-Plan-Vorbereitung): Modell-Defaults und
Rückwärtskompatibilität für SentenceItem/VisualAssetPlanHint."""

from __future__ import annotations

import json

from otio_app.defaults import SEGMENT_ASSET_PLANNING_MODE_PER_SENTENCE
from otio_app.services.voiceover_generation.models import (
    FolderVoiceoverSetting,
    SentenceItem,
    SentenceSegmentAssetPlan,
    VisualAssetPlanHint,
)


def test_visual_asset_plan_hint_defaults_are_neutral() -> None:
    plan = VisualAssetPlanHint()
    assert plan.preferred_cut_count == 1
    assert plan.reuse_risk == ""
    assert plan.needs_visual_variety is False
    assert plan.asset_strategy_reason == ""
    assert plan.supplement_search_hint == ""


def test_sentence_item_new_fields_default_to_empty() -> None:
    item = SentenceItem(sentence_id="s1")
    assert item.second_backup_asset_ids == []
    assert item.visual_asset_plan == VisualAssetPlanHint()
    assert item.planned_segments == []


def test_folder_voiceover_setting_defaults_to_per_sentence_segment_planning_mode() -> None:
    """Phase 7.1: der Default darf das bestehende Verhalten NICHT ändern —
    bestehende Projekte/Settings ohne dieses Feld erhalten automatisch
    PER_SENTENCE (heutiges Verhalten)."""
    setting = FolderVoiceoverSetting(folder_name="Grand Canyon")
    assert setting.segment_asset_planning_mode == SEGMENT_ASSET_PLANNING_MODE_PER_SENTENCE


def test_folder_voiceover_setting_parses_legacy_json_without_segment_planning_mode() -> None:
    legacy_payload = {"folder_name": "Grand Canyon"}
    setting = FolderVoiceoverSetting.model_validate(legacy_payload)
    assert setting.segment_asset_planning_mode == SEGMENT_ASSET_PLANNING_MODE_PER_SENTENCE


def test_sentence_segment_asset_plan_defaults() -> None:
    segment = SentenceSegmentAssetPlan()
    assert segment.segment_order == 1
    assert segment.primary_asset_id == ""
    assert segment.backup_asset_ids == []


def test_sentence_item_parses_legacy_json_without_phase4_fields() -> None:
    """Ein VOR Phase 4 gespeicherter sentence_item-JSON-Eintrag (ohne
    second_backup_asset_ids/visual_asset_plan) muss weiterhin gültig sein —
    additive Felder dürfen bestehende Drafts nicht ungültig machen."""
    legacy_payload = {
        "sentence_id": "sentence_001",
        "text": "Ein alter Satz ohne die neuen Felder.",
        "primary_asset_id": "asset_a",
        "backup_asset_ids": ["asset_b"],
        "needs_supplement_asset": False,
    }
    item = SentenceItem.model_validate(legacy_payload)
    assert item.second_backup_asset_ids == []
    assert item.visual_asset_plan.preferred_cut_count == 1
    assert item.planned_segments == []

    # Roundtrip: serialisieren + wieder laden bleibt stabil.
    round_tripped = SentenceItem.model_validate(json.loads(item.model_dump_json()))
    assert round_tripped == item


def test_sentence_item_accepts_planned_segments_payload() -> None:
    payload = {
        "sentence_id": "sentence_001",
        "text": "Ein langer Satz, der in zwei Shots geteilt wird.",
        "primary_asset_id": "asset_a",
        "visual_asset_plan": {"preferred_cut_count": 2},
        "planned_segments": [
            {"segment_order": 1, "primary_asset_id": "asset_a", "backup_asset_ids": ["asset_b"]},
            {"segment_order": 2, "primary_asset_id": "asset_c", "backup_asset_ids": []},
        ],
    }
    item = SentenceItem.model_validate(payload)
    assert len(item.planned_segments) == 2
    assert item.planned_segments[0].segment_order == 1
    assert item.planned_segments[0].primary_asset_id == "asset_a"
    assert item.planned_segments[0].backup_asset_ids == ["asset_b"]
    assert item.planned_segments[1].primary_asset_id == "asset_c"


def test_sentence_item_accepts_full_phase4_payload() -> None:
    payload = {
        "sentence_id": "sentence_001",
        "text": "Ein Satz mit vollständiger Asset-Planung.",
        "primary_asset_id": "asset_a",
        "backup_asset_ids": ["asset_b"],
        "second_backup_asset_ids": ["asset_c"],
        "visual_asset_plan": {
            "preferred_cut_count": 2,
            "reuse_risk": "medium",
            "needs_visual_variety": True,
            "asset_strategy_reason": "Zwei unterschiedliche Assets für einen langen Satz.",
            "supplement_search_hint": "Grand Canyon sunrise wide",
        },
    }
    item = SentenceItem.model_validate(payload)
    assert item.second_backup_asset_ids == ["asset_c"]
    assert item.visual_asset_plan.preferred_cut_count == 2
    assert item.visual_asset_plan.reuse_risk == "medium"
    assert item.visual_asset_plan.needs_visual_variety is True
    assert item.visual_asset_plan.supplement_search_hint == "Grand Canyon sunrise wide"
