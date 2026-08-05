"""External Coverage-Gaps JSON für Dritt-Apps."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.services.without_voiceover_enhanced.coverage_gap_external_export import (
    build_coverage_gaps_external_export,
    ingest_coverage_gap_inbox,
    persist_coverage_gaps,
    refresh_coverage_gaps_external_export,
)
from otio_app.services.without_voiceover_enhanced.io_utils import load_model, write_json
from otio_app.services.without_voiceover_enhanced.models import (
    AcceptedSupplementsDocument,
    CoverageGap,
    CoverageGapsDocument,
    CoverageGapsExternalDocument,
    EnhancedScriptDocument,
    ScriptSegment,
    StockCandidate,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    accepted_supplements_path,
    coverage_gap_inbox_dir,
    coverage_gaps_external_path,
    coverage_gaps_path,
    script_locked_path,
)


def _project(tmp_path: Path) -> Project:
    root = tmp_path / "proj"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    return Project(
        name="ExtGaps",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        language="de",
        asset_subdir_names=["Achill Island"],
        selected_asset_subdirs=["Achill Island"],
        fps=25.0,
    )


def _lock(project: Project, *, version: str = "script-v1") -> None:
    write_json(
        script_locked_path(project),
        EnhancedScriptDocument(
            script_version=version,
            script_status="locked",
            narration_full="Hello Achill.",
            segments=[
                ScriptSegment(
                    segment_id="achill_island_1",
                    folder_name="Achill Island",
                    text="Hello Achill.",
                    sequence_index=1,
                )
            ],
        ),
    )


def test_persist_coverage_gaps_writes_external_json(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _lock(project)
    coverage = CoverageGapsDocument(
        script_version="script-v1",
        cut_plan_run_id="run-abc",
        gaps=[
            CoverageGap(
                gap_id="achill_island_gap_001",
                related_shot_ids=["achill_island_slot_001"],
                needed_visual="wide cliffs at sunset",
                search_concepts=["Achill cliffs sunset", "Ireland coast aerial"],
                search_queries=["Achill cliffs sunset"],
                preferred_media_type="video",
                target_duration_seconds=4.5,
                priority="high",
            )
        ],
    )
    persist_coverage_gaps(project, coverage)

    assert coverage_gaps_path(project).is_file()
    external_path = coverage_gaps_external_path(project)
    assert external_path.is_file()
    doc = load_model(external_path, CoverageGapsExternalDocument)
    assert doc is not None
    assert doc.schema_version == "enhanced-coverage-gaps-external-v1"
    assert doc.cut_plan_run_id == "run-abc"
    assert doc.open_count == 1
    assert doc.filled_count == 0
    assert len(doc.gaps) == 1
    entry = doc.gaps[0]
    assert entry.gap_id == "achill_island_gap_001"
    assert entry.status == "open"
    assert entry.folder_name == "Achill Island"
    assert entry.slot_id == "achill_island_slot_001"
    assert "Achill cliffs sunset" in entry.search_concepts
    assert entry.needed_visual == "wide cliffs at sunset"
    assert entry.target_duration_seconds == 4.5
    assert "coverage/inbox/" in entry.save.drop_dir.replace("\\", "/")
    assert "achill_island_gap_001" in entry.save.drop_dir
    assert Path(entry.save.drop_dir_absolute).is_dir()
    assert "inventory" in entry.save.inventory_path
    assert entry.filled_asset is None


def test_external_export_marks_filled_from_accepted(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _lock(project)
    coverage = CoverageGapsDocument(
        script_version="script-v1",
        cut_plan_run_id="run-1",
        gaps=[
            CoverageGap(
                gap_id="g1",
                related_shot_ids=["s1"],
                needed_visual="waves",
                search_concepts=["ocean waves"],
            )
        ],
    )
    persist_coverage_gaps(project, coverage)
    write_json(
        accepted_supplements_path(project),
        AcceptedSupplementsDocument(
            script_version="script-v1",
            supplements=[
                StockCandidate(
                    candidate_id="cand_1",
                    provider="manual_local",
                    gap_id="g1",
                    media_type="video",
                    local_media_path="/tmp/waves.mp4",
                    media_validation_status="export_ready",
                    cut_plan_run_id="run-1",
                )
            ],
        ),
    )
    doc = refresh_coverage_gaps_external_export(project)
    assert doc.open_count == 0
    assert doc.filled_count == 1
    assert doc.gaps[0].status == "filled"
    assert doc.gaps[0].filled_asset is not None
    assert doc.gaps[0].filled_asset.candidate_id == "cand_1"


def test_ingest_inbox_assigns_media(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _lock(project)
    coverage = CoverageGapsDocument(
        script_version="script-v1",
        cut_plan_run_id="run-1",
        gaps=[
            CoverageGap(
                gap_id="g_inbox",
                related_shot_ids=["slot_1"],
                needed_visual="lighthouse",
                search_concepts=["lighthouse cliff"],
            )
        ],
    )
    persist_coverage_gaps(project, coverage)
    inbox = coverage_gap_inbox_dir(project, "g_inbox")
    media = inbox / "fill.mp4"
    media.write_bytes(b"fake-mp4-bytes")

    with patch(
        "otio_app.services.without_voiceover_enhanced.manual_gap_assign_service."
        "assign_local_file_to_open_gap"
    ) as assign_mock:
        from otio_app.services.without_voiceover_enhanced.manual_gap_assign_service import (
            ManualGapAssignResult,
        )

        assign_mock.return_value = ManualGapAssignResult(
            candidate=StockCandidate(
                candidate_id="manual_fill",
                provider="manual_local",
                gap_id="g_inbox",
                media_validation_status="export_ready",
            )
        )
        with patch(
            "otio_app.services.without_voiceover_enhanced.gap_status_service."
            "summarize_gap_status"
        ) as status_mock:
            from otio_app.services.without_voiceover_enhanced.gap_status_service import (
                GapStatusSummary,
            )

            status_mock.return_value = GapStatusSummary(
                total=1,
                open_gap_ids=["g_inbox"],
                filled_gap_ids=[],
                cut_plan_run_id="run-1",
            )
            results = ingest_coverage_gap_inbox(project)

    assert len(results) == 1
    assert results[0].ok
    assert results[0].gap_id == "g_inbox"
    assign_mock.assert_called_once()
    kwargs = assign_mock.call_args.kwargs
    assert kwargs["gap_id"] == "g_inbox"
    assert Path(kwargs["source_path"]) == media


def test_build_export_empty_without_coverage(tmp_path: Path) -> None:
    project = _project(tmp_path)
    doc = build_coverage_gaps_external_export(project)
    assert doc.gaps == []
    assert doc.open_count == 0
