"""Phase 4 (Asset-bewusste Cut-Plan-Vorbereitung): Modell-Defaults und
Rückwärtskompatibilität für SentenceItem/VisualAssetPlanHint."""

from __future__ import annotations

import json

from otio_app.services.voiceover_generation.models import SentenceItem, VisualAssetPlanHint


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

    # Roundtrip: serialisieren + wieder laden bleibt stabil.
    round_tripped = SentenceItem.model_validate(json.loads(item.model_dump_json()))
    assert round_tripped == item


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
