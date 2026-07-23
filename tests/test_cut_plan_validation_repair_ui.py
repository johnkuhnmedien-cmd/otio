"""Validation Repair Phase 8: eigenständige UI unterhalb der bestehenden
Supplement Requests — Erzeugen/Neu-Erzeugen der Requests, Einzel- und
Sammel-Reparatur-Buttons, Status-/Hinweistexte."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from otio_app.defaults import CUT_PLAN_ERROR_BLACK_GAP_DURING_VOICEOVER
from otio_app.models import Project, ProjectMode
from otio_app.services.voiceover_generation.cut_plan_builder import save_cut_plan_draft
from otio_app.services.voiceover_generation.cut_plan_models import (
    CutPlanAudioItem,
    CutPlanDocument,
    CutPlanItem,
    CutPlanSourceRef,
    CutPlanValidationError,
    VisualSegment,
)
from otio_app.services.voiceover_generation.cut_plan_validation_repair import (
    save_cut_plan_validation_repair_requests,
)
from otio_app.services.voiceover_generation.cut_plan_validation_repair_models import (
    CutPlanValidationRepairRequest,
    CutPlanValidationRepairRequestsDocument,
)
from otio_app.services.voiceover_generation.cut_plan_validation_repair_resolve_service import (
    CutPlanValidationRepairResult,
)
from otio_app.services.voiceover_generation.cut_plan_supplement_models import (
    CutPlanSupplementAutoResolveAttempt,
)
from otio_app.ui.voiceover_generation.cut_plan_tab import render_cut_plan_page

FOLDER_A = "Grand Canyon"


def _make_project(tmp_path: Path) -> Project:
    project_root = tmp_path / "USA"
    (project_root / FOLDER_A).mkdir(parents=True)
    return Project(
        id="validation-repair-ui-project",
        name="Validation Repair UI Test",
        project_root=str(project_root),
        work_dir=str(project_root / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=[FOLDER_A],
        selected_asset_subdirs=[FOLDER_A],
    )


def _patch_project_selector(project: Project, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("otio_app.ui.project_context.list_projects", lambda: [project])
    monkeypatch.setattr(
        "otio_app.ui.project_context.get_project_by_id",
        lambda project_id: project if project_id == project.id else None,
    )
    monkeypatch.setattr("streamlit.session_state", {"active_project_id": project.id}, raising=False)


def _item(**overrides) -> CutPlanItem:
    defaults = dict(
        cut_item_id="cut_gap", source_refs=[CutPlanSourceRef(source_sentence_id="s1", text="Text")],
        source_scope="folder", folder_name=FOLDER_A, text="Ein Satz.", visual_intent="Weiter Blick.",
        timeline_start_sec=0.0, timeline_end_sec=5.0, duration_sec=5.0,
        chosen_asset_id="", asset_selection_status="SUPPLEMENT_REQUIRED",
    )
    defaults.update(overrides)
    return CutPlanItem(**defaults)


def _project_with_draft_and_repair_request(tmp_path: Path, *, repair_status: str = "PENDING") -> tuple[Project, str]:
    project = _make_project(tmp_path)
    item = _item()
    cut_plan = CutPlanDocument(project_id=project.id, items=[item])
    save_cut_plan_draft(project, cut_plan)

    repair_request = CutPlanValidationRepairRequest(
        repair_id="repair_black_gap_cut_gap", repair_type="BLACK_GAP", cut_item_id="cut_gap",
        source_scope="folder", folder_name=FOLDER_A, text="Ein Satz.", visual_intent="Weiter Blick.",
        gap_start_sec=5.0, gap_end_sec=5.4, needed_duration_sec=2.0, status=repair_status,
    )
    save_cut_plan_validation_repair_requests(
        project, CutPlanValidationRepairRequestsDocument(project_id=project.id, requests=[repair_request])
    )
    return project, repair_request.repair_id


def test_ui_offers_build_button_when_no_requests_exist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _make_project(tmp_path)
    cut_plan = CutPlanDocument(project_id=project.id, items=[_item()])
    save_cut_plan_draft(project, cut_plan)

    _patch_project_selector(project, monkeypatch)
    monkeypatch.setattr("streamlit.button", lambda *a, **k: False)
    monkeypatch.setattr("streamlit.rerun", lambda: None)

    render_cut_plan_page()  # darf nicht werfen; "Validation Repair Requests aus Cut Plan erzeugen" wird angeboten


def test_build_button_creates_requests_document(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _make_project(tmp_path)
    black_gap_item = _item(cut_item_id="cut_gap", timeline_start_sec=5.0, timeline_end_sec=5.4, duration_sec=0.4)
    other_item = _item(
        cut_item_id="cut_ok", timeline_start_sec=0.0, timeline_end_sec=5.0, duration_sec=5.0,
        asset_selection_status="PRIMARY_USED", chosen_asset_id="asset_a",
        planned_visual_segments=[
            VisualSegment(segment_id="seg_ok", timeline_in_sec=0.0, timeline_out_sec=5.0, duration_sec=5.0,
                          asset_id="asset_a", asset_path="/fake/a.jpg", asset_type="image", source_in_sec=0.0,
                          source_out_sec=5.0, track="V1")
        ],
    )
    from otio_app.services.voiceover_generation.cut_plan_models import CutPlanValidationError

    cut_plan = CutPlanDocument(
        project_id=project.id,
        items=[other_item, black_gap_item],
        blockers=[
            CutPlanValidationError(
                type="BLACK_GAP_DURING_VOICEOVER", severity="BLOCKER", scope="sentence",
                cut_item_id="cut_gap", folder_name=FOLDER_A, gap_start_sec=5.0, gap_end_sec=5.4,
            )
        ],
    )
    save_cut_plan_draft(project, cut_plan)

    build_key = f"cut_plan_validation_repair_build_{project.id}"
    _patch_project_selector(project, monkeypatch)
    monkeypatch.setattr("streamlit.button", lambda *a, **k: k.get("key") == build_key)
    monkeypatch.setattr("streamlit.rerun", lambda: None)
    successes: list[str] = []
    monkeypatch.setattr("streamlit.success", lambda msg, **k: successes.append(msg))

    render_cut_plan_page()

    assert any("Validation Repair Request(s) erzeugt" in msg for msg in successes)

    from otio_app.services.voiceover_generation.cut_plan_validation_repair import (
        load_cut_plan_validation_repair_requests,
    )

    reloaded = load_cut_plan_validation_repair_requests(project)
    assert reloaded is not None
    assert len(reloaded.requests) == 1
    assert reloaded.requests[0].cut_item_id == "cut_gap"


def test_ui_breaks_down_black_gaps_by_gap_kind_for_full_item_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kein Validation Repair Request (Blocker ohne verwertbare Gap-Zeit),
    aber die neue Direkt-aus-Draft-Diagnose erkennt trotzdem ein Item ganz
    ohne Asset und empfiehlt Supplement Requests."""
    project = _make_project(tmp_path)
    missing_item = _item(cut_item_id="cut_missing", timeline_start_sec=0.0, timeline_end_sec=5.0, duration_sec=5.0)
    cut_plan = CutPlanDocument(
        project_id=project.id,
        items=[missing_item],
        audio_items=[
            CutPlanAudioItem(scope="folder", folder_name=FOLDER_A, timeline_start_sec=0.0, timeline_end_sec=5.0, duration_sec=5.0)
        ],
        blockers=[
            CutPlanValidationError(
                type=CUT_PLAN_ERROR_BLACK_GAP_DURING_VOICEOVER, severity="BLOCKER", scope="sentence",
                cut_item_id="cut_missing", folder_name=FOLDER_A,
                # Stale Blocker ohne verwertbare Gap-Zeit -> kein Validation Repair Request.
                gap_start_sec=0.0, gap_end_sec=0.0,
            )
        ],
    )
    save_cut_plan_draft(project, cut_plan)
    from otio_app.services.voiceover_generation.cut_plan_validation_repair_models import (
        CutPlanValidationRepairRequestsDocument,
    )

    save_cut_plan_validation_repair_requests(
        project, CutPlanValidationRepairRequestsDocument(project_id=project.id, requests=[])
    )

    _patch_project_selector(project, monkeypatch)
    monkeypatch.setattr("streamlit.button", lambda *a, **k: False)
    monkeypatch.setattr("streamlit.rerun", lambda: None)
    warnings: list[str] = []
    captions: list[str] = []
    monkeypatch.setattr("streamlit.warning", lambda msg, **k: warnings.append(msg))
    monkeypatch.setattr("streamlit.caption", lambda msg, **k: captions.append(msg))

    render_cut_plan_page()

    assert any("1 Item(s) ohne jedes Asset" in msg for msg in warnings)
    assert any("Supplement Requests" in msg for msg in captions)


def test_ui_breaks_down_black_gaps_by_gap_kind_for_residual_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kein Validation Repair Request, aber die Diagnose erkennt eine
    Rest-Lücke bei einem bereits versorgten Item und empfiehlt Residual
    Gap Requests."""
    project = _make_project(tmp_path)
    item = _item(
        cut_item_id="cut_1", timeline_start_sec=0.0, timeline_end_sec=5.0, duration_sec=5.0,
        asset_selection_status="SUPPLEMENT_USED", chosen_asset_id="supplement_pexels_1",
        planned_visual_segments=[
            VisualSegment(
                segment_id="seg_1", timeline_in_sec=0.0, timeline_out_sec=5.0, duration_sec=5.0,
                asset_id="supplement_pexels_1", asset_path="/fake/a.jpg", asset_type="image",
                source_in_sec=0.0, source_out_sec=5.0, track="V1",
            )
        ],
    )
    next_item = _item(cut_item_id="cut_2", timeline_start_sec=20.0, timeline_end_sec=25.0, duration_sec=5.0)
    cut_plan = CutPlanDocument(
        project_id=project.id,
        items=[item, next_item],
        audio_items=[
            CutPlanAudioItem(scope="folder", folder_name=FOLDER_A, timeline_start_sec=0.0, timeline_end_sec=5.0, duration_sec=5.0),
            CutPlanAudioItem(scope="folder", folder_name=FOLDER_A, timeline_start_sec=20.0, timeline_end_sec=25.0, duration_sec=5.0),
        ],
        settings_snapshot={
            "extend_visual_window_to_next_sentence": True, "max_sentence_pause_extension_sec": 15.0,
            "shot_max_sec": 10.0,
        },
        blockers=[
            CutPlanValidationError(
                type=CUT_PLAN_ERROR_BLACK_GAP_DURING_VOICEOVER, severity="BLOCKER", scope="sentence",
                cut_item_id="cut_1", folder_name=FOLDER_A, gap_start_sec=0.0, gap_end_sec=0.0,
            )
        ],
    )
    save_cut_plan_draft(project, cut_plan)
    from otio_app.services.voiceover_generation.cut_plan_validation_repair_models import (
        CutPlanValidationRepairRequestsDocument,
    )

    save_cut_plan_validation_repair_requests(
        project, CutPlanValidationRepairRequestsDocument(project_id=project.id, requests=[])
    )

    _patch_project_selector(project, monkeypatch)
    monkeypatch.setattr("streamlit.button", lambda *a, **k: False)
    monkeypatch.setattr("streamlit.rerun", lambda: None)
    warnings: list[str] = []
    captions: list[str] = []
    monkeypatch.setattr("streamlit.warning", lambda msg, **k: warnings.append(msg))
    monkeypatch.setattr("streamlit.caption", lambda msg, **k: captions.append(msg))

    render_cut_plan_page()

    assert any("1 Rest-Lücke(n)" in msg for msg in warnings)
    assert any("Residual Gap Repair" in msg for msg in captions)


def test_ui_shows_requests_table_and_expander(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, repair_id = _project_with_draft_and_repair_request(tmp_path)

    _patch_project_selector(project, monkeypatch)
    monkeypatch.setattr("streamlit.button", lambda *a, **k: False)
    monkeypatch.setattr("streamlit.rerun", lambda: None)

    render_cut_plan_page()  # darf nicht werfen; Tabelle + Expander werden gerendert


def test_resolve_button_calls_service_and_shows_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, repair_id = _project_with_draft_and_repair_request(tmp_path)
    resolve_key = f"cut_plan_validation_repair_resolve_{project.id}_{repair_id}"

    fake_result = CutPlanValidationRepairResult(
        status="ACCEPTED", repair_id=repair_id, accepted_candidate_id="cand_1", accepted_asset_id="supplement_pexels_1",
        attempts=[
            CutPlanSupplementAutoResolveAttempt(
                candidate_id="cand_1", provider="pexels", asset_type="image", validation_status="PASS",
                validation_score=0.9, validation_reason="Passt.",
            )
        ],
    )

    with patch(
        "otio_app.ui.voiceover_generation.cut_plan_tab.auto_resolve_validation_repair_request",
        return_value=fake_result,
    ) as mock_resolve:
        _patch_project_selector(project, monkeypatch)
        monkeypatch.setattr("streamlit.button", lambda *a, **k: k.get("key") == resolve_key)
        monkeypatch.setattr("streamlit.rerun", lambda: None)
        successes: list[str] = []
        monkeypatch.setattr("streamlit.success", lambda msg, **k: successes.append(msg))

        render_cut_plan_page()

    mock_resolve.assert_called_once_with(project, repair_id)
    assert any("Reparatur erfolgreich" in msg for msg in successes)


def test_resolve_button_shows_unsafe_to_repair_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, repair_id = _project_with_draft_and_repair_request(tmp_path)
    resolve_key = f"cut_plan_validation_repair_resolve_{project.id}_{repair_id}"

    fake_result = CutPlanValidationRepairResult(status="UNSAFE_TO_REPAIR", repair_id=repair_id)

    with patch(
        "otio_app.ui.voiceover_generation.cut_plan_tab.auto_resolve_validation_repair_request",
        return_value=fake_result,
    ):
        _patch_project_selector(project, monkeypatch)
        monkeypatch.setattr("streamlit.button", lambda *a, **k: k.get("key") == resolve_key)
        monkeypatch.setattr("streamlit.rerun", lambda: None)
        warnings: list[str] = []
        monkeypatch.setattr("streamlit.warning", lambda msg, **k: warnings.append(msg))

        render_cut_plan_page()

    assert any("Nicht sicher reparierbar" in msg for msg in warnings)


def test_ui_shows_unsafe_to_repair_hint_for_persisted_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, _ = _project_with_draft_and_repair_request(tmp_path, repair_status="UNSAFE_TO_REPAIR")

    _patch_project_selector(project, monkeypatch)
    monkeypatch.setattr("streamlit.button", lambda *a, **k: False)
    monkeypatch.setattr("streamlit.rerun", lambda: None)
    warnings: list[str] = []
    monkeypatch.setattr("streamlit.warning", lambda msg, **k: warnings.append(msg))

    render_cut_plan_page()

    assert any("nicht genug Spielraum" in msg for msg in warnings)


def test_bulk_resolve_button_calls_service_and_shows_summary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, repair_id = _project_with_draft_and_repair_request(tmp_path)
    bulk_key = f"cut_plan_validation_repair_auto_resolve_all_{project.id}"

    fake_results = [
        CutPlanValidationRepairResult(status="ACCEPTED", repair_id=repair_id),
    ]

    with patch(
        "otio_app.ui.voiceover_generation.cut_plan_tab.auto_resolve_all_validation_repair_requests",
        return_value=fake_results,
    ) as mock_batch:
        _patch_project_selector(project, monkeypatch)
        monkeypatch.setattr("streamlit.button", lambda *a, **k: k.get("key") == bulk_key)
        monkeypatch.setattr("streamlit.rerun", lambda: None)
        successes: list[str] = []
        monkeypatch.setattr("streamlit.success", lambda msg, **k: successes.append(msg))

        render_cut_plan_page()

    mock_batch.assert_called_once_with(project)
    assert any("1 Request(s) bearbeitet" in msg for msg in successes)
    assert any("1 automatisch repariert" in msg for msg in successes)


def test_bulk_resolve_button_disabled_when_all_accepted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, _ = _project_with_draft_and_repair_request(tmp_path, repair_status="ACCEPTED")
    bulk_key = f"cut_plan_validation_repair_auto_resolve_all_{project.id}"

    captured_disabled: list[bool] = []

    def _fake_button(label, *args, **kwargs):
        if kwargs.get("key") == bulk_key:
            captured_disabled.append(kwargs.get("disabled", False))
        return False

    _patch_project_selector(project, monkeypatch)
    monkeypatch.setattr("streamlit.button", _fake_button)
    monkeypatch.setattr("streamlit.rerun", lambda: None)

    render_cut_plan_page()

    assert captured_disabled == [True]


def test_stale_requests_show_warning_after_draft_changed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, _ = _project_with_draft_and_repair_request(tmp_path)
    # Draft ändern, ohne die Requests neu zu erzeugen -> source_cut_plan_hash veraltet.
    cut_plan = CutPlanDocument(project_id=project.id, items=[_item(text="Ein geänderter Satz.")])
    save_cut_plan_draft(project, cut_plan)

    _patch_project_selector(project, monkeypatch)
    monkeypatch.setattr("streamlit.button", lambda *a, **k: False)
    monkeypatch.setattr("streamlit.rerun", lambda: None)
    warnings: list[str] = []
    monkeypatch.setattr("streamlit.warning", lambda msg, **k: warnings.append(msg))

    render_cut_plan_page()

    assert any("älteren Version des Cut Plans" in msg for msg in warnings)
