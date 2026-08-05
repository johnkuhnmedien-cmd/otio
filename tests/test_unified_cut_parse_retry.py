"""Automatischer Parse-Retry bei kaputtem Unified-Cut-JSON (slots/boundaries)."""

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
from otio_app.services.without_voiceover_enhanced.cut_plan_service import (
    UNIFIED_CUT_PARSE_ATTEMPTS,
    _unified_cut_parse_repair_instruction,
    generate_unified_cut_for_folder,
)
from otio_app.services.without_voiceover_enhanced.io_utils import write_json
from otio_app.services.without_voiceover_enhanced.models import (
    EnhancedScriptDocument,
    ScriptSegment,
    SegmentTiming,
    SegmentTimingsDocument,
    VisualIntent,
)
from otio_app.services.without_voiceover_enhanced.paths import segment_timings_path
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    lock_script,
    require_locked_script,
    save_script_draft,
)


def _project(tmp_path: Path) -> Project:
    root = tmp_path / "proj"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    (root / "Canyon").mkdir(exist_ok=True)
    return Project(
        name="ParseRetry",
        project_root=str(root),
        work_dir=str(work),
        language="de",
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        fps=25.0,
        selected_asset_subdirs=["Canyon"],
        asset_subdir_names=["Canyon"],
    )


def _lock_and_time(project: Project) -> str:
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
    n_frames = int(2.0 * sample_rate)
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
                    duration_seconds=2.0,
                ),
            ],
        ),
    )
    return locked.script_version


def _valid_payload() -> dict:
    return {
        "pause_directives": [],
        "boundaries": [
            {
                "cut_id": "cut_000",
                "sentence_id": "Canyon_segment_001__s001",
                "position": "start",
                "offset_seconds": None,
                "alignment": "sentence_boundary",
            },
            {
                "cut_id": "cut_001",
                "sentence_id": "Canyon_segment_001__s001",
                "position": "middle",
                "alignment": "mid_sentence",
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
                "local_asset_id": "loc_a",
                "asset_fit": "strong",
                "asset_fit_reason": "clear match",
                "visual_intent": "establishing",
                "narrative_function": "chapter_open",
                "coverage_gap_id": None,
            },
            {
                "slot_id": "slot_002",
                "local_asset_id": "loc_b",
                "asset_fit": "acceptable",
                "asset_fit_reason": "ok",
                "visual_intent": "detail",
                "narrative_function": "evidence",
                "coverage_gap_id": None,
            },
        ],
    }


def _broken_slots_boundaries_payload() -> dict:
    payload = _valid_payload()
    # 3 boundaries → erwartet 2 Slots; nur 1 Slot → Invariante verletzt
    payload["slots"] = payload["slots"][:1]
    return payload


def test_repair_instruction_mentions_slot_boundary_invariant() -> None:
    text = _unified_cut_parse_repair_instruction(
        "Invariante verletzt: len(slots)=20 muss len(boundaries)-1=21 sein."
    )
    assert "REPAIR" in text
    assert "len(slots) MUST equal len(boundaries) - 1" in text
    assert "len(slots)=20" in text
    assert UNIFIED_CUT_PARSE_ATTEMPTS == 2


def test_generate_unified_cut_retries_once_on_invariant_error(
    tmp_path: Path, monkeypatch
) -> None:
    project = _project(tmp_path)
    _lock_and_time(project)
    prompts: list[str] = []
    calls = {"n": 0}

    def fake_llm(*, prompt: str, model: str, images=None):  # noqa: ANN001
        calls["n"] += 1
        prompts.append(prompt)
        if calls["n"] == 1:
            return json.dumps(_broken_slots_boundaries_payload())
        assert "REPAIR" in prompt
        assert "len(slots) MUST equal len(boundaries) - 1" in prompt
        return json.dumps(_valid_payload())

    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.cut_plan_service._local_assets_payload",
        lambda *args, **kwargs: [
            {
                "asset_id": "loc_a",
                "path": "Canyon/a.mp4",
                "duration_seconds": 8.0,
                "media_type": "video",
            },
            {
                "asset_id": "loc_b",
                "path": "Canyon/b.mp4",
                "duration_seconds": 6.0,
                "media_type": "video",
            },
        ],
    )

    result = generate_unified_cut_for_folder(
        project, "Canyon", llm_callable=fake_llm
    )
    assert result.status == "PASS", result.error
    assert calls["n"] == 2
    assert result.plan is not None
    assert len(result.plan.slots) == 2
    assert len(result.plan.boundaries) == 3
    assert "REPAIR" in prompts[1]
    assert require_locked_script(project).script_version == result.plan.script_version


def test_generate_unified_cut_fails_after_two_parse_errors(
    tmp_path: Path, monkeypatch
) -> None:
    project = _project(tmp_path)
    _lock_and_time(project)
    calls = {"n": 0}

    def fake_llm(*, prompt: str, model: str, images=None):  # noqa: ANN001
        calls["n"] += 1
        return json.dumps(_broken_slots_boundaries_payload())

    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.cut_plan_service._local_assets_payload",
        lambda *args, **kwargs: [],
    )

    result = generate_unified_cut_for_folder(
        project, "Canyon", llm_callable=fake_llm
    )
    assert result.status == "FAIL"
    assert calls["n"] == 2
    assert "Invariante" in (result.error or "")
