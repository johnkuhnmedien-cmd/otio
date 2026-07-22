"""Tests für manuelle Gap-Zuordnung (lokale Datei → export_ready + Inventar)."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.services.inventory_loader import load_folder_inventory
from otio_app.services.without_voiceover_enhanced.io_utils import load_model, write_json
from otio_app.services.without_voiceover_enhanced.manual_gap_assign_service import (
    ManualGapAssignError,
    assign_local_file_to_open_gap,
    gap_search_queries,
    list_open_gaps_for_manual_assign,
)
from otio_app.services.without_voiceover_enhanced.models import (
    AcceptedSupplementsDocument,
    CoverageGap,
    CoverageGapsDocument,
    EnhancedScriptDocument,
    ScriptSegment,
    StockCandidate,
    SupplementFunnelGapReport,
    SupplementFunnelReport,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    accepted_supplements_path,
    coverage_gaps_path,
    stock_candidate_download_dir,
    supplement_funnel_report_path,
)
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    lock_script,
    save_script_draft,
)


def _project(tmp_path: Path) -> Project:
    root = tmp_path / "proj"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    (root / "Canyon").mkdir()
    return Project(
        name="ManualGapAssign",
        project_root=str(root),
        work_dir=str(work),
        language="de",
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        fps=25.0,
        frames_per_shot=3,
        selected_asset_subdirs=["Canyon"],
        asset_subdir_names=["Canyon"],
    )


def _lock(project: Project) -> None:
    save_script_draft(
        project,
        EnhancedScriptDocument(
            narration_full="Denali wilderness road.",
            segments=[
                ScriptSegment(
                    segment_id="Canyon_segment_001",
                    text="Denali wilderness road.",
                    sequence_index=1,
                    folder_name="Canyon",
                )
            ],
        ),
    )
    lock_script(project)


def _jpeg_bytes(color=(10, 20, 30)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (32, 32), color=color).save(buf, format="JPEG")
    return buf.getvalue()


def _write_open_gap(project: Project) -> CoverageGap:
    gap = CoverageGap(
        gap_id="gap_open_1",
        needed_visual="gravel mountain road",
        preferred_media_type="photo",
        editorial_purpose="orientation",
        search_concepts=["denali gravel road", "alaska wilderness highway"],
        search_queries=["denali gravel road"],
        reason="manual_review",
    )
    write_json(
        coverage_gaps_path(project),
        CoverageGapsDocument(script_version="script-v1", gaps=[gap]),
    )
    return gap


def test_gap_search_queries_prefers_concepts_and_dedupes() -> None:
    gap = CoverageGap(
        gap_id="g1",
        search_concepts=["a", "b"],
        search_queries=["b", "c"],
    )
    assert gap_search_queries(gap) == ["a", "b", "c"]


def test_gap_search_queries_fallback_to_needed_visual() -> None:
    gap = CoverageGap(gap_id="g1", needed_visual="foggy ridge")
    assert gap_search_queries(gap) == ["foggy ridge"]


def test_list_open_gaps_excludes_export_ready(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _lock(project)
    media = Path(project.work_dir) / "ready.jpg"
    media.write_bytes(_jpeg_bytes())
    write_json(
        coverage_gaps_path(project),
        CoverageGapsDocument(
            script_version="script-v1",
            gaps=[
                CoverageGap(gap_id="gap_open", needed_visual="open"),
                CoverageGap(gap_id="gap_filled", needed_visual="filled"),
            ],
        ),
    )
    write_json(
        accepted_supplements_path(project),
        AcceptedSupplementsDocument(
            script_version="script-v1",
            supplements=[
                StockCandidate(
                    candidate_id="pexels_1",
                    provider="pexels",
                    media_type="photo",
                    selected=True,
                    gap_id="gap_filled",
                    local_media_path=str(media),
                    media_validation_status="export_ready",
                    funnel_managed=True,
                    license="Pexels License",
                    source_page="https://www.pexels.com/photo/1/",
                )
            ],
        ),
    )
    open_gaps = list_open_gaps_for_manual_assign(project)
    assert [g.gap_id for g in open_gaps] == ["gap_open"]


def test_assign_local_file_copies_accepts_and_inventories(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _lock(project)
    gap = _write_open_gap(project)
    write_json(
        supplement_funnel_report_path(project),
        SupplementFunnelReport(
            schema_version="enhanced-supplement-funnel-v4",
            script_version="script-v1",
            requested_gap_ids=[gap.gap_id],
            open_gap_ids=[gap.gap_id],
            filled_gap_ids=[],
            gaps=[
                SupplementFunnelGapReport(
                    gap_id=gap.gap_id,
                    filled=False,
                    message="manual_review",
                )
            ],
        ),
    )
    source = tmp_path / "manual_source.jpg"
    source.write_bytes(_jpeg_bytes(color=(40, 50, 60)))

    assigned = assign_local_file_to_open_gap(
        project, gap_id=gap.gap_id, source_path=str(source)
    )
    assert assigned.provider == "manual"
    assert assigned.gap_id == gap.gap_id
    assert assigned.media_validation_status == "export_ready"
    assert assigned.funnel_managed is True
    assert Path(assigned.local_media_path or "").is_file()
    assert Path(assigned.local_media_path or "").resolve() != source.resolve()

    download_dir = stock_candidate_download_dir(
        project, gap_id=gap.gap_id, candidate_id=assigned.candidate_id
    )
    assert download_dir.is_dir()
    assert any(download_dir.iterdir())

    accepted = load_model(accepted_supplements_path(project), AcceptedSupplementsDocument)
    assert accepted is not None
    assert len(accepted.supplements) == 1
    assert accepted.supplements[0].candidate_id == assigned.candidate_id

    inventory = load_folder_inventory(project, "Canyon")
    assert any(a.asset_id == assigned.candidate_id for a in inventory.assets)
    asset = next(a for a in inventory.assets if a.asset_id == assigned.candidate_id)
    assert "gravel mountain road" in (asset.description or "")

    report = load_model(supplement_funnel_report_path(project), SupplementFunnelReport)
    assert report is not None
    assert gap.gap_id in report.filled_gap_ids
    assert gap.gap_id not in report.open_gap_ids
    assert list_open_gaps_for_manual_assign(project) == []


def test_assign_rejects_url_and_missing_file(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _lock(project)
    _write_open_gap(project)
    with pytest.raises(ManualGapAssignError, match="lokale Dateipfade"):
        assign_local_file_to_open_gap(
            project,
            gap_id="gap_open_1",
            source_path="https://example.com/a.jpg",
        )
    with pytest.raises(ManualGapAssignError, match="nicht gefunden"):
        assign_local_file_to_open_gap(
            project,
            gap_id="gap_open_1",
            source_path=str(tmp_path / "missing.jpg"),
        )


def test_assign_rejects_already_export_ready(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _lock(project)
    media = Path(project.work_dir) / "ready.jpg"
    media.write_bytes(_jpeg_bytes())
    write_json(
        coverage_gaps_path(project),
        CoverageGapsDocument(
            script_version="script-v1",
            gaps=[CoverageGap(gap_id="gap_filled", needed_visual="filled")],
        ),
    )
    write_json(
        accepted_supplements_path(project),
        AcceptedSupplementsDocument(
            script_version="script-v1",
            supplements=[
                StockCandidate(
                    candidate_id="pexels_1",
                    provider="pexels",
                    media_type="photo",
                    selected=True,
                    gap_id="gap_filled",
                    local_media_path=str(media),
                    media_validation_status="export_ready",
                    funnel_managed=True,
                    license="Pexels License",
                    source_page="https://www.pexels.com/photo/1/",
                )
            ],
        ),
    )
    other = tmp_path / "other.jpg"
    other.write_bytes(_jpeg_bytes(color=(1, 2, 3)))
    with pytest.raises(ManualGapAssignError, match="bereits export_ready"):
        assign_local_file_to_open_gap(
            project, gap_id="gap_filled", source_path=str(other)
        )


def test_ui_manual_gap_assign_markers() -> None:
    source = Path(
        "otio_app/ui/without_voiceover_enhanced/cut_plan_tab.py"
    ).read_text(encoding="utf-8")
    assert "Offene Gaps manuell zuordnen" in source
    assert "enh_show_manual_gap_assign_" in source
    assert "Search Queries (kopieren)" in source
    assert "Zuordnen & inventarisieren" in source
    assert "assign_local_file_to_open_gap" in source
