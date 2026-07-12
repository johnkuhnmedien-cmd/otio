"""Phase 2: Project Brief — Service-Tests."""

from __future__ import annotations

from pathlib import Path

from otio_app.defaults import (
    BRIEF_NEGATIVE_RULE_BIBLICAL_CHRONOLOGY_REQUIRED,
    BRIEF_NEGATIVE_RULE_FLAGS,
    BRIEF_NEGATIVE_RULE_INSTRUCTIONS,
    BRIEF_NEGATIVE_RULE_LABELS,
    BRIEF_NEGATIVE_RULE_NO_CLICHES,
    BRIEF_NEGATIVE_RULE_NO_PARTY_SCENES,
    BRIEF_NEGATIVE_RULE_VOICE_NOT_AI_SOUNDING,
)
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import get_project_brief_path, get_voiceover_generation_dir
from otio_app.services.voiceover_generation.project_brief_service import (
    default_project_brief,
    load_project_brief,
    parse_forbidden_phrases_text,
    save_project_brief,
)
from otio_app.services.voiceover_generation.models import ProjectBrief


def _make_project(tmp_path: Path) -> Project:
    project_root = tmp_path / "USA"
    project_root.mkdir()
    return Project(
        id="brief-project",
        name="Brief Test",
        project_root=str(project_root),
        work_dir=str(project_root / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )


def test_default_project_brief_has_language_de(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    brief = default_project_brief(project)
    assert brief.language == "DE"
    assert brief.project_id == project.id


def test_default_project_brief_enables_all_negative_rules(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    brief = default_project_brief(project)
    assert brief.negative_rule_flags
    assert all(brief.negative_rule_flags.values())


# --- Nutzerfeedback (Juli 2026): neue Standard-Negativregeln ---


def test_new_standard_negative_rules_exist_and_are_active_by_default(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    brief = default_project_brief(project)
    for flag in (
        BRIEF_NEGATIVE_RULE_BIBLICAL_CHRONOLOGY_REQUIRED,
        BRIEF_NEGATIVE_RULE_NO_PARTY_SCENES,
        BRIEF_NEGATIVE_RULE_VOICE_NOT_AI_SOUNDING,
        BRIEF_NEGATIVE_RULE_NO_CLICHES,
    ):
        assert flag in brief.negative_rule_flags
        assert brief.negative_rule_flags[flag] is True


def test_negative_rule_flags_have_no_duplicates() -> None:
    assert len(BRIEF_NEGATIVE_RULE_FLAGS) == len(set(BRIEF_NEGATIVE_RULE_FLAGS))


def test_unused_standard_negative_rules_removed_from_ui() -> None:
    """Nutzerfeedback (Juli 2026): die ursprünglichen Standardregeln, die im
    laufenden Projekt nie aktiviert wurden ("nicht gecheckte Boxen"), wurden
    auf ausdrücklichen Wunsch komplett aus der UI/Konfiguration entfernt —
    nur die tatsächlich genutzten 5 Regeln bleiben übrig."""
    assert set(BRIEF_NEGATIVE_RULE_FLAGS) == {
        "no_unverified_historical_claims",
        "biblical_chronology_required",
        "no_party_scenes",
        "voice_not_ai_sounding",
        "no_cliches",
    }


def test_every_negative_rule_flag_has_a_label_and_instruction() -> None:
    for flag in BRIEF_NEGATIVE_RULE_FLAGS:
        assert flag in BRIEF_NEGATIVE_RULE_LABELS
        assert BRIEF_NEGATIVE_RULE_LABELS[flag].strip()
        assert flag in BRIEF_NEGATIVE_RULE_INSTRUCTIONS
        assert BRIEF_NEGATIVE_RULE_INSTRUCTIONS[flag].strip()


def test_save_and_load_project_brief_roundtrip(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    brief = ProjectBrief(
        project_id=project.id,
        video_title="Wunder der Wüste",
        language="EN",
        tone_tags=["cinematic", "mysterious"],
        negative_rule_flags={"no_invented_facts": True, "no_repetition": False},
        negative_rules_freetext="Keine Klischees.",
        forbidden_phrases=["breathtaking", "must-see"],
        global_extra_prompt="Schreibe wie ein Naturfilm-Kommentator.",
    )
    save_project_brief(project, brief)

    loaded = load_project_brief(project)
    assert loaded.video_title == "Wunder der Wüste"
    assert loaded.language == "EN"
    assert loaded.tone_tags == ["cinematic", "mysterious"]
    assert loaded.negative_rule_flags == {"no_invented_facts": True, "no_repetition": False}
    assert loaded.forbidden_phrases == ["breathtaking", "must-see"]
    assert loaded.global_extra_prompt == "Schreibe wie ein Naturfilm-Kommentator."


def test_load_project_brief_returns_default_when_missing(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    loaded = load_project_brief(project)
    assert loaded.language == "DE"
    assert loaded.video_title == ""


def test_parse_forbidden_phrases_text_splits_lines() -> None:
    text = "breathtaking\n  must-see  \n\nin this video\n"
    assert parse_forbidden_phrases_text(text) == ["breathtaking", "must-see", "in this video"]


def test_parse_forbidden_phrases_text_ignores_blank_lines() -> None:
    assert parse_forbidden_phrases_text("\n\n   \n") == []


def test_save_project_brief_writes_only_under_voiceover_generation_dir(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    brief = default_project_brief(project)
    save_project_brief(project, brief)

    expected_path = get_project_brief_path(project.language_work_dir_path)
    assert expected_path.is_file()

    voiceover_gen_dir = get_voiceover_generation_dir(project.language_work_dir_path)
    assert expected_path.is_relative_to(voiceover_gen_dir)

    # Es darf keine edit_plan/-Struktur durch das Speichern entstehen.
    assert not (project.language_work_dir_path / "edit_plan").exists()
    assert not (project.language_work_dir_path / "exports").exists()


def test_save_project_brief_updates_project_id_and_timestamp(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    brief = ProjectBrief(project_id="wrong-id", video_title="Test")
    saved = save_project_brief(project, brief)
    assert saved.project_id == project.id
