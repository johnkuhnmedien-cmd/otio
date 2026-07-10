"""Phase 2 (Asset-bewusste Cut-Plan-Vorbereitung): reine Asset-Readiness-
Diagnose für Folder-Voice-over-Drafts (folder_asset_readiness.py)."""

from __future__ import annotations

from pathlib import Path

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import get_folder_inventory_path
from otio_app.services.voiceover_generation.folder_asset_readiness import (
    ISSUE_TYPE_DIRECT_REPEAT,
    ISSUE_TYPE_INVALID_ASSET_ID,
    ISSUE_TYPE_LONG_SENTENCE_LOW_ALTERNATIVES,
    ISSUE_TYPE_SUPPLEMENT_RECOMMENDED,
    READINESS_STATUS_NEEDS_REVIEW,
    READINESS_STATUS_PASS,
    build_folder_asset_readiness_report,
    estimate_sentence_duration_sec,
)
from otio_app.services.voiceover_generation.models import FolderVoiceoverDraft, SentenceItem


def _make_project_with_inventory(tmp_path: Path, folder_name: str, asset_ids: list[str]) -> Project:
    project_root = tmp_path / "USA"
    (project_root / folder_name).mkdir(parents=True)
    project = Project(
        id="readiness-project",
        name="Readiness Test",
        project_root=str(project_root),
        work_dir=str(project_root / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=[folder_name],
        selected_asset_subdirs=[folder_name],
    )
    path = get_folder_inventory_path(project.work_dir_path, folder_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    analysis = AssetFolderAnalysis(
        folder=folder_name,
        assets=[
            AssetMediaAnalysis(path=f"{folder_name}/{asset_id}.mp4", asset_id=asset_id, description=asset_id)
            for asset_id in asset_ids
        ],
    )
    path.write_text(analysis.model_dump_json(indent=2), encoding="utf-8")
    return project


def _draft(folder_name: str, sentence_items: list[SentenceItem]) -> FolderVoiceoverDraft:
    return FolderVoiceoverDraft(
        project_id="readiness-project", folder_name=folder_name, sentence_items=sentence_items
    )


# --- estimate_sentence_duration_sec ---


def test_estimate_sentence_duration_sec_returns_zero_for_empty_text() -> None:
    assert estimate_sentence_duration_sec("") == 0.0
    assert estimate_sentence_duration_sec("   ") == 0.0


def test_estimate_sentence_duration_sec_uses_words_per_second_heuristic() -> None:
    # 5 Wörter / 2.5 Wörter pro Sekunde (Default-Heuristik) = 2.0s.
    assert estimate_sentence_duration_sec("Ein zwei drei vier fünf") == 2.0


# --- Grundfälle ---


def test_report_status_pass_when_no_issues(tmp_path: Path) -> None:
    project = _make_project_with_inventory(tmp_path, "Grand Canyon", ["asset_a", "asset_b"])
    draft = _draft(
        "Grand Canyon",
        [
            SentenceItem(sentence_id="s1", text="Ein kurzer Satz.", primary_asset_id="asset_a"),
            SentenceItem(sentence_id="s2", text="Noch ein Satz hier.", primary_asset_id="asset_b"),
        ],
    )
    report = build_folder_asset_readiness_report(project, draft)
    assert report.status == READINESS_STATUS_PASS
    assert report.issues == []
    assert report.sentence_count == 2
    assert report.with_primary_count == 2


def test_report_counts_with_primary_and_with_backup(tmp_path: Path) -> None:
    project = _make_project_with_inventory(tmp_path, "Grand Canyon", ["asset_a", "asset_b"])
    draft = _draft(
        "Grand Canyon",
        [
            SentenceItem(sentence_id="s1", text="Text eins.", primary_asset_id="asset_a"),
            SentenceItem(
                sentence_id="s2",
                text="Text zwei.",
                primary_asset_id="asset_b",
                backup_asset_ids=["asset_a"],
            ),
            SentenceItem(sentence_id="s3", text="Text drei ohne Asset."),
        ],
    )
    report = build_folder_asset_readiness_report(project, draft)
    assert report.with_primary_count == 2
    assert report.with_backup_count == 1


# --- INVALID_ASSET_ID ---


def test_report_flags_invalid_asset_id_not_in_inventory(tmp_path: Path) -> None:
    project = _make_project_with_inventory(tmp_path, "Grand Canyon", ["asset_a"])
    draft = _draft(
        "Grand Canyon",
        [SentenceItem(sentence_id="s1", text="Text.", primary_asset_id="asset_does_not_exist")],
    )
    report = build_folder_asset_readiness_report(project, draft)
    assert report.status == READINESS_STATUS_NEEDS_REVIEW
    assert report.invalid_asset_id_count == 1
    assert any(issue.issue_type == ISSUE_TYPE_INVALID_ASSET_ID for issue in report.issues)


def test_report_flags_invalid_backup_asset_id(tmp_path: Path) -> None:
    project = _make_project_with_inventory(tmp_path, "Grand Canyon", ["asset_a"])
    draft = _draft(
        "Grand Canyon",
        [
            SentenceItem(
                sentence_id="s1",
                text="Text.",
                primary_asset_id="asset_a",
                backup_asset_ids=["asset_missing"],
            )
        ],
    )
    report = build_folder_asset_readiness_report(project, draft)
    assert report.invalid_asset_id_count == 1


# --- DIRECT_REPEAT ---


def test_report_flags_direct_repeat_of_same_primary_asset(tmp_path: Path) -> None:
    project = _make_project_with_inventory(tmp_path, "Grand Canyon", ["asset_a"])
    draft = _draft(
        "Grand Canyon",
        [
            SentenceItem(sentence_id="s1", text="Erster Satz.", primary_asset_id="asset_a"),
            SentenceItem(sentence_id="s2", text="Zweiter Satz.", primary_asset_id="asset_a"),
        ],
    )
    report = build_folder_asset_readiness_report(project, draft)
    assert report.direct_repeat_count == 1
    repeat_issues = [issue for issue in report.issues if issue.issue_type == ISSUE_TYPE_DIRECT_REPEAT]
    assert len(repeat_issues) == 1
    assert repeat_issues[0].sentence_id == "s2"


def test_report_does_not_flag_repeat_when_different_primary_assets(tmp_path: Path) -> None:
    project = _make_project_with_inventory(tmp_path, "Grand Canyon", ["asset_a", "asset_b"])
    draft = _draft(
        "Grand Canyon",
        [
            SentenceItem(sentence_id="s1", text="Erster Satz.", primary_asset_id="asset_a"),
            SentenceItem(sentence_id="s2", text="Zweiter Satz.", primary_asset_id="asset_b"),
        ],
    )
    report = build_folder_asset_readiness_report(project, draft)
    assert report.direct_repeat_count == 0


def test_report_does_not_flag_repeat_across_non_adjacent_sentences(tmp_path: Path) -> None:
    """Nur DIREKT aufeinanderfolgende Sätze mit demselben Primary-Asset
    gelten als problematische Wiederholung — eine spätere Wiederkehr nach
    einem Asset-Wechsel dazwischen ist redaktionell in Ordnung."""
    project = _make_project_with_inventory(tmp_path, "Grand Canyon", ["asset_a", "asset_b"])
    draft = _draft(
        "Grand Canyon",
        [
            SentenceItem(sentence_id="s1", text="Erster Satz.", primary_asset_id="asset_a"),
            SentenceItem(sentence_id="s2", text="Zweiter Satz.", primary_asset_id="asset_b"),
            SentenceItem(sentence_id="s3", text="Dritter Satz.", primary_asset_id="asset_a"),
        ],
    )
    report = build_folder_asset_readiness_report(project, draft)
    assert report.direct_repeat_count == 0


# --- LONG_SENTENCE_LOW_ALTERNATIVES ---


def test_report_flags_long_sentence_with_too_few_alternatives(tmp_path: Path) -> None:
    project = _make_project_with_inventory(tmp_path, "Grand Canyon", ["asset_a"])
    # 21 Wörter / 2.5 Wörter pro Sekunde ~= 8.4s -> bei shot_max_sec=8.0
    # werden 2 Segmente benötigt, aber nur 1 nutzbares Asset ist zugeordnet.
    long_text = " ".join(["Wort"] * 21)
    draft = _draft(
        "Grand Canyon",
        [SentenceItem(sentence_id="s1", text=long_text, primary_asset_id="asset_a")],
    )
    report = build_folder_asset_readiness_report(project, draft, shot_max_sec=8.0)
    assert report.long_sentence_low_alternative_count == 1
    assert any(
        issue.issue_type == ISSUE_TYPE_LONG_SENTENCE_LOW_ALTERNATIVES for issue in report.issues
    )


def test_report_does_not_flag_long_sentence_when_enough_alternatives(tmp_path: Path) -> None:
    project = _make_project_with_inventory(tmp_path, "Grand Canyon", ["asset_a", "asset_b"])
    long_text = " ".join(["Wort"] * 21)
    draft = _draft(
        "Grand Canyon",
        [
            SentenceItem(
                sentence_id="s1",
                text=long_text,
                primary_asset_id="asset_a",
                backup_asset_ids=["asset_b"],
            )
        ],
    )
    report = build_folder_asset_readiness_report(project, draft, shot_max_sec=8.0)
    assert report.long_sentence_low_alternative_count == 0


def test_report_does_not_flag_short_sentence_even_with_one_asset(tmp_path: Path) -> None:
    project = _make_project_with_inventory(tmp_path, "Grand Canyon", ["asset_a"])
    draft = _draft(
        "Grand Canyon",
        [SentenceItem(sentence_id="s1", text="Kurzer Satz.", primary_asset_id="asset_a")],
    )
    report = build_folder_asset_readiness_report(project, draft, shot_max_sec=8.0)
    assert report.long_sentence_low_alternative_count == 0


# --- SUPPLEMENT_RECOMMENDED ---


def test_report_flags_supplement_recommended_when_needs_supplement_asset_true(
    tmp_path: Path,
) -> None:
    project = _make_project_with_inventory(tmp_path, "Grand Canyon", ["asset_a"])
    draft = _draft(
        "Grand Canyon",
        [
            SentenceItem(
                sentence_id="s1",
                text="Text ohne passendes Asset.",
                needs_supplement_asset=True,
                supplement_reason="Kein Asset zeigt das gesuchte Motiv.",
            )
        ],
    )
    report = build_folder_asset_readiness_report(project, draft)
    assert report.supplement_recommended_count == 1
    issue = next(i for i in report.issues if i.issue_type == ISSUE_TYPE_SUPPLEMENT_RECOMMENDED)
    assert issue.message == "Kein Asset zeigt das gesuchte Motiv."


def test_report_flags_supplement_recommended_when_no_primary_and_no_backup(
    tmp_path: Path,
) -> None:
    project = _make_project_with_inventory(tmp_path, "Grand Canyon", ["asset_a"])
    draft = _draft("Grand Canyon", [SentenceItem(sentence_id="s1", text="Text ohne Zuordnung.")])
    report = build_folder_asset_readiness_report(project, draft)
    assert report.supplement_recommended_count == 1


def test_report_status_needs_review_when_issues_present(tmp_path: Path) -> None:
    project = _make_project_with_inventory(tmp_path, "Grand Canyon", ["asset_a"])
    draft = _draft("Grand Canyon", [SentenceItem(sentence_id="s1", text="Text ohne Zuordnung.")])
    report = build_folder_asset_readiness_report(project, draft)
    assert report.status == READINESS_STATUS_NEEDS_REVIEW


def test_report_is_pure_read_only(tmp_path: Path) -> None:
    """Reine Diagnose — darf keine Dateien unter _otio/ schreiben."""
    project = _make_project_with_inventory(tmp_path, "Grand Canyon", ["asset_a"])
    draft = _draft(
        "Grand Canyon",
        [SentenceItem(sentence_id="s1", text="Text.", primary_asset_id="asset_a")],
    )
    files_before = sorted(p for p in project.work_dir_path.rglob("*") if p.is_file())
    build_folder_asset_readiness_report(project, draft)
    files_after = sorted(p for p in project.work_dir_path.rglob("*") if p.is_file())
    assert files_before == files_after


# --- Phase 4 (second_backup_asset_ids counts toward alternatives) ---


def test_second_backup_asset_id_counts_toward_long_sentence_alternatives(tmp_path: Path) -> None:
    """Nutzergrundsatz: second_backup_asset_ids sind echte, passende
    Alternativen — sie müssen also mitzählen, wenn geprüft wird, ob genug
    Assets für einen späteren Split vorhanden sind."""
    project = _make_project_with_inventory(tmp_path, "Grand Canyon", ["asset_a", "asset_b"])
    long_text = " ".join(["Wort"] * 21)
    draft = _draft(
        "Grand Canyon",
        [
            SentenceItem(
                sentence_id="s1",
                text=long_text,
                primary_asset_id="asset_a",
                second_backup_asset_ids=["asset_b"],
            )
        ],
    )
    report = build_folder_asset_readiness_report(project, draft, shot_max_sec=8.0)
    assert report.long_sentence_low_alternative_count == 0


def test_invalid_second_backup_asset_id_is_flagged(tmp_path: Path) -> None:
    project = _make_project_with_inventory(tmp_path, "Grand Canyon", ["asset_a"])
    draft = _draft(
        "Grand Canyon",
        [
            SentenceItem(
                sentence_id="s1",
                text="Text.",
                primary_asset_id="asset_a",
                second_backup_asset_ids=["asset_missing"],
            )
        ],
    )
    report = build_folder_asset_readiness_report(project, draft)
    assert report.invalid_asset_id_count == 1


def test_empty_draft_has_no_issues(tmp_path: Path) -> None:
    project = _make_project_with_inventory(tmp_path, "Grand Canyon", ["asset_a"])
    draft = _draft("Grand Canyon", [])
    report = build_folder_asset_readiness_report(project, draft)
    assert report.sentence_count == 0
    assert report.status == READINESS_STATUS_PASS
