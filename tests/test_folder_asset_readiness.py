"""Phase 2 (Asset-bewusste Cut-Plan-Vorbereitung): reine Asset-Readiness-
Diagnose für Folder-Voice-over-Drafts (folder_asset_readiness.py)."""

from __future__ import annotations

from pathlib import Path

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import get_folder_inventory_path
from otio_app.services.voiceover_generation.folder_asset_readiness import (
    CLOSING_SHOT_ISSUE_SENTENCE_ID,
    ISSUE_TYPE_ASSET_OVER_FOLDER_LIMIT,
    ISSUE_TYPE_ASSET_REUSE_DISTANCE_TOO_SHORT,
    ISSUE_TYPE_CLOSING_SHOT_MISSING,
    ISSUE_TYPE_CLOSING_SHOT_REUSES_RECENT_SENTENCE,
    ISSUE_TYPE_DIRECT_REPEAT,
    ISSUE_TYPE_INVALID_ASSET_ID,
    ISSUE_TYPE_LONG_SENTENCE_LOW_ALTERNATIVES,
    ISSUE_TYPE_SCARCE_ASSET_ASSIGNED_TO_FLEXIBLE_SENTENCE,
    ISSUE_TYPE_SUPPLEMENT_RECOMMENDED,
    READINESS_STATUS_NEEDS_REVIEW,
    READINESS_STATUS_PASS,
    build_folder_asset_readiness_report,
    estimate_sentence_duration_sec,
)
from otio_app.services.voiceover_generation.models import (
    ClosingVisualPlan,
    FolderVoiceoverDraft,
    SentenceItem,
)


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


def _draft(
    folder_name: str,
    sentence_items: list[SentenceItem],
    *,
    closing_visual_plan: ClosingVisualPlan | None = None,
) -> FolderVoiceoverDraft:
    return FolderVoiceoverDraft(
        project_id="readiness-project",
        folder_name=folder_name,
        sentence_items=sentence_items,
        closing_visual_plan=closing_visual_plan or ClosingVisualPlan(),
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
    project = _make_project_with_inventory(tmp_path, "Grand Canyon", ["asset_a", "asset_b", "asset_c"])
    draft = _draft(
        "Grand Canyon",
        [
            SentenceItem(sentence_id="s1", text="Ein kurzer Satz.", primary_asset_id="asset_a"),
            SentenceItem(sentence_id="s2", text="Noch ein Satz hier.", primary_asset_id="asset_b"),
        ],
        closing_visual_plan=ClosingVisualPlan(primary_asset_id="asset_c"),
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


# --- Closing Shot (Nutzervorgabe Juli 2026, "kein closing asset nach dem
# letzten Satz, der die Pause ausfüllt") ---


def test_report_flags_closing_shot_missing_when_no_content_or_supplement(tmp_path: Path) -> None:
    project = _make_project_with_inventory(tmp_path, "Grand Canyon", ["asset_a"])
    draft = _draft(
        "Grand Canyon",
        [SentenceItem(sentence_id="s1", text="Ein Satz.", primary_asset_id="asset_a")],
    )
    report = build_folder_asset_readiness_report(project, draft)
    assert report.closing_shot_missing_count == 1
    assert any(issue.issue_type == ISSUE_TYPE_CLOSING_SHOT_MISSING for issue in report.issues)


def test_report_does_not_flag_closing_shot_missing_when_supplement_requested(tmp_path: Path) -> None:
    project = _make_project_with_inventory(tmp_path, "Grand Canyon", ["asset_a"])
    draft = _draft(
        "Grand Canyon",
        [SentenceItem(sentence_id="s1", text="Ein Satz.", primary_asset_id="asset_a")],
        closing_visual_plan=ClosingVisualPlan(
            needs_supplement_asset=True, supplement_reason="Kein passendes Abschlussmotiv lokal."
        ),
    )
    report = build_folder_asset_readiness_report(project, draft)
    assert report.closing_shot_missing_count == 0


def test_report_flags_closing_shot_reusing_last_sentence_asset(tmp_path: Path) -> None:
    project = _make_project_with_inventory(tmp_path, "Grand Canyon", ["asset_a", "asset_b"])
    draft = _draft(
        "Grand Canyon",
        [
            SentenceItem(sentence_id="s1", text="Erster Satz.", primary_asset_id="asset_a"),
            SentenceItem(sentence_id="s2", text="Letzter Satz.", primary_asset_id="asset_b"),
        ],
        closing_visual_plan=ClosingVisualPlan(primary_asset_id="asset_b"),
    )
    report = build_folder_asset_readiness_report(project, draft)
    assert report.closing_shot_reuse_conflict_count == 1
    issue = next(i for i in report.issues if i.issue_type == ISSUE_TYPE_CLOSING_SHOT_REUSES_RECENT_SENTENCE)
    assert issue.sentence_id == CLOSING_SHOT_ISSUE_SENTENCE_ID


def test_report_flags_closing_shot_reusing_second_to_last_sentence_asset(tmp_path: Path) -> None:
    project = _make_project_with_inventory(tmp_path, "Grand Canyon", ["asset_a", "asset_b"])
    draft = _draft(
        "Grand Canyon",
        [
            SentenceItem(sentence_id="s1", text="Erster Satz.", primary_asset_id="asset_a"),
            SentenceItem(sentence_id="s2", text="Letzter Satz.", primary_asset_id="asset_b"),
        ],
        closing_visual_plan=ClosingVisualPlan(primary_asset_id="asset_a"),
    )
    report = build_folder_asset_readiness_report(project, draft)
    assert report.closing_shot_reuse_conflict_count == 1


def test_report_does_not_flag_closing_shot_with_distinct_asset(tmp_path: Path) -> None:
    project = _make_project_with_inventory(tmp_path, "Grand Canyon", ["asset_a", "asset_b", "asset_c"])
    draft = _draft(
        "Grand Canyon",
        [
            SentenceItem(sentence_id="s1", text="Erster Satz.", primary_asset_id="asset_a"),
            SentenceItem(sentence_id="s2", text="Letzter Satz.", primary_asset_id="asset_b"),
        ],
        closing_visual_plan=ClosingVisualPlan(primary_asset_id="asset_c"),
    )
    report = build_folder_asset_readiness_report(project, draft)
    assert report.closing_shot_reuse_conflict_count == 0


def test_report_does_not_require_closing_shot_for_empty_draft(tmp_path: Path) -> None:
    project = _make_project_with_inventory(tmp_path, "Grand Canyon", ["asset_a"])
    draft = _draft("Grand Canyon", [])
    report = build_folder_asset_readiness_report(project, draft)
    assert report.closing_shot_missing_count == 0


# --- Folder-wide asset allocation: max occurrences + min shot distance ---


def test_report_flags_asset_used_more_than_max_occurrences(tmp_path: Path) -> None:
    project = _make_project_with_inventory(tmp_path, "Grand Canyon", ["asset_a", "asset_b"])
    draft = _draft(
        "Grand Canyon",
        [
            SentenceItem(sentence_id="s1", text="Satz eins.", primary_asset_id="asset_a"),
            SentenceItem(sentence_id="s2", text="Satz zwei.", primary_asset_id="asset_b"),
            SentenceItem(sentence_id="s3", text="Satz drei.", primary_asset_id="asset_a"),
            SentenceItem(sentence_id="s4", text="Satz vier.", primary_asset_id="asset_b"),
            SentenceItem(sentence_id="s5", text="Satz fünf.", primary_asset_id="asset_a"),
            SentenceItem(sentence_id="s6", text="Satz sechs.", primary_asset_id="asset_b"),
            SentenceItem(sentence_id="s7", text="Satz sieben.", primary_asset_id="asset_a"),
        ],
        closing_visual_plan=ClosingVisualPlan(primary_asset_id="asset_b"),
    )
    report = build_folder_asset_readiness_report(project, draft)
    assert report.asset_over_folder_limit_count == 2  # asset_a (4x) und asset_b (4x)
    assert any(issue.issue_type == ISSUE_TYPE_ASSET_OVER_FOLDER_LIMIT for issue in report.issues)


def test_report_does_not_flag_asset_at_exactly_max_occurrences(tmp_path: Path) -> None:
    project = _make_project_with_inventory(tmp_path, "Grand Canyon", ["asset_a"])
    draft = _draft(
        "Grand Canyon",
        [
            SentenceItem(sentence_id="s1", text="Satz eins.", primary_asset_id="asset_a"),
            SentenceItem(sentence_id="s2", text="Satz zwei.", second_backup_asset_ids=["asset_a"]),
            SentenceItem(sentence_id="s3", text="Satz drei.", backup_asset_ids=["asset_a"]),
        ],
    )
    report = build_folder_asset_readiness_report(project, draft)
    assert report.asset_over_folder_limit_count == 0


def test_report_flags_asset_reuse_below_minimum_shot_distance(tmp_path: Path) -> None:
    project = _make_project_with_inventory(tmp_path, "Grand Canyon", ["asset_a", "asset_b"])
    draft = _draft(
        "Grand Canyon",
        [
            SentenceItem(sentence_id="s1", text="Satz eins.", primary_asset_id="asset_a"),
            SentenceItem(sentence_id="s2", text="Satz zwei.", primary_asset_id="asset_b"),
            SentenceItem(sentence_id="s3", text="Satz drei.", primary_asset_id="asset_a"),
        ],
    )
    report = build_folder_asset_readiness_report(project, draft)
    assert report.asset_reuse_distance_violation_count == 1
    issue = next(i for i in report.issues if i.issue_type == ISSUE_TYPE_ASSET_REUSE_DISTANCE_TOO_SHORT)
    assert issue.sentence_id == "s3"


def test_report_does_not_flag_asset_reuse_at_minimum_shot_distance(tmp_path: Path) -> None:
    project = _make_project_with_inventory(
        tmp_path, "Grand Canyon", ["asset_a", "asset_b", "asset_c", "asset_d"]
    )
    draft = _draft(
        "Grand Canyon",
        [
            SentenceItem(sentence_id="s1", text="Satz eins.", primary_asset_id="asset_a"),
            SentenceItem(sentence_id="s2", text="Satz zwei.", primary_asset_id="asset_b"),
            SentenceItem(sentence_id="s3", text="Satz drei.", primary_asset_id="asset_c"),
            SentenceItem(sentence_id="s4", text="Satz vier.", primary_asset_id="asset_d"),
            SentenceItem(sentence_id="s5", text="Satz fünf.", primary_asset_id="asset_a"),
        ],
    )
    report = build_folder_asset_readiness_report(project, draft)
    assert report.asset_reuse_distance_violation_count == 0


def test_report_counts_closing_shot_as_final_shot_position_for_distance(tmp_path: Path) -> None:
    """Ein Asset, das im ersten Satz UND im Closing Shot verwendet wird, muss
    denselben Mindestabstand einhalten wie zwei sentence_items."""
    project = _make_project_with_inventory(tmp_path, "Grand Canyon", ["asset_a", "asset_b"])
    draft = _draft(
        "Grand Canyon",
        [
            SentenceItem(sentence_id="s1", text="Satz eins.", primary_asset_id="asset_a"),
            SentenceItem(sentence_id="s2", text="Satz zwei.", primary_asset_id="asset_b"),
        ],
        closing_visual_plan=ClosingVisualPlan(primary_asset_id="asset_a"),
    )
    report = build_folder_asset_readiness_report(project, draft)
    assert report.asset_reuse_distance_violation_count == 1
    issue = next(i for i in report.issues if i.issue_type == ISSUE_TYPE_ASSET_REUSE_DISTANCE_TOO_SHORT)
    assert issue.sentence_id == CLOSING_SHOT_ISSUE_SENTENCE_ID


# --- Scarce asset allocation ---


def test_report_flags_flexible_sentence_taking_scarce_asset(tmp_path: Path) -> None:
    """Satz s1 hat NUR asset_a als Option, Satz s2 hätte auch asset_b/asset_c
    nutzen können — s2 soll das knappe Asset abgeben, nicht s1."""
    project = _make_project_with_inventory(
        tmp_path, "Grand Canyon", ["asset_a", "asset_b", "asset_c"]
    )
    draft = _draft(
        "Grand Canyon",
        [
            SentenceItem(
                sentence_id="s1",
                text="Satz eins.",
                primary_asset_id="asset_a",
                source_inventory_asset_ids_considered=["asset_a"],
            ),
            SentenceItem(
                sentence_id="s2",
                text="Satz zwei.",
                primary_asset_id="asset_a",
                source_inventory_asset_ids_considered=["asset_a", "asset_b", "asset_c"],
            ),
        ],
    )
    report = build_folder_asset_readiness_report(project, draft)
    assert report.scarce_asset_conflict_count == 1
    issue = next(
        i for i in report.issues if i.issue_type == ISSUE_TYPE_SCARCE_ASSET_ASSIGNED_TO_FLEXIBLE_SENTENCE
    )
    assert issue.sentence_id == "s2"


def test_report_does_not_flag_scarce_asset_when_both_sentences_equally_constrained(
    tmp_path: Path,
) -> None:
    project = _make_project_with_inventory(tmp_path, "Grand Canyon", ["asset_a"])
    draft = _draft(
        "Grand Canyon",
        [
            SentenceItem(
                sentence_id="s1",
                text="Satz eins.",
                primary_asset_id="asset_a",
                source_inventory_asset_ids_considered=["asset_a"],
            ),
            SentenceItem(
                sentence_id="s2",
                text="Satz zwei.",
                primary_asset_id="asset_a",
                source_inventory_asset_ids_considered=["asset_a"],
            ),
        ],
    )
    report = build_folder_asset_readiness_report(project, draft)
    assert report.scarce_asset_conflict_count == 0


def test_report_does_not_flag_scarce_asset_when_only_one_sentence_uses_it(tmp_path: Path) -> None:
    project = _make_project_with_inventory(tmp_path, "Grand Canyon", ["asset_a", "asset_b"])
    draft = _draft(
        "Grand Canyon",
        [
            SentenceItem(sentence_id="s1", text="Satz eins.", primary_asset_id="asset_a"),
            SentenceItem(sentence_id="s2", text="Satz zwei.", primary_asset_id="asset_b"),
        ],
    )
    report = build_folder_asset_readiness_report(project, draft)
    assert report.scarce_asset_conflict_count == 0


# --- Bulk Asset-Readiness für alle Ordner ---


def test_build_all_folder_asset_readiness_reports_covers_active_drafts(tmp_path: Path) -> None:
    from otio_app.services.voiceover_generation.dramaturgy_service import save_confirmed_dramaturgy
    from otio_app.services.voiceover_generation.folder_asset_readiness import (
        build_all_folder_asset_readiness_reports,
    )
    from otio_app.services.voiceover_generation.models import DramaturgyFolderEntry, DramaturgyPlan
    from otio_app.services.voiceover_generation.voiceover_author_service import (
        upsert_folder_voiceover_draft_item,
    )

    project_root = tmp_path / "USA"
    for folder in ("Grand Canyon", "Yellowstone"):
        (project_root / folder).mkdir(parents=True)
    project = Project(
        id="readiness-bulk-project",
        name="Readiness Bulk",
        project_root=str(project_root),
        work_dir=str(project_root / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=["Grand Canyon", "Yellowstone"],
        selected_asset_subdirs=["Grand Canyon", "Yellowstone"],
    )
    for folder, assets in (
        ("Grand Canyon", ["asset_a", "asset_b", "asset_c"]),
        ("Yellowstone", ["asset_a", "asset_b"]),
    ):
        path = get_folder_inventory_path(project.work_dir_path, folder)
        path.parent.mkdir(parents=True, exist_ok=True)
        analysis = AssetFolderAnalysis(
            folder=folder,
            assets=[
                AssetMediaAnalysis(path=f"{folder}/{asset_id}.mp4", asset_id=asset_id, description=asset_id)
                for asset_id in assets
            ],
        )
        path.write_text(analysis.model_dump_json(indent=2), encoding="utf-8")

    save_confirmed_dramaturgy(
        project,
        DramaturgyPlan(
            project_id=project.id,
            recommended_folder_order=[
                DramaturgyFolderEntry(folder_name="Grand Canyon", order_index=1, enabled=True),
                DramaturgyFolderEntry(folder_name="Yellowstone", order_index=2, enabled=True),
            ],
        ),
    )
    upsert_folder_voiceover_draft_item(
        project,
        FolderVoiceoverDraft(
            project_id=project.id,
            folder_name="Grand Canyon",
            sentence_items=[
                SentenceItem(sentence_id="s1", text="Ein Satz.", primary_asset_id="asset_a"),
            ],
            closing_visual_plan=ClosingVisualPlan(primary_asset_id="asset_c"),
        ),
    )
    upsert_folder_voiceover_draft_item(
        project,
        FolderVoiceoverDraft(
            project_id=project.id,
            folder_name="Yellowstone",
            sentence_items=[
                SentenceItem(sentence_id="s1", text="Ein Satz.", primary_asset_id="asset_a"),
            ],
            # kein Closing Shot → NEEDS_REVIEW
        ),
    )

    reports = build_all_folder_asset_readiness_reports(project)
    assert [report.folder_name for report in reports] == ["Grand Canyon", "Yellowstone"]
    assert reports[0].status == READINESS_STATUS_PASS
    assert reports[1].status == READINESS_STATUS_NEEDS_REVIEW
    assert reports[1].closing_shot_missing_count == 1


def test_build_all_folder_asset_readiness_reports_raises_without_dramaturgy(tmp_path: Path) -> None:
    from otio_app.services.voiceover_generation.folder_asset_readiness import (
        build_all_folder_asset_readiness_reports,
    )

    project = _make_project_with_inventory(tmp_path, "Grand Canyon", ["asset_a"])
    try:
        build_all_folder_asset_readiness_reports(project)
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "Dramaturgie" in str(exc)
