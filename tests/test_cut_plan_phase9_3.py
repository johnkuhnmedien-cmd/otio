"""Phase 9.3: EditPlan Bridge Confirm/Freeze + Duration-Cache-Fix.

Noch KEIN Produktions-EditPlan unter _otio/edit_plan/, kein locked
Produktionsplan, kein OTIO-Export, kein Render, keine neue LLM-Planung,
kein build_edit_plan(), kein save_edit_plan()."""

from __future__ import annotations

import inspect
import re
from pathlib import Path
from unittest.mock import patch

import pytest

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis
from otio_app.defaults import EDIT_PLAN_BRIDGE_CONFIRM_STATUS_CONFIRMED
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import (
    get_cut_plan_edit_plan_bridge_confirm_manifest_path,
    get_cut_plan_edit_plan_bridge_confirmed_audio_plan_path,
    get_cut_plan_edit_plan_bridge_confirmed_draft_path,
    get_cut_plan_edit_plan_bridge_confirmed_trace_path,
    get_edit_plan_dir,
    get_exports_dir,
    get_folder_inventory_path,
    get_supplement_dir,
)
from otio_app.services.voiceover_generation.cut_plan_builder import (
    apply_asset_selection_to_draft,
    build_cut_plan_draft,
    save_cut_plan_draft,
    validate_cut_plan_draft,
)
from otio_app.services.voiceover_generation.cut_plan_confirm_service import confirm_cut_plan, load_confirmed_cut_plan
from otio_app.services.voiceover_generation.cut_plan_edit_plan_bridge import (
    build_bridge_audio_plan_from_confirmed_cut_plan,
    build_edit_plan_draft_from_confirmed_cut_plan,
    load_bridge_audio_plan,
    load_edit_plan_bridge_draft,
    save_bridge_audio_plan,
    save_edit_plan_bridge_draft,
    validate_edit_plan_bridge,
)
from otio_app.services.voiceover_generation.cut_plan_edit_plan_confirm_service import (
    can_confirm_edit_plan_bridge,
    confirm_edit_plan_bridge,
    is_confirmed_edit_plan_bridge_stale,
    load_confirmed_bridge_audio_plan,
    load_confirmed_bridge_trace,
    load_confirmed_edit_plan_bridge,
    load_edit_plan_bridge_confirm_manifest,
    unconfirm_edit_plan_bridge,
)
from otio_app.services.voiceover_generation.cut_plan_edit_plan_trace import (
    build_edit_plan_bridge_trace,
    save_edit_plan_bridge_trace,
)
from otio_app.services.voiceover_generation.cut_plan_models import CutPlanSettings
from otio_app.services.voiceover_generation.cut_plan_settings_service import save_cut_plan_settings
from otio_app.services.voiceover_generation.final_plan_service import save_confirmed_voiceover_project_plan
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
        id="cut-plan-edit-plan-bridge-confirm-project",
        name="Cut Plan EditPlan Bridge Confirm Test",
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


def _build_confirmed_project(tmp_path: Path) -> Project:
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
    confirm_cut_plan(project)
    return project


def _build_and_validate_bridge(project: Project) -> None:
    edit_plan = build_edit_plan_draft_from_confirmed_cut_plan(project)
    edit_plan = save_edit_plan_bridge_draft(project, edit_plan)
    audio_plan = build_bridge_audio_plan_from_confirmed_cut_plan(project)
    save_bridge_audio_plan(project, audio_plan)
    confirmed_cut_plan = load_confirmed_cut_plan(project)
    trace = build_edit_plan_bridge_trace(project, confirmed_cut_plan, edit_plan)
    save_edit_plan_bridge_trace(project, trace)
    validate_edit_plan_bridge(project, edit_plan)


def _patch_project_selector(project: Project, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("otio_app.ui.project_context.list_projects", lambda: [project])
    monkeypatch.setattr(
        "otio_app.ui.project_context.get_project_by_id",
        lambda project_id: project if project_id == project.id else None,
    )
    monkeypatch.setattr("streamlit.session_state", {"active_project_id": project.id}, raising=False)


# --- 1: Duration Cache ---


def test_validate_edit_plan_bridge_caches_probe_duration_per_video_path(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    video_path = project.project_root_path / FOLDER_A / "clip.mp4"
    video_path.write_bytes(b"FAKE_VIDEO_BYTES")

    edit_plan = build_edit_plan_draft_from_confirmed_cut_plan(project)
    visual_items = [item for item in edit_plan.timeline_items if item.track == "V1"]
    updated_visuals = [
        item.model_copy(update={"type": "video_shot", "resolved_media_path": str(video_path)})
        for item in visual_items
    ]
    audio_items = [item for item in edit_plan.timeline_items if item.track == "A1"]
    edit_plan = edit_plan.model_copy(update={"timeline_items": updated_visuals + audio_items})
    save_edit_plan_bridge_draft(project, edit_plan)

    with patch(
        "otio_app.services.voiceover_generation.cut_plan_edit_plan_bridge.probe_duration_seconds",
        return_value=100.0,
    ) as mock_probe:
        validate_edit_plan_bridge(project, edit_plan)

    assert mock_probe.call_count == 1


# --- 2-14: can_confirm_edit_plan_bridge ---


def test_cannot_confirm_without_draft(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    eligible, reasons = can_confirm_edit_plan_bridge(project)
    assert eligible is False
    assert any("Draft" in r for r in reasons)


def test_cannot_confirm_without_bridge_audio_plan(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    edit_plan = build_edit_plan_draft_from_confirmed_cut_plan(project)
    save_edit_plan_bridge_draft(project, edit_plan)
    eligible, reasons = can_confirm_edit_plan_bridge(project)
    assert eligible is False
    assert any("bridge_audio_plan" in r for r in reasons)


def test_cannot_confirm_without_trace(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    edit_plan = build_edit_plan_draft_from_confirmed_cut_plan(project)
    save_edit_plan_bridge_draft(project, edit_plan)
    audio_plan = build_bridge_audio_plan_from_confirmed_cut_plan(project)
    save_bridge_audio_plan(project, audio_plan)
    eligible, reasons = can_confirm_edit_plan_bridge(project)
    assert eligible is False
    assert any("trace" in r for r in reasons)


def test_cannot_confirm_without_validation_report(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    edit_plan = build_edit_plan_draft_from_confirmed_cut_plan(project)
    save_edit_plan_bridge_draft(project, edit_plan)
    audio_plan = build_bridge_audio_plan_from_confirmed_cut_plan(project)
    save_bridge_audio_plan(project, audio_plan)
    confirmed_cut_plan = load_confirmed_cut_plan(project)
    trace = build_edit_plan_bridge_trace(project, confirmed_cut_plan, edit_plan)
    save_edit_plan_bridge_trace(project, trace)
    # KEIN validate_edit_plan_bridge aufgerufen.
    eligible, reasons = can_confirm_edit_plan_bridge(project)
    assert eligible is False
    assert any("validieren" in r for r in reasons)


def test_cannot_confirm_when_report_blocked(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    _build_and_validate_bridge(project)
    edit_plan = load_edit_plan_bridge_draft(project)
    audio_items = [item for item in edit_plan.timeline_items if item.track == "A1"]
    audio_items[0].resolved_media_path  # noqa: B018 - keep reference
    audio_dir = project.work_dir_path / "voiceover_generation" / "audio"
    for audio_file in audio_dir.glob("*.mp3"):
        audio_file.unlink()
    validate_edit_plan_bridge(project, edit_plan)  # Report wird jetzt BLOCKED (fehlende Audiodatei)

    eligible, reasons = can_confirm_edit_plan_bridge(project)
    assert eligible is False
    assert any("BLOCKED" in r for r in reasons)


def test_cannot_confirm_with_blockers_in_report(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    _build_and_validate_bridge(project)
    edit_plan = load_edit_plan_bridge_draft(project)

    audio_dir = project.work_dir_path / "voiceover_generation" / "audio"
    for audio_file in audio_dir.glob("*.mp3"):
        audio_file.unlink()
    validate_edit_plan_bridge(project, edit_plan)

    eligible, reasons = can_confirm_edit_plan_bridge(project)
    assert eligible is False
    assert any("Blocker" in r for r in reasons)


def test_cannot_confirm_with_stale_validation_report(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    _build_and_validate_bridge(project)

    # Draft neu erzeugen und speichern, OHNE erneut zu validieren -> Report veraltet.
    new_edit_plan = build_edit_plan_draft_from_confirmed_cut_plan(project)
    new_edit_plan = new_edit_plan.model_copy(update={"plan_generation_notes": list(new_edit_plan.plan_generation_notes) + ["manual_change=true"]})
    save_edit_plan_bridge_draft(project, new_edit_plan)

    eligible, reasons = can_confirm_edit_plan_bridge(project)
    assert eligible is False
    assert any("veraltet" in r for r in reasons)


def test_cannot_confirm_with_stale_confirmed_cut_plan(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    _build_and_validate_bridge(project)

    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id, project_title="Geändert", status="AUDIO_READY",
        intro=ConfirmedIntroPlanItem(), folders=[],
    )
    save_confirmed_voiceover_project_plan(project, plan)

    eligible, reasons = can_confirm_edit_plan_bridge(project)
    assert eligible is False
    assert any("Cut Plan" in r for r in reasons)


def test_cannot_confirm_with_audio_plan_mismatch(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    _build_and_validate_bridge(project)

    audio_plan = load_bridge_audio_plan(project)
    truncated = audio_plan.model_copy(update={"items": audio_plan.items[:-1] if audio_plan.items else []})
    save_bridge_audio_plan(project, truncated)

    eligible, reasons = can_confirm_edit_plan_bridge(project)
    assert eligible is False
    assert any("bridge_audio_plan.json stimmt nicht" in r for r in reasons)


def test_cannot_confirm_with_trace_mismatch(tmp_path: Path) -> None:
    from otio_app.services.voiceover_generation.cut_plan_edit_plan_trace import load_edit_plan_bridge_trace

    project = _build_confirmed_project(tmp_path)
    _build_and_validate_bridge(project)

    trace = load_edit_plan_bridge_trace(project)
    truncated_trace = trace.model_copy(update={"entries": trace.entries[:-1] if trace.entries else []})
    save_edit_plan_bridge_trace(project, truncated_trace)

    eligible, reasons = can_confirm_edit_plan_bridge(project)
    assert eligible is False
    assert any("trace" in r.lower() and "stimmt nicht" in r for r in reasons)


def test_cannot_confirm_with_zero_duration_timeline_item(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    _build_and_validate_bridge(project)

    edit_plan = load_edit_plan_bridge_draft(project)
    broken_item = edit_plan.timeline_items[0].model_copy(
        update={"timeline_out_sec": edit_plan.timeline_items[0].timeline_in_sec}
    )
    updated_items = [broken_item] + edit_plan.timeline_items[1:]
    broken_edit_plan = edit_plan.model_copy(update={"timeline_items": updated_items})
    save_edit_plan_bridge_draft(project, broken_edit_plan)
    # Report absichtlich NICHT neu validiert -> hash mismatch würde ohnehin blocken, wir prüfen aber den
    # spezifischen Grund isoliert über can_confirm_edit_plan_bridge, das den Draft direkt scannt.

    eligible, reasons = can_confirm_edit_plan_bridge(project)
    assert eligible is False
    assert any("Dauer <= 0" in r for r in reasons)


def test_can_confirm_with_pass_report(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    _build_and_validate_bridge(project)
    eligible, reasons = can_confirm_edit_plan_bridge(project)
    assert eligible is True
    assert reasons == []


def test_can_confirm_with_warning_report_without_blockers(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    _build_and_validate_bridge(project)

    from otio_app.services.voiceover_generation.cut_plan_edit_plan_bridge import (
        load_edit_plan_bridge_validation_report,
        save_edit_plan_bridge_validation_report,
    )
    from otio_app.services.voiceover_generation.cut_plan_edit_plan_models import EditPlanBridgeValidationError

    report = load_edit_plan_bridge_validation_report(project)
    warning_report = report.model_copy(
        update={
            "status": "WARNING",
            "warnings": [EditPlanBridgeValidationError(type="SOME_WARNING", severity="WARNING", scope="project")],
        }
    )
    save_edit_plan_bridge_validation_report(project, warning_report)

    eligible, _reasons = can_confirm_edit_plan_bridge(project)
    assert eligible is True


# --- 15-22: confirm_edit_plan_bridge schreibt Dateien + Manifest ---


def test_confirm_writes_confirmed_edit_plan_json(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    _build_and_validate_bridge(project)
    confirm_edit_plan_bridge(project)
    assert get_cut_plan_edit_plan_bridge_confirmed_draft_path(project.work_dir_path).is_file()


def test_confirm_writes_confirmed_bridge_audio_plan_json(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    _build_and_validate_bridge(project)
    confirm_edit_plan_bridge(project)
    assert get_cut_plan_edit_plan_bridge_confirmed_audio_plan_path(project.work_dir_path).is_file()


def test_confirm_writes_confirmed_trace_json(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    _build_and_validate_bridge(project)
    confirm_edit_plan_bridge(project)
    assert get_cut_plan_edit_plan_bridge_confirmed_trace_path(project.work_dir_path).is_file()


def test_confirm_writes_manifest_json(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    _build_and_validate_bridge(project)
    confirm_edit_plan_bridge(project)
    assert get_cut_plan_edit_plan_bridge_confirm_manifest_path(project.work_dir_path).is_file()


def test_manifest_contains_edit_plan_hash(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    _build_and_validate_bridge(project)
    manifest = confirm_edit_plan_bridge(project)
    assert manifest.edit_plan_hash
    assert manifest.status == EDIT_PLAN_BRIDGE_CONFIRM_STATUS_CONFIRMED


def test_manifest_contains_bridge_audio_plan_hash(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    _build_and_validate_bridge(project)
    manifest = confirm_edit_plan_bridge(project)
    assert manifest.bridge_audio_plan_hash


def test_manifest_contains_bridge_trace_hash(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    _build_and_validate_bridge(project)
    manifest = confirm_edit_plan_bridge(project)
    assert manifest.bridge_trace_hash


def test_manifest_contains_validation_report_hash(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    _build_and_validate_bridge(project)
    manifest = confirm_edit_plan_bridge(project)
    assert manifest.validation_report_hash


# --- 23-24: Confirmed-Schutz ---


def test_new_draft_does_not_overwrite_confirmed_bridge(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    _build_and_validate_bridge(project)
    confirm_edit_plan_bridge(project)
    confirmed_before = load_confirmed_edit_plan_bridge(project)

    build_edit_plan_draft_from_confirmed_cut_plan(project)  # Rebuild, aber nicht gespeichert/übernommen

    confirmed_after = load_confirmed_edit_plan_bridge(project)
    assert confirmed_after.model_dump(mode="json") == confirmed_before.model_dump(mode="json")


def test_new_validation_does_not_overwrite_confirmed_bridge(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    _build_and_validate_bridge(project)
    confirm_edit_plan_bridge(project)
    confirmed_before = load_confirmed_edit_plan_bridge(project)

    edit_plan = load_edit_plan_bridge_draft(project)
    validate_edit_plan_bridge(project, edit_plan)  # erneute Validierung

    confirmed_after = load_confirmed_edit_plan_bridge(project)
    assert confirmed_after.model_dump(mode="json") == confirmed_before.model_dump(mode="json")


# --- 25-27: Staleness ---


def test_is_confirmed_edit_plan_bridge_stale_detects_changed_draft(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    _build_and_validate_bridge(project)
    confirm_edit_plan_bridge(project)
    assert is_confirmed_edit_plan_bridge_stale(project) is False

    edit_plan = load_edit_plan_bridge_draft(project)
    changed = edit_plan.model_copy(
        update={"plan_generation_notes": list(edit_plan.plan_generation_notes) + ["extra_note=changed"]}
    )
    save_edit_plan_bridge_draft(project, changed)

    assert is_confirmed_edit_plan_bridge_stale(project) is True


def test_is_confirmed_edit_plan_bridge_stale_detects_changed_audio_plan(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    _build_and_validate_bridge(project)
    confirm_edit_plan_bridge(project)
    assert is_confirmed_edit_plan_bridge_stale(project) is False

    audio_plan = load_bridge_audio_plan(project)
    changed = audio_plan.model_copy(update={"items": audio_plan.items[:-1] if audio_plan.items else []})
    save_bridge_audio_plan(project, changed)

    assert is_confirmed_edit_plan_bridge_stale(project) is True


def test_is_confirmed_edit_plan_bridge_stale_detects_changed_trace(tmp_path: Path) -> None:
    from otio_app.services.voiceover_generation.cut_plan_edit_plan_trace import load_edit_plan_bridge_trace

    project = _build_confirmed_project(tmp_path)
    _build_and_validate_bridge(project)
    confirm_edit_plan_bridge(project)
    assert is_confirmed_edit_plan_bridge_stale(project) is False

    trace = load_edit_plan_bridge_trace(project)
    changed_trace = trace.model_copy(update={"entries": trace.entries[:-1] if trace.entries else []})
    save_edit_plan_bridge_trace(project, changed_trace)

    assert is_confirmed_edit_plan_bridge_stale(project) is True


# --- 28: Unconfirm ---


def test_unconfirm_removes_all_confirmed_files(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    _build_and_validate_bridge(project)
    confirm_edit_plan_bridge(project)

    assert load_edit_plan_bridge_confirm_manifest(project) is not None
    assert load_confirmed_edit_plan_bridge(project) is not None
    assert load_confirmed_bridge_audio_plan(project) is not None
    assert load_confirmed_bridge_trace(project) is not None

    unconfirm_edit_plan_bridge(project)

    assert load_edit_plan_bridge_confirm_manifest(project) is None
    assert load_confirmed_edit_plan_bridge(project) is None
    assert load_confirmed_bridge_audio_plan(project) is None
    assert load_confirmed_bridge_trace(project) is None


# --- 29-32: UI ---


def test_ui_shows_confirm_eligibility(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _build_confirmed_project(tmp_path)
    _build_and_validate_bridge(project)
    _patch_project_selector(project, monkeypatch)

    writes: list[str] = []
    monkeypatch.setattr("streamlit.button", lambda *a, **k: False)
    monkeypatch.setattr("streamlit.rerun", lambda: None)
    monkeypatch.setattr("streamlit.success", lambda msg: writes.append(msg))

    render_cut_plan_page()
    assert any("Confirm-Bedingungen" in msg for msg in writes)


def test_ui_shows_disabled_confirm_button_when_conditions_not_met(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _build_confirmed_project(tmp_path)
    edit_plan = build_edit_plan_draft_from_confirmed_cut_plan(project)
    save_edit_plan_bridge_draft(project, edit_plan)
    # Bewusst KEIN Audio-Plan/Trace/Validation -> can_confirm_edit_plan_bridge muss False sein.

    _patch_project_selector(project, monkeypatch)
    captured_disabled: dict[str, bool] = {}

    def _fake_button(label, *args, **kwargs):
        key = kwargs.get("key", "")
        if key.startswith("cut_plan_edit_plan_bridge_confirm_"):
            captured_disabled["confirm"] = kwargs.get("disabled", False)
        return False

    monkeypatch.setattr("streamlit.button", _fake_button)
    monkeypatch.setattr("streamlit.rerun", lambda: None)

    render_cut_plan_page()
    assert captured_disabled.get("confirm") is True


def test_ui_shows_confirmed_bridge_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _build_confirmed_project(tmp_path)
    _build_and_validate_bridge(project)
    confirm_edit_plan_bridge(project)

    _patch_project_selector(project, monkeypatch)
    metrics: list[tuple] = []
    monkeypatch.setattr("streamlit.button", lambda *a, **k: False)
    monkeypatch.setattr("streamlit.rerun", lambda: None)
    monkeypatch.setattr("streamlit.metric", lambda label, value: metrics.append((label, value)))

    render_cut_plan_page()
    assert any(label == "Status" and value == EDIT_PLAN_BRIDGE_CONFIRM_STATUS_CONFIRMED for label, value in metrics)


def test_ui_shows_staleness_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _build_confirmed_project(tmp_path)
    _build_and_validate_bridge(project)
    confirm_edit_plan_bridge(project)

    edit_plan = load_edit_plan_bridge_draft(project)
    changed = edit_plan.model_copy(
        update={"plan_generation_notes": list(edit_plan.plan_generation_notes) + ["extra_note=changed"]}
    )
    save_edit_plan_bridge_draft(project, changed)

    _patch_project_selector(project, monkeypatch)
    warnings: list[str] = []
    monkeypatch.setattr("streamlit.button", lambda *a, **k: False)
    monkeypatch.setattr("streamlit.rerun", lambda: None)
    monkeypatch.setattr("streamlit.warning", lambda msg: warnings.append(msg))

    render_cut_plan_page()
    assert any("veraltet" in msg for msg in warnings)


# --- 33-39: Schutz bestehender Pipeline ---


def test_no_production_edit_plan_written(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    _build_and_validate_bridge(project)
    confirm_edit_plan_bridge(project)
    from otio_app.project_layout import get_folder_edit_plan_path

    assert not get_folder_edit_plan_path(project.work_dir_path, FOLDER_A).is_file()


def test_no_locked_edit_plan_created(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    _build_and_validate_bridge(project)
    confirm_edit_plan_bridge(project)
    confirmed = load_confirmed_edit_plan_bridge(project)
    assert confirmed.confirmed is False


def test_no_otio_export_triggered(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    _build_and_validate_bridge(project)
    confirm_edit_plan_bridge(project)
    assert not get_exports_dir(project.work_dir_path).exists()


def test_no_files_written_under_edit_plan_dir(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    _build_and_validate_bridge(project)
    confirm_edit_plan_bridge(project)
    assert not get_edit_plan_dir(project.work_dir_path).exists()


def test_no_files_written_under_exports_dir(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    _build_and_validate_bridge(project)
    confirm_edit_plan_bridge(project)
    assert not get_exports_dir(project.work_dir_path).exists()


def test_no_files_written_under_supplement_dir(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    _build_and_validate_bridge(project)
    confirm_edit_plan_bridge(project)
    assert not get_supplement_dir(project.work_dir_path).exists()


def test_no_original_media_modified(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    photo_path = project.project_root_path / FOLDER_A / "photo_a.jpg"
    original = photo_path.read_bytes()
    _build_and_validate_bridge(project)
    confirm_edit_plan_bridge(project)
    assert photo_path.read_bytes() == original


# --- 40-41: Struktureller Schutz / Regression ---

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


def test_bridge_modules_reference_no_forbidden_production_functions() -> None:
    import otio_app.services.voiceover_generation.cut_plan_edit_plan_bridge as bridge_module
    import otio_app.services.voiceover_generation.cut_plan_edit_plan_confirm_service as confirm_module
    import otio_app.services.voiceover_generation.cut_plan_edit_plan_trace as trace_module

    for module in (bridge_module, confirm_module, trace_module):
        source = inspect.getsource(module)
        for symbol in _FORBIDDEN_SYMBOLS:
            assert not re.search(rf"\b{re.escape(symbol)}\b", source), (
                f"{module.__name__} referenziert verbotenes Symbol '{symbol}'."
            )


def test_with_voiceover_workflow_unaffected() -> None:
    from otio_app.services import edit_plan_builder, otio_exporter

    assert hasattr(edit_plan_builder, "build_edit_plan")
    assert hasattr(edit_plan_builder, "save_edit_plan")
    assert hasattr(otio_exporter, "build_otio_timeline")
