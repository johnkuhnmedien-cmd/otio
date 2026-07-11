"""Tests für Regeln-UI-Hilfsfunktionen."""

from __future__ import annotations

from otio_app.analysis_models import EditPlanRule, EditPlanRulesDocument
from otio_app.models import Project
from otio_app.services.edit_plan_rules import RULE_FOLDER_TITLE
from otio_app.ui.edit_plan_rules_ui import merge_rule_widgets_from_session


def _project() -> Project:
    return Project(
        id="ui-rules-test",
        name="Test",
        project_root="/tmp/test",
        work_dir="/tmp/test/_otio",
        asset_subdir_names=["Folder"],
        selected_asset_subdirs=["Folder"],
    )


def test_merge_rule_widgets_reads_font_size_without_rules_tab() -> None:
    project = _project()
    rule = EditPlanRule(
        id="title-rule",
        rule_type=RULE_FOLDER_TITLE,
        enabled=True,
        params={"font_name": "Phosphate", "duration_sec": 5.0, "font_size": 0.0},
    )
    document = EditPlanRulesDocument(project_id=project.id, rules=[rule])
    session = {
        f"rule_enabled_{project.id}_{rule.id}": True,
        f"rule_folder_title_font_{project.id}_{rule.id}": "Helvetica Neue",
        f"rule_folder_title_font_size_{project.id}_{rule.id}": 36.0,
        f"rule_folder_title_duration_{project.id}_{rule.id}": 4.0,
    }

    merged = merge_rule_widgets_from_session(project, document, session=session)
    title_rule = merged.rules[0]
    assert title_rule.params["font_name"] == "Helvetica Neue"
    assert title_rule.params["font_size"] == 36.0
    assert title_rule.params["duration_sec"] == 4.0
