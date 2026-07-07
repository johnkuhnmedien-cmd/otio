"""Tests für Gemini-Zusatzhinweise beim Schnittplan."""

from __future__ import annotations

from pathlib import Path

from otio_app.analysis_models import EditPlanRule, EditPlanRulesDocument
from otio_app.models import Project
from otio_app.services.edit_plan_rules import (
    RULE_CUSTOM,
    create_custom_rule,
    gemini_prompt_text,
    load_edit_plan_rules,
    normalize_rules_document,
    save_edit_plan_rules,
)
from otio_app.services.gemini_client import (
    build_plan_folder_prompt,
    build_plan_passage_prompt,
    normalize_match_quality,
)


def test_build_plan_folder_prompt_includes_outro_when_configured() -> None:
    prompt = build_plan_folder_prompt(
        folder_name="Caddo Lake",
        segment_lines='- beat_id="beat_001" start_sec=0.0 end_sec=5.0 text="Text."',
        asset_lines='- path="/a.mp4" description="See"',
        language="de",
        section_outro_sec=5.0,
        shot_min_sec=3.0,
        shot_max_sec=8.0,
    )
    assert "outro_001" in prompt
    assert "Ausklingen" in prompt
    assert "5.0" in prompt or "duration_sec=5" in prompt
    assert "mindestens 3.0s" in prompt
    assert "höchstens 8.0s" in prompt


def test_build_plan_folder_prompt_includes_shot_timing_rules() -> None:
    prompt = build_plan_folder_prompt(
        folder_name="Folder",
        segment_lines='- beat_id="beat_001" start_sec=0.0 end_sec=10.0 text="Text."',
        asset_lines="- (keine)",
        language="de",
        shot_min_sec=4.0,
        shot_max_sec=10.0,
    )
    assert "Timing-Regeln für Shots" in prompt
    assert "mindestens 4.0s" in prompt
    assert "höchstens 10.0s" in prompt
    assert "weniger, längere parts" in prompt


def test_build_plan_folder_prompt_omits_outro_when_zero() -> None:
    prompt = build_plan_folder_prompt(
        folder_name="Folder",
        segment_lines="- (keine)",
        asset_lines="- (keine)",
        language="de",
        section_outro_sec=0.0,
    )
    assert "Ordner-Ausklingen nach" not in prompt


def test_build_plan_folder_prompt_includes_segments_and_holistic_instruction() -> None:
    prompt = build_plan_folder_prompt(
        folder_name="Arches National Park",
        segment_lines='- beat_id="beat_001" start_sec=0.0 end_sec=12.0 max_parts=2 text="Wir besuchen den Park."',
        asset_lines='- path="/a.mp4" description="Felsbogen"',
        language="de",
        extra_instructions="Assets laufen bis zum nächsten Satz.",
    )
    assert "beat_001" in prompt
    assert "max_parts" in prompt
    assert "gesamtheitlich" in prompt.lower()
    assert "match_quality" in prompt
    assert "Assets laufen bis zum nächsten Satz." in prompt


def test_normalize_match_quality_aliases() -> None:
    assert normalize_match_quality("Sehr gut") == "sehr_gut"
    assert normalize_match_quality("UNPASSEND") == "unpassend"
    assert normalize_match_quality("good") == "gut"


def test_build_plan_passage_prompt_includes_extra_instructions() -> None:
    prompt = build_plan_passage_prompt(
        passage_text="Wir besuchen den Arches National Park.",
        folder_name="Arches National Park",
        asset_lines='- path="/a.mp4" description="Felsbogen"',
        language="de",
        extra_instructions="Assets laufen bis zum nächsten Satz.",
    )
    assert "Arches National Park" in prompt
    assert "Zusätzliche Anweisungen des Editors" in prompt
    assert "Assets laufen bis zum nächsten Satz." in prompt


def test_build_plan_passage_prompt_omits_empty_instructions() -> None:
    prompt = build_plan_passage_prompt(
        passage_text="Text",
        folder_name="Folder",
        asset_lines="- (keine)",
        language="de",
        extra_instructions="   ",
    )
    assert "Zusätzliche Anweisungen" not in prompt


def test_normalize_rules_document_migrates_custom_rules() -> None:
    document = EditPlanRulesDocument(
        project_id="p1",
        rules=[
            create_custom_rule(
                "Satz-Länge",
                "Assets bis zum Beginn des nächsten Satzes laufen lassen.",
            )
        ],
        gemini_prompt="",
    )
    normalized = normalize_rules_document(document)
    assert len(normalized.rules) == 0
    assert "nächsten Satzes" in normalized.gemini_prompt


def test_save_and_load_persists_gemini_prompt(tmp_path: Path) -> None:
    root = tmp_path / "USA"
    root.mkdir()
    project = Project(
        id="prompt-test",
        name="Test",
        project_root=str(root),
        work_dir=str(root / "_otio"),
        asset_subdir_names=["Folder"],
        selected_asset_subdirs=["Folder"],
    )
    document = EditPlanRulesDocument(
        project_id=project.id,
        gemini_prompt="Bevorzuge Weitwinkel.",
        rules=[
            EditPlanRule(
                id="r1",
                rule_type=RULE_CUSTOM,
                enabled=True,
                params={"title": "Alt", "text": "Sollte nicht geladen werden"},
            )
        ],
    )
    save_edit_plan_rules(project, document)
    loaded = load_edit_plan_rules(project)
    assert "Weitwinkel" in loaded.gemini_prompt
    assert gemini_prompt_text(loaded) == loaded.gemini_prompt.strip()
    assert all(rule.rule_type != RULE_CUSTOM for rule in loaded.rules)
