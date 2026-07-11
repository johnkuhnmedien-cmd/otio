"""Phase 8.7: Cut Plan Confirm + Trace (+ Vorab-Hardening Supplement Accept).

Noch KEIN EditPlanDocument, kein OTIO-Export, keine Phase-9-Übersetzung,
kein locked EditPlan, kein LLM-Konfliktlöser."""

from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis
from otio_app.defaults import (
    CUT_PLAN_ASSET_SELECTION_BACKUP_USED,
    CUT_PLAN_ASSET_SELECTION_SUPPLEMENT_USED,
    CUT_PLAN_ASSET_SELECTION_UNRESOLVED,
    CUT_PLAN_STATUS_CONFIRMED,
    CUT_PLAN_STATUS_VALIDATED,
    CUT_PLAN_VALIDATION_STATUS_BLOCKED,
    CUT_PLAN_VALIDATION_STATUS_PASS,
    CUT_PLAN_VALIDATION_STATUS_WARNING,
)
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import (
    get_cut_plan_confirmed_path,
    get_cut_plan_trace_path,
    get_edit_plan_dir,
    get_exports_dir,
    get_folder_inventory_path,
    get_supplement_dir,
)
from otio_app.services.voiceover_generation.cut_plan_builder import (
    apply_asset_selection_to_draft,
    build_cut_plan_draft,
    load_cut_plan_draft,
    save_cut_plan_draft,
    validate_cut_plan_draft,
)
from otio_app.services.voiceover_generation.cut_plan_confirm_service import (
    can_confirm_cut_plan,
    confirm_cut_plan,
    is_confirmed_cut_plan_stale,
    load_confirmed_cut_plan,
    unconfirm_cut_plan,
)
from otio_app.services.voiceover_generation.cut_plan_models import (
    CutPlanDocument,
    CutPlanItem,
    CutPlanSettings,
    CutPlanSourceRef,
    CutPlanValidationReport,
)
from otio_app.services.voiceover_generation.cut_plan_settings_service import save_cut_plan_settings
from otio_app.services.voiceover_generation.cut_plan_supplement_bridge import save_cut_plan_supplement_requests
from otio_app.services.voiceover_generation.cut_plan_supplement_models import (
    CutPlanSupplementRequest,
    CutPlanSupplementRequestsDocument,
)
from otio_app.services.voiceover_generation.cut_plan_trace_service import build_cut_plan_trace
from otio_app.services.voiceover_generation.final_plan_service import (
    save_confirmed_voiceover_project_plan,
)
from otio_app.services.voiceover_generation.models import (
    AlignmentItem,
    ConfirmedFolderPlanItem,
    ConfirmedIntroPlanItem,
    ConfirmedVoiceoverProjectPlan,
    IntroHookVisualBeat,
    SentenceItem,
)
from otio_app.ui.voiceover_generation.cut_plan_tab import render_cut_plan_page

FOLDER_A = "Grand Canyon"


def _make_project(tmp_path: Path) -> Project:
    project_root = tmp_path / "USA"
    (project_root / FOLDER_A).mkdir(parents=True)
    return Project(
        id="cut-plan-confirm-project",
        name="Cut Plan Confirm Test",
        project_root=str(project_root),
        work_dir=str(project_root / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=[FOLDER_A],
        selected_asset_subdirs=[FOLDER_A],
    )


def _write_inventory(project: Project, filenames: list[str]) -> None:
    entries = []
    for filename in filenames:
        (project.project_root_path / FOLDER_A / filename).write_bytes(b"FAKE_MEDIA_BYTES")
        entries.append(AssetMediaAnalysis(path=f"{FOLDER_A}/{filename}", description=filename))
    inv_path = get_folder_inventory_path(project.work_dir_path, FOLDER_A)
    inv_path.parent.mkdir(parents=True, exist_ok=True)
    inv_path.write_text(
        AssetFolderAnalysis(folder=FOLDER_A, assets=entries).model_dump_json(indent=2), encoding="utf-8"
    )


def _write_audio(project: Project, name: str) -> Path:
    audio_dir = project.work_dir_path / "voiceover_generation" / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    path = audio_dir / name
    path.write_bytes(b"FAKE_AUDIO_BYTES")
    return path


def _build_validated_project(tmp_path: Path) -> Project:
    """Ein vollständiges Projekt mit Intro + einem Folder, Standard-Settings,
    das nach Draft-Bau + Asset-Auswahl + Validierung VALIDATED/WARNING
    erreicht (Frame-Rounding-Warnungen bei Standard-Settings bleiben, siehe
    Phase 8.5 — das ist bewusst kein reines PASS)."""
    project = _make_project(tmp_path)
    _write_inventory(project, ["photo_a.jpg", "photo_b.jpg"])
    intro_audio = _write_audio(project, "intro.mp3")
    folder_audio = _write_audio(project, "folder.mp3")

    intro = ConfirmedIntroPlanItem(
        hook_text="Ein Ort voller Geheimnisse.", audio_path=str(intro_audio), audio_duration_sec=5.0,
        visual_beats=[IntroHookVisualBeat(hook_beat_id="hook_beat_001", text="x", primary_asset_id="asset_photo_a")],
        alignment_items=[
            AlignmentItem(sentence_id="hook_beat_001", audio_start_sec=0.0, audio_end_sec=5.0, duration_sec=5.0)
        ],
    )
    folder = ConfirmedFolderPlanItem(
        folder_name=FOLDER_A, order_index=1, audio_path=str(folder_audio), audio_duration_sec=5.0,
        sentence_items=[SentenceItem(sentence_id="sentence_001", text="Ein Satz.", primary_asset_id="asset_photo_b")],
        alignment_items=[
            AlignmentItem(sentence_id="sentence_001", audio_start_sec=0.0, audio_end_sec=5.0, duration_sec=5.0)
        ],
    )
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id, project_title="Test", status="AUDIO_READY", intro=intro, folders=[folder]
    )
    save_confirmed_voiceover_project_plan(project, plan)
    save_cut_plan_settings(project, CutPlanSettings(project_id=project.id))
    draft = build_cut_plan_draft(project)
    save_cut_plan_draft(project, draft)
    apply_asset_selection_to_draft(project)
    validate_cut_plan_draft(project)
    return project


def _patch_project_selector(project: Project, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("otio_app.ui.project_context.list_projects", lambda: [project])
    monkeypatch.setattr(
        "otio_app.ui.project_context.get_project_by_id",
        lambda project_id: project if project_id == project.id else None,
    )
    monkeypatch.setattr("streamlit.session_state", {"active_project_id": project.id}, raising=False)


def _minimal_cut_plan(project: Project, **overrides) -> CutPlanDocument:
    defaults = dict(project_id=project.id, timeline_fps=25)
    defaults.update(overrides)
    return CutPlanDocument(**defaults)


def _minimal_item(**overrides) -> CutPlanItem:
    defaults = dict(
        cut_item_id="cut_001", source_refs=[CutPlanSourceRef(source_sentence_id="s1", text="Text", folder_name=FOLDER_A)],
        source_scope="folder", folder_name=FOLDER_A, text="Ein Satz.", timeline_start_sec=1.0,
        timeline_end_sec=6.0, duration_sec=5.0, audio_start_sec=0.0, audio_end_sec=5.0,
        chosen_asset_id="asset_a", asset_selection_status="PRIMARY_USED",
        primary_asset_id="asset_a",
    )
    defaults.update(overrides)
    return CutPlanItem(**defaults)


def _minimal_report(**overrides) -> CutPlanValidationReport:
    defaults = dict(project_id="p1", status=CUT_PLAN_VALIDATION_STATUS_PASS)
    defaults.update(overrides)
    return CutPlanValidationReport(**defaults)


# --- 1-10: can_confirm_cut_plan ---


def test_cannot_confirm_without_draft() -> None:
    eligible, reasons = can_confirm_cut_plan(MagicMock(), None, _minimal_report())
    assert eligible is False
    assert any("Kein Cut Plan Draft" in r for r in reasons)


def test_cannot_confirm_without_validation_report(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    cut_plan = _minimal_cut_plan(project, status=CUT_PLAN_STATUS_VALIDATED)
    eligible, reasons = can_confirm_cut_plan(project, cut_plan, None)
    assert eligible is False
    assert any("Validation Report" in r for r in reasons)


def test_cannot_confirm_when_report_blocked(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    cut_plan = _minimal_cut_plan(project, status=CUT_PLAN_STATUS_VALIDATED)
    from otio_app.services.voiceover_generation.cut_plan_validator import content_hash_of_cut_plan_content

    report = _minimal_report(status=CUT_PLAN_VALIDATION_STATUS_BLOCKED, cut_plan_hash=content_hash_of_cut_plan_content(cut_plan))
    eligible, reasons = can_confirm_cut_plan(project, cut_plan, report)
    assert eligible is False
    assert any("BLOCKED" in r for r in reasons)


def test_cannot_confirm_when_report_is_stale(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    cut_plan = _minimal_cut_plan(project, status=CUT_PLAN_STATUS_VALIDATED)
    report = _minimal_report(cut_plan_hash="stale-hash-does-not-match")
    eligible, reasons = can_confirm_cut_plan(project, cut_plan, report)
    assert eligible is False
    assert any("veraltet" in r for r in reasons)


def test_cannot_confirm_when_source_plan_is_stale(tmp_path: Path) -> None:
    project = _build_validated_project(tmp_path)
    draft = load_cut_plan_draft(project)
    from otio_app.services.voiceover_generation.cut_plan_validator import load_cut_plan_validation_report

    report = load_cut_plan_validation_report(project)

    # Bestätigten Voice-over-Plan nachträglich ändern -> Draft wird stale.
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id, project_title="Geändert", status="AUDIO_READY",
        intro=ConfirmedIntroPlanItem(), folders=[],
    )
    save_confirmed_voiceover_project_plan(project, plan)

    eligible, reasons = can_confirm_cut_plan(project, draft, report)
    assert eligible is False
    assert any("Voice-over-Projektplan" in r for r in reasons)


def test_cannot_confirm_when_settings_are_stale(tmp_path: Path) -> None:
    project = _build_validated_project(tmp_path)
    draft = load_cut_plan_draft(project)
    from otio_app.services.voiceover_generation.cut_plan_validator import load_cut_plan_validation_report

    report = load_cut_plan_validation_report(project)

    save_cut_plan_settings(project, CutPlanSettings(project_id=project.id, max_asset_usage=9))

    eligible, reasons = can_confirm_cut_plan(project, draft, report)
    assert eligible is False
    assert any("Cut-Plan-Settings" in r for r in reasons)


def test_cannot_confirm_with_unresolved_item(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    item = _minimal_item(asset_selection_status=CUT_PLAN_ASSET_SELECTION_UNRESOLVED, chosen_asset_id="")
    cut_plan = _minimal_cut_plan(project, status=CUT_PLAN_STATUS_VALIDATED, items=[item])
    from otio_app.services.voiceover_generation.cut_plan_validator import content_hash_of_cut_plan_content

    report = _minimal_report(cut_plan_hash=content_hash_of_cut_plan_content(cut_plan))
    eligible, reasons = can_confirm_cut_plan(project, cut_plan, report)
    assert eligible is False
    assert any("UNRESOLVED" in r for r in reasons)


def test_cannot_confirm_with_supplement_required_item(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    item = _minimal_item(asset_selection_status="SUPPLEMENT_REQUIRED", chosen_asset_id="")
    cut_plan = _minimal_cut_plan(project, status=CUT_PLAN_STATUS_VALIDATED, items=[item])
    from otio_app.services.voiceover_generation.cut_plan_validator import content_hash_of_cut_plan_content

    report = _minimal_report(cut_plan_hash=content_hash_of_cut_plan_content(cut_plan))
    eligible, reasons = can_confirm_cut_plan(project, cut_plan, report)
    assert eligible is False
    assert any("SUPPLEMENT_REQUIRED" in r for r in reasons)


def _matching_settings_snapshot(project: Project) -> dict:
    """Speichert Standard-Settings für project UND liefert den passenden
    settings_snapshot-Wert zurück, damit is_cut_plan_settings_stale in
    isolierten can_confirm_cut_plan-Tests nicht fälschlich anschlägt."""
    settings = CutPlanSettings(project_id=project.id)
    save_cut_plan_settings(project, settings)
    return settings.model_dump(mode="json", exclude={"project_id", "generated_at"})


def test_can_confirm_when_validated_and_pass(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    item = _minimal_item()
    cut_plan = _minimal_cut_plan(
        project, status=CUT_PLAN_STATUS_VALIDATED, items=[item],
        settings_snapshot=_matching_settings_snapshot(project),
    )
    from otio_app.services.voiceover_generation.cut_plan_validator import content_hash_of_cut_plan_content

    report = _minimal_report(status=CUT_PLAN_VALIDATION_STATUS_PASS, cut_plan_hash=content_hash_of_cut_plan_content(cut_plan))
    eligible, reasons = can_confirm_cut_plan(project, cut_plan, report)
    assert eligible is True
    assert reasons == []


def test_can_confirm_when_supplement_request_has_accepted_asset_despite_stale_status(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    item = _minimal_item()
    cut_plan = _minimal_cut_plan(
        project, status=CUT_PLAN_STATUS_VALIDATED, items=[item],
        settings_snapshot=_matching_settings_snapshot(project),
    )
    from otio_app.services.voiceover_generation.cut_plan_validator import content_hash_of_cut_plan_content

    asset_path = tmp_path / "accepted.jpg"
    asset_path.write_bytes(b"img")
    save_cut_plan_supplement_requests(
        project,
        CutPlanSupplementRequestsDocument(
            project_id=project.id,
            source_cut_plan_hash=content_hash_of_cut_plan_content(cut_plan),
            requests=[
                CutPlanSupplementRequest(
                    request_id="cutreq_cut_001",
                    cut_item_id="cut_001",
                    folder_name=FOLDER_A,
                    status="CANDIDATES_FOUND",
                    accepted_asset_id="supplement_pexels_stale",
                    accepted_asset_path=str(asset_path),
                )
            ],
        ),
    )
    report = _minimal_report(
        status=CUT_PLAN_VALIDATION_STATUS_PASS,
        cut_plan_hash=content_hash_of_cut_plan_content(cut_plan),
    )
    eligible, reasons = can_confirm_cut_plan(project, cut_plan, report)
    assert eligible is True
    assert reasons == []


def test_can_confirm_when_validated_and_warning_without_blockers(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    item = _minimal_item()
    cut_plan = _minimal_cut_plan(
        project, status=CUT_PLAN_STATUS_VALIDATED, items=[item],
        settings_snapshot=_matching_settings_snapshot(project),
    )
    from otio_app.services.voiceover_generation.cut_plan_validator import (
        CutPlanValidationError,
        content_hash_of_cut_plan_content,
    )

    report = _minimal_report(
        status=CUT_PLAN_VALIDATION_STATUS_WARNING,
        warnings=[CutPlanValidationError(type="FRAME_ROUNDING_ERROR", severity="WARNING", scope="project")],
        cut_plan_hash=content_hash_of_cut_plan_content(cut_plan),
    )
    eligible, reasons = can_confirm_cut_plan(project, cut_plan, report)
    assert eligible is True


# --- 11-13: confirm_cut_plan schreibt Dateien ---


def test_confirm_writes_confirmed_json(tmp_path: Path) -> None:
    project = _build_validated_project(tmp_path)
    confirm_cut_plan(project)
    path = get_cut_plan_confirmed_path(project.work_dir_path)
    assert path.is_file()


def test_confirmed_plan_status_is_confirmed(tmp_path: Path) -> None:
    project = _build_validated_project(tmp_path)
    confirmed = confirm_cut_plan(project)
    assert confirmed.status == CUT_PLAN_STATUS_CONFIRMED
    assert confirmed.confirmed_at is not None


def test_confirm_writes_trace_json(tmp_path: Path) -> None:
    project = _build_validated_project(tmp_path)
    confirm_cut_plan(project)
    path = get_cut_plan_trace_path(project.work_dir_path)
    assert path.is_file()


# --- 14-21: Trace-Inhalt ---


def test_trace_has_one_entry_per_cut_plan_item(tmp_path: Path) -> None:
    project = _build_validated_project(tmp_path)
    draft = load_cut_plan_draft(project)
    trace = build_cut_plan_trace(project, draft)
    assert len(trace.entries) == len(draft.items)


def test_trace_contains_chosen_asset_id(tmp_path: Path) -> None:
    project = _build_validated_project(tmp_path)
    draft = load_cut_plan_draft(project)
    trace = build_cut_plan_trace(project, draft)
    assert all(entry.chosen_asset_id for entry in trace.entries)


def test_trace_contains_original_primary_and_backup_asset_ids(tmp_path: Path) -> None:
    item = _minimal_item(primary_asset_id="asset_primary", backup_asset_ids=["asset_backup_1", "asset_backup_2"])
    cut_plan = CutPlanDocument(project_id="p1", items=[item])
    project = MagicMock()
    project.id = "p1"

    trace = build_cut_plan_trace(project, cut_plan)
    entry = trace.entries[0]
    assert entry.original_primary_asset_id == "asset_primary"
    assert entry.original_backup_asset_ids == ["asset_backup_1", "asset_backup_2"]


def test_trace_marks_fallback_used_for_backup_used_item() -> None:
    item = _minimal_item(asset_selection_status=CUT_PLAN_ASSET_SELECTION_BACKUP_USED)
    cut_plan = CutPlanDocument(project_id="p1", items=[item])
    project = MagicMock()
    project.id = "p1"

    trace = build_cut_plan_trace(project, cut_plan)
    assert trace.entries[0].fallback_used is True


def test_trace_marks_used_supplement_asset_for_supplement_used_item() -> None:
    item = _minimal_item(asset_selection_status=CUT_PLAN_ASSET_SELECTION_SUPPLEMENT_USED)
    cut_plan = CutPlanDocument(project_id="p1", items=[item])
    project = MagicMock()
    project.id = "p1"

    trace = build_cut_plan_trace(project, cut_plan)
    assert trace.entries[0].used_supplement_asset is True
    assert trace.entries[0].asset_selection_status == CUT_PLAN_ASSET_SELECTION_SUPPLEMENT_USED


def test_trace_contains_duration_strategy() -> None:
    item = _minimal_item(duration_strategy="SPLIT")
    cut_plan = CutPlanDocument(project_id="p1", items=[item])
    project = MagicMock()
    project.id = "p1"

    trace = build_cut_plan_trace(project, cut_plan)
    assert trace.entries[0].duration_strategy == "SPLIT"


def test_trace_contains_visual_segment_count() -> None:
    from otio_app.services.voiceover_generation.cut_plan_models import VisualSegment

    segments = [
        VisualSegment(segment_id="seg_1", timeline_in_sec=0.0, timeline_out_sec=2.0, duration_sec=2.0,
                      asset_id="a", asset_path="/x.jpg", asset_type="image", source_out_sec=2.0),
        VisualSegment(segment_id="seg_2", timeline_in_sec=2.0, timeline_out_sec=4.0, duration_sec=2.0,
                      asset_id="a", asset_path="/x.jpg", asset_type="image", source_out_sec=2.0),
    ]
    item = _minimal_item(planned_visual_segments=segments)
    cut_plan = CutPlanDocument(project_id="p1", items=[item])
    project = MagicMock()
    project.id = "p1"

    trace = build_cut_plan_trace(project, cut_plan)
    assert trace.entries[0].visual_segment_count == 2
    assert trace.entries[0].visual_segment_ids == ["seg_1", "seg_2"]


def test_trace_contains_validation_warnings_and_blockers() -> None:
    item = _minimal_item(warnings=["SHOT_TOO_SHORT"], blockers=["ASSET_TOO_SHORT"])
    cut_plan = CutPlanDocument(project_id="p1", items=[item])
    project = MagicMock()
    project.id = "p1"

    trace = build_cut_plan_trace(project, cut_plan)
    assert trace.entries[0].validation_warnings == ["SHOT_TOO_SHORT"]
    assert trace.entries[0].validation_blockers == ["ASSET_TOO_SHORT"]


# --- 22-24: Confirmed-Schutz ---


def test_new_draft_does_not_overwrite_confirmed(tmp_path: Path) -> None:
    project = _build_validated_project(tmp_path)
    confirm_cut_plan(project)
    confirmed_before = load_confirmed_cut_plan(project)

    build_cut_plan_draft(project)  # Rebuild — schreibt NICHT auf cut_plan.draft.json ohne save

    confirmed_after = load_confirmed_cut_plan(project)
    assert confirmed_after.model_dump(mode="json") == confirmed_before.model_dump(mode="json")


def test_new_asset_selection_does_not_overwrite_confirmed(tmp_path: Path) -> None:
    project = _build_validated_project(tmp_path)
    confirm_cut_plan(project)
    confirmed_before = load_confirmed_cut_plan(project)

    apply_asset_selection_to_draft(project)

    confirmed_after = load_confirmed_cut_plan(project)
    assert confirmed_after.model_dump(mode="json") == confirmed_before.model_dump(mode="json")


def test_new_validation_does_not_overwrite_confirmed(tmp_path: Path) -> None:
    project = _build_validated_project(tmp_path)
    confirm_cut_plan(project)
    confirmed_before = load_confirmed_cut_plan(project)

    validate_cut_plan_draft(project)

    confirmed_after = load_confirmed_cut_plan(project)
    assert confirmed_after.model_dump(mode="json") == confirmed_before.model_dump(mode="json")


# --- 25-26: Confirmed Staleness ---


def test_confirmed_stale_detected_when_source_plan_changed(tmp_path: Path) -> None:
    project = _build_validated_project(tmp_path)
    confirmed = confirm_cut_plan(project)
    assert is_confirmed_cut_plan_stale(project, confirmed) is False

    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id, project_title="Geändert", status="AUDIO_READY",
        intro=ConfirmedIntroPlanItem(), folders=[],
    )
    save_confirmed_voiceover_project_plan(project, plan)

    assert is_confirmed_cut_plan_stale(project, confirmed) is True


def test_confirmed_stale_detected_when_settings_changed(tmp_path: Path) -> None:
    project = _build_validated_project(tmp_path)
    confirmed = confirm_cut_plan(project)
    assert is_confirmed_cut_plan_stale(project, confirmed) is False

    save_cut_plan_settings(project, CutPlanSettings(project_id=project.id, max_asset_usage=7))

    assert is_confirmed_cut_plan_stale(project, confirmed) is True


# --- Unconfirm ---


def test_unconfirm_removes_confirmed_file(tmp_path: Path) -> None:
    project = _build_validated_project(tmp_path)
    confirm_cut_plan(project)
    assert load_confirmed_cut_plan(project) is not None

    unconfirm_cut_plan(project)
    assert load_confirmed_cut_plan(project) is None


# --- 27-28: Vorab-Hardening Supplement Accept (Cross-Check, primär in test_cut_plan_phase8_6.py) ---


def test_accept_cut_plan_supplement_candidate_has_force_replace_parameter() -> None:
    from otio_app.services.voiceover_generation.cut_plan_supplement_bridge import (
        accept_cut_plan_supplement_candidate,
    )

    signature = inspect.signature(accept_cut_plan_supplement_candidate)
    assert "force_replace" in signature.parameters
    assert signature.parameters["force_replace"].default is False


# --- 29-32: UI ---


def test_ui_shows_confirm_eligibility(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _build_validated_project(tmp_path)
    _patch_project_selector(project, monkeypatch)

    writes: list[str] = []
    monkeypatch.setattr("streamlit.button", lambda *a, **k: False)
    monkeypatch.setattr("streamlit.rerun", lambda: None)
    monkeypatch.setattr("streamlit.success", lambda msg: writes.append(msg))

    render_cut_plan_page()
    assert any("Confirm-Bedingungen" in msg or "erfüllt" in msg for msg in writes)


def test_ui_disables_confirm_button_when_conditions_not_met(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _make_project(tmp_path)
    _write_inventory(project, ["photo_a.jpg"])
    audio = _write_audio(project, "folder.mp3")
    folder = ConfirmedFolderPlanItem(
        folder_name=FOLDER_A, order_index=1, audio_path=str(audio), audio_duration_sec=5.0,
        sentence_items=[SentenceItem(sentence_id="s1", text="Text", primary_asset_id="asset_photo_a")],
        alignment_items=[AlignmentItem(sentence_id="s1", audio_start_sec=0.0, audio_end_sec=5.0, duration_sec=5.0)],
    )
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id, project_title="T", status="AUDIO_READY", intro=ConfirmedIntroPlanItem(), folders=[folder]
    )
    save_confirmed_voiceover_project_plan(project, plan)
    save_cut_plan_settings(project, CutPlanSettings(project_id=project.id))
    draft = build_cut_plan_draft(project)
    save_cut_plan_draft(project, draft)
    # Bewusst KEINE Asset-Auswahl/Validierung -> can_confirm_cut_plan muss False sein.

    _patch_project_selector(project, monkeypatch)

    captured_disabled: dict[str, bool] = {}

    def _fake_button(label, *args, **kwargs):
        if "bestätigen" in label.lower() and kwargs.get("key", "").startswith("cut_plan_confirm_"):
            captured_disabled["confirm"] = kwargs.get("disabled", False)
        return False

    monkeypatch.setattr("streamlit.button", _fake_button)
    monkeypatch.setattr("streamlit.rerun", lambda: None)

    render_cut_plan_page()
    assert captured_disabled.get("confirm") is True


def test_ui_shows_confirmed_cut_plan_if_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _build_validated_project(tmp_path)
    confirm_cut_plan(project)
    _patch_project_selector(project, monkeypatch)

    metrics: list[tuple] = []
    monkeypatch.setattr("streamlit.button", lambda *a, **k: False)
    monkeypatch.setattr("streamlit.rerun", lambda: None)
    monkeypatch.setattr("streamlit.metric", lambda label, value: metrics.append((label, value)))

    render_cut_plan_page()
    assert any(label == "Status" and value == CUT_PLAN_STATUS_CONFIRMED for label, value in metrics)


def test_ui_shows_trace_table_if_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _build_validated_project(tmp_path)
    confirm_cut_plan(project)
    _patch_project_selector(project, monkeypatch)

    dataframe_calls: list[list] = []
    monkeypatch.setattr("streamlit.button", lambda *a, **k: False)
    monkeypatch.setattr("streamlit.rerun", lambda: None)
    monkeypatch.setattr("streamlit.dataframe", lambda rows, **k: dataframe_calls.append(rows))

    render_cut_plan_page()
    assert any(
        isinstance(rows, list) and rows and "cut_item_id" in rows[0] and "chosen_asset_id" in rows[0]
        for rows in dataframe_calls
    )


# --- 33-38: Schutz bestehender Pipeline ---


def test_no_edit_plan_document_created(tmp_path: Path) -> None:
    project = _build_validated_project(tmp_path)
    confirm_cut_plan(project)
    assert not get_edit_plan_dir(project.work_dir_path).exists()


def test_no_otio_export_triggered(tmp_path: Path) -> None:
    project = _build_validated_project(tmp_path)
    confirm_cut_plan(project)
    assert not get_exports_dir(project.work_dir_path).exists()


def test_no_files_under_supplement_dir(tmp_path: Path) -> None:
    project = _build_validated_project(tmp_path)
    confirm_cut_plan(project)
    assert not get_supplement_dir(project.work_dir_path).exists()


def test_no_files_under_edit_plan_or_exports(tmp_path: Path) -> None:
    project = _build_validated_project(tmp_path)
    confirm_cut_plan(project)
    unconfirm_cut_plan(project)
    assert not get_edit_plan_dir(project.work_dir_path).exists()
    assert not get_exports_dir(project.work_dir_path).exists()


def test_no_regular_inventory_files_modified(tmp_path: Path) -> None:
    project = _build_validated_project(tmp_path)
    inv_path = get_folder_inventory_path(project.work_dir_path, FOLDER_A)
    original = inv_path.read_text(encoding="utf-8")

    confirm_cut_plan(project)

    assert inv_path.read_text(encoding="utf-8") == original


def test_no_original_media_modified(tmp_path: Path) -> None:
    project = _build_validated_project(tmp_path)
    photo_path = project.project_root_path / FOLDER_A / "photo_a.jpg"
    original = photo_path.read_bytes()

    confirm_cut_plan(project)

    assert photo_path.read_bytes() == original


# --- 39-40: Struktureller Schutz / Regression ---

_FORBIDDEN_SYMBOLS = (
    "build_edit_plan",
    "save_edit_plan",
    "edit_plan_builder",
    "otio_exporter",
    "export_otio_timeline",
    "mark_edit_plans_stale_for_folder",
    "replan_folder_after_supplement",
    "extend_folder_inventory",
    "_set_draft",
    "merge_confirmed_edit_plans",
)


def test_cut_plan_modules_never_reference_forbidden_production_symbols() -> None:
    import re

    import otio_app.services.voiceover_generation.cut_plan_asset_selector as asset_selector_module
    import otio_app.services.voiceover_generation.cut_plan_builder as builder_module
    import otio_app.services.voiceover_generation.cut_plan_confirm_service as confirm_module
    import otio_app.services.voiceover_generation.cut_plan_supplement_bridge as bridge_module
    import otio_app.services.voiceover_generation.cut_plan_supplement_models as supplement_models_module
    import otio_app.services.voiceover_generation.cut_plan_timeline_service as timeline_module
    import otio_app.services.voiceover_generation.cut_plan_trace_service as trace_module
    import otio_app.services.voiceover_generation.cut_plan_validator as validator_module
    import otio_app.services.voiceover_generation.cut_plan_visual_coverage as coverage_module
    import otio_app.ui.voiceover_generation.cut_plan_tab as tab_module

    for module in (
        asset_selector_module, builder_module, confirm_module, bridge_module, supplement_models_module,
        timeline_module, trace_module, validator_module, coverage_module, tab_module,
    ):
        source = inspect.getsource(module)
        for forbidden in _FORBIDDEN_SYMBOLS:
            assert not re.search(rf"\b{re.escape(forbidden)}\b", source), (
                f"{module.__name__} referenziert verbotenes Symbol '{forbidden}'."
            )


def test_with_voiceover_workflow_unaffected() -> None:
    from otio_app.services import edit_plan_builder, otio_exporter

    assert hasattr(edit_plan_builder, "build_edit_plan")
    assert hasattr(edit_plan_builder, "save_edit_plan")
    assert hasattr(otio_exporter, "build_otio_timeline")
