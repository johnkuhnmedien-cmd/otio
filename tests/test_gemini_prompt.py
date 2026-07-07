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
    _format_segment_lines,
    build_plan_folder_correction_instructions,
    build_plan_folder_prompt,
    build_plan_passage_prompt,
    normalize_match_quality,
)
from otio_app.services.edit_plan_validator import PlanValidationError


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
    assert "5.0" in prompt
    assert "## Harte Regeln: Shot-Timing" in prompt
    assert "shot_min_sec" in prompt
    assert "3.0" in prompt
    assert "8.0" in prompt


def test_build_plan_folder_prompt_includes_shot_timing_rules() -> None:
    prompt = build_plan_folder_prompt(
        folder_name="Folder",
        segment_lines='- beat_id="beat_001" start_sec=0.0 end_sec=10.0 text="Text."',
        asset_lines="- (keine)",
        language="de",
        shot_min_sec=4.0,
        shot_max_sec=10.0,
    )
    assert "## Harte Regeln: Shot-Timing" in prompt
    assert "shot_min_sec" in prompt
    assert "4.0" in prompt
    assert "shot_max_sec" in prompt
    assert "10.0" in prompt
    assert "parts_min" in prompt
    assert "parts_max" in prompt


def test_build_plan_folder_prompt_omits_outro_when_zero() -> None:
    prompt = build_plan_folder_prompt(
        folder_name="Folder",
        segment_lines="- (keine)",
        asset_lines="- (keine)",
        language="de",
        section_outro_sec=0.0,
    )
    assert "## Zusatz: Ordner-Ausklingen" not in prompt


def test_build_plan_folder_prompt_includes_segments_and_holistic_instruction() -> None:
    prompt = build_plan_folder_prompt(
        folder_name="Arches National Park",
        segment_lines=(
            '- beat_id="beat_001" segment_duration_sec=12.0 allowed_parts_min=2 '
            'allowed_parts_max=4 short_segment_allowed=false text="Wir besuchen den Park."'
        ),
        asset_lines='- path="/a.mp4" description="Felsbogen"',
        language="de",
        extra_instructions="Assets laufen bis zum nächsten Satz.",
    )
    assert "beat_001" in prompt
    assert "allowed_parts_min" in prompt
    assert "allowed_parts_max" in prompt
    assert "gesamtheitlich" in prompt.lower()
    assert "match_quality" in prompt
    assert "Assets laufen bis zum nächsten Satz." in prompt


def test_build_plan_folder_prompt_includes_asset_usage_rules() -> None:
    prompt = build_plan_folder_prompt(
        folder_name="Folder",
        segment_lines="- (keine)",
        asset_lines="- (keine)",
        language="de",
        max_asset_usage=1,
        min_asset_reuse_distance_shots=2,
    )
    assert "## Harte Regeln: Asset-Nutzung" in prompt
    assert "max_asset_usage" in prompt
    assert "max_asset_usage = 1" in prompt
    assert "min_reuse_distance_shots" in prompt
    assert "asset_reuse_policy" in prompt
    assert "hard_block" in prompt


def test_format_segment_lines_includes_allowed_parts_bounds() -> None:
    lines = _format_segment_lines(
        [
            {
                "beat_id": "beat_001",
                "text": "Langer Abschnitt.",
                "start_sec": 0.0,
                "end_sec": 18.0,
            },
            {
                "beat_id": "beat_002",
                "text": "Kurz.",
                "start_sec": 18.0,
                "end_sec": 20.4,
            },
        ],
        shot_min_sec=3.0,
        shot_max_sec=8.0,
    )
    assert "| beat_001 |" in lines
    assert "| 3 |" in lines
    assert "| 6 |" in lines
    assert "| nein |" in lines
    assert "| beat_002 |" in lines
    assert "| 1 |" in lines
    assert "| ja |" in lines
    assert "parts_min" in lines
    assert "parts_max" in lines


def test_build_plan_folder_correction_includes_structured_asset_and_shot_errors() -> None:
    structured = [
        PlanValidationError(
            type="ASSET_USAGE_LIMIT_EXCEEDED",
            asset_id="Antelope_Canyon_Asset02.mp4",
            usage_count=2,
            max_allowed=1,
            timeline_item_ids=["shot_003", "shot_007"],
        ),
        PlanValidationError(
            type="SHOT_TOO_SHORT",
            timeline_item_id="shot_005",
            duration_sec=2.1,
            min_sec=3.0,
            segment_id="beat_002",
            reason="Shot shorter than minimum duration",
        ),
        PlanValidationError(
            type="SHOT_TOO_LONG",
            timeline_item_id="shot_008",
            duration_sec=10.2,
            max_sec=8.0,
            segment_id="beat_004",
            reason="Shot longer than maximum duration",
        ),
    ]
    prompt = build_plan_folder_correction_instructions(
        errors=[],
        structured_errors=structured,
        previous_beats={"beat_002": [{"text": "a", "motif": "m", "match_quality": "gut"}]},
        attempt=2,
        max_attempts=3,
        file_duration_sec=94.88,
        shot_min_sec=3.0,
        shot_max_sec=8.0,
    )
    assert "ASSET_USAGE_LIMIT_EXCEEDED" in prompt
    assert "Antelope_Canyon_Asset02.mp4" in prompt
    assert "shot_003" in prompt
    assert "SHOT_TOO_SHORT" in prompt
    assert "SHOT_TOO_LONG" in prompt
    assert "supplement_request" in prompt.lower()


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
