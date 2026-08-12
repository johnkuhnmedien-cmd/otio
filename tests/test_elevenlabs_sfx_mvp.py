"""ElevenLabs Sound Effects MVP — focused acceptance tests (A–AF)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import opentimelineio as otio
import pytest

from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
    DEFAULT_MAX_SFX_PER_CHAPTER,
    DEFAULT_SFX_PLANNER_MODEL,
    CutPlanOptions,
    load_cut_plan_options,
    save_cut_plan_options,
)
from otio_app.services.without_voiceover_enhanced.elevenlabs_sfx_client import (
    SFX_MODEL_ID,
    SFX_PROMPT_INFLUENCE_DEFAULT,
    SFX_PROMPT_MAX_CHARS,
    ElevenLabsSfxError,
    ElevenLabsSfxResult,
    generate_sound_effect,
)
from otio_app.services.without_voiceover_enhanced.io_utils import write_json
from otio_app.services.without_voiceover_enhanced.models import (
    CutBoundary,
    CutSlot,
    EnhancedScriptDocument,
    ResolvedAudioSegment,
    ResolvedChapterEnvelope,
    ResolvedShot,
    ResolvedTimelineDocument,
    ScriptSegment,
    UnifiedCutPlanDocument,
)
from otio_app.services.without_voiceover_enhanced.music_artifacts import (
    save_music_result,
)
from otio_app.services.without_voiceover_enhanced.otio_export_service import (
    export_otio_from_resolved_timeline,
)
from otio_app.services.without_voiceover_enhanced.otio_music_track import (
    build_optional_music_track,
)
from otio_app.services.without_voiceover_enhanced.otio_sfx_track import (
    build_optional_sfx_track,
    collect_sfx_placements,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    chapter_resolved_timeline_path,
    chapter_unified_cut_plan_path,
    music_result_path,
    music_wav_path,
    script_locked_path,
    sfx_plan_path,
    sfx_result_path,
    sfx_wav_path,
)
from otio_app.services.without_voiceover_enhanced.sfx_artifacts import (
    fingerprint_text,
    load_sfx_result,
    replace_canonical_sfx_set,
    resolved_timeline_fingerprint_from_shots,
    save_sfx_result,
    sfx_status_for_scope,
    usable_sfx_effects_for_otio,
)
from otio_app.services.without_voiceover_enhanced.sfx_planner import (
    SfxPlanValidationError,
    build_planner_input_bundle,
    build_used_shots_for_planner,
    parse_and_validate_sfx_plan,
    resolve_sfx_planner_model_id,
)
from otio_app.services.without_voiceover_enhanced.sfx_prompt import (
    build_sfx_planner_system_rules,
)
from otio_app.services.without_voiceover_enhanced.sfx_service import (
    convert_and_normalize_sfx_wav,
    generate_sfx_for_chapter,
    is_sfx_mvp_chapter_allowed,
    resolve_sfx_anchor,
    sfx_ui_status_chapter,
    usable_sfx_placements_for_otio,
    validate_final_sfx_wav,
)


def _project(tmp_path: Path, *, chapters: list[str] | None = None) -> Project:
    root = tmp_path / "proj"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    names = chapters or ["Yosemite", "Caddo", "Zion", "Bryce"]
    return Project(
        name="SfxMVP",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        language="de",
        asset_subdir_names=list(names),
        selected_asset_subdirs=list(names),
        fps=25.0,
        width=1920,
        height=1080,
    )


def _write_locked(
    project: Project,
    *,
    version: str = "v1",
    chapters: list[tuple[str, str]] | None = None,
) -> EnhancedScriptDocument:
    segs = [
        ScriptSegment(
            segment_id="intro_1",
            folder_name="Intro",
            text="Welcome to the parks.",
            sequence_index=0,
            folder_order_index=0,
        )
    ]
    body = chapters or [
        ("Yosemite", "Yosemite granite walls rise above the valley floor."),
        ("Caddo", "Caddo Lake cypress knees emerge from dark water."),
        ("Zion", "Zion sandstone corridors channel desert light."),
        ("Bryce", "Bryce hoodoos glow under high desert sun."),
    ]
    for i, (folder, text) in enumerate(body, start=1):
        segs.append(
            ScriptSegment(
                segment_id=f"ch_{i}",
                folder_name=folder,
                text=text,
                sequence_index=i,
                folder_order_index=i,
            )
        )
    doc = EnhancedScriptDocument(
        script_version=version,
        script_status="locked",
        narration_full=" ".join(s.text for s in segs),
        segments=segs,
    )
    write_json(script_locked_path(project), doc)
    return doc


def _plan(folder: str) -> UnifiedCutPlanDocument:
    return UnifiedCutPlanDocument(
        script_version="v1",
        boundaries=[
            CutBoundary(
                cut_id=f"{folder}_cut_000",
                sentence_id=f"{folder}_s1",
                position="start",
                alignment="sentence_boundary",
            ),
            CutBoundary(
                cut_id=f"{folder}_cut_001",
                sentence_id=f"{folder}_s1",
                position="middle",
                alignment="sentence_boundary",
            ),
            CutBoundary(
                cut_id=f"{folder}_cut_002",
                sentence_id=f"{folder}_s1",
                position="end",
                alignment="sentence_boundary",
            ),
        ],
        slots=[
            CutSlot(
                slot_id=f"{folder}_slot_001",
                local_asset_id="a1",
                asset_fit="strong",
                asset_fit_reason="fit",
                visual_intent="wide limestone valley",
                needed_visual="wide exterior limestone valley",
            ),
            CutSlot(
                slot_id=f"{folder}_slot_002",
                local_asset_id="a2",
                asset_fit="strong",
                asset_fit_reason="fit",
                visual_intent="forest path",
                needed_visual="forest path through trees",
            ),
        ],
    )


def _resolved(folder: str, *, duration: float = 20.0) -> ResolvedTimelineDocument:
    return ResolvedTimelineDocument(
        script_version="v1",
        fps=25.0,
        total_duration_seconds=duration,
        shots=[
            ResolvedShot(
                shot_id=f"{folder}_slot_001",
                asset_id="a1",
                timeline_start_seconds=0.0,
                timeline_end_seconds=10.0,
                source_start_seconds=0.0,
                source_end_seconds=10.0,
                folder_name=folder,
                chapter_id=folder,
                asset_fit="strong",
            ),
            ResolvedShot(
                shot_id=f"{folder}_slot_002",
                asset_id="a2",
                timeline_start_seconds=10.0,
                timeline_end_seconds=duration,
                source_start_seconds=0.0,
                source_end_seconds=duration - 10.0,
                folder_name=folder,
                chapter_id=folder,
                asset_fit="strong",
            ),
        ],
        audio_segments=[
            ResolvedAudioSegment(
                segment_id=f"ch_1",
                audio_path="/tmp/fake.mp3",
                timeline_start_seconds=1.0,
                timeline_end_seconds=12.0,
                source_start_seconds=0.0,
                source_end_seconds=11.0,
                chapter_id=folder,
            )
        ],
        chapters=[
            ResolvedChapterEnvelope(
                chapter_id=folder,
                folder_name=folder,
                chapter_video_start=0.0,
                chapter_audio_start=1.0,
                chapter_audio_end=12.0,
                chapter_video_end=duration,
                first_shot_id=f"{folder}_slot_001",
                last_shot_id=f"{folder}_slot_002",
                segment_ids=["ch_1"],
            )
        ],
    )


def _seed_chapter(project: Project, folder: str = "Yosemite") -> ResolvedTimelineDocument:
    _write_locked(project)
    plan = _plan(folder)
    resolved = _resolved(folder)
    write_json(chapter_unified_cut_plan_path(project, folder), plan)
    write_json(chapter_resolved_timeline_path(project, folder), resolved)
    return resolved


def _make_wav(path: Path, *, duration: float = 2.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-nostdin",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:duration={duration}",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-c:a",
        "pcm_s16le",
        str(path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return path


def _fake_llm(plan_payload: dict):
    text = json.dumps(plan_payload)

    def _call(*, prompt: str, model: str | None = None, **_kwargs):
        return SimpleNamespace(text=text, model=model)

    return _call


def _fake_sfx_bytes(*, duration: float = 2.0) -> bytes:
    # Generate a tiny real wav via ffmpeg and return bytes for API mock.
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "t.wav"
        _make_wav(path, duration=duration)
        return path.read_bytes()


def _export_otio_allowing_gaps(project, resolved, basename: str):
    from otio_app.services.without_voiceover_enhanced.otio_export_service import (
        EnhancedOtioExportError,
    )

    def _skip_shot(*_a, **_k):
        raise EnhancedOtioExportError("skip shot")

    def _skip_audio(*_a, **_k):
        raise EnhancedOtioExportError("skip audio")

    with (
        patch(
            "otio_app.services.without_voiceover_enhanced.otio_export_service._ensure_shot_media_for_export",
            _skip_shot,
        ),
        patch(
            "otio_app.services.without_voiceover_enhanced.otio_export_service._assert_local_file",
            _skip_audio,
        ),
        patch(
            "otio_app.services.without_voiceover_enhanced.otio_export_service._render_folder_title_items",
            return_value=([], []),
        ),
    ):
        return export_otio_from_resolved_timeline(
            project,
            basename=basename,
            allow_errors=True,
            resolved=resolved,
        )


# --- Planner ---


def test_a_planner_receives_only_used_shots(tmp_path: Path) -> None:
    project = _project(tmp_path)
    resolved = _seed_chapter(project)
    plan = _plan("Yosemite")
    with patch(
        "otio_app.services.without_voiceover_enhanced.sfx_planner._local_assets_payload",
        return_value=[
            {"local_asset_id": "a1", "description": "valley"},
            {"local_asset_id": "a2", "description": "forest"},
            {"local_asset_id": "UNUSED", "description": "should not appear"},
        ],
    ):
        shots = build_used_shots_for_planner(
            project, resolved=resolved, plan=plan, folder_name="Yosemite"
        )
        bundle = build_planner_input_bundle(
            project,
            resolved=resolved,
            plan=plan,
            folder_name="Yosemite",
            locked_script_text="script",
            scope="chapter",
            narration_start=1.0,
            narration_end=12.0,
            scope_total_duration=20.0,
            max_sfx=3,
        )
    ids = {s["asset_id"] for s in shots}
    assert ids == {"a1", "a2"}
    assert "UNUSED" not in bundle["prompt"]
    assert "should not appear" not in bundle["prompt"]


def test_b_planner_receives_final_script(tmp_path: Path) -> None:
    project = _project(tmp_path)
    resolved = _seed_chapter(project)
    plan = _plan("Yosemite")
    with patch(
        "otio_app.services.without_voiceover_enhanced.sfx_planner._local_assets_payload",
        return_value=[],
    ), patch(
        "otio_app.services.without_voiceover_enhanced.sfx_planner.load_cleaned_sentence_rows_for_segments",
        return_value=[],
    ):
        bundle = build_planner_input_bundle(
            project,
            resolved=resolved,
            plan=plan,
            folder_name="Yosemite",
            locked_script_text="FINAL LOCKED SCRIPT TEXT",
            scope="chapter",
            narration_start=1.0,
            narration_end=12.0,
            scope_total_duration=20.0,
            max_sfx=3,
        )
    assert "FINAL LOCKED SCRIPT TEXT" in bundle["prompt"]


def test_c_planner_receives_real_word_timestamps(tmp_path: Path) -> None:
    project = _project(tmp_path)
    resolved = _seed_chapter(project)
    plan = _plan("Yosemite")
    rows = [
        {
            "sentence_id": "ch_1#s0",
            "start_seconds": 0.0,
            "end_seconds": 2.0,
            "words": [
                {
                    "text": "granite",
                    "start_seconds": 0.4,
                    "end_seconds": 0.8,
                    "offset_seconds": 0.4,
                    "original_word_index": 0,
                }
            ],
        }
    ]
    with patch(
        "otio_app.services.without_voiceover_enhanced.sfx_planner._local_assets_payload",
        return_value=[],
    ), patch(
        "otio_app.services.without_voiceover_enhanced.sfx_planner.load_cleaned_sentence_rows_for_segments",
        return_value=rows,
    ):
        bundle = build_planner_input_bundle(
            project,
            resolved=resolved,
            plan=plan,
            folder_name="Yosemite",
            locked_script_text="x",
            scope="chapter",
            narration_start=1.0,
            narration_end=12.0,
            scope_total_duration=20.0,
            max_sfx=3,
        )
    assert any(w["word_ref"].endswith("#0") for w in bundle["word_flow"])
    assert any(abs(w["onset"] - 1.4) < 1e-6 for w in bundle["word_flow"])


def test_d_unused_assets_excluded(tmp_path: Path) -> None:
    test_a_planner_receives_only_used_shots(tmp_path)


def test_e_zero_sfx_valid_plan() -> None:
    plan = parse_and_validate_sfx_plan(
        {"schema_version": "sfx-plan-v1", "scope": "chapter", "sfx": []},
        max_sfx=3,
        known_shot_ids={"s1"},
        known_word_refs=set(),
        scope="chapter",
    )
    assert plan["sfx"] == []


def test_f_default_max_sfx_is_three() -> None:
    assert DEFAULT_MAX_SFX_PER_CHAPTER == 3
    assert CutPlanOptions().max_sfx_per_chapter == 3
    rules = build_sfx_planner_system_rules(max_sfx_per_chapter=3)
    assert "hard maximum, not a target" in rules
    assert "Use fewer whenever possible" in rules


def test_g_over_max_rejected_not_truncated() -> None:
    items = [
        {
            "sfx_id": f"sfx_{i:03d}",
            "sfx_type": "natural_ambience",
            "prompt": "subtle wind, no music, no speech",
            "evidence_basis": "environmental_plausible",
            "editorial_value": "high",
            "shot_id": "slot_1",
            "anchor_type": "shot_start",
            "word_ref": None,
            "duration_class": "short",
            "reason": "x",
        }
        for i in range(1, 6)
    ]
    with pytest.raises(SfxPlanValidationError, match="Maximum"):
        parse_and_validate_sfx_plan(
            {"schema_version": "sfx-plan-v1", "scope": "chapter", "sfx": items},
            max_sfx=3,
            known_shot_ids={"slot_1"},
            known_word_refs=set(),
            scope="chapter",
        )


def test_h_only_high_editorial_value_kept() -> None:
    payload = {
        "schema_version": "sfx-plan-v1",
        "scope": "chapter",
        "sfx": [
            {
                "sfx_id": "sfx_001",
                "sfx_type": "natural_ambience",
                "prompt": "subtle wind, no music, no speech",
                "evidence_basis": "environmental_plausible",
                "editorial_value": "high",
                "shot_id": "slot_1",
                "anchor_type": "shot_start",
                "word_ref": None,
                "duration_class": "short",
                "reason": "ok",
            },
            {
                "sfx_id": "sfx_002",
                "sfx_type": "diegetic_foley",
                "prompt": "footsteps",
                "evidence_basis": "visible",
                "editorial_value": "medium",
                "shot_id": "slot_1",
                "anchor_type": "shot_start",
                "word_ref": None,
                "duration_class": "short",
                "reason": "no",
            },
        ],
    }
    plan = parse_and_validate_sfx_plan(
        payload,
        max_sfx=3,
        known_shot_ids={"slot_1"},
        known_word_refs=set(),
        scope="chapter",
    )
    assert [x["sfx_id"] for x in plan["sfx"]] == ["sfx_001"]


def test_i_invalid_shot_id_rejected() -> None:
    with pytest.raises(SfxPlanValidationError, match="shot_id"):
        parse_and_validate_sfx_plan(
            {
                "schema_version": "sfx-plan-v1",
                "scope": "chapter",
                "sfx": [
                    {
                        "sfx_id": "sfx_001",
                        "sfx_type": "natural_ambience",
                        "prompt": "subtle wind, no music, no speech",
                        "evidence_basis": "environmental_plausible",
                        "editorial_value": "high",
                        "shot_id": "missing",
                        "anchor_type": "shot_start",
                        "word_ref": None,
                        "duration_class": "short",
                        "reason": "x",
                    }
                ],
            },
            max_sfx=3,
            known_shot_ids={"slot_1"},
            known_word_refs=set(),
            scope="chapter",
        )


def test_j_invalid_word_ref_rejected() -> None:
    with pytest.raises(SfxPlanValidationError, match="word_ref"):
        parse_and_validate_sfx_plan(
            {
                "schema_version": "sfx-plan-v1",
                "scope": "chapter",
                "sfx": [
                    {
                        "sfx_id": "sfx_001",
                        "sfx_type": "location_ambience",
                        "prompt": "distant birds, no music, no speech",
                        "evidence_basis": "environmental_plausible",
                        "editorial_value": "high",
                        "shot_id": "slot_1",
                        "anchor_type": "narration_word",
                        "word_ref": "fake#9",
                        "duration_class": "short",
                        "reason": "x",
                    }
                ],
            },
            max_sfx=3,
            known_shot_ids={"slot_1"},
            known_word_refs={"real#0"},
            scope="chapter",
        )


def test_k_narration_word_anchor_resolves_real_onset() -> None:
    shot = ResolvedShot(
        shot_id="slot_1",
        asset_id="a1",
        timeline_start_seconds=0.0,
        timeline_end_seconds=10.0,
        source_start_seconds=0.0,
        source_end_seconds=10.0,
    )
    start, dur = resolve_sfx_anchor(
        item={
            "anchor_type": "narration_word",
            "word_ref": "ch_1#s0#0",
            "duration_class": "short",
        },
        shot=shot,
        word_onsets={"ch_1#s0#0": 3.25},
        scope_total_duration=20.0,
    )
    assert start == pytest.approx(3.25)
    assert dur == pytest.approx(2.0)


# --- ElevenLabs client ---


def test_lmn_client_contract_sfx_v2(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    class _Resp:
        status_code = 200
        content = b"FAKEAUDIO"
        headers = {"Content-Type": "audio/mpeg"}
        text = ""

    def fake_post(url, json=None, headers=None, timeout=None, **_kwargs):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return _Resp()

    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.elevenlabs_sfx_client.get_api_key",
        lambda _k: "test-key",
    )
    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.elevenlabs_sfx_client.requests.post",
        fake_post,
    )
    result = generate_sound_effect(text="subtle wind", duration_seconds=5.0)
    assert result.audio_bytes == b"FAKEAUDIO"
    assert captured["json"]["model_id"] == "eleven_text_to_sound_v2" == SFX_MODEL_ID
    assert captured["json"]["loop"] is False
    assert captured["json"]["prompt_influence"] == SFX_PROMPT_INFLUENCE_DEFAULT == 0.3
    assert "sound-generation" in captured["url"]
    assert "xi-api-key" in captured["headers"]


def test_o_duration_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.elevenlabs_sfx_client.get_api_key",
        lambda _k: "test-key",
    )
    with pytest.raises(ElevenLabsSfxError):
        generate_sound_effect(text="x", duration_seconds=0.1)
    with pytest.raises(ElevenLabsSfxError):
        generate_sound_effect(text="x", duration_seconds=31.0)


def test_p_prompt_over_450_no_api_call(monkeypatch: pytest.MonkeyPatch) -> None:
    called = {"n": 0}

    def fake_post(*_a, **_k):
        called["n"] += 1
        raise AssertionError("should not call API")

    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.elevenlabs_sfx_client.get_api_key",
        lambda _k: "test-key",
    )
    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.elevenlabs_sfx_client.requests.post",
        fake_post,
    )
    with pytest.raises(ElevenLabsSfxError, match="länger"):
        generate_sound_effect(text="x" * (SFX_PROMPT_MAX_CHARS + 1), duration_seconds=2.0)
    assert called["n"] == 0


def test_qrs_wav_normalize_and_reject_corrupt(tmp_path: Path) -> None:
    src = tmp_path / "src.wav"
    _make_wav(src, duration=1.2)
    out = tmp_path / "out.wav"
    actual = convert_and_normalize_sfx_wav(src, target_duration_seconds=2.0, output_path=out)
    assert actual == pytest.approx(2.0, abs=0.05)
    validate_final_sfx_wav(out, target_duration_seconds=2.0)
    bad = tmp_path / "bad.wav"
    bad.write_bytes(b"not-a-wav")
    with pytest.raises(Exception):
        validate_final_sfx_wav(bad, target_duration_seconds=2.0)


# --- Generation / staleness / regen ---


def _patch_chapter_ready():
    return patch(
        "otio_app.services.without_voiceover_enhanced.sfx_service.list_chapter_cut_statuses",
        return_value=[
            SimpleNamespace(
                folder_name="Yosemite", has_resolved=True, matches=True
            )
        ],
    )


def test_generation_zero_sfx_and_partial(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path)
    _seed_chapter(project)
    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.sfx_service.is_elevenlabs_sfx_configured",
        lambda: True,
    )
    with _patch_chapter_ready(), patch(
        "otio_app.services.without_voiceover_enhanced.sfx_planner.load_cleaned_sentence_rows_for_segments",
        return_value=[],
    ), patch(
        "otio_app.services.without_voiceover_enhanced.sfx_planner._local_assets_payload",
        return_value=[],
    ):
        result = generate_sfx_for_chapter(
            project,
            "Yosemite",
            llm_callable=_fake_llm(
                {"schema_version": "sfx-plan-v1", "scope": "chapter", "sfx": []}
            ),
            generate_callable=lambda **_k: (_ for _ in ()).throw(AssertionError("no gen")),
        )
    assert result.status == "completed"
    assert result.effect_count == 0
    assert "keine benötigt" in result.message


def test_t_u_v_staleness_script_timeline_not_music(tmp_path: Path) -> None:
    project = _project(tmp_path)
    resolved = _seed_chapter(project)
    shots = [
        {
            "shot_id": s.shot_id,
            "asset_id": s.asset_id,
            "timeline_start": s.timeline_start_seconds,
            "timeline_end": s.timeline_end_seconds,
            "source_start": s.source_start_seconds,
            "source_end": s.source_end_seconds,
        }
        for s in resolved.shots
    ]
    script_fp = fingerprint_text("Yosemite granite walls rise above the valley floor.")
    timeline_fp = resolved_timeline_fingerprint_from_shots(
        script_version="v1", shots=shots
    )
    wav = sfx_wav_path(project, scope="chapter", folder_name="Yosemite", sfx_id="sfx_001")
    _make_wav(wav, duration=2.0)
    save_sfx_result(
        project,
        {
            "status": "completed",
            "scope": "chapter",
            "chapter_id": "Yosemite",
            "script_fingerprint": script_fp,
            "resolved_timeline_fingerprint": timeline_fp,
            "effects": [
                {
                    "sfx_id": "sfx_001",
                    "status": "completed",
                    "wav_path": str(wav),
                    "timeline_start": 0.0,
                    "duration": 2.0,
                }
            ],
        },
    )
    ok = sfx_status_for_scope(
        project,
        scope="chapter",
        folder_name="Yosemite",
        script_fingerprint=script_fp,
        resolved_timeline_fingerprint=timeline_fp,
        api_key_present=True,
    )
    assert ok["status"] == "completed"
    stale_script = sfx_status_for_scope(
        project,
        scope="chapter",
        folder_name="Yosemite",
        script_fingerprint="changed",
        resolved_timeline_fingerprint=timeline_fp,
        api_key_present=True,
    )
    assert stale_script["status"] == "stale"
    stale_tl = sfx_status_for_scope(
        project,
        scope="chapter",
        folder_name="Yosemite",
        script_fingerprint=script_fp,
        resolved_timeline_fingerprint="changed",
        api_key_present=True,
    )
    assert stale_tl["status"] == "stale"
    # Music change does not affect SFX fingerprints.
    music_wav = music_wav_path(project, scope="chapter", folder_name="Yosemite")
    _make_wav(music_wav, duration=20.0)
    save_music_result(
        project,
        {
            "scope": "chapter",
            "chapter_id": "Yosemite",
            "status": "completed",
            "music_path": str(music_wav),
            "script_fingerprint": "music-other",
            "resolved_timing_fingerprint": "music-other",
            "actual_duration_seconds": 20.0,
        },
    )
    still_ok = sfx_status_for_scope(
        project,
        scope="chapter",
        folder_name="Yosemite",
        script_fingerprint=script_fp,
        resolved_timeline_fingerprint=timeline_fp,
        api_key_present=True,
    )
    assert still_ok["status"] == "completed"


def test_w_failed_regen_preserves_canonical(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path)
    _seed_chapter(project)
    wav = sfx_wav_path(project, scope="chapter", folder_name="Yosemite", sfx_id="sfx_001")
    _make_wav(wav, duration=2.0)
    resolved = _resolved("Yosemite")
    shots = [
        {
            "shot_id": s.shot_id,
            "asset_id": s.asset_id,
            "timeline_start": s.timeline_start_seconds,
            "timeline_end": s.timeline_end_seconds,
            "source_start": s.source_start_seconds,
            "source_end": s.source_end_seconds,
        }
        for s in resolved.shots
    ]
    script_fp = fingerprint_text("Yosemite granite walls rise above the valley floor.")
    timeline_fp = resolved_timeline_fingerprint_from_shots(
        script_version="v1", shots=shots
    )
    save_sfx_result(
        project,
        {
            "status": "completed",
            "scope": "chapter",
            "chapter_id": "Yosemite",
            "script_fingerprint": script_fp,
            "resolved_timeline_fingerprint": timeline_fp,
            "effects": [
                {
                    "sfx_id": "sfx_001",
                    "status": "completed",
                    "wav_path": str(wav),
                    "timeline_start": 0.0,
                    "duration": 2.0,
                    "prompt": "old",
                }
            ],
        },
    )
    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.sfx_service.is_elevenlabs_sfx_configured",
        lambda: True,
    )

    def boom(**_k):
        raise ElevenLabsSfxError("api down")

    plan = {
        "schema_version": "sfx-plan-v1",
        "scope": "chapter",
        "sfx": [
            {
                "sfx_id": "sfx_001",
                "sfx_type": "natural_ambience",
                "prompt": "subtle wind, no music, no speech",
                "evidence_basis": "environmental_plausible",
                "editorial_value": "high",
                "shot_id": "Yosemite_slot_001",
                "anchor_type": "shot_start",
                "word_ref": None,
                "duration_class": "short",
                "reason": "x",
            }
        ],
    }
    with _patch_chapter_ready(), patch(
        "otio_app.services.without_voiceover_enhanced.sfx_planner.load_cleaned_sentence_rows_for_segments",
        return_value=[],
    ), patch(
        "otio_app.services.without_voiceover_enhanced.sfx_planner._local_assets_payload",
        return_value=[],
    ):
        result = generate_sfx_for_chapter(
            project,
            "Yosemite",
            llm_callable=_fake_llm(plan),
            generate_callable=boom,
        )
    assert result.status == "failed"
    assert "erhalten" in result.message
    stored = load_sfx_result(project, scope="chapter", folder_name="Yosemite")
    assert stored is not None
    assert stored["effects"][0]["prompt"] == "old"
    assert wav.is_file()


def test_x_empty_plan_replaces_prior(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path)
    _seed_chapter(project)
    wav = sfx_wav_path(project, scope="chapter", folder_name="Yosemite", sfx_id="sfx_001")
    _make_wav(wav, duration=2.0)
    resolved = _resolved("Yosemite")
    shots = [
        {
            "shot_id": s.shot_id,
            "asset_id": s.asset_id,
            "timeline_start": s.timeline_start_seconds,
            "timeline_end": s.timeline_end_seconds,
            "source_start": s.source_start_seconds,
            "source_end": s.source_end_seconds,
        }
        for s in resolved.shots
    ]
    script_fp = fingerprint_text("Yosemite granite walls rise above the valley floor.")
    timeline_fp = resolved_timeline_fingerprint_from_shots(
        script_version="v1", shots=shots
    )
    save_sfx_result(
        project,
        {
            "status": "completed",
            "scope": "chapter",
            "chapter_id": "Yosemite",
            "script_fingerprint": script_fp,
            "resolved_timeline_fingerprint": timeline_fp,
            "effects": [
                {
                    "sfx_id": "sfx_001",
                    "status": "completed",
                    "wav_path": str(wav),
                    "timeline_start": 0.0,
                    "duration": 2.0,
                }
            ],
        },
    )
    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.sfx_service.is_elevenlabs_sfx_configured",
        lambda: True,
    )
    with _patch_chapter_ready(), patch(
        "otio_app.services.without_voiceover_enhanced.sfx_planner.load_cleaned_sentence_rows_for_segments",
        return_value=[],
    ), patch(
        "otio_app.services.without_voiceover_enhanced.sfx_planner._local_assets_payload",
        return_value=[],
    ):
        result = generate_sfx_for_chapter(
            project,
            "Yosemite",
            llm_callable=_fake_llm(
                {"schema_version": "sfx-plan-v1", "scope": "chapter", "sfx": []}
            ),
        )
    assert result.status == "completed"
    assert result.effect_count == 0
    stored = load_sfx_result(project, scope="chapter", folder_name="Yosemite")
    assert stored is not None
    assert stored["effects"] == []


def test_chapter_4_restricted(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _write_locked(project)
    assert is_sfx_mvp_chapter_allowed(project, "Yosemite")
    assert not is_sfx_mvp_chapter_allowed(project, "Bryce")
    ui = sfx_ui_status_chapter(project, "Bryce")
    assert ui["enabled"] is False
    assert "Kapitel 1–3" in ui["message"]


def test_default_planner_model_gpt56_sol(tmp_path: Path) -> None:
    project = _project(tmp_path)
    assert DEFAULT_SFX_PLANNER_MODEL == "openai:gpt-5.6-sol"
    opts = CutPlanOptions()
    assert opts.sfx_planner_model == "openai:gpt-5.6-sol"
    save_cut_plan_options(project, opts)
    assert resolve_sfx_planner_model_id(project) == "openai:gpt-5.6-sol"


# --- OTIO ---


def test_y_otio_without_sfx(tmp_path: Path) -> None:
    project = _project(tmp_path)
    resolved = _seed_chapter(project)
    path = _export_otio_allowing_gaps(project, resolved, "no_sfx")
    tl = otio.adapters.read_from_file(str(path))
    names = [t.name for t in tl.tracks]
    assert "Sound Effects" not in names


def test_z_aa_ab_ae_af_otio_with_music_and_sfx(tmp_path: Path) -> None:
    project = _project(tmp_path)
    resolved = _seed_chapter(project)
    sfx = sfx_wav_path(project, scope="chapter", folder_name="Yosemite", sfx_id="sfx_001")
    _make_wav(sfx, duration=2.0)
    music = music_wav_path(project, scope="chapter", folder_name="Yosemite")
    _make_wav(music, duration=20.0)

    with (
        patch(
            "otio_app.services.without_voiceover_enhanced.otio_sfx_track.usable_sfx_placements_for_otio",
            return_value=[
                {
                    "sfx_id": "sfx_001",
                    "status": "completed",
                    "wav_path": str(sfx),
                    "timeline_start": 1.5,
                    "duration": 2.0,
                }
            ],
        ),
        patch(
            "otio_app.services.without_voiceover_enhanced.otio_music_track.usable_music_path_for_otio",
            return_value=music,
        ),
        patch(
            "otio_app.services.without_voiceover_enhanced.otio_sfx_track.probe_duration_seconds",
            return_value=2.0,
        ),
        patch(
            "otio_app.services.without_voiceover_enhanced.otio_music_track.probe_duration_seconds",
            return_value=20.0,
        ),
    ):
        path = _export_otio_allowing_gaps(project, resolved, "music_sfx")
    tl = otio.adapters.read_from_file(str(path))
    names = [t.name for t in tl.tracks]
    assert "Music" in names
    assert "Sound Effects" in names
    sfx_track = next(t for t in tl.tracks if t.name == "Sound Effects")
    clips = [c for c in sfx_track if isinstance(c, otio.schema.Clip)]
    assert clips
    url = str(clips[0].media_reference.target_url)
    assert url.endswith(".wav")
    assert not url.lower().startswith("http")
    assert abs(float(clips[0].source_range.start_time.to_seconds()) - 0.0) < 1e-6
    # Narration track remains (Voiceover unchanged).
    assert any(t.name == "Narration" for t in tl.tracks)


def test_ac_ad_stale_and_invalid_skipped(tmp_path: Path) -> None:
    project = _project(tmp_path)
    resolved = _seed_chapter(project)
    effects = usable_sfx_effects_for_otio(
        project,
        scope="chapter",
        folder_name="Yosemite",
        script_fingerprint="a",
        resolved_timeline_fingerprint="b",
    )
    assert effects == []
    placements = collect_sfx_placements(project, resolved)
    assert placements == []


def test_no_key_blocks_only_sfx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path)
    _seed_chapter(project)
    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.sfx_service.is_elevenlabs_sfx_configured",
        lambda: False,
    )
    with _patch_chapter_ready():
        result = generate_sfx_for_chapter(
            project,
            "Yosemite",
            llm_callable=lambda **_k: (_ for _ in ()).throw(AssertionError("no llm")),
        )
    assert result.status == "unavailable"
    assert "API-Key" in result.message
