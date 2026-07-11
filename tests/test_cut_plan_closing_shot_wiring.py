"""Nutzervorgabe (Juli 2026, "wir haben gar kein closing asset nach dem
letzten Satz, der die Pause ausfüllt"): End-to-End-Test für das Cut-Plan-
Wiring des Closing Shots (siehe cut_plan_timeline_service.
_closing_item_skeleton) — verifiziert über die VOLLE Pipeline (Draft ->
Asset-Auswahl -> Visual-Coverage-Fix -> Validierung), dass der Closing
Shot tatsächlich sowohl den kurzen Audio-Tail nach dem letzten Satz als
auch die anschließende Sektionspause visuell abdeckt, wodurch der vorher
unlösbare BLACK_GAP_DURING_VOICEOVER-Blocker verschwindet — ohne dass
dafür das zuletzt gesprochene Satz-Video lang genug sein müsste."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis
from otio_app.defaults import CUT_PLAN_ERROR_BLACK_GAP_DURING_VOICEOVER
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import get_folder_inventory_path
from otio_app.services.voiceover_generation.cut_plan_builder import (
    apply_asset_selection_to_draft,
    build_cut_plan_draft,
    save_cut_plan_draft,
    validate_cut_plan_draft,
)
from otio_app.services.voiceover_generation.cut_plan_models import CutPlanSettings
from otio_app.services.voiceover_generation.cut_plan_settings_service import save_cut_plan_settings
from otio_app.services.voiceover_generation.final_plan_service import (
    save_confirmed_voiceover_project_plan,
)
from otio_app.services.voiceover_generation.models import (
    AlignmentItem,
    ClosingVisualPlan,
    ConfirmedFolderPlanItem,
    ConfirmedVoiceoverProjectPlan,
    SentenceItem,
)

_ASSET_SELECTOR_MODULE = "otio_app.services.voiceover_generation.cut_plan_asset_selector"
_VISUAL_COVERAGE_MODULE = "otio_app.services.voiceover_generation.cut_plan_visual_coverage"

FOLDER_A = "Grand Canyon"
FOLDER_B = "Antelope Canyon"


def _make_project(tmp_path: Path) -> Project:
    project_root = tmp_path / "USA"
    (project_root / FOLDER_A).mkdir(parents=True)
    (project_root / FOLDER_B).mkdir(parents=True)
    return Project(
        id="cut-plan-closing-wiring-project",
        name="Cut Plan Closing Wiring Test",
        project_root=str(project_root),
        work_dir=str(project_root / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=[FOLDER_A, FOLDER_B],
        selected_asset_subdirs=[FOLDER_A, FOLDER_B],
    )


def _write_inventory(project: Project, folder_name: str, filenames: list[str]) -> None:
    folder_dir = project.project_root_path / folder_name
    folder_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    for filename in filenames:
        (folder_dir / filename).write_bytes(b"FAKE_MEDIA_BYTES")
        entries.append(AssetMediaAnalysis(path=f"{folder_name}/{filename}", description=filename))
    inv_path = get_folder_inventory_path(project.work_dir_path, folder_name)
    inv_path.parent.mkdir(parents=True, exist_ok=True)
    inv_path.write_text(
        AssetFolderAnalysis(folder=folder_name, assets=entries).model_dump_json(indent=2), encoding="utf-8"
    )


def _write_audio_files(project: Project, names: list[str]) -> list[Path]:
    audio_dir = project.work_dir_path / "voiceover_generation" / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name in names:
        path = audio_dir / name
        path.write_bytes(b"FAKE_AUDIO_BYTES")
        paths.append(path)
    return paths


def _build_two_folder_project(tmp_path: Path, *, with_closing_plan: bool) -> Project:
    """Zwei Ordner, Pause dazwischen wie beim Nutzer real beobachtet (2.5s).
    Der letzte Satz von FOLDER_A reicht exakt bis ans Audio-Ende (kein
    natürlicher Audio-Tail) UND nutzt ein Video, dessen (gemockte) reale
    Dauer exakt der benötigten Audiodauer entspricht — kann also NICHT von
    extend_section_end_visuals_over_pauses verlängert werden, wenn es
    (mangels Closing Shot) selbst das 'letzte VisualSegment vor Audio-Ende'
    wäre. Der optionale Closing Shot nutzt bewusst ein BILD (Bilder können
    immer verlängert werden, siehe cut_plan_visual_coverage.py) — genau der
    von Nutzer vorgeschlagene Fix."""
    project = _make_project(tmp_path)
    _write_inventory(project, FOLDER_A, ["clip_sentence_a.mp4", "photo_closing_a.jpg"])
    _write_inventory(project, FOLDER_B, ["clip_sentence_b.mp4"])
    audio_a, audio_b = _write_audio_files(project, ["folder_a.mp3", "folder_b.mp3"])

    folder_a = ConfirmedFolderPlanItem(
        folder_name=FOLDER_A,
        order_index=1,
        audio_path=str(audio_a),
        audio_duration_sec=5.0,
        sentence_items=[
            SentenceItem(
                sentence_id="sentence_001",
                text="Ein Satz über den Grand Canyon.",
                primary_asset_id="asset_clip_sentence_a",
            )
        ],
        alignment_items=[
            AlignmentItem(sentence_id="sentence_001", audio_start_sec=0.0, audio_end_sec=5.0, duration_sec=5.0)
        ],
        closing_visual_plan=(
            ClosingVisualPlan(
                visual_intent="aerial establishing shot to close the section",
                primary_asset_id="asset_photo_closing_a",
            )
            if with_closing_plan
            else ClosingVisualPlan()
        ),
    )
    folder_b = ConfirmedFolderPlanItem(
        folder_name=FOLDER_B,
        order_index=2,
        audio_path=str(audio_b),
        audio_duration_sec=5.0,
        sentence_items=[
            SentenceItem(
                sentence_id="sentence_001",
                text="Ein Satz über Antelope Canyon.",
                primary_asset_id="asset_clip_sentence_b",
            )
        ],
        alignment_items=[
            AlignmentItem(sentence_id="sentence_001", audio_start_sec=0.0, audio_end_sec=5.0, duration_sec=5.0)
        ],
    )
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id, project_title="Test", status="AUDIO_READY", folders=[folder_a, folder_b]
    )
    save_confirmed_voiceover_project_plan(project, plan)
    save_cut_plan_settings(
        project,
        CutPlanSettings(
            project_id=project.id,
            initial_audio_offset_sec=1.0,
            pause_between_sections_sec=2.5,
        ),
    )
    draft = build_cut_plan_draft(project)
    save_cut_plan_draft(project, draft)
    return project


def test_closing_shot_removes_black_gap_for_section_pause(tmp_path: Path) -> None:
    project = _build_two_folder_project(tmp_path, with_closing_plan=True)
    with (
        patch(f"{_ASSET_SELECTOR_MODULE}.probe_duration_seconds", return_value=8.0),
        patch(f"{_VISUAL_COVERAGE_MODULE}.probe_duration_seconds", return_value=8.0),
    ):
        apply_asset_selection_to_draft(project)
        updated, report = validate_cut_plan_draft(project)

    assert not any(error.type == CUT_PLAN_ERROR_BLACK_GAP_DURING_VOICEOVER for error in report.blockers)

    closing_item = next(item for item in updated.items if item.is_closing_shot)
    assert closing_item.chosen_asset_id == "asset_photo_closing_a"
    assert closing_item.planned_visual_segments
    segment = closing_item.planned_visual_segments[0]
    assert "section_pause_hold" in segment.reason.split("+")

    folder_b_audio = next(a for a in updated.audio_items if a.folder_name == FOLDER_B)
    assert segment.timeline_out_sec == pytest.approx(folder_b_audio.timeline_start_sec)


def test_closing_shot_does_not_overlap_with_last_sentence_segment(tmp_path: Path) -> None:
    """Da der letzte Satz exakt bis ans Audio-Ende reicht (kein Audio-Tail),
    muss der Closing Shot etwas Zeit vom letzten Satz-Segment 'entleihen'
    (Floor-Mindestdauer) — resolve_timeline_overlaps muss das automatisch
    ohne Überlappung oder neue Lücke auflösen."""
    project = _build_two_folder_project(tmp_path, with_closing_plan=True)
    with (
        patch(f"{_ASSET_SELECTOR_MODULE}.probe_duration_seconds", return_value=8.0),
        patch(f"{_VISUAL_COVERAGE_MODULE}.probe_duration_seconds", return_value=8.0),
    ):
        apply_asset_selection_to_draft(project)

    from otio_app.services.voiceover_generation.cut_plan_builder import load_cut_plan_draft
    from otio_app.services.voiceover_generation.cut_plan_visual_coverage import all_segments_sorted

    draft = load_cut_plan_draft(project)
    all_segments = all_segments_sorted(draft)
    for (segment_a, _), (segment_b, _) in zip(all_segments, all_segments[1:]):
        assert segment_b.timeline_in_sec >= segment_a.timeline_out_sec - 0.01


def test_without_closing_shot_black_gap_remains_for_section_pause(tmp_path: Path) -> None:
    """Gegenprobe (Ausgangszustand, den der Nutzer gemeldet hat): OHNE
    ClosingVisualPlan bleibt die Sektionspause offen, weil das letzte
    Satz-Item selbst (Video, exakt passende Dauer) nicht über die Pause
    hinaus verlängert werden kann."""
    project = _build_two_folder_project(tmp_path, with_closing_plan=False)
    with (
        patch(f"{_ASSET_SELECTOR_MODULE}.probe_duration_seconds", return_value=8.0),
        patch(f"{_VISUAL_COVERAGE_MODULE}.probe_duration_seconds", return_value=8.0),
    ):
        apply_asset_selection_to_draft(project)
        _, report = validate_cut_plan_draft(project)

    assert any(error.type == CUT_PLAN_ERROR_BLACK_GAP_DURING_VOICEOVER for error in report.blockers)


def test_closing_shot_requests_supplement_when_needed(tmp_path: Path) -> None:
    """Ein Closing Shot mit needs_supplement_asset=true (statt Asset) muss
    als eigenständiger, supplementierbarer Blocker sichtbar werden, statt
    stillschweigend zu fehlen — dieselbe Pipeline wie für Sätze."""
    project = _build_two_folder_project(tmp_path, with_closing_plan=False)
    from otio_app.services.voiceover_generation.final_plan_service import load_confirmed_voiceover_project_plan

    source_plan = load_confirmed_voiceover_project_plan(project)
    updated_plan = source_plan.model_copy(
        update={
            "folders": [
                folder.model_copy(
                    update={
                        "closing_visual_plan": ClosingVisualPlan(
                            needs_supplement_asset=True,
                            supplement_reason="Kein passendes Motiv lokal vorhanden.",
                            supplement_search_hint="Grand Canyon aerial dusk",
                        )
                    }
                )
                if folder.folder_name == FOLDER_A
                else folder
                for folder in source_plan.folders
            ]
        }
    )
    save_confirmed_voiceover_project_plan(project, updated_plan)
    new_draft = build_cut_plan_draft(project)
    save_cut_plan_draft(project, new_draft)

    with (
        patch(f"{_ASSET_SELECTOR_MODULE}.probe_duration_seconds", return_value=8.0),
        patch(f"{_VISUAL_COVERAGE_MODULE}.probe_duration_seconds", return_value=8.0),
    ):
        updated = apply_asset_selection_to_draft(project)

    closing_item = next(item for item in updated.items if item.is_closing_shot)
    assert closing_item.needs_supplement_asset is True
    assert closing_item.asset_selection_status == "SUPPLEMENT_REQUIRED"
