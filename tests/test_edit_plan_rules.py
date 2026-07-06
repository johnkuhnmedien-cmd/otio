"""Tests für Schnittplan-Regeln."""

from __future__ import annotations

from pathlib import Path

from otio_app.analysis_models import EditPlanRule, EditPlanRulesDocument, EditPlanShot
from otio_app.models import Project
from otio_app.services.edit_plan_rules import (
    RULE_AUTO_ZOOM_FILL,
    RULE_CUSTOM,
    RULE_FOLDER_TITLE,
    RULE_MAX_ASSET_USES,
    RULE_NO_CONSECUTIVE_SAME_ASSET,
    RULE_TRIM_LEADING,
    apply_edit_plan_rules,
    available_rule_templates,
    create_custom_rule,
    default_rules,
    export_rule_options,
    load_edit_plan_rules,
    normalize_rules_document,
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
    assert RULE_TRIM_LEADING in types
    assert RULE_AUTO_ZOOM_FILL in types
    assert RULE_FOLDER_TITLE in types
    assert len(document.rules) == 5
    folder_title = next(rule for rule in document.rules if rule.rule_type == RULE_FOLDER_TITLE)
    assert folder_title.enabled is False


def test_export_rule_options_reads_trim_and_zoom(tmp_path: Path) -> None:
    project = _project(tmp_path)
    document = EditPlanRulesDocument(
        project_id=project.id,
        rules=[
            EditPlanRule(
                id="trim",
                rule_type=RULE_TRIM_LEADING,
                enabled=True,
                params={"trim_sec": 0.5},
            ),
            EditPlanRule(
                id="zoom",
                rule_type=RULE_AUTO_ZOOM_FILL,
                enabled=True,
            ),
        ],
    )
    opts = export_rule_options(document)
    assert opts.trim_leading_sec == 0.5
    assert opts.auto_zoom_fill is True


def test_export_rule_options_reads_folder_title(tmp_path: Path) -> None:
    project = _project(tmp_path)
    document = EditPlanRulesDocument(
        project_id=project.id,
        rules=[
            EditPlanRule(
                id="title",
                rule_type=RULE_FOLDER_TITLE,
                enabled=True,
                params={"font_name": "Phosphate", "duration_sec": 5.0, "font_size": 48.0},
            ),
        ],
    )
    opts = export_rule_options(document)
    assert opts.folder_title_enabled is True
    assert opts.folder_title_font == "Phosphate"
    assert opts.folder_title_duration_sec == 5.0
    assert opts.folder_title_font_size == 48.0


def test_export_rule_options_auto_font_size_when_zero(tmp_path: Path) -> None:
    project = _project(tmp_path)
    document = EditPlanRulesDocument(
        project_id=project.id,
        rules=[
            EditPlanRule(
                id="title",
                rule_type=RULE_FOLDER_TITLE,
                enabled=True,
                params={"font_name": "Helvetica Neue", "duration_sec": 5.0, "font_size": 0.0},
            ),
        ],
    )
    opts = export_rule_options(document)
    assert opts.folder_title_font_size is None


def test_available_templates_excludes_only_used_system_rules(tmp_path: Path) -> None:
    project = _project(tmp_path)
    document = default_rules(project)
    available = available_rule_templates(document)
    types = {template.rule_type for template in available}
    assert RULE_MAX_ASSET_USES not in types
    assert len(available) >= 2


def test_create_custom_rule_migrates_to_gemini_prompt(tmp_path: Path) -> None:
    rule = create_custom_rule("Keine Intro-Wiederholung", "Intro-Clips nicht doppelt nutzen.")
    assert rule.rule_type == RULE_CUSTOM
    document = normalize_rules_document(
        EditPlanRulesDocument(project_id="p1", rules=[rule])
    )
    assert "Intro-Clips nicht doppelt nutzen." in document.gemini_prompt


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
