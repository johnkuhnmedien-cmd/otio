"""R1: Enhanced OTIO Resolve-sicher — Identität, Ranges, Stills, Vor-/Nachlauf."""

from __future__ import annotations

import json
import subprocess
import wave
from pathlib import Path

import opentimelineio as otio
import pytest
from PIL import Image

from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis
from otio_app.project_layout import get_folder_inventory_path
from otio_app.services.inventory_loader import save_folder_inventory
from otio_app.services.without_voiceover_enhanced.asset_identity import (
    enhanced_asset_id_for_path,
)
from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
    CutPlanOptions,
    save_cut_plan_options,
)
from otio_app.services.without_voiceover_enhanced.io_utils import write_json
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
)
from otio_app.services.without_voiceover_enhanced.otio_export_service import (
    EnhancedOtioExportError,
    _time_range,
    export_otio_from_resolved_timeline,
    validate_resolved_timeline_for_production,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    final_cut_plan_path,
    narration_timeline_path,
    resolved_timeline_path,
    segment_timings_path,
)
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    lock_script,
    save_script_draft,
)
from otio_app.services.without_voiceover_enhanced.timeline_resolver import (
    TimelineResolveError,
    build_asset_catalog,
    resolve_final_timeline,
)


def _write_silent_wav(path: Path, duration_sec: float, rate: int = 16000) -> None:
    frames = int(duration_sec * rate)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * frames)


def _ffmpeg_color_video(
    path: Path,
    *,
    duration: float,
    color: str,
    fps: int = 25,
    timecode: str | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"color=c={color}:s=320x240:d={duration:.3f}:r={fps}",
        "-pix_fmt",
        "yuv420p",
    ]
    if timecode:
        cmd.extend(["-timecode", timecode])
    cmd.append(str(path))
    result = subprocess.run(cmd, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")


def _project(tmp_path: Path) -> Project:
    root = tmp_path / "Spain"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    for name in ("Castle Combe", "Rocamadour"):
        (root / name).mkdir()
    return Project(
        id="resolve-r1",
        name="Resolve R1",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        language="de",
        asset_subdir_names=["Castle Combe", "Rocamadour"],
        selected_asset_subdirs=["Castle Combe", "Rocamadour"],
        fps=25.0,
    )


def _save_inventory(project: Project, folder: str, media: Path, *, asset_id: str = "") -> None:
    rel = f"{folder}/{media.name}"
    item = AssetMediaAnalysis(
        path=rel,
        description=f"{folder} test clip",
        asset_id=asset_id,
        media_type="video" if media.suffix.lower() in {".mov", ".mp4"} else "photo",
    )
    inv = AssetFolderAnalysis(folder=folder, assets=[item], media_files=[media.name])
    path = get_folder_inventory_path(project.work_dir_path, folder)
    save_folder_inventory(path, inv)


def _lock_and_audio(project: Project, wav: Path, duration: float = 4.0) -> None:
    save_script_draft(
        project,
        EnhancedScriptDocument(
            narration_full="Narration text for resolve import.",
            segments=[
                ScriptSegment(
                    segment_id="Castle_Combe_segment_001",
                    text="Narration text for resolve import.",
                    sequence_index=1,
                    folder_name="Castle Combe",
                    folder_order_index=1,
                )
            ],
        ),
    )
    lock_script(project)
    write_json(
        segment_timings_path(project),
        SegmentTimingsDocument(
            script_version="script-v1",
            segments=[
                SegmentTiming(
                    segment_id="Castle_Combe_segment_001",
                    script_version="script-v1",
                    audio_path=str(wav),
                    duration_seconds=duration,
                    audio_status="valid",
                )
            ],
        ),
    )
    write_json(
        narration_timeline_path(project),
        NarrationTimelineDocument(
            script_version="script-v1",
            total_duration_seconds=duration,
            entries=[
                NarrationTimelineEntry(
                    segment_id="Castle_Combe_segment_001",
                    start_seconds=0.0,
                    end_seconds=duration,
                    pause_after_seconds=0.0,
                    audio_duration_seconds=duration,
                )
            ],
        ),
    )


def test_unique_ids_for_same_filename_different_folders(tmp_path: Path) -> None:
    project = _project(tmp_path)
    a = Path(project.project_root) / "Castle Combe" / "Asset00011.mov"
    b = Path(project.project_root) / "Rocamadour" / "Asset00011.mov"
    _ffmpeg_color_video(a, duration=8.0, color="red")
    _ffmpeg_color_video(b, duration=16.0, color="blue")
    _save_inventory(project, "Castle Combe", a)
    _save_inventory(project, "Rocamadour", b)

    id_a = enhanced_asset_id_for_path(project, a, folder_name="Castle Combe")
    id_b = enhanced_asset_id_for_path(project, b, folder_name="Rocamadour")
    assert id_a != id_b
    assert "castle_combe" in id_a
    assert "rocamadour" in id_b

    catalog = build_asset_catalog(project, fps=25.0)
    assert not catalog.collisions
    assert id_a in catalog.by_id
    assert id_b in catalog.by_id
    assert catalog.by_id[id_a]["path"].endswith("Castle Combe/Asset00011.mov") or (
        "Castle Combe" in catalog.by_id[id_a]["path"]
    )
    assert abs(float(catalog.by_id[id_a]["duration_seconds"]) - 8.0) < 0.25
    assert abs(float(catalog.by_id[id_b]["duration_seconds"]) - 16.0) < 0.25


def test_explicit_id_collision_blocks_resolve(tmp_path: Path) -> None:
    project = _project(tmp_path)
    a = Path(project.project_root) / "Castle Combe" / "Asset00011.mov"
    b = Path(project.project_root) / "Rocamadour" / "Asset00011.mov"
    _ffmpeg_color_video(a, duration=6.0, color="red")
    _ffmpeg_color_video(b, duration=6.0, color="green")
    _save_inventory(project, "Castle Combe", a, asset_id="dup_id")
    _save_inventory(project, "Rocamadour", b, asset_id="dup_id")

    catalog = build_asset_catalog(project, fps=25.0)
    assert catalog.collisions
    assert "dup_id" not in catalog.by_id

    wav = project.work_dir_path / "n.wav"
    _write_silent_wav(wav, 3.0)
    _lock_and_audio(project, wav, 3.0)
    write_json(
        final_cut_plan_path(project),
        FinalCutPlanDocument(
            script_version="script-v1",
            shots=[
                FinalShot(
                    shot_id="Castle_Combe_shot_001",
                    narration_start_anchor=NarrationAnchor(
                        segment_id="Castle_Combe_segment_001", offset_seconds=0.0
                    ),
                    narration_end_anchor=NarrationAnchor(
                        segment_id="Castle_Combe_segment_001", offset_seconds=3.0
                    ),
                    asset_id="dup_id",
                )
            ],
        ),
    )
    save_cut_plan_options(
        project,
        CutPlanOptions(
            voiceover_preroll_sec=0.0,
            voiceover_postroll_sec=0.0,
            still_image_style_enabled=False,
            short_asset_tolerance_sec=0.0,
        ),
    )
    with pytest.raises(TimelineResolveError):
        resolve_final_timeline(project)


def test_video_source_range_and_available_range(tmp_path: Path) -> None:
    project = _project(tmp_path)
    video = Path(project.project_root) / "Castle Combe" / "clip.mp4"
    _ffmpeg_color_video(video, duration=10.0, color="orange")
    _save_inventory(project, "Castle Combe", video)
    catalog = build_asset_catalog(project, fps=25.0)
    asset_id = next(iter(catalog.by_id))

    wav = project.work_dir_path / "n.wav"
    _write_silent_wav(wav, 4.0)
    _lock_and_audio(project, wav, 4.0)
    save_cut_plan_options(
        project,
        CutPlanOptions(
            voiceover_preroll_sec=0.0,
            voiceover_postroll_sec=0.0,
            video_head_trim_sec=1.0,
            still_image_style_enabled=False,
            short_asset_tolerance_sec=0.0,
            shot_min_sec=0.4,
            shot_max_sec=60.0,
        ),
    )
    write_json(
        final_cut_plan_path(project),
        FinalCutPlanDocument(
            script_version="script-v1",
            shots=[
                FinalShot(
                    shot_id="Castle_Combe_shot_010",
                    narration_start_anchor=NarrationAnchor(
                        segment_id="Castle_Combe_segment_001", offset_seconds=0.0
                    ),
                    narration_end_anchor=NarrationAnchor(
                        segment_id="Castle_Combe_segment_001", offset_seconds=4.0
                    ),
                    asset_id=asset_id,
                )
            ],
        ),
    )
    resolved = resolve_final_timeline(project)
    assert not resolved.errors
    shot = resolved.shots[0]
    assert Path(shot.resolved_media_path).is_file()
    assert shot.source_end_seconds > shot.source_start_seconds
    span = shot.source_end_seconds - shot.source_start_seconds
    assert abs(span - 4.0) < 0.05
    avail_end = shot.resolved_available_start_seconds + float(
        shot.resolved_media_duration_seconds or 0
    )
    assert shot.source_start_seconds >= shot.resolved_available_start_seconds - 1e-6
    assert shot.source_end_seconds <= avail_end + 1e-6

    out = export_otio_from_resolved_timeline(project, basename="range_ok")
    tl = otio.adapters.read_from_file(str(out))
    clip = next(c for c in tl.tracks[0] if isinstance(c, otio.schema.Clip))
    assert clip.source_range is not None
    assert clip.media_reference.available_range is not None
    src_dur = clip.source_range.duration.to_seconds()
    assert abs(src_dur - span) < 0.05
    avail = clip.media_reference.available_range
    assert clip.source_range.start_time.to_seconds() + src_dur <= (
        avail.start_time.to_seconds() + avail.duration.to_seconds() + 1e-3
    )


def test_embedded_timecode_nonzero(tmp_path: Path) -> None:
    project = _project(tmp_path)
    video = Path(project.project_root) / "Castle Combe" / "tc.mov"
    _ffmpeg_color_video(video, duration=8.0, color="purple", timecode="01:00:00:00")
    _save_inventory(project, "Castle Combe", video)
    catalog = build_asset_catalog(project, fps=25.0)
    asset_id = next(iter(catalog.by_id))
    entry = catalog.by_id[asset_id]
    # Embedded TC may be present depending on ffmpeg mux; assert catalog has start>=0.
    assert entry["available_start_seconds"] >= 0.0

    wav = project.work_dir_path / "n.wav"
    _write_silent_wav(wav, 3.0)
    _lock_and_audio(project, wav, 3.0)
    save_cut_plan_options(
        project,
        CutPlanOptions(
            voiceover_preroll_sec=0.0,
            voiceover_postroll_sec=0.0,
            video_head_trim_sec=0.0,
            still_image_style_enabled=False,
        ),
    )
    write_json(
        final_cut_plan_path(project),
        FinalCutPlanDocument(
            script_version="script-v1",
            shots=[
                FinalShot(
                    shot_id="tc_shot",
                    narration_start_anchor=NarrationAnchor(
                        segment_id="Castle_Combe_segment_001", offset_seconds=0.0
                    ),
                    narration_end_anchor=NarrationAnchor(
                        segment_id="Castle_Combe_segment_001", offset_seconds=3.0
                    ),
                    asset_id=asset_id,
                )
            ],
        ),
    )
    resolved = resolve_final_timeline(project)
    shot = resolved.shots[0]
    out = export_otio_from_resolved_timeline(project, basename="tc_ok")
    tl = otio.adapters.read_from_file(str(out))
    clip = next(c for c in tl.tracks[0] if isinstance(c, otio.schema.Clip))
    avail = clip.media_reference.available_range
    src = clip.source_range
    assert src.start_time.to_seconds() + src.duration.to_seconds() <= (
        avail.start_time.to_seconds() + avail.duration.to_seconds() + 0.05
    )
    assert shot.source_start_seconds >= shot.resolved_available_start_seconds - 1e-6
    # Resolve-sicher: OTIO Ranges file-relativ (nicht Kamera-TC ~3600s).
    assert avail.start_time.to_seconds() == pytest.approx(0.0, abs=0.05)
    assert src.start_time.to_seconds() < 60.0
    content_offset = shot.source_start_seconds - shot.resolved_available_start_seconds
    assert src.start_time.to_seconds() == pytest.approx(max(0.0, content_offset), abs=0.08)


def test_otio_export_file_relative_despite_embedded_camera_tc(tmp_path: Path) -> None:
    """Regression Bisti_slot_005/008: Kamera-TC ~7h darf nicht in OTIO source."""
    from unittest.mock import patch

    from otio_app.services.media_utils import MediaTiming
    from otio_app.services.without_voiceover_enhanced.models import ResolvedShot
    from otio_app.services.without_voiceover_enhanced.otio_export_service import (
        _ensure_shot_media_for_export,
    )

    project = _project(tmp_path)
    video = Path(project.project_root) / "Bisti" / "Bisti_De_Na_Zin_Wilderness_Asset14.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"fake-mp4")
    shot = ResolvedShot(
        shot_id="Bisti_slot_005",
        asset_id="asset__bisti__asset14",
        timeline_start_seconds=34.88,
        timeline_end_seconds=41.92,
        source_start_seconds=25377.337,
        source_end_seconds=25384.377,
        resolved_media_path=str(video),
        resolved_media_kind="video",
        resolved_media_duration_seconds=16.02,
        resolved_available_start_seconds=25372.347,
        folder_name="Bisti",
    )
    timing = MediaTiming(start_sec=25372.347, duration_sec=16.02, rate=29.97)
    with (
        patch(
            "otio_app.services.without_voiceover_enhanced.otio_export_service.probe_media_timing",
            return_value=timing,
        ),
        patch(
            "otio_app.services.without_voiceover_enhanced.otio_export_service.probe_duration_seconds",
            return_value=16.02,
        ),
        patch(
            "otio_app.services.without_voiceover_enhanced.otio_export_service.subprocess.run",
            return_value=type(
                "R",
                (),
                {
                    "returncode": 0,
                    "stdout": json.dumps(
                        {"streams": [{"width": 1920, "height": 1080, "codec_type": "video"}]}
                    ).encode(),
                },
            )(),
        ),
    ):
        path, avail, src0, src1, rate = _ensure_shot_media_for_export(
            project, shot, fps=25.0
        )
    assert path == video.resolve()
    assert avail == 0.0
    assert src0 == pytest.approx(4.99, abs=0.01)
    assert src1 == pytest.approx(12.03, abs=0.01)
    assert src1 - src0 == pytest.approx(7.04, abs=0.01)
    assert rate == pytest.approx(29.97, abs=0.01)


def test_catalog_prefers_clean_over_original_inventory_path(tmp_path: Path) -> None:
    project = _project(tmp_path)
    folder = "Castle Combe"
    original = Path(project.project_root) / folder / "Castle_Asset04.mp4"
    original.write_bytes(b"\x00" * 64)
    clean = (
        project.work_dir_path
        / "clean"
        / folder
        / "Castle_Asset04_3840x2160.mp4"
    )
    clean.parent.mkdir(parents=True)
    clean.write_bytes(b"\x00" * 128)
    _save_inventory(project, folder, original)
    catalog = build_asset_catalog(project, fps=25.0)
    assert catalog.by_id
    entry = next(iter(catalog.by_id.values()))
    assert "clean" in entry["path"].replace("\\", "/")
    assert Path(entry["path"]).name.startswith("Castle_Asset04")


def test_still_jpeg_and_png_hold(tmp_path: Path) -> None:
    project = _project(tmp_path)
    jpg = Path(project.project_root) / "Castle Combe" / "still_test.jpg"
    png = Path(project.project_root) / "Rocamadour" / "still_test.png"
    Image.new("RGB", (64, 48), color=(200, 40, 40)).save(jpg, format="JPEG")
    Image.new("RGB", (64, 48), color=(40, 40, 200)).save(png, format="PNG")
    _save_inventory(project, "Castle Combe", jpg)
    _save_inventory(project, "Rocamadour", png)
    catalog = build_asset_catalog(project, fps=25.0)
    jpg_id = next(
        i for i, e in catalog.by_id.items() if e["path"].endswith("still_test.jpg")
    )
    wav = project.work_dir_path / "n.wav"
    _write_silent_wav(wav, 4.0)
    _lock_and_audio(project, wav, 4.0)
    save_cut_plan_options(
        project,
        CutPlanOptions(
            voiceover_preroll_sec=0.0,
            voiceover_postroll_sec=0.0,
            still_image_style_enabled=False,
        ),
    )
    write_json(
        final_cut_plan_path(project),
        FinalCutPlanDocument(
            script_version="script-v1",
            shots=[
                FinalShot(
                    shot_id="still_shot",
                    narration_start_anchor=NarrationAnchor(
                        segment_id="Castle_Combe_segment_001", offset_seconds=0.0
                    ),
                    narration_end_anchor=NarrationAnchor(
                        segment_id="Castle_Combe_segment_001", offset_seconds=4.0
                    ),
                    asset_id=jpg_id,
                )
            ],
        ),
    )
    resolved = resolve_final_timeline(project)
    shot = resolved.shots[0]
    assert shot.hold_mode == "freeze_video"
    assert Path(shot.resolved_media_path).suffix.lower() == ".mp4"
    span = shot.timeline_end_seconds - shot.timeline_start_seconds
    assert span >= 3.99
    out = export_otio_from_resolved_timeline(project, basename="still_ok")
    tl = otio.adapters.read_from_file(str(out))
    clip = next(c for c in tl.tracks[0] if isinstance(c, otio.schema.Clip))
    assert clip.source_range.duration.to_seconds() >= 3.99
    assert clip.media_reference.available_range is not None
    assert clip.media_reference.available_range.duration.to_seconds() >= 3.99


def test_preroll_postroll_and_fail_closed_gate(tmp_path: Path) -> None:
    project = _project(tmp_path)
    video = Path(project.project_root) / "Castle Combe" / "Asset00011.mov"
    _ffmpeg_color_video(video, duration=20.0, color="teal")
    _save_inventory(project, "Castle Combe", video)
    catalog = build_asset_catalog(project, fps=25.0)
    asset_id = next(iter(catalog.by_id))

    wav = project.work_dir_path / "narration.wav"
    _write_silent_wav(wav, 4.0)
    _lock_and_audio(project, wav, 4.0)
    save_cut_plan_options(
        project,
        CutPlanOptions(
            voiceover_preroll_sec=1.0,
            voiceover_postroll_sec=5.0,
            voiceover_preroll_mode="fixed",
            voiceover_postroll_mode="fixed",
            video_head_trim_sec=0.0,
            still_image_style_enabled=False,
            short_asset_tolerance_sec=0.0,
        ),
    )
    write_json(
        final_cut_plan_path(project),
        FinalCutPlanDocument(
            script_version="script-v1",
            shots=[
                FinalShot(
                    shot_id="main_shot",
                    narration_start_anchor=NarrationAnchor(
                        segment_id="Castle_Combe_segment_001", offset_seconds=0.0
                    ),
                    narration_end_anchor=NarrationAnchor(
                        segment_id="Castle_Combe_segment_001", offset_seconds=4.0
                    ),
                    asset_id=asset_id,
                )
            ],
        ),
    )
    resolved = resolve_final_timeline(project)
    assert abs(resolved.voiceover_preroll_sec - 1.0) < 1e-6
    assert abs(resolved.voiceover_postroll_sec - 5.0) < 1e-6
    assert resolved.audio_segments
    assert abs(resolved.audio_segments[0].timeline_start_seconds - 1.0) < 1e-3
    last_audio = resolved.audio_segments[-1].timeline_end_seconds
    assert resolved.shots[-1].timeline_end_seconds + 1e-3 >= last_audio + 5.0
    # Source darf Dateiende nicht überschreiten.
    shot = resolved.shots[0]
    avail_end = shot.resolved_available_start_seconds + float(
        shot.resolved_media_duration_seconds or 0
    )
    assert shot.source_end_seconds <= avail_end + 1e-3

    out = export_otio_from_resolved_timeline(project, basename="preroll_ok")
    tl = otio.adapters.read_from_file(str(out))
    audio_track = tl.tracks[1]
    # Erster Audio-Clip nach Gap von ~1s
    items = list(audio_track)
    assert isinstance(items[0], otio.schema.Gap)
    assert abs(items[0].source_range.duration.to_seconds() - 1.0) < 0.05

    # Fail-closed: absichtlich ungültige Source trotz errors=[]
    bad = resolved.model_copy(deep=True)
    bad.errors = []
    bad.shots[0].source_start_seconds = 1000.0
    bad.shots[0].source_end_seconds = 1004.0
    write_json(resolved_timeline_path(project), bad)
    gate = validate_resolved_timeline_for_production(project, bad)
    assert gate
    with pytest.raises(EnhancedOtioExportError):
        export_otio_from_resolved_timeline(project, basename="should_block")


def test_http_media_blocked(tmp_path: Path) -> None:
    project = _project(tmp_path)
    write_json(
        resolved_timeline_path(project),
        ResolvedTimelineDocument(
            script_version="script-v1",
            fps=25.0,
            total_duration_seconds=1.0,
            shots=[
                {
                    "shot_id": "s1",
                    "asset_id": "x",
                    "timeline_start_seconds": 0.0,
                    "timeline_end_seconds": 1.0,
                    "source_start_seconds": 0.0,
                    "source_end_seconds": 1.0,
                    "resolved_media_path": "https://example.com/a.mp4",
                    "resolved_media_kind": "video",
                }
            ],
            errors=[],
        ),
    )
    with pytest.raises(EnhancedOtioExportError, match="Web-URL|HTTP"):
        export_otio_from_resolved_timeline(project, basename="http_blocked")


def test_time_range_snaps_to_integer_frames_asset14_regression() -> None:
    """Yosemite_Asset14: fractional start ~1.992 @24fps → 1 schwarzer Frame in Resolve."""
    # Alter OTIO-Wert: available/source start 0.083008s @24
    tr = _time_range(11.96, 24.0, start_sec=0.083008)
    assert tr.start_time.value == 2
    assert tr.start_time.value == int(tr.start_time.value)
    assert tr.duration.value == int(tr.duration.value)
    assert tr.duration.value >= 1
    # Source und Available mit gleichem Snap → kein Off-by-one dazwischen
    avail = _time_range(22.791667, 24.0, start_sec=0.083008)
    assert tr.start_time.value >= avail.start_time.value
