"""Phase 9.1: Confirmed CutPlan -> isoliertes EditPlanDocument-Draft.

Noch KEIN locked EditPlan, kein OTIO-Export, kein Render, keine neue
LLM-Planung, kein build_edit_plan()/save_edit_plan(), kein Überschreiben
bestehender Produktions-EditPlans."""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis
from otio_app.defaults import (
    EDIT_PLAN_BRIDGE_VALIDATION_STATUS_BLOCKED,
    EDIT_PLAN_BRIDGE_VALIDATION_STATUS_PASS,
)
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import (
    get_cut_plan_edit_plan_bridge_dir,
    get_cut_plan_edit_plan_bridge_draft_path,
    get_edit_plan_dir,
    get_exports_dir,
    get_folder_inventory_path,
)
from otio_app.services.voiceover_generation.cut_plan_builder import (
    apply_asset_selection_to_draft,
    build_cut_plan_draft,
    save_cut_plan_draft,
    validate_cut_plan_draft,
)
from otio_app.services.voiceover_generation.cut_plan_confirm_service import confirm_cut_plan
from otio_app.services.voiceover_generation.cut_plan_edit_plan_bridge import (
    build_edit_plan_draft_from_confirmed_cut_plan,
    ceil_to_frame,
    is_edit_plan_bridge_stale,
    load_edit_plan_bridge_draft,
    load_edit_plan_bridge_validation_report,
    round_audio_times_to_frame,
    round_to_frame,
    round_visual_times_to_frame,
    save_edit_plan_bridge_draft,
    validate_edit_plan_bridge,
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
        id="cut-plan-edit-plan-bridge-project",
        name="Cut Plan EditPlan Bridge Test",
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
    audio_dir = project.language_work_dir_path / "voiceover_generation" / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    path = audio_dir / name
    path.write_bytes(b"FAKE_AUDIO_BYTES")
    return path


def _build_confirmed_project(tmp_path: Path) -> Project:
    """Ein vollständiges Projekt mit Intro + einem Folder, das über den
    kompletten Cut-Plan-Trichter bis CONFIRMED läuft."""
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


def _patch_project_selector(project: Project, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("otio_app.ui.project_context.list_projects", lambda: [project])
    monkeypatch.setattr(
        "otio_app.ui.project_context.get_project_by_id",
        lambda project_id: project if project_id == project.id else None,
    )
    monkeypatch.setattr("streamlit.session_state", {"active_project_id": project.id}, raising=False)


# --- 1-2: Bridge-Voraussetzungen ---


def test_bridge_blocks_without_confirmed_cut_plan(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    with pytest.raises(ValueError, match="bestätigter Cut Plan"):
        build_edit_plan_draft_from_confirmed_cut_plan(project)


def test_bridge_blocks_when_confirmed_cut_plan_is_stale(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)

    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id, project_title="Geändert", status="AUDIO_READY",
        intro=ConfirmedIntroPlanItem(), folders=[],
    )
    save_confirmed_voiceover_project_plan(project, plan)

    with pytest.raises(ValueError, match="veraltet"):
        build_edit_plan_draft_from_confirmed_cut_plan(project)


# --- 3-8: Mapping ---


def test_audio_item_becomes_audio_timeline_item(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    edit_plan = build_edit_plan_draft_from_confirmed_cut_plan(project)
    audio_items = [item for item in edit_plan.timeline_items if item.track == "A1"]
    assert len(audio_items) == 2  # Intro + 1 Folder


def test_visual_segment_becomes_visual_timeline_item(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    edit_plan = build_edit_plan_draft_from_confirmed_cut_plan(project)
    visual_items = [item for item in edit_plan.timeline_items if item.track != "A1"]
    assert len(visual_items) == 2  # ein Segment je Item
    assert all(item.type in ("video_shot", "image_shot") for item in visual_items)


def test_intro_audio_is_mapped(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    edit_plan = build_edit_plan_draft_from_confirmed_cut_plan(project)
    intro_audio = [item for item in edit_plan.timeline_items if "intro" in item.timeline_item_id and item.track == "A1"]
    assert len(intro_audio) == 1


def test_folder_audio_is_mapped(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    edit_plan = build_edit_plan_draft_from_confirmed_cut_plan(project)
    folder_audio = [item for item in edit_plan.timeline_items if item.folder_name == FOLDER_A and item.track == "A1"]
    assert len(folder_audio) == 1


def test_intro_visual_segments_are_mapped(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    edit_plan = build_edit_plan_draft_from_confirmed_cut_plan(project)
    intro_visuals = [
        item for item in edit_plan.timeline_items if item.track != "A1" and "intro" in item.section_id
    ]
    assert len(intro_visuals) == 1


def test_folder_visual_segments_are_mapped(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    edit_plan = build_edit_plan_draft_from_confirmed_cut_plan(project)
    folder_visuals = [
        item for item in edit_plan.timeline_items if item.track != "A1" and item.folder_name == FOLDER_A
    ]
    assert len(folder_visuals) == 1


# --- 9-12: Frame-Normalisierung ---


def test_timeline_times_are_frame_rounded() -> None:
    fps = 25
    frame = 1.0 / fps
    rounded = round_to_frame(6.253, fps)
    assert abs(rounded / frame - round(rounded / frame)) < 1e-9


def test_source_times_are_frame_rounded() -> None:
    fps = 25
    timeline_in, timeline_out, source_in, source_out, _rounded, _delta = round_visual_times_to_frame(
        1.0, 6.253, 0.0, 5.253, fps
    )
    frame = 1.0 / fps
    assert abs(source_out / frame - round(source_out / frame)) < 1e-9
    assert abs(source_in / frame - round(source_in / frame)) < 1e-9


def test_audio_is_never_shortened_by_rounding() -> None:
    fps = 25
    duration_sec = 5.003  # nicht frame-genau
    _timeline_in, _timeline_out, _source_in, rounded_source_out, _rounded, _delta = round_audio_times_to_frame(
        1.0, 1.0 + duration_sec, duration_sec, fps
    )
    assert rounded_source_out >= duration_sec  # niemals gekürzt, nur aufgerundet oder gleich


def test_ceil_to_frame_never_rounds_down() -> None:
    fps = 25
    assert ceil_to_frame(5.001, fps) >= 5.001
    assert ceil_to_frame(5.0, fps) == pytest.approx(5.0)  # exakt auf Frame -> keine unnötige Rundung


def test_rounding_delta_is_recorded_in_trace(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    from otio_app.services.voiceover_generation.cut_plan_confirm_service import load_confirmed_cut_plan

    confirmed = load_confirmed_cut_plan(project)
    edit_plan = build_edit_plan_draft_from_confirmed_cut_plan(project)
    trace = build_edit_plan_bridge_trace(project, confirmed, edit_plan)
    assert any(entry.frame_rounded for entry in trace.entries)
    assert any(abs(entry.frame_rounding_delta_sec) >= 0.0 for entry in trace.entries)


# --- 13-16: Trace-Inhalt ---


def test_trace_has_one_entry_per_visual_segment(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    from otio_app.services.voiceover_generation.cut_plan_confirm_service import load_confirmed_cut_plan

    confirmed = load_confirmed_cut_plan(project)
    edit_plan = build_edit_plan_draft_from_confirmed_cut_plan(project)
    trace = build_edit_plan_bridge_trace(project, confirmed, edit_plan)

    total_segments = sum(len(item.planned_visual_segments) for item in confirmed.items)
    visual_entries = [entry for entry in trace.entries if entry.visual_segment_id]
    assert len(visual_entries) == total_segments


def test_trace_contains_audio_entries_or_documents_visual_only(tmp_path: Path) -> None:
    """Design-Entscheidung: Der Trace enthält BEWUSST auch Audio-Entries
    (nicht nur Visual), damit die vollständige Timeline nachvollziehbar ist."""
    project = _build_confirmed_project(tmp_path)
    from otio_app.services.voiceover_generation.cut_plan_confirm_service import load_confirmed_cut_plan

    confirmed = load_confirmed_cut_plan(project)
    edit_plan = build_edit_plan_draft_from_confirmed_cut_plan(project)
    trace = build_edit_plan_bridge_trace(project, confirmed, edit_plan)

    audio_entries = [entry for entry in trace.entries if entry.track == "A1"]
    assert len(audio_entries) == len(confirmed.audio_items)


def test_trace_contains_cut_item_id_and_visual_segment_id(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    from otio_app.services.voiceover_generation.cut_plan_confirm_service import load_confirmed_cut_plan

    confirmed = load_confirmed_cut_plan(project)
    edit_plan = build_edit_plan_draft_from_confirmed_cut_plan(project)
    trace = build_edit_plan_bridge_trace(project, confirmed, edit_plan)

    visual_entries = [entry for entry in trace.entries if entry.visual_segment_id]
    assert all(entry.cut_item_id for entry in visual_entries)
    assert all(entry.visual_segment_id for entry in visual_entries)


def test_trace_contains_source_sentence_or_hook_beat_id(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    from otio_app.services.voiceover_generation.cut_plan_confirm_service import load_confirmed_cut_plan

    confirmed = load_confirmed_cut_plan(project)
    edit_plan = build_edit_plan_draft_from_confirmed_cut_plan(project)
    trace = build_edit_plan_bridge_trace(project, confirmed, edit_plan)

    intro_entry = next(entry for entry in trace.entries if entry.source_scope == "intro" and entry.visual_segment_id)
    folder_entry = next(entry for entry in trace.entries if entry.source_scope == "folder" and entry.visual_segment_id)
    assert intro_entry.source_hook_beat_id == "hook_beat_001"
    assert folder_entry.source_sentence_id == "sentence_001"


# --- 17-21: Bridge Report ---


def test_bridge_report_pass_in_happy_path(tmp_path: Path) -> None:
    from otio_app.services.voiceover_generation.cut_plan_edit_plan_bridge import (
        build_bridge_audio_plan_from_confirmed_cut_plan,
        save_bridge_audio_plan,
    )

    project = _build_confirmed_project(tmp_path)
    edit_plan = build_edit_plan_draft_from_confirmed_cut_plan(project)
    save_edit_plan_bridge_draft(project, edit_plan)
    # Seit Phase 9.2 gehört ein passender bridge_audio_plan.json zum
    # vollständigen, validen Bridge-Draft (siehe AUDIO_PLAN_MISSING-Check).
    save_bridge_audio_plan(project, build_bridge_audio_plan_from_confirmed_cut_plan(project))
    report = validate_edit_plan_bridge(project, edit_plan)
    assert report.status == EDIT_PLAN_BRIDGE_VALIDATION_STATUS_PASS
    assert report.blockers == []


def test_bridge_report_blocked_when_audio_file_missing(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    edit_plan = build_edit_plan_draft_from_confirmed_cut_plan(project)
    save_edit_plan_bridge_draft(project, edit_plan)

    # Audiodatei nachträglich löschen -> Bridge muss dies erkennen.
    audio_dir = project.language_work_dir_path / "voiceover_generation" / "audio"
    for audio_file in audio_dir.glob("*.mp3"):
        audio_file.unlink()

    report = validate_edit_plan_bridge(project, edit_plan)
    assert report.status == EDIT_PLAN_BRIDGE_VALIDATION_STATUS_BLOCKED
    assert any(b.type == "AUDIO_FILE_MISSING" for b in report.blockers)


def test_bridge_report_blocked_when_visual_asset_missing(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    edit_plan = build_edit_plan_draft_from_confirmed_cut_plan(project)
    save_edit_plan_bridge_draft(project, edit_plan)

    for photo_file in (project.project_root_path / FOLDER_A).glob("*.jpg"):
        photo_file.unlink()

    report = validate_edit_plan_bridge(project, edit_plan)
    assert report.status == EDIT_PLAN_BRIDGE_VALIDATION_STATUS_BLOCKED
    assert any(b.type == "ASSET_FILE_MISSING" for b in report.blockers)


def test_bridge_report_blocked_on_timeline_overlap(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    edit_plan = build_edit_plan_draft_from_confirmed_cut_plan(project)

    visual_items = [item for item in edit_plan.timeline_items if item.track != "A1"]
    assert len(visual_items) >= 2
    # Zweites Visual künstlich ins erste hinein verschieben -> Overlap erzwingen.
    overlapping = visual_items[1].model_copy(
        update={"timeline_in_sec": visual_items[0].timeline_in_sec + 0.01}
    )
    updated_items = [
        overlapping if item.timeline_item_id == visual_items[1].timeline_item_id else item
        for item in edit_plan.timeline_items
    ]
    broken_edit_plan = edit_plan.model_copy(update={"timeline_items": updated_items})
    save_edit_plan_bridge_draft(project, broken_edit_plan)

    report = validate_edit_plan_bridge(project, broken_edit_plan)
    assert report.status == EDIT_PLAN_BRIDGE_VALIDATION_STATUS_BLOCKED
    assert any(b.type == "TIMELINE_OVERLAP" for b in report.blockers)


def test_bridge_report_blocked_on_visual_gap_during_audio(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    edit_plan = build_edit_plan_draft_from_confirmed_cut_plan(project)

    # Alle Visual-Items entfernen -> komplettes Schwarzbild während Audio.
    audio_only_items = [item for item in edit_plan.timeline_items if item.track == "A1"]
    broken_edit_plan = edit_plan.model_copy(update={"timeline_items": audio_only_items})
    save_edit_plan_bridge_draft(project, broken_edit_plan)

    report = validate_edit_plan_bridge(project, broken_edit_plan)
    assert report.status == EDIT_PLAN_BRIDGE_VALIDATION_STATUS_BLOCKED
    assert any(b.type in ("BLACK_GAP_DURING_AUDIO", "VISUAL_ITEM_MISSING") for b in report.blockers)


# --- 22-24: Pfade ---


def test_bridge_draft_written_under_edit_plan_bridge_dir(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    edit_plan = build_edit_plan_draft_from_confirmed_cut_plan(project)
    save_edit_plan_bridge_draft(project, edit_plan)

    path = get_cut_plan_edit_plan_bridge_draft_path(project.language_work_dir_path)
    assert path.is_file()
    assert "edit_plan_bridge" in str(path)
    assert "voiceover_generation/cut_plan" in str(path).replace("\\", "/")


def test_bridge_does_not_write_under_edit_plan_dir(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    edit_plan = build_edit_plan_draft_from_confirmed_cut_plan(project)
    save_edit_plan_bridge_draft(project, edit_plan)
    validate_edit_plan_bridge(project, edit_plan)

    assert not get_edit_plan_dir(project.language_work_dir_path).exists()


def test_bridge_does_not_write_under_exports_dir(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    edit_plan = build_edit_plan_draft_from_confirmed_cut_plan(project)
    save_edit_plan_bridge_draft(project, edit_plan)
    validate_edit_plan_bridge(project, edit_plan)

    assert not get_exports_dir(project.language_work_dir_path).exists()


# --- 25-31: Draft-Status / Schutz ---


def test_bridge_creates_no_locked_edit_plan(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    edit_plan = build_edit_plan_draft_from_confirmed_cut_plan(project)
    assert edit_plan.confirmed is False


def test_bridge_creates_no_production_confirmed_edit_plan(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    edit_plan = build_edit_plan_draft_from_confirmed_cut_plan(project)
    save_edit_plan_bridge_draft(project, edit_plan)
    from otio_app.project_layout import get_folder_edit_plan_path

    assert not get_folder_edit_plan_path(project.language_work_dir_path, FOLDER_A).is_file()


def test_bridge_module_does_not_call_build_or_save_edit_plan() -> None:
    import otio_app.services.voiceover_generation.cut_plan_edit_plan_bridge as bridge_module

    source = inspect.getsource(bridge_module)
    assert not re.search(r"\bbuild_edit_plan\b", source)
    assert not re.search(r"\bsave_edit_plan\b", source)


def test_bridge_module_does_not_trigger_otio_export() -> None:
    import otio_app.services.voiceover_generation.cut_plan_edit_plan_bridge as bridge_module
    import otio_app.services.voiceover_generation.cut_plan_edit_plan_trace as trace_module

    for module in (bridge_module, trace_module):
        source = inspect.getsource(module)
        assert not re.search(r"\botio_exporter\b", source)
        assert not re.search(r"\bexport_otio_timeline\b", source)


def test_bridge_draft_contains_no_api_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _build_confirmed_project(tmp_path)
    monkeypatch.setattr(
        "otio_app.services.voiceover_generation.cut_plan_edit_plan_bridge.get_api_key",
        lambda key: "SUPER_SECRET_TEST_KEY_VALUE" if key == "PEXELS_API_KEY" else None,
    )
    edit_plan = build_edit_plan_draft_from_confirmed_cut_plan(project)
    serialized = edit_plan.model_dump_json()
    assert "SUPER_SECRET_TEST_KEY_VALUE" not in serialized

    report = validate_edit_plan_bridge(project, edit_plan)
    assert any(b.type == "SECRET_LEAK_DETECTED" for b in report.blockers) is False  # kein Leak vorhanden


def test_bridge_draft_contains_no_raw_llm_responses(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    edit_plan = build_edit_plan_draft_from_confirmed_cut_plan(project)
    serialized = edit_plan.model_dump_json()
    assert "raw_response" not in serialized
    assert "candidates" not in serialized or "raw_llm" not in serialized.lower()


def test_bridge_draft_contains_no_audio_base64(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    edit_plan = build_edit_plan_draft_from_confirmed_cut_plan(project)
    serialized = edit_plan.model_dump_json()
    assert "audio_base64" not in serialized

    report = validate_edit_plan_bridge(project, edit_plan)
    assert not any(b.type == "SECRET_LEAK_DETECTED" for b in report.blockers)


# --- 32-34: UI ---


def test_ui_shows_edit_plan_bridge_section(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _build_confirmed_project(tmp_path)
    _patch_project_selector(project, monkeypatch)

    subheaders: list[str] = []
    monkeypatch.setattr("streamlit.button", lambda *a, **k: False)
    monkeypatch.setattr("streamlit.rerun", lambda: None)
    monkeypatch.setattr("streamlit.subheader", lambda text: subheaders.append(text))

    render_cut_plan_page()
    assert any("EditPlan Bridge" in text for text in subheaders)


def test_ui_shows_build_button_only_when_confirmed_cut_plan_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    # Bewusst KEIN confirm_cut_plan() -> kein bestätigter Cut Plan vorhanden.

    _patch_project_selector(project, monkeypatch)

    button_labels: list[str] = []

    def _fake_button(label, *args, **kwargs):
        button_labels.append(label)
        return False

    monkeypatch.setattr("streamlit.button", _fake_button)
    monkeypatch.setattr("streamlit.rerun", lambda: None)

    render_cut_plan_page()
    assert not any("EditPlan Draft" in label for label in button_labels)


def test_ui_shows_bridge_report_and_trace_after_creation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _build_confirmed_project(tmp_path)
    edit_plan = build_edit_plan_draft_from_confirmed_cut_plan(project)
    save_edit_plan_bridge_draft(project, edit_plan)
    from otio_app.services.voiceover_generation.cut_plan_confirm_service import load_confirmed_cut_plan

    confirmed = load_confirmed_cut_plan(project)
    trace = build_edit_plan_bridge_trace(project, confirmed, edit_plan)
    save_edit_plan_bridge_trace(project, trace)
    validate_edit_plan_bridge(project, edit_plan)

    _patch_project_selector(project, monkeypatch)

    metrics: list[tuple] = []
    monkeypatch.setattr("streamlit.button", lambda *a, **k: False)
    monkeypatch.setattr("streamlit.rerun", lambda: None)
    monkeypatch.setattr("streamlit.metric", lambda label, value: metrics.append((label, value)))

    render_cut_plan_page()
    assert any(label == "Validation Status" for label, _ in metrics)
    assert any(label == "TimelineItems" for label, _ in metrics)


# --- 35-36: Struktureller Schutz / Regression ---

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


def test_cut_plan_and_bridge_modules_never_reference_forbidden_production_symbols() -> None:
    import otio_app.services.voiceover_generation.cut_plan_asset_selector as asset_selector_module
    import otio_app.services.voiceover_generation.cut_plan_builder as builder_module
    import otio_app.services.voiceover_generation.cut_plan_confirm_service as confirm_module
    import otio_app.services.voiceover_generation.cut_plan_edit_plan_bridge as edit_plan_bridge_module
    import otio_app.services.voiceover_generation.cut_plan_edit_plan_models as edit_plan_models_module
    import otio_app.services.voiceover_generation.cut_plan_edit_plan_trace as edit_plan_trace_module
    import otio_app.services.voiceover_generation.cut_plan_supplement_bridge as supplement_bridge_module
    import otio_app.services.voiceover_generation.cut_plan_timeline_service as timeline_module
    import otio_app.services.voiceover_generation.cut_plan_trace_service as trace_module
    import otio_app.services.voiceover_generation.cut_plan_validator as validator_module
    import otio_app.services.voiceover_generation.cut_plan_visual_coverage as coverage_module
    import otio_app.ui.voiceover_generation.cut_plan_tab as tab_module

    for module in (
        asset_selector_module, builder_module, confirm_module, edit_plan_bridge_module, edit_plan_models_module,
        edit_plan_trace_module, supplement_bridge_module, timeline_module, trace_module, validator_module,
        coverage_module, tab_module,
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


def test_edit_plan_bridge_dir_is_isolated_under_cut_plan(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    bridge_dir = get_cut_plan_edit_plan_bridge_dir(project.language_work_dir_path)
    normalized = str(bridge_dir).replace("\\", "/")
    assert normalized.endswith("voiceover_generation/cut_plan/edit_plan_bridge")


def test_is_edit_plan_bridge_stale_true_without_confirmed_cut_plan(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    edit_plan = build_edit_plan_draft_from_confirmed_cut_plan(project)
    save_edit_plan_bridge_draft(project, edit_plan)

    from otio_app.project_layout import get_cut_plan_confirmed_path

    get_cut_plan_confirmed_path(project.language_work_dir_path).unlink()
    loaded = load_edit_plan_bridge_draft(project)
    assert is_edit_plan_bridge_stale(project, loaded) is True


def test_load_edit_plan_bridge_validation_report_returns_saved_report(tmp_path: Path) -> None:
    from otio_app.services.voiceover_generation.cut_plan_edit_plan_bridge import (
        build_bridge_audio_plan_from_confirmed_cut_plan,
        save_bridge_audio_plan,
    )

    project = _build_confirmed_project(tmp_path)
    edit_plan = build_edit_plan_draft_from_confirmed_cut_plan(project)
    save_edit_plan_bridge_draft(project, edit_plan)
    save_bridge_audio_plan(project, build_bridge_audio_plan_from_confirmed_cut_plan(project))
    validate_edit_plan_bridge(project, edit_plan)

    loaded_report = load_edit_plan_bridge_validation_report(project)
    assert loaded_report is not None
    assert loaded_report.status == EDIT_PLAN_BRIDGE_VALIDATION_STATUS_PASS
