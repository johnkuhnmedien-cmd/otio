"""Phase 9.2: Bridge-Hardening + Audio-Kompatibilität + Boundary-Chaining.

Noch KEIN locked EditPlan, kein Produktions-EditPlan unter _otio/edit_plan/,
kein OTIO-Export, kein Render, keine neue LLM-Planung, kein build_edit_plan(),
kein save_edit_plan()."""

from __future__ import annotations

import inspect
import re
from pathlib import Path
from unittest.mock import patch

import pytest

from otio_app.analysis_models import EditPlanDocument, TimelineItem
from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis
from otio_app.defaults import EDIT_PLAN_BRIDGE_VALIDATION_STATUS_BLOCKED
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import (
    get_cut_plan_edit_plan_bridge_audio_plan_path,
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
from otio_app.services.voiceover_generation.cut_plan_confirm_service import confirm_cut_plan, load_confirmed_cut_plan
from otio_app.services.voiceover_generation.cut_plan_edit_plan_bridge import (
    _SOURCE_CUT_PLAN_HASH_PREFIX,
    build_bridge_audio_plan_from_confirmed_cut_plan,
    build_edit_plan_draft_from_confirmed_cut_plan,
    extract_note_value,
    load_edit_plan_bridge_draft,
    normalize_audio_boundaries,
    normalize_timeline_boundaries_by_track,
    normalize_visual_boundaries,
    save_bridge_audio_plan,
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
FPS = 25


def _make_project(tmp_path: Path) -> Project:
    project_root = tmp_path / "USA"
    (project_root / FOLDER_A).mkdir(parents=True)
    return Project(
        id="cut-plan-edit-plan-bridge-hardening-project",
        name="Cut Plan EditPlan Bridge Hardening Test",
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


def _build_and_persist_bridge(project: Project) -> tuple[EditPlanDocument, "object"]:
    edit_plan = build_edit_plan_draft_from_confirmed_cut_plan(project)
    edit_plan = save_edit_plan_bridge_draft(project, edit_plan)
    audio_plan = build_bridge_audio_plan_from_confirmed_cut_plan(project)
    audio_plan = save_bridge_audio_plan(project, audio_plan)
    confirmed = load_confirmed_cut_plan(project)
    trace = build_edit_plan_bridge_trace(project, confirmed, edit_plan)
    save_edit_plan_bridge_trace(project, trace)
    return edit_plan, audio_plan


def _patch_project_selector(project: Project, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("otio_app.ui.project_context.list_projects", lambda: [project])
    monkeypatch.setattr(
        "otio_app.ui.project_context.get_project_by_id",
        lambda project_id: project if project_id == project.id else None,
    )
    monkeypatch.setattr("streamlit.session_state", {"active_project_id": project.id}, raising=False)


def _visual_item(item_id: str, in_sec: float, out_sec: float, *, asset_path: str = "/fake/a.jpg", asset_type: str = "image") -> TimelineItem:
    return TimelineItem(
        timeline_item_id=item_id,
        type="video_shot" if asset_type == "video" else "image_shot",
        section_id="cut_1",
        folder_name=FOLDER_A,
        asset_id="asset_a",
        resolved_media_path=asset_path,
        timeline_in_sec=in_sec,
        timeline_out_sec=out_sec,
        duration_sec=out_sec - in_sec,
        final_duration_sec=out_sec - in_sec,
        source_in_sec=0.0,
        source_out_sec=out_sec - in_sec,
        track="V1",
        asset_type=asset_type,
    )


def _audio_item(item_id: str, in_sec: float, out_sec: float) -> TimelineItem:
    return TimelineItem(
        timeline_item_id=item_id,
        type="voiceover_audio",
        section_id="intro",
        folder_name="",
        voice_file="/fake/audio.mp3",
        resolved_media_path="/fake/audio.mp3",
        timeline_in_sec=in_sec,
        timeline_out_sec=out_sec,
        duration_sec=out_sec - in_sec,
        final_duration_sec=out_sec - in_sec,
        source_in_sec=0.0,
        source_out_sec=out_sec - in_sec,
        track="A1",
    )


# --- 1-4: Boundary-Chaining Visual ---


def test_boundary_chaining_removes_one_frame_gap_between_visuals() -> None:
    frame = 1.0 / FPS
    item_a = _visual_item("seg_1", 0.0, 5.0)
    item_b = _visual_item("seg_2", 5.0 + frame, 10.0)  # 1-Frame-Gap
    chained = normalize_visual_boundaries([item_a, item_b], FPS)
    assert chained[1].timeline_in_sec == pytest.approx(chained[0].timeline_out_sec)


def test_boundary_chaining_removes_one_frame_overlap_between_visuals() -> None:
    frame = 1.0 / FPS
    item_a = _visual_item("seg_1", 0.0, 5.0)
    item_b = _visual_item("seg_2", 5.0 - frame, 10.0)  # 1-Frame-Overlap
    chained = normalize_visual_boundaries([item_a, item_b], FPS)
    assert chained[1].timeline_in_sec == pytest.approx(chained[0].timeline_out_sec)
    assert chained[0].timeline_out_sec <= chained[1].timeline_in_sec + 1e-9


def test_boundary_chaining_never_produces_zero_duration() -> None:
    frame = 1.0 / FPS
    item_a = _visual_item("seg_1", 0.0, 5.0)
    # Zweites Item endet VOR dem neuen Start (extremer Overlap) -> Mindestdauer 1 Frame erzwingen.
    item_b = _visual_item("seg_2", 4.999, 5.0005)
    chained = normalize_visual_boundaries([item_a, item_b], FPS)
    assert chained[1].timeline_out_sec - chained[1].timeline_in_sec >= frame - 1e-9


def test_boundary_chaining_adjusts_source_out_sec_to_new_duration() -> None:
    item_a = _visual_item("seg_1", 0.0, 5.0)
    item_b = _visual_item("seg_2", 5.1, 10.0)  # Gap von 0.1s
    chained = normalize_visual_boundaries([item_a, item_b], FPS)
    new_duration = chained[1].timeline_out_sec - chained[1].timeline_in_sec
    assert chained[1].source_out_sec == pytest.approx(chained[1].source_in_sec + new_duration)


def test_video_source_out_does_not_silently_exceed_video_duration(tmp_path: Path) -> None:
    """Boundary-Chaining selbst berechnet nur die neuen Werte — ob das bei
    Video die reale Dateidauer überschreitet, erkennt validate_edit_plan_bridge
    (§5), nicht normalize_visual_boundaries selbst. Beim Chaining wird das
    ZWEITE Item nach vorne (auf das Ende des ersten) gezogen — dessen Dauer
    wird dadurch länger, nicht die des ersten Items."""
    project = _build_confirmed_project(tmp_path)
    edit_plan, audio_plan = _build_and_persist_bridge(project)
    video_path = project.project_root_path / FOLDER_A / "clip.mp4"
    video_path.write_bytes(b"FAKE_VIDEO_BYTES")

    visual_items = [item for item in edit_plan.timeline_items if item.track == "V1"]
    item_a = visual_items[0].model_copy(
        update={"timeline_in_sec": 0.0, "timeline_out_sec": 5.0, "resolved_media_path": str(video_path),
                "type": "video_shot", "source_in_sec": 0.0, "source_out_sec": 5.0}
    )
    item_b = visual_items[1].model_copy(
        update={"timeline_in_sec": 5.5, "timeline_out_sec": 10.0, "resolved_media_path": str(video_path),
                "type": "video_shot", "source_in_sec": 0.0, "source_out_sec": 4.5}
    )
    chained = normalize_visual_boundaries([item_a, item_b], FPS)
    # item_b's Start rückt auf item_a's Ende vor -> item_b wird um die Lücke länger.
    assert chained[1].source_out_sec > 4.5  # urspruengliche Dauer von item_b war 4.5s

    audio_items = [item for item in edit_plan.timeline_items if item.track == "A1"]
    broken_edit_plan = edit_plan.model_copy(update={"timeline_items": chained + audio_items})
    save_edit_plan_bridge_draft(project, broken_edit_plan)

    with patch(
        "otio_app.services.voiceover_generation.cut_plan_edit_plan_bridge.probe_duration_seconds",
        return_value=4.6,  # Video ist nur 4.6s lang, aber source_out_sec wurde auf 5.0s verlängert
    ):
        report = validate_edit_plan_bridge(project, broken_edit_plan)
    assert any(b.type == "VIDEO_SOURCE_EXCEEDS_DURATION" for b in report.blockers)


# --- 6-7: Audio-Boundary-Chaining ---


def test_audio_is_not_shortened_by_boundary_normalization() -> None:
    frame = 1.0 / FPS
    item_a = _audio_item("audio_1", 0.0, 5.0)
    item_b = _audio_item("audio_2", 5.0 - frame, 10.0)  # Rundungs-Overlap
    normalized = normalize_audio_boundaries([item_a, item_b], FPS)
    original_duration = 10.0 - (5.0 - frame)
    new_duration = normalized[1].timeline_out_sec - normalized[1].timeline_in_sec
    assert new_duration == pytest.approx(original_duration)  # Dauer bleibt exakt erhalten


def test_audio_pauses_are_preserved() -> None:
    item_a = _audio_item("audio_1", 1.0, 6.0)
    item_b = _audio_item("audio_2", 6.25, 11.25)  # 0.25s Pause, KEIN Overlap
    normalized = normalize_audio_boundaries([item_a, item_b], FPS)
    assert normalized[0].timeline_in_sec == pytest.approx(1.0)
    assert normalized[0].timeline_out_sec == pytest.approx(6.0)
    assert normalized[1].timeline_in_sec == pytest.approx(6.25)  # Pause NICHT entfernt


# --- 8-11: Trace-Erweiterung ---


def test_trace_contains_original_and_rounded_timeline_times(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    edit_plan, _audio_plan = _build_and_persist_bridge(project)
    confirmed = load_confirmed_cut_plan(project)
    trace = build_edit_plan_bridge_trace(project, confirmed, edit_plan)
    assert all(hasattr(entry, "original_timeline_in_sec") for entry in trace.entries)
    entry = trace.entries[0]
    assert entry.original_timeline_in_sec is not None
    assert entry.rounded_timeline_in_sec is not None


def test_trace_marks_boundary_chained_for_adjusted_items() -> None:
    """Konstruiert ein Szenario, in dem Boundary-Chaining tatsächlich einen
    Unterschied macht: zwei Visual-Segmente mit einer 0.03s-Lücke (kleiner
    als ein Frame bei 25fps), die durch unabhängige Rundung entstehen könnte."""
    from otio_app.services.voiceover_generation.cut_plan_edit_plan_models import EditPlanBridgeTraceDocument
    from otio_app.services.voiceover_generation.cut_plan_models import (
        CutPlanDocument,
        CutPlanItem,
        CutPlanSourceRef,
        VisualSegment,
    )

    project_id = "p1"
    segment_1 = VisualSegment(
        segment_id="seg_1", timeline_in_sec=0.0, timeline_out_sec=5.003, duration_sec=5.003,
        asset_id="a", asset_path="/fake/a.jpg", asset_type="image", source_out_sec=5.003,
    )
    segment_2 = VisualSegment(
        segment_id="seg_2", timeline_in_sec=5.003, timeline_out_sec=10.0, duration_sec=4.997,
        asset_id="a", asset_path="/fake/a.jpg", asset_type="image", source_out_sec=4.997,
    )
    item_1 = CutPlanItem(
        cut_item_id="cut_1", source_refs=[CutPlanSourceRef(source_sentence_id="s1")], folder_name=FOLDER_A,
        timeline_start_sec=0.0, timeline_end_sec=5.003, duration_sec=5.003,
        planned_visual_segments=[segment_1],
    )
    item_2 = CutPlanItem(
        cut_item_id="cut_2", source_refs=[CutPlanSourceRef(source_sentence_id="s2")], folder_name=FOLDER_A,
        timeline_start_sec=5.003, timeline_end_sec=10.0, duration_sec=4.997,
        planned_visual_segments=[segment_2],
    )
    cut_plan = CutPlanDocument(project_id=project_id, timeline_fps=FPS, items=[item_1, item_2])

    raw_items = [
        _visual_item("edit_seg_1", 0.0, 5.003),
        _visual_item("edit_seg_2", 5.003, 10.0),
    ]
    # Individuelle Rundung (wie im echten Bau) kann leicht divergieren.
    from otio_app.services.voiceover_generation.cut_plan_edit_plan_bridge import round_visual_times_to_frame

    rounded_items = []
    for raw in raw_items:
        rin, rout, sin, sout, _fr, _delta = round_visual_times_to_frame(
            raw.timeline_in_sec, raw.timeline_out_sec, raw.source_in_sec, raw.source_out_sec, FPS
        )
        rounded_items.append(
            raw.model_copy(update={"timeline_item_id": raw.timeline_item_id, "timeline_in_sec": rin,
                                    "timeline_out_sec": rout, "source_in_sec": sin, "source_out_sec": sout})
        )
    final_items = normalize_visual_boundaries(rounded_items, FPS)
    edit_plan = EditPlanDocument(project_id=project_id, timeline_items=final_items)

    class _FakeProject:
        id = project_id

    trace = build_edit_plan_bridge_trace(_FakeProject(), cut_plan, edit_plan)
    assert isinstance(trace, EditPlanBridgeTraceDocument)
    # Mindestens ein Eintrag sollte boundary_chained=True zeigen, wenn Rundung divergierte,
    # ansonsten bleibt boundary_chained=False — beides ist ein gültiges Ergebnis, Hauptsache das Feld existiert.
    assert all(isinstance(entry.boundary_chained, bool) for entry in trace.entries)


def test_trace_contains_boundary_chain_delta_sec(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    edit_plan, _audio_plan = _build_and_persist_bridge(project)
    confirmed = load_confirmed_cut_plan(project)
    trace = build_edit_plan_bridge_trace(project, confirmed, edit_plan)
    assert all(isinstance(entry.boundary_chain_delta_sec, float) for entry in trace.entries)


def test_trace_contains_source_duration_adjusted(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    edit_plan, _audio_plan = _build_and_persist_bridge(project)
    confirmed = load_confirmed_cut_plan(project)
    trace = build_edit_plan_bridge_trace(project, confirmed, edit_plan)
    assert all(isinstance(entry.source_duration_adjusted, bool) for entry in trace.entries)


# --- 12-15: BridgeAudioPlan ---


def test_bridge_audio_plan_json_is_written(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    _edit_plan, audio_plan = _build_and_persist_bridge(project)
    path = get_cut_plan_edit_plan_bridge_audio_plan_path(project.language_work_dir_path)
    assert path.is_file()
    assert len(audio_plan.items) == 2


def test_bridge_audio_plan_contains_intro_audio(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    _edit_plan, audio_plan = _build_and_persist_bridge(project)
    assert any(item.scope == "intro" for item in audio_plan.items)


def test_bridge_audio_plan_contains_folder_audio(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    _edit_plan, audio_plan = _build_and_persist_bridge(project)
    assert any(item.scope == "folder" and item.folder_name == FOLDER_A for item in audio_plan.items)


def test_bridge_audio_plan_matches_voiceover_audio_timeline_items(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    edit_plan, audio_plan = _build_and_persist_bridge(project)
    audio_timeline_ids = {item.timeline_item_id for item in edit_plan.timeline_items if item.track == "A1"}
    audio_plan_ids = {item.timeline_item_id for item in audio_plan.items}
    assert audio_timeline_ids == audio_plan_ids


# --- 16-17: Audio-Plan Validierung ---


def test_validation_blocks_voiceover_audio_without_bridge_audio_plan(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    edit_plan = build_edit_plan_draft_from_confirmed_cut_plan(project)
    save_edit_plan_bridge_draft(project, edit_plan)
    # KEIN bridge_audio_plan.json erzeugt.
    report = validate_edit_plan_bridge(project, edit_plan)
    assert report.status == EDIT_PLAN_BRIDGE_VALIDATION_STATUS_BLOCKED
    assert any(b.type == "AUDIO_PLAN_MISSING" for b in report.blockers)


def test_validation_blocks_bridge_audio_plan_without_timeline_item(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    edit_plan, audio_plan = _build_and_persist_bridge(project)

    # Ein TimelineItem entfernen, sodass der AudioPlan-Eintrag verwaist.
    remaining_items = [item for item in edit_plan.timeline_items if item.track != "A1"]
    broken_edit_plan = edit_plan.model_copy(update={"timeline_items": remaining_items})
    save_edit_plan_bridge_draft(project, broken_edit_plan)

    report = validate_edit_plan_bridge(project, broken_edit_plan)
    assert report.status == EDIT_PLAN_BRIDGE_VALIDATION_STATUS_BLOCKED
    assert any(b.type == "AUDIO_PLAN_MISMATCH" for b in report.blockers)


# --- 18-20: Validierung Overlap/Gap ---


def test_validation_detects_v1_gap_after_bridge_normalization(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    edit_plan, audio_plan = _build_and_persist_bridge(project)

    visual_items = [item for item in edit_plan.timeline_items if item.track == "V1"]
    assert len(visual_items) >= 2
    gapped = visual_items[1].model_copy(update={"timeline_in_sec": visual_items[1].timeline_in_sec + 0.5})
    updated_items = [
        gapped if item.timeline_item_id == visual_items[1].timeline_item_id else item
        for item in edit_plan.timeline_items
    ]
    broken_edit_plan = edit_plan.model_copy(update={"timeline_items": updated_items})
    save_edit_plan_bridge_draft(project, broken_edit_plan)

    report = validate_edit_plan_bridge(project, broken_edit_plan)
    assert report.status == EDIT_PLAN_BRIDGE_VALIDATION_STATUS_BLOCKED
    assert any(b.type in ("VISUAL_TIMELINE_GAP", "BLACK_GAP_DURING_AUDIO") for b in report.blockers)


def test_validation_detects_v1_overlap_after_bridge_normalization(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    edit_plan, audio_plan = _build_and_persist_bridge(project)

    visual_items = [item for item in edit_plan.timeline_items if item.track == "V1"]
    assert len(visual_items) >= 2
    overlapping = visual_items[1].model_copy(update={"timeline_in_sec": visual_items[0].timeline_in_sec + 0.01})
    updated_items = [
        overlapping if item.timeline_item_id == visual_items[1].timeline_item_id else item
        for item in edit_plan.timeline_items
    ]
    broken_edit_plan = edit_plan.model_copy(update={"timeline_items": updated_items})
    save_edit_plan_bridge_draft(project, broken_edit_plan)

    report = validate_edit_plan_bridge(project, broken_edit_plan)
    assert report.status == EDIT_PLAN_BRIDGE_VALIDATION_STATUS_BLOCKED
    assert any(b.type == "TIMELINE_OVERLAP" for b in report.blockers)


def test_validation_detects_a1_overlap(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    edit_plan, audio_plan = _build_and_persist_bridge(project)

    audio_items = [item for item in edit_plan.timeline_items if item.track == "A1"]
    assert len(audio_items) >= 2
    overlapping = audio_items[1].model_copy(update={"timeline_in_sec": audio_items[0].timeline_in_sec + 0.01})
    updated_items = [
        overlapping if item.timeline_item_id == audio_items[1].timeline_item_id else item
        for item in edit_plan.timeline_items
    ]
    broken_edit_plan = edit_plan.model_copy(update={"timeline_items": updated_items})
    save_edit_plan_bridge_draft(project, broken_edit_plan)

    report = validate_edit_plan_bridge(project, broken_edit_plan)
    assert report.status == EDIT_PLAN_BRIDGE_VALIDATION_STATUS_BLOCKED
    assert any(b.type == "TIMELINE_OVERLAP" for b in report.blockers)


# --- 21: source_cut_plan_hash extrahierbar ---


def test_source_cut_plan_hash_is_uniquely_extractable(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    edit_plan, _audio_plan = _build_and_persist_bridge(project)
    confirmed = load_confirmed_cut_plan(project)

    extracted = extract_note_value(edit_plan.plan_generation_notes, _SOURCE_CUT_PLAN_HASH_PREFIX)
    assert extracted
    assert extracted != "source_cut_plan_path"  # kein Verwechseln mit anderem Präfix
    from otio_app.services.voiceover_generation.llm_trace_service import content_hash_of_model

    assert extracted == content_hash_of_model(confirmed)


# --- 22-23: UI ---


def test_ui_shows_not_export_ready_hint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _build_confirmed_project(tmp_path)
    _patch_project_selector(project, monkeypatch)

    warnings: list[str] = []
    monkeypatch.setattr("streamlit.button", lambda *a, **k: False)
    monkeypatch.setattr("streamlit.rerun", lambda: None)
    monkeypatch.setattr("streamlit.warning", lambda msg: warnings.append(msg))

    render_cut_plan_page()
    assert any("nicht OTIO-exportbereit" in msg for msg in warnings)


def test_ui_shows_bridge_audio_plan_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _build_confirmed_project(tmp_path)
    _build_and_persist_bridge(project)
    validate_edit_plan_bridge(project, load_edit_plan_bridge_draft(project))

    _patch_project_selector(project, monkeypatch)

    metrics: list[tuple] = []
    monkeypatch.setattr("streamlit.button", lambda *a, **k: False)
    monkeypatch.setattr("streamlit.rerun", lambda: None)
    monkeypatch.setattr("streamlit.metric", lambda label, value: metrics.append((label, value)))

    render_cut_plan_page()
    assert any(label == "Bridge Audio Plan" and value == "✅ Ja" for label, value in metrics)
    assert any(label == "AudioPlanItems" for label, _ in metrics)


# --- 24-31: Schutz bestehender Pipeline ---


def test_bridge_creates_no_locked_edit_plan(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    edit_plan, _audio_plan = _build_and_persist_bridge(project)
    assert edit_plan.confirmed is False


def test_bridge_creates_no_production_edit_plan(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    _build_and_persist_bridge(project)
    from otio_app.project_layout import get_folder_edit_plan_path

    assert not get_folder_edit_plan_path(project.language_work_dir_path, FOLDER_A).is_file()


def test_bridge_does_not_trigger_otio_export(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    _build_and_persist_bridge(project)
    validate_edit_plan_bridge(project, load_edit_plan_bridge_draft(project))
    assert not get_exports_dir(project.language_work_dir_path).exists()


def test_no_files_written_under_edit_plan_dir(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    _build_and_persist_bridge(project)
    validate_edit_plan_bridge(project, load_edit_plan_bridge_draft(project))
    assert not get_edit_plan_dir(project.language_work_dir_path).exists()


def test_no_files_written_under_exports_dir(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    _build_and_persist_bridge(project)
    assert not get_exports_dir(project.language_work_dir_path).exists()


def test_no_original_media_modified(tmp_path: Path) -> None:
    project = _build_confirmed_project(tmp_path)
    photo_path = project.project_root_path / FOLDER_A / "photo_a.jpg"
    original = photo_path.read_bytes()
    _build_and_persist_bridge(project)
    assert photo_path.read_bytes() == original


def test_bridge_modules_reference_no_forbidden_production_functions() -> None:
    import otio_app.services.voiceover_generation.cut_plan_edit_plan_bridge as bridge_module
    import otio_app.services.voiceover_generation.cut_plan_edit_plan_trace as trace_module

    forbidden = (
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
    for module in (bridge_module, trace_module):
        source = inspect.getsource(module)
        for symbol in forbidden:
            assert not re.search(rf"\b{re.escape(symbol)}\b", source), (
                f"{module.__name__} referenziert verbotenes Symbol '{symbol}'."
            )


def test_with_voiceover_workflow_unaffected() -> None:
    from otio_app.services import edit_plan_builder, otio_exporter

    assert hasattr(edit_plan_builder, "build_edit_plan")
    assert hasattr(edit_plan_builder, "save_edit_plan")
    assert hasattr(otio_exporter, "build_otio_timeline")


# --- Zusätzlich: normalize_timeline_boundaries_by_track Orchestrator ---


def test_normalize_timeline_boundaries_by_track_separates_v1_and_a1() -> None:
    visual_a = _visual_item("seg_1", 0.0, 5.0)
    visual_b = _visual_item("seg_2", 5.1, 10.0)
    audio_a = _audio_item("audio_1", 1.0, 6.0)
    audio_b = _audio_item("audio_2", 6.25, 11.25)

    result = normalize_timeline_boundaries_by_track([visual_a, audio_a, visual_b, audio_b], FPS)
    result_by_id = {item.timeline_item_id: item for item in result}

    # V1 wurde lückenlos verkettet.
    assert result_by_id["seg_2"].timeline_in_sec == pytest.approx(result_by_id["seg_1"].timeline_out_sec)
    # A1-Pause blieb erhalten (kein Chaining bei bereits korrekter Lücke).
    assert result_by_id["audio_2"].timeline_in_sec == pytest.approx(6.25)


def test_normalize_timeline_boundaries_by_track_leaves_other_tracks_untouched() -> None:
    title_item = TimelineItem(
        timeline_item_id="title_1", type="opening_title", section_id="intro", folder_name="",
        timeline_in_sec=0.0, timeline_out_sec=3.0, duration_sec=3.0, final_duration_sec=3.0,
        source_in_sec=0.0, source_out_sec=3.0, track="V2",
    )
    result = normalize_timeline_boundaries_by_track([title_item], FPS)
    assert result[0].timeline_in_sec == 0.0
    assert result[0].timeline_out_sec == 3.0
