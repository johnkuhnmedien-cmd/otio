"""Intro-Cut: gebündeltes Inventar, strong-only, Opening/Closing, separater OTIO."""

from __future__ import annotations

import json
import wave
from pathlib import Path

import pytest

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis
from otio_app.defaults import (
    AUDIO_SCOPE_INTRO,
    AUDIO_STATUS_READY,
    DEFAULT_ENHANCED_WORK_SUBDIR,
)
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import (
    get_folder_inventory_path,
    get_intro_audio_dir,
    get_intro_hook_confirmed_path,
    get_voiceover_audio_manifest_path,
)
from otio_app.services.without_voiceover_enhanced.intro_cut_service import (
    INTRO_CLOSING_HOLD_DEFAULT_SEC,
    INTRO_OPENING_HOLD_SEC,
    IntroCutError,
    build_bundled_inventory_for_intro,
    enforce_intro_strong_only,
    export_intro_otio,
    generate_intro_cut,
    resolve_intro_timeline,
)
from otio_app.services.without_voiceover_enhanced.io_utils import write_json
from otio_app.services.without_voiceover_enhanced.models import (
    CoverageGap,
    CoverageGapsDocument,
    EditorialAnchor,
    RoughCutPlanDocument,
    RoughShot,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    coverage_gaps_path,
    intro_bundled_inventory_path,
    intro_cut_plan_path,
    intro_resolved_timeline_path,
)
from otio_app.services.without_voiceover_enhanced.script_prompts import (
    build_intro_cut_prompt,
)
from otio_app.services.voiceover_generation.models import (
    ConfirmedIntroHook,
    VoiceoverAudioItem,
    VoiceoverAudioManifest,
)


def _write_silence_wav(path: Path, *, duration_sec: float = 2.0, rate: int = 16000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = int(duration_sec * rate)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * frames)


def _project(tmp_path: Path, folders: list[str] | None = None) -> Project:
    root = tmp_path / "proj"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    folder_names = folders or ["Yosemite", "Caddo"]
    for folder in folder_names:
        (root / folder).mkdir(exist_ok=True)
    return Project(
        name="Intro Cut Test",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        language="de",
        asset_subdir_names=folder_names,
        selected_asset_subdirs=folder_names,
        fps=25.0,
    )


def _seed_inventories(project: Project) -> dict[str, Path]:
    media_paths: dict[str, Path] = {}
    for folder, asset_id in (("Yosemite", "yo_01"), ("Caddo", "ca_01")):
        media = Path(project.project_root) / folder / f"{asset_id}.jpg"
        media.write_bytes(b"\xff\xd8\xff\xd9")
        media_paths[asset_id] = media
        inventory = AssetFolderAnalysis(
            folder=folder,
            assets=[
                AssetMediaAnalysis(
                    path=str(media),
                    asset_id=asset_id,
                    description=f"{folder} landscape",
                    media_type="photo",
                )
            ],
        )
        write_json(get_folder_inventory_path(project.work_dir_path, folder), inventory)
    return media_paths


def _seed_intro_hook_and_audio(project: Project, *, duration_sec: float = 3.0) -> Path:
    hook = ConfirmedIntroHook(
        project_id=project.id,
        hook_id="hook_test",
        hook_text="Drei Orte. Ein Versprechen.",
        word_count=5,
        used_folders=list(project.selected_asset_subdirs),
    )
    path = get_intro_hook_confirmed_path(project.language_work_dir_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(hook.model_dump_json(indent=2), encoding="utf-8")

    audio_dir = get_intro_audio_dir(project.language_work_dir_path)
    audio_path = audio_dir / "intro.wav"
    _write_silence_wav(audio_path, duration_sec=duration_sec)
    manifest = VoiceoverAudioManifest(
        project_id=project.id,
        items=[
            VoiceoverAudioItem(
                scope=AUDIO_SCOPE_INTRO,
                audio_path=str(audio_path),
                audio_duration_sec=duration_sec,
                status=AUDIO_STATUS_READY,
            )
        ],
    )
    manifest_path = get_voiceover_audio_manifest_path(project.language_work_dir_path)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    return audio_path


def test_build_intro_cut_prompt_has_strong_and_holds() -> None:
    prompt = build_intro_cut_prompt(
        intro_hook_json='{"hook_text":"x"}',
        bundled_inventory_json='{"chapters":{}}',
        intro_audio_duration_seconds=12.5,
        style_profile_text="style",
        dramaturgy_text="drama",
    )
    assert "asset_fit \"strong\"" in prompt or 'asset_fit "strong"' in prompt
    assert "acceptable" in prompt  # verboten erklären
    assert "4 seconds" in prompt
    assert "5–8" in prompt or "5-8" in prompt
    assert "BUNDLED INVENTORY" in prompt
    assert "12.500" in prompt


def test_bundled_inventory_contains_all_chapters(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _seed_inventories(project)
    bundled = build_bundled_inventory_for_intro(project)
    assert bundled["chapter_count"] == 2
    assert bundled["asset_count"] == 2
    assert set(bundled["chapters"]) == {"Yosemite", "Caddo"}
    ids = {item["local_asset_id"] for item in bundled["all_assets"]}
    assert ids == {"yo_01", "ca_01"}


def test_enforce_intro_strong_only_rejects_acceptable() -> None:
    cut = RoughCutPlanDocument(
        script_version="intro:test",
        shots=[
            RoughShot(
                shot_id="shot_001",
                start_anchor=EditorialAnchor(segment_id="intro_segment_001", position="start"),
                end_anchor=EditorialAnchor(segment_id="intro_segment_001", position="early"),
                local_asset_id="yo_01",
                asset_id="yo_01",
                asset_fit="acceptable",
                visual_intent="valley",
            ),
            RoughShot(
                shot_id="shot_002",
                start_anchor=EditorialAnchor(segment_id="intro_segment_001", position="middle"),
                end_anchor=EditorialAnchor(segment_id="intro_segment_001", position="end"),
                local_asset_id="ca_01",
                asset_id="ca_01",
                asset_fit="strong",
                visual_intent="swamp",
            ),
        ],
    )
    coverage = CoverageGapsDocument(script_version="intro:test", gaps=[])
    cut2, cov2 = enforce_intro_strong_only(cut, coverage)
    assert cut2.shots[0].local_asset_id is None
    assert cut2.shots[0].asset_fit == "none"
    assert cut2.shots[0].coverage_gap_id
    assert cut2.shots[0].coverage_gap_id.startswith("intro_")
    assert cut2.shots[1].asset_fit == "strong"
    assert cut2.shots[1].local_asset_id == "ca_01"
    assert any(g.gap_id.startswith("intro_") for g in cov2.gaps)


def test_generate_intro_cut_and_resolve_and_export(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _seed_inventories(project)
    _seed_intro_hook_and_audio(project, duration_sec=3.0)

    llm_payload = {
        "shots": [
            {
                "shot_id": "shot_001",
                "shot_role": "opening",
                "start_anchor": {
                    "type": "segment",
                    "segment_id": "intro_segment_001",
                    "position": "start",
                },
                "end_anchor": {
                    "type": "segment",
                    "segment_id": "intro_segment_001",
                    "position": "early",
                },
                "narrative_function": "orientation",
                "visual_intent": "wide valley",
                "local_asset_id": "yo_01",
                "asset_fit": "strong",
                "asset_fit_reason": "matches valley beat",
                "coverage_gap_id": None,
            },
            {
                "shot_id": "shot_002",
                "shot_role": "body",
                "start_anchor": {
                    "type": "segment",
                    "segment_id": "intro_segment_001",
                    "position": "early",
                },
                "end_anchor": {
                    "type": "segment",
                    "segment_id": "intro_segment_001",
                    "position": "late",
                },
                "narrative_function": "evidence",
                "visual_intent": "detail",
                "local_asset_id": "ca_01",
                "asset_fit": "acceptable",
                "asset_fit_reason": "only ok",
                "coverage_gap_id": None,
            },
            {
                "shot_id": "shot_003",
                "shot_role": "closing",
                "start_anchor": {
                    "type": "segment",
                    "segment_id": "intro_segment_001",
                    "position": "late",
                },
                "end_anchor": {
                    "type": "segment",
                    "segment_id": "intro_segment_001",
                    "position": "end",
                },
                "narrative_function": "reflection",
                "visual_intent": "hold",
                "local_asset_id": "yo_01",
                "asset_fit": "strong",
                "asset_fit_reason": "closing hold",
                "coverage_gap_id": None,
                "closing_hold_seconds": 6.0,
            },
        ],
        "coverage_gaps": [],
    }

    def _llm(*, prompt: str, model: str):
        assert "BUNDLED INVENTORY" in prompt
        assert "yo_01" in prompt and "ca_01" in prompt
        return json.dumps(llm_payload)

    result = generate_intro_cut(
        project,
        provider="openai",
        model="gpt-5.6-terra",
        llm_callable=_llm,
    )
    assert result.shot_count == 3
    assert result.gap_count >= 1  # acceptable → gap
    assert intro_bundled_inventory_path(project).is_file()
    assert intro_cut_plan_path(project).is_file()

    main_gaps = json.loads(coverage_gaps_path(project).read_text(encoding="utf-8"))
    assert any(str(g.get("gap_id", "")).startswith("intro_") for g in main_gaps["gaps"])

    # Nur strong Shots für Timing — body wurde zu Gap; opening+closing bleiben.
    resolved = resolve_intro_timeline(project)
    assert resolved.audio_segments
    audio = resolved.audio_segments[0]
    assert audio.timeline_start_seconds == pytest.approx(INTRO_OPENING_HOLD_SEC)
    assert audio.pause_after_seconds == pytest.approx(6.0)
    assert resolved.shots
    assert resolved.shots[0].timeline_start_seconds == pytest.approx(0.0)
    assert resolved.total_duration_seconds == pytest.approx(
        INTRO_OPENING_HOLD_SEC + 3.0 + 6.0
    )
    assert resolved.shots[-1].timeline_end_seconds == pytest.approx(
        resolved.total_duration_seconds
    )

    out = export_intro_otio(project, basename="test_intro")
    assert out.is_file()
    assert out.name == "test_intro.otio"
    text = out.read_text(encoding="utf-8")
    assert "yo_01" in text or "Yosemite" in text


def test_generate_intro_cut_requires_hook(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _seed_inventories(project)
    with pytest.raises(IntroCutError, match="Intro-Hook"):
        generate_intro_cut(project, llm_callable=lambda **_: "{}")
