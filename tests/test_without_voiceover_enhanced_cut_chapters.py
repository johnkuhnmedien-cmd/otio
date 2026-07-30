"""LLM-Lauf 2/3: ein Call pro Dramaturgie-Kapitel."""

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
    generate_final_cut_plan,
    generate_rough_cut_and_pauses,
    list_cut_plan_chapter_names,
)
from otio_app.services.without_voiceover_enhanced.io_utils import write_json
from otio_app.services.without_voiceover_enhanced.models import (
    EnhancedScriptDocument,
    NarrationAnchor,
    NarrationTimelineDocument,
    NarrationTimelineEntry,
    RoughCutPlanDocument,
    RoughShot,
    ScriptSegment,
    SegmentTiming,
    SegmentTimingsDocument,
    VisualIntent,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    narration_timeline_path,
    rough_cut_plan_path,
    segment_timings_path,
)
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    lock_script,
    save_script_draft,
)
from otio_app.services.without_voiceover_enhanced.script_prompts import (
    build_final_cut_prompt,
    build_rough_cut_prompt,
)


def _project(tmp_path: Path, folders: list[str]) -> Project:
    root = tmp_path / "proj"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    for folder in folders:
        (root / folder).mkdir(exist_ok=True)
    return Project(
        name="CutChapters",
        project_root=str(root),
        work_dir=str(work),
        language="de",
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        fps=25.0,
        selected_asset_subdirs=folders,
        asset_subdir_names=folders,
    )


def _confirm(project: Project, folders: list[str]) -> None:
    save_confirmed_dramaturgy(
        project,
        DramaturgyPlan(
            project_id=project.id,
            project_title="Test",
            recommended_folder_order=[
                DramaturgyFolderEntry(
                    folder_name=folder,
                    order_index=index,
                    enabled=True,
                    dramaturgy_role="development",
                )
                for index, folder in enumerate(folders)
            ],
        ),
    )


def _lock_two_chapters(project: Project) -> None:
    save_script_draft(
        project,
        EnhancedScriptDocument(
            narration_full="Canyon text. Desert text.",
            segments=[
                ScriptSegment(
                    segment_id="Canyon_segment_001",
                    text="Canyon text.",
                    sequence_index=1,
                    folder_name="Canyon",
                    folder_order_index=0,
                    visual_intent_ids=["Canyon_intent_001"],
                ),
                ScriptSegment(
                    segment_id="Desert_segment_001",
                    text="Desert text.",
                    sequence_index=2,
                    folder_name="Desert",
                    folder_order_index=1,
                    visual_intent_ids=["Desert_intent_001"],
                ),
            ],
            visual_intents=[
                VisualIntent(
                    intent_id="Canyon_intent_001",
                    description="Canyon wide",
                    folder_name="Canyon",
                ),
                VisualIntent(
                    intent_id="Desert_intent_001",
                    description="Desert dunes",
                    folder_name="Desert",
                ),
            ],
        ),
    )
    lock_script(project)


def _write_silent_wav(path: Path, duration_seconds: float = 0.5) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sample_rate = 16000
    n_frames = int(duration_seconds * sample_rate)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(b"\x00\x00" * n_frames)


def _write_timings(project: Project) -> None:
    audio_dir = Path(project.work_dir) / "audio"
    canyon = audio_dir / "canyon.wav"
    desert = audio_dir / "desert.wav"
    _write_silent_wav(canyon, 2.0)
    _write_silent_wav(desert, 3.0)
    write_json(
        segment_timings_path(project),
        SegmentTimingsDocument(
            script_version="script-v1",
            segments=[
                SegmentTiming(
                    segment_id="Canyon_segment_001",
                    script_version="script-v1",
                    audio_path=str(canyon),
                    duration_seconds=2.0,
                ),
                SegmentTiming(
                    segment_id="Desert_segment_001",
                    script_version="script-v1",
                    audio_path=str(desert),
                    duration_seconds=3.0,
                ),
            ],
        ),
    )


def test_list_cut_plan_chapters_follows_dramaturgy(tmp_path: Path) -> None:
    project = _project(tmp_path, ["Canyon", "Desert"])
    _confirm(project, ["Canyon", "Desert"])
    _lock_two_chapters(project)
    from otio_app.services.without_voiceover_enhanced.script_lock_service import (
        require_locked_script,
    )

    names = list_cut_plan_chapter_names(project, require_locked_script(project))
    assert names == ["Canyon", "Desert"]


def test_rough_cut_prompt_includes_chapter_scope() -> None:
    prompt = build_rough_cut_prompt(
        locked_script_json="{}",
        segment_timings_json="{}",
        local_assets_json="[]",
        style_profile_text="style",
        dramaturgy_text="drama",
        folder_name="Canyon",
        folder_slug="Canyon",
        previous_folder_name=None,
        next_folder_name="Desert",
    )
    assert "CHAPTER SCOPE" in prompt
    assert "Canyon" in prompt
    assert "Canyon_shot_001" in prompt


def test_final_cut_prompt_includes_chapter_scope() -> None:
    prompt = build_final_cut_prompt(
        locked_script_json="{}",
        narration_timeline_json="{}",
        pause_directives_json="[]",
        rough_cut_json="{}",
        local_assets_json="[]",
        accepted_supplements_json="{}",
        style_profile_text="style",
        folder_name="Desert",
        folder_slug="Desert",
        previous_folder_name="Canyon",
        next_folder_name=None,
    )
    assert "CHAPTER SCOPE" in prompt
    assert "Desert" in prompt


def test_generate_rough_cut_one_call_per_chapter(tmp_path: Path) -> None:
    project = _project(tmp_path, ["Canyon", "Desert"])
    _confirm(project, ["Canyon", "Desert"])
    _lock_two_chapters(project)
    _write_timings(project)

    calls: list[str] = []

    def fake_llm(*, prompt: str, model: str) -> str:
        if "CHAPTER SCOPE" in prompt and 'ONLY the chapter "Canyon"' in prompt:
            calls.append("Canyon")
            folder = "Canyon"
            seg = "Canyon_segment_001"
        elif "CHAPTER SCOPE" in prompt and 'ONLY the chapter "Desert"' in prompt:
            calls.append("Desert")
            folder = "Desert"
            seg = "Desert_segment_001"
        else:
            raise AssertionError("Expected chapter-scoped rough-cut prompt")
        return json.dumps(
            {
                "pause_directives": [],
                "shots": [
                    {
                        "shot_id": f"{folder}_shot_001",
                        "start_anchor": {
                            "type": "segment",
                            "segment_id": seg,
                            "position": "start",
                        },
                        "end_anchor": {
                            "type": "segment",
                            "segment_id": seg,
                            "position": "end",
                        },
                        "narrative_function": "orientation",
                        "visual_intent": f"{folder} establishing",
                        "local_asset_id": None,
                        "asset_fit": "none",
                        "asset_fit_reason": "no local asset",
                        "coverage_gap_id": f"{folder}_gap_001",
                    }
                ],
                "coverage_gaps": [
                    {
                        "coverage_gap_id": f"{folder}_gap_001",
                        "shot_id": f"{folder}_shot_001",
                        "needed_visual": f"{folder} landscape",
                        "editorial_purpose": "establish place",
                        "preferred_media_type": "video",
                        "search_concepts": [folder],
                    }
                ],
            }
        )

    progress: list[tuple[str, int, int]] = []
    rough, coverage = generate_rough_cut_and_pauses(
        project,
        llm_callable=fake_llm,
        progress_callback=lambda name, i, t: progress.append((name, i, t)),
    )
    assert calls == ["Canyon", "Desert"]
    assert progress == [("Canyon", 1, 2), ("Desert", 2, 2)]
    assert len(rough.shots) == 2
    assert {s.shot_id for s in rough.shots} == {
        "Canyon_shot_001",
        "Desert_shot_001",
    }
    assert len(coverage.gaps) == 2


def test_generate_final_cut_one_call_per_chapter(tmp_path: Path) -> None:
    project = _project(tmp_path, ["Canyon", "Desert"])
    _confirm(project, ["Canyon", "Desert"])
    _lock_two_chapters(project)
    _write_timings(project)

    write_json(
        narration_timeline_path(project),
        NarrationTimelineDocument(
            script_version="script-v1",
            total_duration_seconds=5.5,
            entries=[
                NarrationTimelineEntry(
                    segment_id="Canyon_segment_001",
                    start_seconds=0.0,
                    end_seconds=2.0,
                    pause_after_seconds=0.5,
                ),
                NarrationTimelineEntry(
                    segment_id="Desert_segment_001",
                    start_seconds=2.5,
                    end_seconds=5.5,
                    pause_after_seconds=0.0,
                ),
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
                    shot_id="Canyon_shot_001",
                    narration_start_anchor=NarrationAnchor(
                        segment_id="Canyon_segment_001", offset_seconds=0.0
                    ),
                    narration_end_anchor=NarrationAnchor(
                        segment_id="Canyon_segment_001", offset_seconds=1.0
                    ),
                    asset_id="asset_canyon",
                ),
                RoughShot(
                    shot_id="Desert_shot_001",
                    narration_start_anchor=NarrationAnchor(
                        segment_id="Desert_segment_001", offset_seconds=0.0
                    ),
                    narration_end_anchor=NarrationAnchor(
                        segment_id="Desert_segment_001", offset_seconds=1.0
                    ),
                    asset_id="asset_desert",
                ),
            ],
        ),
    )

    # Ensure rough slices work via editorial anchors too.
    from otio_app.services.without_voiceover_enhanced.models import EditorialAnchor

    rough = RoughCutPlanDocument(
        script_version="script-v1",
        shots=[
            RoughShot(
                shot_id="Canyon_shot_001",
                start_anchor=EditorialAnchor(
                    type="segment",
                    segment_id="Canyon_segment_001",
                    position="start",
                ),
                end_anchor=EditorialAnchor(
                    type="segment",
                    segment_id="Canyon_segment_001",
                    position="end",
                ),
                local_asset_id="asset_canyon",
                asset_id="asset_canyon",
            ),
            RoughShot(
                shot_id="Desert_shot_001",
                start_anchor=EditorialAnchor(
                    type="segment",
                    segment_id="Desert_segment_001",
                    position="start",
                ),
                end_anchor=EditorialAnchor(
                    type="segment",
                    segment_id="Desert_segment_001",
                    position="end",
                ),
                local_asset_id="asset_desert",
                asset_id="asset_desert",
            ),
        ],
    )
    write_json(rough_cut_plan_path(project), rough)

    calls: list[str] = []

    def fake_llm(*, prompt: str, model: str) -> str:
        if 'ONLY the chapter "Canyon"' in prompt:
            calls.append("Canyon")
            assert "Desert_segment_001" not in prompt
            seg = "Canyon_segment_001"
            asset = "asset_canyon"
            folder = "Canyon"
        elif 'ONLY the chapter "Desert"' in prompt:
            calls.append("Desert")
            assert "Canyon_segment_001" not in prompt
            seg = "Desert_segment_001"
            asset = "asset_desert"
            folder = "Desert"
        else:
            raise AssertionError("Expected chapter-scoped final-cut prompt")
        return json.dumps(
            {
                "shots": [
                    {
                        "shot_id": f"{folder}_shot_001",
                        "narration_start_anchor": {
                            "segment_id": seg,
                            "offset_seconds": 0.0,
                        },
                        "narration_end_anchor": {
                            "segment_id": seg,
                            "offset_seconds": 1.0,
                        },
                        "asset_id": asset,
                    }
                ]
            }
        )

    final = generate_final_cut_plan(project, llm_callable=fake_llm)
    assert calls == ["Canyon", "Desert"]
    assert len(final.shots) == 2
    assert {s.shot_id for s in final.shots} == {
        "Canyon_shot_001",
        "Desert_shot_001",
    }
