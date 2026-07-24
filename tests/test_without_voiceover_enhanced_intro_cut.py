"""Intro Unified Cut: gebündeltes Inventar, strong-only, Intro-Hüllen."""

from __future__ import annotations

from otio_app.services.without_voiceover_enhanced.intro_cut_service import (
    INTRO_CLOSING_HOLD_DEFAULT_SEC,
    INTRO_CLOSING_HOLD_MAX_SEC,
    INTRO_OPENING_HOLD_SEC,
    clamp_intro_closing_hold,
    enforce_intro_strong_only,
    filter_resolved_timeline_to_intro,
    merge_intro_and_body_plans,
    split_intro_from_unified,
)
from otio_app.services.without_voiceover_enhanced.models import (
    CutBoundary,
    CutSlot,
    ResolvedAudioSegment,
    ResolvedChapterEnvelope,
    ResolvedShot,
    ResolvedTimelineDocument,
    UnifiedCutPlanDocument,
)
from otio_app.services.without_voiceover_enhanced.script_prompts import (
    build_intro_unified_cut_prompt,
)


def _slot(slot_id: str, fit: str, asset: str | None) -> CutSlot:
    return CutSlot(
        slot_id=slot_id,
        local_asset_id=asset,
        asset_fit=fit,  # type: ignore[arg-type]
        asset_fit_reason="test",
        visual_intent="valley",
        coverage_gap_id=None if fit == "strong" else f"gap_{slot_id}",
        needed_visual="valley" if fit != "strong" else "",
        search_concepts=["valley wide"] if fit != "strong" else [],
    )


def _bound(cut_id: str, sentence_id: str, position: str = "start") -> CutBoundary:
    return CutBoundary(
        cut_id=cut_id,
        sentence_id=sentence_id,
        position=position,  # type: ignore[arg-type]
        alignment="sentence_boundary",
    )


def test_intro_prompt_rules() -> None:
    prompt = build_intro_unified_cut_prompt(
        locked_script_json="{}",
        segment_timings_json="{}",
        bundled_inventory_json='{"chapters":{}}',
        style_profile_text="s",
        dramaturgy_text="d",
        intro_audio_duration_seconds=9.5,
    )
    assert "BUNDLED INVENTORY" in prompt
    assert "strong" in prompt
    assert "acceptable" in prompt
    assert "4.0" in prompt
    assert "9.500" in prompt


def test_enforce_intro_strong_only_rejects_acceptable() -> None:
    plan = UnifiedCutPlanDocument(
        script_version="v1",
        boundaries=[
            _bound("Intro_cut_000", "Intro_segment_001__s001", "start"),
            _bound("Intro_cut_001", "Intro_segment_001__s001", "middle"),
            _bound("Intro_cut_002", "Intro_segment_001__s001", "end"),
        ],
        slots=[
            _slot("Intro_slot_001", "acceptable", "yo_01"),
            _slot("Intro_slot_002", "strong", "ca_01"),
        ],
        voiceover_postroll_sec=9.0,
    )
    out = enforce_intro_strong_only(plan)
    assert out.slots[0].local_asset_id is None
    assert out.slots[0].asset_fit == "none"
    assert out.slots[0].coverage_gap_id
    assert out.slots[1].asset_fit == "strong"
    assert out.voiceover_preroll_sec == INTRO_OPENING_HOLD_SEC
    assert out.voiceover_postroll_sec == INTRO_CLOSING_HOLD_MAX_SEC  # clamped from 9


def test_clamp_intro_closing_hold() -> None:
    assert clamp_intro_closing_hold(None) == INTRO_CLOSING_HOLD_DEFAULT_SEC
    assert clamp_intro_closing_hold(3.0) == 5.0
    assert clamp_intro_closing_hold(7.0) == 7.0
    assert clamp_intro_closing_hold(12.0) == 8.0


def test_split_and_merge_intro_body() -> None:
    intro = UnifiedCutPlanDocument(
        script_version="v1",
        boundaries=[
            _bound("Intro_cut_000", "Intro_segment_001__s001", "start"),
            _bound("Intro_cut_001", "Intro_segment_001__s001", "end"),
        ],
        slots=[_slot("Intro_slot_001", "strong", "yo_01")],
        voiceover_preroll_sec=4.0,
        voiceover_postroll_sec=6.5,
    )
    body = UnifiedCutPlanDocument(
        script_version="v1",
        boundaries=[
            _bound("Yosemite_cut_000", "seg_a__s001", "start"),
            _bound("Yosemite_cut_001", "seg_a__s002", "end"),
        ],
        slots=[_slot("Yosemite_slot_001", "strong", "yo_02")],
    )
    merged = merge_intro_and_body_plans(
        intro=intro, body=body, script_version="v1"
    )
    assert merged.slots[0].slot_id.startswith("Intro_")
    assert merged.slots[1].slot_id.startswith("Yosemite_")
    assert merged.slots[1].start_sentence_id == "seg_a__s001"
    assert len(merged.boundaries) == 3  # intro 2 + body without first

    split_intro, split_body = split_intro_from_unified(merged)
    assert split_intro is not None
    assert split_body is not None
    assert len(split_intro.slots) == 1
    assert len(split_body.slots) == 1


def test_export_intro_otio_does_not_rewrite_full_timeline(tmp_path) -> None:
    """Regression: Intro-Export darf resolved_timeline.json nicht überschreiben."""
    from pathlib import Path
    from unittest.mock import patch

    from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
    from otio_app.models import Project, ProjectMode
    from otio_app.services.without_voiceover_enhanced.intro_cut_service import (
        export_intro_otio,
    )
    from otio_app.services.without_voiceover_enhanced.io_utils import write_json
    from otio_app.services.without_voiceover_enhanced.paths import (
        resolved_timeline_path,
    )

    root = tmp_path / "proj"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    project = Project(
        name="IntroExport",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        language="de",
        asset_subdir_names=["Yosemite"],
        selected_asset_subdirs=["Yosemite"],
        fps=25.0,
    )
    full = ResolvedTimelineDocument(
        script_version="v1",
        fps=25.0,
        total_duration_seconds=30.0,
        audio_segments=[
            ResolvedAudioSegment(
                segment_id="Intro_segment_001",
                audio_path="/tmp/intro.wav",
                timeline_start_seconds=0.0,
                timeline_end_seconds=5.0,
            ),
            ResolvedAudioSegment(
                segment_id="Yosemite_segment_001",
                audio_path="/tmp/y.wav",
                timeline_start_seconds=10.0,
                timeline_end_seconds=20.0,
            ),
        ],
        shots=[
            ResolvedShot(
                shot_id="Intro_slot_001",
                asset_id="yo_01",
                timeline_start_seconds=0.0,
                timeline_end_seconds=5.0,
                source_start_seconds=0.0,
                source_end_seconds=1.0,
                folder_name="Intro",
            ),
            ResolvedShot(
                shot_id="Yosemite_slot_001",
                asset_id="yo_02",
                timeline_start_seconds=10.0,
                timeline_end_seconds=20.0,
                source_start_seconds=0.0,
                source_end_seconds=1.0,
                folder_name="Yosemite",
            ),
        ],
        chapters=[
            ResolvedChapterEnvelope(
                chapter_id="Intro",
                folder_name="Intro",
                chapter_video_start=0.0,
                chapter_audio_start=0.0,
                chapter_audio_end=5.0,
                chapter_video_end=5.0,
                first_shot_id="Intro_slot_001",
                last_shot_id="Intro_slot_001",
                segment_ids=["Intro_segment_001"],
            )
        ],
    )
    write_json(resolved_timeline_path(project), full)
    before = resolved_timeline_path(project).read_text(encoding="utf-8")

    captured: dict = {}

    def _fake_export(project, *, basename, allow_errors=False, resolved=None, timeline_name=None):
        captured["resolved"] = resolved
        captured["basename"] = basename
        captured["allow_errors"] = allow_errors
        out = Path(project.language_work_dir_path) / "exports" / f"{basename}.otio"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("otio", encoding="utf-8")
        return out

    with patch(
        "otio_app.services.without_voiceover_enhanced.intro_cut_service.export_otio_from_resolved_timeline",
        _fake_export,
    ):
        path = export_intro_otio(project, basename="only_intro", allow_errors=True)

    assert path.name == "only_intro.otio"
    assert captured["allow_errors"] is True
    assert captured["resolved"] is not None
    assert {s.shot_id for s in captured["resolved"].shots} == {"Intro_slot_001"}
    assert {a.segment_id for a in captured["resolved"].audio_segments} == {
        "Intro_segment_001"
    }
    after = resolved_timeline_path(project).read_text(encoding="utf-8")
    assert before == after  # Gesamt-Timeline unverändert


def test_filter_resolved_timeline_to_intro() -> None:
    resolved = ResolvedTimelineDocument(
        script_version="v1",
        fps=25.0,
        total_duration_seconds=30.0,
        audio_segments=[
            ResolvedAudioSegment(
                segment_id="Intro_segment_001",
                audio_path="/tmp/intro.wav",
                timeline_start_seconds=4.0,
                timeline_end_seconds=10.0,
            ),
            ResolvedAudioSegment(
                segment_id="Yosemite_segment_001",
                audio_path="/tmp/y.wav",
                timeline_start_seconds=16.5,
                timeline_end_seconds=25.0,
            ),
        ],
        shots=[
            ResolvedShot(
                shot_id="Intro_slot_001",
                asset_id="yo_01",
                timeline_start_seconds=0.0,
                timeline_end_seconds=16.5,
                source_start_seconds=0.0,
                source_end_seconds=1.0,
                folder_name="Intro",
            ),
            ResolvedShot(
                shot_id="Yosemite_slot_001",
                asset_id="yo_02",
                timeline_start_seconds=16.5,
                timeline_end_seconds=30.0,
                source_start_seconds=0.0,
                source_end_seconds=1.0,
                folder_name="Yosemite",
            ),
        ],
        chapters=[
            ResolvedChapterEnvelope(
                chapter_id="Intro",
                folder_name="Intro",
                chapter_video_start=0.0,
                chapter_audio_start=4.0,
                chapter_audio_end=10.0,
                chapter_video_end=16.5,
                preroll_seconds=4.0,
                postroll_seconds=6.5,
                first_shot_id="Intro_slot_001",
                last_shot_id="Intro_slot_001",
            ),
            ResolvedChapterEnvelope(
                chapter_id="Yosemite",
                folder_name="Yosemite",
                chapter_video_start=16.5,
                chapter_audio_start=16.5,
                chapter_audio_end=25.0,
                chapter_video_end=30.0,
                preroll_seconds=0.0,
                postroll_seconds=5.0,
                first_shot_id="Yosemite_slot_001",
                last_shot_id="Yosemite_slot_001",
            ),
        ],
    )
    intro = filter_resolved_timeline_to_intro(resolved)
    assert len(intro.chapters) == 1
    assert intro.chapters[0].folder_name == "Intro"
    assert intro.chapters[0].chapter_video_start == 0.0
    assert len(intro.shots) == 1
    assert intro.shots[0].shot_id == "Intro_slot_001"
    assert intro.total_duration_seconds == 16.5
