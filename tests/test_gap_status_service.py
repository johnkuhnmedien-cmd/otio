"""Gap-Status — Funnel/Accepted schließt Gaps; stale Run-IDs invalidieren."""

from __future__ import annotations

from pathlib import Path

from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.services.without_voiceover_enhanced.gap_status_service import (
    compute_cut_plan_run_id,
    is_weak_upgrade_gap,
    summarize_gap_status,
)
from otio_app.services.without_voiceover_enhanced.io_utils import load_model, write_json
from otio_app.services.without_voiceover_enhanced.models import (
    CoverageGap,
    CoverageGapsDocument,
    CutBoundary,
    CutSlot,
    GapMergeReport,
    GapMergeSlotResult,
    SupplementFunnelGapReport,
    SupplementFunnelReport,
    UnifiedCutPlanDocument,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    coverage_gaps_path,
    gap_merge_report_path,
    supplement_funnel_report_path,
    unified_cut_plan_path,
)
from otio_app.services.without_voiceover_enhanced.supplement_funnel_service import (
    list_open_funnel_gap_ids,
)
from otio_app.services.without_voiceover_enhanced.unified_cut_plan import unified_to_rough


def _project(tmp_path: Path) -> Project:
    root = tmp_path / "proj"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    return Project(
        id="gap-status",
        name="gap-status",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        asset_subdir_names=["A"],
        selected_asset_subdirs=["A"],
    )


def _photo(project: Project, name: str) -> Path:
    from PIL import Image

    path = project.work_dir_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), color=(20, 80, 40)).save(path, format="JPEG")
    return path


def _plan() -> UnifiedCutPlanDocument:
    return UnifiedCutPlanDocument(
        script_version="script-v1",
        boundaries=[
            CutBoundary(cut_id="b0", sentence_id="a__s001", position="start"),
            CutBoundary(cut_id="b1", sentence_id="a__s002", position="middle"),
            CutBoundary(cut_id="b2", sentence_id="a__s003", position="end"),
        ],
        slots=[
            CutSlot(
                slot_id="A_slot_weak",
                local_asset_id="loc_weak",
                asset_fit="weak",
                coverage_gap_id="gap_A_slot_weak",
                needed_visual="better light",
            ),
            CutSlot(
                slot_id="A_slot_none",
                local_asset_id=None,
                asset_fit="none",
                coverage_gap_id="gap_A_slot_none",
                needed_visual="street",
            ),
        ],
    )


def test_unified_to_rough_writes_cut_plan_run_id() -> None:
    plan = _plan()
    _rough, coverage = unified_to_rough(plan)
    assert coverage.cut_plan_run_id
    assert coverage.cut_plan_run_id == compute_cut_plan_run_id(plan)
    assert is_weak_upgrade_gap(coverage.gaps[0])
    assert not is_weak_upgrade_gap(coverage.gaps[1])


def test_weak_closes_when_funnel_export_ready(tmp_path: Path) -> None:
    """Merge-fähiges Accepted (Still) schließt weak/none — Funnel-JSON allein nicht."""
    from otio_app.services.without_voiceover_enhanced.models import (
        AcceptedSupplementsDocument,
        StockCandidate,
    )
    from otio_app.services.without_voiceover_enhanced.paths import (
        accepted_supplements_path,
    )

    project = _project(tmp_path)
    plan = _plan()
    write_json(unified_cut_plan_path(project), plan)
    _rough, coverage = unified_to_rough(plan)
    write_json(coverage_gaps_path(project), coverage)
    weak_path = _photo(project, "weak.jpg")
    none_path = _photo(project, "none.jpg")
    write_json(
        accepted_supplements_path(project),
        AcceptedSupplementsDocument(
            script_version="script-v1",
            supplements=[
                StockCandidate(
                    candidate_id="cand_weak",
                    provider="pexels",
                    media_type="photo",
                    gap_id="gap_A_slot_weak",
                    local_media_path=str(weak_path),
                    media_validation_status="export_ready",
                    cut_plan_run_id=coverage.cut_plan_run_id,
                ),
                StockCandidate(
                    candidate_id="cand_none",
                    provider="pexels",
                    media_type="photo",
                    gap_id="gap_A_slot_none",
                    local_media_path=str(none_path),
                    media_validation_status="export_ready",
                    cut_plan_run_id=coverage.cut_plan_run_id,
                ),
            ],
        ),
    )
    write_json(
        supplement_funnel_report_path(project),
        SupplementFunnelReport(
            run_id="funnel_x",
            script_version="script-v1",
            cut_plan_run_id=coverage.cut_plan_run_id,
            gaps=[
                SupplementFunnelGapReport(
                    gap_id="gap_A_slot_weak",
                    filled=True,
                    export_ready_candidate_id="cand_weak",
                ),
                SupplementFunnelGapReport(
                    gap_id="gap_A_slot_none",
                    filled=True,
                    export_ready_candidate_id="cand_none",
                ),
            ],
            filled_gap_ids=["gap_A_slot_weak", "gap_A_slot_none"],
        ),
    )

    status = summarize_gap_status(project)
    assert status.total == 2
    assert status.open_count == 0
    assert set(status.filled_gap_ids) == {"gap_A_slot_weak", "gap_A_slot_none"}
    assert list_open_funnel_gap_ids(project) == []


def test_accepted_export_ready_closes_gap_without_funnel_entry(
    tmp_path: Path,
) -> None:
    """Bereits akzeptierte Downloads zählen als erfüllt, auch ohne Funnel-filled."""
    from otio_app.services.without_voiceover_enhanced.models import (
        AcceptedSupplementsDocument,
        StockCandidate,
    )
    from otio_app.services.without_voiceover_enhanced.paths import (
        accepted_supplements_path,
    )

    project = _project(tmp_path)
    plan = _plan()
    write_json(unified_cut_plan_path(project), plan)
    _rough, coverage = unified_to_rough(plan)
    write_json(coverage_gaps_path(project), coverage)
    prev = _photo(project, "prev.jpg")
    write_json(
        accepted_supplements_path(project),
        AcceptedSupplementsDocument(
            script_version="script-v1",
            supplements=[
                StockCandidate(
                    candidate_id="cand_prev",
                    provider="pexels",
                    media_type="photo",
                    gap_id="gap_A_slot_weak",
                    media_validation_status="export_ready",
                    cut_plan_run_id=coverage.cut_plan_run_id,
                    local_media_path=str(prev),
                )
            ],
        ),
    )

    status = summarize_gap_status(project)
    assert "gap_A_slot_weak" in status.filled_gap_ids
    assert "gap_A_slot_none" in status.open_gap_ids
    assert status.filled_count == 1
    assert status.open_count == 1
    assert list_open_funnel_gap_ids(project) == ["gap_A_slot_none"]


def test_weak_closes_only_after_merge_decision(tmp_path: Path) -> None:
    project = _project(tmp_path)
    plan = _plan()
    write_json(unified_cut_plan_path(project), plan)
    _rough, coverage = unified_to_rough(plan)
    write_json(coverage_gaps_path(project), coverage)
    write_json(
        gap_merge_report_path(project),
        GapMergeReport(
            script_version="script-v1",
            cut_plan_run_id=coverage.cut_plan_run_id,
            slots=[
                GapMergeSlotResult(
                    shot_id="A_slot_weak",
                    coverage_gap_id="gap_A_slot_weak",
                    status="kept_local_weak",
                ),
                GapMergeSlotResult(
                    shot_id="A_slot_none",
                    coverage_gap_id="gap_A_slot_none",
                    status="merged",
                ),
            ],
        ),
    )

    status = summarize_gap_status(project)
    assert status.open_count == 0
    assert set(status.filled_gap_ids) == {"gap_A_slot_weak", "gap_A_slot_none"}
    assert list_open_funnel_gap_ids(project) == []


def test_still_image_closes_gap_even_if_labeled_video(tmp_path: Path) -> None:
    """Wikimedia-Foto als video getaggt darf Auto-Lauf und UI nicht spalten."""
    from otio_app.services.without_voiceover_enhanced.models import (
        AcceptedSupplementsDocument,
        StockCandidate,
    )
    from otio_app.services.without_voiceover_enhanced.paths import (
        accepted_supplements_path,
    )

    project = _project(tmp_path)
    plan = _plan()
    write_json(unified_cut_plan_path(project), plan)
    _rough, coverage = unified_to_rough(plan)
    write_json(coverage_gaps_path(project), coverage)
    still = _photo(project, "gorge.jpg")
    write_json(
        accepted_supplements_path(project),
        AcceptedSupplementsDocument(
            script_version="script-v1",
            supplements=[
                StockCandidate(
                    candidate_id="wiki_tolmin",
                    provider="wikimedia",
                    media_type="video",
                    gap_id="gap_A_slot_none",
                    local_media_path=str(still),
                    media_validation_status="export_ready",
                    cut_plan_run_id=coverage.cut_plan_run_id,
                    duration_seconds=0.0,
                )
            ],
        ),
    )
    status = summarize_gap_status(project)
    assert "gap_A_slot_none" in status.filled_gap_ids
    assert "gap_A_slot_none" not in list_open_funnel_gap_ids(project)


def test_cut_plan_tab_lists_same_open_ids_as_auto_run() -> None:
    ui = (
        Path(__file__).resolve().parents[1]
        / "otio_app"
        / "ui"
        / "without_voiceover_enhanced"
        / "cut_plan_tab.py"
    ).read_text(encoding="utf-8")
    assert "Noch offen (gleiche Liste wie Auto-Lauf)" in ui
    funnel = (
        Path(__file__).resolve().parents[1]
        / "otio_app"
        / "services"
        / "without_voiceover_enhanced"
        / "supplement_funnel_service.py"
    ).read_text(encoding="utf-8")
    assert "list(summarize_gap_status(project).open_gap_ids)" in funnel


def test_stale_funnel_without_accepted_does_not_count_as_filled(
    tmp_path: Path,
) -> None:
    """Nur Funnel-Report ohne Accepted → kein Auto-Rebind, bleibt offen."""
    project = _project(tmp_path)
    plan = _plan()
    write_json(unified_cut_plan_path(project), plan)
    _rough, coverage = unified_to_rough(plan)
    write_json(coverage_gaps_path(project), coverage)
    write_json(
        supplement_funnel_report_path(project),
        SupplementFunnelReport(
            run_id="funnel_old",
            script_version="script-v1",
            cut_plan_run_id="stale_run_id",
            gaps=[
                SupplementFunnelGapReport(
                    gap_id="gap_A_slot_none",
                    filled=True,
                    export_ready_candidate_id="old_cand",
                )
            ],
            filled_gap_ids=["gap_A_slot_none"],
        ),
    )

    status = summarize_gap_status(project)
    assert status.funnel_stale is True
    assert "gap_A_slot_none" in status.open_gap_ids
    assert status.filled_count == 0
    assert list_open_funnel_gap_ids(project) == ["gap_A_slot_weak", "gap_A_slot_none"]


def test_restore_accepted_from_funnel_when_accepted_was_purged(
    tmp_path: Path,
) -> None:
    """Alte Migration leerte Accepted — Fills aus Funnel + Datei wiederherstellen."""
    from otio_app.services.without_voiceover_enhanced.models import (
        AcceptedSupplementsDocument,
        FunnelCandidateRecord,
    )
    from otio_app.services.without_voiceover_enhanced.paths import (
        accepted_supplements_path,
        stock_candidate_download_dir,
    )

    project = _project(tmp_path)
    plan = _plan()
    write_json(unified_cut_plan_path(project), plan)
    _rough, coverage = unified_to_rough(plan)
    write_json(coverage_gaps_path(project), coverage)

    media_dir = stock_candidate_download_dir(
        project, gap_id="gap_A_slot_none", candidate_id="pexels_video_restore"
    )
    media_dir.mkdir(parents=True)
    media = media_dir / "pexels_video_restore.jpg"
    from PIL import Image

    Image.new("RGB", (8, 8), color=(20, 80, 40)).save(media, format="JPEG")

    write_json(
        supplement_funnel_report_path(project),
        SupplementFunnelReport(
            run_id="funnel_old",
            script_version="script-v1",
            cut_plan_run_id="old_run",
            filled_gap_ids=["gap_A_slot_none"],
            gaps=[
                SupplementFunnelGapReport(
                    gap_id="gap_A_slot_none",
                    filled=True,
                    export_ready_candidate_id="pexels_video_restore",
                    candidates=[
                        FunnelCandidateRecord(
                            candidate_id="pexels_video_restore",
                            provider="pexels",
                            funnel_status="export_ready",
                            local_media_path=str(media),
                        )
                    ],
                )
            ],
        ),
    )

    status = summarize_gap_status(project)
    assert "gap_A_slot_none" in status.filled_gap_ids
    assert "wiederhergestellt" in (status.message or "").lower() or status.filled_count >= 1
    accepted = load_model(
        accepted_supplements_path(project), AcceptedSupplementsDocument
    )
    assert accepted is not None
    assert any(s.candidate_id == "pexels_video_restore" for s in accepted.supplements)


def test_accepted_with_old_run_id_rebinds_to_current_plan(tmp_path: Path) -> None:
    """Nach neuem Cut-Plan-Lauf: Accepted mit gleicher Gap-ID wieder erfüllt."""
    from otio_app.services.without_voiceover_enhanced.models import (
        AcceptedSupplementsDocument,
        StockCandidate,
    )
    from otio_app.services.without_voiceover_enhanced.paths import (
        accepted_supplements_path,
    )

    project = _project(tmp_path)
    plan = _plan()
    write_json(unified_cut_plan_path(project), plan)
    _rough, coverage = unified_to_rough(plan)
    write_json(coverage_gaps_path(project), coverage)
    none_path = _photo(project, "manual.jpg")
    weak_path = _photo(project, "weak.jpg")
    write_json(
        accepted_supplements_path(project),
        AcceptedSupplementsDocument(
            script_version="script-v1",
            supplements=[
                StockCandidate(
                    candidate_id="cand_manual",
                    provider="manual",
                    media_type="photo",
                    gap_id="gap_A_slot_none",
                    media_validation_status="export_ready",
                    cut_plan_run_id="old_run_before_llm_recut",
                    local_media_path=str(none_path),
                ),
                StockCandidate(
                    candidate_id="cand_weak",
                    provider="pexels",
                    media_type="photo",
                    gap_id="gap_A_slot_weak",
                    media_validation_status="export_ready",
                    cut_plan_run_id="old_run_before_llm_recut",
                    local_media_path=str(weak_path),
                ),
            ],
        ),
    )
    write_json(
        supplement_funnel_report_path(project),
        SupplementFunnelReport(
            run_id="funnel_old",
            script_version="script-v1",
            cut_plan_run_id="old_run_before_llm_recut",
            filled_gap_ids=["gap_A_slot_none", "gap_A_slot_weak"],
            gaps=[
                SupplementFunnelGapReport(
                    gap_id="gap_A_slot_none",
                    filled=True,
                    export_ready_candidate_id="cand_manual",
                ),
                SupplementFunnelGapReport(
                    gap_id="gap_A_slot_weak",
                    filled=True,
                    export_ready_candidate_id="cand_weak",
                ),
            ],
        ),
    )

    status = summarize_gap_status(project)
    assert status.open_count == 0
    assert set(status.filled_gap_ids) == {"gap_A_slot_weak", "gap_A_slot_none"}
    assert status.funnel_stale is False
    assert "Accepted-Fill" in (status.message or "")
    assert list_open_funnel_gap_ids(project) == []

    accepted = load_model(
        accepted_supplements_path(project), AcceptedSupplementsDocument
    )
    assert accepted is not None
    assert all(
        s.cut_plan_run_id == coverage.cut_plan_run_id for s in accepted.supplements
    )


def test_missing_funnel_run_id_treated_as_stale_when_coverage_has_run(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    plan = _plan()
    write_json(unified_cut_plan_path(project), plan)
    _rough, coverage = unified_to_rough(plan)
    write_json(coverage_gaps_path(project), coverage)
    write_json(
        supplement_funnel_report_path(project),
        SupplementFunnelReport(
            run_id="funnel_legacy",
            script_version="script-v1",
            cut_plan_run_id="",  # alter Report
            filled_gap_ids=["gap_A_slot_none"],
            gaps=[
                SupplementFunnelGapReport(
                    gap_id="gap_A_slot_none",
                    filled=True,
                    export_ready_candidate_id="legacy",
                )
            ],
        ),
    )

    status = summarize_gap_status(project)
    assert status.funnel_stale is True
    assert "gap_A_slot_none" in status.open_gap_ids
    assert list_open_funnel_gap_ids(project) == ["gap_A_slot_weak", "gap_A_slot_none"]


def test_legacy_coverage_without_run_id_still_lists_open_gaps(tmp_path: Path) -> None:
    """E2E-4: Funnel-filled allein reicht nicht — ohne merge-fähiges Accepted offen."""
    project = _project(tmp_path)
    write_json(
        coverage_gaps_path(project),
        CoverageGapsDocument(
            script_version="script-v1",
            cut_plan_run_id="",
            gaps=[
                CoverageGap(gap_id="gap_1", needed_visual="a", priority="high"),
                CoverageGap(gap_id="gap_2", needed_visual="b", priority="medium"),
            ],
        ),
    )
    write_json(
        supplement_funnel_report_path(project),
        SupplementFunnelReport(
            run_id="funnel_legacy",
            script_version="script-v1",
            cut_plan_run_id="",
            filled_gap_ids=["gap_1"],
            gaps=[
                SupplementFunnelGapReport(
                    gap_id="gap_1",
                    filled=True,
                    export_ready_candidate_id="c1",
                )
            ],
        ),
    )
    # Kein Accepted mit lokaler Datei → Gap bleibt für Funnel UND UI offen.
    assert list_open_funnel_gap_ids(project) == ["gap_1", "gap_2"]
    status = summarize_gap_status(project)
    assert "gap_1" in status.open_gap_ids
    assert "gap_2" in status.open_gap_ids
    assert "merge-fähig" in (status.message or "")


def test_stale_weak_confirm_on_high_gap_is_reset(tmp_path: Path) -> None:
    """Alt-Bestätigung auf high/none zählt nicht und wird aus Coverage gelöscht."""
    project = _project(tmp_path)
    write_json(
        coverage_gaps_path(project),
        CoverageGapsDocument(
            script_version="script-v1",
            cut_plan_run_id="run_new",
            gaps=[
                CoverageGap(
                    gap_id="Dublin_gap_001",
                    needed_visual="doors",
                    priority="high",
                    user_confirmed_weak=True,
                ),
                CoverageGap(
                    gap_id="gap_weak_ok",
                    needed_visual="light",
                    priority="medium",
                    user_confirmed_weak=True,
                ),
            ],
        ),
    )
    status = summarize_gap_status(project)
    assert "Dublin_gap_001" in status.open_gap_ids
    assert "gap_weak_ok" in status.filled_gap_ids
    assert "gap_weak_ok" not in list_open_funnel_gap_ids(project)
    assert "veraltete Weak-Bestätigung" in (status.message or "")

    reloaded = load_model(coverage_gaps_path(project), CoverageGapsDocument)
    assert reloaded is not None
    by_id = {g.gap_id: g for g in reloaded.gaps}
    assert by_id["Dublin_gap_001"].user_confirmed_weak is False
    assert by_id["gap_weak_ok"].user_confirmed_weak is True


def test_summarize_adds_plan_gaps_missing_from_coverage_json(tmp_path: Path) -> None:
    """Kapitel-Plan-none-Slot muss im Funnel auftauchen, nicht nur Timing blockieren."""
    from otio_app.services.without_voiceover_enhanced.chapter_cut_service import (
        persist_chapter_unified_plan,
    )

    project = _project(tmp_path)
    write_json(
        coverage_gaps_path(project),
        CoverageGapsDocument(
            script_version="script-v1",
            cut_plan_run_id="run_keep",
            gaps=[
                CoverageGap(
                    gap_id="gap_already_filled",
                    needed_visual="old",
                    priority="medium",
                    user_confirmed_weak=True,
                )
            ],
        ),
    )
    plan = UnifiedCutPlanDocument(
        script_version="script-v1",
        boundaries=[
            CutBoundary(cut_id="b0", sentence_id="a__s001", position="start"),
            CutBoundary(cut_id="b1", sentence_id="a__s002", position="end"),
        ],
        slots=[
            CutSlot(
                slot_id="A_slot_009",
                local_asset_id=None,
                asset_fit="none",
                coverage_gap_id="gap_A_slot_009",
                needed_visual="stork",
                asset_fit_reason="Datei fehlt",
            )
        ],
    )
    persist_chapter_unified_plan(
        project, "A", plan, refresh_merged=False, reset_open_gaps=False
    )

    status = summarize_gap_status(project)

    assert "gap_already_filled" in status.filled_gap_ids
    assert "gap_A_slot_009" in status.open_gap_ids
    assert status.cut_plan_run_id == "run_keep"
    assert "nachgetragen" in (status.message or "")

    coverage = load_model(coverage_gaps_path(project), CoverageGapsDocument)
    assert coverage is not None
    assert {gap.gap_id for gap in coverage.gaps} == {
        "gap_already_filled",
        "gap_A_slot_009",
    }
    assert "gap_A_slot_009" in list_open_funnel_gap_ids(project)


def test_sync_uses_slot_id_not_llm_counter_gap_id(tmp_path: Path) -> None:
    """Bestehende JSON mit Piran_gap_001 öffnet gap_Piran_slot_011 im Funnel."""
    from otio_app.services.without_voiceover_enhanced.gap_status_service import (
        sync_missing_plan_gaps_into_coverage,
    )
    from otio_app.services.without_voiceover_enhanced.paths import (
        chapter_unified_cut_plan_path,
    )

    project = _project(tmp_path)
    plan = UnifiedCutPlanDocument(
        script_version="script-v1",
        boundaries=[
            CutBoundary(cut_id="b0", sentence_id="p__s001", position="start"),
            CutBoundary(cut_id="b1", sentence_id="p__s002", position="end"),
        ],
        slots=[
            CutSlot(
                slot_id="Piran_slot_011",
                local_asset_id=None,
                asset_fit="none",
                coverage_gap_id="Piran_gap_001",
                needed_visual="Sečovlje salt harvesting",
                search_concepts=["salt pans", "sečovlje harvest"],
                asset_fit_reason="no matching local clip",
            )
        ],
    )
    write_json(chapter_unified_cut_plan_path(project, "Piran"), plan)
    write_json(
        coverage_gaps_path(project),
        CoverageGapsDocument(
            script_version="script-v1",
            cut_plan_run_id="run_keep",
            gaps=[],
        ),
    )

    added = sync_missing_plan_gaps_into_coverage(project)
    assert added == ["gap_Piran_slot_011"]

    status = summarize_gap_status(project)
    assert "gap_Piran_slot_011" in status.open_gap_ids
    assert "Piran_gap_001" not in status.open_gap_ids
    assert "Piran_gap_001" not in status.filled_gap_ids


def _piran_none_plan() -> UnifiedCutPlanDocument:
    return UnifiedCutPlanDocument(
        script_version="script-v1",
        boundaries=[
            CutBoundary(cut_id="b0", sentence_id="p__s001", position="start"),
            CutBoundary(cut_id="b1", sentence_id="p__s002", position="end"),
        ],
        slots=[
            CutSlot(
                slot_id="Piran_slot_011",
                local_asset_id=None,
                asset_fit="none",
                coverage_gap_id="Piran_gap_001",
                needed_visual="Sečovlje salt harvesting",
                search_concepts=["salt pans", "sečovlje harvest"],
                asset_fit_reason="no matching local clip",
            )
        ],
    )


def test_migrate_merges_llm_counter_fill_onto_slot_id(tmp_path: Path) -> None:
    """Alte erfüllte ID + neue offene Slot-ID werden eine erfüllte Lücke."""
    from otio_app.services.without_voiceover_enhanced.models import (
        AcceptedSupplementsDocument,
        StockCandidate,
    )
    from otio_app.services.without_voiceover_enhanced.paths import (
        accepted_supplements_path,
        chapter_unified_cut_plan_path,
        supplement_funnel_report_path,
    )

    project = _project(tmp_path)
    fill = _photo(project, "salt.jpg")
    write_json(chapter_unified_cut_plan_path(project, "Piran"), _piran_none_plan())
    write_json(
        coverage_gaps_path(project),
        CoverageGapsDocument(
            script_version="script-v1",
            cut_plan_run_id="run_keep",
            gaps=[
                CoverageGap(
                    gap_id="Piran_gap_001",
                    needed_visual="Sečovlje salt harvesting",
                    priority="high",
                    related_shot_ids=["Piran_slot_011"],
                ),
                CoverageGap(
                    gap_id="gap_Piran_slot_011",
                    needed_visual="Sečovlje salt harvesting",
                    priority="high",
                    related_shot_ids=["Piran_slot_011"],
                ),
            ],
        ),
    )
    write_json(
        accepted_supplements_path(project),
        AcceptedSupplementsDocument(
            script_version="script-v1",
            supplements=[
                StockCandidate(
                    candidate_id="cand_salt",
                    provider="pexels",
                    media_type="photo",
                    gap_id="Piran_gap_001",
                    local_media_path=str(fill),
                    media_validation_status="export_ready",
                    cut_plan_run_id="run_keep",
                )
            ],
        ),
    )
    write_json(
        supplement_funnel_report_path(project),
        SupplementFunnelReport(
            run_id="funnel_x",
            script_version="script-v1",
            cut_plan_run_id="run_keep",
            gaps=[
                SupplementFunnelGapReport(
                    gap_id="Piran_gap_001",
                    filled=True,
                    export_ready_candidate_id="cand_salt",
                )
            ],
            filled_gap_ids=["Piran_gap_001"],
            open_gap_ids=["gap_Piran_slot_011"],
        ),
    )

    status = summarize_gap_status(project)

    assert status.total == 1
    assert status.open_gap_ids == []
    assert status.filled_gap_ids == ["gap_Piran_slot_011"]
    coverage = load_model(coverage_gaps_path(project), CoverageGapsDocument)
    assert coverage is not None
    assert [gap.gap_id for gap in coverage.gaps] == ["gap_Piran_slot_011"]


def test_migrate_keeps_placeholder_slot_open(tmp_path: Path) -> None:
    """Timing-Platzhalter ohne Clip: alter Fill darf die Slot-Lücke nicht schließen."""
    from otio_app.services.without_voiceover_enhanced.models import (
        ResolvedShot,
        ResolvedTimelineDocument,
    )
    from otio_app.services.without_voiceover_enhanced.paths import (
        chapter_resolved_timeline_path,
        chapter_unified_cut_plan_path,
        supplement_funnel_report_path,
    )

    project = _project(tmp_path)
    write_json(chapter_unified_cut_plan_path(project, "Piran"), _piran_none_plan())
    write_json(
        coverage_gaps_path(project),
        CoverageGapsDocument(
            script_version="script-v1",
            cut_plan_run_id="run_keep",
            gaps=[
                CoverageGap(
                    gap_id="Piran_gap_001",
                    needed_visual="old fill",
                    priority="high",
                ),
                CoverageGap(
                    gap_id="gap_Piran_slot_011",
                    needed_visual="Sečovlje salt harvesting",
                    priority="high",
                    related_shot_ids=["Piran_slot_011"],
                ),
            ],
        ),
    )
    write_json(
        supplement_funnel_report_path(project),
        SupplementFunnelReport(
            run_id="funnel_x",
            script_version="script-v1",
            cut_plan_run_id="run_keep",
            gaps=[
                SupplementFunnelGapReport(
                    gap_id="Piran_gap_001",
                    filled=True,
                    export_ready_candidate_id="cand_old",
                )
            ],
            filled_gap_ids=["Piran_gap_001"],
        ),
    )
    write_json(
        chapter_resolved_timeline_path(project, "Piran"),
        ResolvedTimelineDocument(
            script_version="script-v1",
            fps=25.0,
            total_duration_seconds=8.0,
            shots=[
                ResolvedShot(
                    shot_id="Piran_slot_011",
                    asset_id="",
                    timeline_start_seconds=0.0,
                    timeline_end_seconds=8.0,
                    source_start_seconds=0.0,
                    source_end_seconds=0.0,
                    is_placeholder=True,
                    hold_mode="placeholder_slate",
                    open_gap=True,
                )
            ],
        ),
    )

    status = summarize_gap_status(project)

    assert status.total == 1
    assert status.open_gap_ids == ["gap_Piran_slot_011"]
    assert status.filled_gap_ids == []
    assert "Piran_gap_001" not in status.open_gap_ids
    assert "Piran_gap_001" not in status.filled_gap_ids


def test_cut_plan_ui_lists_chapters_blocked_by_open_gaps() -> None:
    src = Path(
        "otio_app/ui/without_voiceover_enhanced/cut_plan_tab.py"
    ).read_text(encoding="utf-8")
    assert "blocked_gap_statuses" in src
    assert "chapter_count - len(ready_timing_names)" not in src
    assert "Supplements / Funnel" in src
