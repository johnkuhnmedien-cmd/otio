"""R2: Portables Enhanced-OTIO-Paket — eindeutige Mediendateinamen für Resolve."""

from __future__ import annotations

import hashlib
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
    ScriptSegment,
    SegmentTiming,
    SegmentTimingsDocument,
)
from otio_app.services.without_voiceover_enhanced.otio_export_service import (
    EnhancedOtioExportError,
    export_portable_otio_package,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    final_cut_plan_path,
    narration_timeline_path,
    segment_timings_path,
)
from otio_app.services.without_voiceover_enhanced.portable_export import (
    PortableExportError,
    packaged_filename_for_media,
    stage_media_into_package,
)
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    lock_script,
    save_script_draft,
)
from otio_app.services.without_voiceover_enhanced.timeline_resolver import (
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
    text: str = "",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    vf = f"drawtext=text='{text}':fontsize=28:fontcolor=white:x=12:y=12" if text else None
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
    ]
    if vf:
        cmd.extend(["-vf", vf])
    cmd.extend(["-pix_fmt", "yuv420p", str(path)])
    result = subprocess.run(cmd, capture_output=True, check=False)
    if result.returncode != 0:
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
            str(path),
        ]
        result = subprocess.run(cmd, capture_output=True, check=False)
        assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _project(tmp_path: Path) -> Project:
    root = tmp_path / "Spain"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    for name in ("Castle Combe", "Rocamadour"):
        (root / name).mkdir()
    return Project(
        id="portable-r2",
        name="Portable R2",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        language="de",
        asset_subdir_names=["Castle Combe", "Rocamadour"],
        selected_asset_subdirs=["Castle Combe", "Rocamadour"],
        fps=25.0,
    )


def _save_folder_inventory(project: Project, folder: str, assets: list[tuple[Path, str]]) -> None:
    items = []
    names = []
    for media, asset_id in assets:
        rel = f"{folder}/{media.name}"
        kind = "video" if media.suffix.lower() in {".mov", ".mp4"} else "photo"
        items.append(
            AssetMediaAnalysis(
                path=rel,
                description=f"{folder} {media.name}",
                asset_id=asset_id,
                media_type=kind,
            )
        )
        names.append(media.name)
    inv = AssetFolderAnalysis(folder=folder, assets=items, media_files=names)
    save_folder_inventory(get_folder_inventory_path(project.work_dir_path, folder), inv)


def _lock_and_audio(project: Project, wav: Path, duration: float) -> None:
    save_script_draft(
        project,
        EnhancedScriptDocument(
            narration_full="Portable export narration for resolve package.",
            segments=[
                ScriptSegment(
                    segment_id="Castle_Combe_segment_001",
                    text="Portable export narration for resolve package.",
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


def _build_full_project(tmp_path: Path) -> tuple[Project, str, str, str, str]:
    project = _project(tmp_path)
    root = Path(project.project_root)
    a = root / "Castle Combe" / "Asset00011.mov"
    b = root / "Rocamadour" / "Asset00011.mov"
    jpg = root / "Castle Combe" / "still_test.jpg"
    png = root / "Rocamadour" / "still_test.png"
    _ffmpeg_color_video(a, duration=12.0, color="red", text="CASTLE")
    _ffmpeg_color_video(b, duration=18.0, color="blue", text="ROCA")
    Image.new("RGB", (64, 48), color=(200, 40, 40)).save(jpg, format="JPEG")
    Image.new("RGB", (64, 48), color=(40, 40, 200)).save(png, format="PNG")

    id_a = enhanced_asset_id_for_path(project, a, folder_name="Castle Combe")
    id_b = enhanced_asset_id_for_path(project, b, folder_name="Rocamadour")
    id_jpg = enhanced_asset_id_for_path(project, jpg, folder_name="Castle Combe")
    id_png = enhanced_asset_id_for_path(project, png, folder_name="Rocamadour")
    assert id_a != id_b

    _save_folder_inventory(project, "Castle Combe", [(a, id_a), (jpg, id_jpg)])
    _save_folder_inventory(project, "Rocamadour", [(b, id_b), (png, id_png)])

    wav = project.work_dir_path / "narration.wav"
    _write_silent_wav(wav, 16.0)
    _lock_and_audio(project, wav, 16.0)
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
            shot_min_sec=0.4,
            shot_max_sec=60.0,
            max_asset_usage=5,
        ),
    )
    write_json(
        final_cut_plan_path(project),
        FinalCutPlanDocument(
            script_version="script-v1",
            shots=[
                FinalShot(
                    shot_id="shot_castle",
                    narration_start_anchor=NarrationAnchor(
                        segment_id="Castle_Combe_segment_001", offset_seconds=0.0
                    ),
                    narration_end_anchor=NarrationAnchor(
                        segment_id="Castle_Combe_segment_001", offset_seconds=2.0
                    ),
                    asset_id=id_a,
                ),
                FinalShot(
                    shot_id="shot_jpg",
                    narration_start_anchor=NarrationAnchor(
                        segment_id="Castle_Combe_segment_001", offset_seconds=2.0
                    ),
                    narration_end_anchor=NarrationAnchor(
                        segment_id="Castle_Combe_segment_001", offset_seconds=6.5
                    ),
                    asset_id=id_jpg,
                ),
                FinalShot(
                    shot_id="shot_png",
                    narration_start_anchor=NarrationAnchor(
                        segment_id="Castle_Combe_segment_001", offset_seconds=6.5
                    ),
                    narration_end_anchor=NarrationAnchor(
                        segment_id="Castle_Combe_segment_001", offset_seconds=11.0
                    ),
                    asset_id=id_png,
                ),
                FinalShot(
                    shot_id="shot_roca",
                    narration_start_anchor=NarrationAnchor(
                        segment_id="Castle_Combe_segment_001", offset_seconds=11.0
                    ),
                    narration_end_anchor=NarrationAnchor(
                        segment_id="Castle_Combe_segment_001", offset_seconds=16.0
                    ),
                    asset_id=id_b,
                ),
            ],
        ),
    )
    resolved = resolve_final_timeline(project)
    assert not resolved.errors, resolved.errors
    return project, id_a, id_b, id_jpg, id_png


def test_same_basename_videos_get_unique_package_names(tmp_path: Path) -> None:
    project, id_a, id_b, _jpg, _png = _build_full_project(tmp_path)
    package = export_portable_otio_package(
        project, basename="portable_ok", allow_errors=False
    )
    assert package.is_dir()
    media_dir = package / "media"
    name_a = f"{id_a}.mov"
    name_b = f"{id_b}.mov"
    assert name_a != name_b
    assert (media_dir / name_a).is_file()
    assert (media_dir / name_b).is_file()
    assert _sha256(media_dir / name_a) != _sha256(media_dir / name_b)

    tl = otio.adapters.read_from_file(str(package / "timeline.otio"))
    urls = []
    for track in tl.tracks:
        for item in track:
            media = getattr(item, "media_reference", None)
            if media is None:
                continue
            target = getattr(media, "target_url", None)
            if target:
                urls.append(str(target))
    assert f"media/{name_a}" in urls
    assert f"media/{name_b}" in urls
    assert len({u for u in urls if u.endswith(".mov")}) >= 2
    for url in urls:
        assert "Asset00011.mov" not in Path(url).name or Path(url).name.startswith("asset__")
        assert "/opt/cursor" not in url
        assert "/workspace" not in url
        assert not url.lower().startswith(("http://", "https://"))
        assert not Path(url).is_absolute()
        assert url.startswith("media/")

    manifest = json.loads((package / "media_manifest.json").read_text(encoding="utf-8"))
    by_id = {row["asset_id"]: row for row in manifest}
    assert by_id[id_a]["packaged_filename"] == name_a
    assert by_id[id_b]["packaged_filename"] == name_b
    assert "Castle Combe" in by_id[id_a]["original_path"]
    assert "Rocamadour" in by_id[id_b]["original_path"]
    assert by_id[id_a]["sha256"] != by_id[id_b]["sha256"]


def test_portable_preserves_r1_timing_and_holds(tmp_path: Path) -> None:
    project, _a, _b, _jpg, _png = _build_full_project(tmp_path)
    resolved = resolve_final_timeline(project)
    package = export_portable_otio_package(project, basename="timing_ok")
    tl = otio.adapters.read_from_file(str(package / "timeline.otio"))

    assert abs(resolved.voiceover_preroll_sec - 1.0) < 1e-6
    assert abs(resolved.voiceover_postroll_sec - 5.0) < 1e-6
    assert abs(resolved.audio_segments[0].timeline_start_seconds - 1.0) < 0.05

    by_shot = {s.shot_id: s for s in resolved.shots}
    assert by_shot["shot_jpg"].timeline_end_seconds - by_shot["shot_jpg"].timeline_start_seconds >= 4.0
    assert by_shot["shot_png"].timeline_end_seconds - by_shot["shot_png"].timeline_start_seconds >= 4.0

    clips = {
        c.name: c
        for c in tl.tracks[0]
        if isinstance(c, otio.schema.Clip)
    }
    for shot_id in ("shot_jpg", "shot_png"):
        clip = clips[shot_id]
        assert clip.source_range.duration.to_seconds() >= 4.0 - 0.05
        assert clip.media_reference.available_range is not None
        url = str(clip.media_reference.target_url)
        assert url.startswith("media/")
        assert (package / url).is_file()

    # Source ranges remain inside available
    for clip in clips.values():
        src = clip.source_range
        avail = clip.media_reference.available_range
        src_end = src.start_time.to_seconds() + src.duration.to_seconds()
        avail_end = avail.start_time.to_seconds() + avail.duration.to_seconds()
        assert src.start_time.to_seconds() + 1e-6 >= avail.start_time.to_seconds()
        assert src_end <= avail_end + 0.05


def test_portable_rejects_allow_errors(tmp_path: Path) -> None:
    project, *_ = _build_full_project(tmp_path)
    with pytest.raises(EnhancedOtioExportError, match="allow_errors"):
        export_portable_otio_package(project, basename="bad", allow_errors=True)


def test_package_filename_collision_blocks(tmp_path: Path) -> None:
    project = _project(tmp_path)
    a = Path(project.project_root) / "Castle Combe" / "one.mov"
    b = Path(project.project_root) / "Rocamadour" / "two.mov"
    _ffmpeg_color_video(a, duration=2.0, color="red")
    _ffmpeg_color_video(b, duration=2.0, color="blue")
    package = tmp_path / "pkg"
    package.mkdir()
    # Force identical packaged filenames via identical asset_id stem misuse
    with pytest.raises(PortableExportError, match="kollidiert|Kollision|block"):
        stage_media_into_package(
            project,
            package,
            [
                (a, "dup_name", "video"),
                (b, "dup_name", "video"),
            ],
        )


def test_image_and_video_same_stem_unique_names(tmp_path: Path) -> None:
    project = _project(tmp_path)
    video = Path(project.project_root) / "Castle Combe" / "Asset00011.mov"
    image = Path(project.project_root) / "Castle Combe" / "Asset00011.jpg"
    _ffmpeg_color_video(video, duration=2.0, color="green")
    Image.new("RGB", (32, 32), color=(10, 200, 10)).save(image, format="JPEG")
    id_v = enhanced_asset_id_for_path(project, video, folder_name="Castle Combe")
    id_i = enhanced_asset_id_for_path(project, image, folder_name="Castle Combe")
    # Same stem → same hash path differs by suffix → different IDs
    assert id_v != id_i
    name_v = packaged_filename_for_media(project, video, asset_id=id_v, media_kind="video")
    name_i = packaged_filename_for_media(
        project, image, asset_id=id_i, media_kind="still_hold"
    )
    assert name_v != name_i
    assert name_v.endswith(".mov")
    assert "still_hold_" in name_i or name_i.endswith(".jpg")
