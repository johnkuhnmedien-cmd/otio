"""Phase 4–5: Fit-Brücke, Dauer-Vorfilter, Gap-Merge."""

from __future__ import annotations

from pathlib import Path

import pytest

from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.services.without_voiceover_enhanced.fit_bridge import (
    FIT_SCORE_ACCEPTABLE_MIN,
    FIT_SCORE_STRONG_MIN,
    FIT_SCORE_WEAK_MIN,
    filter_candidates_by_duration,
    fit_bucket_from_final_score,
    passes_duration_prefilter,
    required_candidate_duration_seconds,
    supplement_beats_local,
)
from otio_app.services.without_voiceover_enhanced.gap_merge_service import (
    GapMergeError,
    merge_export_ready_gaps_into_timeline,
)
from otio_app.services.without_voiceover_enhanced.io_utils import write_json
from otio_app.services.without_voiceover_enhanced.models import (
    AcceptedSupplementsDocument,
    CoverageGap,
    CoverageGapsDocument,
    FunnelCandidateRecord,
    GapMergeReport,
    ResolvedShot,
    ResolvedTimelineDocument,
    StockCandidate,
    SupplementFunnelGapReport,
    SupplementFunnelReport,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    accepted_supplements_path,
    coverage_gaps_path,
    gap_merge_report_path,
    resolved_timeline_path,
    supplement_funnel_report_path,
)
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    lock_script,
    save_script_draft,
)
from otio_app.services.without_voiceover_enhanced.models import (
    EnhancedScriptDocument,
    ScriptSegment,
)
from otio_app.services.without_voiceover_enhanced.supplement_thumbnail_rank_service import (
    order_by_final_scores,
)


def test_fit_bucket_thresholds() -> None:
    assert fit_bucket_from_final_score(100) == "strong"
    assert fit_bucket_from_final_score(FIT_SCORE_STRONG_MIN) == "strong"
    assert fit_bucket_from_final_score(FIT_SCORE_ACCEPTABLE_MIN) == "acceptable"
    assert fit_bucket_from_final_score(79) == "acceptable"
    assert fit_bucket_from_final_score(FIT_SCORE_WEAK_MIN) == "weak"
    assert fit_bucket_from_final_score(39) == "reject"
    assert fit_bucket_from_final_score(None) == "reject"


def test_supplement_beats_local_bucket_rules() -> None:
    assert supplement_beats_local(supplement_bucket="acceptable", local_fit="weak")
    assert not supplement_beats_local(supplement_bucket="weak", local_fit="weak")
    assert supplement_beats_local(supplement_bucket="weak", local_fit="none")
    assert not supplement_beats_local(supplement_bucket="reject", local_fit="none")
    assert supplement_beats_local(supplement_bucket="manual", local_fit="weak")


def test_duration_prefilter_video_and_photo() -> None:
    need = required_candidate_duration_seconds(
        9.0, head_trim=1.0, short_tolerance=0.5
    )
    assert need == pytest.approx(10.5)

    short = StockCandidate(
        candidate_id="v1",
        provider="pexels",
        provider_asset_id="1",
        media_type="video",
        duration_seconds=4.0,
        preview_url="https://example.com/a.jpg",
        download_url="https://example.com/a.mp4",
    )
    ok, reason = passes_duration_prefilter(short, min_duration=need)
    assert ok is False
    assert "4.00s" in reason

    photo = StockCandidate(
        candidate_id="p1",
        provider="pexels",
        provider_asset_id="2",
        media_type="photo",
        duration_seconds=None,
        preview_url="https://example.com/b.jpg",
        download_url="https://example.com/b.jpg",
    )
    assert passes_duration_prefilter(photo, min_duration=need)[0] is True

    unknown = StockCandidate(
        candidate_id="v2",
        provider="pexels",
        provider_asset_id="3",
        media_type="video",
        duration_seconds=None,
        preview_url="https://example.com/c.jpg",
        download_url="https://example.com/c.mp4",
    )
    assert passes_duration_prefilter(unknown, min_duration=need)[0] is False

    kept, excluded = filter_candidates_by_duration(
        [short, photo, unknown], min_duration=need
    )
    assert [c.candidate_id for c in kept] == ["p1"]
    assert len(excluded) == 2


def test_order_by_final_scores_rejects_below_40() -> None:
    records = [
        FunnelCandidateRecord(candidate_id="good", decision="fallback"),
        FunnelCandidateRecord(candidate_id="bad", decision="winner"),
    ]
    payload = [
        {
            "candidate_id": "good",
            "final_score": 72,
            "rank": 2,
            "decision": "fallback",
            "reason": "ok",
        },
        {
            "candidate_id": "bad",
            "final_score": 22,
            "rank": 1,
            "decision": "winner",
            "reason": "weak",
        },
    ]
    ordered = order_by_final_scores(records, payload)
    by_id = {r.candidate_id: r for r in ordered}
    assert by_id["bad"].fit_bucket == "reject"
    assert by_id["bad"].decision == "manual_review"
    assert by_id["bad"].excluded is True
    assert by_id["good"].fit_bucket == "acceptable"
    assert by_id["good"].decision == "winner"


def _project(tmp_path: Path) -> Project:
    root = tmp_path / "proj"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    project = Project(
        id="p",
        name="p",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        asset_subdir_names=["A"],
        selected_asset_subdirs=["A"],
        fps=25.0,
    )
    save_script_draft(
        project,
        EnhancedScriptDocument(
            narration_full="Hallo.",
            segments=[
                ScriptSegment(segment_id="seg_001", text="Hallo.", sequence_index=1)
            ],
        ),
    )
    lock_script(project)
    return project


def test_gap_merge_swaps_asset_keeps_timing(tmp_path: Path) -> None:
    project = _project(tmp_path)
    media = project.work_dir_path / "supp.mp4"
    # Minimal valid-enough file for catalog path checks; resolve may skip ffmpeg
    # if we pre-build ResolvedShot fields via merge with mock catalog entry.
    media.write_bytes(b"not-a-real-video")

    write_json(
        coverage_gaps_path(project),
        CoverageGapsDocument(
            script_version="script-v1",
            gaps=[
                CoverageGap(
                    gap_id="gap_1",
                    related_shot_ids=["slot_001"],
                    needed_visual="alley",
                    target_duration_seconds=2.0,
                    priority="high",
                )
            ],
        ),
    )
    write_json(
        accepted_supplements_path(project),
        AcceptedSupplementsDocument(
            script_version="script-v1",
            supplements=[
                StockCandidate(
                    candidate_id="supp_1",
                    provider="manual",
                    provider_asset_id="m1",
                    media_type="photo",
                    gap_id="gap_1",
                    local_media_path=str(media),
                    media_validation_status="export_ready",
                    funnel_managed=True,
                    preview_url="https://example.com/x.jpg",
                    download_url="https://example.com/x.jpg",
                )
            ],
        ),
    )
    write_json(
        supplement_funnel_report_path(project),
        SupplementFunnelReport(
            script_version="script-v1",
            gaps=[
                SupplementFunnelGapReport(
                    gap_id="gap_1",
                    export_ready_candidate_id="supp_1",
                    filled=True,
                    candidates=[
                        FunnelCandidateRecord(
                            candidate_id="supp_1",
                            final_score=70,
                            fit_bucket="acceptable",
                            decision="winner",
                        )
                    ],
                )
            ],
        ),
    )

    timeline = ResolvedTimelineDocument(
        script_version="script-v1",
        fps=25.0,
        total_duration_seconds=5.0,
        shots=[
            ResolvedShot(
                shot_id="slot_001",
                asset_id="",
                timeline_start_seconds=1.0,
                timeline_end_seconds=3.0,
                source_start_seconds=0.0,
                source_end_seconds=2.0,
                editorial_function="evidence",
                asset_fit="none",
                coverage_gap_id="gap_1",
                open_gap=True,
                resolved_media_kind="placeholder",
            )
        ],
    )
    write_json(resolved_timeline_path(project), timeline)

    # Stub export_ready listing + media resolve (no ffmpeg/ffprobe in unit test).
    from otio_app.services.without_voiceover_enhanced import gap_merge_service as gms

    ready_candidate = StockCandidate(
        candidate_id="supp_1",
        provider="manual",
        provider_asset_id="m1",
        media_type="photo",
        gap_id="gap_1",
        local_media_path=str(media),
        media_validation_status="export_ready",
        funnel_managed=True,
        preview_url="https://example.com/x.jpg",
        download_url="https://example.com/x.jpg",
    )

    def _fake_resolve(project, **kwargs):
        return ResolvedShot(
            shot_id=kwargs["shot_id"],
            asset_id=kwargs["asset_id"],
            timeline_start_seconds=kwargs["timeline_start"],
            timeline_end_seconds=kwargs["timeline_end"],
            source_start_seconds=0.0,
            source_end_seconds=kwargs["timeline_end"] - kwargs["timeline_start"],
            editorial_function=kwargs["editorial_function"],
            resolved_media_path=str(media),
            resolved_media_kind="video",
            hold_mode="freeze_video",
        )

    original_resolve = gms._resolve_shot_media
    original_list = gms.list_export_ready_supplements
    gms._resolve_shot_media = _fake_resolve  # type: ignore[assignment]
    gms.list_export_ready_supplements = lambda _p: [ready_candidate]  # type: ignore[assignment]
    try:
        merged, report = merge_export_ready_gaps_into_timeline(
            project, require_closed_none=True, persist=True
        )
    finally:
        gms._resolve_shot_media = original_resolve  # type: ignore[assignment]
        gms.list_export_ready_supplements = original_list  # type: ignore[assignment]

    assert report.merged_shot_ids == ["slot_001"]
    assert merged.shots[0].asset_id == "supp_1"
    assert merged.shots[0].timeline_start_seconds == pytest.approx(1.0)
    assert merged.shots[0].timeline_end_seconds == pytest.approx(3.0)
    assert merged.shots[0].open_gap is False
    assert gap_merge_report_path(project).is_file()
    loaded = GapMergeReport.model_validate_json(
        gap_merge_report_path(project).read_text(encoding="utf-8")
    )
    assert loaded.merged_shot_ids == ["slot_001"]


def test_gap_merge_keeps_weak_without_better_supplement(tmp_path: Path) -> None:
    project = _project(tmp_path)
    write_json(
        coverage_gaps_path(project),
        CoverageGapsDocument(
            script_version="script-v1",
            gaps=[
                CoverageGap(
                    gap_id="gap_w",
                    related_shot_ids=["slot_w"],
                    needed_visual="better",
                    target_duration_seconds=5.0,
                    priority="medium",
                )
            ],
        ),
    )
    # export_ready with weak score → should NOT beat local weak
    write_json(
        accepted_supplements_path(project),
        AcceptedSupplementsDocument(
            script_version="script-v1",
            supplements=[
                StockCandidate(
                    candidate_id="supp_weak",
                    provider="pexels",
                    provider_asset_id="w1",
                    media_type="video",
                    duration_seconds=8.0,
                    gap_id="gap_w",
                    local_media_path=str(project.work_dir_path / "x.mp4"),
                    media_validation_status="export_ready",
                    preview_url="https://example.com/p.jpg",
                    download_url="https://example.com/p.mp4",
                )
            ],
        ),
    )
    (project.work_dir_path / "x.mp4").write_bytes(b"x")
    write_json(
        supplement_funnel_report_path(project),
        SupplementFunnelReport(
            script_version="script-v1",
            gaps=[
                SupplementFunnelGapReport(
                    gap_id="gap_w",
                    filled=True,
                    export_ready_candidate_id="supp_weak",
                    candidates=[
                        FunnelCandidateRecord(
                            candidate_id="supp_weak",
                            final_score=45,
                            fit_bucket="weak",
                            decision="winner",
                        )
                    ],
                )
            ],
        ),
    )
    timeline = ResolvedTimelineDocument(
        script_version="script-v1",
        fps=25.0,
        total_duration_seconds=5.0,
        shots=[
            ResolvedShot(
                shot_id="slot_w",
                asset_id="local_weak",
                timeline_start_seconds=0.0,
                timeline_end_seconds=5.0,
                source_start_seconds=0.0,
                source_end_seconds=5.0,
                asset_fit="weak",
                coverage_gap_id="gap_w",
                open_gap=False,
                resolved_media_path="/tmp/local.mp4",
                resolved_media_kind="video",
            )
        ],
    )
    write_json(resolved_timeline_path(project), timeline)

    from otio_app.services.without_voiceover_enhanced import gap_merge_service as gms

    weak_supp = StockCandidate(
        candidate_id="supp_weak",
        provider="pexels",
        provider_asset_id="w1",
        media_type="video",
        duration_seconds=8.0,
        gap_id="gap_w",
        local_media_path=str(project.work_dir_path / "x.mp4"),
        media_validation_status="export_ready",
        preview_url="https://example.com/p.jpg",
        download_url="https://example.com/p.mp4",
    )
    original_list = gms.list_export_ready_supplements
    gms.list_export_ready_supplements = lambda _p: [weak_supp]  # type: ignore[assignment]
    try:
        merged, report = merge_export_ready_gaps_into_timeline(
            project, require_closed_none=False, persist=False
        )
    finally:
        gms.list_export_ready_supplements = original_list  # type: ignore[assignment]
    assert report.kept_local_shot_ids == ["slot_w"]
    assert merged.shots[0].asset_id == "local_weak"


def test_gap_merge_fail_closed_open_none(tmp_path: Path) -> None:
    project = _project(tmp_path)
    write_json(
        coverage_gaps_path(project),
        CoverageGapsDocument(
            script_version="script-v1",
            gaps=[CoverageGap(gap_id="gap_n", related_shot_ids=["s"], priority="high")],
        ),
    )
    write_json(
        accepted_supplements_path(project),
        AcceptedSupplementsDocument(script_version="script-v1", supplements=[]),
    )
    timeline = ResolvedTimelineDocument(
        script_version="script-v1",
        fps=25.0,
        total_duration_seconds=2.0,
        shots=[
            ResolvedShot(
                shot_id="s",
                asset_id="",
                timeline_start_seconds=0.0,
                timeline_end_seconds=2.0,
                source_start_seconds=0.0,
                source_end_seconds=2.0,
                asset_fit="none",
                coverage_gap_id="gap_n",
                open_gap=True,
            )
        ],
    )
    write_json(resolved_timeline_path(project), timeline)
    with pytest.raises(GapMergeError, match="Offene none"):
        merge_export_ready_gaps_into_timeline(
            project, require_closed_none=True, persist=False
        )
