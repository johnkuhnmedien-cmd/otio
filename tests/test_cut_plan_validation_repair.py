"""Validation Repair Phase 3: Erkennung reparierbarer Rest-Blocker +
Aufbau von CutPlanValidationRepairRequest-Einträgen.

Bewusst kein Netzwerk, keine LLM-Aufrufe, kein Download — reine
Erkennungs-/Aufbau-/Persistenz-Logik (siehe Phase 4/5/6/7 für die
tatsächliche Reparatur-Ausführung)."""

from __future__ import annotations

from pathlib import Path

import pytest

from otio_app.defaults import (
    CUT_PLAN_ERROR_ASSET_REUSE_DISTANCE_TOO_SHORT,
    CUT_PLAN_ERROR_BLACK_GAP_DURING_VOICEOVER,
    CUT_PLAN_ERROR_SHOT_TOO_SHORT,
    CUT_PLAN_VALIDATION_REPAIR_STATUS_PENDING,
    CUT_PLAN_VALIDATION_REPAIR_TYPE_ASSET_REUSE_DISTANCE,
    CUT_PLAN_VALIDATION_REPAIR_TYPE_BLACK_GAP,
)
from otio_app.models import Project, ProjectMode
from otio_app.services.voiceover_generation.cut_plan_models import (
    CutPlanDocument,
    CutPlanItem,
    CutPlanSourceRef,
    CutPlanValidationError,
)
from otio_app.services.voiceover_generation.cut_plan_validation_repair import (
    build_validation_repair_requests_from_cut_plan,
    find_repairable_validation_blockers,
    load_cut_plan_validation_repair_requests,
    save_cut_plan_validation_repair_requests,
    update_cut_plan_validation_repair_request,
)

FOLDER_A = "Grand Canyon"


def _make_project(tmp_path: Path) -> Project:
    project_root = tmp_path / "USA"
    (project_root / FOLDER_A).mkdir(parents=True)
    return Project(
        id="validation-repair-project",
        name="Validation Repair Test",
        project_root=str(project_root),
        work_dir=str(project_root / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=[FOLDER_A],
        selected_asset_subdirs=[FOLDER_A],
    )


def _minimal_item(**overrides) -> CutPlanItem:
    defaults = dict(
        cut_item_id="cut_001", source_refs=[CutPlanSourceRef(source_sentence_id="s1", text="Text")],
        source_scope="folder", folder_name=FOLDER_A, text="Ein Satz.", visual_intent="Weiter Blick.",
        timeline_start_sec=0.0, timeline_end_sec=5.0, duration_sec=5.0,
        chosen_asset_id="asset_a", asset_selection_status="PRIMARY_USED",
    )
    defaults.update(overrides)
    return CutPlanItem(**defaults)


def _black_gap_error(cut_item_id: str, gap_start_sec: float, gap_end_sec: float, **overrides) -> CutPlanValidationError:
    defaults = dict(
        type=CUT_PLAN_ERROR_BLACK_GAP_DURING_VOICEOVER,
        severity="BLOCKER",
        scope="sentence",
        cut_item_id=cut_item_id,
        folder_name=FOLDER_A,
        message=f"{cut_item_id}: visuelles Loch ({gap_start_sec:.2f}s-{gap_end_sec:.2f}s).",
        gap_start_sec=gap_start_sec,
        gap_end_sec=gap_end_sec,
    )
    defaults.update(overrides)
    return CutPlanValidationError(**defaults)


def _reuse_distance_error(cut_item_id: str, **overrides) -> CutPlanValidationError:
    defaults = dict(
        type=CUT_PLAN_ERROR_ASSET_REUSE_DISTANCE_TOO_SHORT,
        severity="BLOCKER",
        scope="sentence",
        cut_item_id=cut_item_id,
        folder_name=FOLDER_A,
        message=f"Asset 'asset_a' zu früh wiederverwendet (cut_item_id={cut_item_id}).",
    )
    defaults.update(overrides)
    return CutPlanValidationError(**defaults)


# --- find_repairable_validation_blockers ---


def test_find_repairable_blockers_includes_black_gap_and_reuse_distance() -> None:
    black_gap = _black_gap_error("cut_001", 5.0, 5.6)
    reuse_distance = _reuse_distance_error("cut_002")
    cut_plan = CutPlanDocument(project_id="p1", blockers=[black_gap, reuse_distance])

    result = find_repairable_validation_blockers(cut_plan)
    assert result == [black_gap, reuse_distance]


def test_find_repairable_blockers_excludes_other_error_types() -> None:
    other = CutPlanValidationError(
        type=CUT_PLAN_ERROR_SHOT_TOO_SHORT, severity="BLOCKER", scope="sentence", cut_item_id="cut_003"
    )
    cut_plan = CutPlanDocument(project_id="p1", blockers=[other])

    assert find_repairable_validation_blockers(cut_plan) == []


def test_find_repairable_blockers_excludes_unattributed_black_gap() -> None:
    """Ein BLACK_GAP-Blocker ohne cut_item_id (kein verantwortliches Item
    gefunden) kann nicht repariert werden — es gibt kein Item, dessen
    Nachbarsegmente angepasst werden könnten."""
    unattributed = _black_gap_error("", 5.0, 5.6)
    cut_plan = CutPlanDocument(project_id="p1", blockers=[unattributed])

    assert find_repairable_validation_blockers(cut_plan) == []


# --- build_validation_repair_requests_from_cut_plan ---


def test_build_creates_black_gap_repair_request_with_gap_bounds(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    item = _minimal_item(cut_item_id="cut_001")
    black_gap = _black_gap_error("cut_001", 5.0, 5.6)
    cut_plan = CutPlanDocument(project_id=project.id, items=[item], blockers=[black_gap])

    document = build_validation_repair_requests_from_cut_plan(project, cut_plan)
    assert len(document.requests) == 1
    request = document.requests[0]
    assert request.repair_type == CUT_PLAN_VALIDATION_REPAIR_TYPE_BLACK_GAP
    assert request.cut_item_id == "cut_001"
    assert request.gap_start_sec == 5.0
    assert request.gap_end_sec == 5.6
    assert request.needed_duration_sec == pytest.approx(0.6)
    assert request.status == CUT_PLAN_VALIDATION_REPAIR_STATUS_PENDING
    assert request.folder_name == FOLDER_A
    assert request.text == "Ein Satz."
    assert request.visual_intent == "Weiter Blick."


def test_build_merges_multiple_black_gap_blockers_for_same_item(tmp_path: Path) -> None:
    """Zwei getrennte BLACK_GAP-Blocker für DASSELBE Item werden zu EINEM
    Request mit dem umfassenden Zeitfenster zusammengeführt."""
    project = _make_project(tmp_path)
    item = _minimal_item(cut_item_id="cut_001")
    gap_1 = _black_gap_error("cut_001", 5.0, 5.4)
    gap_2 = _black_gap_error("cut_001", 6.0, 6.8)
    cut_plan = CutPlanDocument(project_id=project.id, items=[item], blockers=[gap_1, gap_2])

    document = build_validation_repair_requests_from_cut_plan(project, cut_plan)
    assert len(document.requests) == 1
    request = document.requests[0]
    assert request.gap_start_sec == 5.0
    assert request.gap_end_sec == 6.8


def test_build_creates_separate_requests_for_different_items(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    item_1 = _minimal_item(cut_item_id="cut_001")
    item_2 = _minimal_item(cut_item_id="cut_002")
    gap_1 = _black_gap_error("cut_001", 5.0, 5.4)
    gap_2 = _black_gap_error("cut_002", 20.0, 20.5)
    cut_plan = CutPlanDocument(project_id=project.id, items=[item_1, item_2], blockers=[gap_1, gap_2])

    document = build_validation_repair_requests_from_cut_plan(project, cut_plan)
    assert {request.cut_item_id for request in document.requests} == {"cut_001", "cut_002"}


def test_build_creates_asset_reuse_distance_repair_request(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    item = _minimal_item(cut_item_id="cut_002", duration_sec=4.5)
    reuse_distance = _reuse_distance_error("cut_002")
    cut_plan = CutPlanDocument(project_id=project.id, items=[item], blockers=[reuse_distance])

    document = build_validation_repair_requests_from_cut_plan(project, cut_plan)
    assert len(document.requests) == 1
    request = document.requests[0]
    assert request.repair_type == CUT_PLAN_VALIDATION_REPAIR_TYPE_ASSET_REUSE_DISTANCE
    assert request.needed_duration_sec == 4.5
    assert request.gap_start_sec == 0.0
    assert request.gap_end_sec == 0.0


def test_build_skips_black_gap_without_valid_gap_bounds(tmp_path: Path) -> None:
    """Bugfix (Nutzervorgabe Juli 2026, "gap 0.00s-0.00s"): ein BLACK_GAP-
    Blocker OHNE eine einzige verwertbare Gap-Zeit (z. B. weil er aus
    einem VERALTETEN Validierungslauf/aggregate_item_level_errors stammt,
    das nur type=BLACK_GAP_DURING_VOICEOVER ohne gap_start_sec/gap_end_sec
    trägt) darf KEINEN Request mit gap 0.00s-0.00s erzeugen — sonst würde
    eine leere/falsche Reparatur vorgetäuscht."""
    project = _make_project(tmp_path)
    item = _minimal_item(cut_item_id="cut_001")
    stale_black_gap = _black_gap_error("cut_001", 0.0, 0.0)  # gap_end_sec == gap_start_sec -> kein echter Gap
    cut_plan = CutPlanDocument(project_id=project.id, items=[item], blockers=[stale_black_gap])

    document = build_validation_repair_requests_from_cut_plan(project, cut_plan)
    assert document.requests == []


def test_build_creates_black_gap_request_if_any_error_in_group_has_valid_bounds(tmp_path: Path) -> None:
    """Ist innerhalb derselben Gruppe (mehrere BLACK_GAP-Blocker für
    dasselbe Item) mindestens EIN Fehler mit echten Gap-Zeiten vorhanden,
    wird trotzdem ein Request gebaut (nur der stale Eintrag ohne Zeiten
    wird bei der min/max-Berechnung ignoriert)."""
    project = _make_project(tmp_path)
    item = _minimal_item(cut_item_id="cut_001")
    stale = _black_gap_error("cut_001", 0.0, 0.0)
    real = _black_gap_error("cut_001", 5.0, 5.6)
    cut_plan = CutPlanDocument(project_id=project.id, items=[item], blockers=[stale, real])

    document = build_validation_repair_requests_from_cut_plan(project, cut_plan)
    assert len(document.requests) == 1
    assert document.requests[0].gap_start_sec == pytest.approx(5.0)
    assert document.requests[0].gap_end_sec == pytest.approx(5.6)


# --- count_black_gap_items_without_gap_bounds ---


def test_count_black_gap_items_without_gap_bounds_counts_stale_only(tmp_path: Path) -> None:
    from otio_app.services.voiceover_generation.cut_plan_validation_repair import (
        count_black_gap_items_without_gap_bounds,
    )

    project = _make_project(tmp_path)
    item_stale = _minimal_item(cut_item_id="cut_001")
    item_real = _minimal_item(cut_item_id="cut_002")
    stale = _black_gap_error("cut_001", 0.0, 0.0)
    real = _black_gap_error("cut_002", 5.0, 5.6)
    cut_plan = CutPlanDocument(project_id=project.id, items=[item_stale, item_real], blockers=[stale, real])

    assert count_black_gap_items_without_gap_bounds(cut_plan) == 1


def test_count_black_gap_items_without_gap_bounds_is_zero_when_none_stale(tmp_path: Path) -> None:
    from otio_app.services.voiceover_generation.cut_plan_validation_repair import (
        count_black_gap_items_without_gap_bounds,
    )

    project = _make_project(tmp_path)
    item = _minimal_item(cut_item_id="cut_001")
    real = _black_gap_error("cut_001", 5.0, 5.6)
    cut_plan = CutPlanDocument(project_id=project.id, items=[item], blockers=[real])

    assert count_black_gap_items_without_gap_bounds(cut_plan) == 0


def test_build_skips_blocker_for_item_no_longer_in_draft(tmp_path: Path) -> None:
    """Ein Blocker, dessen Item nicht (mehr) im aktuellen Draft existiert
    (veraltete Requests-Datei vs. neuer Draft), wird ignoriert statt einen
    kaputten Request zu erzeugen."""
    project = _make_project(tmp_path)
    black_gap = _black_gap_error("cut_ghost", 5.0, 5.4)
    cut_plan = CutPlanDocument(project_id=project.id, items=[], blockers=[black_gap])

    document = build_validation_repair_requests_from_cut_plan(project, cut_plan)
    assert document.requests == []


def test_build_sets_source_cut_plan_hash(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    item = _minimal_item(cut_item_id="cut_001")
    black_gap = _black_gap_error("cut_001", 5.0, 5.4)
    cut_plan = CutPlanDocument(project_id=project.id, items=[item], blockers=[black_gap])

    document = build_validation_repair_requests_from_cut_plan(project, cut_plan)
    assert document.source_cut_plan_hash != ""


# --- Persistenz ---


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    item = _minimal_item(cut_item_id="cut_001")
    black_gap = _black_gap_error("cut_001", 5.0, 5.4)
    cut_plan = CutPlanDocument(project_id=project.id, items=[item], blockers=[black_gap])
    document = build_validation_repair_requests_from_cut_plan(project, cut_plan)

    save_cut_plan_validation_repair_requests(project, document)
    reloaded = load_cut_plan_validation_repair_requests(project)

    assert reloaded is not None
    assert len(reloaded.requests) == 1
    assert reloaded.requests[0].repair_id == document.requests[0].repair_id


def test_load_returns_none_when_missing(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    assert load_cut_plan_validation_repair_requests(project) is None


def test_update_repair_request_changes_only_target(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    item_1 = _minimal_item(cut_item_id="cut_001")
    item_2 = _minimal_item(cut_item_id="cut_002")
    gap_1 = _black_gap_error("cut_001", 5.0, 5.4)
    gap_2 = _black_gap_error("cut_002", 20.0, 20.5)
    cut_plan = CutPlanDocument(project_id=project.id, items=[item_1, item_2], blockers=[gap_1, gap_2])
    document = build_validation_repair_requests_from_cut_plan(project, cut_plan)
    save_cut_plan_validation_repair_requests(project, document)

    target_id = next(r.repair_id for r in document.requests if r.cut_item_id == "cut_001")
    updated = update_cut_plan_validation_repair_request(project, target_id, status="ACCEPTED", accepted_asset_id="x")

    assert updated is not None
    assert updated.status == "ACCEPTED"
    assert updated.accepted_asset_id == "x"

    reloaded = load_cut_plan_validation_repair_requests(project)
    changed = next(r for r in reloaded.requests if r.repair_id == target_id)
    unchanged = next(r for r in reloaded.requests if r.cut_item_id == "cut_002")
    assert changed.status == "ACCEPTED"
    assert unchanged.status == CUT_PLAN_VALIDATION_REPAIR_STATUS_PENDING
