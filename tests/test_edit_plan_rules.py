"""Tests für Schnittplan-Regeln."""

from __future__ import annotations

from pathlib import Path

from otio_app.analysis_models import EditPlanRule, EditPlanRulesDocument, EditPlanShot
from otio_app.models import Project
from otio_app.services.edit_plan_rules import (
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
    assert RULE_FOLDER_TITLE in types
    assert len(document.rules) == 4
    folder_title = next(rule for rule in document.rules if rule.rule_type == RULE_FOLDER_TITLE)
    assert folder_title.enabled is False


def test_export_rule_options_reads_trim(tmp_path: Path) -> None:
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
        ],
    )
    opts = export_rule_options(document)
    assert opts.trim_leading_sec == 0.5


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


def test_apply_rules_refreshes_metadata_when_reassigning_asset(tmp_path: Path) -> None:
    """Regression: Wenn apply_edit_plan_rules einem Shot wegen eines
    Regelverstoßes (max_asset_usage) ein ANDERES Asset zuweist, müssen auch
    die vom Asset abhängigen Metadaten-Felder (asset_origin, rights_status,
    provider, source_url, supplement_request_id) aufgefrischt werden.
    Vorher blieben diese Felder vom VORHER zugewiesenen (jetzt ungültigen)
    Asset stehen — z. B. zeigte ein neu zugewiesenes Supplement-Asset
    fälschlich weiterhin asset_origin='local_original' oder ''."""
    project = _project(tmp_path)
    rules = EditPlanRulesDocument(
        project_id=project.id,
        rules=[
            EditPlanRule(
                id="r1",
                rule_type=RULE_MAX_ASSET_USES,
                enabled=True,
                params={"max_count": 1},
            ),
        ],
    )
    assets = {
        "Folder": [
            {"path": "/media/a.mp4", "asset_id": "asset_a", "asset_origin": "local_original"},
            {
                "path": "/media/supplement.mp4",
                "asset_id": "asset_supplement",
                "asset_origin": "pexels",
                "rights_status": "APPROVED",
                "provider": "pexels",
                "source_url": "https://pexels.example/123",
                "supplement_request_id": "supp_req_001",
            },
        ]
    }
    shots = [
        EditPlanShot(
            voice_file="voice.wav",
            folder="Folder",
            voice_start_sec=0.0,
            voice_end_sec=3.0,
            duration_sec=3.0,
            asset_path="/media/a.mp4",
            asset_id="asset_a",
            asset_origin="local_original",
        ),
        # Zweiter Shot will DASSELBE Asset — verletzt max_count=1, muss auf
        # das Supplement-Asset umgelenkt werden.
        EditPlanShot(
            voice_file="voice.wav",
            folder="Folder",
            voice_start_sec=3.0,
            voice_end_sec=6.0,
            duration_sec=3.0,
            asset_path="/media/a.mp4",
            asset_id="asset_a",
            asset_origin="local_original",
        ),
    ]

    adjusted = apply_edit_plan_rules(shots, rules, assets)

    assert adjusted[0].asset_path == "/media/a.mp4"
    assert adjusted[0].asset_origin == "local_original"

    assert adjusted[1].asset_path == "/media/supplement.mp4"
    assert adjusted[1].asset_id == "asset_supplement"
    assert adjusted[1].asset_origin == "pexels", (
        f"asset_origin blieb fälschlich stehen: {adjusted[1].asset_origin!r}"
    )
    assert adjusted[1].rights_status == "APPROVED"
    assert adjusted[1].provider == "pexels"
    assert adjusted[1].source_url == "https://pexels.example/123"
    assert adjusted[1].supplement_request_id == "supp_req_001"


def test_apply_rules_respects_min_gap_between_reuse(tmp_path: Path) -> None:
    """Regression: 'Min. Abstand (Shots) bis Wiederverwendung' — dasselbe Asset
    darf erst nach min_gap ANDEREN Shots erneut verwendet werden."""
    project = _project(tmp_path)
    rules = EditPlanRulesDocument(
        project_id=project.id,
        rules=[
            EditPlanRule(
                id="r1",
                rule_type=RULE_MAX_ASSET_USES,
                enabled=True,
                params={"max_count": 10, "min_gap": 2},
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
    # a.mp4 darf erst wieder auftauchen, wenn mind. 2 andere Shots dazwischen liegen.
    last_a_index = -100
    for index, path in enumerate(paths):
        if path == "/media/a.mp4":
            if last_a_index >= 0:
                assert index - last_a_index > 2, (index, last_a_index, paths)
            last_a_index = index
    assert validate_shots_against_rules(adjusted, rules) == []


def test_validate_shots_reports_min_gap_violation(tmp_path: Path) -> None:
    project = _project(tmp_path)
    rules = EditPlanRulesDocument(
        project_id=project.id,
        rules=[
            EditPlanRule(
                id="r1",
                rule_type=RULE_MAX_ASSET_USES,
                enabled=True,
                params={"max_count": 10, "min_gap": 3},
            ),
        ],
    )
    shots = [
        _shot(1, "/media/a.mp4"),
        _shot(2, "/media/b.mp4"),
        _shot(3, "/media/a.mp4"),
    ]
    violations = validate_shots_against_rules(shots, rules)
    assert any("Mindestabstand" in v for v in violations)


def test_min_gap_defaults_to_zero_and_disabled(tmp_path: Path) -> None:
    """min_gap=0 (Default) darf bestehendes Verhalten nicht verändern."""
    project = _project(tmp_path)
    rules = EditPlanRulesDocument(
        project_id=project.id,
        rules=[
            EditPlanRule(
                id="r1",
                rule_type=RULE_MAX_ASSET_USES,
                enabled=True,
                params={"max_count": 10},
            ),
        ],
    )
    shots = [
        _shot(1, "/media/a.mp4"),
        _shot(2, "/media/a.mp4"),
    ]
    assert validate_shots_against_rules(shots, rules) == []
