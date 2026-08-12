#!/usr/bin/env python3
"""TECHNICAL HARNESS / STUB TEST — not a real LLM or product UI test.

Uses a mocked llm_callable and a synthetic voiced chapter fixture to exercise
routing, continuous-word-flow input, and resolve plumbing for Keyword Flow Free.

Do NOT treat output as a real LLM cut or real Streamlit product test.
Manual product comparison is performed by the user.
"""

from __future__ import annotations

import json
import subprocess
import wave
from pathlib import Path

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
    UNIFIED_CUT_STYLE_KEYWORD_FLOW_FREE,
    CutPlanOptions,
    save_cut_plan_options,
)
from otio_app.services.without_voiceover_enhanced.cut_plan_service import (
    generate_unified_cut_for_folder,
)
from otio_app.services.without_voiceover_enhanced.io_utils import write_json
from otio_app.services.without_voiceover_enhanced.keyword_flow_free_input import (
    build_continuous_word_flow_json_for_segments,
)
from otio_app.services.without_voiceover_enhanced.keyword_flow_free_prompt import (
    KEYWORD_FLOW_FREE_MARKER,
    build_keyword_flow_free_prompt,
)
from otio_app.services.without_voiceover_enhanced.models import (
    EnhancedScriptDocument,
    ScriptSegment,
    SegmentAlignment,
    SegmentAlignmentsDocument,
    SegmentTiming,
    SegmentTimingsDocument,
    SentenceTiming,
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
from otio_app.services.without_voiceover_enhanced.script_prompts import (
    build_keyword_flow_unified_cut_prompt,
)
from otio_app.services.without_voiceover_enhanced.sentence_timing_prompt import (
    build_sentence_timings_json_for_segments,
)
from otio_app.services.without_voiceover_enhanced.unified_cut_plan import (
    unified_to_rough,
)
from otio_app.services.without_voiceover_enhanced.unified_timeline_service import (
    resolve_unified_timeline,
)

OUT = Path("/opt/cursor/artifacts/reports")
OUT.mkdir(parents=True, exist_ok=True)
ROOT = Path("/tmp/kff_smoke_project")


def _write_silent_wav(path: Path, duration_sec: float, rate: int = 16000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = max(1, int(duration_sec * rate))
    with wave.open(str(path), "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * frames)


def _ffmpeg_color_video(path: Path, *, duration: float, color: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s=320x240:d={duration}",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


def _save_inventory(project: Project, folder: str, media_files: list[Path]) -> dict[str, str]:
    assets: list[AssetMediaAnalysis] = []
    ids: dict[str, str] = {}
    for media in media_files:
        rel = f"{folder}/{media.name}"
        asset_id = enhanced_asset_id_for_path(project, media, folder_name=folder)
        assets.append(
            AssetMediaAnalysis(
                path=rel,
                description=f"{folder} {media.stem}",
                asset_id=asset_id,
                media_type="video",
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


def _write_alignment(
    project: Project,
    *,
    seg_id: str,
    words: list[tuple[str, float, float]],
    sentences: list[SentenceTiming],
    audio_path: str,
    audio_duration: float,
) -> None:
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


def build_project() -> tuple[Project, dict[str, str]]:
    if ROOT.exists():
        import shutil

        shutil.rmtree(ROOT)
    work = ROOT / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    (ROOT / "Santorini").mkdir()
    (ROOT / "Maps").mkdir()
    project = Project(
        id="kff-smoke",
        name="KFF Smoke",
        project_root=str(ROOT.resolve()),
        work_dir=str(work.resolve()),
        language="de",
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        fps=25.0,
        width=1920,
        height=1080,
        asset_subdir_names=["Santorini", "Maps"],
        selected_asset_subdirs=["Santorini", "Maps"],
    )
    clips = {}
    for name, color in (
        ("island", "blue"),
        ("volcano", "red"),
        ("houses", "white"),
        ("sea", "navy"),
        ("close", "green"),
        ("fallback", "yellow"),
    ):
        path = ROOT / "Santorini" / f"{name}.mp4"
        _ffmpeg_color_video(path, duration=30.0, color=color)
        clips[name] = path
    ids = _save_inventory(project, "Santorini", list(clips.values()))
    map_path = ROOT / "Maps" / "Santorini.mp4"
    _ffmpeg_color_video(map_path, duration=12.0, color="gray")

    wav = work / "audio" / "Santorini_segment_001.wav"
    audio_dur = 18.0
    _write_silent_wav(wav, audio_dur)
    seg_id = "Santorini_segment_001"
    narration = (
        "Die Insel wurde über Jahrtausende von vulkanischen Kräften geprägt. "
        "Heute liegen die weißen Dörfer hoch über der Caldera."
    )
    save_script_draft(
        project,
        EnhancedScriptDocument(
            script_version="script-v1",
            narration_full=narration,
            segments=[
                ScriptSegment(
                    segment_id=seg_id,
                    text=narration,
                    sequence_index=1,
                    folder_name="Santorini",
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
            text="Die Insel wurde über Jahrtausende von vulkanischen Kräften geprägt.",
            start_seconds=0.0,
            end_seconds=9.0,
            duration_seconds=9.0,
        ),
        SentenceTiming(
            sentence_id=s2,
            segment_id=seg_id,
            text="Heute liegen die weißen Dörfer hoch über der Caldera.",
            start_seconds=9.2,
            end_seconds=18.0,
            duration_seconds=8.8,
        ),
    ]
    words = [
        ("Die", 0.0, 0.25),
        ("Insel", 0.3, 0.7),
        ("wurde", 0.8, 1.1),
        ("über", 1.2, 1.5),
        ("Jahrtausende", 1.6, 2.4),
        ("von", 2.5, 2.7),
        ("vulkanischen", 2.8, 3.6),
        ("Kräften", 3.7, 4.3),
        ("geprägt", 4.4, 5.2),
        ("Heute", 9.2, 9.5),
        ("liegen", 9.6, 10.0),
        ("die", 10.1, 10.25),
        ("weißen", 10.4, 11.0),
        ("Dörfer", 11.1, 11.7),
        ("hoch", 11.8, 12.1),
        ("über", 12.2, 12.5),
        ("der", 12.6, 12.8),
        ("Caldera", 12.9, 14.0),
    ]
    _write_alignment(
        project,
        seg_id=seg_id,
        words=words,
        sentences=sentences,
        audio_path=str(wav),
        audio_duration=audio_dur,
    )
    ids.update({"s1": s1, "s2": s2, "seg": seg_id})
    return project, ids


def _free_plan_json(ids: dict[str, str]) -> str:
    """Editorially free plan: multi-shot in s1, visual lag into s2, cut on weißen."""
    s1, s2 = ids["s1"], ids["s2"]
    return json.dumps(
        {
            "voiceover_preroll_sec": None,
            "voiceover_postroll_sec": None,
            "closing_fallback_asset_id": ids["fallback"],
            "closing_fallback_asset_fit": "acceptable",
            "closing_fallback_asset_fit_reason": "reserve closer",
            "closing_fallback_visual_intent": "caldera hold",
            "pause_directives": [],
            "boundaries": [
                {
                    "cut_id": "Santorini_cut_000",
                    "sentence_id": s1,
                    "position": "start",
                    "offset_seconds": 0,
                    "alignment": "sentence_boundary",
                },
                {
                    "cut_id": "Santorini_cut_001",
                    "sentence_id": s1,
                    "position": "middle",
                    "offset_seconds": 2.8,
                    "alignment": "mid_sentence",
                },
                {
                    "cut_id": "Santorini_cut_002",
                    "sentence_id": s2,
                    "position": "middle",
                    "offset_seconds": 1.2,
                    "alignment": "mid_sentence",
                },
                {
                    "cut_id": "Santorini_cut_003",
                    "sentence_id": s2,
                    "position": "end",
                    "offset_seconds": None,
                    "alignment": "sentence_boundary",
                },
            ],
            "slots": [
                {
                    "slot_id": "Santorini_slot_001",
                    "local_asset_id": ids["island"],
                    "asset_fit": "strong",
                    "asset_fit_reason": "island establishing",
                    "visual_intent": "island / caldera overview",
                    "narrative_function": "chapter_open",
                    "coverage_gap_id": None,
                    "covered_sentence_ids": [s1],
                },
                {
                    "slot_id": "Santorini_slot_002",
                    "local_asset_id": ids["volcano"],
                    "asset_fit": "strong",
                    "asset_fit_reason": "volcanic forces",
                    "visual_intent": "volcanic rock / forces",
                    "narrative_function": "evidence",
                    "coverage_gap_id": None,
                    "covered_sentence_ids": [s1, s2],
                },
                {
                    "slot_id": "Santorini_slot_003",
                    "local_asset_id": ids["houses"],
                    "asset_fit": "strong",
                    "asset_fit_reason": "white villages",
                    "visual_intent": "white villages above caldera",
                    "narrative_function": "chapter_close",
                    "coverage_gap_id": None,
                    "covered_sentence_ids": [s2],
                },
            ],
        },
        ensure_ascii=False,
    )


def _kf_plan_json(ids: dict[str, str]) -> str:
    """Classic-ish Keyword Flow: one shot per sentence boundary style."""
    s1, s2 = ids["s1"], ids["s2"]
    return json.dumps(
        {
            "voiceover_preroll_sec": None,
            "voiceover_postroll_sec": None,
            "closing_fallback_asset_id": ids["fallback"],
            "closing_fallback_asset_fit": "acceptable",
            "closing_fallback_asset_fit_reason": "reserve closer",
            "closing_fallback_visual_intent": "caldera hold",
            "pause_directives": [],
            "boundaries": [
                {
                    "cut_id": "Santorini_cut_000",
                    "sentence_id": s1,
                    "position": "start",
                    "offset_seconds": 0,
                    "alignment": "sentence_boundary",
                },
                {
                    "cut_id": "Santorini_cut_001",
                    "sentence_id": s1,
                    "position": "end",
                    "offset_seconds": None,
                    "alignment": "sentence_boundary",
                },
                {
                    "cut_id": "Santorini_cut_002",
                    "sentence_id": s2,
                    "position": "end",
                    "offset_seconds": None,
                    "alignment": "sentence_boundary",
                },
            ],
            "slots": [
                {
                    "slot_id": "Santorini_slot_001",
                    "local_asset_id": ids["island"],
                    "asset_fit": "strong",
                    "asset_fit_reason": "sentence 1 island",
                    "visual_intent": "island",
                    "narrative_function": "chapter_open",
                    "coverage_gap_id": None,
                    "covered_sentence_ids": [s1],
                },
                {
                    "slot_id": "Santorini_slot_002",
                    "local_asset_id": ids["houses"],
                    "asset_fit": "strong",
                    "asset_fit_reason": "sentence 2 villages",
                    "visual_intent": "white villages",
                    "narrative_function": "chapter_close",
                    "coverage_gap_id": None,
                    "covered_sentence_ids": [s2],
                },
            ],
        },
        ensure_ascii=False,
    )


def _analyze_plan(plan, label: str) -> dict:
    from collections import Counter

    sids = [b.sentence_id for b in plan.boundaries]
    mid = [b for b in plan.boundaries if str(b.alignment) == "mid_sentence"]
    cover_counts: Counter[str] = Counter()
    for slot in plan.slots:
        for sid in list(slot.covered_sentence_ids or []):
            cover_counts[str(sid)] += 1
    multi_shot_sentence = any(count >= 2 for count in cover_counts.values())
    # Cross-boundary shot: a slot explicitly covers more than one sentence_id
    # (visual lag / hold across the spoken sentence change).
    cross = any(len(list(s.covered_sentence_ids or [])) >= 2 for s in plan.slots)
    # Cut several words into new sentence: mid_sentence on s2 with offset > 0.5s
    late_cut = any(
        str(b.sentence_id).endswith("s002")
        and str(b.alignment) == "mid_sentence"
        and float(b.offset_seconds or 0) > 0.5
        for b in plan.boundaries
    )
    _rough, coverage = unified_to_rough(plan)
    return {
        "label": label,
        "slot_count": len(plan.slots),
        "boundary_sentence_ids": sids,
        "mid_sentence_offsets": [
            {"sentence_id": b.sentence_id, "offset_seconds": b.offset_seconds}
            for b in mid
        ],
        "sentence_cover_counts": dict(cover_counts),
        "multi_shot_in_sentence": multi_shot_sentence,
        "shot_crosses_sentence_boundary": cross,
        "cut_several_words_into_new_sentence": late_cut,
        "gap_count": len(coverage.gaps),
        "slots": [
            {
                "slot_id": s.slot_id,
                "asset": s.local_asset_id,
                "covered": list(s.covered_sentence_ids or []),
                "fit": s.asset_fit,
            }
            for s in plan.slots
        ],
    }


def main() -> int:
    project, ids = build_project()
    seg_ids = [ids["seg"]]
    continuous = build_continuous_word_flow_json_for_segments(
        project, segment_ids=seg_ids
    )
    (OUT / "continuous_word_flow_example.json").write_text(
        continuous, encoding="utf-8"
    )
    free_prompt = build_keyword_flow_free_prompt(
        locked_script_json="{}",
        segment_timings_json="{}",
        local_assets_json="[]",
        style_profile_text="docu",
        dramaturgy_text="calm",
        continuous_word_flow_json=continuous,
        folder_name="Santorini",
        folder_slug="Santorini",
        shot_constraints_text="SHOT CONSTRAINTS\n- shot_min 3 / shot_max 9\n",
    )
    (OUT / "keyword_flow_free_prompt.txt").write_text(free_prompt, encoding="utf-8")
    assert KEYWORD_FLOW_FREE_MARKER in free_prompt

    save_cut_plan_options(
        project,
        CutPlanOptions(
            cut_plan_mode=CUT_PLAN_MODE_UNIFIED,
            unified_cut_style=UNIFIED_CUT_STYLE_KEYWORD_FLOW_FREE,
            shot_min_sec=3.0,
            shot_max_sec=9.0,
            voiceover_preroll_sec=1.0,
            voiceover_postroll_sec=2.0,
            video_head_trim_sec=0.0,
            still_image_style_enabled=False,
        ),
    )

    captured: dict[str, str] = {}

    def free_llm(*, prompt: str, model: str, images=None):  # noqa: ANN001
        captured["free_prompt"] = prompt
        assert KEYWORD_FLOW_FREE_MARKER in prompt
        assert "CONTINUOUS WORD FLOW" in prompt
        assert "KEYWORD FLOW MARKER" not in prompt
        return _free_plan_json(ids)

    free_result = generate_unified_cut_for_folder(
        project,
        folder_name="Santorini",
        provider="anthropic",
        model="test",
        llm_callable=free_llm,
    )
    free_analysis = _analyze_plan(free_result.plan, "keyword_flow_free")
    free_resolved = resolve_unified_timeline(
        project, free_result.plan, allow_open_gaps=False, persist=True
    )

    # Keyword Flow comparison on same project/input.
    save_cut_plan_options(
        project,
        CutPlanOptions(
            cut_plan_mode=CUT_PLAN_MODE_UNIFIED,
            unified_cut_style=UNIFIED_CUT_STYLE_KEYWORD_FLOW,
            shot_min_sec=3.0,
            shot_max_sec=9.0,
            voiceover_preroll_sec=1.0,
            voiceover_postroll_sec=2.0,
            video_head_trim_sec=0.0,
            still_image_style_enabled=False,
        ),
    )

    def kf_llm(*, prompt: str, model: str, images=None):  # noqa: ANN001
        captured["kf_prompt"] = prompt
        assert "KEYWORD FLOW MARKER" in prompt
        assert KEYWORD_FLOW_FREE_MARKER not in prompt
        return _kf_plan_json(ids)

    kf_result = generate_unified_cut_for_folder(
        project,
        folder_name="Santorini",
        provider="anthropic",
        model="test",
        llm_callable=kf_llm,
    )
    kf_analysis = _analyze_plan(kf_result.plan, "keyword_flow")
    kf_resolved = resolve_unified_timeline(
        project, kf_result.plan, allow_open_gaps=False, persist=False
    )

    # Gap comparison: Free holds across sentence start → no gap; forced gap still honest.
    report = {
        "auftrag": "WITHOUT-VO-ENHANCED-KEYWORD-FLOW-FREE-001",
        "continuous_word_flow_path": str(OUT / "continuous_word_flow_example.json"),
        "free_prompt_path": str(OUT / "keyword_flow_free_prompt.txt"),
        "keyword_flow_free": {
            **free_analysis,
            "resolve_errors": list(free_resolved.errors),
            "resolve_shot_count": len(free_resolved.shots),
            "proofs": {
                "multi_shot_sentence": free_analysis["multi_shot_in_sentence"],
                "cross_sentence_shot": free_analysis["shot_crosses_sentence_boundary"],
                "cut_after_new_sentence_words": free_analysis[
                    "cut_several_words_into_new_sentence"
                ],
            },
        },
        "keyword_flow": {
            **kf_analysis,
            "resolve_errors": list(kf_resolved.errors),
            "resolve_shot_count": len(kf_resolved.shots),
        },
        "coverage_gap_comparison": {
            "free_gap_count": free_analysis["gap_count"],
            "kf_gap_count": kf_analysis["gap_count"],
            "note": (
                "Free holds volcano shot from s1 across sentence start into s2 "
                "until 'weißen' — no gap from sentence change."
            ),
        },
        "prompt_isolation": {
            "free_has_free_marker": KEYWORD_FLOW_FREE_MARKER
            in captured.get("free_prompt", ""),
            "free_lacks_kf_marker": "KEYWORD FLOW MARKER"
            not in captured.get("free_prompt", ""),
            "kf_has_kf_marker": "KEYWORD FLOW MARKER" in captured.get("kf_prompt", ""),
            "kf_lacks_free_marker": KEYWORD_FLOW_FREE_MARKER
            not in captured.get("kf_prompt", ""),
        },
    }
    ok = (
        free_analysis["multi_shot_in_sentence"]
        and free_analysis["shot_crosses_sentence_boundary"]
        and free_analysis["cut_several_words_into_new_sentence"]
        and not free_resolved.errors
        and report["prompt_isolation"]["free_has_free_marker"]
        and report["prompt_isolation"]["kf_has_kf_marker"]
    )
    report["ok"] = ok
    path = OUT / "keyword_flow_free_smoke.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
