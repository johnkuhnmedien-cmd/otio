"""R1A: Enhanced Mehrkapitel-Hüllen, Gap/Overlap-Gate, lokaler Export-Standard."""

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
    export_otio_from_resolved_timeline,
    export_portable_otio_package,
    validate_resolved_timeline_for_production,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    exports_dir,
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
    build_shot_continuity_table,
    resolve_final_timeline,
)


CHAPTERS = ("Castle Combe", "Albarracín", "Rocamadour")
PREROLL = 1.0
POSTROLL = 5.0
SEG_DUR = 4.0


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
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")


def _seg_id(folder: str, index: int) -> str:
    slug = folder.replace(" ", "_").replace("í", "i").replace("á", "a")
    return f"{slug}_segment_{index:03d}"


def _shot_id(folder: str, index: int) -> str:
    slug = folder.replace(" ", "_").replace("í", "i").replace("á", "a")
    return f"{slug}_shot_{index:03d}"


def _project(tmp_path: Path) -> Project:
    root = tmp_path / "Spain_Multichapter"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    for name in CHAPTERS:
        (root / name).mkdir()
    return Project(
        id="multichapter-r1a",
        name="Multichapter R1A",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        language="de",
        asset_subdir_names=list(CHAPTERS),
        selected_asset_subdirs=list(CHAPTERS),
        fps=25.0,
        width=1920,
        height=1080,
    )


def _save_inventory(project: Project, folder: str, media_files: list[Path]) -> list[str]:
    items: list[AssetMediaAnalysis] = []
    ids: list[str] = []
    for media in media_files:
        asset_id = enhanced_asset_id_for_path(project, media, folder_name=folder)
        ids.append(asset_id)
        items.append(
            AssetMediaAnalysis(
                path=f"{folder}/{media.name}",
                description=f"{folder} media",
                asset_id=asset_id,
                media_type=(
                    "video" if media.suffix.lower() in {".mov", ".mp4"} else "photo"
                ),
            )
        )
    inv = AssetFolderAnalysis(
        folder=folder,
        assets=items,
        media_files=[m.name for m in media_files],
    )
    save_folder_inventory(get_folder_inventory_path(project.work_dir_path, folder), inv)
    return ids


def _build_three_chapter_project(tmp_path: Path) -> tuple[Project, dict[str, str]]:
    """Drei Kapitel, je 2 Narrations + 2 Shots; Albarracín mit Still-Hold."""
    project = _project(tmp_path)
    colors = {"Castle Combe": "red", "Albarracín": "green", "Rocamadour": "blue"}
    asset_ids: dict[str, str] = {}

    # Castle Combe: two videos
    castle_paths = []
    for name in ("v1.mov", "v2.mov"):
        path = Path(project.project_root) / "Castle Combe" / name
        _ffmpeg_color_video(path, duration=20.0, color=colors["Castle Combe"])
        castle_paths.append(path)
    castle_ids = _save_inventory(project, "Castle Combe", castle_paths)
    asset_ids["Castle Combe:1"] = castle_ids[0]
    asset_ids["Castle Combe:2"] = castle_ids[1]

    # Albarracín: video + still
    alb_v = Path(project.project_root) / "Albarracín" / "clip.mov"
    _ffmpeg_color_video(alb_v, duration=20.0, color=colors["Albarracín"])
    alb_still = Path(project.project_root) / "Albarracín" / "still.jpg"
    Image.new("RGB", (64, 64), color=(10, 120, 40)).save(alb_still, format="JPEG")
    alb_ids = _save_inventory(project, "Albarracín", [alb_v, alb_still])
    asset_ids["Albarracín:1"] = alb_ids[0]
    asset_ids["Albarracín:2"] = alb_ids[1]

    # Rocamadour: two videos
    roca_paths = []
    for name in ("v1.mov", "v2.mov"):
        path = Path(project.project_root) / "Rocamadour" / name
        _ffmpeg_color_video(path, duration=20.0, color=colors["Rocamadour"])
        roca_paths.append(path)
    roca_ids = _save_inventory(project, "Rocamadour", roca_paths)
    asset_ids["Rocamadour:1"] = roca_ids[0]
    asset_ids["Rocamadour:2"] = roca_ids[1]

    segments: list[ScriptSegment] = []
    timings: list[SegmentTiming] = []
    entries: list[NarrationTimelineEntry] = []
    shots: list[FinalShot] = []
    cursor = 0.0
    seq = 1
    for folder in CHAPTERS:
        for seg_i in (1, 2):
            sid = _seg_id(folder, seg_i)
            wav = project.work_dir_path / f"{sid}.wav"
            _write_silent_wav(wav, SEG_DUR)
            segments.append(
                ScriptSegment(
                    segment_id=sid,
                    text=f"{folder} narration {seg_i}",
                    sequence_index=seq,
                    folder_name=folder,
                    folder_order_index=CHAPTERS.index(folder) + 1,
                )
            )
            timings.append(
                SegmentTiming(
                    segment_id=sid,
                    script_version="script-v1",
                    audio_path=str(wav),
                    duration_seconds=SEG_DUR,
                    audio_status="valid",
                )
            )
            entries.append(
                NarrationTimelineEntry(
                    segment_id=sid,
                    start_seconds=cursor,
                    end_seconds=cursor + SEG_DUR,
                    pause_after_seconds=0.0,
                    audio_duration_seconds=SEG_DUR,
                )
            )
            # One shot per segment, abutting within chapter.
            shots.append(
                FinalShot(
                    shot_id=_shot_id(folder, seg_i),
                    narration_start_anchor=NarrationAnchor(
                        segment_id=sid, offset_seconds=0.0
                    ),
                    narration_end_anchor=NarrationAnchor(
                        segment_id=sid, offset_seconds=SEG_DUR
                    ),
                    asset_id=asset_ids[f"{folder}:{seg_i}"],
                )
            )
            cursor += SEG_DUR
            seq += 1

    save_script_draft(
        project,
        EnhancedScriptDocument(
            narration_full=" ".join(s.text for s in segments),
            segments=segments,
        ),
    )
    lock_script(project)
    write_json(
        segment_timings_path(project),
        SegmentTimingsDocument(script_version="script-v1", segments=timings),
    )
    write_json(
        narration_timeline_path(project),
        NarrationTimelineDocument(
            script_version="script-v1",
            total_duration_seconds=cursor,
            entries=entries,
        ),
    )
    write_json(
        final_cut_plan_path(project),
        FinalCutPlanDocument(script_version="script-v1", shots=shots),
    )
    save_cut_plan_options(
        project,
        CutPlanOptions(
            voiceover_preroll_sec=PREROLL,
            voiceover_postroll_sec=POSTROLL,
            voiceover_preroll_mode="fixed",
            voiceover_postroll_mode="fixed",
            video_head_trim_sec=0.0,
            still_image_style_enabled=False,
            short_asset_tolerance_sec=0.0,
            max_asset_usage=10,
            min_asset_reuse_distance_shots=0,
        ),
    )
    return project, asset_ids


def test_three_chapter_envelopes_and_coverage(tmp_path: Path) -> None:
    project, _ids = _build_three_chapter_project(tmp_path)
    resolved = resolve_final_timeline(project)

    assert len(resolved.chapters) == 3
    assert abs(resolved.voiceover_preroll_sec - PREROLL) < 1e-6
    assert abs(resolved.voiceover_postroll_sec - POSTROLL) < 1e-6

    prev_end = 0.0
    for chapter in resolved.chapters:
        assert abs(chapter.chapter_video_start - prev_end) < 1e-3
        assert abs(chapter.chapter_audio_start - (chapter.chapter_video_start + PREROLL)) < 1e-3
        assert abs(chapter.chapter_video_end - (chapter.chapter_audio_end + POSTROLL)) < 1e-3
        assert abs(chapter.chapter_audio_end - chapter.chapter_audio_start - (2 * SEG_DUR)) < 1e-2
        assert chapter.visual_gap_count == 0
        assert chapter.visual_overlap_count == 0

        ch_shots = [
            s for s in resolved.shots if s.chapter_id == chapter.chapter_id
        ]
        ch_audio = [
            a for a in resolved.audio_segments if a.chapter_id == chapter.chapter_id
        ]
        assert len(ch_shots) >= 2
        assert len(ch_audio) >= 2
        assert min(s.timeline_start_seconds for s in ch_shots) <= chapter.chapter_video_start + 1e-3
        assert max(s.timeline_end_seconds for s in ch_shots) + 1e-3 >= chapter.chapter_video_end
        assert min(a.timeline_start_seconds for a in ch_audio) == pytest.approx(
            chapter.chapter_audio_start, abs=1e-3
        )
        assert min(s.timeline_start_seconds for s in ch_shots) < min(
            a.timeline_start_seconds for a in ch_audio
        )
        prev_end = chapter.chapter_video_end

    # Still-Hold in Albarracín remains a hold, no zoom metadata later.
    alb_still_shots = [
        s
        for s in resolved.shots
        if s.chapter_id == "Albarracín" and s.hold_mode == "freeze_video"
    ]
    assert alb_still_shots

    table = build_shot_continuity_table(resolved.shots, fps=resolved.fps)
    assert table
    assert all(row["repair_or_error"] in {"ok", "frame_snap_candidate"} for row in table)


def test_single_chapter_compatible(tmp_path: Path) -> None:
    project = _project(tmp_path)
    video = Path(project.project_root) / "Castle Combe" / "only.mov"
    _ffmpeg_color_video(video, duration=20.0, color="teal")
    asset_id = _save_inventory(project, "Castle Combe", [video])[0]
    wav = project.work_dir_path / "one.wav"
    _write_silent_wav(wav, 4.0)
    save_script_draft(
        project,
        EnhancedScriptDocument(
            narration_full="One chapter only.",
            segments=[
                ScriptSegment(
                    segment_id="Castle_Combe_segment_001",
                    text="One chapter only.",
                    sequence_index=1,
                    folder_name="Castle Combe",
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
                    duration_seconds=4.0,
                    audio_status="valid",
                )
            ],
        ),
    )
    write_json(
        narration_timeline_path(project),
        NarrationTimelineDocument(
            script_version="script-v1",
            total_duration_seconds=4.0,
            entries=[
                NarrationTimelineEntry(
                    segment_id="Castle_Combe_segment_001",
                    start_seconds=0.0,
                    end_seconds=4.0,
                    pause_after_seconds=0.0,
                    audio_duration_seconds=4.0,
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
    save_cut_plan_options(
        project,
        CutPlanOptions(
            voiceover_preroll_sec=1.0,
            voiceover_postroll_sec=5.0,
            voiceover_preroll_mode="fixed",
            voiceover_postroll_mode="fixed",
            still_image_style_enabled=False,
            short_asset_tolerance_sec=0.0,
        ),
    )
    resolved = resolve_final_timeline(project)
    assert len(resolved.chapters) == 1
    assert abs(resolved.audio_segments[0].timeline_start_seconds - 1.0) < 1e-3
    assert min(s.timeline_start_seconds for s in resolved.shots) == pytest.approx(
        0.0, abs=1e-3
    )
    assert max(s.timeline_end_seconds for s in resolved.shots) + 1e-3 >= 4.0 + 1.0 + 5.0
    editorial = next(s for s in resolved.shots if s.shot_id == "main_shot")
    # Einziger Shot trägt Vorlauf (0–1) und Nachlauf (5–10) selbst.
    assert editorial.timeline_start_seconds == pytest.approx(0.0, abs=1e-3)
    assert editorial.timeline_end_seconds == pytest.approx(10.0, abs=1e-3)
    assert not any(
        str(s.editorial_function or "").startswith("technical_chapter_")
        for s in resolved.shots
    )


def test_large_gap_blocks_and_one_frame_snaps(tmp_path: Path) -> None:
    project, ids = _build_three_chapter_project(tmp_path)
    # Inject intentional large gap inside Castle Combe by shortening first shot end.
    plan = FinalCutPlanDocument.model_validate(
        json.loads(final_cut_plan_path(project).read_text(encoding="utf-8"))
    )
    # First shot ends early → leaves ~1.5s visual gap before second shot.
    plan.shots[0].narration_end_anchor.offset_seconds = 2.5
    plan.shots[1].narration_start_anchor.offset_seconds = 0.0
    write_json(final_cut_plan_path(project), plan)
    with pytest.raises(TimelineResolveError, match="Visuelle Lücke"):
        resolve_final_timeline(project)

    # One-frame gap: 1/25s = 0.04s
    project2, _ = _build_three_chapter_project(tmp_path / "frame")
    plan2 = FinalCutPlanDocument.model_validate(
        json.loads(final_cut_plan_path(project2).read_text(encoding="utf-8"))
    )
    # End first shot 1 frame early within same segment span by using offsets:
    # shot0: 0–3.96, shot1: 4.0–8.0 on raw timeline — after envelope still ~0.04 gap.
    plan2.shots[0].narration_end_anchor.offset_seconds = SEG_DUR - 0.04
    write_json(final_cut_plan_path(project2), plan2)
    resolved = resolve_final_timeline(project2)
    assert any("Ein-Frame-Gap" in r for r in resolved.repairs)
    assert not any("Visuelle Lücke" in e for e in resolved.errors)


def test_overlap_blocks_production(tmp_path: Path) -> None:
    project, _ = _build_three_chapter_project(tmp_path)
    plan = FinalCutPlanDocument.model_validate(
        json.loads(final_cut_plan_path(project).read_text(encoding="utf-8"))
    )
    # Overlap: first shot covers full 0–4, second also starts at 0 of same segment.
    plan.shots[1].narration_start_anchor = NarrationAnchor(
        segment_id=plan.shots[0].narration_start_anchor.segment_id,
        offset_seconds=1.0,
    )
    plan.shots[1].narration_end_anchor = NarrationAnchor(
        segment_id=plan.shots[0].narration_start_anchor.segment_id,
        offset_seconds=3.0,
    )
    write_json(final_cut_plan_path(project), plan)
    with pytest.raises(TimelineResolveError, match="Überlappung"):
        resolve_final_timeline(project)


def test_leading_editorial_gap_blocks(tmp_path: Path) -> None:
    project, _ = _build_three_chapter_project(tmp_path)
    plan = FinalCutPlanDocument.model_validate(
        json.loads(final_cut_plan_path(project).read_text(encoding="utf-8"))
    )
    plan.shots[0].narration_start_anchor.offset_seconds = 2.0
    write_json(final_cut_plan_path(project), plan)
    with pytest.raises(TimelineResolveError, match="Führende visuelle Lücke"):
        resolve_final_timeline(project)


def test_trailing_narration_gap_blocks(tmp_path: Path) -> None:
    project, _ = _build_three_chapter_project(tmp_path)
    # Kapitel-Audio 8s; nur der erste Shot (0–4s) bleibt — 4s Narration ungedeckt.
    plan = FinalCutPlanDocument.model_validate(
        json.loads(final_cut_plan_path(project).read_text(encoding="utf-8"))
    )
    plan.shots = [plan.shots[0], *plan.shots[2:]]
    write_json(final_cut_plan_path(project), plan)
    with pytest.raises(
        TimelineResolveError, match="Abschließende visuelle Lücke während der Narration"
    ):
        resolve_final_timeline(project)


def test_postroll_extends_closing_shot_not_separate_hold(tmp_path: Path) -> None:
    project, _ = _build_three_chapter_project(tmp_path)
    resolved = resolve_final_timeline(project)
    for chapter in resolved.chapters:
        assert not chapter.postroll_hold_shot_id
        assert not chapter.preroll_hold_shot_id
        closing = next(s for s in resolved.shots if s.shot_id == chapter.last_shot_id)
        opening = next(s for s in resolved.shots if s.shot_id == chapter.first_shot_id)
        assert opening.timeline_start_seconds == pytest.approx(
            chapter.chapter_video_start, abs=1e-3
        )
        assert closing.timeline_end_seconds == pytest.approx(
            chapter.chapter_video_end, abs=1e-3
        )
        editorial = [
            s
            for s in resolved.shots
            if s.chapter_id == chapter.chapter_id
            and not str(s.editorial_function or "").startswith("technical_chapter_")
        ]
        for shot in editorial:
            if shot.shot_id in {chapter.first_shot_id, chapter.last_shot_id}:
                continue
            dur = shot.timeline_end_seconds - shot.timeline_start_seconds
            assert dur <= SEG_DUR + 1e-3
    assert not any(
        str(s.editorial_function or "").startswith("technical_chapter_")
        for s in resolved.shots
    )


def test_adjacent_same_asset_blocks_including_opening_closing(tmp_path: Path) -> None:
    project, ids = _build_three_chapter_project(tmp_path)
    plan = FinalCutPlanDocument.model_validate(
        json.loads(final_cut_plan_path(project).read_text(encoding="utf-8"))
    )
    # Opening und nächster Shot dasselbe Asset → fail-closed.
    plan.shots[1].asset_id = plan.shots[0].asset_id
    write_json(final_cut_plan_path(project), plan)
    with pytest.raises(TimelineResolveError, match="Benachbarte Shots nutzen dasselbe Asset"):
        resolve_final_timeline(project)


def test_may_overlap_pause_still_blocks_video_overlap(tmp_path: Path) -> None:
    project, _ = _build_three_chapter_project(tmp_path)
    plan = FinalCutPlanDocument.model_validate(
        json.loads(final_cut_plan_path(project).read_text(encoding="utf-8"))
    )
    plan.shots[0].may_overlap_pause = True
    plan.shots[1].may_overlap_pause = True
    plan.shots[1].narration_start_anchor = NarrationAnchor(
        segment_id=plan.shots[0].narration_start_anchor.segment_id,
        offset_seconds=2.0,
    )
    plan.shots[1].narration_end_anchor = NarrationAnchor(
        segment_id=plan.shots[0].narration_end_anchor.segment_id,
        offset_seconds=4.0,
    )
    write_json(final_cut_plan_path(project), plan)
    with pytest.raises(TimelineResolveError, match="Überlappung"):
        resolve_final_timeline(project)


def test_cross_chapter_shot_anchors_block(tmp_path: Path) -> None:
    project, _ = _build_three_chapter_project(tmp_path)
    plan = FinalCutPlanDocument.model_validate(
        json.loads(final_cut_plan_path(project).read_text(encoding="utf-8"))
    )
    # Start in Castle Combe, Ende in Albarracín.
    plan.shots[0].narration_end_anchor = NarrationAnchor(
        segment_id=plan.shots[2].narration_start_anchor.segment_id,
        offset_seconds=1.0,
    )
    write_json(final_cut_plan_path(project), plan)
    with pytest.raises(
        TimelineResolveError, match="unterschiedlichen Kapiteln"
    ):
        resolve_final_timeline(project)


def test_one_frame_edge_deviation_is_repaired(tmp_path: Path) -> None:
    project, _ = _build_three_chapter_project(tmp_path)
    plan = FinalCutPlanDocument.model_validate(
        json.loads(final_cut_plan_path(project).read_text(encoding="utf-8"))
    )
    # 1 Frame = 0.04s bei 25fps — führend knapp nach Audioanfang.
    plan.shots[0].narration_start_anchor.offset_seconds = 0.04
    write_json(final_cut_plan_path(project), plan)
    resolved = resolve_final_timeline(project)
    assert any("Ein-Frame-Randabweichung führend" in r for r in resolved.repairs)
    assert not any("Führende visuelle Lücke" in e for e in resolved.errors)


def test_local_export_no_original_video_copies_and_no_zoom(tmp_path: Path) -> None:
    project, _ = _build_three_chapter_project(tmp_path)
    resolved = resolve_final_timeline(project)
    assert not resolved.errors

    root = Path(project.project_root)
    before_videos = {
        p.resolve()
        for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in {".mov", ".mp4", ".jpg", ".jpeg", ".png", ".wav"}
    }
    before_export = set()
    exp = exports_dir(project)
    if exp.exists():
        before_export = {p.resolve() for p in exp.rglob("*") if p.is_file()}

    out = export_otio_from_resolved_timeline(
        project, basename="local_std", allow_errors=False
    )
    assert out.is_file()

    after_videos = {
        p.resolve()
        for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in {".mov", ".mp4"}
    }
    # Original project videos must remain; no new copies beside hold_cache / exports otio.
    original_movs = {
        p.resolve()
        for folder in CHAPTERS
        for p in (root / folder).glob("*")
        if p.suffix.lower() in {".mov", ".mp4"}
    }
    assert original_movs.issubset(after_videos)
    # No media/ staging folder for local export.
    assert not (exp / "local_std_package").exists()
    assert not list(exp.glob("**/media/*.mov"))

    payload = out.read_text(encoding="utf-8")
    assert "http://" not in payload
    assert "https://" not in payload
    assert "ZoomX" not in payload
    assert "zoom_factor" not in payload
    assert "build_resolve_zoom_effect" not in payload

    tl = otio.adapters.read_from_file(str(out))
    for track in tl.tracks:
        for item in track:
            if not isinstance(item, otio.schema.Clip):
                continue
            assert not list(item.effects or [])
            meta = dict(item.metadata or {})
            assert "zoom_factor" not in meta
            assert "ZoomX" not in str(meta)

    # Gate still clean.
    assert not validate_resolved_timeline_for_production(project, resolved)


def test_portable_export_still_works(tmp_path: Path) -> None:
    project, _ = _build_three_chapter_project(tmp_path)
    resolve_final_timeline(project)
    package = export_portable_otio_package(
        project, basename="portable_ok", allow_errors=False
    )
    assert (package / "timeline.otio").is_file()
    assert (package / "media").is_dir()
    assert list((package / "media").glob("*"))
    tl = otio.adapters.read_from_file(str(package / "timeline.otio"))
    for track in tl.tracks:
        for item in track:
            media = getattr(item, "media_reference", None)
            if media is None:
                continue
            url = str(getattr(media, "target_url", "") or "")
            if url:
                assert url.startswith("media/")
                assert not url.lower().startswith(("http://", "https://"))


def test_ui_local_export_is_primary() -> None:
    src = Path("otio_app/ui/without_voiceover_enhanced/final_output_tab.py").read_text(
        encoding="utf-8"
    )
    assert "Lokale Produktions-OTIO erzeugen" in src
    assert 'type="primary"' in src
    local_pos = src.index("Lokale Produktions-OTIO erzeugen")
    portable_pos = src.index("Portables Paket erzeugen")
    assert local_pos < portable_pos
    assert "erheblichen Speicherplatz" in src
    assert "Vorhandene Videos werden nicht kopiert" in src
    assert "allow_errors=False" in src
    assert "zoom" not in src.lower() or "Zoom" not in src


def test_ui_cut_plan_decouples_llm3_and_has_named_otio_export() -> None:
    src = Path("otio_app/ui/without_voiceover_enhanced/cut_plan_tab.py").read_text(
        encoding="utf-8"
    )
    assert "LLM-Lauf 3 + Python-Finalisierung" not in src
    assert "LLM-Lauf 3 starten" in src
    assert "Python-Finalisierung starten" in src
    assert "Dateiname / Export-Basename" in src
    assert "Lokale Produktions-OTIO erzeugen" in src
    assert "Portables Paket erzeugen" in src
    assert 'key="enh_final_cut_llm"' in src
    assert 'key="enh_final_cut_python"' in src


def test_albarracin_gap_root_cause_classification(tmp_path: Path) -> None:
    """Reproduziert Kapitel-interne Lücke und klassifiziert sie als Resolve-Gap."""
    project, _ = _build_three_chapter_project(tmp_path)
    plan = FinalCutPlanDocument.model_validate(
        json.loads(final_cut_plan_path(project).read_text(encoding="utf-8"))
    )
    # Albarracín shots are index 2 and 3 (0-based) after Castle Combe's two.
    alb_first = next(s for s in plan.shots if s.shot_id.startswith("Albarracin_shot"))
    alb_first.narration_end_anchor.offset_seconds = 2.0
    write_json(final_cut_plan_path(project), plan)
    with pytest.raises(TimelineResolveError, match="Albarrac"):
        resolve_final_timeline(project)
    # Document written before raise — inspect continuity table.
    resolved = ResolvedTimelineDocument.model_validate(
        json.loads(resolved_timeline_path(project).read_text(encoding="utf-8"))
    )
    table = build_shot_continuity_table(resolved.shots, fps=resolved.fps)
    alb_rows = [r for r in table if "Albarrac" in (r["chapter_id"] or "")]
    assert any(r["repair_or_error"] == "gap_error" for r in alb_rows)
    # Root cause: final_cut_plan anchors leave uncovered narration span;
    # Python resolution preserves the gap and now fail-closes instead of soft-repair.
