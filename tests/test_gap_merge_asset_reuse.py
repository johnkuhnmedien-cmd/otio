"""Gap-Merge muss Cut-Plan Reuse-/Nachbar-Regeln einhalten."""

from __future__ import annotations

from pathlib import Path

from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
    CutPlanOptions,
    save_cut_plan_options,
)
from otio_app.services.without_voiceover_enhanced.gap_merge_service import (
    _filter_candidates_by_cut_plan_reuse,
    _gap_fill_reuse_violation,
    merge_export_ready_gaps_into_timeline,
)
from otio_app.services.without_voiceover_enhanced.io_utils import write_json
from otio_app.services.without_voiceover_enhanced.models import (
    AcceptedSupplementsDocument,
    CoverageGap,
    CoverageGapsDocument,
    EnhancedScriptDocument,
    FunnelCandidateRecord,
    ResolvedShot,
    ResolvedTimelineDocument,
    ScriptSegment,
    StockCandidate,
    SupplementFunnelGapReport,
    SupplementFunnelReport,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    accepted_supplements_path,
    coverage_gaps_path,
    resolved_timeline_path,
    supplement_funnel_report_path,
)
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    lock_script,
    save_script_draft,
)


def test_gap_fill_rejects_consecutive_same_asset() -> None:
    shots = [
        ResolvedShot(
            shot_id="s1",
            asset_id="manual_milos_slot_002",
            timeline_start_seconds=0.0,
            timeline_end_seconds=2.0,
            source_start_seconds=0.0,
            source_end_seconds=2.0,
            folder_name="Milos",
            open_gap=False,
        ),
        ResolvedShot(
            shot_id="s2",
            asset_id="",
            timeline_start_seconds=2.0,
            timeline_end_seconds=4.0,
            source_start_seconds=0.0,
            source_end_seconds=2.0,
            folder_name="Milos",
            coverage_gap_id="gap_1",
            open_gap=True,
        ),
        ResolvedShot(
            shot_id="s3",
            asset_id="other_asset",
            timeline_start_seconds=4.0,
            timeline_end_seconds=6.0,
            source_start_seconds=0.0,
            source_end_seconds=2.0,
            folder_name="Milos",
            open_gap=False,
        ),
    ]
    reason = _gap_fill_reuse_violation(
        "manual_milos_slot_002",
        provisional_shots=shots,
        gap_shot_id="s2",
        max_asset_usage=4,
        min_asset_reuse_distance_shots=4,
    )
    assert reason is not None
    assert "Benachbartes Asset" in reason


def test_gap_fill_rejects_reuse_inside_distance() -> None:
    shots = [
        ResolvedShot(
            shot_id="s1",
            asset_id="asset_a",
            timeline_start_seconds=0.0,
            timeline_end_seconds=1.0,
            source_start_seconds=0.0,
            source_end_seconds=1.0,
            folder_name="Milos",
        ),
        ResolvedShot(
            shot_id="s2",
            asset_id="asset_b",
            timeline_start_seconds=1.0,
            timeline_end_seconds=2.0,
            source_start_seconds=0.0,
            source_end_seconds=1.0,
            folder_name="Milos",
        ),
        ResolvedShot(
            shot_id="s3",
            asset_id="",
            timeline_start_seconds=2.0,
            timeline_end_seconds=3.0,
            source_start_seconds=0.0,
            source_end_seconds=1.0,
            folder_name="Milos",
            coverage_gap_id="gap_1",
            open_gap=True,
        ),
    ]
    reason = _gap_fill_reuse_violation(
        "asset_a",
        provisional_shots=shots,
        gap_shot_id="s3",
        max_asset_usage=4,
        min_asset_reuse_distance_shots=4,
    )
    assert reason is not None
    assert "min Abstand" in reason


def test_gap_fill_allows_reuse_after_distance() -> None:
    shots = [
        ResolvedShot(
            shot_id=f"s{i}",
            asset_id=f"asset_{i}",
            timeline_start_seconds=float(i),
            timeline_end_seconds=float(i + 1),
            source_start_seconds=0.0,
            source_end_seconds=1.0,
            folder_name="Milos",
        )
        for i in range(1, 6)
    ]
    shots[0] = shots[0].model_copy(update={"asset_id": "asset_a"})
    shots.append(
        ResolvedShot(
            shot_id="gap",
            asset_id="",
            timeline_start_seconds=6.0,
            timeline_end_seconds=7.0,
            source_start_seconds=0.0,
            source_end_seconds=1.0,
            folder_name="Milos",
            coverage_gap_id="gap_1",
            open_gap=True,
        )
    )
    # Indices: asset_a at 0, then 4 others (1..4), gap at 5 → gap_shots = 4
    reason = _gap_fill_reuse_violation(
        "asset_a",
        provisional_shots=shots,
        gap_shot_id="gap",
        max_asset_usage=4,
        min_asset_reuse_distance_shots=4,
    )
    assert reason is None


def test_filter_candidates_skips_neighbor_duplicate() -> None:
    shots = [
        ResolvedShot(
            shot_id="prev",
            asset_id="dup_1",
            timeline_start_seconds=0.0,
            timeline_end_seconds=1.0,
            source_start_seconds=0.0,
            source_end_seconds=1.0,
            folder_name="Milos",
        ),
        ResolvedShot(
            shot_id="gap",
            asset_id="",
            timeline_start_seconds=1.0,
            timeline_end_seconds=2.0,
            source_start_seconds=0.0,
            source_end_seconds=1.0,
            folder_name="Milos",
            open_gap=True,
            coverage_gap_id="g1",
        ),
    ]
    candidates = [
        StockCandidate(
            candidate_id="dup_1",
            provider="manual",
            preview_url="https://example.com/a.jpg",
            download_url="https://example.com/a.jpg",
        ),
        StockCandidate(
            candidate_id="other_1",
            provider="manual",
            preview_url="https://example.com/b.jpg",
            download_url="https://example.com/b.jpg",
        ),
    ]
    kept, rejected = _filter_candidates_by_cut_plan_reuse(
        candidates,
        provisional_shots=shots,
        gap_shot_id="gap",
        max_asset_usage=4,
        min_asset_reuse_distance_shots=4,
    )
    assert [c.candidate_id for c in kept] == ["other_1"]
    assert rejected and "Benachbartes Asset" in rejected[0]


def _project(tmp_path: Path) -> Project:
    root = tmp_path / "proj"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    project = Project(
        name="p",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        asset_subdir_names=["Milos"],
        selected_asset_subdirs=["Milos"],
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
    save_cut_plan_options(
        project,
        CutPlanOptions(
            project_id=project.id,
            min_asset_reuse_distance_shots=4,
            max_asset_usage=4,
        ),
    )
    return project


def test_gap_merge_skips_candidate_matching_neighbor(tmp_path: Path) -> None:
    project = _project(tmp_path)
    media_dup = project.work_dir_path / "dup.jpg"
    media_ok = project.work_dir_path / "ok.jpg"
    media_dup.write_bytes(b"img")
    media_ok.write_bytes(b"img")

    write_json(
        coverage_gaps_path(project),
        CoverageGapsDocument(
            script_version="script-v1",
            gaps=[
                CoverageGap(
                    gap_id="gap_1",
                    related_shot_ids=["slot_002"],
                    needed_visual="cove",
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
                    candidate_id="manual_milos_slot_002",
                    provider="manual",
                    provider_asset_id="m2",
                    media_type="photo",
                    gap_id="gap_1",
                    local_media_path=str(media_dup),
                    media_validation_status="export_ready",
                    funnel_managed=True,
                    preview_url="https://example.com/dup.jpg",
                    download_url="https://example.com/dup.jpg",
                ),
                StockCandidate(
                    candidate_id="manual_milos_ok",
                    provider="manual",
                    provider_asset_id="m3",
                    media_type="photo",
                    gap_id="gap_1",
                    local_media_path=str(media_ok),
                    media_validation_status="export_ready",
                    funnel_managed=True,
                    preview_url="https://example.com/ok.jpg",
                    download_url="https://example.com/ok.jpg",
                ),
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
                    export_ready_candidate_id="manual_milos_slot_002",
                    filled=True,
                    candidates=[
                        FunnelCandidateRecord(
                            candidate_id="manual_milos_slot_002",
                            final_score=90,
                            fit_bucket="strong",
                            decision="winner",
                        ),
                        FunnelCandidateRecord(
                            candidate_id="manual_milos_ok",
                            final_score=70,
                            fit_bucket="acceptable",
                            decision="fallback",
                        ),
                    ],
                )
            ],
        ),
    )
    timeline = ResolvedTimelineDocument(
        script_version="script-v1",
        fps=25.0,
        total_duration_seconds=6.0,
        shots=[
            ResolvedShot(
                shot_id="slot_001",
                asset_id="manual_milos_slot_002",
                timeline_start_seconds=0.0,
                timeline_end_seconds=2.0,
                source_start_seconds=0.0,
                source_end_seconds=2.0,
                folder_name="Milos",
                editorial_function="evidence",
                asset_fit="acceptable",
                resolved_media_kind="image",
                resolved_media_path=str(media_dup),
            ),
            ResolvedShot(
                shot_id="slot_002",
                asset_id="",
                timeline_start_seconds=2.0,
                timeline_end_seconds=4.0,
                source_start_seconds=0.0,
                source_end_seconds=2.0,
                folder_name="Milos",
                editorial_function="evidence",
                asset_fit="none",
                coverage_gap_id="gap_1",
                open_gap=True,
                resolved_media_kind="placeholder",
            ),
        ],
    )
    write_json(resolved_timeline_path(project), timeline)

    from otio_app.services.without_voiceover_enhanced import gap_merge_service as gms

    ready = [
        StockCandidate(
            candidate_id="manual_milos_slot_002",
            provider="manual",
            provider_asset_id="m2",
            media_type="photo",
            gap_id="gap_1",
            local_media_path=str(media_dup),
            media_validation_status="export_ready",
            funnel_managed=True,
            preview_url="https://example.com/dup.jpg",
            download_url="https://example.com/dup.jpg",
        ),
        StockCandidate(
            candidate_id="manual_milos_ok",
            provider="manual",
            provider_asset_id="m3",
            media_type="photo",
            gap_id="gap_1",
            local_media_path=str(media_ok),
            media_validation_status="export_ready",
            funnel_managed=True,
            preview_url="https://example.com/ok.jpg",
            download_url="https://example.com/ok.jpg",
        ),
    ]

    def _fake_resolve(project, **kwargs):  # noqa: ANN001, ANN003
        asset_id = str(kwargs.get("asset_id") or "")
        path = str(media_ok if "ok" in asset_id else media_dup)
        return ResolvedShot(
            shot_id=str(kwargs.get("shot_id") or ""),
            asset_id=asset_id,
            timeline_start_seconds=float(kwargs.get("timeline_start") or 0.0),
            timeline_end_seconds=float(kwargs.get("timeline_end") or 0.0),
            source_start_seconds=0.0,
            source_end_seconds=2.0,
            folder_name="Milos",
            editorial_function=str(kwargs.get("editorial_function") or ""),
            asset_fit="acceptable",
            resolved_media_kind="image",
            resolved_media_path=path,
            open_gap=False,
        )

    original_list = gms.list_export_ready_supplements
    original_resolve = gms._resolve_shot_media
    gms.list_export_ready_supplements = lambda _project: ready  # type: ignore[assignment]
    gms._resolve_shot_media = _fake_resolve  # type: ignore[assignment]
    try:
        merged, report = merge_export_ready_gaps_into_timeline(
            project, timeline=timeline, persist=False, persist_report=False
        )
    finally:
        gms.list_export_ready_supplements = original_list  # type: ignore[assignment]
        gms._resolve_shot_media = original_resolve  # type: ignore[assignment]

    gap_shot = next(s for s in merged.shots if s.shot_id == "slot_002")
    assert gap_shot.asset_id == "manual_milos_ok"
    assert gap_shot.open_gap is False
    assert any(s.status == "merged" for s in report.slots)
