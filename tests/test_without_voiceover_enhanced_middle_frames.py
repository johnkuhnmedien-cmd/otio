"""Optional: Mittel-Frames an Enhanced LLM-Lauf 2."""

from __future__ import annotations

import json
from pathlib import Path

from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.services.plan_llm_client import (
    PlanImageAttachment,
    _openai_user_content,
)
from otio_app.services.voiceover_generation.dramaturgy_service import (
    save_confirmed_dramaturgy,
)
from otio_app.services.voiceover_generation.models import (
    DramaturgyFolderEntry,
    DramaturgyPlan,
)
from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
    CutPlanOptions,
    load_cut_plan_options,
    save_cut_plan_options,
)
from otio_app.services.without_voiceover_enhanced.cut_plan_service import (
    _local_assets_payload,
    generate_rough_cut_and_pauses,
    middle_frame_attachments_from_payload,
    select_middle_frame_path,
)
from otio_app.services.without_voiceover_enhanced.io_utils import write_json
from otio_app.services.without_voiceover_enhanced.models import (
    EnhancedScriptDocument,
    ScriptSegment,
    SegmentTiming,
    SegmentTimingsDocument,
)
from otio_app.services.without_voiceover_enhanced.paths import segment_timings_path
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    lock_script,
    save_script_draft,
)
from otio_app.services.without_voiceover_enhanced.script_prompts import (
    build_rough_cut_prompt,
)
from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis
from otio_app.project_layout import get_folder_inventory_path


def _project(tmp_path: Path) -> Project:
    root = tmp_path / "proj"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    (root / "Canyon").mkdir()
    return Project(
        name="MiddleFrames",
        project_root=str(root),
        work_dir=str(work),
        language="de",
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        fps=25.0,
        selected_asset_subdirs=["Canyon"],
        asset_subdir_names=["Canyon"],
    )


def test_select_middle_frame_path_prefers_center(tmp_path: Path) -> None:
    frames = []
    for index in range(1, 4):
        path = tmp_path / f"frame_{index:03d}.jpg"
        path.write_bytes(b"\xff\xd8\xff" + bytes([index]))
        frames.append(str(path))
    middle = select_middle_frame_path(frames)
    assert middle is not None
    assert middle.name == "frame_002.jpg"


def test_cut_plan_options_default_off(tmp_path: Path) -> None:
    project = _project(tmp_path)
    options = load_cut_plan_options(project)
    assert options.include_middle_frames is False
    save_cut_plan_options(project, CutPlanOptions(include_middle_frames=True))
    loaded = load_cut_plan_options(project)
    assert loaded.include_middle_frames is True


def test_local_assets_payload_includes_middle_frame_when_enabled(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    media = Path(project.project_root) / "Canyon" / "clip.mp4"
    media.write_bytes(b"fake")
    frame_dir = Path(project.work_dir) / "frames" / "Canyon" / "clip"
    frame_dir.mkdir(parents=True)
    frames = []
    for index in range(1, 4):
        path = frame_dir / f"frame_{index:03d}.jpg"
        path.write_bytes(b"\xff\xd8\xff" + bytes([index]))
        frames.append(str(path))
    inventory = AssetFolderAnalysis(
        folder="Canyon",
        assets=[
            AssetMediaAnalysis(
                path=str(media),
                asset_id="asset_canyon_1",
                description="Red canyon walls, wide shot",
                frames_used=frames,
                media_type="video",
            )
        ],
    )
    write_json(get_folder_inventory_path(project.work_dir_path, "Canyon"), inventory)

    plain = _local_assets_payload(project, folder_name="Canyon")
    # Beschreibungen immer (für Shot-Länge/Passung); Mittel-Frames nur bei Vision.
    assert plain[0].get("description") == "Red canyon walls, wide shot"
    assert "middle_frame_path" not in plain[0]

    rich = _local_assets_payload(
        project, folder_name="Canyon", include_middle_frames=True
    )
    assert rich[0]["description"] == "Red canyon walls, wide shot"
    assert rich[0]["has_middle_frame"] is True
    assert Path(rich[0]["middle_frame_path"]).name == "frame_002.jpg"

    images = middle_frame_attachments_from_payload(rich, max_images=10)
    assert len(images) == 1
    assert images[0].label == "asset_canyon_1"


def test_rough_cut_prompt_vision_rules_only_when_enabled() -> None:
    plain = build_rough_cut_prompt(
        locked_script_json="{}",
        segment_timings_json="{}",
        local_assets_json="[]",
        style_profile_text="s",
        dramaturgy_text="d",
        include_middle_frames=False,
    )
    vision = build_rough_cut_prompt(
        locked_script_json="{}",
        segment_timings_json="{}",
        local_assets_json="[]",
        style_profile_text="s",
        dramaturgy_text="d",
        include_middle_frames=True,
    )
    assert "MIDDLE-FRAME VISION" not in plain
    assert "MIDDLE-FRAME VISION" in vision
    assert "VISUAL DIVERSITY" in vision


def test_openai_user_content_embeds_images(tmp_path: Path) -> None:
    frame = tmp_path / "frame.jpg"
    frame.write_bytes(b"\xff\xd8\xff\x00")
    content = _openai_user_content(
        "PROMPT",
        [PlanImageAttachment(path=frame, label="asset_1")],
    )
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "PROMPT"}
    assert any(
        part.get("type") == "image_url"
        and str(part["image_url"]["url"]).startswith("data:image/jpeg;base64,")
        for part in content
        if isinstance(part, dict)
    )


def test_generate_rough_cut_passes_images_when_option_on(tmp_path: Path) -> None:
    import wave

    project = _project(tmp_path)
    save_confirmed_dramaturgy(
        project,
        DramaturgyPlan(
            project_id=project.id,
            recommended_folder_order=[
                DramaturgyFolderEntry(
                    folder_name="Canyon",
                    order_index=0,
                    enabled=True,
                )
            ],
        ),
    )
    save_script_draft(
        project,
        EnhancedScriptDocument(
            narration_full="Canyon text.",
            segments=[
                ScriptSegment(
                    segment_id="Canyon_segment_001",
                    text="Canyon text.",
                    sequence_index=1,
                    folder_name="Canyon",
                )
            ],
        ),
    )
    lock_script(project)

    audio = Path(project.work_dir) / "a.wav"
    with wave.open(str(audio), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"\x00\x00" * 8000)
    write_json(
        segment_timings_path(project),
        SegmentTimingsDocument(
            script_version="script-v1",
            segments=[
                SegmentTiming(
                    segment_id="Canyon_segment_001",
                    script_version="script-v1",
                    audio_path=str(audio),
                    duration_seconds=0.5,
                )
            ],
        ),
    )

    media = Path(project.project_root) / "Canyon" / "clip.mp4"
    media.write_bytes(b"fake")
    frame_dir = Path(project.work_dir) / "frames" / "Canyon" / "clip"
    frame_dir.mkdir(parents=True)
    frames = []
    for index in range(1, 4):
        path = frame_dir / f"frame_{index:03d}.jpg"
        path.write_bytes(b"\xff\xd8\xff" + bytes([index]))
        frames.append(str(path))
    write_json(
        get_folder_inventory_path(project.work_dir_path, "Canyon"),
        AssetFolderAnalysis(
            folder="Canyon",
            assets=[
                AssetMediaAnalysis(
                    path=str(media),
                    asset_id="asset_canyon_1",
                    description="Canyon wide",
                    frames_used=frames,
                    media_type="video",
                )
            ],
        ),
    )
    save_cut_plan_options(project, CutPlanOptions(include_middle_frames=True))

    captured: dict = {}

    def fake_llm(*, prompt: str, model: str, images=None) -> str:
        captured["prompt"] = prompt
        captured["images"] = list(images or [])
        return json.dumps(
            {
                "pause_directives": [],
                "shots": [
                    {
                        "shot_id": "Canyon_shot_001",
                        "start_anchor": {
                            "type": "segment",
                            "segment_id": "Canyon_segment_001",
                            "position": "start",
                        },
                        "end_anchor": {
                            "type": "segment",
                            "segment_id": "Canyon_segment_001",
                            "position": "end",
                        },
                        "narrative_function": "orientation",
                        "visual_intent": "establish",
                        "local_asset_id": "asset_canyon_1",
                        "asset_fit": "strong",
                        "asset_fit_reason": "matches",
                        "coverage_gap_id": None,
                    }
                ],
                "coverage_gaps": [],
            }
        )

    generate_rough_cut_and_pauses(project, llm_callable=fake_llm)
    assert "MIDDLE-FRAME VISION" in captured["prompt"]
    assert len(captured["images"]) == 1
    assert captured["images"][0].label == "asset_canyon_1"

    # Option aus → keine Bilder, kein Vision-Promptblock.
    save_cut_plan_options(project, CutPlanOptions(include_middle_frames=False))
    captured.clear()

    def fake_llm_off(*, prompt: str, model: str, images=None) -> str:
        captured["prompt"] = prompt
        captured["images"] = list(images or [])
        return fake_llm(prompt=prompt, model=model, images=images)

    generate_rough_cut_and_pauses(project, llm_callable=fake_llm_off)
    assert "MIDDLE-FRAME VISION" not in captured["prompt"]
    assert captured["images"] == []
