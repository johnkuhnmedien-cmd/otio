"""R2: echte Medienvalidierung — keine Fake-Header als export_ready."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
import pytest
from PIL import Image

from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.services.without_voiceover_enhanced.cut_plan_service import (
    accept_supplement_candidates,
    generate_final_cut_plan,
)
from otio_app.services.without_voiceover_enhanced.io_utils import write_json
from otio_app.services.without_voiceover_enhanced.local_media_service import (
    STATUS_EXPORT_READY,
    STATUS_LOCAL_MEDIA_INVALID,
    STATUS_LOCAL_MEDIA_MISSING,
    assign_local_media_path,
    list_export_ready_supplements,
    refresh_supplement_validation,
    validate_local_media_path,
)
from otio_app.services.without_voiceover_enhanced.models import (
    EnhancedScriptDocument,
    FinalCutPlanDocument,
    FinalShot,
    NarrationAnchor,
    NarrationTimelineDocument,
    NarrationTimelineEntry,
    ResolvedTimelineDocument,
    ScriptSegment,
    SegmentTiming,
    SegmentTimingsDocument,
    StockCandidate,
    StockSearchResultsDocument,
)
from otio_app.services.without_voiceover_enhanced.otio_export_service import (
    EnhancedOtioExportError,
    export_otio_from_resolved_timeline,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    accepted_supplements_path,
    final_cut_plan_path,
    narration_timeline_path,
    resolved_timeline_path,
    segment_timings_path,
    stock_search_results_path,
)
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    lock_script,
    save_script_draft,
)
from otio_app.services.without_voiceover_enhanced.stock_provider_config import (
    SUPPORTED_STOCK_PROVIDERS,
    save_stock_providers_config,
)
from otio_app.services.without_voiceover_enhanced.timeline_resolver import (
    TimelineResolveError,
    resolve_final_timeline,
)


def _enhanced_project(tmp_path: Path) -> Project:
    root = tmp_path / "proj"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    (root / "Assets").mkdir(exist_ok=True)
    return Project(
        name="EnhR2",
        project_root=str(root),
        work_dir=str(work),
        language="en",
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        fps=25.0,
        selected_asset_subdirs=["Assets"],
        asset_subdir_names=["Assets"],
    )


def _lock_minimal(project: Project) -> None:
    save_script_draft(
        project,
        EnhancedScriptDocument(
            narration_full="Hello world.",
            segments=[
                ScriptSegment(
                    segment_id="segment_001",
                    text="Hello world.",
                    sequence_index=1,
                )
            ],
        ),
    )
    lock_script(project)


def _write_valid_jpeg(path: Path) -> Path:
    Image.new("RGB", (16, 16), color=(40, 80, 120)).save(path, format="JPEG")
    return path


def _write_valid_png(path: Path) -> Path:
    Image.new("RGB", (12, 12), color=(200, 100, 50)).save(path, format="PNG")
    return path


def _write_valid_mp4(path: Path) -> Path | None:
    created = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=green:s=32x32:d=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-t",
            "1",
            str(path),
        ],
        capture_output=True,
        check=False,
    )
    if created.returncode != 0 or not path.is_file():
        return None
    return path


def _accept_candidate(
    project: Project,
    *,
    candidate_id: str = "stock_r2",
    media_type: str = "photo",
) -> StockCandidate:
    write_json(
        stock_search_results_path(project),
        StockSearchResultsDocument(
            script_version="script-v1",
            candidates=[
                StockCandidate(
                    candidate_id=candidate_id,
                    provider="wikimedia",
                    title="R2",
                    media_type=media_type,
                    preview_url="https://example.com/p.jpg",
                    source_page="https://example.com/s",
                )
            ],
        ),
    )
    accepted = accept_supplement_candidates(project, [candidate_id])
    return accepted.supplements[0]


def test_valid_jpeg_export_ready(tmp_path: Path) -> None:
    project = _enhanced_project(tmp_path)
    _lock_minimal(project)
    _accept_candidate(project, media_type="photo")
    local = _write_valid_jpeg(project.work_dir_path / "ok.jpg")
    updated = assign_local_media_path(project, "stock_r2", str(local))
    assert updated.media_validation_status == STATUS_EXPORT_READY
    assert updated.media_validation_error is None


def test_valid_png_export_ready(tmp_path: Path) -> None:
    project = _enhanced_project(tmp_path)
    _lock_minimal(project)
    _accept_candidate(project, media_type="image")
    local = _write_valid_png(project.work_dir_path / "ok.png")
    updated = assign_local_media_path(project, "stock_r2", str(local))
    assert updated.media_validation_status == STATUS_EXPORT_READY


def test_jpeg_header_with_null_bytes_invalid(tmp_path: Path) -> None:
    project = _enhanced_project(tmp_path)
    _lock_minimal(project)
    _accept_candidate(project, media_type="photo")
    local = project.work_dir_path / "fake.jpg"
    local.write_bytes(b"\xff\xd8\xff" + b"\x00" * 64)
    updated = assign_local_media_path(project, "stock_r2", str(local))
    assert updated.media_validation_status == STATUS_LOCAL_MEDIA_INVALID
    assert updated.media_validation_error


def test_text_file_with_jpg_extension_invalid(tmp_path: Path) -> None:
    path = tmp_path / "not_an_image.jpg"
    path.write_text("this is not a jpeg\n", encoding="utf-8")
    status, err = validate_local_media_path(str(path), media_type="photo")
    assert status == STATUS_LOCAL_MEDIA_INVALID
    assert err is not None


def test_empty_image_file_invalid(tmp_path: Path) -> None:
    path = tmp_path / "empty.jpg"
    path.write_bytes(b"")
    status, err = validate_local_media_path(str(path), media_type="photo")
    assert status == STATUS_LOCAL_MEDIA_INVALID
    assert err is not None


def test_random_bytes_mp4_invalid(tmp_path: Path) -> None:
    path = tmp_path / "noise.mp4"
    path.write_bytes(bytes(range(256)) * 4)
    status, err = validate_local_media_path(str(path), media_type="video")
    assert status == STATUS_LOCAL_MEDIA_INVALID
    assert err is not None


def test_valid_small_video_export_ready(tmp_path: Path) -> None:
    project = _enhanced_project(tmp_path)
    _lock_minimal(project)
    _accept_candidate(project, media_type="video")
    local = _write_valid_mp4(project.work_dir_path / "ok.mp4")
    if local is None:
        pytest.skip("ffmpeg Test-Fixture nicht verfügbar")
    updated = assign_local_media_path(project, "stock_r2", str(local))
    assert updated.media_validation_status == STATUS_EXPORT_READY


def test_unknown_media_type_blocked(tmp_path: Path) -> None:
    path = _write_valid_jpeg(tmp_path / "real.jpg")
    status, err = validate_local_media_path(str(path), media_type="document")
    assert status == STATUS_LOCAL_MEDIA_INVALID
    assert "Unbekannte Medienart" in (err or "")

    status2, err2 = validate_local_media_path(str(path), media_type="audio")
    assert status2 == STATUS_LOCAL_MEDIA_INVALID
    assert "Unbekannte Medienart" in (err2 or "")


def test_missing_and_http_blocked(tmp_path: Path) -> None:
    status, _ = validate_local_media_path(str(tmp_path / "missing.jpg"), media_type="photo")
    assert status == STATUS_LOCAL_MEDIA_MISSING
    status, err = validate_local_media_path("https://example.com/x.jpg", media_type="photo")
    assert status == STATUS_LOCAL_MEDIA_INVALID
    assert "HTTP" in (err or "")


def test_damaged_local_not_in_export_ready_list(tmp_path: Path) -> None:
    project = _enhanced_project(tmp_path)
    _lock_minimal(project)
    _accept_candidate(project, media_type="photo")
    bad = project.work_dir_path / "bad.jpg"
    bad.write_bytes(b"\xff\xd8\xff" + b"\x00" * 32)
    assign_local_media_path(project, "stock_r2", str(bad))
    ready = list_export_ready_supplements(project)
    assert ready == []


def test_damaged_local_not_in_resolved_timeline(tmp_path: Path) -> None:
    project = _enhanced_project(tmp_path)
    _lock_minimal(project)
    _accept_candidate(project, media_type="photo")
    bad = project.work_dir_path / "bad.jpg"
    bad.write_text("not jpeg", encoding="utf-8")
    assign_local_media_path(project, "stock_r2", str(bad))

    write_json(
        segment_timings_path(project),
        SegmentTimingsDocument(
            script_version="script-v1",
            segments=[
                SegmentTiming(
                    segment_id="segment_001",
                    script_version="script-v1",
                    audio_path=str(tmp_path / "a.wav"),
                    duration_seconds=1.0,
                )
            ],
        ),
    )
    write_json(
        narration_timeline_path(project),
        NarrationTimelineDocument(
            script_version="script-v1",
            total_duration_seconds=1.0,
            entries=[
                NarrationTimelineEntry(
                    segment_id="segment_001",
                    start_seconds=0.0,
                    end_seconds=1.0,
                    pause_after_seconds=0.0,
                )
            ],
        ),
    )
    write_json(
        final_cut_plan_path(project),
        FinalCutPlanDocument(
            script_version="script-v1",
            shots=[
                FinalShot(
                    shot_id="shot_001",
                    narration_start_anchor=NarrationAnchor(
                        segment_id="segment_001", offset_seconds=0.0
                    ),
                    narration_end_anchor=NarrationAnchor(
                        segment_id="segment_001", offset_seconds=1.0
                    ),
                    asset_id="stock_r2",
                )
            ],
        ),
    )
    with pytest.raises(TimelineResolveError, match="nicht export_ready|Unbekannte Asset"):
        resolve_final_timeline(project)


def test_damaged_local_blocks_otio(tmp_path: Path) -> None:
    project = _enhanced_project(tmp_path)
    _lock_minimal(project)
    _accept_candidate(project, media_type="photo")
    bad = project.work_dir_path / "bad.jpg"
    bad.write_bytes(b"\xff\xd8\xff\x00\x00")
    assign_local_media_path(project, "stock_r2", str(bad))
    write_json(
        resolved_timeline_path(project),
        ResolvedTimelineDocument(
            script_version="script-v1",
            fps=25.0,
            total_duration_seconds=1.0,
            audio_segments=[],
            shots=[
                {
                    "shot_id": "shot_001",
                    "asset_id": "stock_r2",
                    "timeline_start_seconds": 0.0,
                    "timeline_end_seconds": 1.0,
                    "source_start_seconds": 0.0,
                    "source_end_seconds": 1.0,
                }
            ],
            repairs=[],
            errors=[],
        ),
    )
    with pytest.raises(EnhancedOtioExportError):
        export_otio_from_resolved_timeline(project)


def test_valid_local_survives_disabled_provider(tmp_path: Path) -> None:
    project = _enhanced_project(tmp_path)
    _lock_minimal(project)
    _accept_candidate(project, media_type="photo")
    local = _write_valid_jpeg(project.work_dir_path / "keep.jpg")
    updated = assign_local_media_path(project, "stock_r2", str(local))
    assert updated.media_validation_status == STATUS_EXPORT_READY
    save_stock_providers_config(
        project, {name: False for name in SUPPORTED_STOCK_PROVIDERS}
    )
    refreshed = refresh_supplement_validation(updated)
    assert refreshed.media_validation_status == STATUS_EXPORT_READY
    ready = list_export_ready_supplements(project)
    assert len(ready) == 1
    assert ready[0].candidate_id == "stock_r2"


def test_damaged_not_in_final_llm_run(tmp_path: Path) -> None:
    """Beschädigte lokale Datei gelangt nicht in den finalen LLM-Lauf."""
    from otio_app.services.without_voiceover_enhanced.models import (
        RoughCutPlanDocument,
        RoughShot,
    )
    from otio_app.services.without_voiceover_enhanced.paths import rough_cut_plan_path

    project = _enhanced_project(tmp_path)
    _lock_minimal(project)
    _accept_candidate(project, media_type="photo")
    bad = project.work_dir_path / "bad.jpg"
    bad.write_bytes(b"not-a-real-image")
    assign_local_media_path(project, "stock_r2", str(bad))
    assert list_export_ready_supplements(project) == []

    write_json(
        narration_timeline_path(project),
        NarrationTimelineDocument(
            script_version="script-v1",
            total_duration_seconds=1.0,
            entries=[
                NarrationTimelineEntry(
                    segment_id="segment_001",
                    start_seconds=0.0,
                    end_seconds=1.0,
                    pause_after_seconds=0.0,
                )
            ],
        ),
    )
    write_json(
        rough_cut_plan_path(project),
        RoughCutPlanDocument(
            script_version="script-v1",
            pause_directives=[],
            shots=[
                RoughShot(
                    shot_id="shot_001",
                    narration_start_anchor=NarrationAnchor(
                        segment_id="segment_001", offset_seconds=0.0
                    ),
                    narration_end_anchor=NarrationAnchor(
                        segment_id="segment_001", offset_seconds=1.0
                    ),
                    asset_id=None,
                )
            ],
        ),
    )

    captured: dict[str, str] = {}

    def fake_llm(*, prompt: str, model: str) -> str:
        captured["prompt"] = prompt
        return json.dumps(
            {
                "shots": [
                    {
                        "shot_id": "shot_001",
                        "narration_start_anchor": {
                            "segment_id": "segment_001",
                            "offset_seconds": 0.0,
                        },
                        "narration_end_anchor": {
                            "segment_id": "segment_001",
                            "offset_seconds": 1.0,
                        },
                        "asset_id": "local_placeholder",
                    }
                ]
            }
        )

    generate_final_cut_plan(project, llm_callable=fake_llm)
    assert "prompt" in captured
    # export_ready list was empty → accepted supplements block must not list stock_r2.
    assert '"candidate_id": "stock_r2"' not in captured["prompt"]
    compact = captured["prompt"].replace(" ", "").replace("\n", "")
    assert '"supplements":[]' in compact
