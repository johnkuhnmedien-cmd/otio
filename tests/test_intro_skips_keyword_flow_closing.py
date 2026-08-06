"""Intro-Timing darf Keyword-Flow-Closing-Pflichtfelder nicht erzwingen."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.services.without_voiceover_enhanced.intro_cut_service import (
    intro_unified_cut_plan_path,
    resolve_intro_timeline,
)
from otio_app.services.without_voiceover_enhanced.io_utils import write_json
from otio_app.services.without_voiceover_enhanced.models import (
    CutBoundary,
    CutSlot,
    ResolvedAudioSegment,
    ResolvedShot,
    ResolvedTimelineDocument,
    UnifiedCutPlanDocument,
)
from otio_app.services.without_voiceover_enhanced.script_prompts import (
    build_intro_unified_cut_prompt,
)


def _project(tmp_path: Path) -> Project:
    root = tmp_path / "proj"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    return Project(
        name="IntroSkipKF",
        project_root=str(root),
        work_dir=str(work),
        language="en",
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        fps=25.0,
        width=1920,
        height=1080,
        asset_subdir_names=["Intro"],
        selected_asset_subdirs=["Intro"],
    )


def _intro_plan() -> UnifiedCutPlanDocument:
    return UnifiedCutPlanDocument(
        script_version="script-v1",
        voiceover_preroll_sec=4.0,
        voiceover_postroll_sec=6.5,
        closing_fallback_asset_id=None,
        boundaries=[
            CutBoundary(
                cut_id="Intro_cut_000",
                sentence_id="Intro_segment_001__s001",
                position="start",
                alignment="sentence_boundary",
            ),
            CutBoundary(
                cut_id="Intro_cut_001",
                sentence_id="Intro_segment_001__s001",
                position="end",
                alignment="sentence_boundary",
            ),
        ],
        slots=[
            CutSlot(
                slot_id="Intro_slot_001",
                local_asset_id="asset_a",
                asset_fit="strong",
                asset_fit_reason="ok",
                visual_intent="wide",
            )
        ],
    )


def test_intro_prompt_asks_for_fallback_id_not_kf_fit_fields() -> None:
    prompt = build_intro_unified_cut_prompt(
        locked_script_json="{}",
        segment_timings_json="{}",
        bundled_inventory_json="{}",
        style_profile_text="s",
        dramaturgy_text="d",
    )
    assert "closing_fallback_asset_id" in prompt
    # Schema must not require KF fit fields (may be named only in "Do NOT emit").
    assert '"closing_fallback_asset_fit"' not in prompt
    assert '"closing_fallback_asset_fit_reason"' not in prompt


def test_resolve_intro_timeline_disables_keyword_flow_rules(tmp_path: Path) -> None:
    project = _project(tmp_path)
    write_json(intro_unified_cut_plan_path(project), _intro_plan())
    captured: dict[str, object] = {}

    stub = ResolvedTimelineDocument(
        script_version="script-v1",
        fps=25.0,
        total_duration_seconds=2.0,
        shots=[
            ResolvedShot(
                shot_id="Intro_slot_001",
                asset_id="asset_a",
                timeline_start_seconds=0.0,
                timeline_end_seconds=2.0,
                source_start_seconds=0.0,
                source_end_seconds=2.0,
                folder_name="Intro",
                chapter_id="Intro",
            )
        ],
        audio_segments=[
            ResolvedAudioSegment(
                segment_id="Intro_segment_001",
                audio_path="/tmp/intro.wav",
                timeline_start_seconds=0.0,
                timeline_end_seconds=2.0,
            )
        ],
    )

    def _fake_resolve(*_args, **kwargs):
        captured.update(kwargs)
        return stub

    with (
        patch(
            "otio_app.services.without_voiceover_enhanced.intro_cut_service.assert_enhanced_work_root"
        ),
        patch(
            "otio_app.services.without_voiceover_enhanced.unified_timeline_service.resolve_unified_timeline",
            side_effect=_fake_resolve,
        ),
        patch(
            "otio_app.services.without_voiceover_enhanced.gap_merge_service.merge_export_ready_gaps_into_timeline",
            side_effect=lambda _project, **kwargs: (kwargs["timeline"], object()),
        ),
    ):
        out = resolve_intro_timeline(project)

    assert captured.get("apply_keyword_flow_rules") is False
    assert out.shots
