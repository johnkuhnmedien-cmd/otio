"""Tests für Clean-Media-Einstellungen."""

from __future__ import annotations

from pathlib import Path

from otio_app.analysis_models import EditPlanRule, EditPlanRulesDocument
from otio_app.models import Project
from otio_app.services.clean_media_settings import (
    RULE_AUTO_ZOOM_FILL_LEGACY,
    CleanMediaSettings,
    load_clean_media_settings,
    save_clean_media_settings,
)
from otio_app.services.edit_plan_rules import load_edit_plan_rules, save_edit_plan_rules


def _project(tmp_path: Path) -> Project:
    root = tmp_path / "USA"
    root.mkdir()
    return Project(
        id="clean-settings-test",
        name="USA",
        project_root=str(root),
        work_dir=str(root / "_otio"),
        asset_subdir_names=["Bisti"],
        selected_asset_subdirs=["Bisti"],
        width=3840,
        height=2160,
    )


def test_load_clean_media_settings_defaults(tmp_path: Path) -> None:
    project = _project(tmp_path)
    settings = load_clean_media_settings(project)
    assert settings.auto_zoom_fill is False


def test_save_and_load_clean_media_settings_roundtrip(tmp_path: Path) -> None:
    project = _project(tmp_path)
    save_clean_media_settings(project, CleanMediaSettings(auto_zoom_fill=True))
    loaded = load_clean_media_settings(project)
    assert loaded.auto_zoom_fill is True


def test_migrates_legacy_auto_zoom_rule_from_edit_plan_rules(tmp_path: Path) -> None:
    project = _project(tmp_path)
    save_edit_plan_rules(
        project,
        EditPlanRulesDocument(
            project_id=project.id,
            rules=[
                EditPlanRule(
                    id="zoom",
                    rule_type=RULE_AUTO_ZOOM_FILL_LEGACY,
                    enabled=True,
                )
            ],
        ),
    )

    settings = load_clean_media_settings(project)
    assert settings.auto_zoom_fill is True

    rules = load_edit_plan_rules(project)
    assert all(rule.rule_type != RULE_AUTO_ZOOM_FILL_LEGACY for rule in rules.rules)

    reloaded = load_clean_media_settings(project)
    assert reloaded.auto_zoom_fill is True
