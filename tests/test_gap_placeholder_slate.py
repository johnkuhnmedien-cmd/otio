"""Fix 2: Gap-/Bridge-Placeholder als ffmpeg-Slate."""

from __future__ import annotations

from pathlib import Path

from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.services.without_voiceover_enhanced.media_hold import (
    ensure_gap_placeholder_slate,
)
from otio_app.services.without_voiceover_enhanced.otio_export_service import (
    validate_resolved_timeline_for_production,
)
from otio_app.services.without_voiceover_enhanced.models import (
    ResolvedShot,
    ResolvedTimelineDocument,
)
from otio_app.services.without_voiceover_enhanced.paths import placeholders_dir
from otio_app.services.without_voiceover_enhanced.unified_timeline_service import (
    TimedSlot,
    _placeholder_resolved_shot,
)


def _project(tmp_path: Path) -> Project:
    work = tmp_path / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    return Project(
        id="p",
        name="p",
        project_root=str(tmp_path),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        asset_subdir_names=["A"],
        selected_asset_subdirs=["A"],
    )


def test_ensure_gap_placeholder_slate_writes_video(tmp_path: Path) -> None:
    project = _project(tmp_path)
    out = ensure_gap_placeholder_slate(
        project,
        shot_id="Yosemite_slot_011",
        gap_id="gap_yosemite_011",
        needed_visual="close-up waterfall mist",
        start_seconds=12.0,
        end_seconds=15.5,
        fps=25.0,
    )
    assert out.is_file()
    assert out.stat().st_size > 0
    assert out.parent == placeholders_dir(project)
    # Cache-Hit
    again = ensure_gap_placeholder_slate(
        project,
        shot_id="Yosemite_slot_011",
        gap_id="gap_yosemite_011",
        needed_visual="close-up waterfall mist",
        start_seconds=12.0,
        end_seconds=15.5,
        fps=25.0,
    )
    assert again == out


def test_placeholder_resolved_shot_sets_path_and_flags(tmp_path: Path) -> None:
    project = _project(tmp_path)
    timed = TimedSlot(
        slot_id="bridge_001",
        start_seconds=10.0,
        end_seconds=12.0,
        start_boundary_id="b0",
        end_boundary_id="b1",
        cut_alignment="sentence_boundary",
        asset_id=None,
        asset_fit="none",
        asset_fit_reason="Kapitelübergang",
        coverage_gap_id="gap_bridge_001",
        narrative_function="chapter_transition",
        source_range_intent="",
        visual_intent="chapter transition",
        needed_visual="chapter transition / hold",
    )
    shot = _placeholder_resolved_shot(project, timed, fps=25.0)
    assert shot.is_placeholder is True
    assert shot.open_gap is True
    assert shot.hold_mode == "placeholder_slate"
    assert shot.coverage_gap_id == "gap_bridge_001"
    assert Path(shot.resolved_media_path).is_file()


def test_production_gate_blocks_placeholder_even_with_path(tmp_path: Path) -> None:
    project = _project(tmp_path)
    slate = ensure_gap_placeholder_slate(
        project,
        shot_id="slot_x",
        gap_id="gap_x",
        needed_visual="x",
        start_seconds=0.0,
        end_seconds=2.0,
        fps=25.0,
    )
    resolved = ResolvedTimelineDocument(
        script_version="v1",
        fps=25.0,
        total_duration_seconds=2.0,
        shots=[
            ResolvedShot(
                shot_id="slot_x",
                asset_id="",
                timeline_start_seconds=0.0,
                timeline_end_seconds=2.0,
                source_start_seconds=0.0,
                source_end_seconds=2.0,
                resolved_media_path=str(slate),
                resolved_media_kind="video",
                hold_mode="placeholder_slate",
                open_gap=True,
                is_placeholder=True,
                coverage_gap_id="gap_x",
            )
        ],
    )
    errors = validate_resolved_timeline_for_production(project, resolved)
    assert any("Placeholder" in e or "offener Gap" in e for e in errors)
    assert not any("resolved_media_path fehlt" in e for e in errors)
