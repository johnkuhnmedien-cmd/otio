"""Enhanced: generischer Ordner-Fallback nach Stock-/Funnel-Fail."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis
from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import get_folder_inventory_path
from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
    CutPlanOptions,
    save_cut_plan_options,
)
from otio_app.services.without_voiceover_enhanced.generic_gap_fallback_service import (
    apply_generic_fallback_to_gap,
    apply_generic_fallback_to_open_gaps,
    select_generic_fallback_for_gap,
    try_generic_fallback_after_stock_fail,
    build_asset_usage_ledger,
)
from otio_app.services.without_voiceover_enhanced.gap_status_service import (
    summarize_gap_status,
)
from otio_app.services.without_voiceover_enhanced.io_utils import load_model, write_json
from otio_app.services.without_voiceover_enhanced.models import (
    AcceptedSupplementsDocument,
    CoverageGap,
    CoverageGapsDocument,
    CutBoundary,
    CutSlot,
    EnhancedScriptDocument,
    ScriptSegment,
    SupplementFunnelGapReport,
    SupplementFunnelReport,
    UnifiedCutPlanDocument,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    accepted_supplements_path,
    coverage_gaps_path,
    supplement_funnel_report_path,
    unified_cut_plan_path,
)
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    lock_script,
    save_script_draft,
)
from otio_app.services.without_voiceover_enhanced.supplement_funnel_service import (
    _mark_gap_open_or_generic_fallback,
)


FOLDER = "Canyon"


def _jpeg_bytes(color=(40, 80, 120)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (64, 64), color=color).save(buf, format="JPEG")
    return buf.getvalue()


def _project(tmp_path: Path) -> Project:
    root = tmp_path / "proj"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    (root / FOLDER).mkdir()
    return Project(
        name="GenericGapFallback",
        project_root=str(root),
        work_dir=str(work),
        language="de",
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        fps=25.0,
        selected_asset_subdirs=[FOLDER],
        asset_subdir_names=[FOLDER],
    )


def _lock(project: Project) -> None:
    save_script_draft(
        project,
        EnhancedScriptDocument(
            narration_full="Canyon cliffs at dusk.",
            segments=[
                ScriptSegment(
                    segment_id="Canyon_segment_001",
                    text="Canyon cliffs at dusk.",
                    sequence_index=1,
                    folder_name=FOLDER,
                )
            ],
        ),
    )
    lock_script(project)


def _write_inventory(
    project: Project, entries: list[tuple[str, str, str]]
) -> None:
    """entries: (filename, description, asset_id)."""
    assets: list[AssetMediaAnalysis] = []
    for filename, description, asset_id in entries:
        rel = f"{FOLDER}/{filename}"
        path = project.project_root_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if filename.lower().endswith((".jpg", ".jpeg", ".png")):
            path.write_bytes(_jpeg_bytes())
        else:
            path.write_bytes(b"not-a-real-video")
        assets.append(
            AssetMediaAnalysis(
                path=rel,
                description=description,
                asset_id=asset_id,
                approved_for_cut_plan=True,
                analysis_status="complete",
                media_type="image" if filename.lower().endswith((".jpg", ".jpeg", ".png")) else "video",
            )
        )
    inv = get_folder_inventory_path(project.work_dir_path, FOLDER)
    inv.parent.mkdir(parents=True, exist_ok=True)
    inv.write_text(
        AssetFolderAnalysis(folder=FOLDER, assets=assets).model_dump_json(indent=2),
        encoding="utf-8",
    )


def _open_gap(project: Project, gap_id: str = "gap_slot_001") -> CoverageGap:
    gap = CoverageGap(
        gap_id=gap_id,
        needed_visual="wide canyon establishing",
        preferred_media_type="photo",
        search_concepts=["canyon landscape"],
        reason="none",
        priority="high",
        target_duration_seconds=4.0,
    )
    write_json(
        coverage_gaps_path(project),
        CoverageGapsDocument(
            script_version="script-v1",
            cut_plan_run_id="run_test",
            gaps=[gap],
        ),
    )
    return gap


def test_select_prefers_establishing_inventory_photo(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _lock(project)
    _write_inventory(
        project,
        [
            ("logo_text.jpg", "logo text overlay", "asset_logo"),
            (
                "establishing_landscape.jpg",
                "Establishing landscape overview of the canyon",
                "asset_establishing",
            ),
        ],
    )
    gap = _open_gap(project)
    chosen, asset_id, path = select_generic_fallback_for_gap(project, gap)
    assert chosen is not None
    assert asset_id == "asset_establishing"
    assert path is not None and path.is_file()


def test_apply_fills_accepted_and_closes_gap_status(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _lock(project)
    _write_inventory(
        project,
        [
            (
                "establishing_landscape.jpg",
                "Establishing landscape overview",
                "asset_establishing",
            )
        ],
    )
    _open_gap(project)
    result = apply_generic_fallback_to_gap(project, "gap_slot_001")
    assert result.status == "filled"
    assert result.asset_id == "asset_establishing"
    accepted = load_model(
        accepted_supplements_path(project), AcceptedSupplementsDocument
    )
    assert accepted is not None
    assert len(accepted.supplements) == 1
    cand = accepted.supplements[0]
    assert cand.assign_status == "generic_fallback"
    assert cand.provider == "generic_fallback"
    assert cand.media_validation_status == "export_ready"
    assert cand.gap_id == "gap_slot_001"
    status = summarize_gap_status(project)
    assert "gap_slot_001" in status.filled_gap_ids
    assert "gap_slot_001" not in status.open_gap_ids


def test_apply_fails_when_inventory_empty(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _lock(project)
    _write_inventory(project, [])
    _open_gap(project)
    result = apply_generic_fallback_to_gap(project, "gap_slot_001")
    assert result.status == "failed"
    assert summarize_gap_status(project).open_count == 1


def test_batch_fills_multiple_open_gaps(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _lock(project)
    _write_inventory(
        project,
        [
            ("establishing_a.jpg", "Establishing landscape shot A", "asset_a"),
            ("establishing_b.jpg", "Establishing landscape shot B", "asset_b"),
        ],
    )
    write_json(
        coverage_gaps_path(project),
        CoverageGapsDocument(
            script_version="script-v1",
            cut_plan_run_id="run_test",
            gaps=[
                CoverageGap(
                    gap_id="gap_1",
                    needed_visual="wide",
                    target_duration_seconds=3.0,
                ),
                CoverageGap(
                    gap_id="gap_2",
                    needed_visual="detail",
                    target_duration_seconds=3.0,
                ),
            ],
        ),
    )
    batch = apply_generic_fallback_to_open_gaps(project)
    assert batch.filled_count == 2
    assert batch.failed_count == 0
    accepted = load_model(
        accepted_supplements_path(project), AcceptedSupplementsDocument
    )
    assert accepted is not None
    assert len(accepted.supplements) == 2
    asset_ids = {s.provider_asset_id for s in accepted.supplements}
    assert asset_ids == {"asset_a", "asset_b"}


def test_funnel_hook_fills_after_stock_fail(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _lock(project)
    _write_inventory(
        project,
        [
            (
                "establishing_landscape.jpg",
                "Establishing landscape overview",
                "asset_establishing",
            )
        ],
    )
    gap = _open_gap(project)
    report = SupplementFunnelReport(
        run_id="funnel_x",
        script_version="script-v1",
        cut_plan_run_id="run_test",
        requested_gap_ids=[gap.gap_id],
    )
    gap_report = SupplementFunnelGapReport(gap_id=gap.gap_id, run_id="funnel_x")
    ledger = build_asset_usage_ledger(project)
    filled = try_generic_fallback_after_stock_fail(
        project,
        gap=gap,
        report=report,
        gap_report=gap_report,
        ledger=ledger,
    )
    assert filled is True
    assert report.generic_fallback_count == 1
    assert gap.gap_id in report.filled_gap_ids
    assert gap.gap_id not in report.open_gap_ids
    assert "generischer Ordner-Fallback" in gap_report.message


def test_mark_gap_open_or_generic_fallback_helper(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _lock(project)
    _write_inventory(
        project,
        [
            (
                "establishing_landscape.jpg",
                "Establishing landscape overview",
                "asset_establishing",
            )
        ],
    )
    gap = _open_gap(project)
    report = SupplementFunnelReport(
        run_id="funnel_y",
        script_version="script-v1",
        cut_plan_run_id="run_test",
        requested_gap_ids=[gap.gap_id],
    )
    gap_report = SupplementFunnelGapReport(gap_id=gap.gap_id)
    ledger = build_asset_usage_ledger(project)
    _mark_gap_open_or_generic_fallback(
        project,
        gap=gap,
        report=report,
        gap_report=gap_report,
        ledger=ledger,
        stock_message="Keine geeigneten Kandidaten.",
    )
    assert gap.gap_id in report.filled_gap_ids
    assert report.generic_fallback_count == 1


def test_mark_gap_stays_open_without_inventory(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _lock(project)
    _write_inventory(project, [])
    gap = _open_gap(project)
    report = SupplementFunnelReport(
        run_id="funnel_z",
        script_version="script-v1",
        cut_plan_run_id="run_test",
        requested_gap_ids=[gap.gap_id],
    )
    gap_report = SupplementFunnelGapReport(gap_id=gap.gap_id)
    ledger = build_asset_usage_ledger(project)
    _mark_gap_open_or_generic_fallback(
        project,
        gap=gap,
        report=report,
        gap_report=gap_report,
        ledger=ledger,
        stock_message="Kein export_ready nach 3 Download-Versuch(en).",
    )
    assert gap.gap_id in report.open_gap_ids
    assert report.generic_fallback_count == 0
    assert "Generic-Fallback" in gap_report.message


def test_respects_max_asset_usage(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _lock(project)
    _write_inventory(
        project,
        [
            (
                "establishing_landscape.jpg",
                "Establishing landscape overview",
                "asset_only",
            )
        ],
    )
    write_json(
        coverage_gaps_path(project),
        CoverageGapsDocument(
            script_version="script-v1",
            cut_plan_run_id="run_test",
            gaps=[
                CoverageGap(gap_id="gap_1", needed_visual="a", target_duration_seconds=2),
                CoverageGap(gap_id="gap_2", needed_visual="b", target_duration_seconds=2),
                CoverageGap(gap_id="gap_3", needed_visual="c", target_duration_seconds=2),
            ],
        ),
    )
    # Default max_asset_usage=2 → höchstens zwei Fills mit demselben Asset.
    with patch(
        "otio_app.services.without_voiceover_enhanced.generic_gap_fallback_service.load_cut_plan_options",
    ) as opts:
        from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
            CutPlanOptions,
        )

        opts.return_value = CutPlanOptions(max_asset_usage=1)
        batch = apply_generic_fallback_to_open_gaps(project)
    assert batch.filled_count == 1
    assert batch.failed_count == 2


def _boundaries(slot_count: int) -> list[CutBoundary]:
    bounds: list[CutBoundary] = []
    for index in range(slot_count + 1):
        if index == 0:
            position = "start"
        elif index == slot_count:
            position = "end"
        else:
            position = "middle"
        bounds.append(
            CutBoundary(
                cut_id=f"b{index}",
                sentence_id="Canyon_segment_001",
                position=position,
            )
        )
    return bounds


def _write_cut_plan(project: Project, slots: list[CutSlot]) -> None:
    write_json(
        unified_cut_plan_path(project),
        UnifiedCutPlanDocument(
            script_version="script-v1",
            boundaries=_boundaries(len(slots)),
            slots=slots,
        ),
    )
    gaps = [
        CoverageGap(
            gap_id=str(slot.coverage_gap_id),
            related_shot_ids=[slot.slot_id],
            needed_visual=slot.needed_visual or "wide canyon establishing",
            preferred_media_type="photo",
            target_duration_seconds=4.0,
        )
        for slot in slots
        if str(slot.coverage_gap_id or "").strip()
    ]
    write_json(
        coverage_gaps_path(project),
        CoverageGapsDocument(
            script_version="script-v1",
            cut_plan_run_id="run_test",
            gaps=gaps,
        ),
    )


def test_generic_fallback_skips_adjacent_cut_plan_asset(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _lock(project)
    _write_inventory(
        project,
        [
            (
                "establishing_landscape.jpg",
                "Establishing landscape overview of the canyon",
                "asset_establishing",
            ),
            ("detail_rock.jpg", "Rock detail from the canyon wall", "asset_detail"),
        ],
    )
    _write_cut_plan(
        project,
        [
            CutSlot(
                slot_id="Canyon_slot_001",
                local_asset_id="asset_establishing",
                asset_fit="acceptable",
            ),
            CutSlot(
                slot_id="Canyon_slot_002",
                local_asset_id=None,
                asset_fit="none",
                coverage_gap_id="gap_slot_002",
                needed_visual="wide canyon establishing",
            ),
        ],
    )
    gap = CoverageGap(
        gap_id="gap_slot_002",
        related_shot_ids=["Canyon_slot_002"],
        needed_visual="wide canyon establishing",
        preferred_media_type="photo",
        target_duration_seconds=4.0,
    )
    chosen, asset_id, _path = select_generic_fallback_for_gap(project, gap)
    assert chosen is not None
    assert asset_id == "asset_detail"


def test_generic_fallback_fails_when_only_adjacent_asset_exists(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _lock(project)
    _write_inventory(
        project,
        [
            (
                "establishing_landscape.jpg",
                "Establishing landscape overview of the canyon",
                "asset_establishing",
            )
        ],
    )
    _write_cut_plan(
        project,
        [
            CutSlot(
                slot_id="Canyon_slot_001",
                local_asset_id="asset_establishing",
                asset_fit="acceptable",
            ),
            CutSlot(
                slot_id="Canyon_slot_002",
                local_asset_id=None,
                asset_fit="none",
                coverage_gap_id="gap_slot_002",
                needed_visual="wide canyon establishing",
            ),
        ],
    )
    result = apply_generic_fallback_to_gap(project, "gap_slot_002")
    assert result.status == "failed"
    assert summarize_gap_status(project).open_count == 1


def test_generic_fallback_respects_min_distance_not_just_neighbor(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _lock(project)
    save_cut_plan_options(project, CutPlanOptions(min_asset_reuse_distance_shots=4))
    _write_inventory(
        project,
        [
            (
                "establishing_landscape.jpg",
                "Establishing landscape overview of the canyon",
                "asset_establishing",
            ),
            ("detail_rock.jpg", "Rock detail from the canyon wall", "asset_detail"),
        ],
    )
    _write_cut_plan(
        project,
        [
            CutSlot(
                slot_id="Canyon_slot_001",
                local_asset_id="asset_establishing",
                asset_fit="acceptable",
            ),
            CutSlot(
                slot_id="Canyon_slot_002",
                local_asset_id="asset_other",
                asset_fit="acceptable",
            ),
            CutSlot(
                slot_id="Canyon_slot_003",
                local_asset_id=None,
                asset_fit="none",
                coverage_gap_id="gap_slot_003",
                needed_visual="wide canyon establishing",
            ),
        ],
    )
    gap = CoverageGap(
        gap_id="gap_slot_003",
        related_shot_ids=["Canyon_slot_003"],
        needed_visual="wide canyon establishing",
        preferred_media_type="photo",
        target_duration_seconds=4.0,
    )
    chosen, asset_id, _path = select_generic_fallback_for_gap(project, gap)
    assert chosen is not None
    assert asset_id == "asset_detail"


def test_generic_fallback_allows_reuse_after_min_distance(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _lock(project)
    save_cut_plan_options(project, CutPlanOptions(min_asset_reuse_distance_shots=4))
    _write_inventory(
        project,
        [
            (
                "establishing_landscape.jpg",
                "Establishing landscape overview of the canyon",
                "asset_establishing",
            )
        ],
    )
    filler = [
        CutSlot(
            slot_id=f"Canyon_slot_{index:03d}",
            local_asset_id=f"asset_fill_{index}",
            asset_fit="acceptable",
        )
        for index in range(2, 6)
    ]
    _write_cut_plan(
        project,
        [
            CutSlot(
                slot_id="Canyon_slot_001",
                local_asset_id="asset_establishing",
                asset_fit="acceptable",
            ),
            *filler,
            CutSlot(
                slot_id="Canyon_slot_006",
                local_asset_id=None,
                asset_fit="none",
                coverage_gap_id="gap_slot_006",
                needed_visual="wide canyon establishing",
            ),
        ],
    )
    gap = CoverageGap(
        gap_id="gap_slot_006",
        related_shot_ids=["Canyon_slot_006"],
        needed_visual="wide canyon establishing",
        preferred_media_type="photo",
        target_duration_seconds=4.0,
    )
    chosen, asset_id, _path = select_generic_fallback_for_gap(project, gap)
    assert chosen is not None
    assert asset_id == "asset_establishing"


def test_batch_generic_fallback_does_not_reuse_for_adjacent_gaps(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    _lock(project)
    save_cut_plan_options(project, CutPlanOptions(min_asset_reuse_distance_shots=4))
    _write_inventory(
        project,
        [
            ("establishing_a.jpg", "Establishing landscape shot A", "asset_a"),
            ("establishing_b.jpg", "Establishing landscape shot B", "asset_b"),
        ],
    )
    _write_cut_plan(
        project,
        [
            CutSlot(
                slot_id="Canyon_slot_001",
                local_asset_id=None,
                asset_fit="none",
                coverage_gap_id="gap_1",
                needed_visual="wide",
            ),
            CutSlot(
                slot_id="Canyon_slot_002",
                local_asset_id=None,
                asset_fit="none",
                coverage_gap_id="gap_2",
                needed_visual="wide",
            ),
        ],
    )
    batch = apply_generic_fallback_to_open_gaps(project)
    assert batch.filled_count == 2
    assert batch.failed_count == 0
    accepted = load_model(
        accepted_supplements_path(project), AcceptedSupplementsDocument
    )
    assert accepted is not None
    assert {item.provider_asset_id for item in accepted.supplements} == {
        "asset_a",
        "asset_b",
    }
