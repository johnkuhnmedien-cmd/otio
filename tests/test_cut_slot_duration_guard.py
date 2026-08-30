"""LLM-Cut: zu kurze Motion-Videos werden nicht als strong behalten."""

from __future__ import annotations

import json
import wave
from pathlib import Path

from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.services.voiceover_generation.dramaturgy_service import (
    save_confirmed_dramaturgy,
)
from otio_app.services.voiceover_generation.models import (
    DramaturgyFolderEntry,
    DramaturgyPlan,
)
from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
    CutPlanOptions,
    LLM_ASSET_DURATION_SAFETY_SEC,
    save_cut_plan_options,
)
from otio_app.services.without_voiceover_enhanced.cut_plan_service import (
    UNIFIED_CUT_PARSE_ATTEMPTS,
    _unified_cut_parse_repair_instruction,
    generate_unified_cut_for_folder,
)
from otio_app.services.without_voiceover_enhanced.cut_slot_duration_guard import (
    TOO_SHORT_ERROR_PREFIX,
    catalog_from_prompt_assets,
    collect_too_short_motion_assignments,
    demote_too_short_motion_slots,
    is_still_asset,
    planning_usable_seconds,
    sentence_index_from_timing_rows,
    stamp_slot_target_durations,
)
from otio_app.services.without_voiceover_enhanced.io_utils import write_json
from otio_app.services.without_voiceover_enhanced.models import (
    CutBoundary,
    CutSlot,
    EnhancedScriptDocument,
    ScriptSegment,
    SegmentAlignment,
    SegmentAlignmentsDocument,
    SegmentTiming,
    SegmentTimingsDocument,
    SentenceTiming,
    UnifiedCutPlanDocument,
    VisualIntent,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    segment_alignments_path,
    segment_timings_path,
)
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    lock_script,
    save_script_draft,
)
from otio_app.services.without_voiceover_enhanced.unified_cut_plan import unified_to_rough


def _boundary(
    cut_id: str,
    sentence_id: str,
    *,
    position: str = "start",
) -> CutBoundary:
    return CutBoundary(
        cut_id=cut_id,
        sentence_id=sentence_id,
        position=position,  # type: ignore[arg-type]
        alignment="sentence_boundary",
    )


def _slot(slot_id: str, asset_id: str, *, fit: str = "strong") -> CutSlot:
    return CutSlot(
        slot_id=slot_id,
        local_asset_id=asset_id,
        asset_fit=fit,  # type: ignore[arg-type]
        asset_fit_reason="match",
        visual_intent="valley",
        narrative_function="orientation",
    )


def _two_slot_plan(*, last_asset: str = "short_clip") -> UnifiedCutPlanDocument:
    return UnifiedCutPlanDocument(
        script_version="v1",
        boundaries=[
            _boundary("cut_000", "Canyon_segment_001__s001", position="start"),
            _boundary("cut_001", "Canyon_segment_001__s001", position="end"),
            _boundary("cut_002", "Canyon_segment_001__s002", position="end"),
        ],
        slots=[
            _slot("slot_001", "long_clip"),
            _slot("slot_002", last_asset),
        ],
    )


def _sentence_index() -> dict[str, SentenceTiming]:
    return sentence_index_from_timing_rows(
        [
            {
                "sentence_id": "Canyon_segment_001__s001",
                "segment_id": "Canyon_segment_001",
                "start_seconds": 0.0,
                "end_seconds": 8.0,
            },
            {
                "sentence_id": "Canyon_segment_001__s002",
                "segment_id": "Canyon_segment_001",
                "start_seconds": 8.0,
                "end_seconds": 16.0,
            },
        ]
    )


def _catalog() -> dict[str, dict]:
    return catalog_from_prompt_assets(
        [
            {
                "local_asset_id": "long_clip",
                "path": "Canyon/long.mp4",
                "media_type": "video",
                "duration_seconds": 30.0,
            },
            {
                "local_asset_id": "short_clip",
                "path": "Canyon/short.mp4",
                "media_type": "video",
                "duration_seconds": 6.0,
            },
            {
                "local_asset_id": "still_photo",
                "path": "Canyon/still.jpg",
                "media_type": "photo",
                "duration_seconds": 0.0,
            },
        ]
    )


def test_planning_usable_subtracts_trim_and_safety() -> None:
    usable = planning_usable_seconds(
        {"duration_seconds": 10.0, "usable_in_s": 2.0, "media_type": "video"},
        head_trim_sec=1.0,
        safety_sec=LLM_ASSET_DURATION_SAFETY_SEC,
    )
    assert usable == 7.0


def test_stills_are_not_motion_constrained() -> None:
    assert is_still_asset({"media_type": "photo", "path": "a.jpg"})
    assert planning_usable_seconds(
        {"media_type": "photo", "duration_seconds": 1.0}
    ) is None


def test_short_last_slot_video_is_collected_as_too_short() -> None:
    hits = collect_too_short_motion_assignments(
        _two_slot_plan(),
        _catalog(),
        sentence_index=_sentence_index(),
        head_trim_sec=1.0,
        short_tolerance_sec=1.0,
        preroll_sec=1.0,
        postroll_sec=5.0,
    )
    assert len(hits) == 1
    assert hits[0].slot_id == "slot_002"
    assert hits[0].asset_id == "short_clip"
    assert hits[0].need_seconds == 13.0  # 8s last sentence + 5s Nachlauf
    assert TOO_SHORT_ERROR_PREFIX in hits[0].reason


def test_long_enough_last_slot_is_kept() -> None:
    hits = collect_too_short_motion_assignments(
        _two_slot_plan(last_asset="long_clip"),
        _catalog(),
        sentence_index=_sentence_index(),
        head_trim_sec=1.0,
        short_tolerance_sec=1.0,
        preroll_sec=1.0,
        postroll_sec=5.0,
    )
    assert hits == []


def test_still_on_last_slot_is_not_too_short() -> None:
    hits = collect_too_short_motion_assignments(
        _two_slot_plan(last_asset="still_photo"),
        _catalog(),
        sentence_index=_sentence_index(),
        head_trim_sec=1.0,
        short_tolerance_sec=1.0,
        preroll_sec=1.0,
        postroll_sec=5.0,
    )
    assert hits == []


def test_without_sentence_timings_does_not_false_demote() -> None:
    hits = collect_too_short_motion_assignments(
        _two_slot_plan(),
        _catalog(),
        sentence_index={},
        head_trim_sec=1.0,
        short_tolerance_sec=1.0,
        preroll_sec=1.0,
        postroll_sec=5.0,
    )
    assert hits == []


def test_demote_too_short_becomes_coverage_gap() -> None:
    plan = _two_slot_plan()
    hits = collect_too_short_motion_assignments(
        plan,
        _catalog(),
        sentence_index=_sentence_index(),
        head_trim_sec=1.0,
        short_tolerance_sec=1.0,
        preroll_sec=1.0,
        postroll_sec=5.0,
    )
    updated, notes = demote_too_short_motion_slots(plan, hits)
    assert notes == ["slot_002"]
    assert updated.slots[0].local_asset_id == "long_clip"
    assert updated.slots[1].local_asset_id is None
    assert updated.slots[1].asset_fit == "none"
    assert updated.slots[1].coverage_gap_id
    assert updated.slots[1].target_duration_seconds == 13.0
    _, coverage = unified_to_rough(updated)
    assert any(gap.gap_id == updated.slots[1].coverage_gap_id for gap in coverage.gaps)
    assert any(
        gap.target_duration_seconds == 13.0 for gap in coverage.gaps
    )


def test_stamp_last_slot_includes_postroll() -> None:
    stamped = stamp_slot_target_durations(
        _two_slot_plan(),
        sentence_index=_sentence_index(),
        preroll_sec=1.0,
        postroll_sec=5.0,
    )
    assert stamped.slots[0].target_duration_seconds == 9.0  # 8s + 1s Vorlauf
    assert stamped.slots[1].target_duration_seconds == 13.0


def test_repair_instruction_explains_short_motion_clip() -> None:
    text = _unified_cut_parse_repair_instruction(
        f"{TOO_SHORT_ERROR_PREFIX}: slot_010 asset x planning_usable=4.00s < need=12.00s"
    )
    assert "REPAIR" in text
    assert "planning_usable" in text
    assert "Nachlauf" in text
    assert UNIFIED_CUT_PARSE_ATTEMPTS == 2


def _project(tmp_path: Path) -> Project:
    root = tmp_path / "proj"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    (root / "Canyon").mkdir(exist_ok=True)
    return Project(
        name="ShortVideoCut",
        project_root=str(root),
        work_dir=str(work),
        language="de",
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        fps=25.0,
        selected_asset_subdirs=["Canyon"],
        asset_subdir_names=["Canyon"],
    )


def _lock_time_and_align(project: Project) -> str:
    save_confirmed_dramaturgy(
        project,
        DramaturgyPlan(
            project_id=project.id,
            project_title="Test",
            recommended_folder_order=[
                DramaturgyFolderEntry(
                    folder_name="Canyon",
                    order_index=0,
                    enabled=True,
                    dramaturgy_role="development",
                )
            ],
        ),
    )
    save_script_draft(
        project,
        EnhancedScriptDocument(
            narration_full="Canyon text one. Canyon text two.",
            segments=[
                ScriptSegment(
                    segment_id="Canyon_segment_001",
                    text="Canyon text one. Canyon text two.",
                    sequence_index=1,
                    folder_name="Canyon",
                    folder_order_index=0,
                    visual_intent_ids=["Canyon_intent_001"],
                ),
            ],
            visual_intents=[
                VisualIntent(
                    intent_id="Canyon_intent_001",
                    description="Canyon wide",
                    folder_name="Canyon",
                ),
            ],
        ),
    )
    locked = lock_script(project)
    audio = Path(project.work_dir) / "audio" / "canyon.wav"
    audio.parent.mkdir(parents=True, exist_ok=True)
    sample_rate = 16000
    n_frames = int(16.0 * sample_rate)
    with wave.open(str(audio), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(b"\x00\x00" * n_frames)
    write_json(
        segment_timings_path(project),
        SegmentTimingsDocument(
            script_version=locked.script_version,
            segments=[
                SegmentTiming(
                    segment_id="Canyon_segment_001",
                    script_version=locked.script_version,
                    audio_path=str(audio),
                    duration_seconds=16.0,
                ),
            ],
        ),
    )
    write_json(
        segment_alignments_path(project),
        SegmentAlignmentsDocument(
            script_version=locked.script_version,
            segments=[
                SegmentAlignment(
                    segment_id="Canyon_segment_001",
                    script_version=locked.script_version,
                    audio_path=str(audio),
                    audio_duration_seconds=16.0,
                    tts_text="Canyon text one. Canyon text two.",
                    timestamps_path="",
                    sentences=[
                        SentenceTiming(
                            sentence_id="Canyon_segment_001__s001",
                            segment_id="Canyon_segment_001",
                            text="Canyon text one.",
                            start_seconds=0.0,
                            end_seconds=8.0,
                            duration_seconds=8.0,
                        ),
                        SentenceTiming(
                            sentence_id="Canyon_segment_001__s002",
                            segment_id="Canyon_segment_001",
                            text="Canyon text two.",
                            start_seconds=8.0,
                            end_seconds=16.0,
                            duration_seconds=8.0,
                        ),
                    ],
                )
            ],
        ),
    )
    save_cut_plan_options(
        project,
        CutPlanOptions(
            shot_min_sec=3.0,
            shot_max_sec=12.0,
            video_head_trim_sec=1.0,
            short_asset_tolerance_sec=1.0,
            voiceover_preroll_sec=1.0,
            voiceover_postroll_sec=5.0,
        ),
    )
    return locked.script_version


def _cut_payload(*, last_asset: str) -> dict:
    return {
        "pause_directives": [],
        "boundaries": [
            {
                "cut_id": "cut_000",
                "sentence_id": "Canyon_segment_001__s001",
                "position": "start",
                "alignment": "sentence_boundary",
            },
            {
                "cut_id": "cut_001",
                "sentence_id": "Canyon_segment_001__s001",
                "position": "end",
                "alignment": "sentence_boundary",
            },
            {
                "cut_id": "cut_002",
                "sentence_id": "Canyon_segment_001__s002",
                "position": "end",
                "alignment": "sentence_boundary",
            },
        ],
        "slots": [
            {
                "slot_id": "slot_001",
                "local_asset_id": "long_clip",
                "asset_fit": "strong",
                "asset_fit_reason": "establishing",
                "visual_intent": "canyon wide",
                "narrative_function": "chapter_open",
            },
            {
                "slot_id": "slot_002",
                "local_asset_id": last_asset,
                "asset_fit": "strong",
                "asset_fit_reason": "closing",
                "visual_intent": "canyon detail",
                "narrative_function": "chapter_close",
            },
        ],
    }


def _assets() -> list[dict]:
    return [
        {
            "asset_id": "long_clip",
            "local_asset_id": "long_clip",
            "path": "Canyon/long.mp4",
            "duration_seconds": 30.0,
            "media_type": "video",
        },
        {
            "asset_id": "short_clip",
            "local_asset_id": "short_clip",
            "path": "Canyon/short.mp4",
            "duration_seconds": 6.0,
            "media_type": "video",
        },
    ]


def test_generate_unified_cut_retries_then_keeps_longer_clip(
    tmp_path: Path, monkeypatch
) -> None:
    project = _project(tmp_path)
    _lock_time_and_align(project)
    prompts: list[str] = []
    calls = {"n": 0}

    def fake_llm(*, prompt: str, model: str, images=None):  # noqa: ANN001
        calls["n"] += 1
        prompts.append(prompt)
        if calls["n"] == 1:
            return json.dumps(_cut_payload(last_asset="short_clip"))
        assert "REPAIR" in prompt
        assert "planning_usable" in prompt
        return json.dumps(_cut_payload(last_asset="long_clip"))

    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.cut_plan_service._local_assets_payload",
        lambda *args, **kwargs: _assets(),
    )
    result = generate_unified_cut_for_folder(
        project, "Canyon", llm_callable=fake_llm
    )
    assert result.status == "PASS", result.error
    assert calls["n"] == 2
    assert result.plan is not None
    last = result.plan.slots[-1]
    assert last.local_asset_id == "long_clip"
    assert last.asset_fit == "strong"


def test_generate_unified_cut_demotes_short_clip_after_retry(
    tmp_path: Path, monkeypatch
) -> None:
    project = _project(tmp_path)
    _lock_time_and_align(project)
    calls = {"n": 0}

    def fake_llm(*, prompt: str, model: str, images=None):  # noqa: ANN001
        calls["n"] += 1
        return json.dumps(_cut_payload(last_asset="short_clip"))

    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.cut_plan_service._local_assets_payload",
        lambda *args, **kwargs: _assets(),
    )
    result = generate_unified_cut_for_folder(
        project, "Canyon", llm_callable=fake_llm
    )
    assert result.status == "PASS", result.error
    assert calls["n"] == 2
    assert result.plan is not None
    last = result.plan.slots[-1]
    assert last.local_asset_id is None
    assert last.asset_fit == "none"
    assert last.coverage_gap_id
    assert TOO_SHORT_ERROR_PREFIX in (last.asset_fit_reason or "")
    assert result.gap_count >= 1
