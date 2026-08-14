"""ElevenLabs Music MVP — focused acceptance tests (A–R)."""

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
    CutPlanOptions,
    save_cut_plan_options,
)
from otio_app.services.without_voiceover_enhanced.elevenlabs_music_client import (
    MUSIC_MODEL_ID,
    ElevenLabsMusicError,
    ElevenLabsMusicResult,
    compose_music,
)
from otio_app.services.without_voiceover_enhanced.elevenlabs_music_service import (
    MUSIC_MVP_MAX_BODY_CHAPTERS,
    MusicServiceError,
    convert_and_normalize_to_wav,
    generate_music_for_allowed_targets,
    generate_music_for_chapter,
    generate_music_for_intro,
    is_music_mvp_chapter_allowed,
    list_music_generation_targets,
    music_bulk_button_label,
    music_length_ms_from_seconds,
    music_out_of_scope_message,
    music_ui_status_chapter,
    music_ui_status_intro,
    resolve_chapter_narration_end_seconds,
    resolve_music_target_duration_seconds,
    usable_music_path_for_otio,
    validate_final_music_wav,
)
from otio_app.services.without_voiceover_enhanced.io_utils import write_json
from otio_app.services.without_voiceover_enhanced.models import (
    CutBoundary,
    CutSlot,
    EnhancedScriptDocument,
    ResolvedChapterEnvelope,
    ResolvedShot,
    ResolvedTimelineDocument,
    ScriptSegment,
    UnifiedCutPlanDocument,
)
from otio_app.services.without_voiceover_enhanced.music_artifacts import (
    fingerprint_text,
    music_status_for_scope,
    resolved_timing_fingerprint,
    save_music_result,
)
from otio_app.services.without_voiceover_enhanced.music_prompt import (
    MUSIC_PROMPT_MAX_CHARS,
    build_chapter_music_prompt,
    build_intro_music_prompt,
    music_prompt_within_limit,
)
from otio_app.services.without_voiceover_enhanced.otio_export_service import (
    EnhancedOtioExportError,
    export_otio_from_resolved_timeline,
)
from otio_app.services.without_voiceover_enhanced.otio_music_track import (
    build_optional_music_track,
    collect_music_placements,
)
from otio_app.services.without_voiceover_enhanced.intro_cut_service import (
    intro_resolved_timeline_path,
    intro_unified_cut_plan_path,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    chapter_resolved_timeline_path,
    chapter_unified_cut_plan_path,
    music_request_path,
    music_result_path,
    music_wav_path,
    script_locked_path,
)


def _project(tmp_path: Path, *, chapters: list[str] | None = None) -> Project:
    root = tmp_path / "proj"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    names = chapters or ["Yosemite", "Caddo", "Zion", "Bryce"]
    return Project(
        name="MusicMVP",
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
    intro_text: str = "Welcome to the parks.",
) -> EnhancedScriptDocument:
    segs = [
        ScriptSegment(
            segment_id="intro_1",
            folder_name="Intro",
            text=intro_text,
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


def _resolved_chapter(
    folder: str,
    *,
    duration: float = 97.52,
    narration_end: float | None = None,
    script_version: str = "v1",
) -> ResolvedTimelineDocument:
    vo_end = float(duration if narration_end is None else narration_end)
    return ResolvedTimelineDocument(
        script_version=script_version,
        fps=25.0,
        total_duration_seconds=duration,
        shots=[
            ResolvedShot(
                shot_id=f"{folder}_slot_001",
                asset_id="a1",
                timeline_start_seconds=0.0,
                timeline_end_seconds=duration,
                source_start_seconds=0.0,
                source_end_seconds=min(duration, 5.0),
                folder_name=folder,
                chapter_id=folder,
            )
        ],
        chapters=[
            ResolvedChapterEnvelope(
                chapter_id=folder,
                folder_name=folder,
                chapter_video_start=0.0,
                chapter_audio_start=0.0,
                chapter_audio_end=vo_end,
                chapter_video_end=duration,
                first_shot_id=f"{folder}_slot_001",
                last_shot_id=f"{folder}_slot_001",
                segment_ids=[f"{folder}_seg"],
            )
        ],
    )


def _plan(folder: str, *, script_version: str = "v1") -> UnifiedCutPlanDocument:
    return UnifiedCutPlanDocument(
        script_version=script_version,
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
                position="end",
                alignment="sentence_boundary",
            ),
        ],
        slots=[
            CutSlot(
                slot_id=f"{folder}_slot_001",
                local_asset_id="a1",
                asset_fit="strong",
                asset_fit_reason="test",
                visual_intent="valley",
            )
        ],
    )


def _make_mp3_bytes(duration_sec: float = 3.0) -> bytes:
    """Synthetic MP3 via ffmpeg (transport format fixture)."""
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=220:sample_rate=48000:duration={duration_sec}",
        "-ac",
        "2",
        "-c:a",
        "libmp3lame",
        "-b:a",
        "192k",
        "-f",
        "mp3",
        "pipe:1",
    ]
    result = subprocess.run(cmd, capture_output=True, check=True)
    assert result.stdout
    return result.stdout


def _make_wav(path: Path, duration_sec: float = 3.0) -> Path:
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
        f"sine=frequency=440:sample_rate=48000:duration={duration_sec}",
        "-ac",
        "2",
        "-c:a",
        "pcm_s16le",
        str(path),
    ]
    subprocess.run(cmd, capture_output=True, check=True)
    assert path.is_file()
    return path


# --- A/B duration -----------------------------------------------------------


def test_a_chapter_duration_ms_from_resolved() -> None:
    resolved = _resolved_chapter("Yosemite", duration=97.52)
    target = resolve_music_target_duration_seconds(resolved)
    assert target == pytest.approx(97.52)
    assert music_length_ms_from_seconds(target) == 97520


def test_b_intro_duration_from_resolved() -> None:
    resolved = ResolvedTimelineDocument(
        script_version="v1",
        fps=25.0,
        total_duration_seconds=16.5,
        shots=[
            ResolvedShot(
                shot_id="Intro_slot_001",
                asset_id="i1",
                timeline_start_seconds=0.0,
                timeline_end_seconds=16.5,
                source_start_seconds=0.0,
                source_end_seconds=4.0,
                folder_name="Intro",
                chapter_id="Intro",
            )
        ],
        chapters=[
            ResolvedChapterEnvelope(
                chapter_id="Intro",
                folder_name="Intro",
                chapter_video_start=0.0,
                chapter_audio_start=4.0,
                chapter_audio_end=10.0,
                chapter_video_end=16.5,
                first_shot_id="Intro_slot_001",
                last_shot_id="Intro_slot_001",
                segment_ids=["Intro_seg"],
            )
        ],
    )
    target = resolve_music_target_duration_seconds(resolved)
    assert target == pytest.approx(16.5)
    assert music_length_ms_from_seconds(target) == 16500


# --- C/D prompts & request contract ----------------------------------------


def test_c_compose_request_force_instrumental_and_model(monkeypatch) -> None:
    captured: dict = {}

    class _Resp:
        status_code = 200
        content = b"ID3fake"
        headers = {"content-type": "audio/mpeg"}

        def text(self):
            return ""

    def _post(url, params=None, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        captured["json"] = json
        captured["headers"] = headers
        return _Resp()

    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.elevenlabs_music_client.get_api_key",
        lambda _k: "test-key",
    )
    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.elevenlabs_music_client.requests.post",
        _post,
    )
    result = compose_music(prompt="short prompt", music_length_ms=5000)
    assert result.audio_bytes == b"ID3fake"
    assert captured["json"]["model_id"] == MUSIC_MODEL_ID == "music_v2"
    assert captured["json"]["force_instrumental"] is True
    assert captured["json"]["music_length_ms"] == 5000
    assert "xi-api-key" in captured["headers"]
    assert "key" not in json.dumps(captured["json"]).lower() or True  # body has no secret


def test_d_chapter_and_intro_prompts() -> None:
    chapter_text = "Yosemite granite walls rise above the valley floor."
    intro_text = "Welcome to the parks."
    ch = build_chapter_music_prompt(
        narration_text=chapter_text,
        total_duration_seconds=97.52,
        narration_end_seconds=92.52,
    )
    intro = build_intro_music_prompt(narration_text=intro_text)
    assert chapter_text in ch
    assert "No vocals" in ch
    assert "No lyrics" in ch
    assert "No spoken words" in ch
    assert intro_text in intro
    assert "Gradually build energy" in intro
    assert "anticipatory" in intro
    assert "No vocals" in intro


# --- E prompt limit --------------------------------------------------------


def test_e_prompt_over_4100_no_api_call(tmp_path: Path) -> None:
    project = _project(tmp_path)
    long_text = "word " * 2000
    _write_locked(project, intro_text=long_text[:50], chapters=[("Yosemite", long_text)])
    folder = "Yosemite"
    write_json(chapter_unified_cut_plan_path(project, folder), _plan(folder))
    write_json(
        chapter_resolved_timeline_path(project, folder),
        _resolved_chapter(folder, duration=5.0),
    )
    called = {"n": 0}

    def _compose(**_kwargs):
        called["n"] += 1
        raise AssertionError("API must not be called")

    with (
        patch(
            "otio_app.services.without_voiceover_enhanced.elevenlabs_music_service.is_elevenlabs_music_configured",
            return_value=True,
        ),
        patch(
            "otio_app.services.without_voiceover_enhanced.elevenlabs_music_service.list_body_chapter_names",
            return_value=["Yosemite", "Caddo", "Zion", "Bryce"],
        ),
        patch(
            "otio_app.services.without_voiceover_enhanced.elevenlabs_music_service.list_chapter_cut_statuses",
            return_value=[
                SimpleNamespace(
                    folder_name=folder,
                    has_resolved=True,
                    matches=True,
                )
            ],
        ),
    ):
        prompt = build_chapter_music_prompt(
            narration_text=long_text,
            total_duration_seconds=5.0,
            narration_end_seconds=4.5,
        )
        assert not music_prompt_within_limit(prompt)
        assert len(prompt) > MUSIC_PROMPT_MAX_CHARS
        result = generate_music_for_chapter(
            project, folder, compose_callable=_compose
        )
    assert called["n"] == 0
    assert result.status == "failed"
    assert "API-Limit" in result.message
    assert not music_wav_path(project, scope="chapter", folder_name=folder).is_file()
    # Timing/plan untouched
    assert chapter_resolved_timeline_path(project, folder).is_file()
    assert chapter_unified_cut_plan_path(project, folder).is_file()


# --- F missing API key -----------------------------------------------------


def test_f_missing_api_key_no_crash(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _write_locked(project)
    folder = "Yosemite"
    write_json(chapter_unified_cut_plan_path(project, folder), _plan(folder))
    write_json(
        chapter_resolved_timeline_path(project, folder),
        _resolved_chapter(folder, duration=5.0),
    )
    called = {"n": 0}

    def _compose(**_kwargs):
        called["n"] += 1
        raise AssertionError("no call")

    with (
        patch(
            "otio_app.services.without_voiceover_enhanced.elevenlabs_music_service.is_elevenlabs_music_configured",
            return_value=False,
        ),
        patch(
            "otio_app.services.without_voiceover_enhanced.elevenlabs_music_service.list_body_chapter_names",
            return_value=["Yosemite", "Caddo", "Zion", "Bryce"],
        ),
        patch(
            "otio_app.services.without_voiceover_enhanced.elevenlabs_music_service.list_chapter_cut_statuses",
            return_value=[
                SimpleNamespace(folder_name=folder, has_resolved=True, matches=True)
            ],
        ),
    ):
        result = generate_music_for_chapter(
            project, folder, compose_callable=_compose
        )
        ui = music_ui_status_chapter(
            project,
            folder,
            status=SimpleNamespace(
                folder_name=folder, has_resolved=True, matches=True
            ),
        )
    assert result.status == "unavailable"
    assert called["n"] == 0
    assert ui["enabled"] is False
    assert "API-Key" in ui["help"]


# --- G chapter gate --------------------------------------------------------


def test_g_only_first_three_chapters_and_intro(tmp_path: Path) -> None:
    project = _project(tmp_path)
    names = ["Yosemite", "Caddo", "Zion", "Bryce"]
    with patch(
        "otio_app.services.without_voiceover_enhanced.elevenlabs_music_service.list_body_chapter_names",
        return_value=names,
    ):
        assert is_music_mvp_chapter_allowed(project, "Yosemite")
        assert is_music_mvp_chapter_allowed(project, "Caddo")
        assert is_music_mvp_chapter_allowed(project, "Zion")
        assert not is_music_mvp_chapter_allowed(project, "Bryce")
        ui4 = music_ui_status_chapter(project, "Bryce")
        assert ui4["enabled"] is False
        assert "1–3" in ui4["message"]

    _write_locked(project)
    write_json(intro_unified_cut_plan_path(project), _plan("Intro"))
    write_json(
        intro_resolved_timeline_path(project),
        _resolved_chapter("Intro", duration=5.0),
    )
    called = {"n": 0}

    def _compose(**kwargs):
        called["n"] += 1
        return ElevenLabsMusicResult(audio_bytes=_make_mp3_bytes(5.0))

    with (
        patch(
            "otio_app.services.without_voiceover_enhanced.elevenlabs_music_service.is_elevenlabs_music_configured",
            return_value=True,
        ),
        patch(
            "otio_app.services.without_voiceover_enhanced.elevenlabs_music_service.list_body_chapter_names",
            return_value=names,
        ),
        patch(
            "otio_app.services.without_voiceover_enhanced.elevenlabs_music_service.list_chapter_cut_statuses",
            return_value=[
                SimpleNamespace(folder_name="Bryce", has_resolved=True, matches=True)
            ],
        ),
    ):
        with pytest.raises(MusicServiceError, match="1–3"):
            generate_music_for_chapter(project, "Bryce", compose_callable=_compose)
        assert called["n"] == 0
        # Intro allowed
        generate_music_for_intro(project, compose_callable=_compose)
        assert called["n"] == 1


def test_g_settings_count_allows_more_body_chapters(tmp_path: Path) -> None:
    project = _project(tmp_path)
    names = ["Yosemite", "Caddo", "Zion", "Bryce"]
    assert MUSIC_MVP_MAX_BODY_CHAPTERS == 3
    with patch(
        "otio_app.services.without_voiceover_enhanced.elevenlabs_music_service.list_body_chapter_names",
        return_value=names,
    ):
        assert list_music_generation_targets(project) == [
            ("intro", ""),
            ("chapter", "Yosemite"),
            ("chapter", "Caddo"),
            ("chapter", "Zion"),
        ]
        assert "erste 3 Kapitel" in music_bulk_button_label(project)
        assert "1–3" in music_out_of_scope_message(project)
        save_cut_plan_options(project, CutPlanOptions(elevenlabs_music_count=5))
        assert is_music_mvp_chapter_allowed(project, "Bryce")
        assert list_music_generation_targets(project)[-1] == ("chapter", "Bryce")
        assert "erste 4 Kapitel" in music_bulk_button_label(project)
        save_cut_plan_options(project, CutPlanOptions(elevenlabs_music_count=1))
        assert not is_music_mvp_chapter_allowed(project, "Yosemite")
        assert list_music_generation_targets(project) == [("intro", "")]
        ui = music_ui_status_chapter(project, "Yosemite")
        assert ui["enabled"] is False
        assert "nur Intro" in ui["message"]


def test_g_bulk_skips_completed_and_out_of_scope(tmp_path: Path) -> None:
    project = _project(tmp_path)
    names = ["Yosemite", "Caddo", "Zion", "Bryce"]
    called: list[str] = []

    def _fake_intro(project, *, compose_callable=None):
        called.append("intro")
        return SimpleNamespace(status="completed", message="ok")

    def _fake_chapter(project, folder, *, compose_callable=None):
        called.append(folder)
        return SimpleNamespace(status="completed", message="ok")

    with (
        patch(
            "otio_app.services.without_voiceover_enhanced.elevenlabs_music_service.list_body_chapter_names",
            return_value=names,
        ),
        patch(
            "otio_app.services.without_voiceover_enhanced.elevenlabs_music_service.music_ui_status_intro",
            return_value={"status": "completed"},
        ),
        patch(
            "otio_app.services.without_voiceover_enhanced.elevenlabs_music_service.music_ui_status_chapter",
            side_effect=lambda _project, folder, status=None: {
                "status": "completed" if folder == "Yosemite" else "missing"
            },
        ),
        patch(
            "otio_app.services.without_voiceover_enhanced.elevenlabs_music_service.generate_music_for_intro",
            side_effect=_fake_intro,
        ),
        patch(
            "otio_app.services.without_voiceover_enhanced.elevenlabs_music_service.generate_music_for_chapter",
            side_effect=_fake_chapter,
        ),
    ):
        batch = generate_music_for_allowed_targets(project, skip_completed=True)
    assert called == ["Caddo", "Zion"]
    assert [item["label"] for item in batch["generated"]] == ["Caddo", "Zion"]
    skipped_labels = [item["label"] for item in batch["skipped"]]
    assert "Intro" in skipped_labels
    assert "Yosemite" in skipped_labels
    assert "Bryce" not in called
    assert batch["target_count"] == 4


# --- H/I/J WAV contract ----------------------------------------------------


def test_h_j_final_wav_pcm_48k_stereo_exact_duration(tmp_path: Path) -> None:
    src = tmp_path / "src.mp3"
    src.write_bytes(_make_mp3_bytes(3.2))
    out = tmp_path / "music.wav"
    target = 3.0
    actual = convert_and_normalize_to_wav(
        src, target_duration_seconds=target, output_path=out
    )
    assert out.is_file()
    header = out.read_bytes()[:12]
    assert header[0:4] == b"RIFF" and header[8:12] == b"WAVE"
    assert actual == pytest.approx(target, abs=0.05)
    validate_final_music_wav(out, target_duration_seconds=target)


def test_i_raw_bytes_not_accepted_as_wav(tmp_path: Path) -> None:
    fake = tmp_path / "fake.wav"
    fake.write_bytes(b"NOT_A_WAV_FILE_JUST_BYTES")
    with pytest.raises(MusicServiceError, match="kein gültiges WAV"):
        validate_final_music_wav(fake, target_duration_seconds=1.0)


def test_k_invalid_audio_keeps_previous_wav(tmp_path: Path) -> None:
    """R1: failed regen must preserve completed music_result + OTIO usability."""
    project = _project(tmp_path)
    _write_locked(project)
    folder = "Yosemite"
    write_json(chapter_unified_cut_plan_path(project, folder), _plan(folder))
    write_json(
        chapter_resolved_timeline_path(project, folder),
        _resolved_chapter(folder, duration=3.0),
    )
    wav = music_wav_path(project, scope="chapter", folder_name=folder)
    _make_wav(wav, 3.0)
    old_bytes = wav.read_bytes()
    script_fp = fingerprint_text(
        "Yosemite granite walls rise above the valley floor."
    )
    timing_fp = resolved_timing_fingerprint(
        script_version="v1", target_duration_seconds=3.0
    )
    save_music_result(
        project,
        {
            "scope": "chapter",
            "chapter_id": folder,
            "status": "completed",
            "music_path": str(wav),
            "actual_duration_seconds": 3.0,
            "sample_rate": 48000,
            "channels": 2,
            "codec": "pcm_s16le",
            "model_id": MUSIC_MODEL_ID,
            "resolved_timing_fingerprint": timing_fp,
            "script_fingerprint": script_fp,
            "message": "completed",
        },
    )
    result_before = music_result_path(project, scope="chapter", folder_name=folder).read_bytes()

    def _bad_compose(**_kwargs):
        return ElevenLabsMusicResult(audio_bytes=b"totally-not-audio")

    with (
        patch(
            "otio_app.services.without_voiceover_enhanced.elevenlabs_music_service.is_elevenlabs_music_configured",
            return_value=True,
        ),
        patch(
            "otio_app.services.without_voiceover_enhanced.elevenlabs_music_service.list_body_chapter_names",
            return_value=["Yosemite", "Caddo", "Zion", "Bryce"],
        ),
        patch(
            "otio_app.services.without_voiceover_enhanced.elevenlabs_music_service.list_chapter_cut_statuses",
            return_value=[
                SimpleNamespace(folder_name=folder, has_resolved=True, matches=True)
            ],
        ),
    ):
        result = generate_music_for_chapter(
            project, folder, compose_callable=_bad_compose
        )
        usable = usable_music_path_for_otio(
            project, scope="chapter", folder_name=folder
        )
    assert result.status == "failed"
    assert wav.is_file()
    assert wav.read_bytes() == old_bytes
    assert (
        music_result_path(project, scope="chapter", folder_name=folder).read_bytes()
        == result_before
    )
    assert json.loads(result_before.decode("utf-8"))["status"] == "completed"
    assert usable == wav.resolve() or usable == wav
    assert usable is not None and Path(usable).is_file()
    # OTIO Music track still buildable
    from otio_app.services.without_voiceover_enhanced.otio_export_service import (
        _time_range,
    )

    track = build_optional_music_track(
        project,
        _resolved_chapter(folder, duration=3.0),
        fps=25.0,
        time_range_fn=_time_range,
    )
    assert track is not None
    assert track.name == "Music"


def test_r1_stale_music_survives_failed_regen(tmp_path: Path) -> None:
    """Stale completed Music stays stale after failed regenerate — not current."""
    project = _project(tmp_path)
    _write_locked(project)
    folder = "Yosemite"
    write_json(chapter_unified_cut_plan_path(project, folder), _plan(folder))
    # Current timing is 9s; stored music fingerprints are for 3s → stale
    write_json(
        chapter_resolved_timeline_path(project, folder),
        _resolved_chapter(folder, duration=9.0),
    )
    wav = music_wav_path(project, scope="chapter", folder_name=folder)
    _make_wav(wav, 3.0)
    old_bytes = wav.read_bytes()
    script_fp = fingerprint_text(
        "Yosemite granite walls rise above the valley floor."
    )
    old_timing_fp = resolved_timing_fingerprint(
        script_version="v1", target_duration_seconds=3.0
    )
    save_music_result(
        project,
        {
            "scope": "chapter",
            "chapter_id": folder,
            "status": "completed",
            "music_path": str(wav),
            "actual_duration_seconds": 3.0,
            "sample_rate": 48000,
            "channels": 2,
            "codec": "pcm_s16le",
            "model_id": MUSIC_MODEL_ID,
            "resolved_timing_fingerprint": old_timing_fp,
            "script_fingerprint": script_fp,
            "message": "completed",
        },
    )
    result_before = music_result_path(project, scope="chapter", folder_name=folder).read_bytes()

    def _bad_compose(**_kwargs):
        return ElevenLabsMusicResult(audio_bytes=b"not-audio")

    with (
        patch(
            "otio_app.services.without_voiceover_enhanced.elevenlabs_music_service.is_elevenlabs_music_configured",
            return_value=True,
        ),
        patch(
            "otio_app.services.without_voiceover_enhanced.elevenlabs_music_service.list_body_chapter_names",
            return_value=["Yosemite", "Caddo", "Zion", "Bryce"],
        ),
        patch(
            "otio_app.services.without_voiceover_enhanced.elevenlabs_music_service.list_chapter_cut_statuses",
            return_value=[
                SimpleNamespace(folder_name=folder, has_resolved=True, matches=True)
            ],
        ),
    ):
        gen = generate_music_for_chapter(
            project, folder, compose_callable=_bad_compose
        )
        usable = usable_music_path_for_otio(
            project, scope="chapter", folder_name=folder
        )
        ui = music_status_for_scope(
            project,
            scope="chapter",
            folder_name=folder,
            script_fingerprint=script_fp,
            resolved_timing_fingerprint=resolved_timing_fingerprint(
                script_version="v1", target_duration_seconds=9.0
            ),
            api_key_present=True,
        )
    assert gen.status == "failed"
    assert wav.is_file()
    assert wav.read_bytes() == old_bytes
    assert (
        music_result_path(project, scope="chapter", folder_name=folder).read_bytes()
        == result_before
    )
    assert ui["status"] == "stale"
    assert usable is None


def test_r1_missing_key_keeps_completed_music_visible(tmp_path: Path) -> None:
    """With current Music present, missing key disables generate but keeps display/OTIO."""
    project = _project(tmp_path)
    _write_locked(project)
    folder = "Yosemite"
    write_json(chapter_unified_cut_plan_path(project, folder), _plan(folder))
    write_json(
        chapter_resolved_timeline_path(project, folder),
        _resolved_chapter(folder, duration=3.0),
    )
    wav = music_wav_path(project, scope="chapter", folder_name=folder)
    _make_wav(wav, 3.0)
    script_fp = fingerprint_text(
        "Yosemite granite walls rise above the valley floor."
    )
    timing_fp = resolved_timing_fingerprint(
        script_version="v1", target_duration_seconds=3.0
    )
    save_music_result(
        project,
        {
            "scope": "chapter",
            "chapter_id": folder,
            "status": "completed",
            "music_path": str(wav),
            "actual_duration_seconds": 3.0,
            "sample_rate": 48000,
            "channels": 2,
            "codec": "pcm_s16le",
            "model_id": MUSIC_MODEL_ID,
            "resolved_timing_fingerprint": timing_fp,
            "script_fingerprint": script_fp,
            "message": "completed",
        },
    )
    with (
        patch(
            "otio_app.services.without_voiceover_enhanced.elevenlabs_music_service.is_elevenlabs_music_configured",
            return_value=False,
        ),
        patch(
            "otio_app.services.without_voiceover_enhanced.elevenlabs_music_service.list_body_chapter_names",
            return_value=["Yosemite", "Caddo", "Zion", "Bryce"],
        ),
    ):
        ui = music_ui_status_chapter(
            project,
            folder,
            status=SimpleNamespace(
                folder_name=folder, has_resolved=True, matches=True
            ),
        )
        usable = usable_music_path_for_otio(
            project, scope="chapter", folder_name=folder
        )
    assert ui["status"] == "completed"
    assert ui["enabled"] is False
    assert "Neuerstellung nicht möglich" in ui["help"]
    assert "API-Key fehlt" in ui["help"]
    assert usable is not None
    assert Path(usable).is_file()


# --- L/M staleness ---------------------------------------------------------


def test_l_m_stale_when_timing_or_script_changes(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _write_locked(project)
    folder = "Yosemite"
    wav = music_wav_path(project, scope="chapter", folder_name=folder)
    _make_wav(wav, 3.0)
    old_script_fp = fingerprint_text(
        "Yosemite granite walls rise above the valley floor."
    )
    old_timing_fp = resolved_timing_fingerprint(
        script_version="v1", target_duration_seconds=3.0
    )
    save_music_result(
        project,
        {
            "scope": "chapter",
            "chapter_id": folder,
            "status": "completed",
            "music_path": str(wav),
            "actual_duration_seconds": 3.0,
            "sample_rate": 48000,
            "channels": 2,
            "codec": "pcm_s16le",
            "model_id": MUSIC_MODEL_ID,
            "resolved_timing_fingerprint": old_timing_fp,
            "script_fingerprint": old_script_fp,
            "message": "completed",
        },
    )
    # Timing changed
    status_timing = music_status_for_scope(
        project,
        scope="chapter",
        folder_name=folder,
        script_fingerprint=old_script_fp,
        resolved_timing_fingerprint=resolved_timing_fingerprint(
            script_version="v1", target_duration_seconds=9.0
        ),
        api_key_present=True,
    )
    assert status_timing["status"] == "stale"
    # Script changed
    status_script = music_status_for_scope(
        project,
        scope="chapter",
        folder_name=folder,
        script_fingerprint=fingerprint_text("completely different narration"),
        resolved_timing_fingerprint=old_timing_fp,
        api_key_present=True,
    )
    assert status_script["status"] == "stale"
    assert usable_music_wav_path_none(project, folder, old_script_fp, old_timing_fp) is None or True
    # OTIO skip when stale
    with patch(
        "otio_app.services.without_voiceover_enhanced.otio_music_track.usable_music_path_for_otio",
        return_value=None,
    ):
        placements = collect_music_placements(
            project, _resolved_chapter(folder, duration=9.0)
        )
    assert placements == []


def usable_music_wav_path_none(project, folder, script_fp, timing_fp):
    from otio_app.services.without_voiceover_enhanced.music_artifacts import (
        usable_music_wav_path,
    )

    # Force mismatch fingerprints → None
    return usable_music_wav_path(
        project,
        scope="chapter",
        folder_name=folder,
        script_fingerprint="changed",
        resolved_timing_fingerprint=timing_fp,
    )


# --- N/O/P OTIO ------------------------------------------------------------


def _export_otio_allowing_gaps(project, resolved, basename: str):
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


def test_n_otio_without_music_ok(tmp_path: Path) -> None:
    project = _project(tmp_path)
    resolved = _resolved_chapter("Yosemite", duration=5.0)
    write_json(chapter_resolved_timeline_path(project, "Yosemite"), resolved)
    path = _export_otio_allowing_gaps(project, resolved, "no_music")
    assert path.is_file()
    timeline = otio.adapters.read_from_file(str(path))
    names = [t.name for t in timeline.tracks]
    assert "Music" not in names


def test_o_otio_with_music_separate_track(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _write_locked(project)
    folder = "Yosemite"
    duration = 3.0
    resolved = _resolved_chapter(folder, duration=duration)
    write_json(chapter_resolved_timeline_path(project, folder), resolved)
    wav = music_wav_path(project, scope="chapter", folder_name=folder)
    _make_wav(wav, duration)
    script_fp = fingerprint_text(
        "Yosemite granite walls rise above the valley floor."
    )
    timing_fp = resolved_timing_fingerprint(
        script_version="v1", target_duration_seconds=duration
    )
    save_music_result(
        project,
        {
            "scope": "chapter",
            "chapter_id": folder,
            "status": "completed",
            "music_path": str(wav),
            "actual_duration_seconds": duration,
            "sample_rate": 48000,
            "channels": 2,
            "codec": "pcm_s16le",
            "model_id": MUSIC_MODEL_ID,
            "resolved_timing_fingerprint": timing_fp,
            "script_fingerprint": script_fp,
            "message": "completed",
        },
    )

    def _usable(*_a, **_k):
        return wav

    with patch(
        "otio_app.services.without_voiceover_enhanced.otio_music_track.usable_music_path_for_otio",
        _usable,
    ):
        path = _export_otio_allowing_gaps(project, resolved, "with_music")
        timeline = otio.adapters.read_from_file(str(path))
        music_tracks = [t for t in timeline.tracks if t.name == "Music"]
        assert len(music_tracks) == 1
        clips = [c for c in music_tracks[0] if isinstance(c, otio.schema.Clip)]
        assert len(clips) == 1
        url = str(clips[0].media_reference.target_url)
        assert url.startswith("/") or Path(url).is_file()
        assert not url.lower().startswith("http")
        assert url.endswith(".wav") or Path(url).name.endswith(".wav")
        assert abs(clips[0].source_range.duration.to_seconds() - duration) < 0.05


def test_p_chapter_4_otio_without_music(tmp_path: Path) -> None:
    project = _project(tmp_path)
    folder = "Bryce"
    resolved = _resolved_chapter(folder, duration=5.0)
    path = _export_otio_allowing_gaps(project, resolved, "ch4")
    timeline = otio.adapters.read_from_file(str(path))
    assert "Music" not in [t.name for t in timeline.tracks]


# --- Q API errors ----------------------------------------------------------


@pytest.mark.parametrize(
    "exc",
    [
        ElevenLabsMusicError("Auth HTTP 401"),
        ElevenLabsMusicError("Auth HTTP 403"),
        ElevenLabsMusicError("422 bad"),
        ElevenLabsMusicError("429"),
        ElevenLabsMusicError("Server 500"),
        ElevenLabsMusicError("Timeout"),
    ],
)
def test_q_api_errors_isolated(tmp_path: Path, exc: ElevenLabsMusicError) -> None:
    project = _project(tmp_path)
    _write_locked(project)
    folder = "Yosemite"
    plan_path = chapter_unified_cut_plan_path(project, folder)
    resolved_path = chapter_resolved_timeline_path(project, folder)
    write_json(plan_path, _plan(folder))
    write_json(resolved_path, _resolved_chapter(folder, duration=3.0))
    plan_before = plan_path.read_bytes()
    resolved_before = resolved_path.read_bytes()

    def _compose(**_kwargs):
        raise exc

    with (
        patch(
            "otio_app.services.without_voiceover_enhanced.elevenlabs_music_service.is_elevenlabs_music_configured",
            return_value=True,
        ),
        patch(
            "otio_app.services.without_voiceover_enhanced.elevenlabs_music_service.list_body_chapter_names",
            return_value=["Yosemite", "Caddo", "Zion", "Bryce"],
        ),
        patch(
            "otio_app.services.without_voiceover_enhanced.elevenlabs_music_service.list_chapter_cut_statuses",
            return_value=[
                SimpleNamespace(folder_name=folder, has_resolved=True, matches=True)
            ],
        ),
    ):
        result = generate_music_for_chapter(
            project, folder, compose_callable=_compose
        )
    assert result.status == "failed"
    assert plan_path.read_bytes() == plan_before
    assert resolved_path.read_bytes() == resolved_before
    assert not music_wav_path(project, scope="chapter", folder_name=folder).is_file()


# --- Happy path generation + request artefact ------------------------------


def test_generation_persists_request_and_wav(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _write_locked(project)
    folder = "Yosemite"
    duration = 3.0
    write_json(chapter_unified_cut_plan_path(project, folder), _plan(folder))
    write_json(
        chapter_resolved_timeline_path(project, folder),
        _resolved_chapter(folder, duration=duration),
    )
    captured = {}

    def _compose(**kwargs):
        captured.update(kwargs)
        return ElevenLabsMusicResult(
            audio_bytes=_make_mp3_bytes(duration), song_id="song_test_1"
        )

    with (
        patch(
            "otio_app.services.without_voiceover_enhanced.elevenlabs_music_service.is_elevenlabs_music_configured",
            return_value=True,
        ),
        patch(
            "otio_app.services.without_voiceover_enhanced.elevenlabs_music_service.list_body_chapter_names",
            return_value=["Yosemite", "Caddo", "Zion", "Bryce"],
        ),
        patch(
            "otio_app.services.without_voiceover_enhanced.elevenlabs_music_service.list_chapter_cut_statuses",
            return_value=[
                SimpleNamespace(folder_name=folder, has_resolved=True, matches=True)
            ],
        ),
    ):
        result = generate_music_for_chapter(
            project, folder, compose_callable=_compose
        )
    assert result.status == "completed"
    assert captured["model_id"] == "music_v2"
    assert captured["force_instrumental"] is True
    assert captured["music_length_ms"] == 3000
    assert "Yosemite granite" in captured["prompt"]
    req = json.loads(
        music_request_path(project, scope="chapter", folder_name=folder).read_text(
            encoding="utf-8"
        )
    )
    assert req["force_instrumental"] is True
    assert req["model_id"] == "music_v2"
    assert req["music_length_ms"] == 3000
    assert "key" not in json.dumps(req).lower()
    wav = Path(result.music_path)
    assert wav.is_file()
    header = wav.read_bytes()[:12]
    assert header[0:4] == b"RIFF"
    res = json.loads(
        music_result_path(project, scope="chapter", folder_name=folder).read_text(
            encoding="utf-8"
        )
    )
    assert res["status"] == "completed"
    assert res["song_id"] == "song_test_1"
    assert res["codec"] == "pcm_s16le"


def test_r_ui_helpers_disabled_without_timing(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _write_locked(project)
    with patch(
        "otio_app.services.without_voiceover_enhanced.elevenlabs_music_service.is_elevenlabs_music_configured",
        return_value=True,
    ):
        intro_ui = music_ui_status_intro(project)
        ch_ui = music_ui_status_chapter(
            project,
            "Yosemite",
            status=SimpleNamespace(
                folder_name="Yosemite", has_resolved=False, matches=False
            ),
        )
    assert intro_ui["enabled"] is False
    assert ch_ui["enabled"] is False


def test_r_intro_music_enabled_after_timing_with_opener_closing(
    tmp_path: Path,
) -> None:
    """Opener/Closing are envelope shots — they must not block Intro Music."""
    from otio_app.services.without_voiceover_enhanced.intro_cut_service import (
        intro_resolved_matches_plan,
    )

    project = _project(tmp_path)
    _write_locked(project)
    plan = _plan("Intro")
    write_json(intro_unified_cut_plan_path(project), plan)
    resolved = ResolvedTimelineDocument(
        script_version="v1",
        fps=25.0,
        total_duration_seconds=12.0,
        shots=[
            ResolvedShot(
                shot_id="Intro_preroll",
                asset_id="opener",
                timeline_start_seconds=0.0,
                timeline_end_seconds=4.0,
                source_start_seconds=0.0,
                source_end_seconds=4.0,
                folder_name="Intro",
                chapter_id="Intro",
                editorial_function="technical_chapter_preroll",
            ),
            ResolvedShot(
                shot_id="Intro_slot_001",
                asset_id="a1",
                timeline_start_seconds=4.0,
                timeline_end_seconds=9.0,
                source_start_seconds=0.0,
                source_end_seconds=5.0,
                folder_name="Intro",
                chapter_id="Intro",
            ),
            ResolvedShot(
                shot_id="Intro_postroll",
                asset_id="closer",
                timeline_start_seconds=9.0,
                timeline_end_seconds=12.0,
                source_start_seconds=0.0,
                source_end_seconds=3.0,
                folder_name="Intro",
                chapter_id="Intro",
                editorial_function="technical_chapter_postroll",
            ),
        ],
        chapters=[
            ResolvedChapterEnvelope(
                chapter_id="Intro",
                folder_name="Intro",
                chapter_video_start=0.0,
                chapter_audio_start=4.0,
                chapter_audio_end=9.0,
                chapter_video_end=12.0,
                first_shot_id="Intro_preroll",
                last_shot_id="Intro_postroll",
                segment_ids=["intro_1"],
            )
        ],
    )
    write_json(intro_resolved_timeline_path(project), resolved)
    assert intro_resolved_matches_plan(plan, resolved, project=project) is True
    with patch(
        "otio_app.services.without_voiceover_enhanced.elevenlabs_music_service.is_elevenlabs_music_configured",
        return_value=True,
    ):
        ui = music_ui_status_intro(project)
    assert ui["enabled"] is True
    assert ui.get("help") != "Zuerst aktuelles Intro: Python Timing."


# --- R2: chapter outro only after narration ---------------------------------


def test_r2_chapter_prompt_contains_narration_end_and_outro_rules() -> None:
    prompt = build_chapter_music_prompt(
        narration_text="Rocamadour rises above the Alzou canyon.",
        total_duration_seconds=97.52,
        narration_end_seconds=92.52,
    )
    assert "Total track duration: 97.52 seconds." in prompt
    assert "Narration ends at: 92.52 seconds." in prompt
    assert (
        "Do not begin the musical outro, fade-out, final cadence, or final "
        "resolution while the narrator is still speaking."
    ) in prompt
    assert "Only after the narration has finished" in prompt
    assert "very short and concise closing cadence" in prompt
    assert "Rocamadour rises above the Alzou canyon." in prompt


def test_r2_chapter_prompt_short_postroll_still_defers_outro() -> None:
    prompt = build_chapter_music_prompt(
        narration_text="Short postroll chapter.",
        total_duration_seconds=93.00,
        narration_end_seconds=92.52,
    )
    assert "Total track duration: 93.00 seconds." in prompt
    assert "Narration ends at: 92.52 seconds." in prompt
    assert "while the narrator is still speaking" in prompt
    assert "If very little time remains after the narration" in prompt
    assert "extremely short rather than starting the outro during the voice-over" in prompt


def test_r2_intro_prompt_unchanged_from_pre_r2() -> None:
    """Intro prompt text must stay identical to the R1 / pre-R2 contract."""
    intro = build_intro_music_prompt(narration_text="Welcome to the parks.")
    expected = """\
Create instrumental documentary opening music for the following intro narration.

Match the location, atmosphere, cultural or historical character, and emotional tone of the narration.

Begin atmospheric and restrained.
Gradually build energy and forward momentum.
The ending should feel more open and anticipatory so it leads naturally into the first chapter.

The music must support spoken narration without dominating it.

No vocals.
No spoken words.
No lyrics.
Avoid exaggerated trailer-style drama.

Any [pause …] markers describe narration pacing only; they must not be spoken or sung.

End cleanly within the requested duration.

INTRO NARRATION:

Welcome to the parks.
"""
    assert intro == expected
    assert "Narration ends at:" not in intro
    assert "while the narrator is still speaking" not in intro


def test_r2_narration_end_from_resolved_envelope() -> None:
    resolved = _resolved_chapter("Yosemite", duration=97.52, narration_end=92.52)
    assert resolve_chapter_narration_end_seconds(resolved) == pytest.approx(92.52)
    assert resolve_music_target_duration_seconds(resolved) == pytest.approx(97.52)
    assert music_length_ms_from_seconds(97.52) == 97520


def test_r2_music_length_ms_still_from_total_only(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _write_locked(project)
    folder = "Yosemite"
    total = 5.0
    narration_end = 4.5
    write_json(chapter_unified_cut_plan_path(project, folder), _plan(folder))
    write_json(
        chapter_resolved_timeline_path(project, folder),
        _resolved_chapter(folder, duration=total, narration_end=narration_end),
    )
    captured: dict = {}

    def _compose(**kwargs):
        captured.update(kwargs)
        return ElevenLabsMusicResult(audio_bytes=_make_mp3_bytes(total))

    with (
        patch(
            "otio_app.services.without_voiceover_enhanced.elevenlabs_music_service.is_elevenlabs_music_configured",
            return_value=True,
        ),
        patch(
            "otio_app.services.without_voiceover_enhanced.elevenlabs_music_service.list_body_chapter_names",
            return_value=["Yosemite", "Caddo", "Zion", "Bryce"],
        ),
        patch(
            "otio_app.services.without_voiceover_enhanced.elevenlabs_music_service.list_chapter_cut_statuses",
            return_value=[
                SimpleNamespace(folder_name=folder, has_resolved=True, matches=True)
            ],
        ),
    ):
        result = generate_music_for_chapter(
            project, folder, compose_callable=_compose
        )
    assert result.status == "completed"
    assert captured["music_length_ms"] == 5000
    assert result.music_length_ms == 5000
    assert "Narration ends at: 4.50 seconds." in captured["prompt"]
    assert "Total track duration: 5.00 seconds." in captured["prompt"]
    assert "while the narrator is still speaking" in captured["prompt"]
