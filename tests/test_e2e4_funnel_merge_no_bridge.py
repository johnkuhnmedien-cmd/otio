"""E2E-4: Funnel/Merge-Wahrheitsquelle, Accepted-run_id, kein Bridge-Slot."""

from __future__ import annotations

from pathlib import Path

import pytest

from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.services.without_voiceover_enhanced.io_utils import load_model, write_json
from otio_app.services.without_voiceover_enhanced.local_media_service import (
    STATUS_EXPORT_READY,
    list_export_ready_supplements,
    migrate_accepted_supplements,
)
from otio_app.services.without_voiceover_enhanced.models import (
    AcceptedSupplementsDocument,
    CoverageGap,
    CoverageGapsDocument,
    CutBoundary,
    CutSlot,
    EnhancedScriptDocument,
    ScriptSegment,
    StockCandidate,
    SupplementFunnelGapReport,
    SupplementFunnelReport,
    UnifiedCutPlanDocument,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    accepted_supplements_path,
    coverage_gaps_path,
    supplement_funnel_report_path,
)
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    lock_script,
    save_script_draft,
)
from otio_app.services.without_voiceover_enhanced.supplement_funnel_service import (
    _gap_already_export_ready,
    list_open_funnel_gap_ids,
)
from otio_app.services.without_voiceover_enhanced.unified_cut_plan import unified_to_rough
from otio_app.services.without_voiceover_enhanced.unified_timeline_service import (
    _seconds_ceil_to_frame,
    _seconds_floor_to_frame,
    _snap_chapter_edge_boundary_times,
)
from otio_app.services.without_voiceover_enhanced.models import (
    NarrationTimelineDocument,
    NarrationTimelineEntry,
    SentenceTiming,
)


def _project(tmp_path: Path) -> Project:
    root = tmp_path / "proj"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    return Project(
        id="e2e4",
        name="e2e4",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        asset_subdir_names=["Yosemite"],
        selected_asset_subdirs=["Yosemite"],
        fps=25.0,
        width=1920,
        height=1080,
    )


def _lock(project: Project) -> None:
    save_script_draft(
        project,
        EnhancedScriptDocument(
            narration_full="Hello world.",
            segments=[
                ScriptSegment(
                    segment_id="Yosemite_segment_001",
                    sequence_index=1,
                    folder_name="Yosemite",
                    text="Hello world.",
                )
            ],
        ),
    )
    lock_script(project)


def test_accepted_without_run_id_rebinds_bridge_purged(tmp_path: Path) -> None:
    """Run-ID-Drift wird reboundet; nur Legacy-Bridge-Gaps werden gelöscht."""
    project = _project(tmp_path)
    _lock(project)
    write_json(
        coverage_gaps_path(project),
        CoverageGapsDocument(
            script_version="v1",
            cut_plan_run_id="run_abc",
            gaps=[
                CoverageGap(
                    gap_id="gap_Yosemite_slot_011",
                    related_shot_ids=["Yosemite_slot_011"],
                    needed_visual="granite",
                    target_duration_seconds=8.0,
                )
            ],
        ),
    )
    media = project.work_dir_path / "vid.mp4"
    media.write_bytes(b"\x00" * 64)
    write_json(
        accepted_supplements_path(project),
        AcceptedSupplementsDocument(
            script_version="v1",
            supplements=[
                StockCandidate(
                    candidate_id="pexels_video_1451126",
                    provider="pexels",
                    media_type="video",
                    gap_id="gap_Yosemite_slot_011",
                    local_media_path=str(media),
                    media_validation_status=STATUS_EXPORT_READY,
                    duration_seconds=12.0,
                    cut_plan_run_id="",  # stale → rebind
                ),
                StockCandidate(
                    candidate_id="bridge_junk",
                    provider="pexels",
                    media_type="video",
                    gap_id="gap_bridge_001",
                    local_media_path=str(media),
                    media_validation_status=STATUS_EXPORT_READY,
                    duration_seconds=5.0,
                    cut_plan_run_id="run_abc",
                ),
            ],
        ),
    )
    migrate_accepted_supplements(project)
    accepted = load_model(accepted_supplements_path(project), AcceptedSupplementsDocument)
    assert accepted is not None
    ids = {s.candidate_id for s in accepted.supplements}
    assert "pexels_video_1451126" in ids
    assert "bridge_junk" not in ids
    yos = next(s for s in accepted.supplements if s.candidate_id == "pexels_video_1451126")
    assert yos.cut_plan_run_id == "run_abc"


def test_gap_already_export_ready_requires_merge_criteria(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path)
    _lock(project)
    gap = CoverageGap(
        gap_id="gap_Yosemite_slot_011",
        related_shot_ids=["Yosemite_slot_011"],
        needed_visual="granite",
        target_duration_seconds=30.0,  # länger als Kandidat
    )
    write_json(
        coverage_gaps_path(project),
        CoverageGapsDocument(
            script_version="v1",
            cut_plan_run_id="run_abc",
            gaps=[gap],
        ),
    )
    media = project.work_dir_path / "short.mp4"
    media.write_bytes(b"\x00" * 64)
    write_json(
        accepted_supplements_path(project),
        AcceptedSupplementsDocument(
            script_version="v1",
            supplements=[
                StockCandidate(
                    candidate_id="pexels_video_short",
                    provider="pexels",
                    media_type="video",
                    gap_id="gap_Yosemite_slot_011",
                    local_media_path=str(media),
                    media_validation_status=STATUS_EXPORT_READY,
                    duration_seconds=5.0,  # < target
                    cut_plan_run_id="run_abc",
                )
            ],
        ),
    )
    # Funnel sagt filled — trotzdem nicht export_ready für Skip.
    funnel = SupplementFunnelReport(
        script_version="v1",
        cut_plan_run_id="run_abc",
        gaps=[
            SupplementFunnelGapReport(
                gap_id="gap_Yosemite_slot_011",
                filled=True,
                export_ready_candidate_id="pexels_video_short",
            )
        ],
        filled_gap_ids=["gap_Yosemite_slot_011"],
    )
    write_json(supplement_funnel_report_path(project), funnel)
    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.supplement_funnel_service."
        "probe_duration_seconds",
        lambda _p: 5.0,
    )
    assert (
        _gap_already_export_ready(
            funnel,
            gap_id="gap_Yosemite_slot_011",
            project=project,
            trust_accepted=True,
            gap=gap,
            expected_run_id="run_abc",
        )
        is False
    )
    assert list_open_funnel_gap_ids(project) == ["gap_Yosemite_slot_011"]
    from otio_app.services.without_voiceover_enhanced.gap_status_service import (
        summarize_gap_status,
    )

    status = summarize_gap_status(project)
    assert "gap_Yosemite_slot_011" in status.open_gap_ids
    assert "merge-fähig" in (status.message or "")


def test_merge_and_persist_unified_cuts_no_bridge_slot() -> None:
    plan_a = UnifiedCutPlanDocument(
        script_version="v1",
        boundaries=[
            CutBoundary(cut_id="a0", sentence_id="A__s001", position="start"),
            CutBoundary(cut_id="a1", sentence_id="A__s002", position="end"),
        ],
        slots=[
            CutSlot(slot_id="A_slot_001", local_asset_id="x", asset_fit="strong"),
        ],
    )
    plan_b = UnifiedCutPlanDocument(
        script_version="v1",
        boundaries=[
            CutBoundary(cut_id="b0", sentence_id="B__s001", position="start"),
            CutBoundary(cut_id="b1", sentence_id="B__s002", position="end"),
        ],
        slots=[
            CutSlot(slot_id="B_slot_001", local_asset_id="y", asset_fit="strong"),
        ],
    )
    # merge_and_persist braucht Project — testen Join-Logik über unified_to_rough
    # auf manuell zusammengesetztem Plan (wie merge es baut).
    first_slot = plan_b.slots[0].model_copy(
        update={"start_sentence_id": plan_b.boundaries[0].sentence_id}
    )
    merged = UnifiedCutPlanDocument(
        script_version="v1",
        boundaries=list(plan_a.boundaries) + list(plan_b.boundaries[1:]),
        slots=list(plan_a.slots) + [first_slot],
    )
    assert len(merged.slots) == len(merged.boundaries) - 1
    assert not any(s.slot_id.startswith("bridge_") for s in merged.slots)
    assert merged.slots[1].start_sentence_id == "B__s001"
    _rough, coverage = unified_to_rough(merged)
    assert not any("bridge" in g.gap_id for g in coverage.gaps)


def test_chapter_edge_snap_floor_ceil() -> None:
    plan = UnifiedCutPlanDocument(
        script_version="v1",
        boundaries=[
            CutBoundary(cut_id="b0", sentence_id="seg__s001", position="start"),
            CutBoundary(cut_id="b1", sentence_id="seg__s002", position="end"),
        ],
        slots=[CutSlot(slot_id="slot_001", asset_fit="none")],
    )
    # Audio 10.08s, Satzende bei 10.00 → ceil auf 10.08 bei 25fps = 10.08
    timeline = NarrationTimelineDocument(
        script_version="v1",
        total_duration_seconds=10.08,
        entries=[
            NarrationTimelineEntry(
                segment_id="seg",
                start_seconds=0.04,
                end_seconds=10.08,
                pause_after_seconds=0.0,
                audio_duration_seconds=10.04,
            )
        ],
    )
    sentences = {
        "seg__s001": SentenceTiming(
            sentence_id="seg__s001",
            segment_id="seg",
            text="A",
            start_seconds=0.0,
            end_seconds=1.0,
            duration_seconds=1.0,
        ),
        "seg__s002": SentenceTiming(
            sentence_id="seg__s002",
            segment_id="seg",
            text="B",
            start_seconds=9.0,
            end_seconds=10.0,
            duration_seconds=1.0,
        ),
    }
    raw = [0.12, 10.0]  # Satz-basiert, weicht von Audio ab
    repairs: list[str] = []
    out = _snap_chapter_edge_boundary_times(
        raw,
        plan,
        timeline,
        sentence_index=sentences,
        segment_to_chapter={"seg": "Yosemite"},
        fps=25.0,
        repairs=repairs,
    )
    assert out[0] == pytest.approx(_seconds_floor_to_frame(0.04, 25.0))
    assert out[1] == pytest.approx(_seconds_ceil_to_frame(10.08, 25.0))
    assert repairs
