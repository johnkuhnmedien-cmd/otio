"""Sequenzielle Supplement-Auflösung (Download → Frames → LLM)."""

from __future__ import annotations

from pathlib import Path

from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.services.without_voiceover_enhanced.io_utils import write_json
from otio_app.services.without_voiceover_enhanced.models import (
    CoverageGap,
    CoverageGapsDocument,
    EnhancedScriptDocument,
    ScriptSegment,
    StockCandidate,
    StockSearchResultsDocument,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    accepted_supplements_path,
    coverage_gaps_path,
    stock_search_results_path,
)
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    lock_script,
    save_script_draft,
)
from otio_app.services.without_voiceover_enhanced.supplement_resolve_service import (
    dedupe_stock_candidates,
    rank_candidates_for_gap,
    resolve_supplements_for_gaps,
)
from otio_app.project_layout import get_folder_inventory_path
from otio_app.services.without_voiceover_enhanced.io_utils import load_model
from otio_app.analysis_models import AssetFolderAnalysis


def _project(tmp_path: Path) -> Project:
    root = tmp_path / "proj"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    (root / "Canyon").mkdir()
    return Project(
        name="ResolveTest",
        project_root=str(root),
        work_dir=str(work),
        language="de",
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        fps=25.0,
        frames_per_shot=1,
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


def test_dedupe_and_rank() -> None:
    gap = CoverageGap(gap_id="gap_1", preferred_media_type="video", needed_visual="road")
    candidates = [
        StockCandidate(
            candidate_id="a",
            provider="pexels",
            media_type="photo",
            download_url="https://x/a.jpg",
            width=100,
            height=100,
            gap_id="gap_1",
        ),
        StockCandidate(
            candidate_id="a",
            provider="pexels",
            media_type="photo",
            download_url="https://x/a.jpg",
            gap_id="gap_2",
        ),
        StockCandidate(
            candidate_id="b",
            provider="pexels",
            media_type="video",
            download_url="https://x/b.mp4",
            width=1920,
            height=1080,
            duration_seconds=5,
            license="Pexels",
            gap_id="gap_1",
        ),
    ]
    unique = dedupe_stock_candidates(candidates)
    assert [c.candidate_id for c in unique] == ["a", "b"]
    ranked = rank_candidates_for_gap(unique, gap)
    assert ranked[0].candidate_id == "b"


def test_resolve_stops_on_first_pass_and_cleans_fail(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _lock(project)
    write_json(
        coverage_gaps_path(project),
        CoverageGapsDocument(
            script_version="script-v1",
            gaps=[
                CoverageGap(
                    gap_id="gap_1",
                    needed_visual="gravel road in denali",
                    preferred_media_type="photo",
                    must_include=["road"],
                )
            ],
        ),
    )
    write_json(
        stock_search_results_path(project),
        StockSearchResultsDocument(
            script_version="script-v1",
            candidates=[
                StockCandidate(
                    candidate_id="cand_fail",
                    provider="pexels",
                    media_type="photo",
                    download_url="https://example.com/fail.jpg",
                    gap_id="gap_1",
                    title="wrong",
                ),
                StockCandidate(
                    candidate_id="cand_pass",
                    provider="pexels",
                    media_type="photo",
                    download_url="https://example.com/pass.jpg",
                    gap_id="gap_1",
                    title="road",
                ),
            ],
        ),
    )

    downloaded: list[str] = []

    def fake_download(project, candidate, *, gap_id: str) -> Path:
        from otio_app.services.without_voiceover_enhanced.paths import (
            stock_candidate_download_dir,
        )

        target_dir = stock_candidate_download_dir(
            project, gap_id=gap_id, candidate_id=candidate.candidate_id
        )
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"{candidate.candidate_id}.jpg"
        # Minimal valid JPEG for Pillow validation.
        from PIL import Image

        Image.new("RGB", (32, 32), color=(80, 120, 40)).save(path, format="JPEG")
        downloaded.append(candidate.candidate_id)
        return path

    def fake_llm(**kwargs) -> dict:
        name = kwargs["media_name"]
        if name == "cand_fail":
            return {
                "description": "ocean",
                "status": "FAIL",
                "score": 0.1,
                "reason": "wrong subject",
            }
        return {
            "description": "gravel road",
            "status": "PASS",
            "score": 0.9,
            "reason": "matches",
        }

    # Patch frame extract to copy the jpeg as a "frame".
    import otio_app.services.without_voiceover_enhanced.supplement_resolve_service as svc

    original_extract = svc._extract_validation_frames

    def fake_frames(project, media_path: Path):
        frame = media_path.parent / "frames" / "frame_001.jpg"
        frame.parent.mkdir(parents=True, exist_ok=True)
        frame.write_bytes(media_path.read_bytes())
        return [frame]

    svc._extract_validation_frames = fake_frames  # type: ignore[assignment]
    try:
        report = resolve_supplements_for_gaps(
            project,
            max_candidates_per_gap=5,
            llm_callable=fake_llm,
            download_callable=fake_download,
        )
    finally:
        svc._extract_validation_frames = original_extract  # type: ignore[assignment]

    assert report.filled_gap_ids == ["gap_1"]
    assert downloaded == ["cand_fail", "cand_pass"]
    # FAIL candidate directory removed.
    fail_dir = (
        Path(project.work_dir)
        / "en"
        / "voiceover_generation"
        / "stock"
        / "downloads"
    )
    # language is de
    fail_dir = (
        Path(project.language_work_dir_path)
        / "voiceover_generation"
        / "stock"
        / "downloads"
        / "gap_1"
    )
    assert not (fail_dir / "cand_fail").exists()
    assert (fail_dir / "cand_pass").exists()
    assert accepted_supplements_path(project).is_file()
    inventory = load_model(
        get_folder_inventory_path(project.work_dir_path, "Canyon"),
        AssetFolderAnalysis,
    )
    assert inventory is not None
    assert any(a.asset_id == "cand_pass" for a in inventory.assets)
