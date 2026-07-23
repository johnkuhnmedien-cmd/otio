"""Timing darf Merge nicht überspringen; Überlänge ≤ Nachlauf ist kein Blocker."""

from __future__ import annotations

from pathlib import Path

import pytest

from otio_app.services.without_voiceover_enhanced.gap_status_service import (
    compute_cut_plan_run_id,
)
from otio_app.services.without_voiceover_enhanced.models import (
    CutBoundary,
    CutSlot,
    UnifiedCutPlanDocument,
)
from otio_app.services.without_voiceover_enhanced.timeline_resolver import (
    _apply_chapter_envelopes,
)
from otio_app.services.without_voiceover_enhanced.models import (
    FinalCutPlanDocument,
    FinalShot,
    NarrationAnchor,
    NarrationTimelineDocument,
    NarrationTimelineEntry,
    ResolvedAudioSegment,
    ResolvedShot,
)


class _Locked:
    def __init__(self, segments: list) -> None:
        self.segments = segments


class _Seg:
    def __init__(self, segment_id: str, folder_name: str, sequence_index: int) -> None:
        self.segment_id = segment_id
        self.folder_name = folder_name
        self.sequence_index = sequence_index


def test_cut_plan_run_id_ignores_target_duration() -> None:
    plan_a = UnifiedCutPlanDocument(
        script_version="v1",
        boundaries=[
            CutBoundary(cut_id="b0", sentence_id="a__s001", position="start"),
            CutBoundary(cut_id="b1", sentence_id="a__s002", position="end"),
        ],
        slots=[CutSlot(slot_id="A_slot_001", asset_fit="none", needed_visual="x")],
    )
    plan_b = plan_a.model_copy(deep=True)
    plan_b.slots[0].target_duration_seconds = 12.34
    assert compute_cut_plan_run_id(plan_a) == compute_cut_plan_run_id(plan_b)
    assert compute_cut_plan_run_id(plan_a)


def test_closing_overhang_within_postroll_is_repaired_not_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
    from otio_app.models import Project, ProjectMode

    root = tmp_path / "p"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    project = Project(
        id="overhang",
        name="overhang",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        asset_subdir_names=["Caddo"],
        selected_asset_subdirs=["Caddo"],
        fps=25.0,
    )
    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.timeline_resolver."
        "_reapply_hold_for_timeline_span",
        lambda *args, **kwargs: None,
    )
    audio_end = 20.0
    # Closing endet 6.08s nach Audio — bei postroll=5 + Ausklang ≤ Toleranz.
    shot_end = 26.08
    shots = [
        ResolvedShot(
            shot_id="Caddo_slot_001",
            asset_id="a1",
            timeline_start_seconds=0.0,
            timeline_end_seconds=10.0,
            source_start_seconds=0.0,
            source_end_seconds=10.0,
            resolved_media_path="/tmp/a.mp4",
            resolved_media_kind="video",
            resolved_media_duration_seconds=40.0,
            folder_name="Caddo",
        ),
        ResolvedShot(
            shot_id="Caddo_slot_002",
            asset_id="a2",
            timeline_start_seconds=10.0,
            timeline_end_seconds=shot_end,
            source_start_seconds=0.0,
            source_end_seconds=16.0,
            resolved_media_path="/tmp/b.mp4",
            resolved_media_kind="video",
            resolved_media_duration_seconds=40.0,
            folder_name="Caddo",
        ),
    ]
    audio = [
        ResolvedAudioSegment(
            segment_id="Caddo_segment_001",
            audio_path="/tmp/c.mp3",
            timeline_start_seconds=0.0,
            timeline_end_seconds=audio_end,
            source_start_seconds=0.0,
            source_end_seconds=audio_end,
            pause_after_seconds=0.0,
        )
    ]
    narration = NarrationTimelineDocument(
        script_version="v1",
        total_duration_seconds=audio_end,
        entries=[
            NarrationTimelineEntry(
                segment_id="Caddo_segment_001",
                start_seconds=0.0,
                end_seconds=audio_end,
                pause_after_seconds=0.0,
                audio_duration_seconds=audio_end,
            )
        ],
    )
    final = FinalCutPlanDocument(
        script_version="v1",
        shots=[
            FinalShot(
                shot_id="Caddo_slot_001",
                narration_start_anchor=NarrationAnchor(
                    segment_id="Caddo_segment_001", offset_seconds=0.0
                ),
                narration_end_anchor=NarrationAnchor(
                    segment_id="Caddo_segment_001", offset_seconds=10.0
                ),
                asset_id="a1",
            ),
            FinalShot(
                shot_id="Caddo_slot_002",
                narration_start_anchor=NarrationAnchor(
                    segment_id="Caddo_segment_001", offset_seconds=10.0
                ),
                narration_end_anchor=NarrationAnchor(
                    segment_id="Caddo_segment_001", offset_seconds=audio_end
                ),
                asset_id="a2",
            ),
        ],
    )
    locked = _Locked([_Seg("Caddo_segment_001", "Caddo", 1)])
    repairs: list[str] = []
    errors: list[str] = []
    envelopes = _apply_chapter_envelopes(
        project,
        locked=locked,
        final=final,
        ordered=shots,
        audio_segments=audio,
        preroll=5.0,
        postroll=5.0,
        fps=25.0,
        repairs=repairs,
        errors=errors,
        narration_timeline=narration,
    )
    assert not any("Überlänge" in e for e in errors), errors
    assert any("Closing-Überlänge innerhalb Nachlauf" in r for r in repairs)
    assert envelopes
    closing = next(s for s in shots if s.shot_id == "Caddo_slot_002")
    # Nach Envelope: Closing endet am Video-Ende (= Audio-Ende + Nachlauf).
    assert closing.timeline_end_seconds == pytest.approx(
        envelopes[0].chapter_video_end, abs=1e-3
    )
    assert envelopes[0].chapter_video_end == pytest.approx(
        envelopes[0].chapter_audio_end + 5.0, abs=1e-3
    )
