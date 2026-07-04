"""Tests für Schnittplan-Regeln."""

from __future__ import annotations

from pathlib import Path

from otio_app.analysis_models import EditPlanRule, EditPlanRulesDocument, EditPlanShot
from otio_app.models import Project
from otio_app.services.edit_plan_rules import (
    RULE_CUSTOM_NOTE,
    RULE_MAX_ASSET_USES,
    RULE_NO_CONSECUTIVE_SAME_ASSET,
    apply_edit_plan_rules,
    available_rule_templates,
    default_rules,
    load_edit_plan_rules,
    save_edit_plan_rules,
    validate_shots_against_rules,
)


def _project(tmp_path: Path) -> Project:
    root = tmp_path / "USA"
    root.mkdir()
    return Project(
        id="rules-test",
        name="Test",
        project_root=str(root),
        work_dir=str(root / "_otio"),
        asset_subdir_names=["Folder"],
        selected_asset_subdirs=["Folder"],
    )


def _shot(index: int, asset: str | None) -> EditPlanShot:
    return EditPlanShot(
        voice_file="voice.wav",
        folder="Folder",
        voice_start_sec=float(index),
        voice_end_sec=float(index + 3),
        duration_sec=3.0,
        asset_path=asset,
        motif=f"motif {index}",
        passage_text=f"text {index}",
    )


def test_default_rules_include_max_and_consecutive(tmp_path: Path) -> None:
    project = _project(tmp_path)
    document = default_rules(project)
    types = {rule.rule_type for rule in document.rules}
    assert RULE_MAX_ASSET_USES in types
    assert RULE_NO_CONSECUTIVE_SAME_ASSET in types
    assert len(document.rules) == 2


def test_available_templates_allows_custom_notes_and_placeholders(tmp_path: Path) -> None:
    project = _project(tmp_path)
    document = default_rules(project)
    available = available_rule_templates(document)
    types = {template.rule_type for template in available}
    assert RULE_CUSTOM_NOTE in types
    assert len(available) >= 3


def test_save_and_load_rules(tmp_path: Path) -> None:
    project = _project(tmp_path)
    document = EditPlanRulesDocument(
        project_id=project.id,
        rules=[
            EditPlanRule(
                id="r1",
                rule_type=RULE_MAX_ASSET_USES,
                enabled=True,
                params={"max_count": 3},
            )
        ],
    )
    save_edit_plan_rules(project, document)
    loaded = load_edit_plan_rules(project)
    assert loaded.rules[0].params["max_count"] == 3


def test_apply_rules_avoids_consecutive_and_max_uses(tmp_path: Path) -> None:
    project = _project(tmp_path)
    rules = EditPlanRulesDocument(
        project_id=project.id,
        rules=[
            EditPlanRule(
                id="r1",
                rule_type=RULE_MAX_ASSET_USES,
                enabled=True,
                params={"max_count": 2},
            ),
            EditPlanRule(
                id="r2",
                rule_type=RULE_NO_CONSECUTIVE_SAME_ASSET,
                enabled=True,
            ),
        ],
    )
    assets = {
        "Folder": [
            "/media/a.mp4",
            "/media/b.mp4",
            "/media/c.mp4",
        ]
    }
    shots = [
        _shot(1, "/media/a.mp4"),
        _shot(2, "/media/a.mp4"),
        _shot(3, "/media/a.mp4"),
        _shot(4, "/media/a.mp4"),
    ]
    adjusted = apply_edit_plan_rules(shots, rules, assets)
    paths = [shot.asset_path for shot in adjusted]
    assert paths[0] == "/media/a.mp4"
    assert paths[1] != "/media/a.mp4"
    assert paths.count("/media/a.mp4") <= 2
    assert validate_shots_against_rules(adjusted, rules) == []
