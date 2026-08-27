"""Keyword Flow R2: echte Produktions-Resolve/OTIO (keine synthetische Timeline)."""

from __future__ import annotations

import json
import subprocess
import wave
from pathlib import Path

import opentimelineio as otio
import pytest

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis
from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import get_folder_inventory_path
from otio_app.services.inventory_loader import save_folder_inventory
from otio_app.services.without_voiceover_enhanced.asset_identity import (
    enhanced_asset_id_for_path,
)
from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
    CUT_PLAN_MODE_UNIFIED,
    UNIFIED_CUT_STYLE_KEYWORD_FLOW,
    UNIFIED_CUT_STYLE_KEYWORD_SYNC,
    UNIFIED_CUT_STYLE_RHYTHM,
    CutPlanOptions,
    save_cut_plan_options,
)
from otio_app.services.without_voiceover_enhanced.io_utils import write_json
from otio_app.services.without_voiceover_enhanced.models import (
    CutBoundary,
    CutSlot,
    EnhancedScriptDocument,
    PauseDirective,
    ScriptSegment,
    SegmentAlignment,
    SegmentAlignmentsDocument,
    SegmentTiming,
    SegmentTimingsDocument,
    SentenceTiming,
    UnifiedCutPlanDocument,
)
from otio_app.services.without_voiceover_enhanced.otio_export_service import (
    EnhancedOtioExportError,
    export_otio_from_resolved_timeline,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    segment_alignments_path,
    segment_timestamps_path,
    segment_timings_path,
)
from otio_app.services.without_voiceover_enhanced.script_author_service import (
    save_script_draft,
)
from otio_app.services.without_voiceover_enhanced.script_lock_service import lock_script
from otio_app.services.without_voiceover_enhanced.sentence_timing_prompt import (
    clean_words_for_keyword_flow_prompt,
)
from otio_app.services.without_voiceover_enhanced.unified_cut_plan import (
    parse_unified_cut_response,
)
from otio_app.services.without_voiceover_enhanced.unified_timeline_service import (
    resolve_unified_timeline,
)


def _write_silent_wav(path: Path, duration_sec: float, rate: int = 16000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = max(1, int(duration_sec * rate))
    with wave.open(str(path), "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * frames)


def _ffmpeg_color_video(
    path: Path, *, duration: float, color: str = "green", size: str = "320x240"
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s={size}:d={duration}",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


def _save_inventory(project: Project, folder: str, media_files: list[Path]) -> dict[str, str]:
    """Schreibt alle Medien eines Ordners in ein Inventar; liefert name→asset_id."""
    assets: list[AssetMediaAnalysis] = []
    ids: dict[str, str] = {}
    for media in media_files:
        rel = f"{folder}/{media.name}"
        asset_id = enhanced_asset_id_for_path(project, media, folder_name=folder)
        assets.append(
            AssetMediaAnalysis(
                path=rel,
                description=f"{folder} {media.name}",
                asset_id=asset_id,
                media_type=(
                    "video" if media.suffix.lower() in {".mov", ".mp4"} else "photo"
                ),
            )
        )
        ids[media.stem] = asset_id
    inv = AssetFolderAnalysis(
        folder=folder,
        assets=assets,
        media_files=[m.name for m in media_files],
    )
    save_folder_inventory(get_folder_inventory_path(project.work_dir_path, folder), inv)
    return ids


def _write_alignment_words(
    project: Project,
    *,
    seg_id: str,
    words: list[tuple[str, float, float]],
    sentences: list[SentenceTiming],
    audio_path: str,
    audio_duration: float,
) -> None:
    # Character alignment covering words with spaces between.
    chars: list[str] = []
    starts: list[float] = []
    ends: list[float] = []
    for index, (text, start, end) in enumerate(words):
        if index:
            chars.append(" ")
            gap_t = (ends[-1] + start) / 2.0 if ends else start
            starts.append(gap_t)
            ends.append(gap_t)
        span = max(len(text), 1)
        for offset, ch in enumerate(text):
            chars.append(ch)
            t0 = start + (end - start) * (offset / span)
            t1 = start + (end - start) * ((offset + 1) / span)
            starts.append(t0)
            ends.append(t1)
    ts_path = segment_timestamps_path(project, seg_id)
    ts_path.parent.mkdir(parents=True, exist_ok=True)
    ts_path.write_text(
        json.dumps(
            {
                "alignment": {
                    "characters": chars,
                    "character_start_times_seconds": starts,
                    "character_end_times_seconds": ends,
                }
            }
        ),
        encoding="utf-8",
    )
    write_json(
        segment_alignments_path(project),
        SegmentAlignmentsDocument(
            script_version="script-v1",
            segments=[
                SegmentAlignment(
                    segment_id=seg_id,
                    script_version="script-v1",
                    audio_path=audio_path,
                    audio_duration_seconds=audio_duration,
                    tts_text=" ".join(w[0] for w in words),
                    timestamps_path=str(ts_path),
                    sentences=sentences,
                )
            ],
        ),
    )


def _build_chapter_a_project(
    tmp_path: Path,
    *,
    project_dirname: str = "KFProd",
    project_name: str = "KF E2E",
    project_id: str = "kf-e2e",
) -> tuple[Project, dict[str, str]]:
    root = tmp_path / project_dirname
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    for name in ("ChapterA", "Maps"):
        (root / name).mkdir()
    project = Project(
        id=project_id,
        name=project_name,
        project_root=str(root.resolve()),
        work_dir=str(work.resolve()),
        language="de",
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        fps=25.0,
        width=1920,
        height=1080,
        asset_subdir_names=["ChapterA", "Maps"],
        selected_asset_subdirs=["ChapterA", "Maps"],
    )
    ids: dict[str, str] = {}
    clip1 = root / "ChapterA" / "clip_a1.mp4"
    clip2 = root / "ChapterA" / "clip_a2.mp4"
    close = root / "ChapterA" / "close_a.mp4"
    fallback = root / "ChapterA" / "fallback_a.mp4"
    # Long enough for map-replaced preroll + narration + postroll holds.
    _ffmpeg_color_video(clip1, duration=24.0, color="blue")
    _ffmpeg_color_video(clip2, duration=16.0, color="green")
    _ffmpeg_color_video(close, duration=24.0, color="red")
    _ffmpeg_color_video(fallback, duration=24.0, color="yellow")
    chapter_ids = _save_inventory(project, "ChapterA", [clip1, clip2, close, fallback])
    ids["clip1"] = chapter_ids["clip_a1"]
    ids["clip2"] = chapter_ids["clip_a2"]
    ids["close"] = chapter_ids["close_a"]
    ids["fallback"] = chapter_ids["fallback_a"]

    map_path = root / "Maps" / "ChapterA.mp4"
    _ffmpeg_color_video(map_path, duration=12.0, color="white")
    # Maps folder is technical opener source; inventory optional.

    wav = work / "audio" / "ChapterA_segment_001.wav"
    audio_dur = 12.0
    _write_silent_wav(wav, audio_dur)

    seg_id = "ChapterA_segment_001"
    save_script_draft(
        project,
        EnhancedScriptDocument(
            script_version="script-v1",
            narration_full="Der Salto Angel faellt. Dann folgt der Ausklang.",
            segments=[
                ScriptSegment(
                    segment_id=seg_id,
                    text="Der Salto Angel faellt. Dann folgt der Ausklang.",
                    sequence_index=1,
                    folder_name="ChapterA",
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
                    segment_id=seg_id,
                    script_version="script-v1",
                    audio_path=str(wav),
                    duration_seconds=audio_dur,
                    audio_status="valid",
                )
            ],
        ),
    )
    s1 = f"{seg_id}__s001"
    s2 = f"{seg_id}__s002"
    sentences = [
        SentenceTiming(
            sentence_id=s1,
            segment_id=seg_id,
            text="Der Salto Angel faellt.",
            start_seconds=0.0,
            end_seconds=9.0,
            duration_seconds=9.0,
        ),
        SentenceTiming(
            sentence_id=s2,
            segment_id=seg_id,
            text="Dann folgt der Ausklang.",
            start_seconds=9.2,
            end_seconds=12.0,
            duration_seconds=2.8,
        ),
    ]
    # Natural gap only 0.25s (< 0.4s = 10 frames) — R2 must still allow long pause.
    words = [
        ("Der", 0.0, 0.3),
        ("Salto", 0.4, 0.9),
        ("Angel", 1.0, 1.5),
        ("faellt", 8.0, 8.9),
        ("Dann", 9.15, 9.5),
        ("folgt", 9.6, 10.0),
        ("der", 10.1, 10.3),
        ("Ausklang", 10.4, 11.8),
    ]
    _write_alignment_words(
        project,
        seg_id=seg_id,
        words=words,
        sentences=sentences,
        audio_path=str(wav),
        audio_duration=audio_dur,
    )
    save_cut_plan_options(
        project,
        CutPlanOptions(
            cut_plan_mode=CUT_PLAN_MODE_UNIFIED,
            unified_cut_style=UNIFIED_CUT_STYLE_KEYWORD_FLOW,
            shot_min_sec=4.0,
            shot_max_sec=9.0,
            voiceover_preroll_sec=1.0,
            voiceover_postroll_sec=2.0,
            video_head_trim_sec=0.0,
            still_image_style_enabled=False,
        ),
    )
    ids["s1"] = s1
    ids["s2"] = s2
    ids["seg"] = seg_id
    return project, ids


def _plan_with_pause_and_closing(ids: dict[str, str]) -> UnifiedCutPlanDocument:
    return UnifiedCutPlanDocument(
        script_version="script-v1",
        closing_fallback_asset_id=ids["fallback"],
        closing_fallback_asset_fit="acceptable",
        closing_fallback_asset_fit_reason="reserve closer same chapter intent",
        closing_fallback_visual_intent="same closing intent as primary",
        pause_directives=[],
        boundaries=[
            CutBoundary(
                cut_id="c0",
                sentence_id=ids["s1"],
                position="start",
                offset_seconds=0.0,
                alignment="sentence_boundary",
            ),
            CutBoundary(
                cut_id="c1",
                sentence_id=ids["s1"],
                position="end",
                alignment="sentence_boundary",
            ),
            CutBoundary(
                cut_id="c2",
                sentence_id=ids["s2"],
                position="end",
                alignment="sentence_boundary",
            ),
        ],
        slots=[
            CutSlot(
                slot_id="slot_a",
                local_asset_id=ids["clip1"],
                asset_fit="strong",
                asset_fit_reason="named entity",
            ),
            CutSlot(
                slot_id="slot_close",
                local_asset_id=ids["close"],
                asset_fit="strong",
                asset_fit_reason="closing",
                narrative_function="chapter_close",
            ),
        ],
    )


def test_spoken_numbers_kept_pause_tag_digits_removed() -> None:
    raw = [
        {"text": "Year", "offset_seconds": 0.0, "start_seconds": 0.0, "end_seconds": 0.2, "original_word_index": 0},
        {"text": "400", "offset_seconds": 0.2, "start_seconds": 0.2, "end_seconds": 0.5, "original_word_index": 1},
        {"text": "and", "offset_seconds": 0.5, "start_seconds": 0.5, "end_seconds": 0.7, "original_word_index": 2},
        {"text": "1889", "offset_seconds": 0.7, "start_seconds": 0.7, "end_seconds": 1.0, "original_word_index": 3},
        {"text": "plus", "offset_seconds": 1.0, "start_seconds": 1.0, "end_seconds": 1.2, "original_word_index": 4},
        {"text": "12.5", "offset_seconds": 1.2, "start_seconds": 1.2, "end_seconds": 1.5, "original_word_index": 5},
        {"text": "[pause", "offset_seconds": 1.5, "start_seconds": 1.5, "end_seconds": 1.55, "original_word_index": 6},
        {"text": "2", "offset_seconds": 1.55, "start_seconds": 1.55, "end_seconds": 1.6, "original_word_index": 7},
        {"text": "seconds]", "offset_seconds": 1.6, "start_seconds": 1.6, "end_seconds": 1.65, "original_word_index": 8},
    ]
    cleaned = clean_words_for_keyword_flow_prompt(raw, sentence_id="s")
    texts = [w["text"] for w in cleaned]
    assert "400" in texts
    assert "1889" in texts
    assert "12.5" in texts
    assert "2" not in texts
    assert "[pause" not in texts


def test_production_otio_has_map_and_no_keyword_flow_audio_gaps(tmp_path: Path) -> None:
    project, ids = _build_chapter_a_project(tmp_path)
    plan = _plan_with_pause_and_closing(ids)
    assert plan.pause_directives == []
    resolved = resolve_unified_timeline(
        project, plan, allow_open_gaps=False, persist=True
    )
    assert not resolved.errors, resolved.errors
    # Map opener decision from real resolver repairs/metadata.
    assert any("Map-Opener" in r or "map" in r.lower() for r in resolved.repairs)
    assert any(
        str(s.editorial_function or "") == "technical_chapter_map_opener"
        for s in resolved.shots
    )
    # Keyword Flow darf keine künstliche Stille mehr einfügen.
    assert all(
        float(a.pause_after_seconds or 0.0) == pytest.approx(0.0)
        for a in resolved.audio_segments
    )

    out = export_otio_from_resolved_timeline(
        project, basename="kf_e2e_no_pause", resolved=resolved
    )
    assert out.is_file()
    tl = otio.adapters.read_from_file(str(out))
    audio = next(t for t in tl.tracks if t.kind == otio.schema.TrackKind.Audio)
    video = next(t for t in tl.tracks if t.kind == otio.schema.TrackKind.Video)

    gaps = [c for c in audio if isinstance(c, otio.schema.Gap)]
    pause_gaps = [
        g for g in gaps if abs(g.duration().to_seconds() - 1.5) < 0.05
    ]
    assert not pause_gaps, "OTIO darf keine KF-Pausen-Gaps enthalten"

    clips = [c for c in audio if isinstance(c, otio.schema.Clip)]
    assert clips
    covered = 0.0
    for clip in clips:
        src = clip.source_range
        assert src is not None
        covered += src.duration.to_seconds()
    assert covered == pytest.approx(12.0, abs=0.15)

    # Map opener on video before VO: first video clip ~9s technical map.
    vclips = [c for c in video if isinstance(c, otio.schema.Clip)]
    assert vclips
    first = vclips[0]
    assert first.duration().to_seconds() == pytest.approx(9.0, abs=0.08)


def test_rhythm_style_inserts_rendered_map_opener(tmp_path: Path) -> None:
    """Karten gehören in die Timeline unabhängig vom Unified-Stil."""
    project, ids = _build_chapter_a_project(tmp_path)
    save_cut_plan_options(
        project,
        CutPlanOptions(
            cut_plan_mode=CUT_PLAN_MODE_UNIFIED,
            unified_cut_style=UNIFIED_CUT_STYLE_RHYTHM,
            shot_min_sec=4.0,
            shot_max_sec=9.0,
            voiceover_preroll_sec=1.0,
            voiceover_postroll_sec=2.0,
            video_head_trim_sec=0.0,
            still_image_style_enabled=False,
        ),
    )
    plan = _plan_with_pause_and_closing(ids)
    resolved = resolve_unified_timeline(
        project, plan, allow_open_gaps=False, persist=True
    )
    assert not resolved.errors, resolved.errors
    assert any(
        str(s.editorial_function or "") == "technical_chapter_map_opener"
        for s in resolved.shots
    )
    map_shot = next(
        s
        for s in resolved.shots
        if str(s.editorial_function or "") == "technical_chapter_map_opener"
    )
    assert map_shot.timeline_end_seconds - map_shot.timeline_start_seconds == pytest.approx(
        9.0, abs=0.08
    )


def test_old_plan_with_pause_directives_blocks_resolve(tmp_path: Path) -> None:
    from otio_app.services.without_voiceover_enhanced.unified_timeline_service import (
        UnifiedTimelineError,
    )

    project, ids = _build_chapter_a_project(tmp_path)
    plan = _plan_with_pause_and_closing(ids)
    plan = plan.model_copy(
        update={
            "pause_directives": [
                PauseDirective(
                    after_segment_id=ids["seg"],
                    after_sentence_id=ids["s1"],
                    pause_function="anticipation",
                    duration_class="long",
                )
            ]
        }
    )
    with pytest.raises(
        UnifiedTimelineError, match="nicht mehr unterstützte Pausenverlängerungen"
    ):
        resolve_unified_timeline(project, plan, allow_open_gaps=False, persist=False)


def test_closing_fallback_used_when_primary_file_missing(tmp_path: Path) -> None:
    project, ids = _build_chapter_a_project(tmp_path)
    # Delete primary closing media after inventory/catalog path is recorded.
    primary_path = Path(project.project_root) / "ChapterA" / "close_a.mp4"
    primary_path.unlink()
    plan = _plan_with_pause_and_closing(ids)
    resolved = resolve_unified_timeline(
        project, plan, allow_open_gaps=False, persist=True
    )
    assert not resolved.errors, resolved.errors
    assert any("Fallback" in r for r in resolved.repairs)
    close_shots = [
        s
        for s in resolved.shots
        if s.asset_id == ids["fallback"]
        or str(s.resolved_media_path or "").endswith("fallback_a.mp4")
    ]
    assert close_shots
    export_otio_from_resolved_timeline(
        project, basename="kf_e2e_fallback", resolved=resolved
    )


def test_closing_both_invalid_blocks_export(tmp_path: Path) -> None:
    from otio_app.services.without_voiceover_enhanced.unified_timeline_service import (
        UnifiedTimelineError,
    )

    project, ids = _build_chapter_a_project(tmp_path)
    (Path(project.project_root) / "ChapterA" / "close_a.mp4").unlink()
    (Path(project.project_root) / "ChapterA" / "fallback_a.mp4").unlink()
    plan = _plan_with_pause_and_closing(ids)
    with pytest.raises(UnifiedTimelineError, match="beide"):
        resolve_unified_timeline(
            project, plan, allow_open_gaps=False, persist=True
        )


def test_map_too_short_uses_normal_preroll(tmp_path: Path) -> None:
    project, ids = _build_chapter_a_project(tmp_path)
    short = Path(project.project_root) / "Maps" / "ChapterA.mp4"
    _ffmpeg_color_video(short, duration=4.0, color="gray")
    plan = _plan_with_pause_and_closing(ids)
    resolved = resolve_unified_timeline(
        project, plan, allow_open_gaps=False, persist=True
    )
    assert not resolved.errors, resolved.errors
    assert any("zu kurz" in r for r in resolved.repairs)
    assert not any(
        str(s.editorial_function or "") == "technical_chapter_map_opener"
        for s in resolved.shots
    )


def test_rhythm_and_keyword_sync_strip_pause_directives() -> None:
    payload = {
        "pause_directives": [
            {
                "after_sentence_id": "s1",
                "pause_function": "breath",
                "duration_class": "long",
            }
        ],
        "boundaries": [
            {
                "cut_id": "c0",
                "sentence_id": "s1",
                "position": "start",
                "offset_seconds": 0,
                "alignment": "sentence_boundary",
            },
            {
                "cut_id": "c1",
                "sentence_id": "s1",
                "position": "end",
                "alignment": "sentence_boundary",
            },
        ],
        "slots": [
            {
                "slot_id": "s",
                "local_asset_id": "a",
                "asset_fit": "strong",
            }
        ],
    }
    rhythm = parse_unified_cut_response(payload, "script-v1")
    assert rhythm.pause_directives == []
    # Keyword-sync path also defaults allow_pause_directives=False
    sync = parse_unified_cut_response(payload, "script-v1", allow_pause_directives=False)
    assert sync.pause_directives == []
    assert UNIFIED_CUT_STYLE_RHYTHM and UNIFIED_CUT_STYLE_KEYWORD_SYNC


def _otio_target_urls(otio_path: Path) -> list[str]:
    tl = otio.adapters.read_from_file(str(otio_path))
    urls: list[str] = []
    for track in tl.tracks:
        for clip in track:
            if not isinstance(clip, otio.schema.Clip):
                continue
            media = clip.media_reference
            if media is None:
                continue
            target = getattr(media, "target_url", None)
            if target:
                urls.append(str(target))
    return urls


def test_closing_primary_valid_keeps_primary(tmp_path: Path) -> None:
    project, ids = _build_chapter_a_project(tmp_path)
    plan = _plan_with_pause_and_closing(ids)
    resolved = resolve_unified_timeline(
        project, plan, allow_open_gaps=False, persist=True
    )
    assert not resolved.errors, resolved.errors
    assert not any("Fallback" in r for r in resolved.repairs)
    close_shots = [s for s in resolved.shots if s.asset_id == ids["close"]]
    assert close_shots
    out = export_otio_from_resolved_timeline(
        project, basename="kf_primary_ok", resolved=resolved
    )
    urls = _otio_target_urls(out)
    assert any(u.endswith("close_a.mp4") for u in urls)
    assert not any(u.endswith("fallback_a.mp4") for u in urls)


def test_closing_primary_damaged_uses_fallback_in_otio(tmp_path: Path) -> None:
    """Primary existiert lokal, ist aber technisch unbrauchbar → Fallback im OTIO."""
    project, ids = _build_chapter_a_project(tmp_path)
    primary = Path(project.project_root) / "ChapterA" / "close_a.mp4"
    primary.write_bytes(b"not-a-valid-video-file")
    plan = _plan_with_pause_and_closing(ids)
    assert plan.closing_fallback_asset_fit == "acceptable"
    resolved = resolve_unified_timeline(
        project, plan, allow_open_gaps=False, persist=True
    )
    assert not resolved.errors, resolved.errors
    assert any("Fallback" in r and ids["fallback"] in r for r in resolved.repairs)
    assert any(s.asset_id == ids["fallback"] for s in resolved.shots)
    editorial_close = [
        s
        for s in resolved.shots
        if s.asset_id == ids["close"]
        and not str(s.editorial_function or "").startswith("technical_")
    ]
    assert not editorial_close
    out = export_otio_from_resolved_timeline(
        project, basename="kf_primary_damaged", resolved=resolved
    )
    urls = _otio_target_urls(out)
    assert any("fallback_a.mp4" in u for u in urls)
    assert not any("close_a.mp4" in u for u in urls)


def test_closing_primary_too_short_uses_fallback(tmp_path: Path) -> None:
    project, ids = _build_chapter_a_project(tmp_path)
    primary = Path(project.project_root) / "ChapterA" / "close_a.mp4"
    _ffmpeg_color_video(primary, duration=0.4, color="red")
    plan = _plan_with_pause_and_closing(ids)
    resolved = resolve_unified_timeline(
        project, plan, allow_open_gaps=False, persist=True
    )
    assert not resolved.errors, resolved.errors
    assert any("Fallback" in r for r in resolved.repairs)
    assert any(s.asset_id == ids["fallback"] for s in resolved.shots)
    out = export_otio_from_resolved_timeline(
        project, basename="kf_primary_short", resolved=resolved
    )
    assert any("fallback_a.mp4" in u for u in _otio_target_urls(out))


def test_closing_usage_rule_forces_fallback(tmp_path: Path) -> None:
    project, ids = _build_chapter_a_project(tmp_path)
    save_cut_plan_options(
        project,
        CutPlanOptions(
            cut_plan_mode=CUT_PLAN_MODE_UNIFIED,
            unified_cut_style=UNIFIED_CUT_STYLE_KEYWORD_FLOW,
            shot_min_sec=4.0,
            shot_max_sec=9.0,
            voiceover_preroll_sec=1.0,
            voiceover_postroll_sec=2.0,
            video_head_trim_sec=0.0,
            still_image_style_enabled=False,
            max_asset_usage=1,
        ),
    )
    plan = _plan_with_pause_and_closing(ids)
    # Erster Slot verbraucht Primary Closing → Usage-Regel zwingt Fallback.
    plan.slots[0].local_asset_id = ids["close"]
    resolved = resolve_unified_timeline(
        project, plan, allow_open_gaps=False, persist=True
    )
    assert not resolved.errors, resolved.errors
    assert any("Fallback" in r for r in resolved.repairs)
    editorial = [
        s
        for s in resolved.shots
        if not str(s.editorial_function or "").startswith("technical_")
        and s.asset_id
    ]
    close_uses = [s for s in editorial if s.asset_id == ids["close"]]
    assert len(close_uses) == 1
    assert any(s.asset_id == ids["fallback"] for s in editorial)


def test_closing_fallback_damaged_blocks(tmp_path: Path) -> None:
    from otio_app.services.without_voiceover_enhanced.unified_timeline_service import (
        UnifiedTimelineError,
    )

    project, ids = _build_chapter_a_project(tmp_path)
    primary = Path(project.project_root) / "ChapterA" / "close_a.mp4"
    primary.write_bytes(b"broken-primary")
    fallback = Path(project.project_root) / "ChapterA" / "fallback_a.mp4"
    fallback.write_bytes(b"broken-fallback")
    plan = _plan_with_pause_and_closing(ids)
    with pytest.raises(UnifiedTimelineError):
        resolve_unified_timeline(
            project, plan, allow_open_gaps=False, persist=True
        )


def test_closing_weak_fallback_fit_blocks(tmp_path: Path) -> None:
    from otio_app.services.without_voiceover_enhanced.unified_timeline_service import (
        UnifiedTimelineError,
    )

    project, ids = _build_chapter_a_project(tmp_path)
    plan = _plan_with_pause_and_closing(ids)
    plan.closing_fallback_asset_fit = "weak"
    with pytest.raises(UnifiedTimelineError, match="Fallback-Fit|unzulässig|weak"):
        resolve_unified_timeline(
            project, plan, allow_open_gaps=False, persist=True
        )


def test_persistent_local_and_portable_otio_refs(tmp_path: Path) -> None:
    from otio_app.services.without_voiceover_enhanced.otio_export_service import (
        export_portable_otio_package,
    )

    project, ids = _build_chapter_a_project(tmp_path)
    plan = _plan_with_pause_and_closing(ids)
    resolved = resolve_unified_timeline(
        project, plan, allow_open_gaps=False, persist=True
    )
    assert not resolved.errors, resolved.errors
    local = export_otio_from_resolved_timeline(
        project, basename="kf_persist_local", resolved=resolved
    )
    package = export_portable_otio_package(
        project, basename="kf_persist_portable", allow_errors=False
    )
    portable_otio = package / "timeline.otio"
    assert local.is_file()
    assert portable_otio.is_file()
    for url in _otio_target_urls(local):
        assert not url.lower().startswith("http")
        assert "/tmp/kf_" not in url
        assert Path(url).is_file()
    package_resolved = package.resolve()
    for url in _otio_target_urls(portable_otio):
        assert not url.lower().startswith("http")
        assert "/tmp/kf_" not in url
        target = Path(url)
        if not target.is_absolute():
            target = (package / url).resolve()
        else:
            target = target.resolve()
        assert target.is_file()
        assert str(target).startswith(str(package_resolved))
